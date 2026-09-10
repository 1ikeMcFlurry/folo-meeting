"""Local, non-recording provisioning checks. Never print passwords or PoP."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import time
import xml.etree.ElementTree as ET

ADB = "D:/Software/Android/Sdk/platform-tools/adb.exe"
PHONE = "3B15AN01XMH00000"


def adb(*args):
    result = subprocess.run([ADB, "-s", PHONE, *args], capture_output=True, check=True)
    return result.stdout


def ui():
    raw = adb("exec-out", "uiautomator", "dump", "/dev/tty").decode("utf-8", errors="replace")
    return ET.fromstring(raw[raw.index("<?xml"):raw.index("</hierarchy>") + 12])


def click(node):
    bounds = list(map(int, re.findall(r"\d+", node.get("bounds", ""))))
    assert len(bounds) == 4
    adb("shell", "input", "tap", str((bounds[0] + bounds[2]) // 2), str((bounds[1] + bounds[3]) // 2))


def test_network():
    values = {}
    for line in Path("tools/meeting_trial/.env").read_text(encoding="utf-8-sig").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() in {"MEETING_WIFI_SSID", "MEETING_WIFI_PASSWORD"}:
            values[key.strip()] = value.strip().strip("\"'")
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["ui", "tap", "select-network", "scroll", "password", "screen", "serial"])
    parser.add_argument("--text")
    parser.add_argument("--wrong", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--seconds", type=int, default=8)
    args = parser.parse_args()
    if args.action == "ui":
        for n in ui().iter("node"):
            if n.get("password") == "true" or n.get("class")=="android.widget.EditText":
                print("[Input field]", n.get("content-desc", ""), n.get("bounds"))
            elif n.get("text") or n.get("content-desc"):
                print(n.get("text") or n.get("content-desc"), n.get("bounds"))
    elif args.action in {"tap", "select-network"}:
        text = args.text if args.action == "tap" else test_network()["MEETING_WIFI_SSID"]
        found = [n for n in ui().iter("node") if n.get("text") == text or n.get("text", "").startswith(text + "\n")]
        assert len(found) == 1, "Expected exactly one matching control"
        click(found[0]); print("Tapped matching control")
    elif args.action == "scroll":
        n=next(n for n in ui().iter("node") if n.get("scrollable")=="true")
        x1,y1,x2,y2=map(int,re.findall(r"\d+",n.get("bounds")))
        adb("shell","input","swipe",str((x1+x2)//2),str(y2-140),str((x1+x2)//2),str(y1+100),"400")
    elif args.action == "password":
        nodes = [n for n in ui().iter("node") if n.get("password") == "true" and n.get("content-desc") == "Wi-Fi 密码"]
        assert len(nodes) == 1, "Expected a hidden Wi-Fi password field"
        password = "wrong-pass-20260910" if args.wrong else test_network()["MEETING_WIFI_PASSWORD"]
        # adb shell receives a shell command; quote explicitly, never interpolate
        # the password into this script's command line or logs.
        assert all(32 <= ord(c) < 127 for c in password), "Enter non-ASCII passwords on the phone"
        import shlex
        click(nodes[0]); adb("shell", "input text " + shlex.quote(password.replace(" ", "%s")))
        print("Entered test password (hidden)")
    elif args.action == "screen":
        assert args.output and args.output.suffix == ".png"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(adb("exec-out", "screencap", "-p")); print("Screenshot saved")
    elif args.action == "serial":
        import serial
        port=serial.Serial()
        port.port,port.baudrate,port.timeout="COM3",115200,0.2
        port.dtr=port.rts=False
        port.open()
        with port:
            if args.text:
                messages={"ping": {"cmd":"ping"}, "ble": {"cmd":"wifi_setup"},
                          "cycle": {"cmd":"companion","request":{"cmd":"ble_cycle"}}}
                assert args.text in messages
                port.write(("\n" + json.dumps(messages[args.text]) + "\n").encode())
            end=time.monotonic()+min(args.seconds,55)
            while time.monotonic()<end:
                line=port.readline().decode("utf-8",errors="replace").strip()
                try:
                    value=json.loads(line)
                    if args.text=="cycle" and isinstance(value,dict) and "ok" in value:
                        print(json.dumps({"event":"ble_cycle_reply","ok":value["ok"]}),flush=True)
                    if isinstance(value,dict) and "event" in value:
                        # An allowlist prevents future firmware secrets entering logs.
                        safe={k:v for k,v in value.items() if k in {"event","wifi_saved","wifi_connected","wifi_setup","companion","ble_enabled","ble_connected","largest_block","security","committed","wifi_restored","ble","ui_state"}}
                        print(json.dumps(safe,ensure_ascii=False),flush=True)
                except ValueError:
                    if any(mark in line for mark in ["Guru Meditation", "assert failed", "Backtrace:", "abort() was called", "Rebooting", "ELF file SHA256"]):
                        print(line,flush=True)


if __name__ == "__main__":
    main()
