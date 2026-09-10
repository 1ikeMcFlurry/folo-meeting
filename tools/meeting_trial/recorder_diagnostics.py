"""Read only recorder diagnostics; suppress task IDs, text, credentials and signed URLs."""
import argparse
import json
import time
import serial

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--seconds", type=int, default=10)
args = parser.parse_args()
allowed = {"event", "heap", "min_heap", "ui_state", "elapsed_ms", "largest_block", "wifi_saved", "wifi_connected",
           "ble_enabled", "ble_connected", "phase", "error_code", "complete", "audio_output_supported", "audio_output_enabled",
           "audio_bytes", "captured_bytes", "end_reason", "code", "ok", "error", "export_stage", "reset_reason", "uptime_ms"}
port = serial.Serial()
port.port, port.baudrate, port.timeout = "COM3", 115200, .2
port.dtr = port.rts = False
port.open()
with port:
    port.write(b'\n?\n{"cmd":"companion","request":{"cmd":"status"}}\n')
    end = time.monotonic() + min(args.seconds, 55)
    while time.monotonic() < end:
        line = port.readline().decode("utf-8", errors="replace").strip()
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                safe = {k: v for k, v in value.items() if k in allowed}
                if safe:
                    print(json.dumps(safe, ensure_ascii=False), flush=True)
        except ValueError:
            if any(mark in line for mark in ["Guru Meditation", "assert failed", "Backtrace:", "abort() was called", "Rebooting", "ELF file SHA256", "rst:", "Stack memory:", "Stack smashing", "MEPC", "MCAUSE", "MTVAL", "Saved PC:"]):
                print(line, flush=True)
