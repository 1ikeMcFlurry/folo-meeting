#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trae_card BLE 上位机 —— GATT 客户端(中心角色)。

功能:
  1. 扫描并连接卡片(默认名 TRAE-CARD)。
  2. 发送 JSON 配置(Spec A, type=0x01):文本里有哪个键就改哪个字段。
  3. 传输全屏图片(Spec B, type=0x02):转基线 JPG,≤240×320、宽高 8 的倍数。
  4. 从固件内置头像列表选择名称,以 JSON avatar_name 下发。
  5. 订阅 notify(0x11),实时显示卡片回报的处理状态。

应用层帧: [ver=1][type][len:2 小端][payload]。命令写特征累积重组,故一帧可跨多次写。
依赖: pip install bleak pillow   (见 requirements-client.txt)
运行: python ble_card_client.py       (Windows/Linux/macOS 均可,bleak 跨平台)
"""
import asyncio
import io
import json
import os
import re
import sys
import tempfile
import threading
import queue
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from bleak import BleakScanner, BleakClient
except ImportError:
    raise SystemExit("缺少 bleak,请先: pip install bleak pillow")
try:
    from PIL import Image
except ImportError:
    raise SystemExit("缺少 Pillow,请先: pip install bleak pillow")

from screen_capture_protocol import (
    Ack,
    CaptureAssembler,
    CaptureProtocolError,
    Complete,
    rgb565le_to_image,
)

# ---- 128 位 UUID(由固件 TRAECARD 基址推出,末字节为字段号)----
SVC_UUID    = "54524145-4341-5244-0000-000000000000"
CMD_UUID    = "54524145-4341-5244-0000-000000000010"   # 命令写
NOTIFY_UUID = "54524145-4341-5244-0000-000000000011"   # 状态 notify
GAME_UUID   = "54524145-4341-5244-0000-000000000013"   # 游戏积分读(9B blob)
CAPTURE_UUID = "54524145-4341-5244-0000-000000000014"  # 屏幕截图 notify

DEFAULT_NAME = "TRAE-CARD"

# 帧 / 子协议常量(需与固件一致)
FRAME_VER   = 1
TYPE_JSON   = 0x01
TYPE_JPG    = 0x02
JPG_BEGIN   = 0x00
JPG_DATA    = 0x01
JPG_END     = 0x02
TYPE_SCORE  = 0x03            # RTTTL 乐谱
SCORE_BEGIN = 0x00
SCORE_DATA  = 0x01
SCORE_END   = 0x02
TYPE_CAPTURE = 0x06
SC_OP_START, SC_OP_ACK, SC_OP_FINISH, SC_OP_CANCEL = 0x01, 0x02, 0x03, 0x04
MAX_PAYLOAD = 256              # 固件 FRAME_MAX_PAYLOAD
JPG_CHUNK   = 220             # 每个 DATA 帧的图片/乐谱字节(帧 payload = 1+chunk ≤ 256)
SCR_W, SCR_H = 240, 320
AVATAR_NAME_RE = re.compile(r"^[a-z0-9_-]{1,15}$")


def load_avatar_names(catalog_path=None):
    """Load the catalog emitted beside imgava.bin, with a source-tree fallback."""
    project_root = Path(__file__).resolve().parent.parent
    candidates = ([Path(catalog_path)] if catalog_path else [
        Path(__file__).resolve().with_name("avatar_catalog.json"),
        project_root / "build" / "avatar_catalog.json",
    ])
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            names = payload.get("avatars", [])
            if (payload.get("version") == 1 and isinstance(names, list) and
                    len(names) >= 1 and all(isinstance(n, str) and AVATAR_NAME_RE.fullmatch(n)
                                            for n in names)):
                return tuple(names)
        except (OSError, ValueError, TypeError):
            continue
    source_names = sorted(path.stem for path in (project_root / "assets" / "avatars").glob("*.png")
                          if AVATAR_NAME_RE.fullmatch(path.stem))
    return tuple(source_names) or ("default",)
RTTTL_SAMPLES = {
    "Super Mario": "Mario:d=4,o=5,b=100:16e6,16e6,32p,16e6,32p,16c6,16e6,16g6,8p,16g,8p",
    "Tetris": "Tetris:d=4,o=5,b=160:e6,8b,8c6,8d6,16e6,16d6,8c6,8b,a,8a,8c6,e6,8d6,8c6,b,8b,8c6,d6,e6,c6,a,2a",
    "Nokia": "Nokia:d=4,o=5,b=125:8e6,8d6,f#,g#,8c#6,8b,d,e,8b,8a,c#,e,2a",
}

# notify 状态码(CFG_ST_*,现三类业务共用同一套码,见 hal_config.h)
STATUS = {
    0x00: "ACK(进行中)", 0x01: "完成✓",
    0x10: "帧格式错", 0x11: "重组超时", 0x12: "时序错", 0x13: "解析失败",
    0x14: "无有效键", 0x15: "超长", 0x16: "存储失败", 0x17: "解码失败",
    0x18: "不支持的类型", 0x19: "JPG格式不支持(渐进式?)", 0x1A: "JPG数据不完整",
    0x1B: "设备忙,稍后重试",
    0x1C: "截图通道未连接或未订阅",
    0x1D: "头像名称不存在或资源损坏",
}


def status_text(_msg_type, status):
    return STATUS.get(status, f"未知(0x{status:02X})")


def _save_capture_image(image, dialog=filedialog):
    """Atomically save a JPEG without exposing or deleting a partial destination."""
    path = dialog.asksaveasfilename(
        defaultextension=".jpg",
        initialfile=time.strftime("trae-card-%Y%m%d-%H%M%S.jpg"),
        filetypes=[("JPEG 图片", "*.jpg *.jpeg")])
    if not path:
        return None
    directory = os.path.dirname(os.path.abspath(path))
    prefix = f".{os.path.basename(path)}."
    fd, temporary_path = tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=directory)
    os.close(fd)
    try:
        image.save(temporary_path, "JPEG", quality=90)
        os.replace(temporary_path, path)
    except Exception:
        try:
            os.remove(temporary_path)
        except OSError:
            pass
        raise
    return path


def convert_to_jpg(path, quality=85):
    """全屏图:转 ≤240×320、宽高 8 的倍数的基线 JPEG bytes。返回 (data, (w,h))。"""
    im = Image.open(path)
    if im.mode != "RGB":
        im = im.convert("RGB")
    im.thumbnail((SCR_W, SCR_H))          # 等比缩小(不放大)
    w, h = im.size
    w8, h8 = w - (w % 8), h - (h % 8)     # 向下取整到 8 的倍数
    if w8 < 8 or h8 < 8:
        raise ValueError("图片过小,无法对齐到 8 的倍数")
    if (w8, h8) != (w, h):
        im = im.crop((0, 0, w8, h8))
    buf = io.BytesIO()
    # progressive 默认关闭 = 基线 JPEG(固件解码器只吃基线)
    im.save(buf, "JPEG", quality=int(quality))
    return buf.getvalue(), (w8, h8)


class BleWorker:
    """在后台线程跑 asyncio 事件循环,承载所有 bleak 操作。"""

    def __init__(self, on_event):
        # on_event(kind, payload) 会被从后台线程调用,GUI 侧需线程安全地转回主线程
        self.on_event = on_event
        self.loop = asyncio.new_event_loop()
        self.client = None
        self._devices = []
        self._disconnect_reported = True
        self._init_capture_state()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _init_capture_state(self):
        self._capture_assembler = CaptureAssembler()
        self._capture_active = False
        self._capture_started_at = None
        self._capture_cancel_requested = False
        self._capture_terminating = False

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    # ---- 协程 ----
    async def _scan(self, name_filter):
        self.on_event("log", f"扫描中(5s)...")
        found = await BleakScanner.discover(timeout=5.0)
        devs = []
        for d in found:
            nm = d.name or ""
            if name_filter and name_filter.lower() not in nm.lower():
                continue
            devs.append((d.address, nm))
        self._devices = devs
        self.on_event("devices", devs)
        self.on_event("log", f"扫描完成,匹配 {len(devs)} 台设备")

    async def _connect(self, address):
        self.on_event("log", f"连接 {address} ...")
        cli = BleakClient(address, disconnected_callback=self._on_ble_disconnect)
        await cli.connect()
        self.client = cli
        self._disconnect_reported = False
        try:
            await cli.start_notify(NOTIFY_UUID, self._on_notify)
            await cli.start_notify(CAPTURE_UUID, self._on_capture_notify)
        except Exception:
            try:
                await cli.disconnect()
            finally:
                self._mark_disconnected(cli)
            raise
        mtu = getattr(cli, "mtu_size", 23) or 23
        self.on_event("connected", (address, mtu))
        self.on_event("log", f"已连接,MTU={mtu}")

    async def _disconnect(self):
        cli = self.client
        if cli:
            try:
                await cli.disconnect()
            except Exception:
                pass
            finally:
                self._mark_disconnected(cli)

    def _on_ble_disconnect(self, client):
        self._mark_disconnected(client)

    def _mark_disconnected(self, client):
        if client is not None and client is not self.client:
            return
        self.client = None
        if self._capture_active:
            self.on_event(
                "log",
                f"✗ 截图因断连终止 ({self._capture_assembler.received_bytes}B, "
                f"{self._capture_elapsed():.2f}s)",
            )
        self._reset_capture()
        if not self._disconnect_reported:
            self._disconnect_reported = True
            self.on_event("disconnected", None)
            self.on_event("log", "已断开")

    def _on_notify(self, _sender, data):
        if len(data) >= 2:
            t, s = data[0], data[1]
            self.on_event("log", f"◀ notify: type=0x{t:02X} status={s} ({status_text(t, s)})")
            if t == TYPE_CAPTURE:
                self.on_event("capture_status", (s, status_text(t, s)))
                if s >= 0x10 and self._capture_active:
                    self._capture_terminal_error(f"截图失败: {status_text(t, s)}")
        else:
            self.on_event("log", f"◀ notify(原始): {data.hex()}")

    def _on_capture_notify(self, _sender, data):
        """Bleak-loop callback: reassemble, queue events, and schedule control writes only."""
        if not self._capture_active or self._capture_terminating:
            self.on_event("log", "◀ 忽略非活动截图通知")
            return
        try:
            result = self._capture_assembler.feed(data)
        except CaptureProtocolError as exc:
            capture_id = self._capture_assembler.capture_id
            try:
                packet = bytes(data)
            except (TypeError, ValueError):
                packet = b""
            if capture_id is None and len(packet) >= 3:
                capture_id = int.from_bytes(packet[1:3], "little") or None
            transferred = self._capture_assembler.received_bytes
            elapsed = self._capture_elapsed()
            self._capture_assembler.cancel()
            self._capture_terminating = True
            self._schedule_capture_control(
                self._cancel_protocol_error(
                    capture_id, f"截图协议错误: {exc}", transferred, elapsed),
                "协议取消",
            )
            return

        if self._capture_cancel_requested:
            self._schedule_capture_control(
                self._cancel_and_reset(result.capture_id), "取消", cleanup_on_error=True)
            return

        ack = result.ack if isinstance(result, Complete) else result
        self._schedule_capture_control(self._ack_capture(ack), "ACK", cleanup_on_error=True)
        self.on_event(
            "capture_progress",
            (self._capture_assembler.received_bytes, self._capture_assembler.total_len),
        )
        if isinstance(result, Complete):
            elapsed = self._capture_elapsed()
            self.on_event(
                "log",
                f"◀ 截图传输完成 {len(result.data)}B,耗时 {elapsed:.2f}s",
            )
            self.on_event("capture_complete", result)

    def _capture_elapsed(self):
        if self._capture_started_at is None:
            return 0.0
        return max(0.0, time.monotonic() - self._capture_started_at)

    def _reset_capture(self):
        self._capture_assembler.cancel()
        self._capture_active = False
        self._capture_started_at = None
        self._capture_cancel_requested = False
        self._capture_terminating = False

    def _capture_terminal_error(self, message):
        transferred = self._capture_assembler.received_bytes
        elapsed = self._capture_elapsed()
        self._reset_capture()
        self.on_event("log", f"✗ {message} ({transferred}B, {elapsed:.2f}s)")
        self.on_event("capture_error", message)

    def _schedule_capture_control(self, coro, label, cleanup_on_error=False):
        future = self.submit(coro)

        def done(fut):
            try:
                exc = fut.exception()
            except BaseException as callback_exc:
                exc = callback_exc
            if exc is not None:
                if cleanup_on_error:
                    self._capture_terminal_error(f"截图{label}发送失败: {exc}")
                else:
                    self.on_event("log", f"✗ 截图{label}发送失败: {exc!r}")

        future.add_done_callback(done)
        return future

    def _write_chunk_size(self):
        mtu = getattr(self.client, "mtu_size", 23) or 23
        return max(20, min(mtu - 3, 244))

    async def _write_frame(self, msg_type, payload, response=True):
        """构一帧并按 MTU 分包写命令特征。response=True 带响应(保证有序可靠,配置/图片/乐谱用);
        response=False 无响应(免每帧往返 ACK,音频流用,配合固件不逐帧 notify + 客户端实时节流)。"""
        if len(payload) > MAX_PAYLOAD:
            raise ValueError(f"payload 超长 {len(payload)} > {MAX_PAYLOAD}")
        frame = bytes([FRAME_VER, msg_type, len(payload) & 0xFF, (len(payload) >> 8) & 0xFF]) + payload
        step = self._write_chunk_size()
        for i in range(0, len(frame), step):
            await self.client.write_gatt_char(CMD_UUID, frame[i:i + step], response=response)

    async def _start_capture(self):
        await self._write_frame(TYPE_CAPTURE, bytes([SC_OP_START]))

    async def _ack_capture(self, ack):
        payload = bytes([SC_OP_ACK]) + ack.capture_id.to_bytes(2, "little") + ack.seq.to_bytes(2, "little")
        await self._write_frame(TYPE_CAPTURE, payload)

    async def _finish_capture(self, capture_id):
        payload = bytes([SC_OP_FINISH]) + capture_id.to_bytes(2, "little")
        await self._write_frame(TYPE_CAPTURE, payload)

    async def _cancel_capture(self, capture_id):
        payload = bytes([SC_OP_CANCEL]) + capture_id.to_bytes(2, "little")
        await self._write_frame(TYPE_CAPTURE, payload)

    async def _finish_and_reset(self, capture_id):
        await self._finish_capture(capture_id)
        self._reset_capture()
        self.on_event("capture_finished", capture_id)

    async def _cancel_and_reset(self, capture_id):
        transferred = self._capture_assembler.received_bytes
        elapsed = self._capture_elapsed()
        await self._cancel_capture(capture_id)
        self._reset_capture()
        self.on_event("log", f"▶ 已取消截图 ({transferred}B, {elapsed:.2f}s)")
        self.on_event("capture_cancelled", capture_id)

    async def _cancel_protocol_error(self, capture_id, message, transferred, elapsed):
        terminal_message = message
        try:
            if capture_id is not None and self.client is not None:
                await self._cancel_capture(capture_id)
        except Exception as exc:
            terminal_message = f"{message}; CANCEL 发送失败: {exc}"
        self._reset_capture()
        self.on_event("log", f"✗ {terminal_message} ({transferred}B, {elapsed:.2f}s)")
        self.on_event("capture_error", terminal_message)

    async def _request_cancel_capture(self, capture_id=None):
        if not self._capture_active:
            return
        active_id = capture_id or self._capture_assembler.capture_id
        if active_id is None:
            self._capture_cancel_requested = True
            self.on_event("capture_status", (None, "等待截图 ID 后取消..."))
            return
        await self._cancel_and_reset(active_id)

    async def _send_json(self, text):
        payload = text.encode("utf-8")
        if len(payload) > MAX_PAYLOAD:
            self.on_event("log", f"✗ JSON 过长 {len(payload)}B > {MAX_PAYLOAD}B")
            return
        await self._write_frame(TYPE_JSON, payload)
        self.on_event("log", f"▶ 已发送 JSON ({len(payload)}B),等待 notify...")

    async def _send_image(self, data, size, fmt):
        await self._write_frame(TYPE_JSON, b'{"img_mode":"fullscreen"}')
        where = "全屏"
        total = len(data)
        self.on_event("log", f"▶ 发送{where}图 {fmt} {size[0]}x{size[1]}, {total}B ...")
        await self._write_frame(TYPE_JPG, bytes([JPG_BEGIN]) + total.to_bytes(4, "little"))
        off = 0
        while off < total:
            c = data[off:off + JPG_CHUNK]
            await self._write_frame(TYPE_JPG, bytes([JPG_DATA]) + c)
            off += len(c)
            self.on_event("progress", (off, total))
        await self._write_frame(TYPE_JPG, bytes([JPG_END]))
        self.on_event("log", "▶ 图片发送完成,等待 notify...")

    async def _send_score(self, text):
        data = text.encode("utf-8")
        total = len(data)
        self.on_event("log", f"▶ 发送乐谱 {total}B ...")
        await self._write_frame(TYPE_SCORE, bytes([SCORE_BEGIN]) + total.to_bytes(2, "little"))
        off = 0
        while off < total:
            c = data[off:off + JPG_CHUNK]
            await self._write_frame(TYPE_SCORE, bytes([SCORE_DATA]) + c)
            off += len(c)
        await self._write_frame(TYPE_SCORE, bytes([SCORE_END]))
        self.on_event("log", "▶ 乐谱发送完成,等待 notify...")

    # ---- 供 GUI 调用(线程安全地投递到 loop) ----
    def scan(self, name_filter):
        fut = self.submit(self._scan(name_filter))
        fut.add_done_callback(self._err_cb)

    def connect(self, address):
        fut = self.submit(self._connect(address))
        fut.add_done_callback(self._err_cb)

    def disconnect(self):
        fut = self.submit(self._disconnect())
        fut.add_done_callback(self._err_cb)

    def start_capture(self):
        if not self.client:
            self.on_event("capture_error", "未连接")
            return
        if self._capture_active:
            return
        self._capture_assembler.cancel()
        self._capture_active = True
        self._capture_started_at = time.monotonic()
        self._capture_cancel_requested = False
        self.on_event("capture_status", (None, "正在请求截图..."))
        self._schedule_capture_control(self._start_capture(), "START", cleanup_on_error=True)

    def finish_capture(self, capture_id):
        if not self.client:
            self._capture_terminal_error("保存完成但设备已断开")
            return
        self._schedule_capture_control(
            self._finish_and_reset(capture_id), "FINISH", cleanup_on_error=True)

    def cancel_capture(self, capture_id=None):
        if not self.client:
            self._reset_capture()
            self.on_event("capture_cancelled", capture_id)
            return
        self._schedule_capture_control(
            self._request_cancel_capture(capture_id), "CANCEL", cleanup_on_error=True)

    def send_json(self, text):
        if not self.client:
            self.on_event("log", "✗ 未连接")
            return
        fut = self.submit(self._send_json(text))
        fut.add_done_callback(self._err_cb)

    def send_image(self, data, size, fmt):
        if not self.client:
            self.on_event("log", "✗ 未连接")
            return
        fut = self.submit(self._send_image(data, size, fmt))
        fut.add_done_callback(self._err_cb)

    def send_score(self, text):
        if not self.client:
            self.on_event("log", "✗ 未连接")
            return
        fut = self.submit(self._send_score(text))
        fut.add_done_callback(self._err_cb)

    async def _read_gamescore(self):
        # 读 …0013:9B blob = [0]ver=0x01 [1..4]game_total u32 LE [5..8]game_best u32 LE
        data = await self.client.read_gatt_char(GAME_UUID)
        if len(data) < 9:
            self.on_event("log", f"◀ 游戏积分(原始,长度异常): {data.hex()}")
            return
        ver = data[0]
        total = int.from_bytes(data[1:5], "little")
        best  = int.from_bytes(data[5:9], "little")
        self.on_event("log", f"◀ 游戏积分: ver=0x{ver:02X} 累计={total} 单局最高={best}")

    def read_gamescore(self):
        if not self.client:
            self.on_event("log", "✗ 未连接")
            return
        fut = self.submit(self._read_gamescore())
        fut.add_done_callback(self._err_cb)

    def _err_cb(self, fut):
        exc = fut.exception()
        if exc:
            self.on_event("log", f"✗ 出错: {exc!r}")


class App:
    def __init__(self, root):
        self.root = root
        if isinstance(root, (tk.Tk, tk.Toplevel)):   # 嵌入 Frame(工具箱)时跳过窗口属性
            root.title("trae_card BLE 上位机")
            root.geometry("640x680")
        self.q = queue.Queue()
        self.worker = BleWorker(self._post_event)
        self.jpg_data = None
        self.jpg_size = None
        self.img_fmt = None
        self._capture_active = False
        self._capture_started_at = None
        self._capture_bulk_states = None
        self._build_ui()
        self.root.after(80, self._drain)

    # 后台线程 → 主线程:事件先入队,主线程定时取出处理
    def _post_event(self, kind, payload):
        self.q.put((kind, payload))

    def _drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                self._handle(kind, payload)
        except queue.Empty:
            pass
        self.root.after(80, self._drain)

    def _handle(self, kind, payload):
        if kind == "log":
            self._log(payload)
        elif kind == "devices":
            self.dev_combo["values"] = [f"{nm or '(无名)'}  [{addr}]" for addr, nm in payload]
            self._devices = payload
            if payload:
                self.dev_combo.current(0)
        elif kind == "connected":
            addr, mtu = payload
            self.status_var.set(f"已连接  MTU={mtu}")
            self.btn_connect.config(text="断开")
            self.connected = True
            self._update_capture_buttons()
        elif kind == "disconnected":
            self.status_var.set("未连接")
            self.btn_connect.config(text="连接")
            self.connected = False
            self._end_capture_ui("连接已断开")
        elif kind == "progress":
            off, total = payload
            self.prog["value"] = off * 100 / total if total else 0
            self.prog_var.set(f"{off}/{total}B")
        elif kind == "capture_progress":
            if self._capture_active:
                off, total = payload
                self.capture_prog["value"] = off * 100 / total if total else 0
                self.capture_status_var.set(f"接收中 {off}/{total}B")
        elif kind == "capture_status":
            _status, text = payload
            if self._capture_active:
                self.capture_status_var.set(text)
        elif kind == "capture_complete":
            self._handle_capture_complete(payload)
        elif kind == "capture_error":
            self._log(f"✗ {payload}")
            self._end_capture_ui(payload)
        elif kind == "capture_cancelled":
            self._end_capture_ui("已取消")
        elif kind == "capture_finished":
            self._end_capture_ui("已保存")

    # ---- UI ----
    def _build_ui(self):
        self.connected = False
        self._devices = []

        top = ttk.LabelFrame(self.root, text="连接")
        top.pack(fill="x", padx=8, pady=6)
        ttk.Label(top, text="名称过滤:").grid(row=0, column=0, padx=4, pady=4)
        self.name_var = tk.StringVar(value=DEFAULT_NAME)
        ttk.Entry(top, textvariable=self.name_var, width=14).grid(row=0, column=1, padx=4)
        self.btn_scan = ttk.Button(top, text="扫描", command=self._on_scan)
        self.btn_scan.grid(row=0, column=2, padx=4)
        self.dev_combo = ttk.Combobox(top, width=40, state="readonly")
        self.dev_combo.grid(row=0, column=3, padx=4)
        self.btn_connect = ttk.Button(top, text="连接", command=self._on_connect)
        self.btn_connect.grid(row=0, column=4, padx=4)
        self.status_var = tk.StringVar(value="未连接")
        ttk.Label(top, textvariable=self.status_var, foreground="blue").grid(
            row=1, column=0, columnspan=5, sticky="w", padx=6, pady=2)

        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=8, pady=6)

        # --- JSON 配置页 ---
        p1 = ttk.Frame(nb)
        nb.add(p1, text="配置(JSON)")
        ttk.Label(p1, text="发送的 JSON(有哪个键改哪个字段):").pack(anchor="w", padx=6, pady=4)
        self.json_text = tk.Text(p1, height=8)
        self.json_text.pack(fill="x", padx=6)
        self.json_text.insert("1.0",
            '{"nickname":"NEO","avatar_name":"default","token":1580,"token_max":40000,"sleep_min":5,"volume":50}')
        row = ttk.Frame(p1); row.pack(fill="x", padx=6, pady=6)
        self.btn_send_json = ttk.Button(row, text="发送配置", command=self._on_send_json)
        self.btn_send_json.pack(side="left")
        self.btn_sync_time = ttk.Button(row, text="同步时间", command=self._on_sync_time)
        self.btn_sync_time.pack(side="left", padx=6)
        self.btn_read_game = ttk.Button(row, text="读游戏积分", command=self._on_read_game)
        self.btn_read_game.pack(side="left")
        self.btn_clear_game = ttk.Button(row, text="清零游戏积分", command=self._on_clear_game)
        self.btn_clear_game.pack(side="left", padx=6)
        ttk.Label(row, text="(当前主页显示: nickname / avatar_name / token / token_max。"
                            "role/level/battery 等仍可发但已不显示)",
                  foreground="gray", wraplength=560, justify="left").pack(side="left", padx=8)
        avatar_row = ttk.Frame(p1); avatar_row.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Label(avatar_row, text="内置头像:").pack(side="left")
        self.avatar_names = load_avatar_names()
        self.avatar_name = tk.StringVar(value=self.avatar_names[0])
        self.avatar_combo = ttk.Combobox(
            avatar_row, width=18, state="normal", textvariable=self.avatar_name,
            values=self.avatar_names)
        self.avatar_combo.pack(side="left", padx=6)
        self.btn_send_avatar = ttk.Button(
            avatar_row, text="应用头像", command=self._on_send_avatar)
        self.btn_send_avatar.pack(side="left")

        # --- 图片页 ---
        p2 = ttk.Frame(nb)
        nb.add(p2, text="图片")

        r1 = ttk.Frame(p2); r1.pack(fill="x", padx=6, pady=6)
        ttk.Button(r1, text="选择全屏图片...", command=self._on_pick_image).pack(side="left")
        self.q_cap = ttk.Label(r1, text="JPG质量:")
        self.q_cap.pack(side="left", padx=(12, 2))
        self.quality = tk.IntVar(value=85)
        self.q_scale = ttk.Scale(r1, from_=50, to=95, variable=self.quality, orient="horizontal",
                                 length=120, command=lambda e: self._reconvert())
        self.q_scale.pack(side="left")
        self.q_lbl = ttk.Label(r1, text="85"); self.q_lbl.pack(side="left", padx=4)
        self.img_info = tk.StringVar(value="未选择图片")
        ttk.Label(p2, textvariable=self.img_info, foreground="blue", wraplength=600,
                  justify="left").pack(anchor="w", padx=6)
        self.btn_send_img = ttk.Button(p2, text="发送全屏图片", command=self._on_send_image, state="disabled")
        self.btn_send_img.pack(anchor="w", padx=6, pady=6)
        self.prog = ttk.Progressbar(p2, maximum=100)
        self.prog.pack(fill="x", padx=6, pady=2)
        self.prog_var = tk.StringVar(value="")
        ttk.Label(p2, textvariable=self.prog_var, foreground="gray").pack(anchor="w", padx=6)

        # --- 乐谱页 ---
        p3 = ttk.Frame(nb)
        nb.add(p3, text="乐谱(RTTTL)")
        r3 = ttk.Frame(p3); r3.pack(fill="x", padx=6, pady=6)
        ttk.Label(r3, text="示例:").pack(side="left")
        self.sample_combo = ttk.Combobox(r3, width=18, state="readonly",
                                          values=list(RTTTL_SAMPLES.keys()))
        self.sample_combo.pack(side="left", padx=4)
        self.sample_combo.bind("<<ComboboxSelected>>", self._on_pick_sample)
        ttk.Label(p3, text="RTTTL 文本（发送后立即播放，并保存为下次开机音乐）:").pack(anchor="w", padx=6)
        self.score_text = tk.Text(p3, height=6)
        self.score_text.pack(fill="x", padx=6)
        self.score_text.insert("1.0", RTTTL_SAMPLES["Super Mario"])
        self.btn_send_score = ttk.Button(p3, text="发送乐谱", command=self._on_send_score)
        self.btn_send_score.pack(anchor="w", padx=6, pady=6)

        # --- 截图页 ---
        p4 = ttk.Frame(nb)
        nb.add(p4, text="截图")
        capture_row = ttk.Frame(p4)
        capture_row.pack(fill="x", padx=6, pady=(10, 6))
        self.btn_capture_start = ttk.Button(
            capture_row, text="截图并保存", command=self._on_capture_start, state="disabled")
        self.btn_capture_start.pack(side="left")
        self.btn_capture_cancel = ttk.Button(
            capture_row, text="取消", command=self._on_capture_cancel, state="disabled")
        self.btn_capture_cancel.pack(side="left", padx=6)
        self.capture_prog = ttk.Progressbar(p4, maximum=100)
        self.capture_prog.pack(fill="x", padx=6, pady=4)
        self.capture_status_var = tk.StringVar(value="连接设备后可截图")
        ttk.Label(p4, textvariable=self.capture_status_var, foreground="blue").pack(
            anchor="w", padx=6, pady=2)
        self._bulk_buttons = [
            self.btn_scan, self.btn_send_json, self.btn_sync_time, self.btn_read_game,
            self.btn_clear_game, self.btn_send_avatar,
            self.btn_send_img, self.btn_send_score,
        ]

        # --- 日志 ---
        lf = ttk.LabelFrame(self.root, text="日志 / notify")
        lf.pack(fill="both", expand=True, padx=8, pady=6)
        self.log_text = tk.Text(lf, height=10, state="disabled", bg="#111", fg="#0f0")
        self.log_text.pack(fill="both", expand=True, padx=4, pady=4)

    def _log(self, msg):
        self.log_text.config(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _update_capture_buttons(self):
        self.btn_capture_start.config(
            state="normal" if self.connected and not self._capture_active else "disabled")
        self.btn_capture_cancel.config(state="normal" if self._capture_active else "disabled")

    def _begin_capture_ui(self):
        if self._capture_active:
            return False
        self._capture_active = True
        self._capture_started_at = time.monotonic()
        self.capture_prog["value"] = 0
        self.capture_status_var.set("正在请求截图...")
        self._capture_bulk_states = [button.cget("state") for button in self._bulk_buttons]
        for button in self._bulk_buttons:
            button.config(state="disabled")
        self._update_capture_buttons()
        return True

    def _end_capture_ui(self, status):
        self._capture_active = False
        self._capture_started_at = None
        if self._capture_bulk_states is not None:
            for button, state in zip(self._bulk_buttons, self._capture_bulk_states):
                button.config(state=state)
            self._capture_bulk_states = None
        self.capture_status_var.set(status)
        self._update_capture_buttons()

    def _handle_capture_complete(self, complete):
        if not self._capture_active:
            return
        try:
            image = rgb565le_to_image(complete.data, complete.width, complete.height)
            path = _save_capture_image(image)
        except Exception as exc:
            self.worker.cancel_capture(complete.capture_id)
            self._log(f"✗ 保存截图失败: {exc}")
            messagebox.showerror("保存截图失败", str(exc))
            self.capture_status_var.set("保存失败,正在取消...")
            self.btn_capture_cancel.config(state="disabled")
            return
        if path:
            self.worker.finish_capture(complete.capture_id)
            self._log(f"✓ 截图已保存: {path}")
            self.capture_status_var.set("已保存,正在确认...")
            self.btn_capture_cancel.config(state="disabled")
        else:
            self.worker.cancel_capture(complete.capture_id)
            self.capture_status_var.set("正在取消...")
            self.btn_capture_cancel.config(state="disabled")

    # ---- 事件 ----
    def _on_scan(self):
        self.worker.scan(self.name_var.get().strip())

    def _on_connect(self):
        if self.connected:
            self.worker.disconnect()
            return
        idx = self.dev_combo.current()
        if idx < 0 or idx >= len(self._devices):
            messagebox.showwarning("提示", "请先扫描并选择设备")
            return
        addr = self._devices[idx][0]
        self.worker.connect(addr)

    def _on_send_json(self):
        text = self.json_text.get("1.0", "end").strip()
        if not text:
            return
        self.worker.send_json(text)

    def _on_send_avatar(self):
        name = self.avatar_name.get()
        if AVATAR_NAME_RE.fullmatch(name):
            self.worker.send_json('{"avatar_name":"%s"}' % name)
            self._log(f"▶ 切换内置头像: {name}")
        else:
            messagebox.showwarning("头像名称无效", "仅允许 1–15 位小写字母、数字、_ 或 -")

    def _on_sync_time(self):
        # 卡片按"本地秒当作 UTC"存储(见协议),故这里把本地墙钟换算成 Unix 秒下发。
        # tm_gmtoff = 本地相对 UTC 的偏移秒(东正),已含夏令时。
        now = time.time()
        local_epoch = int(now + time.localtime(now).tm_gmtoff)
        self.worker.send_json('{"time":%d}' % local_epoch)
        self._log(f"▶ 同步时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))} "
                  f"(local_epoch={local_epoch})")

    def _on_read_game(self):
        # 读工牌端游戏积分(累计/单局最高)。小程序同步流程:先读走此值上报,再清零。
        self.worker.read_gamescore()

    def _on_clear_game(self):
        # 下发 game_clear 清零工牌端累计/最高积分(读走后调用,见协议"游戏积分同步")。
        self.worker.send_json('{"game_clear":true}')
        self._log("▶ 已下发清零游戏积分")

    def _on_pick_image(self):
        path = filedialog.askopenfilename(
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tiff"), ("所有文件", "*.*")])
        if not path:
            return
        self._img_path = path
        self._reconvert()

    def _reconvert(self):
        self.q_lbl.config(text=str(self.quality.get()))
        if not getattr(self, "_img_path", None):
            return
        try:
            data, size = convert_to_jpg(self._img_path, self.quality.get())
            fmt = "JPG"
        except Exception as e:
            self.img_info.set(f"转换失败: {e}")
            self.btn_send_img.config(state="disabled")
            self.jpg_data = None
            return
        self.jpg_data, self.jpg_size, self.img_fmt = data, size, fmt
        frames = (len(data) + JPG_CHUNK - 1) // JPG_CHUNK
        self.img_info.set(f"[全屏] {fmt} {size[0]}x{size[1]}, {len(data)}B, 约 {frames} 个数据帧")
        self.btn_send_img.config(state="disabled" if self._capture_active else "normal")

    def _on_send_image(self):
        if not self.jpg_data:
            return
        self.prog["value"] = 0
        self.worker.send_image(self.jpg_data, self.jpg_size, self.img_fmt)

    def _on_capture_start(self):
        if not self.connected or not self._begin_capture_ui():
            return
        self.worker.start_capture()

    def _on_capture_cancel(self):
        if not self._capture_active:
            return
        self.capture_status_var.set("正在取消...")
        self.btn_capture_cancel.config(state="disabled")
        self.worker.cancel_capture()

    def _on_pick_sample(self, _e):
        name = self.sample_combo.get()
        if name in RTTTL_SAMPLES:
            self.score_text.delete("1.0", "end")
            self.score_text.insert("1.0", RTTTL_SAMPLES[name])

    def _on_send_score(self):
        text = self.score_text.get("1.0", "end").strip()
        if text:
            self.worker.send_score(text)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
