"""Capture the actual device LCD while idle. Saves screen PNG only, never audio."""
import argparse
from pathlib import Path
import struct
import time
import zlib

import serial
from device_trial import read_events


def png_chunk(kind, content):
    return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--port", default="COM3")
    args = parser.parse_args()
    port = serial.Serial()
    port.port, port.baudrate, port.timeout = args.port, 115200, 0.5
    port.dtr = port.rts = False
    port.open()
    width, height = 240, 320
    rows = [bytearray(width * 3) for _ in range(height)]
    covered = [bytearray(width) for _ in range(height)]
    try:
        time.sleep(0.2)
        port.reset_input_buffer()
        port.write(b'\n{"cmd":"ping"}\n')
        for event in read_events(port, time.monotonic() + 5):
            if event["event"] == "meeting_ready":
                break
        else:
            raise RuntimeError("Device is not idle; capture cancelled")
        port.write(b'{"cmd":"screenshot"}\n')
        for event in read_events(port, time.monotonic() + 45):
            if event["event"] == "screen_done":
                if not event.get("ok") or not all(all(row) for row in covered):
                    raise RuntimeError("Incomplete screen capture")
                raw = b"".join(b"\0" + row for row in rows)
                png = b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                args.output.write_bytes(png + png_chunk(b"IDAT", zlib.compress(raw)) + png_chunk(b"IEND", b""))
                print(str(args.output.resolve()))
                return
            if event["event"] != "screen_row":
                continue
            x, y, w = (event[k] for k in ("x", "y", "w"))
            if not 0 <= y < height or not 0 <= x < width or not 0 < w <= width - x:
                raise ValueError("Invalid screen region")
            pixels = bytearray()
            for count, color in event["runs"]:
                if not 0 < count <= w or not 0 <= color <= 65535:
                    raise ValueError("Invalid screen pixels")
                r, g, b = (color >> 11) & 31, (color >> 5) & 63, color & 31
                pixels.extend(bytes(((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))) * count)
            if len(pixels) != w * 3:
                raise ValueError("Wrong screen row width")
            rows[y][x * 3:(x + w) * 3] = pixels
            covered[y][x:x + w] = b"\1" * w
        raise RuntimeError("Screen capture timed out")
    finally:
        port.close()


if __name__ == "__main__":
    main()
