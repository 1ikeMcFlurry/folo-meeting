#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/gen_reward_art.py —— 生成 token 加/扣奖励动画的像素位图 C 头 ui_reward_art.h。
产出 3 张 ARGB8888 位图(放 flash .rodata,零 DRAM):
  - ui_coin      : 金币(径向金色 + 高光 + 中心星,旋转靠运行时 scale_x 振荡实现)
  - ui_gift_body : 礼盒盒体(红盒 + 金色十字丝带)
  - ui_gift_lid  : 礼盒盒盖(红盖 + 蝴蝶结,动画中向上飞出)
ARGB8888 在 LVGL 内存里字节序为 B,G,R,A;透明 a=0。带 PNG 预览便于校验。
"""
import struct, zlib, math

def emit_dsc(name, W, H, cells):
    data = bytearray()
    for (b, g, r, a) in cells: data += bytes((b, g, r, a))
    out = ["static const uint8_t %s_map[] = {" % name]
    for i in range(0, len(data), 24):
        out.append("    " + ",".join(str(x) for x in data[i:i+24]) + ",")
    out += ["};", "",
            "static const lv_image_dsc_t %s = {" % name,
            "    .header = { .magic = LV_IMAGE_HEADER_MAGIC, .cf = LV_COLOR_FORMAT_ARGB8888,",
            "                .w = %d, .h = %d, .stride = %d }," % (W, H, W*4),
            "    .data_size = sizeof(%s_map)," % name,
            "    .data = %s_map," % name, "};", ""]
    return "\n".join(out), (W, H, cells)

def lerp(a, b, t): return tuple(int(a[i]+(b[i]-a[i])*t) for i in range(3))

# ---------------- 金币 32×32 ----------------
def build_coin():
    W = H = 32
    cx = cy = (W-1)/2.0
    R = W/2.0 - 1
    GOLD_L = (255, 236, 150)   # 高光金
    GOLD   = (255, 200, 46)    # 主体金
    GOLD_D = (206, 148, 20)    # 暗金
    RIM    = (150, 100, 10)    # 边缘
    STAR   = (255, 248, 210)   # 中心星
    cells = []
    # 5x5 星形罩(中心)
    STARM = [" X ", "XXX", " X "]
    for y in range(H):
        for x in range(W):
            dx, dy = x-cx, y-cy
            d = math.hypot(dx, dy)
            if d > R:
                cells.append((0,0,0,0)); continue
            if d > R-1.4:
                r,g,b = RIM
            else:
                # 左上高光 → 右下暗
                t = (-dx - dy) / (R*1.7)
                if d < R*0.30:
                    r,g,b = STAR                      # 中心亮点(星)
                elif t > 0.35:
                    r,g,b = GOLD_L
                elif t < -0.30:
                    r,g,b = GOLD_D
                else:
                    r,g,b = lerp(GOLD, GOLD_L, max(0,t))
            cells.append((b, g, r, 255))
    return emit_dsc("ui_coin", W, H, cells)

# ---------------- 礼盒盒体 40×30 ----------------
def build_body():
    W, H = 40, 30
    RED_L=(255,96,110); RED=(226,54,72); RED_D=(176,30,48)
    GOLD=(255,206,74); GOLD_D=(206,150,26)
    cells=[]
    rib0, rib1 = W//2-3, W//2+3          # 中央竖丝带
    hrb0, hrb1 = 6, 12                    # 上横丝带
    for y in range(H):
        for x in range(W):
            if rib0<=x<=rib1:
                r,g,b = GOLD if (x!=rib0 and x!=rib1) else GOLD_D
            elif hrb0<=y<=hrb1:
                r,g,b = GOLD if (y!=hrb0 and y!=hrb1) else GOLD_D
            else:
                # 盒体明暗:上亮下暗 + 左右描边
                if x<=1 or x>=W-2 or y>=H-2: r,g,b = RED_D
                elif y<=2: r,g,b = RED_L
                else: r,g,b = RED
            cells.append((b,g,r,255))
    return emit_dsc("ui_gift_body", W, H, cells)

# ---------------- 礼盒盒盖(带蝴蝶结)44×20 ----------------
def build_lid():
    W, H = 44, 20
    RED_L=(255,96,110); RED=(210,44,62); RED_D=(168,26,44)
    GOLD=(255,206,74); GOLD_D=(206,150,26)
    cells=[[ (0,0,0,0) ] for _ in range(0)]
    grid=[(0,0,0,0)]*(W*H)
    def put(x,y,c):
        if 0<=x<W and 0<=y<H: grid[y*W+x]=c
    LID_TOP=8
    # 盖板(下半)
    for y in range(LID_TOP, H):
        for x in range(W):
            if y>=H-2 or x<=1 or x>=W-2: r,g,b=RED_D
            elif y==LID_TOP: r,g,b=RED_L
            else: r,g,b=RED
            put(x,y,(b,g,r,255))
    # 盖板中央金丝带
    for y in range(LID_TOP,H):
        for x in (W//2-1,W//2,W//2+1):
            r,g,b = GOLD if x==W//2 else GOLD_D
            put(x,y,(b,g,r,255))
    # 蝴蝶结:两侧三角环 + 中心结
    for y in range(0,LID_TOP+1):
        span = y+1
        for x in range(W//2-2-span, W//2-2):        # 左环
            r,g,b=GOLD if (x>W//2-3-span) else GOLD_D; put(x,y,(b,g,r,255))
        for x in range(W//2+3, W//2+3+span):        # 右环
            r,g,b=GOLD if (x<W//2+2+span) else GOLD_D; put(x,y,(b,g,r,255))
    for y in range(2,LID_TOP+2):                    # 中心结
        for x in (W//2-1,W//2,W//2+1):
            r,g,b=GOLD_D if y<4 else GOLD; put(x,y,(b,g,r,255))
    return emit_dsc("ui_gift_lid", W, H, [tuple(c) for c in grid])

# ---------------- 输出 ----------------
def write_png(path, sheet):
    # sheet: list of (name,(W,H,cells)); 竖排拼一张预览(深底)
    pad=6; maxw=max(w for _,(w,h,_) in sheet)
    tot_h=sum(h for _,(w,h,_) in sheet)+pad*(len(sheet)+1)
    PW,PH=maxw+2*pad, tot_h
    buf=bytearray((PW*PH)*3)
    for i in range(PW*PH): buf[i*3:i*3+3]=bytes((18,22,34))
    yoff=pad
    for _,(w,h,cells) in sheet:
        xoff=(PW-w)//2
        for yy in range(h):
            for xx in range(w):
                b,g,r,a=cells[yy*w+xx]
                if a==0: continue
                idx=((yoff+yy)*PW+(xoff+xx))*3
                buf[idx],buf[idx+1],buf[idx+2]=r,g,b
        yoff+=h+pad
    def chunk(t,d):
        c=t+d; return struct.pack(">I",len(d))+c+struct.pack(">I",zlib.crc32(c)&0xffffffff)
    raw=bytearray()
    for y in range(PH):
        raw.append(0); raw+=buf[y*PW*3:(y+1)*PW*3]
    png=b"\x89PNG\r\n\x1a\n"
    png+=chunk(b"IHDR",struct.pack(">IIBBBBB",PW,PH,8,2,0,0,0))
    png+=chunk(b"IDAT",zlib.compress(bytes(raw),9))
    png+=chunk(b"IEND",b"")
    open(path,"wb").write(png)

if __name__ == "__main__":
    parts=["// 自动生成(tools/gen_reward_art.py),勿手改。奖励动画像素位图(ARGB8888,放 flash)。",
           "#pragma once", '#include "lvgl.h"', ""]
    sheet=[]
    for fn in (build_coin, build_body, build_lid):
        code, meta = fn()
        parts.append(code)
        name = code.split("static const uint8_t ")[1].split("_map")[0]
        sheet.append((name, meta))
        print("%-14s %dx%d (%d 字节)" % (name, meta[0], meta[1], meta[0]*meta[1]*4))
    open("components/ui/presentation/src/ui_reward_art.h","w").write("\n".join(parts))
    write_png("/home/cjiio/trae_card/scratch_reward_preview.png", sheet)
    print("已写 ui_reward_art.h + 预览 scratch_reward_preview.png")
