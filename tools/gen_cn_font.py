#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 LVGL 9 中文点阵字体 C 文件(ASCII + GB2312 一级汉字)。

用法:
  python3 tools/gen_cn_font.py                # 生成 16px 和 24px 两档
  python3 tools/gen_cn_font.py --size 16      # 只生成某一档

字符集: ASCII 0x20-0x7E + GB2312 一级汉字(3755 字,按频率排的常用字)
位深:   2bpp(4 级灰度) —— 中文小字比 1bpp 清晰得多,比 4bpp 省一半 flash
输出:   components/ui/presentation/src/lv_font_cn_<size>.c  (字体进 .rodata/flash,不占 DRAM)

换字体只需改 FONT_PATH(量产建议换开源字体,如思源黑体/文泉驿)。
"""
import argparse
import os
import sys

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("缺少 Pillow: pip install pillow")

FONT_PATH = "/mnt/c/Windows/Fonts/simhei.ttf"   # 黑体:小字号点阵最清晰
BPP = 2
OUT_DIR = os.path.join(os.path.dirname(__file__), "../components/ui/presentation/src")


def gb2312_level1():
    """枚举 GB2312 一级汉字(0xB0A1-0xD7F9, 3755 字),按编码序=频率序。"""
    chars = []
    for hi in range(0xB0, 0xD8):
        for lo in range(0xA1, 0xFF):
            try:
                ch = bytes([hi, lo]).decode("gb2312")
            except UnicodeDecodeError:
                continue
            if len(ch) == 1 and 0x4E00 <= ord(ch) <= 0x9FA5:
                chars.append(ch)
    return chars


def pack_bits(vals, bpp):
    """按 bpp 把像素值打包成字节流(MSB first,末尾补 0)——LVGL fmt_txt 位图格式。"""
    out = bytearray()
    acc = nbits = 0
    for v in vals:
        acc = (acc << bpp) | (v & ((1 << bpp) - 1))
        nbits += bpp
        while nbits >= 8:
            nbits -= 8
            out.append((acc >> nbits) & 0xFF)
    if nbits:
        out.append((acc << (8 - nbits)) & 0xFF)
    return bytes(out)


def render_glyph(font, ch, ascent, bpp):
    """渲染一个字形,返回 (bitmap_bytes, box_w, box_h, ofs_x, ofs_y, adv_w)。"""
    adv_w = int(round(font.getlength(ch) * 16))       # LVGL adv_w 单位 = 1/16 px
    bbox = font.getbbox(ch)
    if bbox is None:
        return b"", 0, 0, 0, 0, adv_w
    x0, y0, x1, y1 = bbox
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:                              # 空格等无墨迹字形
        return b"", 0, 0, 0, 0, adv_w

    img = Image.new("L", (w, h), 0)
    ImageDraw.Draw(img).text((-x0, -y0), ch, font=font, fill=255)
    shift = 8 - bpp
    vals = [p >> shift for p in img.getdata()]        # 255→3 (2bpp)
    return pack_bits(vals, bpp), w, h, x0, ascent - y1, adv_w


def gen(size, out_path, font_name):
    font = ImageFont.truetype(FONT_PATH, size)
    ascent, descent = font.getmetrics()

    ascii_chars = [chr(c) for c in range(0x20, 0x7F)]         # 95 个
    cjk_chars = gb2312_level1()
    cjk_chars.sort(key=ord)
    all_chars = ascii_chars + cjk_chars

    bitmap = bytearray()
    dscs = [(0, 0, 0, 0, 0, 0)]                                # id 0 保留
    for ch in all_chars:
        bm, bw, bh, ox, oy, aw = render_glyph(font, ch, ascent, BPP)
        dscs.append((len(bitmap), aw, bw, bh, ox, oy))
        bitmap += bm

    cjk_codes = [ord(c) for c in cjk_chars]
    cjk_start, cjk_end = cjk_codes[0], cjk_codes[-1]
    ascii_gid_start = 1
    cjk_gid_start = 1 + len(ascii_chars)

    L = []
    L.append('// 自动生成(tools/gen_cn_font.py),请勿手改。')
    L.append(f'// 字体:{os.path.basename(FONT_PATH)}  字号:{size}px  位深:{BPP}bpp')
    L.append(f'// 字符集:ASCII(0x20-0x7E) + GB2312 一级汉字({len(cjk_chars)} 字)')
    L.append('// 数据为 const,进 flash(.rodata),不占 DRAM。')
    L.append('#include "lvgl.h"')
    L.append('')
    L.append(f'#if LV_FONT_CN_{size}_ENABLE || 1')
    L.append('')
    L.append('static const uint8_t glyph_bitmap[] = {')
    for i in range(0, len(bitmap), 16):
        L.append('    ' + ''.join(f'0x{b:02X},' for b in bitmap[i:i + 16]))
    L.append('};')
    L.append('')
    L.append('static const lv_font_fmt_txt_glyph_dsc_t glyph_dsc[] = {')
    for (bi, aw, bw, bh, ox, oy) in dscs:
        L.append(f'    {{.bitmap_index = {bi}, .adv_w = {aw}, .box_w = {bw}, '
                 f'.box_h = {bh}, .ofs_x = {ox}, .ofs_y = {oy}}},')
    L.append('};')
    L.append('')
    L.append('/*汉字码位稀疏,用 SPARSE_TINY 的 unicode 偏移表*/')
    L.append('static const uint16_t unicode_list_cjk[] = {')
    offs = [c - cjk_start for c in cjk_codes]
    for i in range(0, len(offs), 12):
        L.append('    ' + ''.join(f'0x{o:04X}, ' for o in offs[i:i + 12]))
    L.append('};')
    L.append('')
    L.append('static const lv_font_fmt_txt_cmap_t cmaps[] = {')
    L.append('    {')
    L.append(f'        .range_start = 32, .range_length = {len(ascii_chars)}, '
             f'.glyph_id_start = {ascii_gid_start},')
    L.append('        .unicode_list = NULL, .glyph_id_ofs_list = NULL, .list_length = 0,')
    L.append('        .type = LV_FONT_FMT_TXT_CMAP_FORMAT0_TINY')
    L.append('    },')
    L.append('    {')
    L.append(f'        .range_start = {cjk_start}, .range_length = {cjk_end - cjk_start + 1}, '
             f'.glyph_id_start = {cjk_gid_start},')
    L.append(f'        .unicode_list = unicode_list_cjk, .glyph_id_ofs_list = NULL, '
             f'.list_length = {len(cjk_codes)},')
    L.append('        .type = LV_FONT_FMT_TXT_CMAP_SPARSE_TINY')
    L.append('    }')
    L.append('};')
    L.append('')
    L.append('static const lv_font_fmt_txt_dsc_t font_dsc = {')
    L.append('    .glyph_bitmap = glyph_bitmap,')
    L.append('    .glyph_dsc = glyph_dsc,')
    L.append('    .cmaps = cmaps,')
    L.append('    .kern_dsc = NULL,')
    L.append('    .kern_scale = 0,')
    L.append('    .cmap_num = 2,')
    L.append(f'    .bpp = {BPP},')
    L.append('    .kern_classes = 0,')
    L.append('    .bitmap_format = 0,')
    L.append('};')
    L.append('')
    L.append(f'const lv_font_t {font_name} = {{')
    L.append('    .get_glyph_dsc = lv_font_get_glyph_dsc_fmt_txt,')
    L.append('    .get_glyph_bitmap = lv_font_get_bitmap_fmt_txt,')
    L.append(f'    .line_height = {ascent + descent},')
    L.append(f'    .base_line = {descent},')
    L.append('    .subpx = LV_FONT_SUBPX_NONE,')
    L.append('    .underline_position = -2,')
    L.append('    .underline_thickness = 1,')
    L.append('    .dsc = &font_dsc,')
    L.append('    .fallback = NULL,')
    L.append('    .user_data = NULL,')
    L.append('};')
    L.append('')
    L.append('#endif')
    L.append('')

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"生成 {os.path.normpath(out_path)}  字形={len(dscs)-1} "
          f"位图={len(bitmap)/1024:.1f}KB 行高={ascent+descent} 基线={descent}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, action="append", help="字号(可多次);默认 16 和 24")
    args = ap.parse_args()
    sizes = args.size or [16, 24]
    if not os.path.exists(FONT_PATH):
        sys.exit(f"找不到字体文件: {FONT_PATH}")
    os.makedirs(OUT_DIR, exist_ok=True)
    for s in sizes:
        gen(s, os.path.join(OUT_DIR, f"lv_font_cn_{s}.c"), f"lv_font_cn_{s}")


if __name__ == "__main__":
    main()
