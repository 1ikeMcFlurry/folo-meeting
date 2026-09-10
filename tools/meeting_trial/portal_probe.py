"""Run the device's HTTP self-check over USB. Never switches the computer's network."""
import argparse
import json
import time
import serial
from device_trial import read_events


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM3")
    args = parser.parse_args()
    port = serial.Serial()
    port.port, port.baudrate, port.timeout = args.port, 115200, 0.5
    port.dtr = port.rts = False
    port.open()
    try:
        port.write(b'\n{"cmd":"ping"}\n')
        for event in read_events(port, time.monotonic() + 5):
            if event.get("event") == "meeting_ready":
                if not event.get("wifi_setup"):
                    raise RuntimeError("Device is not in AP setup; check cancelled")
                break
        else:
            raise RuntimeError("Idle device did not answer")
        port.write(b'{"cmd":"portal_check"}\n')
        for event in read_events(port, time.monotonic() + 40):
            if event.get("event") in ("portal_check", "portal_check_done"):
                print(json.dumps(event, ensure_ascii=False), flush=True)
            if event.get("event") == "portal_check_done":
                return 0 if event.get("ok") else 1
        raise RuntimeError("Device HTTP self-check timed out")
    finally:
        port.close()


if __name__ == "__main__":
    raise SystemExit(main())
