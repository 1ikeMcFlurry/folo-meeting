#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TRAE-CARD token 广播发送器 —— 模拟 Android pad,给卡片发积分变动广播。

协议见 doc/ble-broadcast-protocol-for-gateway.md。

三种运行方式:
  1) GUI(需 Windows 原生 Python,WSL2 访问不到蓝牙电台):
       py -m pip install -r tools/requirements-win.txt
       py tools/token_broadcast_gui.py
  2) 自检(任何平台,不需要蓝牙,用于验证帧组装逻辑):
       python3 tools/token_broadcast_gui.py --selftest
  3) 只算帧不发(任何平台,把 hex 贴进 nRF Connect 的 Advertiser 手工发):
       python3 tools/token_broadcast_gui.py --frame --sn f0f5bd84a86c \
               --op add --seq 1 --balance 1234

签名密钥输入优先级: --pk-file > CARDID_PK 环境变量 > --pk / GUI 输入框。
这些旧参数名为兼容保留，实际值必须是目标设备的 DeviceSecret，不是 ProductKey。
不建议走命令行 --pk,它会进 shell 历史。
"""
import argparse
import csv
import hashlib
import hmac
import json
import os
import struct
import sys

# ---------------------------------------------------------------------------
# 协议常量(与 components/core/services/include/services/token_bcast.h 对应)
# ---------------------------------------------------------------------------
COMPANY_ID   = 0xFFFF          # 由 addManufacturerData 的 manufacturerId 提供
MAGIC        = b"HB"           # 0x48 0x42
HDR          = 0x12            # 高 4 位=版本(1),低 4 位=类型(2)
MFG_LEN      = 26              # 卡片校验:厂商数据必须恰好这么长
SIGN_LEN     = 18              # 签名覆盖 mfg[0..17]
MAC_LEN      = 8
BALANCE_MAX  = 999999
SEQ_MAX      = 0xFFFFFF        # uint24
ADV_SECONDS  = 3.0             # 协议建议:每条消息持续广播 3 秒

OPS = {"add": 0x01, "sub": 0x02, "fail": 0x03, "sync": 0x04, "pet": 0x05}
OP_LABEL = {0x01: "加分成功", 0x02: "扣分成功", 0x03: "失败", 0x04: "静默同步",
            0x05: "宠物开关(balance=宠物类型,0=关)"}

SEQ_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "token_seq_state.json")

# 文档 §7 的公开测试向量(假 DeviceSecret,不是真实设备密钥)
TV_PK      = "TESTPK0123456789"
TV_SN      = "f0f5bd84a86c"
TV_EXPECT  = ("ff ff 48 42 12 01 f0 f5 bd 84 a8 6c "
              "01 00 00 d2 04 00 19 3a 4e 54 c4 dc 27 eb")


# ---------------------------------------------------------------------------
# 纯逻辑:帧组装。无 GUI、无 WinRT 依赖,任何平台都能跑,便于自检。
# ---------------------------------------------------------------------------
def build_frame(sn: str, op: int, seq: int, balance: int, pk: str) -> bytes:
    """组装 26 字节厂商数据(含公司 ID)。参数非法时抛 ValueError。
    pk 现在应是该设备的 DeviceSecret(每设备密钥),不再是全厂共享 ProductKey。"""
    sn = sn.strip().lower().replace(":", "").replace("-", "").replace(" ", "")
    if len(sn) != 12:
        raise ValueError(f"SN 必须是 12 个十六进制字符(去冒号),当前 {len(sn)} 个")
    try:
        target = bytes.fromhex(sn)
    except ValueError:
        raise ValueError("SN 含非十六进制字符")

    if op not in OP_LABEL:
        raise ValueError("op 必须是 0x01/0x02/0x03/0x04/0x05")
    # seq 现在只是 nonce:卡片按消息签名去重,不检查它的数值,但**每条新消息必须不同**,
    # 否则内容相同的两次操作会撞成同一个签名,第二条被当成重复包丢掉。
    # 本地递增是最简单的保证方式,所以工具仍按递增使用它。
    if not (0 <= seq <= SEQ_MAX):
        raise ValueError(f"seq 必须在 0..{SEQ_MAX}")
    if not (0 <= balance <= BALANCE_MAX):
        raise ValueError(f"balance 必须在 0..{BALANCE_MAX}")
    if op == OPS["pet"] and balance > 255:
        raise ValueError("宠物类型必须在 0..255（0=关闭）")
    if not pk:
        raise ValueError("DeviceSecret 为空 —— 没有它算不出签名")

    body = (struct.pack("<H", COMPANY_ID) + MAGIC + bytes([HDR, op]) + target
            + struct.pack("<I", seq)[:3]          # uint24 小端
            + struct.pack("<I", balance)[:3])     # uint24 小端
    assert len(body) == SIGN_LEN, len(body)

    # 密钥用 DeviceSecret 的原始 ASCII 字节,不做 hex 解码 —— 这是最容易写错的一处,
    # 错了的现象是"卡片一直响失败音",或干脆全场无反应。
    mac = hmac.new(pk.encode("ascii"), body, hashlib.sha256).digest()[:MAC_LEN]

    frame = body + mac
    assert len(frame) == MFG_LEN, len(frame)
    return frame


def payload_for_android(frame: bytes) -> bytes:
    """Android 的 addManufacturerData(0xFFFF, byte[]) 里那 24 字节 —— 去掉公司 ID。"""
    return frame[2:]


def hexs(b: bytes) -> str:
    return " ".join(f"{x:02x}" for x in b)


def describe(frame: bytes) -> str:
    """逐字段拆解,便于与文档 §3 的表对照。"""
    f = frame
    rows = [
        ("[0..1]",   f[0:2],   "公司 ID(manufacturerId=0xFFFF 提供)"),
        ("[2..3]",   f[2:4],   "魔数 'H''B'"),
        ("[4]",      f[4:5],   f"hdr: 版本={f[4] >> 4} 类型={f[4] & 0x0F}"),
        ("[5]",      f[5:6],   f"op = {OP_LABEL.get(f[5], '?')}"),
        ("[6..11]",  f[6:12],  f"target = {f[6:12].hex()}"),
        ("[12..14]", f[12:15], f"seq = {int.from_bytes(f[12:15], 'little')}"),
        ("[15..17]", f[15:18], f"balance = {int.from_bytes(f[15:18], 'little')}"),
        ("[18..25]", f[18:26], "mac(HMAC-SHA256 前 8 字节)"),
    ]
    return "\n".join(f"  {o:<9} {hexs(v):<26} {d}" for o, v, d in rows)


# ---------------------------------------------------------------------------
# seq 计数器持久化。卡片按消息签名去重,不再要求 seq 全局单调,所以计数器回退
# 不会像旧设计那样锁死卡片。但仍需保证**每条新消息的 seq 不同**,否则内容相同的
# 两次操作会撞成同一个签名、第二条被当重复包丢掉。本地递增是最省事的保证方式。
# ---------------------------------------------------------------------------
def load_seq(sn: str) -> int:
    try:
        with open(SEQ_STATE_FILE, encoding="utf-8") as fp:
            return int(json.load(fp).get(sn.lower(), 0))
    except (OSError, ValueError, AttributeError):
        return 0


def save_seq(sn: str, seq: int) -> None:
    data = {}
    try:
        with open(SEQ_STATE_FILE, encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, ValueError):
        pass
    data[sn.lower()] = int(seq)
    try:
        with open(SEQ_STATE_FILE, "w", encoding="utf-8") as fp:
            json.dump(data, fp, indent=2, ensure_ascii=False)
    except OSError as e:
        print(f"警告: seq 状态写入失败({e}),下次可能重复使用同一个 seq", file=sys.stderr)


# 台账默认在仓库根(make_cardid.py 以 cwd 写),本脚本在 tools/,取其上级。
DEFAULT_LEDGER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "cardid_ledger.csv")


def resolve_device_secret(sn: str, ledger_path: str = "") -> str:
    """按 SN 从 cardid_ledger.csv 查该设备的 DeviceSecret(台账 key 列)。
    token 现在用每设备 DeviceSecret 验签,网关必须逐台用对应密钥签名。"""
    sn = sn.strip().lower().replace(":", "").replace("-", "").replace(" ", "")
    path = ledger_path or DEFAULT_LEDGER
    try:
        with open(path, newline="", encoding="utf-8") as fp:
            for row in csv.DictReader(fp):
                if row.get("sn", "").strip().lower() == sn:
                    key = row.get("key", "").strip()
                    if not key:
                        raise ValueError(f"台账里 SN={sn} 的 key 列为空")
                    return key
    except FileNotFoundError:
        raise ValueError(f"找不到台账 {path};先用 make_cardid.py 烧录登记,或手填签名密钥")
    raise ValueError(f"台账 {path} 里没有 SN={sn};确认该卡已烧录登记")


def resolve_key(sn: str, manual: str = "", pk_file: str = "", ledger: str = "") -> str:
    """签名密钥来源:手填/文件/CARDID_PK 环境变量(测试覆盖)优先;否则按 SN 查台账 DeviceSecret。"""
    if pk_file:
        with open(pk_file, encoding="utf-8") as fp:
            return fp.read().strip()
    if manual.strip():
        return manual.strip()
    env = os.environ.get("CARDID_PK", "").strip()
    if env:
        return env
    return resolve_device_secret(sn, ledger)


# ---------------------------------------------------------------------------
# 自检:对着文档 §7 的测试向量验证帧组装。不需要蓝牙,任何平台可跑。
# ---------------------------------------------------------------------------
def selftest() -> int:
    fails = 0

    def ck(cond, msg):
        nonlocal fails
        print(f"  {'✓' if cond else '✗'} {msg}")
        if not cond:
            fails += 1

    print("协议自检(对照 doc/ble-broadcast-protocol-for-gateway.md §7)")
    f = build_frame(TV_SN, OPS["add"], 1, 1234, TV_PK)
    ck(hexs(f) == TV_EXPECT, "测试向量逐字节一致")
    ck(len(f) == 26, "厂商数据 26 字节")
    ck(len(payload_for_android(f)) == 24, "Android byte[] 24 字节(卡在上限内)")
    ck(f[:2] == b"\xff\xff" and f[2:4] == b"HB", "公司 ID + 魔数")
    ck(f[4] == 0x12, "hdr = 0x12")

    # 签名范围:必须是 mfg[0..17] 18 字节,且**包含**开头的 FF FF
    expect_mac = hmac.new(TV_PK.encode(), f[:18], hashlib.sha256).digest()[:8]
    ck(f[18:] == expect_mac, "签名覆盖 mfg[0..17] 共 18 字节(含公司 ID)")
    wrong = hmac.new(TV_PK.encode(), f[2:18], hashlib.sha256).digest()[:8]
    ck(f[18:] != wrong, "签名**不是**只覆盖 Android byte[](漏掉 FF FF 是常见错误)")

    # 小端 uint24
    g = build_frame(TV_SN, OPS["add"], 0x123456, 0x0400, TV_PK)
    ck(g[12:15] == b"\x56\x34\x12", "seq 小端 uint24")
    ck(g[15:18] == b"\x00\x04\x00", "balance 小端 uint24")

    # target 不反转
    ck(g[6:12] == bytes.fromhex(TV_SN), "target = SN 直接 hex 解码,不反转字节序")

    # 边界拦截
    for bad, why in ((dict(seq=SEQ_MAX + 1), "seq 溢出被拒"),
                     (dict(balance=BALANCE_MAX + 1), "balance>999999 被拒"),
                     (dict(sn="f0f5bd84a86"), "SN 长度不对被拒"),
                     (dict(pk=""), "空 DeviceSecret 被拒")):
        kw = dict(sn=TV_SN, op=OPS["add"], seq=1, balance=1, pk=TV_PK)
        kw.update(bad)
        try:
            build_frame(**kw)
            ck(False, why)
        except ValueError:
            ck(True, why)

    ck(build_frame(TV_SN, OPS["add"], 1, BALANCE_MAX, TV_PK) is not None,
       "balance=999999 边界值通过")
    ck(build_frame(TV_SN, OPS["sync"], 1, 1, TV_PK)[5] == 0x04, "op=0x04 静默同步")
    try:
        build_frame(TV_SN, OPS["pet"], 1, 256, TV_PK)
        ck(False, "宠物类型>255 被拒")
    except ValueError:
        ck(True, "宠物类型>255 被拒")
    ck(build_frame(TV_SN, OPS["add"], 0, 1, TV_PK) is not None,
       "seq=0 现在合法(它只是 nonce)")

    print(f"\n{'全部通过' if fails == 0 else f'{fails} 项失败'}")
    return 1 if fails else 0


# ---------------------------------------------------------------------------
# WinRT 广播后端(仅 Windows)。惰性导入,便于上面的逻辑在任何平台自检。
# ---------------------------------------------------------------------------
class TokenAdvertiser:
    def __init__(self):
        self._pub = None
        self._token = None
        self._status_cb = None

    def on_status(self, cb):
        self._status_cb = cb

    def _on_status(self, sender, args):
        if self._status_cb:
            self._status_cb(int(args.status), int(args.error))

    def start(self, frame: bytes):
        """开始广播。frame 是含公司 ID 的 26 字节;WinRT 只吃后 24 字节。"""
        from winrt.windows.storage.streams import DataWriter
        from winrt.windows.devices.bluetooth.advertisement import (
            BluetoothLEManufacturerData, BluetoothLEAdvertisementPublisher)
        self.stop()
        w = DataWriter()
        w.write_bytes(payload_for_android(frame))
        mfd = BluetoothLEManufacturerData(COMPANY_ID, w.detach_buffer())
        pub = BluetoothLEAdvertisementPublisher()
        pub.advertisement.manufacturer_data.append(mfd)
        if self._status_cb is not None:
            self._token = pub.add_status_changed(self._on_status)
        pub.start()
        self._pub = pub

    def stop(self):
        if self._pub is None:
            return
        try:
            if self._token is not None:
                self._pub.remove_status_changed(self._token)
        except Exception:
            pass
        try:
            self._pub.stop()
        except Exception:
            pass
        self._pub = None
        self._token = None

    def is_running(self):
        return self._pub is not None


# ---------------------------------------------------------------------------
# GUI(赛博绿暗色主题,与 tools/ble_advertiser_gui.py 保持一致)
# ---------------------------------------------------------------------------
BG, PANEL, BORDER = "#0E1512", "#141C18", "#2A3A33"
GREEN, GREEN_D, RED = "#35E07E", "#25A75C", "#E0553B"
TEXT, MUTED, AMBER = "#EAF6EF", "#7C8B83", "#E0B03B"
FONT, FONT_B = ("Helvetica", 11), ("Helvetica", 11, "bold")
FONT_H, FONT_SM = ("Helvetica", 17, "bold"), ("Helvetica", 9)
FONT_MONO = ("Consolas", 10)

_PUB_STATUS = {
    0: ("○ 已创建", MUTED), 1: ("… 等待中", MUTED), 2: ("● 广播中", GREEN),
    3: ("… 停止中", MUTED), 4: ("○ 已停止", MUTED),
    5: ("✕ 失败(蓝牙不可用/被占用)", RED),
}


def run_gui():
    import tkinter as tk
    from tkinter import messagebox

    def mk_button(parent, text, cmd, primary=False):
        if primary:
            return tk.Button(parent, text=text, command=cmd, bg=GREEN, fg="#08130C",
                             activebackground=GREEN_D, activeforeground="#08130C",
                             disabledforeground="#0b130e", relief="flat", bd=0,
                             font=FONT_B, cursor="hand2", padx=14, pady=9,
                             highlightthickness=0)
        return tk.Button(parent, text=text, command=cmd, bg=PANEL, fg=GREEN,
                         activebackground="#1B2A22", activeforeground=GREEN,
                         disabledforeground=MUTED, relief="flat", bd=0,
                         font=FONT, cursor="hand2", padx=14, pady=9,
                         highlightthickness=0)

    root = tk.Tk()
    root.title("TRAE-CARD token 广播发送器")
    root.configure(bg=BG)
    root.resizable(False, False)

    adv = TokenAdvertiser()
    state = {"job": None}

    wrap = tk.Frame(root, bg=BG, padx=18, pady=16)
    wrap.pack()
    tk.Label(wrap, text="TOKEN 广播发送器", bg=BG, fg=GREEN, font=FONT_H).pack(anchor="w")
    tk.Label(wrap, text="模拟 Android pad 给卡片发积分变动广播", bg=BG, fg=MUTED,
             font=FONT_SM).pack(anchor="w", pady=(0, 12))

    form = tk.Frame(wrap, bg=BG)
    form.pack(fill="x")
    v_sn  = tk.StringVar(value="")
    v_pk  = tk.StringVar(value=os.environ.get("CARDID_PK", ""))
    v_bal = tk.StringVar(value="1234")
    v_seq = tk.StringVar(value="1")
    v_op  = tk.StringVar(value="add")

    def row(label, var, r, show=None, hint=""):
        tk.Label(form, text=label, bg=BG, fg=TEXT, font=FONT).grid(
            row=r, column=0, sticky="w", pady=4)
        e = tk.Entry(form, textvariable=var, bg=PANEL, fg=TEXT, font=FONT_MONO,
                     insertbackground=GREEN, relief="flat", width=34,
                     show=show, highlightthickness=1, highlightbackground=BORDER)
        e.grid(row=r, column=1, sticky="w", padx=(10, 6), pady=4)
        if hint:
            tk.Label(form, text=hint, bg=BG, fg=MUTED, font=FONT_SM).grid(
                row=r, column=2, sticky="w")
        return e

    row("目标 SN", v_sn, 0, hint="12 位十六进制,从产线台账取")
    row("签名密钥", v_pk, 1, show="•", hint="留空=按SN查台账DeviceSecret")
    row("余额 balance", v_bal, 2, hint=f"0..{BALANCE_MAX}")
    row("序号 seq", v_seq, 3, hint="必须递增,从 1 开始")

    tk.Label(form, text="操作 op", bg=BG, fg=TEXT, font=FONT).grid(
        row=4, column=0, sticky="w", pady=4)
    opbar = tk.Frame(form, bg=BG)
    opbar.grid(row=4, column=1, sticky="w", padx=(10, 6))
    for key, label in (("add", "加分成功"), ("sub", "扣分成功"), ("pet", "宠物开关"),
                       ("fail", "失败"), ("sync", "静默同步")):
        tk.Radiobutton(opbar, text=label, variable=v_op, value=key, bg=BG, fg=TEXT,
                       selectcolor=PANEL, activebackground=BG, activeforeground=GREEN,
                       font=FONT, highlightthickness=0, bd=0).pack(side="left", padx=(0, 10))

    preview = tk.Text(wrap, height=13, width=68, bg="#0B120F", fg=TEXT,
                      font=FONT_MONO, relief="flat", highlightthickness=1,
                      highlightbackground=BORDER, wrap="none")
    preview.pack(fill="x", pady=(12, 8))

    status = tk.Label(wrap, text="○ 就绪", bg=BG, fg=MUTED, font=FONT_B)
    status.pack(anchor="w")

    def set_status(text, color):
        status.config(text=text, fg=color)

    def show(text):
        preview.delete("1.0", "end")
        preview.insert("1.0", text)

    def current_frame():
        # 密钥:手填框优先(测试),否则按 SN 从台账取该设备 DeviceSecret
        secret = resolve_key(v_sn.get(), v_pk.get())
        return build_frame(v_sn.get(), OPS[v_op.get()],
                           int(v_seq.get() or 0), int(v_bal.get() or 0), secret)

    def do_preview(*_):
        try:
            f = current_frame()
        except (ValueError, KeyError) as e:
            show(f"⚠ {e}")
            return
        show("完整厂商数据(26 字节,卡片收到的内容)\n  " + hexs(f) +
             "\n\nAndroid addManufacturerData 的 byte[](24 字节)\n  " +
             hexs(payload_for_android(f)) + "\n\n逐字段拆解\n" + describe(f))

    for v in (v_sn, v_pk, v_bal, v_seq, v_op):
        v.trace_add("write", do_preview)

    def load_last_seq():
        sn = v_sn.get().strip()
        if len(sn.replace(":", "")) == 12:
            v_seq.set(str(load_seq(sn) + 1))

    def stop_adv():
        if state["job"]:
            root.after_cancel(state["job"])
            state["job"] = None
        adv.stop()
        set_status("○ 已停止", MUTED)

    def send():
        try:
            f = current_frame()
        except (ValueError, KeyError) as e:
            messagebox.showerror("参数错误", str(e))
            return
        try:
            adv.start(f)
        except ImportError:
            messagebox.showerror(
                "缺少 WinRT",
                "需要 Windows 原生 Python 并安装依赖:\n"
                "  py -m pip install -r tools/requirements-win.txt\n\n"
                "WSL2 访问不到蓝牙电台,只能用 --frame 模式生成 hex 后手工发。")
            return
        except OSError as e:
            messagebox.showerror("广播失败", f"{e}\n\n检查蓝牙是否开启、适配器是否被占用。")
            return
        sn = v_sn.get().strip()
        save_seq(sn, int(v_seq.get()))
        set_status(f"● 广播中… {ADV_SECONDS:.0f} 秒后自动停止", GREEN)
        # 协议建议 v1 每条消息持续广播 3 秒;即便当前扫描占空比约 70%,单发仍不可靠。
        # 卡片靠 seq 去重,重复 30 次也只处理一次。
        state["job"] = root.after(int(ADV_SECONDS * 1000), on_done)

    def on_done():
        state["job"] = None
        adv.stop()
        set_status("✓ 已发送完毕,seq 已自动 +1", GREEN)
        v_seq.set(str(int(v_seq.get()) + 1))

    btns = tk.Frame(wrap, bg=BG)
    btns.pack(fill="x", pady=(10, 0))
    mk_button(btns, f"发送(广播 {ADV_SECONDS:.0f} 秒)", send, primary=True).pack(side="left")
    mk_button(btns, "停止", stop_adv).pack(side="left", padx=(8, 0))
    mk_button(btns, "读取上次 seq", load_last_seq).pack(side="left", padx=(8, 0))

    # 专用宠物开关:一键开/关(op=pet,余额自动置为类型 / 0),省得手改 op+余额。
    petbar = tk.Frame(wrap, bg=BG)
    petbar.pack(fill="x", pady=(8, 0))
    tk.Label(petbar, text="宠物类型", bg=BG, fg=TEXT, font=FONT).pack(side="left")
    v_pet = tk.StringVar(value="1")
    tk.Entry(petbar, textvariable=v_pet, bg=PANEL, fg=TEXT, font=FONT_MONO, width=4,
             relief="flat", highlightthickness=1, highlightbackground=BORDER).pack(side="left", padx=(6, 10))

    def pet_on():
        v_op.set("pet"); v_bal.set(v_pet.get().strip() or "1"); send()

    def pet_off():
        v_op.set("pet"); v_bal.set("0"); send()
    mk_button(petbar, "开启宠物", pet_on).pack(side="left")
    mk_button(petbar, "关闭宠物", pet_off).pack(side="left", padx=(8, 0))

    def on_close():
        stop_adv()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    do_preview()
    root.mainloop()


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="TRAE-CARD token 广播发送器")
    ap.add_argument("--selftest", action="store_true",
                    help="校验帧组装逻辑(不需要蓝牙,任何平台可跑)")
    ap.add_argument("--frame", action="store_true",
                    help="只计算并打印帧,不发送(可贴进 nRF Connect 手工发)")
    ap.add_argument("--sn", default="", help="目标卡 SN,12 位十六进制")
    ap.add_argument("--op", default="add", choices=list(OPS), help="操作类型")
    ap.add_argument("--seq", type=int, default=0, help="序号,必须 >=1 且递增")
    ap.add_argument("--balance", type=int, default=0, help=f"余额 0..{BALANCE_MAX}")
    ap.add_argument("--pk", default="", help="手填签名密钥(测试覆盖;留空=按SN查台账DeviceSecret)")
    ap.add_argument("--pk-file", default="", help="从文件读签名密钥")
    ap.add_argument("--ledger", default="", help=f"台账路径(默认 {DEFAULT_LEDGER})")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if args.frame:
        try:
            # 密钥:手填/文件/环境变量优先,否则按 SN 从台账取该设备 DeviceSecret
            secret = resolve_key(args.sn, args.pk, args.pk_file, args.ledger)
        except (ValueError, OSError) as e:
            print(f"错误: {e}", file=sys.stderr)
            return 2
        seq = args.seq if args.seq else load_seq(args.sn) + 1
        try:
            f = build_frame(args.sn, OPS[args.op], seq, args.balance, secret)
        except ValueError as e:
            print(f"错误: {e}", file=sys.stderr)
            return 2
        print(f"SN={args.sn}  op={args.op}  seq={seq}  balance={args.balance}\n")
        print("完整厂商数据(26 字节,卡片收到的内容):")
        print(" ", hexs(f))
        print("\nAndroid addManufacturerData 的 byte[](24 字节):")
        print(" ", hexs(payload_for_android(f)))
        print("\n逐字段拆解:")
        print(describe(f))
        print("\n提示: nRF Connect → Advertiser → 新建 → Manufacturer Data,")
        print(f"      Company ID 填 FFFF,数据填上面那 24 字节。")
        save_seq(args.sn, seq)
        return 0

    run_gui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
