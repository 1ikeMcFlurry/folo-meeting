# 种子驱动像素小人生成器 —— 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 传入一个 32 位随机种子,生成独一无二、且同种子恒定不变的正常人形像素小人,替换现有写死的钢铁侠头像。

**Architecture:** 纯逻辑生成器 `avatar`(seed → 16×26 角色网格 + 种子派生调色板)落在 `core/services`,可脱离 IDF 用 gcc 单测;`presentation/ui_profile.c` 只把 `palette[role]` 转成 `lv_color` 画到画布;`app` 传入常量种子。

**Tech Stack:** C99、ESP-IDF 5.5.2、LVGL 9、mulberry32 PRNG。

> **⚠ 后续演进(2026-07-01,晚于本计划成稿):** 生成器风格经确认后由"人形全身"改为 **赛博朋克半身像 + 男/女随机**;并做了两处相关调整:①种子来源改为**每次开机硬件随机**(`platform_random_seed()`→`esp_random()`,见 Task 4);②角色枚举重命名(`AV_PANTS→AV_SHADE`、`AV_SHOE→AV_BACKDROP`,新增语义见 spec §3),`avatar.c` 生成器重写为赛博半身像,眨眼把 `AV_EYE` 映射到 `AV_OUTLINE`(HUD 瞬灭)。**下方 Task 1/Task 3 的代码块是初版(人形)记录;最终实现以 `avatar.c`/`ui_profile.c` 源码与 spec §3/§4 为准。** 骨架(接口/分层/CMake 接线/放大 cell=4/palette 渲染/API)不变。

**本仓库特殊约定(务必先读):**
- 本项目**不是 git 仓库**,所有"提交"用**检查点**代替(不执行 `git`)。
- `host_test`(linux target)当前因缺 `libbsd` 头文件无法构建(环境预先问题,与本功能无关)。因此纯逻辑模块 `avatar` 的 TDD **用 gcc 独立编译验证**(它零 IDF 依赖);Unity 测试文件仍加入仓库以符合工程约定,待 libbsd 修复后可在 host_test 跑。
- 整机集成验证用 `idf.py build`(esp32s3)。运行任何 `idf.py` 前先 `source "$IDF_PATH/export.sh"`。
- 临时文件目录(scratchpad):`/tmp/claude-1000/-home-cjiio-trae-card/82e5d0c8-4331-4280-9c6f-510076c1242a/scratchpad`

---

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `components/core/services/include/services/avatar.h` | 新增。角色枚举、`avatar_t`、`avatar_generate` 声明 |
| `components/core/services/src/avatar.c` | 新增。mulberry32 PRNG + HSV 调色 + 人形生成器 |
| `components/core/services/CMakeLists.txt` | 修改。`SRCS` 加 `src/avatar.c` |
| `components/core/services/test/test_avatar.c` | 新增。Unity 确定性/边界测试 |
| `components/core/services/test/CMakeLists.txt` | 修改。测试 `SRCS` 加 `test_avatar.c` |
| `host_test/main/CMakeLists.txt` | 修改。`SRCS` 加 `test_avatar.c` |
| `components/ui/presentation/include/presentation/ui_profile.h` | 修改。加 `ui_profile_set_avatar_seed` |
| `components/ui/presentation/src/ui_profile.c` | 修改。删写死头像,改 palette 渲染,放大到 cell=4,去喷焰,布局下移 |
| `components/app/src/app.c` | 修改。调用 `ui_profile_set_avatar_seed(AVATAR_SEED)` |

---

## Task 1: 纯逻辑生成器模块 `avatar`(gcc TDD)

**Files:**
- Create: `components/core/services/include/services/avatar.h`
- Create: `components/core/services/src/avatar.c`
- Test(临时,scratchpad): `.../scratchpad/ta_main.c`

- [ ] **Step 1: 写头文件 `avatar.h`**

Create `components/core/services/include/services/avatar.h`:

```c
// components/core/services/include/services/avatar.h
#pragma once
#include <stdint.h>

#define AVATAR_COLS 16
#define AVATAR_ROWS 26

typedef enum {
    AV_BG = 0,     // 透明背景(不绘制)
    AV_OUTLINE,    // 描边/嘴
    AV_SKIN,       // 皮肤
    AV_HAIR,       // 头发/帽子
    AV_SHIRT,      // 上衣主色
    AV_SHIRT2,     // 上衣副色/图案
    AV_PANTS,      // 下装(裤/裙)
    AV_SHOE,       // 鞋
    AV_EYE,        // 眼睛(眨眼时按 AV_SKIN 上色)
    AV_ACCENT,     // 配饰高光(眼镜/帽檐)
    AV_ROLE_COUNT
} avatar_role_t;

typedef struct {
    uint8_t  cell[AVATAR_ROWS][AVATAR_COLS];  // 每格取值为 avatar_role_t
    uint32_t palette[AV_ROLE_COUNT];          // 每个角色的颜色 0xRRGGBB(种子派生)
} avatar_t;

// 确定性生成:同一 seed → 同一 avatar_t
void avatar_generate(uint32_t seed, avatar_t *out);
```

- [ ] **Step 2: 写临时 gcc 测试 `ta_main.c`**

Create `/tmp/claude-1000/-home-cjiio-trae-card/82e5d0c8-4331-4280-9c6f-510076c1242a/scratchpad/ta_main.c`:

```c
#include "services/avatar.h"
#include <assert.h>
#include <string.h>
#include <stdio.h>

int main(void) {
    avatar_t a, b, c;
    avatar_generate(42u, &a);
    avatar_generate(42u, &b);
    assert(memcmp(&a, &b, sizeof a) == 0);          // 确定性

    avatar_generate(43u, &c);
    assert(memcmp(&a, &c, sizeof a) != 0);          // 不同种子不同

    uint32_t seeds[] = {0u, 1u, 0xFFFFFFFFu, 12345u};
    for (int i = 0; i < 4; i++) {
        avatar_t g; avatar_generate(seeds[i], &g);
        for (int y = 0; y < AVATAR_ROWS; y++)
            for (int x = 0; x < AVATAR_COLS; x++)
                assert(g.cell[y][x] < AV_ROLE_COUNT);   // 值域合法
    }
    printf("all avatar tests passed\n");
    return 0;
}
```

- [ ] **Step 3: 运行测试确认失败(未实现)**

Run:
```bash
cd /home/cjiio/trae_card && \
gcc .../scratchpad/ta_main.c \
    -I components/core/services/include \
    -o /tmp/.../scratchpad/ta 2>&1 | head
```
(把 `.../scratchpad` 换成完整 scratchpad 路径)
Expected: 链接/编译失败 —— `undefined reference to 'avatar_generate'`(因为 `avatar.c` 还没写)。

- [ ] **Step 4: 实现 `avatar.c`**

Create `components/core/services/src/avatar.c`:

```c
// components/core/services/src/avatar.c —— 种子驱动的确定性人形像素小人
#include "services/avatar.h"
#include <string.h>

// ---- mulberry32:种子初始化的确定性 PRNG ----
static uint32_t rng_next(uint32_t *s) {
    uint32_t z = (*s += 0x6D2B79F5u);
    z = (z ^ (z >> 15)) * (z | 1u);
    z ^= z + (z ^ (z >> 7)) * (z | 61u);
    return z ^ (z >> 14);
}
static uint32_t rb(uint32_t *s, uint32_t n) { return rng_next(s) % n; }  // 0..n-1

// ---- HSV(h:0..359 s,v:0..255) → 0xRRGGBB ----
static uint32_t hsv(int h, int sat, int val) {
    if (sat > 255) sat = 255;
    if (sat < 0) sat = 0;
    if (val > 255) val = 255;
    if (val < 0) val = 0;
    h %= 360; if (h < 0) h += 360;
    int region = h / 60, rem = (h - region * 60) * 255 / 60;
    int p = val * (255 - sat) / 255;
    int q = val * (255 - sat * rem / 255) / 255;
    int t = val * (255 - sat * (255 - rem) / 255) / 255;
    int R, G, B;
    switch (region) {
        case 0:  R = val; G = t;   B = p;   break;
        case 1:  R = q;   G = val; B = p;   break;
        case 2:  R = p;   G = val; B = t;   break;
        case 3:  R = p;   G = q;   B = val; break;
        case 4:  R = t;   G = p;   B = val; break;
        default: R = val; G = p;   B = q;   break;
    }
    return ((uint32_t)R << 16) | ((uint32_t)G << 8) | (uint32_t)B;
}

// 画一格并水平镜像(左右对称)
static void px(avatar_t *a, int x, int y, uint8_t role) {
    if (x < 0 || x >= AVATAR_COLS || y < 0 || y >= AVATAR_ROWS) return;
    a->cell[y][x] = role;
    a->cell[y][AVATAR_COLS - 1 - x] = role;
}
// 以中线为中心画一段 hw 列宽(左半 8-hw..7 及其镜像)
static void hspan(avatar_t *a, int y, int hw, uint8_t role) {
    for (int x = AVATAR_COLS / 2 - hw; x < AVATAR_COLS / 2; x++) px(a, x, y, role);
}

void avatar_generate(uint32_t seed, avatar_t *out) {
    memset(out, 0, sizeof(*out));
    uint32_t s = seed * 0x9E3779B9u + 0x6D2B79F5u;   // 混淆,避免 seed=0 退化

    // ---------- 种子派生调色板 ----------
    int skinH  = 18 + (int)rb(&s, 22);               // 18..39 暖色皮肤
    int skinV  = 205 + (int)rb(&s, 40);
    int hairH  = (int)rb(&s, 360);
    int shirtH = (int)rb(&s, 360);
    int pantsH = (int)rb(&s, 360);
    out->palette[AV_BG]      = 0x000000;
    out->palette[AV_OUTLINE] = 0x0E1512;
    out->palette[AV_SKIN]    = hsv(skinH, 95, skinV);
    out->palette[AV_HAIR]    = hsv(hairH, 55 + (int)rb(&s, 120), 70 + (int)rb(&s, 110));
    out->palette[AV_SHIRT]   = hsv(shirtH, 130 + (int)rb(&s, 90), 150 + (int)rb(&s, 80));
    out->palette[AV_SHIRT2]  = hsv((shirtH + 30) % 360, 150, 210);
    out->palette[AV_PANTS]   = hsv(pantsH, 80 + (int)rb(&s, 110), 90 + (int)rb(&s, 80));
    out->palette[AV_SHOE]    = hsv((int)rb(&s, 360), 40, 70);
    out->palette[AV_EYE]     = 0x101018;
    out->palette[AV_ACCENT]  = hsv((shirtH + 180) % 360, 210, 235);

    // ---------- 形状随机轴 ----------
    int headHW  = 3 + (int)rb(&s, 2);   // 头半宽 3..4
    int bodyHW  = 3 + (int)rb(&s, 2);   // 躯干半宽 3..4
    int hairSty = (int)rb(&s, 4);       // 0 短发 1 盖头 2 尖刺 3 帽子
    int chest   = (int)rb(&s, 3);       // 0 纯色 1 竖条 2 胸标
    int lower   = (int)rb(&s, 3);       // 0 长裤 1 短裤 2 裙
    int hasShoe = (int)rb(&s, 2);
    int glasses = (int)rb(&s, 3) == 0;

    // ---------- 头 ----------
    int headTop = 2, headBot = 9;
    for (int y = headTop; y <= headBot; y++) hspan(out, y, headHW, AV_SKIN);
    px(out, AVATAR_COLS / 2 - headHW - 1, 6, AV_SKIN);          // 耳
    px(out, AVATAR_COLS / 2 - headHW + 1, 6, AV_EYE);          // 眼(镜像成两只)
    if (glasses) {
        px(out, AVATAR_COLS / 2 - headHW + 1, 6, AV_ACCENT);
        px(out, AVATAR_COLS / 2 - headHW,     6, AV_ACCENT);
    }
    px(out, 7, 8, AV_OUTLINE);                                  // 嘴(中线两格)

    // ---------- 头发 ----------
    for (int y = headTop - 1; y <= headTop + 1; y++) hspan(out, y, headHW, AV_HAIR);
    if (hairSty == 1) { for (int y = headTop; y <= 6; y++) px(out, AVATAR_COLS / 2 - headHW, y, AV_HAIR); }
    else if (hairSty == 2) { hspan(out, headTop - 2, headHW - 1, AV_HAIR); }
    else if (hairSty == 3) { hspan(out, headTop - 1, headHW, AV_ACCENT); }

    // ---------- 颈 ----------
    hspan(out, 10, 1, AV_SKIN);

    // ---------- 躯干 ----------
    int tTop = 11, tBot = 17;
    for (int y = tTop; y <= tBot; y++) hspan(out, y, bodyHW, AV_SHIRT);
    if (chest == 1) { for (int y = tTop; y <= tBot; y++) px(out, 7, y, AV_SHIRT2); }
    else if (chest == 2) { px(out, 7, tTop + 1, AV_SHIRT2); px(out, 7, tTop + 2, AV_SHIRT2); }

    // ---------- 手臂 ----------
    for (int y = tTop; y <= tBot - 2; y++) px(out, AVATAR_COLS / 2 - bodyHW - 1, y, AV_SHIRT);
    px(out, AVATAR_COLS / 2 - bodyHW - 1, tBot - 1, AV_SKIN);   // 手

    // ---------- 下半身 ----------
    int lTop = 18;
    if (lower == 2) {                                          // 裙
        for (int i = 0; i < 3; i++) hspan(out, lTop + i, bodyHW + (i == 2 ? 1 : 0), AV_PANTS);
        px(out, 6, lTop + 3, AV_SKIN); px(out, 6, lTop + 4, AV_SKIN);
    } else {                                                   // 裤(长/短)
        int legBot = (lower == 1) ? lTop + 3 : lTop + 5;
        for (int y = lTop; y <= legBot; y++) px(out, 6, y, AV_PANTS);   // 两条腿(镜像)
        if (lower == 1) { for (int y = legBot + 1; y <= legBot + 2; y++) px(out, 6, y, AV_SKIN); }
    }

    // ---------- 鞋 ----------
    if (hasShoe) { px(out, 6, 24, AV_SHOE); px(out, 5, 24, AV_SHOE); }
}
```

- [ ] **Step 5: 运行测试确认通过**

Run:
```bash
cd /home/cjiio/trae_card && \
gcc .../scratchpad/ta_main.c components/core/services/src/avatar.c \
    -I components/core/services/include -std=c99 -Wall \
    -o /tmp/.../scratchpad/ta && /tmp/.../scratchpad/ta
```
Expected: 编译无警告,输出 `all avatar tests passed`,退出码 0。

- [ ] **Step 6: 检查点**

确认 `avatar.h`/`avatar.c` 已就位、gcc 测试通过。删除临时产物:`rm -f /tmp/.../scratchpad/ta`。

---

## Task 2: 把生成器编入 services 组件 + Unity 测试(工程约定)

**Files:**
- Modify: `components/core/services/CMakeLists.txt`
- Create: `components/core/services/test/test_avatar.c`
- Modify: `components/core/services/test/CMakeLists.txt`
- Modify: `host_test/main/CMakeLists.txt`

- [ ] **Step 1: 把 `avatar.c` 加入 services 组件的 SRCS**

Modify `components/core/services/CMakeLists.txt` —— 在 `SRCS` 列表末尾加 `"src/avatar.c"`:

```cmake
# components/services/CMakeLists.txt
idf_component_register(
    SRCS "src/led_service.c" "src/input_service.c" "src/audio_service.c" "src/ui_model.c" "src/avatar.c"
    INCLUDE_DIRS "include"
    REQUIRES ports)
```

- [ ] **Step 2: 写 Unity 测试 `test_avatar.c`**

Create `components/core/services/test/test_avatar.c`:

```c
#include "unity.h"
#include "services/avatar.h"
#include <string.h>

TEST_CASE("avatar generation is deterministic", "[avatar]") {
    avatar_t a, b;
    avatar_generate(42u, &a);
    avatar_generate(42u, &b);
    TEST_ASSERT_EQUAL_INT(0, memcmp(&a, &b, sizeof a));
}

TEST_CASE("different seeds produce different avatars", "[avatar]") {
    avatar_t a, c;
    avatar_generate(1u, &a);
    avatar_generate(2u, &c);
    TEST_ASSERT_NOT_EQUAL(0, memcmp(&a, &c, sizeof a));
}

TEST_CASE("all avatar cells are valid roles", "[avatar]") {
    avatar_t g;
    avatar_generate(0xFFFFFFFFu, &g);
    for (int y = 0; y < AVATAR_ROWS; y++)
        for (int x = 0; x < AVATAR_COLS; x++)
            TEST_ASSERT_LESS_THAN_UINT8(AV_ROLE_COUNT, g.cell[y][x]);
}
```

- [ ] **Step 3: 把测试源接入两处 CMake**

Modify `components/core/services/test/CMakeLists.txt`:

```cmake
# components/services/test/CMakeLists.txt
idf_component_register(
    SRCS "test_led_service.c" "test_input_service.c" "test_avatar.c" "mocks/mock_hal.c"
    INCLUDE_DIRS "."
    REQUIRES services unity)
```

Modify `host_test/main/CMakeLists.txt` —— 在 `SRCS` 里加一行 `"${TEST_DIR}/test_avatar.c"`:

```cmake
idf_component_register(
    SRCS "test_runner.c"
         "${TEST_DIR}/test_led_service.c"
         "${TEST_DIR}/test_input_service.c"
         "${TEST_DIR}/test_avatar.c"
         "${TEST_DIR}/mocks/mock_hal.c"
    INCLUDE_DIRS "${TEST_DIR}"
    REQUIRES services unity)
```

- [ ] **Step 4: 检查点**

Task 5 的整机构建会编译 `avatar.c`。host_test/linux 待 libbsd 修复后可运行 Unity 测试(本环境不阻塞)。

---

## Task 3: 表现层改用生成器 + 放大 + 去喷焰 + 新 API

**Files:**
- Modify: `components/ui/presentation/include/presentation/ui_profile.h`
- Modify: `components/ui/presentation/src/ui_profile.c`

- [ ] **Step 1: 头文件加 seed API**

Modify `components/ui/presentation/include/presentation/ui_profile.h` —— 在其它 `ui_profile_set_*` 声明旁加:

```c
void ui_profile_set_avatar_seed(uint32_t seed);     // 重算像素小人并重绘
```
文件顶部若无 `#include <stdint.h>` 则加上。

- [ ] **Step 2: `ui_profile.c` 顶部包含生成器,放大画布**

在 `#include "lvgl.h"` 后加:
```c
#include "services/avatar.h"
```
把像素格尺寸从 3 改到 4:
```c
#define AVA_CELL 4      // 放大:16×26 网格 → 画布 64×104
```
(`AVA_COLS`/`AVA_ROWS`/`AVA_W`/`AVA_H`/`ava_buf` 都基于 `AVA_CELL` 宏推导,自动变大。)

- [ ] **Step 3: 删除写死头像与喷焰常量,改 palette 渲染**

删除 `static const char *AVATAR[AVA_ROWS] = { ... };` 整块,以及 `#define EYE_ROW 9`、`#define ROW_EYE_BLINK ...`、`#define ROW_BLANK ...`。

把 `cell_color()`、`draw_grid()`、`render_avatar()` 三个函数整体替换为:

```c
// 角色 → 颜色(查种子派生调色板);blink 时把眼睛按肤色画(闭眼)
static void draw_grid(bool blink) {
    lv_canvas_fill_bg(P.avatar_cv, COL_BG, LV_OPA_COVER);
    for (int r = 0; r < AVA_ROWS; r++) {
        for (int c = 0; c < AVA_COLS; c++) {
            uint8_t role = P.avatar.cell[r][c];
            if (role == AV_BG) continue;
            if (blink && role == AV_EYE) role = AV_SKIN;
            lv_color_t col = lv_color_hex(P.avatar.palette[role]);
            for (int dy = 0; dy < AVA_CELL; dy++)
                for (int dx = 0; dx < AVA_CELL; dx++)
                    lv_canvas_set_px(P.avatar_cv, c * AVA_CELL + dx, r * AVA_CELL + dy, col, LV_OPA_COVER);
        }
    }
}
static void render_avatar(bool blink) { draw_grid(blink); }
```

> 注意:原代码里画布对象字段名为 `P.avatar`(`lv_obj_t*`)。因为现在 `P` 要同时保存"角色网格"(`avatar_t`),把画布字段**改名为 `P.avatar_cv`**,网格字段叫 `P.avatar`。下一步一并改结构体与所有引用处。

- [ ] **Step 4: 改结构体 profile_t,加入网格,画布字段改名**

在 `profile_t` 里:把 `lv_obj_t *avatar;` 改成 `lv_obj_t *avatar_cv;` 并新增 `avatar_t avatar;`;删除 `int flame;`。改后相关字段如下:

```c
    // 头像
    lv_obj_t   *avatar_cv;    // LVGL 画布对象
    avatar_t    avatar;       // 当前小人的角色网格 + 调色板
    lv_timer_t *anim;
    int         blink_left;   // 当前闭眼剩余帧
    int         next_blink;   // 距下次眨眼帧数
```

- [ ] **Step 5: 改 make_avatar / 动画回调,去喷焰**

把 `make_avatar()` 改为(用改名后的字段,先用种子 0 生成一版以便有画面):
```c
static lv_obj_t *make_avatar(lv_obj_t *p) {
    P.avatar_cv = lv_canvas_create(p);
    lv_canvas_set_buffer(P.avatar_cv, ava_buf, AVA_W, AVA_H, LV_COLOR_FORMAT_RGB565);
    avatar_generate(0u, &P.avatar);
    render_avatar(false);
    return P.avatar_cv;
}
```

把 `avatar_anim_cb()` 改为(去掉 `P.flame` 那一行喷焰逻辑):
```c
static void avatar_anim_cb(lv_timer_t *t) {
    (void)t;
    if (P.blink_left > 0) {
        P.blink_left--;
    } else if (--P.next_blink <= 0) {
        P.blink_left = 1 + rand() % 2;     // 闭眼 1..2 帧
        P.next_blink = 8 + rand() % 20;    // 距下次眨眼
    }
    render_avatar(P.blink_left > 0);
}
```

`avatar_bob_cb()` 里引用画布的地方无需改(它拿的是传入的 `obj`)。

- [ ] **Step 6: 新增 `ui_profile_set_avatar_seed`**

在 `ui_profile.c` 里(如 `make_avatar` 之后)新增:
```c
void ui_profile_set_avatar_seed(uint32_t seed) {
    avatar_generate(seed, &P.avatar);
    if (P.avatar_cv) render_avatar(P.blink_left > 0);
}
```

- [ ] **Step 7: `ui_profile_create` 里改画布引用 + 布局下移 + 去喷焰初始化**

在 `ui_profile_create()` 中:

1. 头像创建与动画那段,把 `P.flame = 0;` 删除,`make_avatar` 返回值仍存入局部 `av`(它就是 `P.avatar_cv`),bob 动画不变。头像对齐 y 从 78 上移到 66:
```c
    lv_obj_t *av = make_avatar(scr);
    lv_obj_align(av, LV_ALIGN_TOP_MID, 0, 66);   // 放大后上移让位
```
2. 眨眼定时器初始化删掉喷焰相关,只留:
```c
    srand(lv_tick_get());
    P.blink_left = 0;
    P.next_blink = 10 + rand() % 15;
    P.anim = lv_timer_create(avatar_anim_cb, 180, NULL);
```
3. 身份三行 y 偏移下移,给放大后的头像(66..170)让位:
```c
    lv_obj_align(P.role_lbl, LV_ALIGN_TOP_MID, 2, 180);   // 原 162
    lv_obj_align(P.name_lbl, LV_ALIGN_TOP_MID, 0, 196);   // 原 176
    lv_obj_align(P.sub_lbl,  LV_ALIGN_TOP_MID, 1, 230);   // 原 210
```
(底部等级卡片不动。像素/间距最终以真机屏幕观感为准微调。)

- [ ] **Step 8: 检查点**

确认 `ui_profile.c` 里已无写死头像/喷焰残留:
```bash
grep -nE "AVATAR\[|flame|ROW_EYE_BLINK|ROW_BLANK" components/ui/presentation/src/ui_profile.c
```
Expected: 无输出。(`P.avatar`/`P.avatar_cv` 属正常字段引用,不在此检查内。)

---

## Task 4: 组装层传入"每次开机随机"的种子

**Files:**
- Modify: `components/platform/platform_esp32/include/platform/platform_factory.h`
- Create: `components/platform/platform_esp32/src/platform_sys.c`
- Modify: `components/platform/platform_esp32/CMakeLists.txt`
- Modify: `components/app/src/app.c`

- [ ] **Step 1: 平台层加硬件随机种子(IDF 依赖锁在平台层)**

`platform_factory.h` 末尾加:
```c
// 硬件随机数种子:每次开机不同(用于随机化像素小人等)。与外设开关无关,始终可用。
#include <stdint.h>
uint32_t platform_random_seed(void);
```
新建 `components/platform/platform_esp32/src/platform_sys.c`(不受 `PERIPH_*` 门控):
```c
// components/platform_esp32/src/platform_sys.c —— 平台系统级服务(与外设编译开关无关)
#include "platform/platform_factory.h"
#include "esp_random.h"

uint32_t platform_random_seed(void) {
    return esp_random();   // 硬件 RNG,每次开机产出不同值
}
```
`platform_esp32/CMakeLists.txt`:`SRCS` 加 `"src/platform_sys.c"`,`PRIV_REQUIRES` 加 `esp_hw_support`(提供 `esp_random.h`)。

- [ ] **Step 2: app 每次开机喂随机种子**

`app.c` 的 `#if PERIPH_DISPLAY` 块内已 `#include "presentation/ui_profile.h"`。在 LVGL 锁内 `ui_profile_create()` 之后调用:
```c
        ui_profile_create();
        ui_profile_set_avatar_seed(platform_random_seed());   // 每次开机随机生成像素小人
```
(`platform_random_seed` 声明在 `platform_factory.h`,app 已包含;`app` 已 `REQUIRES platform_esp32`,无需改 CMake。)

- [ ] **Step 3: 检查点**

生成器仍是确定性(同种子同小人),种子每次开机变 → 每次重启换一个小人。日后要"固定某台设备的小人",把 `platform_random_seed()` 换成设备唯一 ID / NVS 读值即可,不动生成器与表现层。

---

## Task 5: 整机构建集成验证

- [ ] **Step 1: 全量构建 esp32s3**

Run:
```bash
cd /home/cjiio/trae_card && source "$IDF_PATH/export.sh" >/dev/null 2>&1 && \
idf.py build 2>&1 | tail -25
```
Expected: `Project build complete`(或 `.bin` 生成),无编译/链接错误。重点确认 `avatar.c`、`ui_profile.c`、`app.c` 均编过,且 `services`/`presentation`/`app` 链接成功。

- [ ] **Step 2: 若构建失败**

按报错定位:多为字段改名(`P.avatar` vs `P.avatar_cv`)遗漏或 include 缺失。修正后重跑 Step 1。

- [ ] **Step 3: 最终检查点**

构建通过即完成。可选:烧录真机 `idf.py flash monitor` 目视确认小人显示、放大、眨眼、上下浮动正常,换 `AVATAR_SEED` 值确认小人随之改变、同值不变。

---

## 附:验收对照(spec → 计划)

| spec 要求 | 对应 |
| --- | --- |
| 确定性(同种子同小人) | Task1 gcc 测试 + Task2 Unity `deterministic` |
| 独特性(不同种子不同) | Task1/Task2 `different seeds` |
| 生成落 core/services、纯逻辑无 LVGL | Task1(avatar.c 仅依赖 stdint/string) |
| 表现层查 palette 渲染 | Task3 Step3 |
| 放大到 cell=4 + 布局下移 | Task3 Step2/Step7 |
| bob+眨眼、去喷焰 | Task3 Step5/Step7 |
| app 传常量种子、API 预留 | Task4 |
| 单测在 core/services/test 并接 host_test | Task2 |
