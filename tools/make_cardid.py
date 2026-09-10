#!/usr/bin/env python3
# tools/make_cardid.py —— 产线:读 MAC → 生成 cardid 分区 bin → 烧录 → 追加台账
#
# 用法:
#   CARDID_PK=<全厂共享密钥> python3 tools/make_cardid.py --port /dev/ttyACM0 --hw A1.0
#   (或 --pk-file <路径>;不推荐用 --pk 命令行参数,会留在 ps/shell 历史/MES 作业日志里)
#
# 依赖: ESP-IDF 环境(提供 esptool 与 nvs_partition_gen.py)
import argparse
import csv
import os
import re
import secrets
import subprocess
import sys
import tempfile
from datetime import datetime

# 必须与 partitions.csv 生成出的实际偏移一致。改动分区表后请重新核对:
#   python3 $IDF_PATH/components/partition_table/gen_esp32part.py build/partition_table/partition-table.bin
CARDID_OFFSET = "0x356000"
CARDID_SIZE   = "0x4000"
LEDGER = "cardid_ledger.csv"


def read_mac(port: str) -> str:
    """用 esptool 读 base MAC(= ESP_MAC_WIFI_STA),返回去冒号小写的 12 字符 SN。"""
    out = subprocess.run(
        [sys.executable, "-m", "esptool", "-p", port, "read_mac"],
        capture_output=True, text=True, check=True).stdout
    m = re.search(r"MAC:\s*((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})", out)
    if not m:
        raise RuntimeError("未能从 esptool 输出里解析出 MAC:\n" + out)
    return m.group(1).replace(":", "").lower()


def provision(port, pk, hw, order="", dry_run=False, keep_bin="", log=print):
    """读 MAC → 生成 folotoy-key NVS bin → 烧 cardid → 写台账。返回 {'sn','key'}。
    调用方须已校验 pk/hw 长度、提供 ProductKey。可被 GUI/CLI 复用。异常向上抛。"""
    idf = os.environ.get("IDF_PATH")
    if not idf:
        raise RuntimeError("未设置 IDF_PATH,请先 source export.sh")
    gen = os.path.join(idf, "components", "nvs_flash",
                       "nvs_partition_generator", "nvs_partition_gen.py")
    sn = read_mac(port)
    # 必须用 secrets:random 是梅森旋转,少量输出即可反推、预测整批 Key。
    key = secrets.token_hex(16)          # 16 字节熵 → 32 个十六进制字符
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = os.path.join(tmp, "cardid.csv")
        bin_path = os.path.join(tmp, "cardid.bin")
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            # 与固件一致的 FoloToy 结构(namespace=folotoy-key)
            w.writerow(["key", "type", "encoding", "value"])
            w.writerow(["folotoy-key", "namespace", "", ""])
            w.writerow(["DeviceKey",       "data", "string", sn])
            w.writerow(["DeviceSecret",    "data", "string", key])
            w.writerow(["ProductKey",      "data", "string", pk])
            w.writerow(["HardwareVersion", "data", "string", hw])
        subprocess.run([sys.executable, gen, "generate",
                        csv_path, bin_path, CARDID_SIZE], check=True)
        if keep_bin:
            import shutil
            shutil.copyfile(bin_path, keep_bin)
            log(f"bin 已另存: {keep_bin}")
        if dry_run:
            log(f"[dry-run] SN={sn} KEY={key}")
            return {"sn": sn, "key": key, "dry_run": True}
        subprocess.run([sys.executable, "-m", "esptool", "-p", port,
                        "write_flash", CARDID_OFFSET, bin_path], check=True)
    # 台账含明文 Key,按密钥材料管理 → 0600、gitignore、勿外发。pk 不入台账。
    new = not os.path.exists(LEDGER)
    fd = os.open(LEDGER, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["sn", "key", "hw", "order", "timestamp"])
        w.writerow([sn, key, hw, order, datetime.now().isoformat(timespec="seconds")])
    log(f"OK  SN={sn}  已写入台账 {LEDGER}")
    return {"sn": sn, "key": key}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True, help="串口,如 /dev/ttyACM0 或 COM3")
    ap.add_argument("--pk-file", default="", metavar="PATH",
                    help="从文件读取全厂共享 ProductKey(推荐)。文件内容为纯密钥,首尾空白会被去掉")
    ap.add_argument("--pk", default="",
                    help="直接给出 ProductKey。**不推荐** —— 命令行会进 ps/shell 历史/MES 作业日志。"
                         "优先用 CARDID_PK 环境变量或 --pk-file")
    ap.add_argument("--hw", required=True, help="硬件版本,如 A1.0(≤8 字符)")
    ap.add_argument("--order", default="", help="工单号,写进台账")
    ap.add_argument("--dry-run", action="store_true", help="只生成 bin,不烧录不写台账")
    ap.add_argument("--keep-bin", default="", metavar="PATH",
                    help="把生成的 bin 另存到该路径(默认用临时目录,跑完即删)。"
                         "验证'烧错设备检测'时需要保留 bin,用这个参数")
    args = ap.parse_args()

    # ProductKey 的来源优先级:--pk-file > CARDID_PK 环境变量 > --pk(不推荐)
    # 不用命令行是因为它会进 ps aux、shell 历史、以及记录命令行的 MES 作业日志。
    # 这个密钥一旦泄漏,攻击者可伪造发给全厂每一张卡的广播,且无法检测。
    if args.pk_file:
        with open(args.pk_file, encoding="utf-8") as f:
            pk = f.read().strip()
    elif os.environ.get("CARDID_PK"):
        pk = os.environ["CARDID_PK"].strip()
    elif args.pk:
        print("警告: 通过命令行传 --pk 会把密钥留在 ps/shell 历史/作业日志里,"
              "建议改用 CARDID_PK 环境变量或 --pk-file", file=sys.stderr)
        pk = args.pk
    else:
        print("错误: 未提供 ProductKey。请设 CARDID_PK 环境变量,或用 --pk-file <路径>",
              file=sys.stderr)
        return 2

    if len(pk) > 16:
        print("错误: pk 超过 16 字符", file=sys.stderr); return 2
    if len(args.hw) > 8:
        print("错误: hw 超过 8 字符", file=sys.stderr); return 2

    try:
        provision(args.port, pk, args.hw, args.order, args.dry_run, args.keep_bin)
    except subprocess.CalledProcessError as e:
        print(f"错误: 子进程失败 {e}", file=sys.stderr); return 1
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr); return 1
    print("请复位设备,串口应出现: 身份自检通过 SN=<本机>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
