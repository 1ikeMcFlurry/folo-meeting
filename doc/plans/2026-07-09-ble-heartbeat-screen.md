# 蓝牙广播 → 爱心跳动屏 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 设备扫描到特定厂商自定义 BLE 广播（`FF FF 48 42 …`）时切到爱心屏，每收到一个匹配包爱心跳动一下，连续 5 秒无匹配则自动退回主界面。

**Architecture:** 遵循现有六边形分层。纯匹配逻辑放 core/services（可 host_test 单测）；扫描能力经新 HAL 端口 `hal_ble_scan` 由 platform 的 NimBLE 观察者实现，与现有 GATT 服务端共用同一个 host；爱心屏是新的 LVGL 表现层单元，照抄 `ui_game` 的开/关/线程安全输入范式；app 组合根负责接线。

**Tech Stack:** ESP-IDF、NimBLE（observer + peripheral 多角色并存）、LVGL 9.5（`lv_canvas` 绘制 + `lv_anim` 缩放动画）、Unity（host_test 单测）。

**设计依据:** `doc/specs/2026-07-09-ble-heartbeat-screen-design.md`

---

## 环境与约定（务必先读）

- **本仓库不是 git 仓库**（`git rev-parse` 报错）。因此本计划**没有 `git commit` 步骤**；每个任务末尾用「构建校验（Checkpoint）」代替提交。若后续 `git init`，可在每个 Checkpoint 处提交。
- **构建/烧录须在已激活 ESP-IDF 的交互 shell 里执行**（本机非交互 shell 的 `IDF_PATH` 为空）。示例假设已 `. $IDF_PATH/export.sh`。
- **host_test 当前失效**：`host_test/build/` 是从另一个项目（`/home/cjiio/hello_world`）配置的、且依赖的 libbsd 头文件路径已不存在，系统也无 `libbsd-dev`。因此 Task 1 的单测**代码必须写并注册**，但「运行单测」是可选/best-effort：需要先重建 host_test 工程并提供 libbsd 头（见 Task 1 步骤 6 的说明）。**主验证手段是固件构建（Task 6）+ 硬件联调（Task 7）。**
- LVGL 版本 9.5：屏幕切换用 `lv_screen_active()` / `lv_screen_load()`；对象删除用 `lv_obj_delete()`；定时器用 `lv_timer_create/lv_timer_delete`。
- 显示分辨率 240×320（竖屏），深色底沿用 `0x1C1C1C`。

---

## 文件结构

**新建：**
- `components/core/services/include/services/ble_match.h` — 纯匹配谓词 + 常量声明
- `components/core/services/src/ble_match.c` — `ble_match_is_heartbeat()` 实现
- `components/core/services/test/test_ble_match.c` — Unity 单测
- `components/core/ports/include/hal/hal_ble_scan.h` — 新 HAL 端口（扫描匹配回调）
- `components/platform/platform_esp32/src/ble_internal.h` — 平台内部头，暴露 `ble_scan_start()`
- `components/platform/platform_esp32/src/ble_scan.c` — NimBLE observer 实现
- `components/ui/presentation/include/presentation/ui_heart.h` — 爱心屏接口
- `components/ui/presentation/src/ui_heart.c` — 爱心屏实现

**修改：**
- `components/core/services/CMakeLists.txt` — SRCS 加 `ble_match.c`
- `components/core/services/test/CMakeLists.txt` — SRCS 加 `test_ble_match.c`
- `host_test/main/CMakeLists.txt` — SRCS 加 `test_ble_match.c`
- `components/platform/platform_esp32/src/ble_config.c` — `on_sync()` 里追加 `ble_scan_start(s_addr_type)`
- `components/platform/platform_esp32/CMakeLists.txt` — SRCS 加 `ble_scan.c`
- `components/platform/platform_esp32/include/platform/platform_factory.h` — 加 `platform_create_ble_scan()` 声明 + include 端口头
- `components/ui/presentation/CMakeLists.txt` — SRCS 加 `ui_heart.c`
- `components/app/src/app.c` — 接线 scan → heart

---

## Task 1: 纯匹配模块 `ble_match`（TDD）

匹配规则：厂商数据字节序列以 `FF FF 48 42` 开头即命中（公司 ID `0xFFFF` 小端 + 魔数 `"HB"`），多余负载忽略。

**Files:**
- Create: `components/core/services/include/services/ble_match.h`
- Create: `components/core/services/src/ble_match.c`
- Create: `components/core/services/test/test_ble_match.c`
- Modify: `components/core/services/CMakeLists.txt`
- Modify: `components/core/services/test/CMakeLists.txt`
- Modify: `host_test/main/CMakeLists.txt`

- [ ] **Step 1: 写头文件**（先建接口，测试才能 include）

`components/core/services/include/services/ble_match.h`:
```c
// components/core/services/include/services/ble_match.h —— 纯逻辑,无 NimBLE/LVGL
#pragma once
#include <stdint.h>
#include <stdbool.h>

// 目标广播的厂商数据前缀:公司 ID 0xFFFF(小端 FF FF) + 魔数 "HB"(0x48 0x42)。
#define BLE_MATCH_PREFIX_0 0xFF
#define BLE_MATCH_PREFIX_1 0xFF
#define BLE_MATCH_PREFIX_2 0x48   // 'H'
#define BLE_MATCH_PREFIX_3 0x42   // 'B'
#define BLE_MATCH_PREFIX_LEN 4

// mfg = 广播里 Manufacturer Specific Data 的原始字节(含公司 ID 两字节),len 为其长度。
// 前 4 字节等于上面的前缀即命中。返回 true=命中。
bool ble_match_is_heartbeat(const uint8_t *mfg, int len);
```

- [ ] **Step 2: 写失败测试**

`components/core/services/test/test_ble_match.c`:
```c
#include "unity.h"
#include "services/ble_match.h"

TEST_CASE("exact 4-byte prefix matches", "[ble_match]") {
    const uint8_t d[] = {0xFF, 0xFF, 0x48, 0x42};
    TEST_ASSERT_TRUE(ble_match_is_heartbeat(d, 4));
}
TEST_CASE("prefix with extra payload matches", "[ble_match]") {
    const uint8_t d[] = {0xFF, 0xFF, 0x48, 0x42, 0x01, 0x02, 0x03};
    TEST_ASSERT_TRUE(ble_match_is_heartbeat(d, 7));
}
TEST_CASE("wrong company id rejected", "[ble_match]") {
    const uint8_t d[] = {0x4C, 0x00, 0x48, 0x42};   // Apple 0x004C
    TEST_ASSERT_FALSE(ble_match_is_heartbeat(d, 4));
}
TEST_CASE("wrong magic rejected", "[ble_match]") {
    const uint8_t d[] = {0xFF, 0xFF, 0x58, 0x42};   // 'X''B'
    TEST_ASSERT_FALSE(ble_match_is_heartbeat(d, 4));
}
TEST_CASE("too short rejected", "[ble_match]") {
    const uint8_t d[] = {0xFF, 0xFF, 0x48};
    TEST_ASSERT_FALSE(ble_match_is_heartbeat(d, 3));
}
TEST_CASE("null pointer rejected", "[ble_match]") {
    TEST_ASSERT_FALSE(ble_match_is_heartbeat(NULL, 4));
}
```

- [ ] **Step 3: 注册测试源到两处 CMake**

`components/core/services/test/CMakeLists.txt` — 在 SRCS 末尾加 `"test_ble_match.c"`：
```cmake
# components/services/test/CMakeLists.txt
idf_component_register(
    SRCS "test_led_service.c" "test_input_service.c" "test_avatar.c" "test_profile_ctl.c" "test_ble_match.c" "mocks/mock_hal.c"
    INCLUDE_DIRS "."
    REQUIRES services unity)
```

`host_test/main/CMakeLists.txt` — 在 SRCS 列表里加 `"${TEST_DIR}/test_ble_match.c"`（放在其它 test_*.c 之后、mock 之前）。

- [ ] **Step 4: 写最小实现**

`components/core/services/src/ble_match.c`:
```c
// components/core/services/src/ble_match.c
#include "services/ble_match.h"

bool ble_match_is_heartbeat(const uint8_t *mfg, int len) {
    if (mfg == NULL || len < BLE_MATCH_PREFIX_LEN) return false;
    return mfg[0] == BLE_MATCH_PREFIX_0 && mfg[1] == BLE_MATCH_PREFIX_1 &&
           mfg[2] == BLE_MATCH_PREFIX_2 && mfg[3] == BLE_MATCH_PREFIX_3;
}
```

- [ ] **Step 5: 注册实现源到 services CMake**

`components/core/services/CMakeLists.txt` — SRCS 末尾加 `"src/ble_match.c"`：
```cmake
idf_component_register(
    SRCS "src/led_service.c" "src/input_service.c" "src/audio_service.c" "src/ui_model.c" "src/avatar.c" "src/profile_ctl.c" "src/ble_match.c"
    INCLUDE_DIRS "include"
    REQUIRES ports)
```

- [ ] **Step 6: 运行单测（可选，best-effort）**

host_test 工程当前失效，需先重建。在**已激活 IDF 的交互 shell** 里，且已准备好 libbsd 头文件时：
```bash
rm -rf host_test/build
cd host_test && idf.py --preview set-target linux \
  -DBSD_INC=<libbsd头目录> -DBSD_LIB=<含libbsd.so的目录> build
./build/host_test.elf
```
Expected：Unity 汇总里 `[ble_match]` 6 个用例全 PASS。
若本机无法提供 libbsd（无 `libbsd-dev`），跳过此步，改由 Task 6 的固件编译保证 `ble_match.c` 能编过，硬件联调（Task 7）验证匹配行为。

- [ ] **Step 7: Checkpoint** — 逻辑正确性以 Step 6 或后续固件构建为准，无 git 提交。

---

## Task 2: 新 HAL 端口 `hal_ble_scan`

**Files:**
- Create: `components/core/ports/include/hal/hal_ble_scan.h`

- [ ] **Step 1: 写端口头文件**

`components/core/ports/include/hal/hal_ble_scan.h`:
```c
// components/core/ports/include/hal/hal_ble_scan.h
#pragma once
#include <stdint.h>

// 一次匹配命中:命中的厂商数据原始字节(含公司 ID 两字节)+ 长度。
// 回调在 NimBLE host 任务上下文触发,实现方须保证跨线程安全(参考 on_cfg_write)。
typedef void (*ble_match_cb_t)(const uint8_t *data, int len, void *user);

typedef struct hal_ble_scan_s hal_ble_scan_t;
typedef struct {
    void (*on_match)(hal_ble_scan_t *self, ble_match_cb_t cb, void *user);
} hal_ble_scan_api_t;
struct hal_ble_scan_s { const hal_ble_scan_api_t *api; void *impl; };

static inline void hal_ble_scan_on_match(hal_ble_scan_t *s, ble_match_cb_t cb, void *user) {
    s->api->on_match(s, cb, user);
}
```

`ports` 是纯头组件（无 SRCS），新增头无需改 CMake。

- [ ] **Step 2: Checkpoint** — 头文件独立无依赖，随 Task 3 一起编译验证。

---

## Task 3: 平台 NimBLE 观察者实现 `ble_scan.c`

与 `ble_config.c` 共用同一个 NimBLE host（不重复 init）；扫描在 host 同步回调 `on_sync` 里启动。被动扫描 + 关去重。

**Files:**
- Create: `components/platform/platform_esp32/src/ble_internal.h`
- Create: `components/platform/platform_esp32/src/ble_scan.c`
- Modify: `components/platform/platform_esp32/src/ble_config.c`（on_sync 追加一行 + include）
- Modify: `components/platform/platform_esp32/CMakeLists.txt`
- Modify: `components/platform/platform_esp32/include/platform/platform_factory.h`

- [ ] **Step 1: 写内部头**

`components/platform/platform_esp32/src/ble_internal.h`:
```c
// components/platform/platform_esp32/src/ble_internal.h —— 平台内 BLE 协作接口
#pragma once
#include <stdint.h>

// 由 ble_config.c 的 on_sync 调用:在广播启动后启动 BLE 扫描(observer)。
// addr_type 为 ble_hs_id_infer_auto 推得的本机地址类型。
void ble_scan_start(uint8_t addr_type);
```

- [ ] **Step 2: 写扫描实现**

`components/platform/platform_esp32/src/ble_scan.c`:
```c
// components/platform/platform_esp32/src/ble_scan.c —— NimBLE observer:匹配厂商广播 → app 回调
#include "platform/board_config.h"
#if PERIPH_BLE
#include "platform/platform_factory.h"
#include "hal/hal_ble_scan.h"
#include "services/ble_match.h"
#include "ble_internal.h"
#include "esp_log.h"
#include "host/ble_hs.h"
#include "host/ble_gap.h"

static const char *TAG = "ble_scan";

static ble_match_cb_t s_cb;
static void          *s_user;

// disc 回调:解析广播字段,取厂商数据,命中就回调上层。
static int scan_gap_event(struct ble_gap_event *event, void *arg) {
    (void)arg;
    if (event->type != BLE_GAP_EVENT_DISC) return 0;
    struct ble_hs_adv_fields f;
    if (ble_hs_adv_parse_fields(&f, event->disc.data, event->disc.length_data) != 0) return 0;
    if (f.mfg_data && ble_match_is_heartbeat(f.mfg_data, f.mfg_data_len)) {
        if (s_cb) s_cb(f.mfg_data, f.mfg_data_len, s_user);
    }
    return 0;
}

// on_sync 里调用:被动扫描、关重复过滤(要连续上报以便"收到就跳")、温和占空比。
void ble_scan_start(uint8_t addr_type) {
    struct ble_gap_disc_params dp = {0};
    dp.passive = 1;
    dp.filter_duplicates = 0;
    dp.itvl = 256;    // 256 * 0.625ms = 160ms 扫描间隔
    dp.window = 48;   // 48 * 0.625ms = 30ms 扫描窗口(约 19% 占空,给手机连接留时间)
    int rc = ble_gap_disc(addr_type, BLE_HS_FOREVER, &dp, scan_gap_event, NULL);
    if (rc != 0) ESP_LOGW(TAG, "ble_gap_disc failed rc=%d", rc);
}

static void scan_on_match(hal_ble_scan_t *self, ble_match_cb_t cb, void *user) {
    (void)self; s_cb = cb; s_user = user;
}
static const hal_ble_scan_api_t API = { .on_match = scan_on_match };
static hal_ble_scan_t s_handle = { .api = &API, .impl = NULL };

hal_ble_scan_t *platform_create_ble_scan(const board_config_t *cfg) {
    (void)cfg;
    // 仅登记句柄;实际扫描由 ble_config.c 的 on_sync → ble_scan_start 启动
    // (二者共用同一个 NimBLE host,host 已在 platform_create_ble_config 里初始化)。
    return &s_handle;
}
#endif // PERIPH_BLE
```

- [ ] **Step 3: 在 ble_config.c 的 on_sync 里启动扫描**

`components/platform/platform_esp32/src/ble_config.c` — 顶部 include 区加：
```c
#include "ble_internal.h"
```
把 `on_sync` 改为（在 `start_adv()` 之后加一行）：
```c
static void on_sync(void) {
    ble_hs_id_infer_auto(0, &s_addr_type);
    start_adv();
    ble_scan_start(s_addr_type);   // 广播启动后并行启动扫描(observer)
}
```

- [ ] **Step 4: 注册源文件到平台 CMake**

`components/platform/platform_esp32/CMakeLists.txt` — SRCS 里 `"src/ble_config.c"` 后加 `"src/ble_scan.c"`，并把 `services` 加入 `REQUIRES`（ble_scan.c 用到 `services/ble_match.h`）：
```cmake
idf_component_register(
    SRCS "src/disp_st7789.c" "src/lvgl_port_setup.c" "src/btn_iot_button.c"
         "src/led_ws2812.c" "src/audio_es8311.c" "src/platform_sys.c" "src/ble_config.c" "src/ble_scan.c"
    INCLUDE_DIRS "include"
    REQUIRES ports services driver esp_lcd
    PRIV_REQUIRES esp_lvgl_port led_strip button esp_codec_dev esp_hw_support bt nvs_flash
                  esp_driver_i2c esp_driver_i2s)
```

- [ ] **Step 5: 在工厂头声明 creator**

`components/platform/platform_esp32/include/platform/platform_factory.h` — include 区加 `#include "hal/hal_ble_scan.h"`；在 `platform_create_ble_config` 声明之后加：
```c
// BLE 扫描(observer):监听外部厂商广播,匹配到 → 回调。与 PERIPH_BLE 相关。
hal_ble_scan_t *platform_create_ble_scan(const board_config_t *cfg);
```

- [ ] **Step 6: Checkpoint** — 编译在 Task 6 统一验证（此处仅确保源与 CMake 一致）。

---

## Task 4: 爱心屏表现层 `ui_heart`

照抄 `ui_game` 的开/关/线程安全输入范式。爱心用 `lv_canvas` 按心形不等式逐像素填红；跳动用 `lv_anim` 对对象做缩放（放大再回弹）；内部 `lv_timer` 消费 pending 跳动并计空闲，满 5 秒自动退回。

> **实现时对照 LVGL 9.5 真实头文件所做的 API 修正（下方代码块为修正前草案，实际落地已改）：**
> 1. `lv_anim_set_time` → `lv_anim_set_duration`
> 2. `lv_anim_set_playback_time` → `lv_anim_set_reverse_duration`
> 3. 统一 `lv_obj_set_style_transform_scale`（9.5 不存在）→ 分别调 `lv_obj_set_style_transform_scale_x` + `..._scale_y`（同值），`scale_cb` 内设两轴
> 4. `lv_obj_clear_flag`（仅 v8 兼容宏）→ `lv_obj_remove_flag`（与 `ui_game.c` 一致）
>
> **硬件实测又发现并修复的重大问题（RAM）：** 原设计的 `static uint8_t s_canvas_buf[120*120*4]`=57.6KB 常驻 DRAM，在 ESP32-C3 上把堆压到 NimBLE 初始化所需之下 → `hci inits failed`/`nimble host init failed` → 在 `ble_gatts_count_cfg` 崩溃。**改法**：爱心位图改为 `static const` ARGB8888 放 Flash(.rodata，0 DRAM)，用 `lv_image`+`lv_image_dsc_t` 显示、`lv_image_set_scale` 跳动（`lv_canvas` 需可写缓冲只能占 RAM，故弃用）。位图由 `tools/gen_heart_bitmap.py` 生成 `ui_heart_bitmap.h`。另在 `ble_config.c` 加 `nimble_port_init()` 返回值检查作防御。

**Files:**
- Create: `components/ui/presentation/include/presentation/ui_heart.h`
- Create: `components/ui/presentation/src/ui_heart.c`
- Modify: `components/ui/presentation/CMakeLists.txt`

- [ ] **Step 1: 写接口头**

`components/ui/presentation/include/presentation/ui_heart.h`:
```c
// components/ui/presentation/include/presentation/ui_heart.h
#pragma once
#include <stdbool.h>

// 爱心跳动屏。除 ui_heart_beat() 外,涉及 LVGL 对象的函数须在 LVGL 任务上下文/持锁时调用。
typedef void (*ui_heart_exit_cb_t)(void *user);

// 建爱心屏并载入。记录当前屏为"上一屏",退出时载回。须持 LVGL 锁调用。
void ui_heart_open(ui_heart_exit_cb_t on_exit, void *user);

// 触发一次跳动。线程安全:仅置 pending 标志,由内部 lv_timer(LVGL 任务)消费。
// 可从 NimBLE host 任务调用。
void ui_heart_beat(void);

// 是否处于爱心屏(app 据此决定"开屏"还是"仅跳一下")。
bool ui_heart_is_active(void);
```

- [ ] **Step 2: 写实现**

`components/ui/presentation/src/ui_heart.c`:
```c
// components/ui/presentation/src/ui_heart.c —— 监听广播时显示的爱心跳动屏
#include "presentation/ui_heart.h"
#include "lvgl.h"

#define HEART_SZ   120                 // 爱心画布边长(px)
#define BG_COLOR   lv_color_hex(0x1C1C1C)
#define HEART_COL  lv_color_hex(0xFF3B5C)
#define TICK_MS    60                  // 心跳/空闲检查周期
#define IDLE_MS    5000                // 连续无匹配广播多久自动退回
#define BEAT_UP    120                 // 放大动画时长(ms)
#define BEAT_BACK  160                 // 回弹时长(ms)
#define SCALE_MIN  256                 // LVGL 缩放 256 = 100%
#define SCALE_MAX  320                 // 峰值约 1.25x

// 画布缓冲(ARGB8888)。静态分配,避免运行期大块 malloc。
static uint8_t s_canvas_buf[HEART_SZ * HEART_SZ * 4] __attribute__((aligned(4)));

static struct {
    bool          active;
    lv_obj_t     *scr, *prev_scr, *heart;
    lv_timer_t   *timer;
    volatile int  pending;             // ui_heart_beat 置 1,timer 消费(跨线程)
    bool          animating;           // 一次跳动进行中,避免过密重启
    int           idle_ms;
    ui_heart_exit_cb_t on_exit;
    void         *on_exit_user;
} H;

// 心形不等式:归一化坐标下 (x^2 + y^2 - 1)^3 - x^2 * y^3 <= 0 为心内。
static bool in_heart(int px, int py) {
    float x = (px - HEART_SZ / 2.0f) / (HEART_SZ * 0.42f);
    float y = (HEART_SZ / 2.0f - py) / (HEART_SZ * 0.42f) + 0.20f;   // 上移,让尖角居中偏下
    float a = x * x + y * y - 1.0f;
    return a * a * a - x * x * y * y * y <= 0.0f;
}

static void heart_render(void) {
    lv_canvas_fill_bg(H.heart, HEART_COL, LV_OPA_TRANSP);   // 清透明
    for (int py = 0; py < HEART_SZ; py++)
        for (int px = 0; px < HEART_SZ; px++)
            if (in_heart(px, py))
                lv_canvas_set_px(H.heart, px, py, HEART_COL, LV_OPA_COVER);
}

static void scale_cb(void *obj, int32_t v) {
    lv_obj_set_style_transform_scale((lv_obj_t *)obj, v, 0);
}
static void beat_done_cb(lv_anim_t *a) { (void)a; H.animating = false; }

static void beat_play(void) {
    if (H.animating) return;            // 上一跳未结束就丢弃,保持离散清晰的跳动
    H.animating = true;
    lv_anim_t a; lv_anim_init(&a);
    lv_anim_set_var(&a, H.heart);
    lv_anim_set_exec_cb(&a, scale_cb);
    lv_anim_set_values(&a, SCALE_MIN, SCALE_MAX);
    lv_anim_set_time(&a, BEAT_UP);
    lv_anim_set_playback_time(&a, BEAT_BACK);   // 自动回弹到 SCALE_MIN
    lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
    lv_anim_set_completed_cb(&a, beat_done_cb);
    lv_anim_start(&a);
}

static void teardown(void) {
    if (!H.active) return;
    if (H.timer) { lv_timer_delete(H.timer); H.timer = NULL; }
    if (H.prev_scr) lv_screen_load(H.prev_scr);
    if (H.scr) { lv_obj_delete(H.scr); H.scr = NULL; H.heart = NULL; }
    H.active = false;
    if (H.on_exit) H.on_exit(H.on_exit_user);
}

static void tick(lv_timer_t *t) {
    (void)t;
    if (H.pending) { H.pending = 0; H.idle_ms = 0; beat_play(); }
    else { H.idle_ms += TICK_MS; if (H.idle_ms >= IDLE_MS) teardown(); }
}

void ui_heart_open(ui_heart_exit_cb_t on_exit, void *user) {
    if (H.active) return;
    H.on_exit = on_exit; H.on_exit_user = user;
    H.prev_scr = lv_screen_active();
    H.pending = 0; H.animating = false; H.idle_ms = 0;

    H.scr = lv_obj_create(NULL);
    lv_obj_set_style_bg_color(H.scr, BG_COLOR, 0);
    lv_obj_set_style_border_width(H.scr, 0, 0);
    lv_obj_clear_flag(H.scr, LV_OBJ_FLAG_SCROLLABLE);

    H.heart = lv_canvas_create(H.scr);
    lv_canvas_set_buffer(H.heart, s_canvas_buf, HEART_SZ, HEART_SZ, LV_COLOR_FORMAT_ARGB8888);
    lv_obj_center(H.heart);
    // 缩放以画布中心为支点
    lv_obj_set_style_transform_pivot_x(H.heart, HEART_SZ / 2, 0);
    lv_obj_set_style_transform_pivot_y(H.heart, HEART_SZ / 2, 0);
    heart_render();

    lv_screen_load(H.scr);
    H.active = true;
    H.timer = lv_timer_create(tick, TICK_MS, NULL);
}

void ui_heart_beat(void) { H.pending = 1; }   // 线程安全:仅置标志

bool ui_heart_is_active(void) { return H.active; }
```

- [ ] **Step 3: 注册源到表现层 CMake**

`components/ui/presentation/CMakeLists.txt` — SRCS 加 `"src/ui_heart.c"`：
```cmake
idf_component_register(
    SRCS "src/ui_presenter.c" "src/ui_profile.c" "src/ui_game.c" "src/ui_heart.c"
    INCLUDE_DIRS "include"
    REQUIRES services
    PRIV_REQUIRES lvgl)
```

- [ ] **Step 4: Checkpoint** — 编译在 Task 6 统一验证。

---

## Task 5: app 接线（scan → heart）

**Files:**
- Modify: `components/app/src/app.c`

- [ ] **Step 1: include 爱心屏头**

`components/app/src/app.c` — 在 `#if PERIPH_DISPLAY` 的 include 块里（`ui_game.h` 附近）加：
```c
#include "presentation/ui_heart.h"
```

- [ ] **Step 2: 加匹配回调与句柄**

在 `#if PERIPH_BLE` 顶部区域（`s_profile` 定义附近、`on_cfg_write` 之前或之后）加。注意 `on_ble_match` 需要用到 `ui_heart_*`/`ui_game_*`，这些仅在 `PERIPH_DISPLAY` 下有意义，故整段用双重守卫：
```c
#if PERIPH_BLE && PERIPH_DISPLAY
static hal_ble_scan_t *s_scan;
static void on_heart_exit(void *user) { (void)user; }   // 爱心屏不占按键导航,无需恢复

// 扫描匹配回调(NimBLE host 任务上下文)。游戏中忽略;否则持 LVGL 锁,
// 首次进爱心屏,随后触发一次跳动。
static void on_ble_match(const uint8_t *data, int len, void *user) {
    (void)data; (void)len; (void)user;
    if (ui_game_is_active()) return;
    if (platform_lvgl_lock(0)) {
        if (!ui_heart_is_active()) ui_heart_open(on_heart_exit, NULL);
        ui_heart_beat();
        platform_lvgl_unlock();
    }
}
#endif
```

- [ ] **Step 3: 在 app_run 里创建并注册**

`components/app/src/app.c` 的 `app_run()` 中，现有 `#if PERIPH_BLE` 块末尾（`publish_export();` 之后）加：
```c
#if PERIPH_DISPLAY
    s_scan = platform_create_ble_scan(&board);
    hal_ble_scan_on_match(s_scan, on_ble_match, NULL);
#endif
```
（该 `#if PERIPH_DISPLAY` 嵌在外层 `#if PERIPH_BLE` 内，等效于双重守卫，与 Step 2 的 `s_scan`/回调守卫一致。）

- [ ] **Step 4: 确认依赖**

`components/app/CMakeLists.txt` 的 `REQUIRES` 已含 `ports services presentation platform_esp32`，`hal_ble_scan.h`（ports）与 `platform_create_ble_scan`（platform_esp32）均已覆盖，无需改。

- [ ] **Step 5: Checkpoint** — 编译在 Task 6 统一验证。

---

## Task 6: sdkconfig 核对 + 固件构建（主验证）

**Files:**
- 可能 Modify: `sdkconfig.defaults`（若 observer 角色未开）

- [ ] **Step 1: 核对 NimBLE observer 角色**

```bash
grep -iE "NIMBLE_ROLE_OBSERVER|NIMBLE_ROLE_CENTRAL|NIMBLE_ROLE_BROADCASTER|NIMBLE_ROLE_PERIPHERAL" sdkconfig
```
Expected：`CONFIG_BT_NIMBLE_ROLE_OBSERVER=y`（以及 PERIPHERAL/BROADCASTER=y）。
若 `OBSERVER` 为 `is not set`，在 `sdkconfig.defaults` 追加：
```
CONFIG_BT_NIMBLE_ROLE_OBSERVER=y
```
然后删 `sdkconfig` 让其重生成（`rm sdkconfig`），或在 `idf.py menuconfig` 里 Component config → Bluetooth → NimBLE Options → 勾选 Observer role。

- [ ] **Step 2: 全量构建固件**（已激活 IDF 的 shell）

```bash
idf.py build
```
Expected：`Project build complete.`，无编译/链接错误。重点确认新增源 `ble_match.c`/`ble_scan.c`/`ui_heart.c` 均被编译、`platform_create_ble_scan` 与 `ble_scan_start` 链接通过。

- [ ] **Step 3: Checkpoint** — 构建通过即视为集成完成，进入硬件联调。

---

## Task 7: 硬件联调验证

**Files:** 无（人工验证）

- [ ] **Step 1: 烧录并监看**

```bash
idf.py -p <PORT> flash monitor
```
Expected：正常开机进主界面（个人名片/dock），日志无 `ble_gap_disc failed`。

- [ ] **Step 2: 用手机发匹配广播**

手机装 nRF Connect → Advertiser → 新建广播包 → 添加 Manufacturer data：Company ID `0xFFFF`，附加数据 `4842`（整体厂商数据 = `FFFF4842`）→ 开始广播。
Expected：设备界面切到爱心屏，爱心持续跳动（约每 100–300ms 一跳）。

- [ ] **Step 3: 停止广播观察超时退回**

停止 nRF Connect 广播。
Expected：约 5 秒后设备自动退回主界面。

- [ ] **Step 4: 共存性验证（可选但推荐）**

一边保持手机 nRF Connect **连接**设备（连上 GATT、可写配置字段），一边用另一台设备/另一广播源发 `FFFF4842`。
Expected：连接不中断、配置仍可写，同时爱心屏能进入并跳动（期间可能偶有漏包导致跳动略稀疏，属正常时分复用）。

- [ ] **Step 5: 游戏态不打断验证**

进入三线跑酷小游戏，期间发 `FFFF4842`。
Expected：游戏不被打断（`on_ble_match` 在 `ui_game_is_active()` 时直接返回）。

---

## Self-Review（作者自检）

- **Spec 覆盖**：识别方式(FFFF4842)→Task1；观察者扫描/关去重/共存占空比→Task3+Task6.Step1；爱心屏+跳动动画→Task4；进入/游戏态忽略/超时退回→Task4(timer)+Task5(接线)；发送端参考→Task7.Step2；测试→Task1(单测)+Task7(硬件)。均有对应任务。
- **占位符**：无 TBD/TODO，所有代码步骤含完整代码。
- **类型/命名一致性**：`ble_match_is_heartbeat`(Task1 定义/Task3 调用)、`ble_scan_start`(ble_internal.h 定义/ble_config on_sync 调用/ble_scan.c 实现)、`platform_create_ble_scan`(factory 声明/ble_scan.c 实现/app 调用)、`hal_ble_scan_on_match`/`ble_match_cb_t`(端口定义/app 使用)、`ui_heart_open/beat/is_active`(头定义/impl/ app 调用) 全一致。
- **无 git**：全程以构建/硬件验证替代提交，已在环境约定中说明。
