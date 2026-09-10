#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# tools/factory_gui.py —— 产线功能测试【GUI 治具】
# 面向不懂技术的工人:8 工位超大色块,插入 USB 自动开测,整块绿=良品 / 整块红=不良,
# 巨大 ✓ / ✗ 一眼可辨。多设备并行(每台一后台线程),热插拔即测即走。
#
# 判定逻辑复用 factory_test.py(单一真源)。依赖: pyserial + tkinter(自带)。
# 运行:  python3 tools/factory_gui.py
import os
import sys
import platform
import threading
import queue
import time
import tkinter as tk
from tkinter import ttk

if getattr(sys, "frozen", False):
    os.chdir(os.path.dirname(sys.executable))   # exe:台账 factory_test_log.csv 落在 exe 旁边
else:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("缺少依赖 pyserial,请先: pip install pyserial", file=sys.stderr)
    sys.exit(2)

from factory_test import (Dev, run_auto, run_disp, run_btn, overall_of,
                          append_log, listen_once, LOG)

STATIONS = 8
COLS, ROWS = 4, 2
ESP_VID = 0x303A          # Espressif USB-Serial-JTAG 的 VID,用于过滤非本机串口
POLL_MS = 400             # 热插拔轮询周期(插上到开测的延迟)

# 颜色(整块背景)
C = {
    "idle":     ("#23272e", "#5a6472", "空闲", "插入设备自动测试"),
    "connect":  ("#1565C0", "#ffffff", "●", "检测到设备…"),
    "testing":  ("#F9A825", "#1a1a1a", "…", "测试中"),
    "disp":     ("#6A1B9A", "#ffffff", "?", "看屏幕颜色对不对?"),
    "btn":      ("#00838F", "#ffffff", "⌨", "请按 上 / 下 / 确定"),
    "pass":     ("#2E7D32", "#ffffff", "✓", "良品 PASS"),
    "fail":     ("#C62828", "#ffffff", "✗", "不良 FAIL"),
    "error":    ("#EF6C00", "#ffffff", "!", "设备异常·请重插"),
}

if platform.system() == "Windows":
    FAM = "Microsoft YaHei UI"
elif platform.system() == "Darwin":
    FAM = "PingFang SC"
else:
    FAM = "WenQuanYi Micro Hei"


class Station:
    """一个工位色块(2×4 网格中的一格)。所有 UI 方法只在主线程调用。"""
    def __init__(self, idx, parent, ctrl):
        self.idx = idx
        self.ctrl = ctrl
        self.port = None
        self.gen = 0                     # 代号:换设备/释放时自增,丢弃过期 worker 事件
        self.sn = "-"
        self.ev = threading.Event()      # 人工确认同步(worker 等,UI 按钮置位)
        self.manual = None

        self.f = tk.Frame(parent, bd=2, relief="ridge")
        self.big = tk.Label(self.f, font=(FAM, 76, "bold"))
        self.big.pack(pady=(18, 0))
        self.msg = tk.Label(self.f, font=(FAM, 22, "bold"))
        self.msg.pack()
        self.sub = tk.Label(self.f, font=(FAM, 14))
        self.sub.pack(pady=(2, 0))
        self.detail = tk.Label(self.f, font=(FAM, 12), justify="center")
        self.detail.pack(pady=(6, 0))

        # 人工确认大按钮(默认隐藏)
        self.btns = tk.Frame(self.f)
        self.b_ok = tk.Button(self.btns, text="通过 ✓", font=(FAM, 20, "bold"),
                              bg="#2E7D32", fg="white", width=8, height=1,
                              command=lambda: self._manual(True))
        self.b_ng = tk.Button(self.btns, text="不通过 ✗", font=(FAM, 20, "bold"),
                              bg="#C62828", fg="white", width=8, height=1,
                              command=lambda: self._manual(False))
        self.b_ok.pack(side="left", padx=8)
        self.b_ng.pack(side="left", padx=8)

        # 重测按钮(默认隐藏)
        self.b_retry = tk.Button(self.f, text="重测", font=(FAM, 13),
                                 command=self._retry)

        self.render("idle")

    def grid(self, r, c):
        self.f.grid(row=r, column=c, sticky="nsew", padx=4, pady=4)

    # —— UI 渲染(主线程)——
    def render(self, state, sn=None, detail="", show_retry=False):
        bg, fg, glyph, label = C[state]
        for w in (self.f, self.big, self.msg, self.sub, self.detail):
            w.configure(bg=bg)
        self.big.configure(text=glyph, fg=fg)
        self.msg.configure(text=label, fg=fg)
        self.sub.configure(text=(f"工位 {self.idx+1}" if not self.port
                                 else f"工位 {self.idx+1} · {self.port}"), fg=fg)
        self.detail.configure(text=detail, fg=fg)
        self.btns.pack_forget()
        if state == "disp":
            self.btns.configure(bg=bg)
            self.btns.pack(pady=10)
        self.b_retry.pack_forget()
        if show_retry and self.port:
            self.b_retry.configure(bg=bg, fg=fg)
            self.b_retry.pack(pady=(8, 0))

    def on_event(self, gen, kind, d):
        if gen != self.gen:
            return                        # 过期设备的事件,丢弃
        if kind == "connect":
            self.render("connect")
        elif kind == "sn":
            self.sn = d.get("sn", "-")
        elif kind == "item":
            self.render("testing", detail=self._progress(d))
        elif kind == "await_disp":
            self.render("disp")
        elif kind == "btn_wait":
            self.render("btn", detail="")
        elif kind == "btn_key":
            self.render("btn", detail=f"已检测: {d.get('keys','')}")
        elif kind == "result":
            ok = d["overall"] == "PASS"
            self.render("pass" if ok else "fail",
                        detail=(f"SN {self.sn}" if ok
                                else f"SN {self.sn}\n失败: {d.get('fails','')}"),
                        show_retry=True)
            self.ctrl.tally(ok)
        elif kind == "error":
            self.render("error", detail=d.get("msg", ""), show_retry=True)

    def _progress(self, d):
        done = d.get("done", [])
        return "  ".join(f"{it}{'✓' if v in ('PASS','OK') else '✗'}" for it, v in done)

    # —— 人工确认 / 重测(主线程按钮回调)——
    def _manual(self, ok):
        self.manual = ok
        self.ev.set()

    def _retry(self):
        if self.port and not self.ctrl.is_busy(self.idx):
            self.ctrl.start_worker(self)

    # —— 生命周期 ——
    def bind_port(self, port):
        self.port = port
        self.gen += 1

    def free(self):
        self.port = None
        self.gen += 1
        self.render("idle")


class Controller:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.stations = []
        self.workers = {}                 # idx -> Thread
        self.loglock = threading.Lock()
        self.n_pass = 0
        self.n_fail = 0

        root.title("trae_card 产线功能测试台 · 8 工位")
        root.configure(bg="#15181d")
        root.geometry("1280x820")

        top = tk.Frame(root, bg="#15181d")
        top.pack(fill="x", padx=10, pady=8)
        tk.Label(top, text="产线功能测试台", font=(FAM, 24, "bold"),
                 bg="#15181d", fg="#e6edf3").pack(side="left")

        self.include_manual = tk.BooleanVar(value=False)
        self.passive = tk.BooleanVar(value=True)
        self.only_esp = tk.BooleanVar(value=True)
        tk.Checkbutton(top, text="被动监听(设备自助自检)", variable=self.passive,
                       font=(FAM, 13), bg="#15181d", fg="#e6edf3",
                       selectcolor="#15181d", activebackground="#15181d",
                       activeforeground="#e6edf3").pack(side="left", padx=16)
        tk.Checkbutton(top, text="含人工项(屏幕/按键)", variable=self.include_manual,
                       font=(FAM, 13), bg="#15181d", fg="#e6edf3",
                       selectcolor="#15181d", activebackground="#15181d",
                       activeforeground="#e6edf3").pack(side="left", padx=16)
        tk.Checkbutton(top, text="仅 Espressif 设备", variable=self.only_esp,
                       font=(FAM, 13), bg="#15181d", fg="#e6edf3",
                       selectcolor="#15181d", activebackground="#15181d",
                       activeforeground="#e6edf3").pack(side="left")

        tk.Label(top, text="HW", font=(FAM, 13), bg="#15181d",
                 fg="#e6edf3").pack(side="left", padx=(20, 2))
        self.hw = tk.Entry(top, font=(FAM, 13), width=8)
        self.hw.insert(0, "A1.0")
        self.hw.pack(side="left")
        tk.Label(top, text="工单", font=(FAM, 13), bg="#15181d",
                 fg="#e6edf3").pack(side="left", padx=(12, 2))
        self.order = tk.Entry(top, font=(FAM, 13), width=8)
        self.order.pack(side="left")

        self.stat = tk.Label(top, text="", font=(FAM, 16, "bold"), bg="#15181d")
        self.stat.pack(side="right")
        self._update_tally()

        grid = tk.Frame(root, bg="#15181d")
        grid.pack(fill="both", expand=True, padx=6, pady=6)
        for r in range(ROWS):
            grid.rowconfigure(r, weight=1)
        for c in range(COLS):
            grid.columnconfigure(c, weight=1)
        for i in range(STATIONS):
            st = Station(i, grid, self)
            st.grid(i // COLS, i % COLS)
            self.stations.append(st)

        self.root.after(POLL_MS, self._scan_ports)
        self.root.after(80, self._drain)

    # —— 计数 ——
    def tally(self, ok):
        if ok:
            self.n_pass += 1
        else:
            self.n_fail += 1
        self._update_tally()

    def _update_tally(self):
        tot = self.n_pass + self.n_fail
        rate = (self.n_pass / tot * 100) if tot else 0
        self.stat.configure(
            text=f"总 {tot}   ✓ {self.n_pass}   ✗ {self.n_fail}   良率 {rate:.1f}%",
            fg=("#3fb950" if self.n_fail == 0 else "#f0a020"))

    def is_busy(self, idx):
        t = self.workers.get(idx)
        return t is not None and t.is_alive()

    # —— 热插拔扫描 ——
    def _current_ports(self):
        ports = []
        for p in list_ports.comports():
            if self.only_esp.get() and (p.vid != ESP_VID):
                continue
            ports.append(p.device)
        return set(ports)

    def _scan_ports(self):
        try:
            cur = self._current_ports()
            bound = {st.port for st in self.stations if st.port}
            # 新插入:分配空闲工位并开测
            for port in sorted(cur - bound):
                st = next((s for s in self.stations if s.port is None), None)
                if st is None:
                    break                 # 8 工位已满
                st.bind_port(port)
                self.start_worker(st)
            # 拔出:释放工位(若 worker 仍在跑,gen 已变,其事件会被丢弃)
            for st in self.stations:
                if st.port and st.port not in cur and not self.is_busy(st.idx):
                    st.free()
        finally:
            self.root.after(POLL_MS, self._scan_ports)

    # —— 事件泵(worker → UI)——
    def post(self, idx, gen, kind, **d):
        self.q.put((idx, gen, kind, d))

    def _drain(self):
        try:
            while True:
                idx, gen, kind, d = self.q.get_nowait()
                self.stations[idx].on_event(gen, kind, d)
        except queue.Empty:
            pass
        self.root.after(80, self._drain)

    # —— 启动一台设备的测试线程 ——
    def start_worker(self, st):
        if self.is_busy(st.idx):
            return
        gen = st.gen
        hw = self.hw.get().strip()
        order = self.order.get().strip()
        manual = self.include_manual.get()
        passive = self.passive.get()
        t = threading.Thread(target=self._worker,
                             args=(st, gen, st.port, hw, order, manual, passive), daemon=True)
        self.workers[st.idx] = t
        t.start()

    def _worker(self, st, gen, port, hw, order, manual, passive):
        dev = None
        try:
            # 打开串口(设备刚枚举可能还没就绪,重试几次)
            for _ in range(6):
                try:
                    dev = Dev(port)
                    break
                except Exception:
                    time.sleep(0.4)
            if dev is None:
                self.post(st.idx, gen, "error", msg="打不开串口")
                return
            self.post(st.idx, gen, "connect")

            results, done = {}, []

            def on_item(item, verdict, kv):
                results[item] = verdict
                done.append((item, verdict))
                self.post(st.idx, gen, "item", done=list(done))

            # —— 被动监听:设备自助自检(工人上电按住确定键),PC 只读不发 ——
            if passive:
                def on_sn(sn):
                    st.sn = sn
                    self.post(st.idx, gen, "sn", sn=sn)
                sn, results = listen_once(dev, on_item=on_item, on_sn=on_sn)
                if sn is None:
                    self.post(st.idx, gen, "error", msg="未监听到自检(未按确定键?)")
                    return
                overall = overall_of(results)
                with self.loglock:
                    append_log(LOG, sn, hw, order, results)
                fails = ",".join(k for k, v in results.items() if v not in ("PASS", "OK"))
                self.post(st.idx, gen, "result", overall=overall, sn=sn, fails=fails)
                return

            # —— 主动模式:PC 驱动 AT+TEST ——
            sn = dev.wait_ready()
            if not sn:
                self.post(st.idx, gen, "error", msg="设备无响应")
                return
            st.sn = sn
            self.post(st.idx, gen, "sn", sn=sn)
            run_auto(dev, on_item)

            # 人工项:仅在自动项全过时才做(坏板不必再人工)
            if manual and overall_of(results) == "PASS":
                run_disp(dev)
                st.ev.clear(); st.manual = None
                self.post(st.idx, gen, "await_disp")
                st.ev.wait(90)
                results["DISP"] = "PASS" if st.manual else "FAIL"
                done.append(("DISP", results["DISP"]))
                if results["DISP"] == "PASS":
                    self.post(st.idx, gen, "btn_wait")
                    keys = []
                    results["BTN"] = run_btn(
                        dev, on_key=lambda k: (keys.append(k),
                        self.post(st.idx, gen, "btn_key", keys=" ".join(keys))))
                    done.append(("BTN", results["BTN"]))

            overall = overall_of(results)
            with self.loglock:
                append_log(LOG, sn, hw, order, results)
            fails = ",".join(k for k, v in results.items() if v not in ("PASS", "OK"))
            self.post(st.idx, gen, "result", overall=overall, sn=sn, fails=fails)
        except Exception as e:
            self.post(st.idx, gen, "error", msg=str(e)[:40])
        finally:
            if dev:
                dev.close()


def main():
    root = tk.Tk()
    Controller(root)
    root.mainloop()


if __name__ == "__main__":
    main()
