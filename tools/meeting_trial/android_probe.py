"""Inspect the meeting App only; screenshots are allowed only on the device tab."""
import argparse
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET
import sys
sys.stdout.reconfigure(encoding="utf-8")

ADB="D:/Software/Android/Sdk/platform-tools/adb.exe"

def adb(*args):
    return subprocess.check_output([ADB,*args],stderr=subprocess.DEVNULL)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=["inspect","tap","screen"])
    parser.add_argument("value",nargs="?")
    args=parser.parse_args()
    path="/data/local/tmp/folo-ui.xml"
    try:
        adb("shell","uiautomator","dump",path)
        raw=adb("shell","cat",path)
    finally: adb("shell","rm","-f",path)
    root=ET.fromstring(raw)
    app_nodes=[n for n in root.iter("node") if n.get("package")=="com.folotoy.meeting"]
    if not app_nodes:
        print("Visible package names only:",sorted({n.get("package","") for n in root.iter("node")}))
        raise RuntimeError("Meeting App is not in foreground; no other app content inspected")
    sensitive=any(n.get("password")=="true" or
        (n.get("text")=="服务设置" and n.get("clickable")!="true") for n in app_nodes)
    if sensitive and args.action=="screen": raise RuntimeError("Refusing to capture credential fields")
    if args.action=="inspect":
        for node in app_nodes:
            if sensitive and node.get("class")=="android.widget.EditText":
                print("[input hidden]",node.get("content-desc", ""),node.get("bounds")); continue
            if node.get("text") or node.get("content-desc"):
                print(node.get("text") or node.get("content-desc"),node.get("bounds"))
    elif args.action=="tap":
        matches=[n for n in app_nodes if n.get("text")==args.value or n.get("content-desc")==args.value]
        node=next((n for n in matches if n.get("clickable")=="true"),matches[0])
        if sensitive and node.get("class")!="android.widget.Button" and args.value not in {"设备","记录","服务设置"}:
            raise RuntimeError("Only action buttons may be tapped while credentials are visible")
        import re
        x1,y1,x2,y2=map(int,re.findall(r"\d+",node.get("bounds")))
        adb("shell","input","tap",str((x1+x2)//2),str((y1+y2)//2))
    else:
        if not any(n.get("text")=="连接你的AI通行证" or n.get("text")=="设备已连接" for n in app_nodes):
            raise RuntimeError("Only the non-sensitive device tab may be captured")
        output=Path(args.value); output.parent.mkdir(parents=True,exist_ok=True)
        output.write_bytes(adb("exec-out","screencap","-p")); print(output.resolve())

if __name__=="__main__": main()
