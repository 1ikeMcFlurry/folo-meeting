#!/usr/bin/env python3
"""Normalize generated portraits for the embedded PNG decoder."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image


OUTPUT_SIZE = (96, 156)


class AvatarNormalizeError(ValueError):
    pass


def _color_distance(left: tuple[int, ...], right: tuple[int, ...]) -> float:
    return math.sqrt(sum((int(left[i]) - int(right[i])) ** 2 for i in range(3)))


def _border_pixels(image: Image.Image) -> list[tuple[int, ...]]:
    width, height = image.size
    return (
        [image.getpixel((x, 0)) for x in range(width)]
        + [image.getpixel((x, height - 1)) for x in range(width)]
        + [image.getpixel((0, y)) for y in range(1, height - 1)]
        + [image.getpixel((width - 1, y)) for y in range(1, height - 1)]
    )


def _remove_flat_border(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    border = _border_pixels(rgba)
    key = tuple(sum(pixel[channel] for pixel in border) // len(border) for channel in range(3))
    if max(_color_distance(pixel, key) for pixel in border) > 24:
        raise AvatarNormalizeError("source border is not a removable flat background")

    output = rgba.copy()
    converted = []
    pixels = rgba.get_flattened_data()
    for red, green, blue, alpha in pixels:
        distance = _color_distance((red, green, blue), key)
        if distance <= 12:
            matte = 0
        elif distance >= 64:
            matte = alpha
        else:
            matte = round(alpha * (distance - 12) / 52)
        converted.append((red, green, blue, matte))
    output.putdata(converted)
    return output


def normalize_avatar(source: Path, output: Path, *, require_alpha: bool = False) -> None:
    source = Path(source)
    output = Path(output)
    with Image.open(source) as opened:
        image = opened.convert("RGBA")

    alpha_min, alpha_max = image.getchannel("A").getextrema()
    if alpha_min == alpha_max == 255:
        image = _remove_flat_border(image)
        alpha_min, alpha_max = image.getchannel("A").getextrema()
    if require_alpha and (alpha_min != 0 or alpha_max == 0):
        raise AvatarNormalizeError("avatar must contain transparent background and opaque subject")

    image.thumbnail(OUTPUT_SIZE, Image.Resampling.NEAREST)
    canvas = Image.new("RGBA", OUTPUT_SIZE, (0, 0, 0, 0))
    left = (OUTPUT_SIZE[0] - image.width) // 2
    top = (OUTPUT_SIZE[1] - image.height) // 2
    canvas.alpha_composite(image, (left, top))
    # A small fixed palette preserves the intended pixel-art look while keeping
    # all built-in portraits within the constrained flash resource partition.
    canvas = canvas.quantize(colors=64, method=Image.Quantize.FASTOCTREE).convert("RGBA")
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, format="PNG", optimize=True, interlace=0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--require-alpha", action="store_true")
    args = parser.parse_args()
    normalize_avatar(args.input, args.output, require_alpha=args.require_alpha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
