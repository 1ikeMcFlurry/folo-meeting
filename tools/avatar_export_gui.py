#!/usr/bin/env python3
# TRAE-CARD 头像导出 GUI:扫描并手动选择蓝牙设备、连接读取头像、可调放大倍数、保存透明 PNG。
# 用法:pip install -r tools/requirements.txt && python3 tools/avatar_export_gui.py
import asyncio
import threading
import tkinter as tk
from tkinter import filedialog
from PIL import Image, ImageTk, ImageDraw
from bleak import BleakScanner, BleakClient

EXPORT_UUID = "54524145-4341-5244-0000-000000000009"
PREF_NAME = "TRAE-CARD"      # 扫描后优先排序/自动选中的设备名

# ---- 赛博绿暗色主题 ----
BG      = "#0E1512"
PANEL   = "#141C18"
PANEL2  = "#0B120F"
BORDER  = "#2A3A33"
GREEN   = "#35E07E"
GREEN_D = "#25A75C"
TEXT    = "#EAF6EF"
MUTED   = "#7C8B83"
SEL     = "#123A22"
FONT     = ("Helvetica", 11)
FONT_B   = ("Helvetica", 11, "bold")
FONT_H   = ("Helvetica", 17, "bold")
FONT_SM  = ("Helvetica", 9)
FONT_MONO = ("DejaVu Sans Mono", 10)


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
                     highlightthickness=1, highlightbackground=BORDER,
                     highlightcolor=BORDER)


def card(parent, **kw):
    return tk.Frame(parent, bg=PANEL, highlightthickness=1,
                    highlightbackground=BORDER, **kw)


def checkerboard(w, h, cell=10):
    """透明背景可视化:淡色棋盘格。"""
    tile = Image.new("RGB", (cell * 2, cell * 2), (28, 38, 33))
    d = ImageDraw.Draw(tile)
    d.rectangle([cell, 0, cell * 2 - 1, cell - 1], fill=(19, 27, 23))
    d.rectangle([0, cell, cell - 1, cell * 2 - 1], fill=(19, 27, 23))
    bg = Image.new("RGB", (w, h))
    for y in range(0, h, cell * 2):
        for x in range(0, w, cell * 2):
            bg.paste(tile, (x, y))
    return bg


def decode(blob: bytes) -> Image.Image:
    """把导出 blob 还原成 16×26 RGBA 图(role 0 = 透明背景)。"""
    if len(blob) < 4:
        raise ValueError("数据太短")
    _ver, cols, rows, rc = blob[0], blob[1], blob[2], blob[3]
    off = 4
    pal = []
    for _ in range(rc):
        r, g, b = blob[off + 1], blob[off + 2], blob[off + 3]   # 大端 0x00RRGGBB
        pal.append((r, g, b))
        off += 4
    if len(blob) < off + rows * cols:
        raise ValueError("数据不完整")
    img = Image.new("RGBA", (cols, rows), (0, 0, 0, 0))
    px = img.load()
    for y in range(rows):
        for x in range(cols):
            role = blob[off]
            off += 1
            if role != 0 and role < len(pal):
                r, g, b = pal[role]
                px[x, y] = (r, g, b, 255)
    return img


async def scan_devices(timeout: float = 6.0):
    return await BleakScanner.discover(timeout=timeout)


async def read_avatar(device):
    async with BleakClient(device) as client:
        return bytes(await client.read_gatt_char(EXPORT_UUID))


class BleWorker:
    """后台线程跑 asyncio 事件循环,供 GUI 提交 BLE 协程。"""
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro, on_done):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        fut.add_done_callback(on_done)


class App:
    def __init__(self, root):
        self.root = root
        self.worker = BleWorker()
        self.img = None
        self.devices = []
        self.tkimg = None

        if isinstance(root, (tk.Tk, tk.Toplevel)):   # 嵌入 Frame(工具箱)时跳过窗口属性
            root.title("TRAE-CARD · 头像导出")
            root.geometry("1040x740")
            root.minsize(880, 600)
        root.configure(bg=BG)

        # ---- 顶部标题栏 ----
        head = tk.Frame(root, bg=BG)
        head.pack(fill="x", padx=18, pady=(16, 6))
        tk.Label(head, text="◆ TRAE-CARD", bg=BG, fg=GREEN,
                 font=FONT_H).pack(side="left")
        tk.Label(head, text="AVATAR EXPORT", bg=BG, fg=MUTED,
                 font=("Helvetica", 10)).pack(side="left", padx=(10, 0), pady=(6, 0))

        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True, padx=18, pady=(6, 6))

        # ---- 左侧:设备 + 控制 ----
        left = card(body)
        left.pack(side="left", fill="y", padx=(0, 12))
        pad = tk.Frame(left, bg=PANEL)
        pad.pack(fill="both", expand=True, padx=14, pady=14)

        mk_button(pad, "⟳  扫描设备", self.on_scan, primary=True).pack(fill="x")
        tk.Label(pad, text="选择要连接的设备", bg=PANEL, fg=MUTED,
                 font=FONT_SM).pack(anchor="w", pady=(14, 4))

        lb_wrap = tk.Frame(pad, bg=BORDER)          # 细边框
        lb_wrap.pack(fill="y", expand=True)
        self.listbox = tk.Listbox(lb_wrap, width=38, height=15, bg=PANEL2, fg=TEXT,
                                  selectbackground=SEL, selectforeground=GREEN,
                                  highlightthickness=0, bd=0, activestyle="none",
                                  font=FONT_MONO)
        sb = tk.Scrollbar(lb_wrap, command=self.listbox.yview, width=10)
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        sb.pack(side="right", fill="y")
        self.listbox.bind("<Double-Button-1>", lambda e: self.on_read())

        self.connect_btn = mk_button(pad, "⇩  连接并读取", self.on_read, primary=True)
        self.connect_btn.pack(fill="x", pady=(12, 4))
        self.connect_btn.config(state="disabled")

        sf = tk.Frame(pad, bg=PANEL)
        sf.pack(fill="x", pady=(10, 2))
        self.scale_lbl = tk.Label(sf, text="放大  ×16", bg=PANEL, fg=TEXT, font=FONT)
        self.scale_lbl.pack(anchor="w")
        self.scale = tk.IntVar(value=16)
        tk.Scale(sf, from_=1, to=48, orient="horizontal", variable=self.scale,
                 command=self._on_scale, showvalue=False, bg=PANEL, fg=GREEN,
                 troughcolor=PANEL2, activebackground=GREEN, highlightthickness=0,
                 bd=0, sliderrelief="flat", length=220).pack(fill="x")

        self.save_btn = mk_button(pad, "⤓  保存为 PNG", self.on_save)
        self.save_btn.pack(fill="x", pady=(12, 0))
        self.save_btn.config(state="disabled")

        # ---- 右侧:预览 ----
        right = card(body)
        right.pack(side="right", fill="both", expand=True)
        self.view = tk.Label(right, bg=PANEL, text="连接设备后在此预览头像",
                             fg="#3f4c45", font=FONT)
        self.view.place(relx=0.5, rely=0.5, anchor="center")

        # ---- 底部状态栏 ----
        self.status = tk.Label(root, text="请先点『扫描设备』", bg=PANEL2, fg=MUTED,
                               anchor="w", font=FONT_SM, padx=14, pady=6)
        self.status.pack(fill="x", side="bottom")

    # ---- helpers ----
    def set_status(self, txt, ok=False):
        self.root.after(0, lambda: self.status.config(
            text=txt, fg=(GREEN if ok else MUTED)))

    def _cur_scale(self):
        try:
            return max(1, int(self.scale.get()))
        except Exception:
            return 1

    def _on_scale(self, _v):
        self.scale_lbl.config(text=f"放大  ×{self._cur_scale()}")
        self.rerender()

    def rerender(self):
        if self.img is None:
            return
        s = self._cur_scale()
        big = self.img.resize((self.img.width * s, self.img.height * s), Image.NEAREST)
        bg = checkerboard(big.width, big.height)
        bg.paste(big, (0, 0), big)          # 用 alpha 合成到棋盘格
        self.tkimg = ImageTk.PhotoImage(bg)
        self.view.config(image=self.tkimg, text="")

    # ---- scan ----
    def on_scan(self):
        self.set_status("扫描中…(约 6 秒)")
        self.connect_btn.config(state="disabled")
        self.worker.submit(scan_devices(), self._scan_done)

    def _scan_done(self, fut):
        try:
            devs = fut.result()
        except Exception as e:
            self.set_status(f"扫描失败: {e}")
            return

        def apply():
            devs_sorted = sorted(
                devs, key=lambda d: (d.name != PREF_NAME, (d.name or "").lower()))
            self.devices = devs_sorted
            self.listbox.delete(0, "end")
            for d in devs_sorted:
                mark = "★ " if d.name == PREF_NAME else "  "
                self.listbox.insert("end", f"{mark}{d.name or '(未知)':<16}{d.address}")
            for i, d in enumerate(devs_sorted):
                if d.name == PREF_NAME:
                    self.listbox.selection_clear(0, "end")
                    self.listbox.selection_set(i)
                    self.listbox.see(i)
                    break
            self.connect_btn.config(state="normal" if devs_sorted else "disabled")
            self.set_status(f"发现 {len(devs_sorted)} 个设备,选一个后点『连接并读取』",
                            ok=bool(devs_sorted))
        self.root.after(0, apply)

    # ---- read ----
    def on_read(self):
        sel = self.listbox.curselection()
        if not sel:
            self.set_status("请先在列表里选择一个设备")
            return
        device = self.devices[sel[0]]
        self.set_status(f"连接 {device.name or device.address} 读取中…")
        self.worker.submit(read_avatar(device), self._read_done)

    def _read_done(self, fut):
        try:
            img = decode(fut.result())
        except Exception as e:
            self.set_status(f"读取失败: {e}")
            return

        def apply():
            self.img = img
            self.rerender()
            self.save_btn.config(state="normal")
            self.set_status(f"已读取 {img.width}×{img.height},可调放大或保存 PNG", ok=True)
        self.root.after(0, apply)

    # ---- save ----
    def on_save(self):
        if self.img is None:
            return
        path = filedialog.asksaveasfilename(defaultextension=".png",
                                            filetypes=[("PNG", "*.png")])
        if not path:
            return
        s = self._cur_scale()
        out = self.img.resize((self.img.width * s, self.img.height * s), Image.NEAREST)
        out.save(path)
        self.set_status(f"已保存 {path}", ok=True)


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
