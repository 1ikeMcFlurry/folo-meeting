# BLE 控制档案卡信息(阶段一)—— 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用手机通用 BLE 工具(nRF Connect)写 GATT 特征,实时修改档案卡的名字/角色/副标题/电量/等级/经验/在线。

**Architecture:** 端口 `hal/hal_config.h` 定义字段枚举 + 写回调;`core/services/profile_ctl` 纯逻辑做文本解析/校验(可 PC 单测);`platform_esp32/ble_config.c` 用 NimBLE 起 GATT 服务实现该端口;`app` 注册回调,持 LVGL 锁调 `ui_profile_set_*`。

**Tech Stack:** C99、ESP-IDF 5.5.2、NimBLE、LVGL 9。

**本仓库特殊约定(务必先读):**
- 本项目**不是 git 仓库**,"提交"用**检查点**代替(不执行 `git`)。
- `host_test`(linux)因缺 `libbsd` 暂不可构建;纯逻辑模块 `profile_ctl` 的 TDD **用 gcc 独立编译验证**(零 IDF 依赖)。Unity 测试仍加入仓库以符合约定。
- 整机集成用 `idf.py build`(target 已是 esp32c3)。跑 `idf.py` 前先 `source "$IDF_PATH/export.sh"`;**务必在项目根 `/home/cjiio/trae_card` 下执行**。
- scratchpad:`/tmp/claude-1000/-home-cjiio-trae-card/82e5d0c8-4331-4280-9c6f-510076c1242a/scratchpad`

---

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `components/core/ports/include/hal/hal_config.h` | 新增。`cfg_field_t` 枚举 + `hal_config` 端口(写回调) |
| `components/core/services/include/services/profile_ctl.h` | 新增。`profile_data_t` + `profile_ctl_apply` |
| `components/core/services/src/profile_ctl.c` | 新增。文本解析/校验/裁剪 |
| `components/core/services/CMakeLists.txt` | 加 `src/profile_ctl.c` |
| `components/core/services/test/test_profile_ctl.c` | 新增 Unity 测试 |
| `components/core/services/test/CMakeLists.txt` + `host_test/main/CMakeLists.txt` | 挂测试源 |
| `components/platform/platform_esp32/include/platform/board_config.h` | 加 `PERIPH_BLE` / `BLE_DEVICE_NAME` |
| `components/platform/platform_esp32/include/platform/platform_factory.h` | 加 `platform_create_ble_config` |
| `components/platform/platform_esp32/src/ble_config.c` | 新增。NimBLE GATT 服务 |
| `components/platform/platform_esp32/CMakeLists.txt` | 加 `ble_config.c` + `PRIV_REQUIRES bt` |
| `components/app/src/app.c` | 创建 ble_config + 注册回调 + 粘合 |
| `sdkconfig.defaults` + `sdkconfig` | 启用 NimBLE |

---

## Task 1: 端口 `hal_config` + 业务 `profile_ctl`(gcc TDD)

**Files:**
- Create: `components/core/ports/include/hal/hal_config.h`
- Create: `components/core/services/include/services/profile_ctl.h`
- Create: `components/core/services/src/profile_ctl.c`
- Temp test: `.../scratchpad/pc_main.c`

- [ ] **Step 1: 写端口头 `hal_config.h`**

Create `components/core/ports/include/hal/hal_config.h`:
```c
// components/core/ports/include/hal/hal_config.h
#pragma once

// 可被外部配置源(BLE)写入的字段
typedef enum {
    CFG_NAME = 0,
    CFG_ROLE,
    CFG_SUBTITLE,
    CFG_BATTERY,
    CFG_LEVEL,
    CFG_XP,
    CFG_XP_MAX,
    CFG_ONLINE,
    CFG_FIELD_COUNT
} cfg_field_t;

// 一次写入:字段 + 原始字节(UTF-8 文本,可能无 NUL 结尾)+ 长度。
typedef void (*cfg_write_cb_t)(cfg_field_t field, const char *text, int len, void *user);

typedef struct hal_config_s hal_config_t;
typedef struct {
    void (*on_write)(hal_config_t *self, cfg_write_cb_t cb, void *user);
} hal_config_api_t;
struct hal_config_s { const hal_config_api_t *api; void *impl; };

static inline void hal_config_on_write(hal_config_t *c, cfg_write_cb_t cb, void *user) {
    c->api->on_write(c, cb, user);
}
```

- [ ] **Step 2: 写业务头 `profile_ctl.h`**

Create `components/core/services/include/services/profile_ctl.h`:
```c
// components/core/services/include/services/profile_ctl.h
#pragma once
#include <stdbool.h>
#include "hal/hal_config.h"

typedef struct {
    char name[24];       // 23 + NUL
    char role[20];
    char subtitle[28];
    int  battery;        // 0..100
    int  level;          // 0..999
    int  xp;             // >=0
    int  xp_max;         // >=1
    bool online;
} profile_data_t;

void profile_ctl_init(profile_data_t *d);   // 默认值(与界面初始一致)

// 应用一次字段写入(解析/校验/裁剪)。成功返回被改字段(cfg_field_t);非法返回 -1。
int  profile_ctl_apply(profile_data_t *d, cfg_field_t field, const char *text, int len);
```

- [ ] **Step 3: 写临时 gcc 测试 `pc_main.c`**

Create `/tmp/claude-1000/-home-cjiio-trae-card/82e5d0c8-4331-4280-9c6f-510076c1242a/scratchpad/pc_main.c`:
```c
#include "services/profile_ctl.h"
#include <assert.h>
#include <string.h>
#include <stdio.h>
#define APPLY(f,str) profile_ctl_apply(&d,(f),(str),(int)strlen(str))
int main(void){
    profile_data_t d; profile_ctl_init(&d);
    assert(APPLY(CFG_BATTERY,"82")==CFG_BATTERY && d.battery==82);
    assert(APPLY(CFG_BATTERY,"300")==CFG_BATTERY && d.battery==100);
    assert(APPLY(CFG_BATTERY,"-5")==CFG_BATTERY && d.battery==0);
    assert(APPLY(CFG_BATTERY,"abc")==-1);
    assert(APPLY(CFG_XP_MAX,"0")==CFG_XP_MAX && d.xp_max==1);
    assert(APPLY(CFG_LEVEL,"7")==CFG_LEVEL && d.level==7);
    assert(APPLY(CFG_ONLINE,"on")==CFG_ONLINE && d.online==true);
    assert(APPLY(CFG_ONLINE,"0")==CFG_ONLINE && d.online==false);
    assert(APPLY(CFG_ONLINE,"x")==-1);
    const char *ln="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
    assert(APPLY(CFG_NAME,ln)==CFG_NAME); assert(strlen(d.name)==23);
    assert(APPLY(CFG_ROLE,"HACKER")==CFG_ROLE && strcmp(d.role,"HACKER")==0);
    printf("all profile_ctl tests passed\n");
    return 0;
}
```

- [ ] **Step 4: 运行确认失败(未实现)**

Run:
```bash
cd /home/cjiio/trae_card && gcc /tmp/claude-1000/-home-cjiio-trae-card/82e5d0c8-4331-4280-9c6f-510076c1242a/scratchpad/pc_main.c \
  -I components/core/services/include -I components/core/ports/include \
  -o /tmp/claude-1000/-home-cjiio-trae-card/82e5d0c8-4331-4280-9c6f-510076c1242a/scratchpad/pc 2>&1 | head
```
Expected: 链接错误 `undefined reference to 'profile_ctl_init'/'profile_ctl_apply'`。

- [ ] **Step 5: 实现 `profile_ctl.c`**

Create `components/core/services/src/profile_ctl.c`:
```c
// components/core/services/src/profile_ctl.c —— 文本字段解析/校验/裁剪(纯逻辑)
#include "services/profile_ctl.h"
#include <string.h>

static void copy_trunc(char *dst, int cap, const char *src, int len) {
    if (len < 0) len = 0;
    if (len > cap - 1) len = cap - 1;
    memcpy(dst, src, (size_t)len);
    dst[len] = '\0';
}
// 十进制解析(可带 +/-,允许前导空格)。成功置 *out 返回 1,否则返回 0。
static int parse_int(const char *s, int len, int *out) {
    int i = 0, sign = 1, any = 0;
    long v = 0;
    while (i < len && s[i] == ' ') i++;
    if (i < len && (s[i] == '+' || s[i] == '-')) { if (s[i] == '-') sign = -1; i++; }
    for (; i < len; i++) {
        if (s[i] < '0' || s[i] > '9') return 0;
        v = v * 10 + (s[i] - '0'); any = 1;
        if (v > 1000000000L) v = 1000000000L;
    }
    if (!any) return 0;
    *out = (int)(sign * v);
    return 1;
}
static int clampi(int v, int lo, int hi) { return v < lo ? lo : (v > hi ? hi : v); }

static int parse_bool(const char *s, int len, bool *out) {
    if (len == 1 && s[0] == '1') { *out = true;  return 1; }
    if (len == 1 && s[0] == '0') { *out = false; return 1; }
    char b[8];
    if (len < 0 || len > 7) return 0;
    for (int i = 0; i < len; i++) { char c = s[i]; if (c >= 'A' && c <= 'Z') c += 32; b[i] = c; }
    b[len] = '\0';
    if (strcmp(b, "on") == 0 || strcmp(b, "true") == 0)  { *out = true;  return 1; }
    if (strcmp(b, "off") == 0 || strcmp(b, "false") == 0) { *out = false; return 1; }
    return 0;
}

void profile_ctl_init(profile_data_t *d) {
    memset(d, 0, sizeof(*d));
    copy_trunc(d->name,     sizeof d->name,     "FoloToy", 7);
    copy_trunc(d->role,     sizeof d->role,     "NETRUNNER", 9);
    copy_trunc(d->subtitle, sizeof d->subtitle, "DATA STALKER", 12);
    d->battery = 82; d->level = 1; d->xp = 0; d->xp_max = 100; d->online = true;
}

int profile_ctl_apply(profile_data_t *d, cfg_field_t field, const char *text, int len) {
    int v; bool b;
    switch (field) {
        case CFG_NAME:     copy_trunc(d->name,     sizeof d->name,     text, len); return CFG_NAME;
        case CFG_ROLE:     copy_trunc(d->role,     sizeof d->role,     text, len); return CFG_ROLE;
        case CFG_SUBTITLE: copy_trunc(d->subtitle, sizeof d->subtitle, text, len); return CFG_SUBTITLE;
        case CFG_BATTERY:  if (!parse_int(text, len, &v)) return -1; d->battery = clampi(v, 0, 100); return CFG_BATTERY;
        case CFG_LEVEL:    if (!parse_int(text, len, &v)) return -1; d->level   = clampi(v, 0, 999); return CFG_LEVEL;
        case CFG_XP:       if (!parse_int(text, len, &v)) return -1; d->xp      = (v < 0 ? 0 : v);   return CFG_XP;
        case CFG_XP_MAX:   if (!parse_int(text, len, &v)) return -1; d->xp_max  = (v < 1 ? 1 : v);   return CFG_XP_MAX;
        case CFG_ONLINE:   if (!parse_bool(text, len, &b)) return -1; d->online = b;                 return CFG_ONLINE;
        default: return -1;
    }
}
```

- [ ] **Step 6: 运行确认通过**

Run:
```bash
cd /home/cjiio/trae_card && gcc /tmp/claude-1000/-home-cjiio-trae-card/82e5d0c8-4331-4280-9c6f-510076c1242a/scratchpad/pc_main.c \
  components/core/services/src/profile_ctl.c \
  -I components/core/services/include -I components/core/ports/include -std=c99 -Wall -Wextra \
  -o /tmp/claude-1000/-home-cjiio-trae-card/82e5d0c8-4331-4280-9c6f-510076c1242a/scratchpad/pc \
  && /tmp/claude-1000/-home-cjiio-trae-card/82e5d0c8-4331-4280-9c6f-510076c1242a/scratchpad/pc
```
Expected: 零警告,输出 `all profile_ctl tests passed`,退出码 0。清理:`rm -f .../scratchpad/pc`。

- [ ] **Step 7: 检查点** —— 三个源文件就位、gcc 测试通过。

---

## Task 2: 编入 services + Unity 测试

**Files:**
- Modify: `components/core/services/CMakeLists.txt`
- Create: `components/core/services/test/test_profile_ctl.c`
- Modify: `components/core/services/test/CMakeLists.txt`
- Modify: `host_test/main/CMakeLists.txt`

- [ ] **Step 1: services CMake 加源**

Read `components/core/services/CMakeLists.txt`,在 `SRCS` 末尾追加 `"src/profile_ctl.c"`(保留现有 `led_service.c`/`input_service.c`/`audio_service.c`/`ui_model.c`/`avatar.c`)。结果 `SRCS` 应含全部六个 + `profile_ctl.c`。

- [ ] **Step 2: 写 Unity 测试**

Create `components/core/services/test/test_profile_ctl.c`:
```c
#include "unity.h"
#include "services/profile_ctl.h"
#include <string.h>

TEST_CASE("battery clamps to 0..100", "[profile_ctl]") {
    profile_data_t d; profile_ctl_init(&d);
    TEST_ASSERT_EQUAL_INT(CFG_BATTERY, profile_ctl_apply(&d, CFG_BATTERY, "300", 3));
    TEST_ASSERT_EQUAL_INT(100, d.battery);
    TEST_ASSERT_EQUAL_INT(CFG_BATTERY, profile_ctl_apply(&d, CFG_BATTERY, "-5", 2));
    TEST_ASSERT_EQUAL_INT(0, d.battery);
}
TEST_CASE("non-numeric is rejected", "[profile_ctl]") {
    profile_data_t d; profile_ctl_init(&d);
    TEST_ASSERT_EQUAL_INT(-1, profile_ctl_apply(&d, CFG_BATTERY, "abc", 3));
}
TEST_CASE("xp_max floors at 1", "[profile_ctl]") {
    profile_data_t d; profile_ctl_init(&d);
    profile_ctl_apply(&d, CFG_XP_MAX, "0", 1);
    TEST_ASSERT_EQUAL_INT(1, d.xp_max);
}
TEST_CASE("online parses on/off", "[profile_ctl]") {
    profile_data_t d; profile_ctl_init(&d);
    profile_ctl_apply(&d, CFG_ONLINE, "on", 2);  TEST_ASSERT_TRUE(d.online);
    profile_ctl_apply(&d, CFG_ONLINE, "0", 1);   TEST_ASSERT_FALSE(d.online);
    TEST_ASSERT_EQUAL_INT(-1, profile_ctl_apply(&d, CFG_ONLINE, "x", 1));
}
TEST_CASE("name truncates to 23", "[profile_ctl]") {
    profile_data_t d; profile_ctl_init(&d);
    const char *ln = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
    profile_ctl_apply(&d, CFG_NAME, ln, (int)strlen(ln));
    TEST_ASSERT_EQUAL_UINT(23, (unsigned)strlen(d.name));
}
```

- [ ] **Step 3: 挂测试源到两处 CMake**

`components/core/services/test/CMakeLists.txt`:在 `SRCS` 插入 `"test_profile_ctl.c"`(保留 `test_led_service.c`/`test_input_service.c`/`test_avatar.c`/`mocks/mock_hal.c`)。

`host_test/main/CMakeLists.txt`:在 `${TEST_DIR}/...` 组里加一行 `"${TEST_DIR}/test_profile_ctl.c"`(先 Read,保留 `set(TEST_DIR ...)` 与 `WHOLE_ARCHIVE` 行)。

- [ ] **Step 4: 检查点** —— Task 6 整机构建会编译 `profile_ctl.c`。

---

## Task 3: 启用 NimBLE + 外设开关

**Files:**
- Modify: `components/platform/platform_esp32/include/platform/board_config.h`
- Modify: `sdkconfig.defaults`
- Modify: `sdkconfig`

- [ ] **Step 1: board_config 加开关与设备名**

在 `board_config.h` 的外设开关区(`PERIPH_DISPLAY` 等旁)加:
```c
#define PERIPH_BLE      1   // BLE 档案卡控制
```
并在文件内(靠近其它宏)加设备名:
```c
#define BLE_DEVICE_NAME "TRAE-CARD"
```

- [ ] **Step 2: sdkconfig.defaults 追加 NimBLE(可复现)**

在 `sdkconfig.defaults` 末尾追加:
```
CONFIG_BT_ENABLED=y
CONFIG_BT_NIMBLE_ENABLED=y
```

- [ ] **Step 3: 直接写入现有 sdkconfig(关键!)**

> ESP-IDF 只在**首次生成** `sdkconfig` 时套用 `sdkconfig.defaults`;仓库已有 `sdkconfig`,故须直接改它。

把 `# CONFIG_BT_ENABLED is not set` 一行替换为 `CONFIG_BT_ENABLED=y`,并在其后追加 `CONFIG_BT_NIMBLE_ENABLED=y`。用命令完成:
```bash
cd /home/cjiio/trae_card
sed -i 's/^# CONFIG_BT_ENABLED is not set$/CONFIG_BT_ENABLED=y\nCONFIG_BT_NIMBLE_ENABLED=y/' sdkconfig
grep -n "CONFIG_BT_ENABLED\|CONFIG_BT_NIMBLE_ENABLED" sdkconfig
```
Expected: 两行分别为 `CONFIG_BT_ENABLED=y` 和 `CONFIG_BT_NIMBLE_ENABLED=y`。(build 时 Kconfig 会据此补齐其余 NimBLE 默认项。)

- [ ] **Step 4: 检查点** —— 真正生效在 Task 6 构建后再核对 `sdkconfig` 里 `CONFIG_BT_NIMBLE_ENABLED=y`。

---

## Task 4: 平台适配器 `ble_config.c`(NimBLE GATT)

**Files:**
- Modify: `components/platform/platform_esp32/include/platform/platform_factory.h`
- Create: `components/platform/platform_esp32/src/ble_config.c`
- Modify: `components/platform/platform_esp32/CMakeLists.txt`

- [ ] **Step 1: platform_factory 声明工厂**

在 `platform_factory.h` 顶部 include 区加 `#include "hal/hal_config.h"`;在文件末尾加:
```c
// BLE 档案卡控制:启动 NimBLE + GATT 服务,返回 hal_config 端口(与外设开关 PERIPH_BLE 相关)。
hal_config_t *platform_create_ble_config(const board_config_t *cfg);
```

- [ ] **Step 2: 写 `ble_config.c`**

Create `components/platform/platform_esp32/src/ble_config.c`:
```c
// components/platform_esp32/src/ble_config.c —— NimBLE GATT:8 个可写特征 → hal_config 回调
#include "platform/board_config.h"
#if PERIPH_BLE
#include "platform/platform_factory.h"
#include "hal/hal_config.h"
#include <string.h>
#include "esp_log.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "host/ble_hs.h"
#include "host/util/util.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"

static const char *TAG = "ble_config";

// UUID 基址:ASCII "TRAECARD" + 末字节字段号。NimBLE 用小端字节序存储。
#define TRAE_UUID128(nn) BLE_UUID128_INIT( \
    (nn),0x00,0x00,0x00,0x00,0x00,0x00,0x00, 0x44,0x52,0x41,0x43,0x45,0x41,0x52,0x54)

static const ble_uuid128_t g_svc_uuid = TRAE_UUID128(0x00);
static const ble_uuid128_t g_chr_uuid[CFG_FIELD_COUNT] = {
    TRAE_UUID128(0x01), TRAE_UUID128(0x02), TRAE_UUID128(0x03), TRAE_UUID128(0x04),
    TRAE_UUID128(0x05), TRAE_UUID128(0x06), TRAE_UUID128(0x07), TRAE_UUID128(0x08),
};
static const char *g_chr_name[CFG_FIELD_COUNT] = {
    "Name", "Role", "Subtitle", "Battery", "Level", "XP", "XP_Max", "Online",
};

static cfg_write_cb_t s_cb;
static void          *s_user;
static uint8_t        s_addr_type;

static int gap_event(struct ble_gap_event *event, void *arg);   // 前置声明(start_adv 用)

// 特征写:把 mbuf 拉平成文本,回调给上层。
static int chr_write(uint16_t conn, uint16_t attr, struct ble_gatt_access_ctxt *ctxt, void *arg) {
    (void)conn; (void)attr;
    if (ctxt->op != BLE_GATT_ACCESS_OP_WRITE_CHR) return BLE_ATT_ERR_UNLIKELY;
    char buf[64];
    uint16_t out = 0;
    uint16_t avail = OS_MBUF_PKTLEN(ctxt->om);
    if (avail > (uint16_t)(sizeof(buf) - 1)) avail = sizeof(buf) - 1;
    if (ble_hs_mbuf_to_flat(ctxt->om, buf, avail, &out) != 0) return BLE_ATT_ERR_UNLIKELY;
    if (s_cb) s_cb((cfg_field_t)(intptr_t)arg, buf, (int)out, s_user);
    return 0;
}
// 特征用户描述(0x2901):返回友好名。
static int cud_read(uint16_t conn, uint16_t attr, struct ble_gatt_access_ctxt *ctxt, void *arg) {
    (void)conn; (void)attr;
    const char *name = (const char *)arg;
    return os_mbuf_append(ctxt->om, name, strlen(name)) == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
}

// 每字段一个特征 + 一个 CUD 描述符。
static struct ble_gatt_dsc_def g_dscs[CFG_FIELD_COUNT][2];
static struct ble_gatt_chr_def g_chrs[CFG_FIELD_COUNT + 1];
static struct ble_gatt_svc_def g_svcs[2];

static void build_gatt_table(void) {
    for (int i = 0; i < CFG_FIELD_COUNT; i++) {
        g_dscs[i][0].uuid = BLE_UUID16_DECLARE(0x2901);
        g_dscs[i][0].att_flags = BLE_ATT_F_READ;
        g_dscs[i][0].access_cb = cud_read;
        g_dscs[i][0].arg = (void *)g_chr_name[i];
        memset(&g_dscs[i][1], 0, sizeof(g_dscs[i][1]));   // 结束哨兵

        g_chrs[i].uuid = &g_chr_uuid[i].u;
        g_chrs[i].access_cb = chr_write;
        g_chrs[i].arg = (void *)(intptr_t)i;
        g_chrs[i].flags = BLE_GATT_CHR_F_WRITE;
        g_chrs[i].descriptors = g_dscs[i];
    }
    memset(&g_chrs[CFG_FIELD_COUNT], 0, sizeof(g_chrs[CFG_FIELD_COUNT]));  // 结束哨兵

    g_svcs[0].type = BLE_GATT_SVC_TYPE_PRIMARY;
    g_svcs[0].uuid = &g_svc_uuid.u;
    g_svcs[0].characteristics = g_chrs;
    memset(&g_svcs[1], 0, sizeof(g_svcs[1]));
}

static void start_adv(void) {
    struct ble_gap_adv_params advp;
    struct ble_hs_adv_fields f;
    memset(&advp, 0, sizeof(advp));
    memset(&f, 0, sizeof(f));
    advp.conn_mode = BLE_GAP_CONN_MODE_UND;
    advp.disc_mode = BLE_GAP_DISC_MODE_GEN;
    f.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    f.name = (uint8_t *)BLE_DEVICE_NAME;
    f.name_len = strlen(BLE_DEVICE_NAME);
    f.name_is_complete = 1;
    ble_gap_adv_set_fields(&f);
    ble_gap_adv_start(s_addr_type, NULL, BLE_HS_FOREVER, &advp, gap_event, NULL);
}

static int gap_event(struct ble_gap_event *event, void *arg) {
    (void)arg;
    if (event->type == BLE_GAP_EVENT_DISCONNECT || event->type == BLE_GAP_EVENT_ADV_COMPLETE) {
        start_adv();
    }
    return 0;
}

static void on_sync(void) {
    ble_hs_id_infer_auto(0, &s_addr_type);
    start_adv();
}
static void on_reset(int reason) { ESP_LOGW(TAG, "nimble reset, reason=%d", reason); }

static void host_task(void *param) {
    (void)param;
    nimble_port_run();
    nimble_port_freertos_deinit();
}

static void cfg_on_write(hal_config_t *self, cfg_write_cb_t cb, void *user) {
    (void)self; s_cb = cb; s_user = user;
}
static const hal_config_api_t API = { .on_write = cfg_on_write };
static hal_config_t s_handle = { .api = &API, .impl = NULL };

hal_config_t *platform_create_ble_config(const board_config_t *cfg) {
    (void)cfg;
    nimble_port_init();
    ble_hs_cfg.sync_cb = on_sync;
    ble_hs_cfg.reset_cb = on_reset;
    ble_svc_gap_init();
    ble_svc_gatt_init();
    build_gatt_table();
    ble_gatts_count_cfg(g_svcs);
    ble_gatts_add_svcs(g_svcs);
    ble_svc_gap_device_name_set(BLE_DEVICE_NAME);
    nimble_port_freertos_init(host_task);
    return &s_handle;
}
#endif // PERIPH_BLE
```

- [ ] **Step 3: platform CMake 加源与依赖**

`components/platform/platform_esp32/CMakeLists.txt`:`SRCS` 末尾加 `"src/ble_config.c"`;`PRIV_REQUIRES` 末尾加 `bt`。

- [ ] **Step 4: 检查点** —— 编译在 Task 6 验证(NimBLE 头需 `bt` 组件 + Task 3 的 sdkconfig)。

---

## Task 5: 组装层粘合 `app.c`

**Files:**
- Modify: `components/app/src/app.c`

- [ ] **Step 1: include + 回调 + 注册**

在 `app.c` 顶部 include 区(`#if PERIPH_DISPLAY ... #endif` 之后)加:
```c
#if PERIPH_BLE
#include "services/profile_ctl.h"
#endif
```
(端口 `hal/hal_config.h` 已被 `platform/platform_factory.h` 间接包含。)

在文件里(`app_run` 之前、其它 `#if PERIPH_BUTTON` 回调附近)加回调:
```c
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
```

- [ ] **Step 2: 在 app_run 里创建并注册**

在 `app_run()` 中,UI(`#if PERIPH_DISPLAY` 块)之后加:
```c
#if PERIPH_BLE
    profile_ctl_init(&s_profile);
    hal_config_t *cfg_src = platform_create_ble_config(&board);
    hal_config_on_write(cfg_src, on_cfg_write, NULL);
#endif
```

- [ ] **Step 3: 检查点** —— 逻辑齐了,Task 6 构建验证。

---

## Task 6: 整机构建 + 真机说明

- [ ] **Step 1: 全量构建**

Run:
```bash
cd /home/cjiio/trae_card && source "$IDF_PATH/export.sh" >/dev/null 2>&1 && idf.py build 2>&1 | tail -25
```
Expected: `Project build complete`,无 error/warning。NimBLE(`bt`)、`ble_config.c`、`profile_ctl.c`、`app.c` 均编过并链接。

- [ ] **Step 2: 确认 BLE 已启用**

Run:
```bash
cd /home/cjiio/trae_card && grep -n "CONFIG_BT_NIMBLE_ENABLED=y" sdkconfig && \
  find build -name "ble_config.c.obj" -o -name "profile_ctl.c.obj" 2>/dev/null
```
Expected: 见到 `CONFIG_BT_NIMBLE_ENABLED=y` 与两个 `.obj`。若 `sdkconfig` 未含该行:重新执行 Task 3 Step 3 的 `sed`(确保 `CONFIG_BT_ENABLED=y` 已写入 `sdkconfig`),再重跑 Step 1 构建。

- [ ] **Step 3: 若构建失败(NimBLE API)**

不同 IDF 小版本 NimBLE API 名称可能微调。常见修正:
- `nimble_port_init()` 若返回 `esp_err_t` 未用 → 忽略或 `(void)`。
- 头文件路径以 `bt` 组件实际为准(`host/ble_hs.h` 等标准)。
- 若 `ble_gatts_count_cfg`/`ble_gatts_add_svcs` 名称报错,确认 include 了 `host/ble_hs.h`。
修正后重跑 Step 1。

- [ ] **Step 4: 真机验证(手动,需硬件)**

`idf.py -p <串口> flash monitor` → 手机 nRF Connect 扫描连接 `TRAE-CARD` → 找到自定义服务(UUID 以 `...52 54` 结尾)→ 对各特征写文本:`Name`="NEO"、`Battery`="55"、`Level`="9"、`Online`="0" → 看档案卡实时更新。

- [ ] **Step 5: 最终检查点** —— 构建通过即完成;真机交互为手动验收。

---

## 附:验收对照(spec → 计划)

| spec 要求 | 对应任务 |
| --- | --- |
| 端口 `hal_config`(字段枚举+回调) | Task 1 Step 1 |
| `profile_ctl` 解析/校验/裁剪 + 单测 | Task 1 / Task 2 |
| NimBLE GATT 8 可写特征 + CUD + 重广播 | Task 4 |
| UUID "TRAECARD" 方案 | Task 4 Step 2 |
| `PERIPH_BLE` 开关 + 设备名 | Task 3 Step 1 |
| 启用 NimBLE(defaults + 现有 sdkconfig) | Task 3 Step 2/3 |
| app 粘合(校验→锁→ui_profile) | Task 5 |
| 整机构建验证 | Task 6 |
