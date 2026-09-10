#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/gen_pixel_text.py —— 把文字用 5x7 像素字体逐点放大成方块,生成 LVGL A8 位图 C 头文件。
A8(每像素 1 字节 alpha):on 像素 alpha=255、off=0;显示时用 image_recolor 上霓虹色。
位图放 flash(.rodata),不占 DRAM。用法见文件末尾 __main__。
"""
import sys

# 5x7 像素字体(每字 7 行,每行 5 位,'X'=亮点)。只含用到的字母 + 空格。
FONT = {
    ' ': ["     ", "     ", "     ", "     ", "     ", "     ", "     "],
    'H': ["X   X", "X   X", "X   X", "XXXXX", "X   X", "X   X", "X   X"],
    'E': ["XXXXX", "X    ", "X    ", "XXXX ", "X    ", "X    ", "XXXXX"],
    'L': ["X    ", "X    ", "X    ", "X    ", "X    ", "X    ", "XXXXX"],
    'O': [" XXX ", "X   X", "X   X", "X   X", "X   X", "X   X", " XXX "],
    'W': ["X   X", "X   X", "X   X", "X X X", "X X X", "XX XX", "X   X"],
    'R': ["XXXX ", "X   X", "X   X", "XXXX ", "X X  ", "X  X ", "X   X"],
    'D': ["XXXX ", "X   X", "X   X", "X   X", "X   X", "X   X", "XXXX "],
    'T': ["XXXXX", "  X  ", "  X  ", "  X  ", "  X  ", "  X  ", "  X  "],
    'A': [" XXX ", "X   X", "X   X", "XXXXX", "X   X", "X   X", "X   X"],
    'C': [" XXXX", "X    ", "X    ", "X    ", "X    ", "X    ", " XXXX"],
    'K': ["X   X", "X  X ", "X X  ", "XX   ", "X X  ", "X  X ", "X   X"],
}
CW, CH = 5, 7   # 单字逻辑宽高


def render(lines, block, char_gap=1, line_gap=1):
    """把多行文字渲染成 A8 像素矩阵(值 0/255)。block=每个逻辑像素的方块边长。"""
    # 逻辑网格尺寸
    grid_w = max(len(s) * CW + (len(s) - 1) * char_gap for s in lines)
    grid_h = len(lines) * CH + (len(lines) - 1) * line_gap
    grid = [[0] * grid_w for _ in range(grid_h)]
    for li, s in enumerate(lines):
        y0 = li * (CH + line_gap)
        # 本行居中
        row_w = len(s) * CW + (len(s) - 1) * char_gap
        x0 = (grid_w - row_w) // 2
        for ci, ch in enumerate(s):
            glyph = FONT[ch.upper()]
            cx = x0 + ci * (CW + char_gap)
            for ry in range(CH):
                for rx in range(CW):
                    if glyph[ry][rx] != ' ':
                        grid[y0 + ry][cx + rx] = 1
    # 放大成方块
    W, H = grid_w * block, grid_h * block
    buf = bytearray(W * H)
    for gy in range(grid_h):
        for gx in range(grid_w):
            if grid[gy][gx]:
                for by in range(block):
                    for bx in range(block):
                        buf[(gy * block + by) * W + (gx * block + bx)] = 255
    return W, H, bytes(buf)


def emit_c(name, W, H, data):
    out = []
    out.append("// 自动生成(tools/gen_pixel_text.py),勿手改。A8 像素文字位图,放 flash。")
    out.append("#pragma once")
    out.append('#include "lvgl.h"')
    out.append("")
    out.append("static const uint8_t %s_map[] = {" % name)
    for i in range(0, len(data), 20):
        out.append("    " + ",".join(str(b) for b in data[i:i + 20]) + ",")
    out.append("};")
    out.append("")
    out.append("static const lv_image_dsc_t %s = {" % name)
    out.append("    .header = { .magic = LV_IMAGE_HEADER_MAGIC, .cf = LV_COLOR_FORMAT_A8,")
    out.append("                .w = %d, .h = %d, .stride = %d }," % (W, H, W))
    out.append("    .data_size = sizeof(%s_map)," % name)
    out.append("    .data = %s_map," % name)
    out.append("};")
    out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    # 用法: gen_pixel_text.py <out.h> <c符号名> <方块px> <文字,行以 / 分隔>
    #   例: gen_pixel_text.py .../ui_bcast_pixels.h ui_bcast_pixels 6 HELLO/WORLD
    # 颜色由显示端 image_recolor 决定(A8 只存 alpha)。
    if len(sys.argv) >= 5:
        path, name, block, text = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
        lines = text.split("/")
    else:
        path, name, block, lines = "components/ui/presentation/src/ui_bcast_pixels.h", \
                                   "ui_bcast_pixels", 6, ["HELLO", "WORLD"]
    W, H, data = render(lines, block)
    open(path, "w").write(emit_c(name, W, H, data))
    print("已生成 %s : %dx%d, %d 字节(A8)" % (path, W, H, len(data)))
