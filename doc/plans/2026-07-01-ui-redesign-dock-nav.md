# UI 重构(大头像/身份 + 横向 dock + 按键导航)—— 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把档案界面重排为「大头像 + 身份信息(上) + 底部横向 dock(下)」;头像静态无动画;上/下键在 dock 导航、确定键执行(换头像/亮度/LED/在线)。

**Architecture:** `presentation/ui_profile.c` 重排布局 + 建 4 个可聚焦 dock 磁贴(加入 LVGL 默认导航 group)+ 暴露 `on_action` 回调;`app.c` 复用 `platform_lvgl_attach_buttons` 接入编码器导航,注册并执行 dock 动作;去掉头像 bob/blink 动画与旧的"按键换头像"。

**Tech Stack:** C99、ESP-IDF 5.5.2、LVGL 9(group/encoder 导航)。

**本仓库特殊约定(务必先读):**
- 非 git 仓库,"提交"用**检查点**代替。
- 本重构无纯逻辑新模块,无 gcc 单测;三处文件互相依赖,**整机 `idf.py build` 是唯一编译验证**(Task 4);导航手感需真机。
- 跑 `idf.py` 前 `source "$IDF_PATH/export.sh"`,并**在项目根 `/home/cjiio/trae_card` 执行**。
- 字体:LVGL 配置里已启用的 montserrat 字号为 **12/14/24/28/34**(勿用未启用的字号如 20);Chinese 无字库,**dock 文字用英文/符号**。

---

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `components/ui/presentation/include/presentation/ui_profile.h` | 加 `ui_action_cb_t`、`ui_profile_set_on_action`、`ui_profile_set_dock_value` |
| `components/ui/presentation/src/ui_profile.c` | 重写:新布局 + dock + 去动画 + 回调 |
| `components/app/src/app.c` | 去 on_button;attach_buttons;注册并执行 dock 动作 |

---

## Task 1: 表现层头文件接口

**Files:** Modify `components/ui/presentation/include/presentation/ui_profile.h`

- [ ] **Step 1: 追加接口声明**

在 `ui_profile.h` 末尾(其它 `ui_profile_set_*` 之后)追加:
```c
// dock 某项被“确定”时回调(item_id: 0换头像 1亮度 2 LED 3在线)
typedef void (*ui_action_cb_t)(int item_id, void *user);
void ui_profile_set_on_action(ui_action_cb_t cb, void *user);

// 更新某 dock 磁贴显示的当前值文本(如 "60%"、"GRN"、"YES")
void ui_profile_set_dock_value(int item_id, const char *text);
```
其余保留不动。

- [ ] **Step 2: 检查点** —— 声明齐了,Task 2 实现。

---

## Task 2: 重写 `ui_profile.c`(新布局 + dock + 去动画)

**Files:** Rewrite `components/ui/presentation/src/ui_profile.c`

- [ ] **Step 1: 用以下完整内容替换整个文件**

```c
// components/presentation/src/ui_profile.c —— 档案页 + 底部横向 dock(静态头像 + 按键导航)
#include "presentation/ui_profile.h"
#include "lvgl.h"
#include "services/avatar.h"
#include <stdint.h>

// ---- 调色板 ----
#define COL_BG      lv_color_hex(0x0E1512)
#define COL_PANEL   lv_color_hex(0x141C18)
#define COL_PANEL_F lv_color_hex(0x12261C)   // dock 焦点态背景
#define COL_BORDER  lv_color_hex(0x2A3A33)
#define COL_TRACK   lv_color_hex(0x223029)
#define COL_GREEN   lv_color_hex(0x35E07E)
#define COL_GREEN2  lv_color_hex(0x46C77D)
#define COL_WHITE   lv_color_hex(0xF2F5F3)
#define COL_GRAY    lv_color_hex(0x707D77)
#define COL_BATT_LOW  lv_color_hex(0xF2C14E)
#define COL_BATT_CRIT lv_color_hex(0xE0463A)

// ---- 像素小人(放大 cell=6 → 画布 96×156)----
#define AVA_CELL 6
#define AVA_COLS 16
#define AVA_ROWS 26
#define AVA_W (AVA_COLS * AVA_CELL)   // 96
#define AVA_H (AVA_ROWS * AVA_CELL)   // 156
static uint8_t ava_buf[AVA_W * AVA_H * 2] __attribute__((aligned(4)));

typedef struct {
    lv_obj_t *time_lbl, *date_lbl;
    lv_obj_t *online_dot, *batt_pct;
    lv_obj_t *name_lbl, *role_lbl, *sub_lbl;
    lv_obj_t *lvl_num, *xp_lbl, *bar;
    int hh, mm, ss;
    lv_timer_t *clock;
    lv_obj_t *avatar_cv;
    avatar_t  avatar;
    lv_obj_t *dock_val[4];
} profile_t;
static profile_t P;

static ui_action_cb_t s_action_cb;
static void          *s_action_user;

static lv_obj_t *make_label(lv_obj_t *p, const char *txt, const lv_font_t *f, lv_color_t c) {
    lv_obj_t *l = lv_label_create(p);
    lv_label_set_text(l, txt);
    lv_obj_set_style_text_font(l, f, 0);
    lv_obj_set_style_text_color(l, c, 0);
    return l;
}
static lv_obj_t *make_box(lv_obj_t *p, int w, int h, lv_color_t bg) {
    lv_obj_t *o = lv_obj_create(p);
    lv_obj_remove_style_all(o);
    lv_obj_set_size(o, w, h);
    lv_obj_set_style_bg_color(o, bg, 0);
    lv_obj_set_style_bg_opa(o, LV_OPA_COVER, 0);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_SCROLLABLE);
    return o;
}
static lv_obj_t *make_dot(lv_obj_t *p, lv_color_t c, int sz) {
    lv_obj_t *d = make_box(p, sz, sz, c);
    lv_obj_set_style_radius(d, LV_RADIUS_CIRCLE, 0);
    return d;
}

// ---- 头像渲染(静态)。批量绘制期间关失效,画完只整块失效一次 ----
static void draw_grid(void) {
    lv_display_t *disp = lv_obj_get_display(P.avatar_cv);
    lv_display_enable_invalidation(disp, false);
    lv_canvas_fill_bg(P.avatar_cv, COL_BG, LV_OPA_COVER);
    for (int r = 0; r < AVA_ROWS; r++) {
        for (int c = 0; c < AVA_COLS; c++) {
            uint8_t role = P.avatar.cell[r][c];
            if (role == AV_BG) continue;
            lv_color_t col = lv_color_hex(P.avatar.palette[role]);
            for (int dy = 0; dy < AVA_CELL; dy++)
                for (int dx = 0; dx < AVA_CELL; dx++)
                    lv_canvas_set_px(P.avatar_cv, c * AVA_CELL + dx, r * AVA_CELL + dy, col, LV_OPA_COVER);
        }
    }
    lv_display_enable_invalidation(disp, true);
    lv_obj_invalidate(P.avatar_cv);
}

void ui_profile_set_avatar_seed(uint32_t seed) {
    avatar_generate(seed, &P.avatar);
    if (P.avatar_cv) draw_grid();
}

// ---- dock ----
static void dock_clicked(lv_event_t *e) {
    lv_obj_t *tile = lv_event_get_target(e);
    int id = (int)(intptr_t)lv_obj_get_user_data(tile);
    if (s_action_cb) s_action_cb(id, s_action_user);
}
static void make_dock_tile(lv_obj_t *parent, int x, int id, const char *name, const char *val) {
    lv_obj_t *t = lv_button_create(parent);
    lv_obj_remove_style_all(t);
    lv_obj_set_size(t, 52, 60);
    lv_obj_set_pos(t, x, 8);
    lv_obj_set_style_radius(t, 8, 0);
    lv_obj_set_style_bg_color(t, COL_PANEL, 0);
    lv_obj_set_style_bg_opa(t, LV_OPA_COVER, 0);
    lv_obj_set_style_border_color(t, COL_BORDER, 0);
    lv_obj_set_style_border_width(t, 1, 0);
    lv_obj_set_style_border_color(t, COL_GREEN, LV_STATE_FOCUSED);
    lv_obj_set_style_border_width(t, 2, LV_STATE_FOCUSED);
    lv_obj_set_style_bg_color(t, COL_PANEL_F, LV_STATE_FOCUSED);
    lv_obj_remove_flag(t, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_user_data(t, (void *)(intptr_t)id);
    lv_obj_add_event_cb(t, dock_clicked, LV_EVENT_CLICKED, NULL);

    // 加入默认导航 group(由 platform_lvgl_attach_buttons 预先设定)
    lv_group_t *g = lv_group_get_default();
    if (g) lv_group_add_obj(g, t);

    lv_obj_t *nl = make_label(t, name, &lv_font_montserrat_12, COL_GRAY);
    lv_obj_align(nl, LV_ALIGN_TOP_MID, 0, 4);
    lv_obj_t *vl = make_label(t, val, &lv_font_montserrat_14, COL_GREEN);
    lv_obj_align(vl, LV_ALIGN_BOTTOM_MID, 0, -6);
    if (id >= 0 && id < 4) P.dock_val[id] = vl;
}

void ui_profile_set_on_action(ui_action_cb_t cb, void *user) {
    s_action_cb = cb; s_action_user = user;
}
void ui_profile_set_dock_value(int item_id, const char *text) {
    if (item_id >= 0 && item_id < 4 && P.dock_val[item_id] && text)
        lv_label_set_text(P.dock_val[item_id], text);
}

static void refresh_time(void) {
    if (P.time_lbl) lv_label_set_text_fmt(P.time_lbl, "%02d:%02d", P.hh, P.mm);
}
static void clock_cb(lv_timer_t *t) {
    (void)t;
    if (++P.ss >= 60) {
        P.ss = 0;
        if (++P.mm >= 60) { P.mm = 0; P.hh = (P.hh + 1) % 24; }
        refresh_time();
    }
}

void ui_profile_create(void) {
    lv_obj_t *scr = lv_screen_active();
    lv_obj_set_style_bg_color(scr, COL_BG, 0);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, 0);
    lv_obj_set_style_pad_all(scr, 0, 0);
    lv_obj_remove_flag(scr, LV_OBJ_FLAG_SCROLLABLE);

    // ---- 状态栏 ----
    P.hh = 20; P.mm = 48; P.ss = 0;
    P.time_lbl = make_label(scr, "20:48", &lv_font_montserrat_24, COL_WHITE);
    lv_obj_set_pos(P.time_lbl, 12, 8);
    P.date_lbl = make_label(scr, "MON", &lv_font_montserrat_12, COL_GRAY);
    lv_obj_set_style_text_letter_space(P.date_lbl, 2, 0);
    lv_obj_set_pos(P.date_lbl, 92, 16);
    P.batt_pct = make_label(scr, "82%", &lv_font_montserrat_14, COL_WHITE);
    lv_obj_align(P.batt_pct, LV_ALIGN_TOP_RIGHT, -12, 12);
    P.online_dot = make_dot(scr, COL_GREEN, 8);
    lv_obj_align_to(P.online_dot, P.batt_pct, LV_ALIGN_OUT_LEFT_MID, -8, 0);

    // ---- 头像(静态,左)----
    P.avatar_cv = lv_canvas_create(scr);
    lv_canvas_set_buffer(P.avatar_cv, ava_buf, AVA_W, AVA_H, LV_COLOR_FORMAT_RGB565);
    lv_obj_set_pos(P.avatar_cv, 12, 34);
    avatar_generate(0u, &P.avatar);
    draw_grid();

    // ---- 身份(右)----
    P.name_lbl = make_label(scr, "FoloToy", &lv_font_montserrat_28, COL_WHITE);
    lv_obj_set_pos(P.name_lbl, 118, 40);
    P.role_lbl = make_label(scr, "NETRUNNER", &lv_font_montserrat_14, COL_GREEN2);
    lv_obj_set_style_text_letter_space(P.role_lbl, 3, 0);
    lv_obj_set_pos(P.role_lbl, 118, 78);
    P.sub_lbl = make_label(scr, "DATA STALKER", &lv_font_montserrat_12, COL_GRAY);
    lv_obj_set_style_text_letter_space(P.sub_lbl, 2, 0);
    lv_obj_set_pos(P.sub_lbl, 118, 100);

    P.lvl_num = make_label(scr, "LV.27", &lv_font_montserrat_14, COL_WHITE);
    lv_obj_set_pos(P.lvl_num, 118, 124);
    P.bar = lv_bar_create(scr);
    lv_obj_set_size(P.bar, 110, 8);
    lv_obj_set_pos(P.bar, 118, 146);
    lv_bar_set_range(P.bar, 0, 5000);
    lv_bar_set_value(P.bar, 3120, LV_ANIM_OFF);
    lv_obj_set_style_radius(P.bar, 4, 0);
    lv_obj_set_style_bg_color(P.bar, COL_TRACK, 0);
    lv_obj_set_style_bg_opa(P.bar, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(P.bar, 4, LV_PART_INDICATOR);
    lv_obj_set_style_bg_color(P.bar, COL_GREEN, LV_PART_INDICATOR);
    P.xp_lbl = make_label(scr, "3120 / 5000 XP", &lv_font_montserrat_12, COL_GRAY);
    lv_obj_set_pos(P.xp_lbl, 118, 158);

    // ---- 底部横向 dock ----
    lv_obj_t *dock = lv_obj_create(scr);
    lv_obj_remove_style_all(dock);
    lv_obj_set_size(dock, 240, 76);
    lv_obj_align(dock, LV_ALIGN_BOTTOM_MID, 0, 0);
    lv_obj_set_style_bg_color(dock, COL_PANEL, 0);
    lv_obj_set_style_bg_opa(dock, LV_OPA_COVER, 0);
    lv_obj_set_style_border_color(dock, COL_BORDER, 0);
    lv_obj_set_style_border_width(dock, 1, 0);
    lv_obj_remove_flag(dock, LV_OBJ_FLAG_SCROLLABLE);
    make_dock_tile(dock, 8,   0, "AVATAR", LV_SYMBOL_REFRESH);
    make_dock_tile(dock, 66,  1, "BRIGHT", "60%");
    make_dock_tile(dock, 124, 2, "LED",    "OFF");
    make_dock_tile(dock, 182, 3, "ONLINE", "YES");

    P.clock = lv_timer_create(clock_cb, 1000, NULL);
    refresh_time();
}

// ================= 运行时 setter(请在 LVGL 锁内调用)=================

void ui_profile_set_time(int hour, int minute) {
    P.hh = ((hour % 24) + 24) % 24;
    P.mm = ((minute % 60) + 60) % 60;
    P.ss = 0;
    refresh_time();
}
void ui_profile_set_date(const char *txt) {
    if (P.date_lbl && txt) lv_label_set_text(P.date_lbl, txt);
}
void ui_profile_set_battery(int percent) {
    if (percent < 0) percent = 0;
    if (percent > 100) percent = 100;
    lv_color_t col = (percent < 10) ? COL_BATT_CRIT
                   : (percent < 20) ? COL_BATT_LOW
                                    : COL_WHITE;
    if (P.batt_pct) {
        lv_label_set_text_fmt(P.batt_pct, "%d%%", percent);
        lv_obj_set_style_text_color(P.batt_pct, col, 0);
    }
}
void ui_profile_set_online(bool online) {
    if (P.online_dot)
        lv_obj_set_style_bg_color(P.online_dot, online ? COL_GREEN : COL_GRAY, 0);
}
void ui_profile_set_name(const char *name) {
    if (P.name_lbl && name) lv_label_set_text(P.name_lbl, name);
}
void ui_profile_set_role(const char *role) {
    if (P.role_lbl && role) lv_label_set_text(P.role_lbl, role);
}
void ui_profile_set_subtitle(const char *sub) {
    if (P.sub_lbl && sub) lv_label_set_text(P.sub_lbl, sub);
}
void ui_profile_set_level(int level, int xp, int xp_max) {
    if (xp_max <= 0) xp_max = 1;
    if (xp < 0) xp = 0;
    if (xp > xp_max) xp = xp_max;
    if (P.lvl_num) lv_label_set_text_fmt(P.lvl_num, "LV.%d", level);
    if (P.xp_lbl)  lv_label_set_text_fmt(P.xp_lbl, "%d / %d XP", xp, xp_max);
    if (P.bar) {
        lv_bar_set_range(P.bar, 0, xp_max);
        lv_bar_set_value(P.bar, xp, LV_ANIM_OFF);
    }
}
```

- [ ] **Step 2: 检查点** —— 无 bob/blink/anim 残留;`grep -nE "bob|anim|blink" components/ui/presentation/src/ui_profile.c` 应无输出。编译在 Task 4 验证。

---

## Task 3: 重写 `app.c`(导航接线 + dock 动作)

**Files:** Rewrite `components/app/src/app.c`

- [ ] **Step 1: 用以下完整内容替换整个文件**

```c
// components/app/src/app.c —— 唯一知道"具体实现"的地方
#include "app/app.h"
#include "platform/platform_factory.h"
#include "platform/board_config.h"
#include "services/ui_model.h"
#include <stdio.h>
#if PERIPH_LED
#include "services/led_service.h"
#endif
#if PERIPH_AUDIO
#include "services/audio_service.h"
#endif
#if PERIPH_DISPLAY
#include "presentation/ui_profile.h"
#endif
#if PERIPH_BLE
#include "services/profile_ctl.h"
#endif

#if PERIPH_BLE
static profile_data_t s_profile;
// BLE 写入回调(NimBLE host task 上下文)→ 校验 → 持 LVGL 锁更新界面
static void on_cfg_write(cfg_field_t field, const char *text, int len, void *user) {
    (void)user;
    if (profile_ctl_apply(&s_profile, field, text, len) < 0) return;
#if PERIPH_DISPLAY
    if (platform_lvgl_lock(0)) {
        switch (field) {
            case CFG_NAME:     ui_profile_set_name(s_profile.name);         break;
            case CFG_ROLE:     ui_profile_set_role(s_profile.role);         break;
            case CFG_SUBTITLE: ui_profile_set_subtitle(s_profile.subtitle); break;
            case CFG_BATTERY:  ui_profile_set_battery(s_profile.battery);   break;
            case CFG_ONLINE:   ui_profile_set_online(s_profile.online);     break;
            case CFG_LEVEL: case CFG_XP: case CFG_XP_MAX:
                ui_profile_set_level(s_profile.level, s_profile.xp, s_profile.xp_max); break;
            default: break;
        }
        platform_lvgl_unlock();
    }
#endif
}
#endif

#if PERIPH_LED
static led_service_t s_led;
static int s_led_idx = 0;                 // 0关 1绿 2青 3品红
#endif

#if PERIPH_DISPLAY
static hal_display_t *s_disp;
static int s_bright_idx = 1;              // 0:30 1:60 2:100
static const int BRIGHT[3] = {30, 60, 100};
static bool s_online = true;

// dock 某项被"确定"(编码器点击,运行在 LVGL 任务内,无需再加锁)
static void on_dock_action(int id, void *user) {
    (void)user;
    switch (id) {
        case 0:  // 换头像
            ui_profile_set_avatar_seed(platform_random_seed());
            break;
        case 1: {  // 屏幕亮度:30→60→100 循环
            s_bright_idx = (s_bright_idx + 1) % 3;
            if (s_disp) hal_display_set_backlight(s_disp, BRIGHT[s_bright_idx]);
            char b[8]; snprintf(b, sizeof b, "%d%%", BRIGHT[s_bright_idx]);
            ui_profile_set_dock_value(1, b);
            break;
        }
        case 2:  // LED:关→绿→青→品红 循环
#if PERIPH_LED
            s_led_idx = (s_led_idx + 1) % 4;
            switch (s_led_idx) {
                case 0: led_service_set_all(&s_led, 0, 0, 0);     ui_profile_set_dock_value(2, "OFF"); break;
                case 1: led_service_set_all(&s_led, 0, 180, 60);  ui_profile_set_dock_value(2, "GRN"); break;
                case 2: led_service_set_all(&s_led, 0, 160, 180); ui_profile_set_dock_value(2, "CYN"); break;
                case 3: led_service_set_all(&s_led, 180, 0, 120); ui_profile_set_dock_value(2, "MAG"); break;
            }
#endif
            break;
        case 3:  // 在线:切换
            s_online = !s_online;
            ui_profile_set_online(s_online);
            ui_profile_set_dock_value(3, s_online ? "YES" : "NO");
            break;
        default: break;
    }
}
#endif // PERIPH_DISPLAY

void app_run(void) {
    static const board_config_t board = BOARD_CONFIG_DEFAULT();

    // ui_model 始终存在,作为各业务的共享状态。
    static ui_model_t model; ui_model_init(&model);

#if PERIPH_BUTTON
    hal_button_t *btn = platform_create_button(&board);
#endif

#if PERIPH_DISPLAY
    s_disp = platform_create_display(&board);
    struct _lv_display_t *lvdisp = platform_lvgl_init(s_disp, &board);
#if PERIPH_BUTTON
    platform_lvgl_attach_buttons(btn, lvdisp);   // 上=prev 下=next 确定=enter(设默认导航 group)
#endif
    if (platform_lvgl_lock(0)) {                 // 建界面(dock 磁贴自动入导航 group)
        ui_profile_create();
        ui_profile_set_avatar_seed(platform_random_seed());
        ui_profile_set_on_action(on_dock_action, NULL);
        ui_profile_set_dock_value(1, "60%");
        ui_profile_set_dock_value(2, "OFF");
        ui_profile_set_dock_value(3, "YES");
        platform_lvgl_unlock();
    }
    hal_display_set_backlight(s_disp, BRIGHT[s_bright_idx]);   // 初始 60%
#endif

#if PERIPH_BLE
    profile_ctl_init(&s_profile);
    hal_config_t *cfg_src = platform_create_ble_config(&board);
    hal_config_on_write(cfg_src, on_cfg_write, NULL);
#endif

#if PERIPH_LED
    hal_led_strip_t *strip = platform_create_led_strip(&board);
    led_service_init(&s_led, strip);
    led_service_set_all(&s_led, 0, 0, 0);        // 默认关(与 dock 初值 OFF 一致)
#endif

#if PERIPH_AUDIO
    hal_audio_t *audio = platform_create_audio(&board);
    static audio_service_t au; audio_service_init(&au, audio);
    (void)au;
#endif

    ui_model_set_status(&model, "ready");
    ui_model_set_battery(&model, 100);
}
```

- [ ] **Step 2: 检查点** —— 无旧 `on_button`/`s_battery` 残留;`grep -nE "on_button|s_battery" components/app/src/app.c` 应无输出。

---

## Task 4: 整机构建 + 真机验证

- [ ] **Step 1: 全量构建**

Run:
```bash
cd /home/cjiio/trae_card && source "$IDF_PATH/export.sh" >/dev/null 2>&1 && idf.py build 2>&1 | tail -25
```
Expected: `Project build complete`,无 error/warning。`ui_profile.c`/`app.c` 均编过并链接。

- [ ] **Step 2: 若构建失败**

常见:LVGL API 名(`lv_button_create`/`lv_group_get_default`/`lv_group_add_obj`/`lv_obj_set_user_data`/`lv_display_enable_invalidation`/`LV_SYMBOL_REFRESH`/`LV_STATE_FOCUSED` 均为 LVGL 9 公有 API);字体未启用(仅用 12/14/24/28)。按报错修正后重跑 Step 1。

- [ ] **Step 3: 真机验证(手动,需硬件)**

`idf.py -p <串口> flash monitor`:
1. 界面:大头像(静态,左)+ 名字/角色/副标题/等级条(右)+ 底部 4 磁贴 dock;
2. **长时间运行不再触发 task_wdt**(头像已无动画);
3. 上/下键移动 dock 焦点(绿框高亮),确定分别:换头像 / 亮度 30→60→100 循环 / LED 关→绿→青→品红 / 在线 YES↔NO,磁贴值随之更新;
4. nRF Connect 写 name/role/battery 等仍更新右侧身份区。
   - 若确定键进入了"编辑模式"而非直接触发,给导航 group 关闭编辑或确认磁贴无编辑属性(实现阶段真机核对)。

- [ ] **Step 4: 最终检查点** —— 构建通过即完成;导航手感为真机验收。

---

## 附:验收对照(spec → 计划)

| spec 要求 | 对应 |
| --- | --- |
| 布局(状态栏/大头像+身份/横向 dock) | Task 2 `ui_profile_create` |
| 头像静态、去 bob/blink | Task 2(删除动画,`draw_grid()` 无 blink) |
| 头像放大 cell=6 | Task 2(`AVA_CELL 6`) |
| 导航(上/下/确定,LVGL group) | Task 3(`platform_lvgl_attach_buttons`)+ Task 2(磁贴入 group) |
| 4 dock 动作(换头像/亮度/LED/在线)+ 值显示 | Task 3 `on_dock_action` + Task 2 `dock_value` |
| on_action / dock_value 接口 | Task 1 / Task 2 |
| 去旧"按键换头像" | Task 3(删 on_button) |
| BLE 字段仍更新 | Task 3(保留 on_cfg_write) |
| 整机构建 | Task 4 |
