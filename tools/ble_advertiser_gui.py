#!/usr/bin/env python3
# TRAE-CARD BLE 广播发送器:在 Windows 上发送带自定义厂商数据的 BLE 广播,
# 用来测试固件的"爱心跳动屏"(固件匹配 Manufacturer Data 前缀 FF FF 48 42)。
#
# 必须用 Windows 原生 Python 运行(不能在 WSL2 里跑,WSL2 访问不到蓝牙电台):
#   py -m pip install -r tools/requirements-win.txt
#   py tools/ble_advertiser_gui.py
#
# 需 Windows 10+ 且有 BLE 蓝牙适配器。
import sys
import tkinter as tk
from tkinter import messagebox

# ---- 赛博绿暗色主题(与 avatar_export_gui.py 保持一致)----
BG      = "#0E1512"
PANEL   = "#141C18"
PANEL2  = "#0B120F"
BORDER  = "#2A3A33"
GREEN   = "#35E07E"
GREEN_D = "#25A75C"
RED     = "#E0553B"
TEXT    = "#EAF6EF"
MUTED   = "#7C8B83"
FONT     = ("Helvetica", 11)
FONT_B   = ("Helvetica", 11, "bold")
FONT_H   = ("Helvetica", 17, "bold")
FONT_SM  = ("Helvetica", 9)
FONT_MONO = ("DejaVu Sans Mono", 12)

# 固件匹配的默认值:Company ID 0xFFFF(BLE SIG 测试保留值) + 魔数 "HB" = 48 42
DEFAULT_COMPANY = "FFFF"
DEFAULT_PAYLOAD = "4842"

# BluetoothLEAdvertisementPublisherStatus 枚举 → 友好文案
_PUB_STATUS = {
    0: ("○ 已创建", MUTED),
    1: ("… 等待中", MUTED),
    2: ("● 广播中", GREEN),
    3: ("… 停止中", MUTED),
    4: ("○ 已停止", MUTED),
    5: ("✕ 失败(蓝牙不可用/被占用)", RED),
}


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


# ---------------------------------------------------------------------------
# 后端:无 GUI 依赖,纯 hex/字节逻辑可脱离 WinRT 单测;WinRT 调用惰性导入。
# ---------------------------------------------------------------------------
class AdvertiserBackend:
    def __init__(self):
        self._publisher = None
        self._token = None
        self._status_cb = None

    # ---- 纯逻辑(可单测,无需 WinRT)----
    @staticmethod
    def parse_inputs(company_hex, payload_hex):
        """解析输入 → (company_id:int, payload:bytes)。非法时抛 ValueError。"""
        c = company_hex.strip().replace("0x", "").replace("0X", "")
        if not c:
            raise ValueError("Company ID 不能为空")
        try:
            cid = int(c, 16)
        except ValueError:
            raise ValueError("Company ID 必须是十六进制")
        if not (0 <= cid <= 0xFFFF):
            raise ValueError("Company ID 需在 0000..FFFF")
        p = payload_hex.strip().replace(" ", "")
        try:
            payload = bytes.fromhex(p) if p else b""
        except ValueError:
            raise ValueError("Data 负载必须是偶数位十六进制")
        return cid, payload

    @staticmethod
    def on_air_bytes(company_id, payload):
        """空口实际字节:Company ID 小端(2 字节) + 负载。固件即按此匹配。"""
        return company_id.to_bytes(2, "little") + payload

    @staticmethod
    def on_air_preview(company_id, payload):
        return " ".join(f"{b:02X}" for b in AdvertiserBackend.on_air_bytes(company_id, payload))

    # ---- WinRT 交互(惰性导入)----
    def _build_mfd(self, company_id, payload):
        from winrt.windows.storage.streams import DataWriter
        from winrt.windows.devices.bluetooth.advertisement import BluetoothLEManufacturerData
        w = DataWriter()
        w.write_bytes(payload)          # ← 原来是 list(payload)
        buf = w.detach_buffer()
        return BluetoothLEManufacturerData(company_id, buf)

    def start(self, company_id, payload):
        """开始持续广播。会先停掉已有广播。可能抛 ImportError / OSError。"""
        from winrt.windows.devices.bluetooth.advertisement import BluetoothLEAdvertisementPublisher
        self.stop()
        pub = BluetoothLEAdvertisementPublisher()
        pub.advertisement.manufacturer_data.append(self._build_mfd(company_id, payload))
        if self._status_cb is not None:
            self._token = pub.add_status_changed(self._on_status)
        pub.start()
        self._publisher = pub

    def stop(self):
        if self._publisher is None:
            return
        try:
            if self._token is not None:
                self._publisher.remove_status_changed(self._token)
        except Exception:
            pass
        try:
            self._publisher.stop()
        except Exception:
            pass
        self._publisher = None
        self._token = None

    def is_running(self):
        return self._publisher is not None

    def on_status(self, cb):
        """cb(status:int, error:int) —— 注意在 WinRT 线程上触发。"""
        self._status_cb = cb

    def _on_status(self, sender, args):
        if self._status_cb is not None:
            self._status_cb(int(args.status), int(args.error))


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
class AdvertiserGUI:
    def __init__(self, root):
        self.root = root
        self.backend = AdvertiserBackend()
        self.backend.on_status(self._status_from_winrt)
        self._pulsing = False

        if isinstance(root, (tk.Tk, tk.Toplevel)):   # 嵌入 Frame(工具箱)时跳过窗口属性
            root.title("TRAE-CARD · BLE 广播发送器")
            root.resizable(False, False)
        root.configure(bg=BG)

        wrap = tk.Frame(root, bg=BG)
        wrap.pack(padx=20, pady=18)

        tk.Label(wrap, text="BLE 广播发送器", bg=BG, fg=GREEN, font=FONT_H).pack(anchor="w")
        tk.Label(wrap, text="发送厂商自定义广播以驱动固件爱心跳动屏", bg=BG, fg=MUTED,
                 font=FONT_SM).pack(anchor="w", pady=(0, 12))

        box = card(wrap)
        box.pack(fill="x")
        inner = tk.Frame(box, bg=PANEL)
        inner.pack(padx=16, pady=14, fill="x")

        self.company_var = tk.StringVar(value=DEFAULT_COMPANY)
        self.payload_var = tk.StringVar(value=DEFAULT_PAYLOAD)
        self.pulse_var = tk.StringVar(value="300")

        self._field(inner, "Company ID (hex)", self.company_var, 0)
        self._field(inner, "Data 负载 (hex)", self.payload_var, 1)

        tk.Label(inner, text="空口预览", bg=PANEL, fg=MUTED, font=FONT_SM)\
            .grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.preview_lbl = tk.Label(inner, text="", bg=PANEL2, fg=GREEN, font=FONT_MONO,
                                    anchor="w", padx=10, pady=6)
        self.preview_lbl.grid(row=2, column=1, sticky="we", pady=(8, 0))

        self._field(inner, "脉冲时长 (ms)", self.pulse_var, 3, top_pad=8)
        inner.columnconfigure(1, weight=1)

        btns = tk.Frame(wrap, bg=BG)
        btns.pack(fill="x", pady=(14, 0))
        self.toggle_btn = mk_button(btns, "开始广播", self._toggle, primary=True)
        self.toggle_btn.pack(side="left")
        self.pulse_btn = mk_button(btns, "脉冲一下", self._pulse)
        self.pulse_btn.pack(side="left", padx=8)
        mk_button(btns, "重置默认", self._reset).pack(side="right")

        self.status_lbl = tk.Label(wrap, text="○ 已停止", bg=BG, fg=MUTED, font=FONT_B)
        self.status_lbl.pack(anchor="w", pady=(14, 0))

        for v in (self.company_var, self.payload_var):
            v.trace_add("write", lambda *_: self._revalidate())
        self._revalidate()

        if sys.platform != "win32":
            self._set_status("✕ 需在 Windows 原生 Python 下运行", RED)

        if isinstance(root, (tk.Tk, tk.Toplevel)):   # 独立窗口才拦关闭;嵌入 Frame 无此方法
            root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _field(self, parent, label, var, row, top_pad=0):
        tk.Label(parent, text=label, bg=PANEL, fg=TEXT, font=FONT)\
            .grid(row=row, column=0, sticky="w", pady=(top_pad, 0))
        e = tk.Entry(parent, textvariable=var, bg=PANEL2, fg=GREEN, font=FONT_MONO,
                     insertbackground=GREEN, relief="flat", width=22,
                     highlightthickness=1, highlightbackground=BORDER, highlightcolor=GREEN)
        e.grid(row=row, column=1, sticky="we", padx=(12, 0), pady=(top_pad, 0), ipady=4)
        return e

    # ---- 校验与预览 ----
    def _revalidate(self):
        try:
            cid, payload = AdvertiserBackend.parse_inputs(self.company_var.get(),
                                                          self.payload_var.get())
            self.preview_lbl.config(text=AdvertiserBackend.on_air_preview(cid, payload), fg=GREEN)
            self._valid = True
        except ValueError as e:
            self.preview_lbl.config(text=str(e), fg=RED)
            self._valid = False
        self._sync_buttons()
        return getattr(self, "_valid", False)

    def _sync_buttons(self):
        running = self.backend.is_running()
        self.toggle_btn.config(text="停止广播" if running else "开始广播",
                               state="normal" if (self._valid or running) else "disabled")
        # 脉冲仅在未持续广播且输入合法时可用
        self.pulse_btn.config(state="normal" if (self._valid and not running and not self._pulsing)
                              else "disabled")

    # ---- 动作 ----
    def _current_inputs(self):
        return AdvertiserBackend.parse_inputs(self.company_var.get(), self.payload_var.get())

    def _toggle(self):
        if self.backend.is_running():
            self.backend.stop()
            self._set_status("○ 已停止", MUTED)
        else:
            if not self._start():
                return
        self._sync_buttons()

    def _start(self):
        try:
            cid, payload = self._current_inputs()
            self.backend.start(cid, payload)
            self._set_status("● 广播中", GREEN)
            return True
        except ImportError:
            messagebox.showerror("缺少依赖 / 环境不对",
                                 "无法导入 WinRT。请在 Windows 原生 Python 下运行,并安装:\n"
                                 "py -m pip install -r tools/requirements-win.txt")
        except Exception as e:
            messagebox.showerror("广播失败", f"启动广播出错:\n{e}")
        self._set_status("✕ 失败", RED)
        return False

    def _pulse(self):
        if self.backend.is_running() or self._pulsing:
            return
        try:
            ms = int(self.pulse_var.get())
            if ms <= 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning("脉冲时长", "脉冲时长需为正整数毫秒")
            return
        if not self._start():
            return
        self._pulsing = True
        self._sync_buttons()
        self.root.after(ms, self._pulse_end)

    def _pulse_end(self):
        self.backend.stop()
        self._pulsing = False
        self._set_status("○ 已停止(脉冲结束)", MUTED)
        self._sync_buttons()

    def _reset(self):
        self.company_var.set(DEFAULT_COMPANY)
        self.payload_var.set(DEFAULT_PAYLOAD)

    # ---- 状态 ----
    def _set_status(self, text, color):
        self.status_lbl.config(text=text, fg=color)

    def _status_from_winrt(self, status, error):
        # WinRT 线程 → 编回 Tk 主线程
        text, color = _PUB_STATUS.get(status, (f"状态 {status}", MUTED))
        if status == 5 and error:
            text = f"✕ 失败(错误码 {error})"
        self.root.after(0, lambda: self._set_status(text, color))

    def _on_close(self):
        try:
            self.backend.stop()
        finally:
            self.root.destroy()


def main():
    root = tk.Tk()
    AdvertiserGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
