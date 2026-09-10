"""Back up only the factory application; never read recording/identity partitions."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

import esptool


def partitions(data):
    result = []
    for position in range(0, len(data), 32):
        entry = data[position:position + 32]
        if len(entry) != 32 or entry[:2] != b"\xaa\x50":
            break
        _, kind, subtype, offset, size, name, flags = struct.unpack("<HBBII16sI", entry)
        result.append({"name": name.split(b"\0", 1)[0].decode("ascii"),
                       "type": kind, "subtype": subtype, "offset": offset,
                       "size": size, "flags": flags})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM3")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    target = args.output_dir.resolve()
    if target.exists():
        raise RuntimeError("Backup directory already exists; refusing to overwrite")
    esp = esptool.detect_chip(args.port)
    try:
        if esp.CHIP_NAME != "ESP32-C3":
            raise RuntimeError("Unexpected chip")
        if esp.get_secure_boot_enabled() or esp.get_flash_encryption_enabled():
            raise RuntimeError("Protected device requires a separately reviewed flashing workflow")
        esp = esp.run_stub()
        # Read-only stub transfer block size (not a flash erase operation).
        # Keep individual USB packets below this board's receive-buffer limit.
        esp.FLASH_SECTOR_SIZE = 1024
        table = esp.read_flash(0x8000, 0x1000)
        entries = partitions(table)
        matches = [e for e in entries if e["name"] == "factory" and e["type"] == 0 and e["subtype"] == 0]
        if len(matches) != 1:
            raise RuntimeError("Expected one factory application partition")
        app = matches[0]
        if app["offset"] != 0x10000 or not 0 < app["size"] <= 0x300000:
            raise RuntimeError("Unexpected factory partition boundary")
        for entry in entries:
            if entry is not app and max(app["offset"], entry["offset"]) < min(app["offset"] + app["size"], entry["offset"] + entry["size"]):
                raise RuntimeError("Partition overlap")
        # Bound USB serial reads: long transfers can overrun the C3 USB bridge.
        image = bytearray()
        for position in range(0, app["size"], 1024):
            image.extend(esp.read_flash(app["offset"] + position, min(1024, app["size"] - position)))
            if len(image) % 0x40000 == 0:
                print(f"Backed up {len(image)} / {app['size']} application bytes", flush=True)
        if image[:1] != b"\xe9":
            raise RuntimeError("Factory partition does not contain an ESP application image")
        target.mkdir(parents=True)
        (target / "factory-original.bin").write_bytes(image)
        (target / "partition-table.bin").write_bytes(table)
        metadata = {"chip": esp.CHIP_NAME, "app": app, "partitions": entries,
                    "sha256": hashlib.sha256(image).hexdigest(),
                    "partition_table_sha256": hashlib.sha256(table).hexdigest()}
        (target / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"event": "backup_complete", "path": str(target),
                          "app_bytes": len(image), "sha256": metadata["sha256"]}))
    finally:
        esp.hard_reset()
        esp._port.close()


if __name__ == "__main__":
    main()
