"""USB diagnostics for the independent recorder. Never writes audio or prints secrets."""
import argparse
import json
import os
from pathlib import Path
import time
import serial
import sys
sys.stdout.reconfigure(encoding="utf-8")
from device_trial import load_config


def connect(port="COM3"):
    stream=serial.Serial()
    stream.port, stream.baudrate, stream.timeout=port,115200,0.2
    stream.dtr=stream.rts=False
    stream.open()
    stream.reset_input_buffer()
    stream.write(b"\n")
    return stream


def request(stream, command, timeout=45):
    seq=int(time.monotonic()*1000)%100000000
    payload={"cmd":"companion","request":{**command,"seq":seq}}
    stream.write(json.dumps(payload,ensure_ascii=False,separators=(",",":")).encode()+b"\n")
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        raw=stream.readline()
        try: value=json.loads(raw)
        except (ValueError,UnicodeDecodeError): continue
        if value.get("seq")==seq:
            return value
    raise RuntimeError("Device response timed out")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=["status","configure","wifi","cycle","start","ping","trial","monitor","parser_check"])
    parser.add_argument("--port",default="COM3")
    parser.add_argument("--seconds",type=int,default=60)
    args=parser.parse_args()
    command={"cmd":args.action}
    if args.action in ("configure","wifi"):
        load_config(Path(__file__).with_name(".env"))
        if args.action=="configure":
            if not 1<=args.seconds<=86400: parser.error("seconds must be 1..86400")
            command["settings"]={"ak_id":os.environ["ALIBABA_CLOUD_ACCESS_KEY_ID"],
                "ak_secret":os.environ["ALIBABA_CLOUD_ACCESS_KEY_SECRET"],"app_key":os.environ["TINGWU_APP_KEY"],
                "export_mode":"phone","max_seconds":args.seconds}
        else: command.update(ssid=os.environ["MEETING_WIFI_SSID"],password=os.environ["MEETING_WIFI_PASSWORD"])
    if args.action=="cycle": command["cmd"]="ble_cycle"
    with connect(args.port) as stream:
        if args.action=="monitor":
            end=time.monotonic()+args.seconds
            while time.monotonic()<end:
                raw=stream.readline().decode("utf-8",errors="replace").strip()
                if any(word in raw for word in ("Guru Meditation","abort()","assert failed","Backtrace:","ELF file SHA256","Stack smashing","rst:","Saved PC:","Rebooting","MEPC", "MCAUSE", "MTVAL")):
                    print(raw[:1000],flush=True)
                try: value=json.loads(raw)
                except ValueError: continue
                if value.get("event") in {"meeting_ready","init_failed","device_session_finished"}:
                    print(json.dumps(value),flush=True)
            return
        if args.action=="trial":
            print(json.dumps(request(stream,{"cmd":"start"})),flush=True)
            end=time.monotonic()+args.seconds+40
            while time.monotonic()<end:
                raw=stream.readline().decode("utf-8",errors="replace")
                if any(word in raw for word in ("Guru Meditation","abort()","assert failed","Backtrace:","ELF file SHA256","Stack smashing","Saved PC:","Rebooting","MEPC","MCAUSE","MTVAL")):
                    print(raw.strip()[:1000],flush=True)
                try: value=json.loads(raw)
                except (ValueError,UnicodeDecodeError): continue
                if value.get("event") in {"wifi_connected","wss_connected","stt_started","recording","stream_finished","device_session_finished","capture_error","provider_error","provider_json_error"}:
                    print(json.dumps(value,ensure_ascii=False),flush=True)
                if value.get("event")=="device_session_finished": return
            raise RuntimeError("Recording monitor timed out; query device status before retrying")
        if args.action in ("ping","parser_check"):
            stream.write(json.dumps({"cmd":"ping" if args.action=="ping" else "result_check"}).encode()+b"\n")
            until=time.monotonic()+5
            while time.monotonic()<until:
                try: value=json.loads(stream.readline())
                except (ValueError,UnicodeDecodeError): continue
                if value.get("event")==("meeting_ready" if args.action=="ping" else "result_check"): print(json.dumps(value)); return
            raise RuntimeError("Device ping timed out")
        print(json.dumps(request(stream,command),ensure_ascii=False))


if __name__=="__main__": main()
