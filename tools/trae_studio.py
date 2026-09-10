#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# tools/trae_studio.py —— TRAE-CARD 工具箱(单窗口独立应用)
# 把 tools/ 下各工具整合进一个程序:左侧导航切换功能面板,复用各工具的核心函数
# (import,不开子进程),底部统一控制台。重依赖(librosa/pyserial/bleak)按需惰性导入,
# 缺依赖时只影响对应面板,应用照常启动。
#
# 运行:  python tools/trae_studio.py   (tkinter 为标准库;各面板依赖见提示)
import os
import sys
import threading
import queue
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# 工作目录:源码运行时用仓库根(台账/生成物相对路径);打成 exe 时用 exe 所在目录
# (cardid_ledger.csv 等就落在 exe 旁边)。冻结模式下各库模块已被 PyInstaller 打进包内。
if getattr(sys, "frozen", False):
    os.chdir(os.path.dirname(sys.executable))
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, HERE)
    os.chdir(os.path.dirname(HERE))

# 配色
BG, PANEL, SIDE = "#0E1116", "#151a21", "#0A0D12"
FG, MUTED, ACC = "#e6edf3", "#7d8896", "#39d98a"
BORDER = "#232a33"
FONT = ("Microsoft YaHei UI" if sys.platform == "win32" else "sans-serif", 11)
FONT_H = (FONT[0], 15, "bold")
FONT_MONO = ("Consolas" if sys.platform == "win32" else "monospace", 10)


class Studio:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        # BLE 共享:一个后台事件循环引擎 + 一个线程安全事件队列(worker 线程 → 主线程)
        self.ble = None            # ble_card_client.BleWorker(上位机引擎)
        self.avw = None            # avatar_export_gui.BleWorker(头像扫描/读取)
        self.adv_be = None         # ble_advertiser_gui.AdvertiserBackend(WinRT 广播)
        self.ble_q = queue.Queue()
        self._ble_handler = None   # 当前 BLE 面板的事件处理(切面板时清空)
        self._imgref = None        # 头像预览 PhotoImage 引用(防 GC)
        root.title("TRAE-CARD 工具箱")
        root.configure(bg=BG)
        root.geometry("1080x760")

        # 左侧导航
        side = tk.Frame(root, bg=SIDE, width=210)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Label(side, text="TRAE-CARD", bg=SIDE, fg=ACC,
                 font=(FONT[0], 16, "bold")).pack(anchor="w", padx=16, pady=(16, 2))
        tk.Label(side, text="工具箱", bg=SIDE, fg=MUTED, font=FONT).pack(anchor="w", padx=16)

        # 内容区 + 控制台
        right = tk.Frame(root, bg=BG)
        right.pack(side="left", fill="both", expand=True)
        self.content = tk.Frame(right, bg=BG)
        self.content.pack(fill="both", expand=True, padx=16, pady=12)

        cons = tk.Frame(right, bg=BG)
        cons.pack(fill="x", side="bottom")
        bar = tk.Frame(cons, bg=BG); bar.pack(fill="x")
        tk.Label(bar, text="控制台", bg=BG, fg=MUTED, font=FONT).pack(side="left", padx=(16, 0))
        tk.Button(bar, text="清空", command=lambda: self.console.delete("1.0", "end"),
                  bg=PANEL, fg=FG, relief="flat", font=FONT).pack(side="right", padx=16)
        self.console = tk.Text(cons, height=9, bg="#080B0F", fg="#b9c4cf", font=FONT_MONO,
                               relief="flat", highlightthickness=1, highlightbackground=BORDER)
        self.console.pack(fill="x", padx=16, pady=(2, 12))

        # 导航项:(分组, 名称, 构建函数)
        nav = [
            ("产线生产", "身份烧录 make_cardid", self.panel_identity),
            ("产线生产", "功能产测 factory", self.panel_factory),
            ("产线生产", "Token 广播", self.panel_token),
            ("音频", "音频 → RTTTL", self.panel_mp3),
            ("音频", "音频 → ADPCM 片段", self.panel_adpcm),
            ("资源生成", "像素文字位图", self.panel_pixel),
            ("资源生成", "奖励动画位图", self.panel_reward),
            ("资源生成", "中文点阵字体", self.panel_cnfont),
            ("BLE 调试", "BLE 上位机", self.panel_ble_client),
            ("BLE 调试", "头像导出", self.panel_ble_avatar),
            ("BLE 调试", "BLE 广播发送器", self.panel_ble_adv),
        ]
        cur_group = None
        self.buttons = {}
        for group, name, builder in nav:
            if group != cur_group:
                tk.Label(side, text=group, bg=SIDE, fg=MUTED,
                         font=(FONT[0], 9)).pack(anchor="w", padx=16, pady=(14, 2))
                cur_group = group
            b = tk.Button(side, text=name, anchor="w", bg=SIDE, fg=FG, relief="flat",
                          font=FONT, activebackground=PANEL, activeforeground=ACC,
                          command=lambda bd=builder, nm=name: self.show(bd, nm))
            b.pack(fill="x", padx=8)
            self.buttons[name] = b

        self.show(self.panel_identity, "身份烧录 make_cardid")
        self.root.after(80, self._drain)

    # ---------- 基础设施 ----------
    def log(self, msg):
        self.q.put(str(msg))

    def _drain(self):
        try:
            while True:
                self.console.insert("end", self.q.get_nowait() + "\n")
                self.console.see("end")
        except queue.Empty:
            pass
        # BLE 事件(worker 线程投递):log 恒进控制台;结构化事件交当前面板处理(若在)
        try:
            while True:
                kind, payload = self.ble_q.get_nowait()
                if kind == "log":
                    self.console.insert("end", str(payload) + "\n"); self.console.see("end")
                elif self._ble_handler:
                    try:
                        self._ble_handler(kind, payload)
                    except Exception as e:
                        self.console.insert("end", "UI 更新异常: %s\n" % e)
        except queue.Empty:
            pass
        self.root.after(80, self._drain)

    def run_bg(self, fn):
        def worker():
            try:
                fn()
            except Exception as e:
                self.log("✗ 错误: %s" % e)
                self.log(traceback.format_exc().rstrip())
        threading.Thread(target=worker, daemon=True).start()

    def show(self, builder, name):
        self._ble_handler = None      # 切面板:停派 BLE 结构化事件到旧面板(连接/引擎保留)
        for b in self.buttons.values():
            b.configure(fg=FG, bg=SIDE)
        self.buttons[name].configure(fg=ACC, bg=PANEL)
        for w in self.content.winfo_children():
            w.destroy()
        builder(self.content)

    # ---------- 面板通用小部件 ----------
    def _title(self, parent, text, sub=""):
        tk.Label(parent, text=text, bg=BG, fg=FG, font=FONT_H).pack(anchor="w")
        if sub:
            tk.Label(parent, text=sub, bg=BG, fg=MUTED, font=FONT).pack(anchor="w", pady=(0, 10))

    def _field(self, parent, label, default="", show=None, width=32):
        row = tk.Frame(parent, bg=BG); row.pack(fill="x", pady=3)
        tk.Label(row, text=label, bg=BG, fg=FG, font=FONT, width=14, anchor="w").pack(side="left")
        var = tk.StringVar(value=default)
        tk.Entry(row, textvariable=var, bg=PANEL, fg=FG, font=FONT_MONO, width=width,
                 relief="flat", insertbackground=ACC, show=show,
                 highlightthickness=1, highlightbackground=BORDER).pack(side="left")
        return var

    def _btn(self, parent, text, cmd):
        return tk.Button(parent, text=text, command=cmd, bg=ACC, fg="#06120c",
                         font=(FONT[0], 11, "bold"), relief="flat", padx=16, pady=4)

    def _filepick(self, parent, label, default="", save=False):
        row = tk.Frame(parent, bg=BG); row.pack(fill="x", pady=3)
        tk.Label(row, text=label, bg=BG, fg=FG, font=FONT, width=14, anchor="w").pack(side="left")
        var = tk.StringVar(value=default)
        tk.Entry(row, textvariable=var, bg=PANEL, fg=FG, font=FONT_MONO, width=40,
                 relief="flat", insertbackground=ACC,
                 highlightthickness=1, highlightbackground=BORDER).pack(side="left")

        def pick():
            p = (filedialog.asksaveasfilename() if save else filedialog.askopenfilename())
            if p:
                var.set(p)
        tk.Button(row, text="…", command=pick, bg=PANEL, fg=FG, relief="flat").pack(side="left", padx=4)
        return var

    # ================= 面板:身份烧录 =================
    def panel_identity(self, p):
        self._title(p, "身份烧录 (make_cardid)",
                    "读 MAC → 生成 folotoy-key NVS → 烧 cardid → 写台账。ProductKey 走文件/环境变量,勿明文外泄。")
        port = self._field(p, "串口", "/dev/ttyACM0")
        hw = self._field(p, "硬件版本", "A1.0")
        order = self._field(p, "工单号", "")
        pkfile = self._filepick(p, "ProductKey 文件")
        dry = tk.BooleanVar(value=False)
        tk.Checkbutton(p, text="dry-run(只生成不烧录)", variable=dry, bg=BG, fg=FG,
                       selectcolor=PANEL, font=FONT, activebackground=BG).pack(anchor="w", pady=4)

        def go():
            import make_cardid
            pk = ""
            if pkfile.get().strip():
                pk = open(pkfile.get().strip(), encoding="utf-8").read().strip()
            elif os.environ.get("CARDID_PK"):
                pk = os.environ["CARDID_PK"].strip()
            if not pk:
                self.log("✗ 未提供 ProductKey(选文件或设 CARDID_PK 环境变量)"); return
            if len(pk) > 32 or len(hw.get()) > 16:
                self.log("✗ ProductKey/hw 超长"); return
            self.log("▶ 烧录中 %s ..." % port.get())
            r = make_cardid.provision(port.get().strip(), pk, hw.get().strip(),
                                      order.get().strip(), dry.get(), log=self.log)
            self.log("✓ 完成 SN=%s%s" % (r["sn"], "(dry-run)" if r.get("dry_run") else ""))
        self._btn(p, "开始烧录", lambda: self.run_bg(go)).pack(anchor="w", pady=10)

    # ================= 面板:功能产测 =================
    def panel_factory(self, p):
        self._title(p, "功能产测 (factory)",
                    "对单台设备跑 AT+TEST。被动监听=设备自助自检(上电按住确定键),PC 只记录。")
        port = self._field(p, "串口", "/dev/ttyACM0")
        hw = self._field(p, "硬件版本", "A1.0")
        mode = tk.StringVar(value="listen")
        bar = tk.Frame(p, bg=BG); bar.pack(anchor="w", pady=4)
        for v, t in (("listen", "被动监听(设备自助)"), ("active", "主动 AT+TEST?")):
            tk.Radiobutton(bar, text=t, variable=mode, value=v, bg=BG, fg=FG,
                           selectcolor=PANEL, font=FONT, activebackground=BG).pack(side="left", padx=(0, 12))
        tree = ttk.Treeview(p, columns=("v",), height=9)
        tree.heading("#0", text="项"); tree.heading("v", text="结果")
        tree.column("#0", width=120); tree.column("v", width=360)
        tree.pack(fill="x", pady=8)
        result = tk.Label(p, text="", bg=BG, fg=MUTED, font=(FONT[0], 14, "bold"))
        result.pack(anchor="w")

        def go():
            import factory_test as ft
            for i in tree.get_children():
                tree.delete(i)
            self.log("▶ 打开 %s ..." % port.get())
            dev = ft.Dev(port.get().strip())
            rows = {}

            def on_item(item, verdict, kv):
                self.root.after(0, lambda: tree.insert(
                    "", "end", iid=item, text=item,
                    values=("%s  %s" % (verdict, ",".join("%s=%s" % kv2 for kv2 in kv.items())),)))
                rows[item] = verdict
            if mode.get() == "listen":
                self.log("… 等待设备自助自检输出(上电时按住确定键)")
                sn, res = ft.listen_once(dev, on_item=on_item)
            else:
                sn = dev.wait_ready()
                res = {k: v for k, (v, _) in ft.run_auto(dev, lambda i, vd, kv: on_item(i, vd, kv)).items()}
            dev.close()
            ov = ft.overall_of(res) if res else "FAIL"
            ft.append_log(ft.LOG, sn or "-", hw.get().strip(), "", res)
            self.root.after(0, lambda: result.configure(
                text="整机 %s   SN=%s" % (ov, sn or "-"),
                fg=ACC if ov == "PASS" else "#ff5a5a"))
            self.log("✓ %s SN=%s 已写台账" % (ov, sn or "-"))
        self._btn(p, "开始", lambda: self.run_bg(go)).pack(anchor="w", pady=6)

    # ================= 面板:Token 广播 =================
    def panel_token(self, p):
        self._title(p, "Token 广播",
                    "组装积分变动广播帧(HMAC 用该设备 DeviceSecret,按 SN 查台账)。预览可跨平台;实发需蓝牙电台。")
        import token_broadcast_gui as tb
        sn = self._field(p, "目标 SN", "")
        key = self._field(p, "签名密钥", "", show="•")
        tk.Label(p, text="(留空=按 SN 从台账取 DeviceSecret)", bg=BG, fg=MUTED,
                 font=(FONT[0], 9)).pack(anchor="w", padx=112)
        bal = self._field(p, "余额 balance", "1234")
        seq = self._field(p, "序号 seq", "1")
        op = tk.StringVar(value="add")
        bar = tk.Frame(p, bg=BG); bar.pack(anchor="w", pady=4)
        for v, t in (("add", "加分"), ("sub", "扣分"), ("pet", "宠物"), ("fail", "失败"), ("sync", "静默同步")):
            tk.Radiobutton(bar, text=t, variable=op, value=v, bg=BG, fg=FG,
                           selectcolor=PANEL, font=FONT, activebackground=BG).pack(side="left", padx=(0, 10))
        out = tk.Text(p, height=9, bg="#080B0F", fg=FG, font=FONT_MONO, relief="flat",
                      highlightthickness=1, highlightbackground=BORDER)
        out.pack(fill="x", pady=8)

        def preview():
            out.delete("1.0", "end")
            try:
                secret = tb.resolve_key(sn.get(), key.get())
                f = tb.build_frame(sn.get(), tb.OPS[op.get()], int(seq.get() or 0),
                                   int(bal.get() or 0), secret)
            except Exception as e:
                out.insert("1.0", "⚠ %s" % e); return
            out.insert("1.0", "完整厂商数据(26 字节):\n  " + tb.hexs(f) +
                       "\n\nAndroid byte[](24 字节):\n  " + tb.hexs(tb.payload_for_android(f)) +
                       "\n\n逐字段:\n" + tb.describe(f))
        self._btn(p, "生成/预览帧", preview).pack(anchor="w", pady=6)

    # ================= 面板:音频 → RTTTL =================
    def panel_mp3(self, p):
        self._title(p, "音频 → RTTTL", "把 mp3/wav 近似成单音曲谱。需 librosa(pip install librosa)。")
        src = self._filepick(p, "输入音频")
        name = self._field(p, "曲名", "Song")
        bpm = self._field(p, "BPM(留空自动)", "")
        minms = self._field(p, "最短音符 ms", "70")
        out = tk.Text(p, height=8, bg="#080B0F", fg=FG, font=FONT_MONO, relief="flat",
                      highlightthickness=1, highlightbackground=BORDER)
        out.pack(fill="x", pady=8)

        def go():
            import mp3_to_rtttl as m
            self.log("▶ 转换 %s ..." % src.get())
            r = m.convert(src.get().strip(), name=name.get().strip() or "Song",
                          bpm=float(bpm.get()) if bpm.get().strip() else None,
                          min_note_ms=float(minms.get() or 70), log=self.log)
            self.root.after(0, lambda: (out.delete("1.0", "end"), out.insert("1.0", r)))
            self.log("✓ %d 字节,%d 音符" % (len(r.encode()), r.count(",") + 1))
        self._btn(p, "转换", lambda: self.run_bg(go)).pack(anchor="w", pady=6)

    # ================= 面板:音频 → ADPCM =================
    def panel_adpcm(self, p):
        self._title(p, "音频 → ADPCM 片段", "wav/mp3 → IMA-ADPCM 片段容器(BLE 下发用)。需 wav_to_adpcm 的后端。")
        src = self._filepick(p, "输入音频")
        dst = self._filepick(p, "输出片段", save=True)
        sr = self._field(p, "采样率", "16000")

        def go():
            import wav_to_adpcm as w
            self.log("▶ 构建片段 sr=%s ..." % sr.get())
            data = w.build_clip(src.get().strip(), target_sr=int(sr.get() or 16000))
            path = dst.get().strip() or (os.path.splitext(src.get())[0] + ".clip")
            with open(path, "wb") as f:
                f.write(data)
            self.log("✓ 已写 %s(%d 字节)" % (path, len(data)))
        self._btn(p, "生成片段", lambda: self.run_bg(go)).pack(anchor="w", pady=6)

    # ================= 面板:像素文字位图 =================
    def panel_pixel(self, p):
        self._title(p, "像素文字位图", "5x7 像素字放大成 A8 位图头(LVGL)。/ 分隔多行。")
        name = self._field(p, "C 符号名", "ui_text")
        block = self._field(p, "方块 px", "6")
        text = self._field(p, "文字(/换行)", "HELLO/WORLD")
        out = self._filepick(p, "输出 .h", save=True)

        def go():
            import gen_pixel_text as g
            lines = text.get().split("/")
            W, H, data = g.render(lines, int(block.get() or 6))
            path = out.get().strip() or ("components/ui/presentation/src/%s.h" % name.get())
            open(path, "w").write(g.emit_c(name.get().strip(), W, H, data))
            self.log("✓ 已写 %s : %dx%d(%d 字节)" % (path, W, H, len(data)))
        self._btn(p, "生成", lambda: self.run_bg(go)).pack(anchor="w", pady=6)

    # ================= 面板:奖励动画位图 =================
    def panel_reward(self, p):
        self._title(p, "奖励动画位图", "生成金币/礼盒像素图头 ui_reward_art.h + 预览 PNG。")

        def go():
            import gen_reward_art as g
            parts = ['// 自动生成(gen_reward_art),勿手改。', "#pragma once", '#include "lvgl.h"', ""]
            sheet = []
            for fn in (g.build_coin, g.build_body, g.build_lid):
                code, meta = fn()
                parts.append(code)
                sheet.append((code.split("static const uint8_t ")[1].split("_map")[0], meta))
            open("components/ui/presentation/src/ui_reward_art.h", "w").write("\n".join(parts))
            g.write_png("scratch_reward_preview.png", sheet)
            self.log("✓ 已写 ui_reward_art.h + scratch_reward_preview.png")
        self._btn(p, "生成", lambda: self.run_bg(go)).pack(anchor="w", pady=6)

    # ================= 面板:中文点阵字体 =================
    def panel_cnfont(self, p):
        self._title(p, "中文点阵字体", "生成 LVGL GB2312 一级字点阵字体 C 文件。需 Pillow + 字体文件(慢)。")
        sizes = self._field(p, "字号(逗号)", "16,24")

        def go():
            import gen_cn_font as g
            for s in [int(x) for x in sizes.get().replace("，", ",").split(",") if x.strip()]:
                out = "components/ui/presentation/src/lv_font_cn_%d.c" % s
                self.log("▶ 生成 %dpx → %s(较慢)..." % (s, out))
                g.gen(s, out, g.FONT_PATH)
                self.log("✓ 完成 %dpx" % s)
        self._btn(p, "生成", lambda: self.run_bg(go)).pack(anchor="w", pady=6)

    def _dep_note(self, p, need, err):
        tk.Label(p, text="此面板需 %s;当前不可用:\n%s" % (need, err), bg=BG, fg="#ff8a5a",
                 font=FONT, justify="left").pack(anchor="w", pady=20)

    # ================= 面板:BLE 上位机(原生,复用 BleWorker 引擎)=================
    def panel_ble_client(self, p):
        self._title(p, "BLE 上位机", "连接卡片:写配置 / 发图 / 发乐谱 / 发音频片段 / 读积分。需 bleak + pillow。")
        try:
            import ble_card_client as bc
        except Exception as e:
            self._dep_note(p, "bleak + pillow", e); return
        self._bc = bc
        if self.ble is None:               # 引擎只建一次(切面板/重连都复用)
            self.ble = bc.BleWorker(lambda k, pl: self.ble_q.put((k, pl)))

        # —— 扫描 / 连接 ——
        r1 = tk.Frame(p, bg=BG); r1.pack(fill="x", pady=3)
        nm = tk.StringVar(value="TRAE-CARD")
        tk.Label(r1, text="设备名过滤", bg=BG, fg=FG, font=FONT).pack(side="left")
        tk.Entry(r1, textvariable=nm, bg=PANEL, fg=FG, font=FONT_MONO, width=14, relief="flat",
                 highlightthickness=1, highlightbackground=BORDER).pack(side="left", padx=6)
        tk.Button(r1, text="扫描", command=lambda: self.ble.scan(nm.get()),
                  bg=PANEL, fg=FG, relief="flat").pack(side="left")
        self.bc_dev = ttk.Combobox(r1, width=34, state="readonly")
        self.bc_dev.pack(side="left", padx=6)
        r2 = tk.Frame(p, bg=BG); r2.pack(fill="x", pady=3)

        def do_connect():
            v = self.bc_dev.get()
            if not v:
                self.log("✗ 先扫描并选择设备"); return
            self.ble.connect(v.split("  ")[0])
        tk.Button(r2, text="连接", command=do_connect, bg=PANEL, fg=FG, relief="flat").pack(side="left")
        tk.Button(r2, text="断开", command=lambda: self.ble.disconnect(),
                  bg=PANEL, fg=FG, relief="flat").pack(side="left", padx=6)
        self.bc_status = tk.Label(r2, text="未连接", bg=BG, fg=MUTED, font=FONT)
        self.bc_status.pack(side="left", padx=8)
        self.bc_prog = ttk.Progressbar(p, maximum=100)
        self.bc_prog.pack(fill="x", pady=4)

        def act_row(label, widgets_builder, action):
            row = tk.Frame(p, bg=BG); row.pack(fill="x", pady=3)
            tk.Label(row, text=label, bg=BG, fg=FG, font=FONT, width=12, anchor="w").pack(side="left")
            state = widgets_builder(row)
            tk.Button(row, text="发送", command=lambda: action(state), bg=ACC, fg="#06120c",
                      font=(FONT[0], 10, "bold"), relief="flat", padx=10).pack(side="left", padx=6)

        def entry_in(row, default, w=34):
            v = tk.StringVar(value=default)
            tk.Entry(row, textvariable=v, bg=PANEL, fg=FG, font=FONT_MONO, width=w, relief="flat",
                     highlightthickness=1, highlightbackground=BORDER).pack(side="left")
            return v

        act_row("JSON 配置", lambda row: entry_in(row, '{"name":"Foo"}'),
                lambda v: self.ble.send_json(v.get()))
        act_row("乐谱 RTTTL", lambda row: entry_in(row, bc.RTTTL_SAMPLES["Super Mario"]),
                lambda v: self.ble.send_score(v.get()))

        # 图片(需选文件 + 模式)
        rimg = tk.Frame(p, bg=BG); rimg.pack(fill="x", pady=3)
        tk.Label(rimg, text="图片", bg=BG, fg=FG, font=FONT, width=12, anchor="w").pack(side="left")
        imgv = tk.StringVar()
        tk.Entry(rimg, textvariable=imgv, bg=PANEL, fg=FG, font=FONT_MONO, width=26, relief="flat",
                 highlightthickness=1, highlightbackground=BORDER).pack(side="left")
        tk.Button(rimg, text="…", command=lambda: imgv.set(filedialog.askopenfilename() or imgv.get()),
                  bg=PANEL, fg=FG, relief="flat").pack(side="left", padx=2)
        mode = tk.StringVar(value="avatar")
        for m in ("avatar", "fullscreen"):
            tk.Radiobutton(rimg, text=m, variable=mode, value=m, bg=BG, fg=FG, selectcolor=PANEL,
                           font=(FONT[0], 9), activebackground=BG).pack(side="left")

        def send_img():
            try:
                data, size, fmt = bc.convert_image(imgv.get().strip(), mode.get())
            except Exception as e:
                self.log("✗ 图片处理: %s" % e); return
            self.ble.send_image(data, size, mode.get(), fmt)
        tk.Button(rimg, text="发送", command=send_img, bg=ACC, fg="#06120c",
                  font=(FONT[0], 10, "bold"), relief="flat", padx=10).pack(side="left", padx=6)

        # 音频片段(文件 + clip_id)
        rclip = tk.Frame(p, bg=BG); rclip.pack(fill="x", pady=3)
        tk.Label(rclip, text="音频片段", bg=BG, fg=FG, font=FONT, width=12, anchor="w").pack(side="left")
        clipv = tk.StringVar()
        tk.Entry(rclip, textvariable=clipv, bg=PANEL, fg=FG, font=FONT_MONO, width=26, relief="flat",
                 highlightthickness=1, highlightbackground=BORDER).pack(side="left")
        tk.Button(rclip, text="…", command=lambda: clipv.set(filedialog.askopenfilename() or clipv.get()),
                  bg=PANEL, fg=FG, relief="flat").pack(side="left", padx=2)
        cid = tk.StringVar(value="0")
        tk.Label(rclip, text="clip_id", bg=BG, fg=MUTED, font=(FONT[0], 9)).pack(side="left", padx=(6, 2))
        tk.Entry(rclip, textvariable=cid, bg=PANEL, fg=FG, font=FONT_MONO, width=3, relief="flat",
                 highlightthickness=1, highlightbackground=BORDER).pack(side="left")
        tk.Button(rclip, text="发送", command=lambda: self.ble.send_clip(int(cid.get() or 0), clipv.get().strip()),
                  bg=ACC, fg="#06120c", font=(FONT[0], 10, "bold"), relief="flat", padx=10).pack(side="left", padx=6)

        tk.Button(p, text="读游戏积分", command=lambda: self.ble.read_gamescore(),
                  bg=PANEL, fg=FG, relief="flat").pack(anchor="w", pady=6)
        self._ble_handler = self._bc_handle

    def _bc_handle(self, kind, payload):
        if kind == "devices":
            self.bc_dev["values"] = ["%s  %s" % (a, n) for a, n in payload]
            if payload:
                self.bc_dev.current(0)
        elif kind == "connected":
            _addr, mtu = payload
            self.bc_status.configure(text="已连接 MTU=%d" % mtu, fg=ACC)
        elif kind == "disconnected":
            self.bc_status.configure(text="未连接", fg=MUTED)
        elif kind == "progress":
            off, total = payload
            self.bc_prog["value"] = (off * 100.0 / total) if total else 0

    # ================= 面板:头像导出(原生,复用 scan/read/decode)=================
    def panel_ble_avatar(self, p):
        self._title(p, "头像导出", "扫描设备 → 读取头像特征 → 解码预览 → 导出 PNG。需 bleak + pillow。")
        try:
            import avatar_export_gui as av
        except Exception as e:
            self._dep_note(p, "bleak + pillow", e); return
        self._av = av
        if self.avw is None:
            self.avw = av.BleWorker()
        self.av_devs, self.av_img = [], None
        r1 = tk.Frame(p, bg=BG); r1.pack(fill="x", pady=3)
        tk.Button(r1, text="扫描设备", bg=PANEL, fg=FG, relief="flat",
                  command=lambda: self.avw.submit(av.scan_devices(),
                                                  lambda f: self.ble_q.put(("av_scan", f)))).pack(side="left")
        self.av_combo = ttk.Combobox(r1, width=40, state="readonly")
        self.av_combo.pack(side="left", padx=6)

        def do_read():
            i = self.av_combo.current()
            if i < 0 or i >= len(self.av_devs):
                self.log("✗ 先扫描并选择设备"); return
            self.log("▶ 读取头像 ...")
            self.avw.submit(av.read_avatar(self.av_devs[i]), lambda f: self.ble_q.put(("av_read", f)))
        tk.Button(r1, text="读取头像", bg=PANEL, fg=FG, relief="flat", command=do_read).pack(side="left")
        self.av_canvas = tk.Label(p, bg=PANEL, text="(头像预览)", fg=MUTED, font=FONT, width=24, height=10)
        self.av_canvas.pack(anchor="w", pady=8)

        def do_export():
            if self.av_img is None:
                self.log("✗ 先读取头像"); return
            path = filedialog.asksaveasfilename(defaultextension=".png")
            if path:
                self.av_img.save(path); self.log("✓ 已导出 %s" % path)
        tk.Button(p, text="导出 PNG", command=do_export, bg=ACC, fg="#06120c",
                  font=(FONT[0], 10, "bold"), relief="flat", padx=12).pack(anchor="w")
        self._ble_handler = self._av_handle

    def _av_handle(self, kind, fut):
        if kind == "av_scan":
            try:
                self.av_devs = list(fut.result())
            except Exception as e:
                self.log("✗ 扫描失败: %s" % e); return
            self.av_combo["values"] = ["%s  %s" % (getattr(d, "name", "") or "?", getattr(d, "address", d))
                                       for d in self.av_devs]
            if self.av_devs:
                self.av_combo.current(0)
            self.log("✓ 扫描到 %d 台设备" % len(self.av_devs))
        elif kind == "av_read":
            try:
                from PIL import ImageTk
                blob = fut.result()
                img = self._av.decode(blob)
            except Exception as e:
                self.log("✗ 读取/解码失败: %s" % e); return
            self.av_img = img
            big = img.resize((img.width * 6, img.height * 6))
            self._imgref = ImageTk.PhotoImage(big)
            self.av_canvas.configure(image=self._imgref, text="", width=big.width, height=big.height)
            self.log("✓ 头像 %dx%d 已解码" % (img.width, img.height))

    # ================= 面板:BLE 广播发送器(原生,复用 AdvertiserBackend + token 帧)=================
    def panel_ble_adv(self, p):
        self._title(p, "BLE 广播发送器", "WinRT 持续广播 token 帧(按 SN 从台账取 DeviceSecret 签名)。仅 Windows。")
        try:
            import ble_advertiser_gui as adv
            import token_broadcast_gui as tb
        except Exception as e:
            self._dep_note(p, "token 逻辑(WinRT 仅 Windows)", e); return
        self._adv, self._tb = adv, tb
        if self.adv_be is None:
            self.adv_be = adv.AdvertiserBackend()
            self.adv_be.on_status(lambda s, e: self.ble_q.put(("adv_status", (s, e))))
        sn = self._field(p, "目标 SN", "")
        key = self._field(p, "签名密钥", "", show="•")
        bal = self._field(p, "余额 balance", "1234")
        seq = self._field(p, "序号 seq", "1")
        op = tk.StringVar(value="add")
        bar = tk.Frame(p, bg=BG); bar.pack(anchor="w", pady=4)
        for v, t in (("add", "加分"), ("sub", "扣分"), ("pet", "宠物"), ("fail", "失败"), ("sync", "静默同步")):
            tk.Radiobutton(bar, text=t, variable=op, value=v, bg=BG, fg=FG, selectcolor=PANEL,
                           font=FONT, activebackground=BG).pack(side="left", padx=(0, 10))
        self.adv_status = tk.Label(p, text="○ 就绪", bg=BG, fg=MUTED, font=(FONT[0], 12, "bold"))
        self.adv_status.pack(anchor="w", pady=6)

        def start():
            try:
                secret = tb.resolve_key(sn.get(), key.get())
                frame = tb.build_frame(sn.get(), tb.OPS[op.get()], int(seq.get() or 0),
                                       int(bal.get() or 0), secret)
                self.adv_be.start(tb.COMPANY_ID, tb.payload_for_android(frame))
            except Exception as e:
                self.log("✗ 广播启动失败: %s" % e); return
            self.log("▶ 开始广播 SN=%s op=%s" % (sn.get(), op.get()))
        row = tk.Frame(p, bg=BG); row.pack(anchor="w", pady=6)
        tk.Button(row, text="开始广播", command=start, bg=ACC, fg="#06120c",
                  font=(FONT[0], 11, "bold"), relief="flat", padx=14).pack(side="left")
        tk.Button(row, text="停止", command=lambda: (self.adv_be.stop(), self.log("○ 已停止")),
                  bg=PANEL, fg=FG, relief="flat", padx=12).pack(side="left", padx=8)

        # 专用宠物开关:一键开/关(op=pet,余额=类型 / 0)
        prow = tk.Frame(p, bg=BG); prow.pack(anchor="w", pady=6)
        tk.Label(prow, text="宠物类型", bg=BG, fg=FG, font=FONT).pack(side="left")
        pv = tk.StringVar(value="1")
        tk.Entry(prow, textvariable=pv, bg=PANEL, fg=FG, font=FONT_MONO, width=4, relief="flat",
                 highlightthickness=1, highlightbackground=BORDER).pack(side="left", padx=(6, 10))
        tk.Button(prow, text="开启宠物", bg=ACC, fg="#06120c", font=(FONT[0], 10, "bold"), relief="flat",
                  command=lambda: (op.set("pet"), bal.set(pv.get().strip() or "1"), start())).pack(side="left")
        tk.Button(prow, text="关闭宠物", bg=PANEL, fg=FG, relief="flat",
                  command=lambda: (op.set("pet"), bal.set("0"), start())).pack(side="left", padx=8)
        self._ble_handler = self._adv_handle

    def _adv_handle(self, kind, payload):
        if kind == "adv_status":
            s, e = payload
            self.adv_status.configure(text="● 广播中 status=%d err=%d" % (s, e),
                                      fg=ACC if e == 0 else "#ff5a5a")


def main():
    root = tk.Tk()
    Studio(root)
    root.mainloop()


if __name__ == "__main__":
    main()
