#!/usr/bin/env python3
# 生成马里奥 ARGB8888 位图头 components/ui/presentation/src/ui_mario_bitmap.h。
# 位图为 const → 进 Flash(.rodata),不占 DRAM(避免与 BLE 争堆内存)。
#
# 用 16x16 像素画栅格 + 整数倍最近邻放大,保持像素风的硬边缘 —— 双线性插值会把
# 8bit 风格糊掉。放大 8 倍 → 128x128。
#   python3 tools/gen_mario_bitmap.py            生成头文件
#   python3 tools/gen_mario_bitmap.py --preview  只在终端预览,不写文件
import os
import sys

SCALE = 8               # 16x16 → 128x128
OUT = os.path.join(os.path.dirname(__file__),
                   "../components/ui/presentation/src/ui_mario_bitmap.h")

# 调色板(R,G,B)。'.' 为全透明。
PAL = {
    "r": (0xE5, 0x25, 0x21),   # 帽子/上衣 红
    "s": (0xFC, 0xA0, 0x44),   # 皮肤
    "h": (0x6A, 0x32, 0x00),   # 头发/胡子/鞋 棕
    "b": (0x18, 0x58, 0xF8),   # 背带裤 蓝
    "y": (0xFC, 0xD8, 0x30),   # 纽扣 黄
}

# 16x16 像素画。每行必须正好 16 个字符。
SPRITE = [
    "....rrrrrr......",
    "...rrrrrrrrrr...",
    "...hhhssshh.....",
    "..hshsssssh.....",
    "..hshhsssssh....",
    "..hhhhhhhhss....",
    "....ssssssss....",
    "...rrbrrrrb.....",
    "..rrrbrrrrbrr...",
    ".rrrrbbbbbbrrr..",
    ".ssrrbybbybrrss.",
    ".sssbbbbbbbbsss.",
    ".ssbbbbbbbbbbss.",
    "...bbbb..bbbb...",
    "..hhhh....hhhh..",
    ".hhhhh....hhhhh.",
]

PREVIEW_CH = {"r": "R", "s": "s", "h": "#", "b": "B", "y": "o", ".": " "}


def validate():
    bad = [(i, len(r)) for i, r in enumerate(SPRITE) if len(r) != 16]
    if bad:
        raise SystemExit(f"栅格行长必须是 16:{bad}")
    unknown = {c for r in SPRITE for c in r if c != "." and c not in PAL}
    if unknown:
        raise SystemExit(f"调色板里没有这些字符:{sorted(unknown)}")
    if len(SPRITE) != 16:
        raise SystemExit(f"栅格必须 16 行,当前 {len(SPRITE)}")


def preview():
    print("16x16 栅格预览(R=红 s=肤 #=棕 B=蓝 o=黄):\n")
    for r in SPRITE:
        print("   " + "".join(PREVIEW_CH[c] * 2 for c in r))
    print()


def main():
    validate()
    preview()
    if "--preview" in sys.argv:
        return

    W = H = 16 * SCALE
    data = bytearray()
    for py in range(H):
        row = SPRITE[py // SCALE]
        for px in range(W):
            c = row[px // SCALE]
            if c == ".":
                data += bytes((0, 0, 0, 0))
            else:
                r, g, b = PAL[c]
                data += bytes((b, g, r, 0xFF))   # LVGL ARGB8888 内存序: B,G,R,A

    lines = [
        "// 自动生成(tools/gen_mario_bitmap.py):马里奥 ARGB8888 位图,放 Flash(.rodata),0 DRAM。",
        f"// 16x16 像素画最近邻放大 {SCALE} 倍 → {W}x{H},保持像素风硬边缘。由 ui_mario.c 使用。",
        "#pragma once", '#include "lvgl.h"', "",
        f"#define UI_MARIO_W {W}", f"#define UI_MARIO_H {H}", "",
        "static const uint8_t ui_mario_map[] = {",
    ]
    row = []
    for byte in data:
        row.append(f"0x{byte:02X},")
        if len(row) == 16:
            lines.append("    " + "".join(row)); row = []
    if row:
        lines.append("    " + "".join(row))
    lines += ["};", "",
              "static const lv_image_dsc_t ui_mario_dsc = {",
              "    .header = {", "        .magic = LV_IMAGE_HEADER_MAGIC,",
              "        .cf = LV_COLOR_FORMAT_ARGB8888,", "        .flags = 0,",
              "        .w = UI_MARIO_W,", "        .h = UI_MARIO_H,",
              "        .stride = UI_MARIO_W * 4,", "        .reserved_2 = 0,", "    },",
              "    .data_size = sizeof(ui_mario_map),", "    .data = ui_mario_map,", "};", ""]
    with open(OUT, "w") as fp:
        fp.write("\n".join(lines))
    print(f"生成 {os.path.normpath(OUT)}  ({W}x{H}, {len(data)} 字节位图 → Flash)")


if __name__ == "__main__":
    main()
