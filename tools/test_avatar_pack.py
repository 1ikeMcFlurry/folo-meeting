import json
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

from PIL import Image

from tools.avatar_pack import AvatarPackError, build_pack


PARTITION_SIZE = 0x20000
NAMES = [
    "default", "amber_bot", "aqua_panda", "blue_wolf", "cyber_fox",
    "holo_bear", "mint_robot", "neon_cat", "red_rabbit", "violet_owl",
]


def make_catalog(root: Path) -> None:
    for index, name in enumerate(NAMES):
        image = Image.new("RGBA", (96, 156), (index * 17, 40, 90, 255))
        image.putpixel((0, 0), (0, 0, 0, 0))
        image.save(root / f"{name}.png", format="PNG", interlace=0)


class AvatarPackTest(unittest.TestCase):
    def test_builds_deterministic_aligned_pack_with_valid_crcs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_catalog(root)
            packed = build_pack(root, PARTITION_SIZE)

            self.assertEqual(PARTITION_SIZE, len(packed))
            self.assertEqual(b"AVA1", packed[:4])
            version, count, header_size, total_size, index_crc = struct.unpack_from(
                "<BBHII", packed, 4
            )
            self.assertEqual((1, 10, 296), (version, count, header_size))
            self.assertLess(total_size, PARTITION_SIZE)
            index = packed[16:header_size]
            self.assertEqual(index_crc, zlib.crc32(index) & 0xFFFFFFFF)
            self.assertEqual(b"\xff" * (PARTITION_SIZE - total_size), packed[total_size:])

            found_names = []
            for offset in range(16, header_size, 28):
                raw_name, png_offset, png_length, png_crc = struct.unpack_from(
                    "<16sIII", packed, offset
                )
                found_names.append(raw_name.split(b"\0", 1)[0].decode("ascii"))
                self.assertEqual(0, png_offset % 4)
                png = packed[png_offset:png_offset + png_length]
                self.assertEqual(b"\x89PNG\r\n\x1a\n", png[:8])
                self.assertEqual(png_crc, zlib.crc32(png) & 0xFFFFFFFF)
            self.assertEqual(sorted(NAMES), found_names)
            self.assertEqual(packed, build_pack(root, PARTITION_SIZE))

    def test_cli_packs_every_png_and_writes_matching_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_catalog(root)
            Image.new("RGBA", (96, 156), (255, 0, 0, 255)).save(root / "scratch.png")
            output = root / "imgava.bin"
            catalog = root / "avatar_catalog.json"
            subprocess.run([
                sys.executable, str(Path(__file__).with_name("avatar_pack.py")),
                "--input", str(root), "--output", str(output),
                "--catalog-output", str(catalog), "--size", hex(PARTITION_SIZE),
            ], check=True)
            self.assertEqual(11, output.read_bytes()[5])
            self.assertEqual(sorted(NAMES + ["scratch"]), json.loads(catalog.read_text())["avatars"])

    def test_accepts_any_nonempty_dynamic_catalog_without_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_catalog(root)
            (root / "default.png").rename(root / "staff_10.png")
            self.assertEqual(10, build_pack(root, PARTITION_SIZE)[5])

            for path in list(root.glob("*.png"))[1:]:
                path.unlink()
            self.assertEqual(1, build_pack(root, PARTITION_SIZE)[5])

            for path in root.glob("*.png"):
                path.unlink()
            with self.assertRaisesRegex(AvatarPackError, "at least one"):
                build_pack(root, PARTITION_SIZE)

    def test_rejects_invalid_name_dimensions_mode_and_corrupt_png(self):
        mutations = (
            ("invalid name", lambda root: (root / "bad name.png").write_bytes((root / "default.png").read_bytes())),
            ("96x156", lambda root: Image.new("RGBA", (95, 156)).save(root / "default.png")),
            ("RGBA", lambda root: Image.new("RGB", (96, 156)).save(root / "default.png")),
            ("PNG", lambda root: (root / "default.png").write_bytes(b"not a png")),
        )
        for expected, mutate in mutations:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                make_catalog(root)
                mutate(root)
                with self.assertRaisesRegex(AvatarPackError, expected):
                    build_pack(root, PARTITION_SIZE)

    def test_rejects_duplicate_case_collision_and_partition_overflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_catalog(root)
            (root / "DEFAULT.png").write_bytes((root / "default.png").read_bytes())
            with self.assertRaisesRegex(AvatarPackError, "duplicate"):
                build_pack(root, PARTITION_SIZE)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_catalog(root)
            with self.assertRaisesRegex(AvatarPackError, "overflow"):
                build_pack(root, 512)


if __name__ == "__main__":
    unittest.main()
