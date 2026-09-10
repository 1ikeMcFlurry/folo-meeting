import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from tools.normalize_avatar import AvatarNormalizeError, normalize_avatar


class NormalizeAvatarTest(unittest.TestCase):
    def test_normalizes_chroma_portrait_to_decoder_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.png"
            output = root / "avatar.png"

            image = Image.new("RGB", (180, 240), (0, 255, 0))
            draw = ImageDraw.Draw(image)
            draw.rectangle((45, 30, 135, 220), fill=(24, 80, 190))
            image.save(source)

            normalize_avatar(source, output, require_alpha=True)

            with Image.open(output) as result:
                self.assertEqual((96, 156), result.size)
                self.assertEqual("RGBA", result.mode)
                self.assertEqual(0, result.getpixel((0, 0))[3])
                self.assertGreater(result.getpixel((48, 78))[3], 240)
                self.assertNotEqual(1, result.info.get("interlace", 0))

    def test_rejects_source_without_removable_border_background(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "opaque.png"
            output = root / "avatar.png"
            image = Image.new("RGB", (96, 156))
            pixels = image.load()
            for y in range(156):
                for x in range(96):
                    pixels[x, y] = ((x * 17) % 256, (y * 29) % 256, (x + y) % 256)
            image.save(source)

            with self.assertRaises(AvatarNormalizeError):
                normalize_avatar(source, output, require_alpha=True)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
