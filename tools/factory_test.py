#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# tools/factory_test.py —— 产线功能测试:通过 USB 串口驱动固件的 AT+TEST 指令。
# 本文件既是命令行工具,也是共享库:GUI 治具(factory_gui.py)复用这里的
#   Dev / parse_line / run_auto / run_disp / run_btn —— 保证判定逻辑单一真源。
#
# 与身份烧录(make_cardid.py)解耦:
#   产线顺序 = 烧 app 固件 → make_cardid.py 写 Key → 复位 → 功能测试。
#
# 依赖: pyserial   (pip install pyserial)
# 用法:
#   python3 tools/factory_test.py --port /dev/ttyACM0 --hw A1.0 --order T001
#   python3 tools/factory_test.py --port COM3 --no-interactive   # 仅自动项
import argparse
import csv
import os
import sys
import time
from datetime import datetime

try:
    import serial  # pyserial
except ImportError:
    print("缺少依赖 pyserial,请先: pip install pyserial", file=sys.stderr)
    sys.exit(2)

LOG = "factory_test_log.csv"
AUTO_ITEMS = ["INFO", "I2C", "AUDIO", "BATT", "BLE", "ID"]   # AT+TEST? 自动项
ALL_ITEMS  = AUTO_ITEMS + ["DISP", "BTN"]                    # 含人工项(台账列)

# ANSI 颜色(Windows 10+ 终端支持)
G, R, Y, B0, RST = "\033[92m", "\033[91m", "\033[93m", "\033[1m", "\033[0m"


def parse_line(line: str):
    """解析一行 '+TEST:ITEM,k=v,...,PASS' → (item, kv, verdict)。非 +TEST 行返回 None。"""
    if not line.startswith("+TEST:"):
        return None
    parts = line[len("+TEST:"):].split(",")
    item = parts[0]
    kv, verdict = {}, None
    for p in parts[1:]:
        if p in ("PASS", "FAIL", "OK"):
            verdict = p
        elif "=" in p:
            k, v = p.split("=", 1)
            kv[k] = v
    return item, kv, verdict


class Dev:
    """一台设备的串口封装。USB-Serial-JTAG 为原生 USB-CDC,波特率无实义。"""
    def __init__(self, port, baud=115200, timeout=0.2):
        self.port = port
        self.s = serial.Serial()
        self.s.port = port
        self.s.baudrate = baud
        self.s.timeout = timeout
        self.s.dtr = False       # 不拉 DTR/RTS,避免个别配置把芯片带进下载模式
        self.s.rts = False
        self.s.open()
        time.sleep(0.15)
        self.s.reset_input_buffer()

    def send(self, cmd: str):
        self.s.write((cmd + "\r\n").encode())
        self.s.flush()

    def collect(self, stop_item=None, overall_timeout=8.0, on_row=None, stop_pred=None):
        """读 +TEST 行直到超时,或满足停止条件。on_row(item,kv,verdict) 实时回调。
        停止条件二选一:stop_pred(item,kv,verdict) 为真;或 item==stop_item。
        注意:同一 item 可能多行(如 BTN 的 capturing/key/PASS),这类要用 stop_pred
        按 verdict 判停,不能用 stop_item(否则会在首行就停)。"""
        rows, t0 = [], time.time()
        while time.time() - t0 < overall_timeout:
            raw = self.s.readline().decode(errors="replace").strip()
            if not raw:
                continue
            r = parse_line(raw)
            if r is None:
                continue        # 忽略固件其它日志 / selftest printf 噪声
            rows.append(r)
            if on_row:
                on_row(*r)
            if stop_pred is not None:
                if stop_pred(*r):
                    break
            elif stop_item is not None and r[0] == stop_item:
                break
        return rows

    def wait_ready(self, tries=10):
        """等设备就绪:反复发 AT+TEST=INFO 直到收到 INFO 行(设备刚枚举可能还没起来)。"""
        for _ in range(tries):
            self.send("AT+TEST=INFO")
            for item, kv, verdict in self.collect("INFO", overall_timeout=1.0):
                if item == "INFO":
                    return kv.get("sn", "-")
            time.sleep(0.3)
        return None

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


# ————— 可复用测试运行器(CLI 与 GUI 共用)—————

def run_auto(dev: Dev, on_item=None):
    """跑 AT+TEST? 自动项。返回 {item: (verdict, kv)}。on_item(item,verdict,kv) 实时回调。"""
    out = {}

    def _row(item, kv, verdict):
        if item == "RESULT":
            return
        if verdict:
            out[item] = (verdict, kv)
            if on_item:
                on_item(item, verdict, kv)

    dev.send("AT+TEST?")
    dev.collect("RESULT", overall_timeout=14.0, on_row=_row)   # 音频回环阻塞 ~1.3s
    return out


def run_disp(dev: Dev):
    """触发屏幕色块自检(设备端循环显示约 5s)。返回 True 表示已触发。"""
    dev.send("AT+TEST=DISP")
    dev.collect("DISP", overall_timeout=3.0)
    return True


def run_btn(dev: Dev, on_key=None, timeout=12.0):
    """逐键捕获。on_key(name) 每识别到一键回调。返回 'PASS'/'FAIL'。"""
    verdict = "FAIL"
    dev.send("AT+TEST=BTN")

    def _row(item, kv, v):
        nonlocal verdict
        if item != "BTN":
            return
        if "key" in kv and on_key:
            on_key(kv["key"])
        if v:
            verdict = v

    # BTN 有多行(capturing/key.../PASS|FAIL),按“出现 verdict”判停,而非首个 BTN 行。
    dev.collect(overall_timeout=timeout, on_row=_row,
                stop_pred=lambda i, kv, v: i == "BTN" and v in ("PASS", "FAIL"))
    return verdict


def listen_once(dev: Dev, on_item=None, on_sn=None, first_timeout=15.0, timeout=180.0):
    """被动监听设备自助自检:不发指令,只读设备 printf 的 +TEST 行。
    先等 first_timeout 出现首行 +TEST(否则判定非自检设备,返回 (None,{}));
    之后一直读到 +TEST:RESULT。返回 (sn, {item:verdict})。"""
    import time as _t
    results, sn, seen = {}, None, False

    def _row(item, kv, v):
        nonlocal sn, seen
        seen = True
        if "sn" in kv and not sn:        # SELFTEST,begin 或 INFO 行都带 sn
            sn = kv["sn"]
            if on_sn:
                on_sn(sn)
        if item in ("SELFTEST", "RESULT"):
            return
        if v:
            results[item] = v
            if on_item:
                on_item(item, v, kv)

    # 阶段一:等首行
    t0 = _t.time()
    while not seen and _t.time() - t0 < first_timeout:
        dev.collect(overall_timeout=0.5, on_row=_row,
                    stop_pred=lambda i, kv, v: True)   # 收到任意一行就返回本轮
    if not seen:
        return None, {}
    # 阶段二:读到 RESULT
    dev.collect(overall_timeout=timeout, on_row=_row,
                stop_pred=lambda i, kv, v: i == "RESULT")
    return sn, results


def overall_of(results: dict) -> str:
    """results: {item: verdict}。任一非 PASS/OK → FAIL。"""
    return "PASS" if all(v in ("PASS", "OK") for v in results.values()) else "FAIL"


def append_log(path, sn, hw, order, results: dict):
    """追加一行测试台账(仅结果,无 Key;SN=MAC 非机密)。"""
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["timestamp", "sn", "hw", "order", "overall"] + ALL_ITEMS)
        w.writerow([datetime.now().isoformat(timespec="seconds"), sn, hw, order,
                    overall_of(results)] + [results.get(c, "-") for c in ALL_ITEMS])


# ————— 命令行入口 —————

def _show(item, verdict, extra=""):
    tag = f"{G}PASS{RST}" if verdict in ("PASS", "OK") else f"{R}FAIL{RST}"
    print(f"  [{tag}] {item:<8} {extra}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True, help="串口,如 /dev/ttyACM0 或 COM3")
    ap.add_argument("--hw", default="", help="硬件版本,记入台账")
    ap.add_argument("--order", default="", help="工单号,记入台账")
    ap.add_argument("--no-interactive", action="store_true", help="仅跑自动项")
    ap.add_argument("--listen", action="store_true",
                    help="被动监听模式:不发指令,只记录设备自助自检(上电按住确定键)的结果")
    ap.add_argument("--log", default=LOG, help=f"测试台账路径(默认 {LOG})")
    args = ap.parse_args()

    try:
        dev = Dev(args.port)
    except Exception as e:
        print(f"{R}打开串口失败: {e}{RST}", file=sys.stderr)
        return 2

    if args.listen:
        print(f"{B0}=== 被动监听(设备自助自检){args.port} ==={RST}")
        sn, results = listen_once(
            dev, on_item=lambda i, v, kv: _show(i, v, ",".join(f"{k}={vv}" for k, vv in kv.items())))
        dev.close()
        if sn is None:
            print(f"{Y}未监听到自检输出(设备未进自检?上电时按住确定键){RST}")
            return 2
        overall = overall_of(results)
        print(f"\n{(G if overall=='PASS' else R)}{B0}  整机 {overall}  SN={sn}{RST}")
        append_log(args.log, sn, args.hw, args.order, results)
        print(f"  已写台账 {args.log}")
        return 0 if overall == "PASS" else 1

    print(f"{B0}=== 功能测试 {args.port} ==={RST}")
    results, sn = {}, "-"
    for item, (verdict, kv) in run_auto(dev).items():
        if item == "INFO":
            sn = kv.get("sn", "-")
        results[item] = verdict
        _show(item, verdict, ",".join(f"{k}={v}" for k, v in kv.items()))

    if not args.no_interactive:
        print(f"{Y}— 屏幕自检:一屏显示 红/绿/蓝/白/黑 色带 + 网格,看一眼即可 —{RST}")
        run_disp(dev)
        results["DISP"] = "PASS" if input("  屏幕正常? [y/N] ").strip().lower() == "y" else "FAIL"
        _show("DISP", results["DISP"])
        print(f"{Y}— 按键自检:依次按 上 / 下 / 确定(10s 内)—{RST}")
        results["BTN"] = run_btn(dev, on_key=lambda k: print(f"    检测到按键: {k}"))
        _show("BTN", results["BTN"])

    dev.close()

    overall = overall_of(results)
    fails = [k for k, v in results.items() if v not in ("PASS", "OK")]
    print()
    if overall == "PASS":
        print(f"{G}{B0}  ██  整机 PASS  SN={sn}  ██{RST}")
    else:
        print(f"{R}{B0}  ██  整机 FAIL  SN={sn}  失败项: {','.join(fails)}  ██{RST}")
    append_log(args.log, sn, args.hw, args.order, results)
    print(f"  已写台账 {args.log}")
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
