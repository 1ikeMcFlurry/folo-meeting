# BLE 结构化配置通道 实现计划（Spec A）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 BLE 配置从 8 个可写文本特征重构为「一条命令写特征 + notify 状态特征」的结构化 JSON 通道，支持分包重组、部分更新、头像 seed 下发、积分余额显示、NVS 持久化。

**Architecture:** 沿用现有六边形分层。纯逻辑（帧重组 `frame_reasm`、JSON 部分更新 `config_json`、profile 序列化）放 `core/services` 可单测；存储抽象 `hal_kv` 端口 + `kv_nvs` 平台实现；`ble_config.c` 做 NimBLE 胶水（新特征 + 重组状态机 + notify）；`app.c` 组合根接线。传输帧头 `[ver][type][len]` 中的 `type` 为 A/B/C 三阶段共用地基。

**Tech Stack:** ESP-IDF v5.5.3、ESP32-C3、NimBLE、cJSON（随 IDF，组件名 `json`）、NVS、LVGL 9.5、Unity。

**环境约定（贯穿全计划）：**
- 本仓库**非 git 仓库**：所有「Commit」步骤改为**跳过 git**，用 `idf.py build` 编译通过作为该任务完成判据。
- **host_test 环境已知损坏**（见记忆 `host-test-idf-env`）：Unity 测试文件照写并挂到 `host_test`，但**执行验证以 `idf.py build` 固件编译 + 硬件联调为准**。每个「run test」步骤给出理想命令，同时注明当前以固件 build 为回退验证。
- 无法在本会话内 `idf.py build`（IDF 非交互 shell 未导出）：编译/硬件验证由用户在激活的 IDF shell 执行。

---

## 文件结构（先锁定职责）

| 文件 | 责任 | 动作 |
|---|---|---|
| `components/core/services/include/services/profile_ctl.h` | profile 数据结构 + 默认值 + 序列化声明；新增 `avatar_seed`/`token` | 修改 |
| `components/core/services/src/profile_ctl.c` | 默认值 + 定长 blob 序列化/反序列化；移除依赖 `cfg_field_t` 的旧 `apply` | 修改 |
| `components/core/services/include/services/config_json.h` | JSON 部分更新接口 + `cfg_changed_t` | 新增 |
| `components/core/services/src/config_json.c` | cJSON 解析整条 JSON，逐键校验/裁剪，回填 `profile_data_t`，输出改动标记 | 新增 |
| `components/core/services/include/services/frame_reasm.h` | 分帧重组状态机接口 | 新增 |
| `components/core/services/src/frame_reasm.c` | 纯状态机：喂字节 → 凑头 → 按 len 收齐 → 输出 type/payload | 新增 |
| `components/core/ports/include/hal/hal_kv.h` | key-value 存储端口 | 新增 |
| `components/core/ports/include/hal/hal_config.h` | 改为消息回调 `cfg_msg_cb_t` + `notify_status`；移除 `cfg_field_t` | 修改 |
| `components/platform/platform_esp32/src/kv_nvs.c` | NVS 实现 `hal_kv` + 工厂 | 新增 |
| `components/platform/platform_esp32/src/ble_config.c` | 新命令写/notify 特征、重组、conn_handle、移除 8 特征 | 修改 |
| `components/platform/platform_esp32/include/platform/platform_factory.h` | 加 `platform_create_kv` 声明 + `#include "hal/hal_kv.h"` | 修改 |
| `components/ui/presentation/include/presentation/ui_profile.h` | 加 `ui_profile_set_token` | 修改 |
| `components/ui/presentation/src/ui_profile.c` | 加积分余额 label；dock 移除 AVATAR 格改 4 格布局 | 修改 |
| `components/app/src/app.c` | 加载/持久化 profile、消息回调、notify、移除 dock 换头像 | 修改 |
| 各 `CMakeLists.txt` / `test/CMakeLists.txt` / `host_test/main/CMakeLists.txt` | 注册新源文件、加 `json` 依赖 | 修改 |

依赖顺序：Task1（profile 数据）→ Task2（config_json）、Task3（frame_reasm）、Task4（hal_kv）可并行 → Task5（ble_config）依赖 1/3 + hal_config → Task6（ui）→ Task7（app 接线）依赖全部 → Task8（整机验证）。

---

## Task 1: profile_ctl 扩展（新增字段 + 序列化）

**Files:**
- Modify: `components/core/services/include/services/profile_ctl.h`
- Modify: `components/core/services/src/profile_ctl.c`
- Test: `components/core/services/test/test_profile_ctl.c`（新增）

- [ ] **Step 1: 改写 profile_ctl.h**（加字段 + 序列化声明，去掉 `hal_config.h`/旧 apply 依赖）

```c
// components/core/services/include/services/profile_ctl.h
#pragma once
#include <stdbool.h>
#include <stdint.h>

typedef struct {
    char     name[24];       // 23 + NUL
    char     role[20];
    char     subtitle[28];
    int      battery;        // 0..100
    int      level;          // 0..999
    int      xp;             // >=0
    int      xp_max;         // >=1
    bool     online;
    uint32_t avatar_seed;    // 头像种子(确定性生成)
    int      token;          // 积分余额 >=0
} profile_data_t;

#define PROFILE_DEFAULT_SEED  1u          // 出厂默认头像 seed(不再开机随机)
#define PROFILE_BLOB_VER      1
#define PROFILE_BLOB_SIZE     (1 + (int)sizeof(profile_data_t))  // [ver][struct]

void profile_ctl_init(profile_data_t *d);   // 出厂默认

// 序列化到定长 blob(带版本头)。成功返回写入字节数,cap 不足返回 -1。
int  profile_serialize(const profile_data_t *d, uint8_t *buf, int cap);
// 从 blob 还原。版本/长度不符返回 -1,成功返回 0。
int  profile_deserialize(profile_data_t *d, const uint8_t *buf, int len);
```

- [ ] **Step 2: 改写 profile_ctl.c**（默认值加新字段，删旧 parse/apply，加序列化）

```c
// components/core/services/src/profile_ctl.c —— profile 默认值 + 定长 blob 序列化(纯逻辑)
#include "services/profile_ctl.h"
#include <string.h>

static void copy_trunc(char *dst, int cap, const char *src, int len) {
    if (len < 0) len = 0;
    if (len > cap - 1) len = cap - 1;
    memcpy(dst, src, (size_t)len);
    dst[len] = '\0';
}

void profile_ctl_init(profile_data_t *d) {
    memset(d, 0, sizeof(*d));
    copy_trunc(d->name,     sizeof d->name,     "FoloToy", 7);
    copy_trunc(d->role,     sizeof d->role,     "NETRUNNER", 9);
    copy_trunc(d->subtitle, sizeof d->subtitle, "DATA STALKER", 12);
    d->battery = 82; d->level = 1; d->xp = 0; d->xp_max = 100; d->online = true;
    d->avatar_seed = PROFILE_DEFAULT_SEED; d->token = 0;
}

int profile_serialize(const profile_data_t *d, uint8_t *buf, int cap) {
    if (cap < PROFILE_BLOB_SIZE) return -1;
    buf[0] = PROFILE_BLOB_VER;
    memcpy(buf + 1, d, sizeof(*d));
    return PROFILE_BLOB_SIZE;
}

int profile_deserialize(profile_data_t *d, const uint8_t *buf, int len) {
    if (len != PROFILE_BLOB_SIZE || buf[0] != PROFILE_BLOB_VER) return -1;
    memcpy(d, buf + 1, sizeof(*d));
    return 0;
}
```

- [ ] **Step 3: 写测试** `components/core/services/test/test_profile_ctl.c`

```c
#include "unity.h"
#include "services/profile_ctl.h"
#include <string.h>

TEST_CASE("profile default has new fields", "[profile]") {
    profile_data_t d; profile_ctl_init(&d);
    TEST_ASSERT_EQUAL_STRING("FoloToy", d.name);
    TEST_ASSERT_EQUAL_UINT32(PROFILE_DEFAULT_SEED, d.avatar_seed);
    TEST_ASSERT_EQUAL_INT(0, d.token);
}

TEST_CASE("profile serialize/deserialize round-trips", "[profile]") {
    profile_data_t a; profile_ctl_init(&a);
    a.level = 42; a.token = 1580; a.avatar_seed = 0xDEADBEEF; strcpy(a.name, "NEO");
    uint8_t blob[PROFILE_BLOB_SIZE];
    TEST_ASSERT_EQUAL_INT(PROFILE_BLOB_SIZE, profile_serialize(&a, blob, sizeof blob));
    profile_data_t b; memset(&b, 0, sizeof b);
    TEST_ASSERT_EQUAL_INT(0, profile_deserialize(&b, blob, PROFILE_BLOB_SIZE));
    TEST_ASSERT_EQUAL_INT(42, b.level);
    TEST_ASSERT_EQUAL_INT(1580, b.token);
    TEST_ASSERT_EQUAL_UINT32(0xDEADBEEF, b.avatar_seed);
    TEST_ASSERT_EQUAL_STRING("NEO", b.name);
}

TEST_CASE("profile deserialize rejects wrong version/len", "[profile]") {
    profile_data_t b; profile_ctl_init(&b);
    uint8_t blob[PROFILE_BLOB_SIZE]; profile_serialize(&b, blob, sizeof blob);
    blob[0] = 99;   // 坏版本
    TEST_ASSERT_EQUAL_INT(-1, profile_deserialize(&b, blob, PROFILE_BLOB_SIZE));
    TEST_ASSERT_EQUAL_INT(-1, profile_deserialize(&b, blob, PROFILE_BLOB_SIZE - 1));
}
```

- [ ] **Step 4: 注册测试**：编辑 `components/core/services/test/CMakeLists.txt` 的 `SRCS` 追加 `"test_profile_ctl.c"`。

- [ ] **Step 5: 验证**

Run（理想）: host_test 下 `idf.py build && ./build/test_app`，跑 `[profile]` 组。
当前回退: `idf.py build`（固件），确认 profile_ctl.c 编译通过、无对 `cfg_field_t` 的残留引用。
Expected: 编译通过；host 可运行时三个用例 PASS。

- [ ] **Step 6: Commit**：本仓库非 git，跳过。判据 = 上一步 build 通过。

---

## Task 2: config_json 模块（JSON 部分更新）

**Files:**
- Create: `components/core/services/include/services/config_json.h`
- Create: `components/core/services/src/config_json.c`
- Test: `components/core/services/test/test_config_json.c`
- Modify: `components/core/services/CMakeLists.txt`（加源文件 + `json` 依赖）

- [ ] **Step 1: 写头文件** `config_json.h`

```c
// components/core/services/include/services/config_json.h
#pragma once
#include <stdbool.h>
#include "services/profile_ctl.h"

// 本次 JSON 改动了哪些字段(供上层按需刷新 UI)。level/xp/xp_max 合并为 level。
typedef struct {
    bool name, role, subtitle, battery, level, online, token, avatar;
} cfg_changed_t;

// 对 profile 应用一条 JSON(可能无 NUL 结尾)的部分更新。
// 返回: >0 = 改动的字段数(notify status 0);
//        0 = 解析成功但无任何已知合法键(notify status 3);
//       -1 = JSON 解析失败或根不是对象(notify status 2)。
// chg 可为 NULL。
int config_json_apply(profile_data_t *d, const char *json, int len, cfg_changed_t *chg);
```

- [ ] **Step 2: 写测试** `test_config_json.c`（先失败）

```c
#include "unity.h"
#include "services/config_json.h"
#include <string.h>

TEST_CASE("json partial update changes only present keys", "[cfgjson]") {
    profile_data_t d; profile_ctl_init(&d);
    const char *j = "{\"name\":\"NEO\",\"level\":7,\"token\":1580,\"avatar_seed\":12345}";
    cfg_changed_t c;
    int n = config_json_apply(&d, j, (int)strlen(j), &c);
    TEST_ASSERT_EQUAL_INT(4, n);
    TEST_ASSERT_EQUAL_STRING("NEO", d.name);
    TEST_ASSERT_EQUAL_INT(7, d.level);
    TEST_ASSERT_EQUAL_INT(1580, d.token);
    TEST_ASSERT_EQUAL_UINT32(12345u, d.avatar_seed);
    TEST_ASSERT_TRUE(c.name && c.level && c.token && c.avatar);
    TEST_ASSERT_FALSE(c.role);          // 未出现 → 未改
    TEST_ASSERT_EQUAL_STRING("NETRUNNER", d.role);
}

TEST_CASE("json clamps out-of-range numbers", "[cfgjson]") {
    profile_data_t d; profile_ctl_init(&d);
    const char *j = "{\"battery\":250,\"level\":-5,\"xp_max\":0,\"token\":-9}";
    int n = config_json_apply(&d, j, (int)strlen(j), NULL);
    TEST_ASSERT_TRUE(n >= 3);
    TEST_ASSERT_EQUAL_INT(100, d.battery);
    TEST_ASSERT_EQUAL_INT(0, d.level);
    TEST_ASSERT_EQUAL_INT(1, d.xp_max);
    TEST_ASSERT_EQUAL_INT(0, d.token);
}

TEST_CASE("json wrong-type key is skipped", "[cfgjson]") {
    profile_data_t d; profile_ctl_init(&d);
    const char *j = "{\"battery\":\"high\",\"online\":true}";
    cfg_changed_t c;
    int n = config_json_apply(&d, j, (int)strlen(j), &c);
    TEST_ASSERT_EQUAL_INT(1, n);        // 只有 online 生效
    TEST_ASSERT_FALSE(c.battery);
    TEST_ASSERT_TRUE(c.online);
    TEST_ASSERT_EQUAL_INT(82, d.battery);
}

TEST_CASE("json no known key returns 0", "[cfgjson]") {
    profile_data_t d; profile_ctl_init(&d);
    const char *j = "{\"unknown\":1}";
    TEST_ASSERT_EQUAL_INT(0, config_json_apply(&d, j, (int)strlen(j), NULL));
}

TEST_CASE("json bad or non-object returns -1", "[cfgjson]") {
    profile_data_t d; profile_ctl_init(&d);
    TEST_ASSERT_EQUAL_INT(-1, config_json_apply(&d, "not json", 8, NULL));
    TEST_ASSERT_EQUAL_INT(-1, config_json_apply(&d, "[1,2]", 5, NULL));
}
```

- [ ] **Step 3: 实现** `config_json.c`

```c
// components/core/services/src/config_json.c —— cJSON 部分更新 profile(纯逻辑)
#include "services/config_json.h"
#include "cJSON.h"
#include <string.h>

static int clampi(int v, int lo, int hi) { return v < lo ? lo : (v > hi ? hi : v); }

static void copy_trunc(char *dst, int cap, const char *src) {
    int n = (int)strlen(src);
    if (n > cap - 1) n = cap - 1;
    memcpy(dst, src, (size_t)n);
    dst[n] = '\0';
}

// 取字符串键:存在且类型为字符串 → 回填 → 返回 1
static int take_str(cJSON *o, const char *k, char *dst, int cap, bool *flag) {
    cJSON *it = cJSON_GetObjectItemCaseSensitive(o, k);
    if (!cJSON_IsString(it) || it->valuestring == NULL) return 0;
    copy_trunc(dst, cap, it->valuestring);
    if (flag) *flag = true;
    return 1;
}

int config_json_apply(profile_data_t *d, const char *json, int len, cfg_changed_t *chg) {
    cfg_changed_t local = {0};
    if (chg) *chg = local;
    cJSON *root = cJSON_ParseWithLength(json, (size_t)len);
    if (!root) return -1;
    if (!cJSON_IsObject(root)) { cJSON_Delete(root); return -1; }

    int n = 0;
    cfg_changed_t c = {0};

    if (take_str(root, "name",     d->name,     (int)sizeof d->name,     &c.name))     n++;
    if (take_str(root, "role",     d->role,     (int)sizeof d->role,     &c.role))     n++;
    if (take_str(root, "subtitle", d->subtitle, (int)sizeof d->subtitle, &c.subtitle)) n++;

    cJSON *it;
    it = cJSON_GetObjectItemCaseSensitive(root, "battery");
    if (cJSON_IsNumber(it)) { d->battery = clampi((int)it->valuedouble, 0, 100); c.battery = true; n++; }

    it = cJSON_GetObjectItemCaseSensitive(root, "level");
    if (cJSON_IsNumber(it)) { d->level = clampi((int)it->valuedouble, 0, 999); c.level = true; n++; }
    it = cJSON_GetObjectItemCaseSensitive(root, "xp");
    if (cJSON_IsNumber(it)) { int v=(int)it->valuedouble; d->xp = v<0?0:v; c.level = true; n++; }
    it = cJSON_GetObjectItemCaseSensitive(root, "xp_max");
    if (cJSON_IsNumber(it)) { int v=(int)it->valuedouble; d->xp_max = v<1?1:v; c.level = true; n++; }

    it = cJSON_GetObjectItemCaseSensitive(root, "token");
    if (cJSON_IsNumber(it)) {
        double v = it->valuedouble;
        if (v < 0) v = 0; if (v > 1e9) v = 1e9;
        d->token = (int)v; c.token = true; n++;
    }

    it = cJSON_GetObjectItemCaseSensitive(root, "online");
    if (cJSON_IsBool(it)) { d->online = cJSON_IsTrue(it); c.online = true; n++; }

    it = cJSON_GetObjectItemCaseSensitive(root, "avatar_seed");
    if (cJSON_IsNumber(it)) { d->avatar_seed = (uint32_t)it->valuedouble; c.avatar = true; n++; }

    cJSON_Delete(root);
    if (chg) *chg = c;
    return n;
}
```

注：`xp`/`level`/`xp_max` 三键都置 `c.level`，但**每个出现的键各计 1**（`n++`），与测试 `json clamps` 期望 `n>=3` 一致。

- [ ] **Step 4: 改 CMakeLists**：`components/core/services/CMakeLists.txt`

```cmake
# components/services/CMakeLists.txt
idf_component_register(
    SRCS "src/led_service.c" "src/input_service.c" "src/audio_service.c" "src/ui_model.c"
         "src/avatar.c" "src/profile_ctl.c" "src/ble_match.c"
         "src/config_json.c" "src/frame_reasm.c"
    INCLUDE_DIRS "include"
    REQUIRES ports json)
```
（`frame_reasm.c` 在 Task 3 创建；此处一并登记。若按任务顺序单独 build Task 2，可暂不加 `frame_reasm.c` 再于 Task 3 加回——推荐直接一次写全并按依赖顺序实现。）

- [ ] **Step 5: 注册测试**：`components/core/services/test/CMakeLists.txt` 的 `SRCS` 追加 `"test_config_json.c"`。

- [ ] **Step 6: 验证**

Run（理想）: host_test 跑 `[cfgjson]` 组（需 host 有 cJSON；host_test 已知损坏则跳过）。
回退: `idf.py build` 固件编译通过（cJSON 随 IDF，`REQUIRES json` 后可解析）。
Expected: 编译通过；host 可运行时 5 个用例 PASS。

- [ ] **Step 7: Commit**：非 git，跳过。判据 = build 通过。

---

## Task 3: frame_reasm 模块（分帧重组状态机）

**Files:**
- Create: `components/core/services/include/services/frame_reasm.h`
- Create: `components/core/services/src/frame_reasm.c`
- Test: `components/core/services/test/test_frame_reasm.c`

- [ ] **Step 1: 写头文件** `frame_reasm.h`

```c
// components/core/services/include/services/frame_reasm.h
#pragma once
#include <stdint.h>

#define FRAME_MAX_PAYLOAD 256
#define FRAME_HDR_LEN     4          // [ver:1][type:1][len:2 小端]
#define FRAME_VER         1

typedef enum {
    FRAME_NEED_MORE = 0,   // 尚未凑齐一帧
    FRAME_READY     = 1,   // 凑齐一帧(见 out_type/out_payload/out_len)
    FRAME_ERR_VER   = -1,  // 版本号非法
    FRAME_ERR_LEN   = -2,  // 声明 len 超过 FRAME_MAX_PAYLOAD
} frame_status_t;

typedef struct {
    uint8_t  buf[FRAME_HDR_LEN + FRAME_MAX_PAYLOAD];
    int      have;         // 已累积字节数
    int      need;         // 整帧总长(头凑齐后确定); -1 表示头未凑齐
} frame_reasm_t;

void frame_reasm_reset(frame_reasm_t *r);

// 追加一段写入字节。逐帧驱动:返回 FRAME_READY 时,out_* 指向本帧;
// 调用方处理后应继续调用(剩余字节可能含下一帧)——本实现一次 push 只输出至多一帧,
// 约定单次 BLE 写不跨帧(小程序每帧独立分包),故足够。错误码见 frame_status_t。
// FRAME_READY / 错误 返回后内部自动 reset。
frame_status_t frame_reasm_push(frame_reasm_t *r, const uint8_t *data, int len,
                                uint8_t *out_type, const uint8_t **out_payload, int *out_len);
```

- [ ] **Step 2: 写测试** `test_frame_reasm.c`

```c
#include "unity.h"
#include "services/frame_reasm.h"
#include <string.h>

static void mk_hdr(uint8_t *h, uint8_t type, int len) {
    h[0]=FRAME_VER; h[1]=type; h[2]=(uint8_t)(len&0xFF); h[3]=(uint8_t)((len>>8)&0xFF);
}

TEST_CASE("reasm single chunk whole frame", "[reasm]") {
    frame_reasm_t r; frame_reasm_reset(&r);
    uint8_t f[4+3]; mk_hdr(f,0x01,3); f[4]='a'; f[5]='b'; f[6]='c';
    uint8_t type; const uint8_t *p; int len;
    TEST_ASSERT_EQUAL_INT(FRAME_READY, frame_reasm_push(&r,f,sizeof f,&type,&p,&len));
    TEST_ASSERT_EQUAL_UINT8(0x01,type);
    TEST_ASSERT_EQUAL_INT(3,len);
    TEST_ASSERT_EQUAL_INT(0, memcmp(p,"abc",3));
}

TEST_CASE("reasm split across chunks (header split + payload split)", "[reasm]") {
    frame_reasm_t r; frame_reasm_reset(&r);
    uint8_t f[4+5]; mk_hdr(f,0x01,5); memcpy(f+4,"hello",5);
    uint8_t type; const uint8_t *p; int len;
    // 分片:2 + 3(跨头) + 4(跨 payload)
    TEST_ASSERT_EQUAL_INT(FRAME_NEED_MORE, frame_reasm_push(&r,f,2,&type,&p,&len));
    TEST_ASSERT_EQUAL_INT(FRAME_NEED_MORE, frame_reasm_push(&r,f+2,3,&type,&p,&len));
    TEST_ASSERT_EQUAL_INT(FRAME_READY,     frame_reasm_push(&r,f+5,4,&type,&p,&len));
    TEST_ASSERT_EQUAL_INT(5,len);
    TEST_ASSERT_EQUAL_INT(0, memcmp(p,"hello",5));
}

TEST_CASE("reasm rejects bad version and oversize len", "[reasm]") {
    frame_reasm_t r; uint8_t type; const uint8_t *p; int len;
    uint8_t bad[4]; mk_hdr(bad,0x01,3); bad[0]=9;
    frame_reasm_reset(&r);
    TEST_ASSERT_EQUAL_INT(FRAME_ERR_VER, frame_reasm_push(&r,bad,4,&type,&p,&len));
    uint8_t big[4]; mk_hdr(big,0x01, FRAME_MAX_PAYLOAD+1);
    frame_reasm_reset(&r);
    TEST_ASSERT_EQUAL_INT(FRAME_ERR_LEN, frame_reasm_push(&r,big,4,&type,&p,&len));
}

TEST_CASE("reasm zero-length payload frame", "[reasm]") {
    frame_reasm_t r; frame_reasm_reset(&r);
    uint8_t f[4]; mk_hdr(f,0x03,0);
    uint8_t type; const uint8_t *p; int len;
    TEST_ASSERT_EQUAL_INT(FRAME_READY, frame_reasm_push(&r,f,4,&type,&p,&len));
    TEST_ASSERT_EQUAL_UINT8(0x03,type);
    TEST_ASSERT_EQUAL_INT(0,len);
}
```

- [ ] **Step 3: 实现** `frame_reasm.c`

```c
// components/core/services/src/frame_reasm.c —— BLE 分帧重组状态机(纯逻辑)
#include "services/frame_reasm.h"
#include <string.h>

void frame_reasm_reset(frame_reasm_t *r) { r->have = 0; r->need = -1; }

frame_status_t frame_reasm_push(frame_reasm_t *r, const uint8_t *data, int len,
                                uint8_t *out_type, const uint8_t **out_payload, int *out_len) {
    for (int i = 0; i < len; i++) {
        if (r->have < (int)sizeof r->buf) r->buf[r->have] = data[i];
        r->have++;

        if (r->need < 0 && r->have == FRAME_HDR_LEN) {
            if (r->buf[0] != FRAME_VER) { frame_reasm_reset(r); return FRAME_ERR_VER; }
            int plen = (int)r->buf[2] | ((int)r->buf[3] << 8);
            if (plen > FRAME_MAX_PAYLOAD) { frame_reasm_reset(r); return FRAME_ERR_LEN; }
            r->need = FRAME_HDR_LEN + plen;
        }
        if (r->need >= 0 && r->have >= r->need) {
            *out_type    = r->buf[1];
            *out_payload = r->buf + FRAME_HDR_LEN;
            *out_len     = r->need - FRAME_HDR_LEN;
            frame_reasm_reset(r);
            return FRAME_READY;
        }
    }
    return FRAME_NEED_MORE;
}
```

- [ ] **Step 4: 登记源文件**：确认 `components/core/services/CMakeLists.txt` 的 SRCS 含 `"src/frame_reasm.c"`（Task 2 Step 4 已加）。

- [ ] **Step 5: 注册测试**：`components/core/services/test/CMakeLists.txt` 追加 `"test_frame_reasm.c"`。

- [ ] **Step 6: 验证**

Run（理想）: host_test 跑 `[reasm]` 组（纯逻辑，不依赖 cJSON，host 若可用应能跑）。
回退: `idf.py build` 固件编译通过。
Expected: 编译通过；4 个用例 PASS。

- [ ] **Step 7: Commit**：非 git，跳过。

---

## Task 4: hal_kv 端口 + NVS 实现

**Files:**
- Create: `components/core/ports/include/hal/hal_kv.h`
- Create: `components/platform/platform_esp32/src/kv_nvs.c`
- Modify: `components/platform/platform_esp32/include/platform/platform_factory.h`
- Modify: `components/platform/platform_esp32/CMakeLists.txt`

- [ ] **Step 1: 写端口** `hal_kv.h`

```c
// components/core/ports/include/hal/hal_kv.h
#pragma once
#include <stdint.h>

typedef struct hal_kv_s hal_kv_t;
typedef struct {
    // 读: 成功把值写入 buf(<=cap),*out_len 为实际长度,返回 0;不存在/出错返回 -1。
    int (*get)(hal_kv_t *self, const char *key, void *buf, int cap, int *out_len);
    // 写: 返回 0 成功,<0 失败。
    int (*set)(hal_kv_t *self, const char *key, const void *data, int len);
} hal_kv_api_t;
struct hal_kv_s { const hal_kv_api_t *api; void *impl; };

static inline int hal_kv_get(hal_kv_t *c, const char *key, void *buf, int cap, int *out_len) {
    return c->api->get(c, key, buf, cap, out_len);
}
static inline int hal_kv_set(hal_kv_t *c, const char *key, const void *data, int len) {
    return c->api->set(c, key, data, len);
}
```

- [ ] **Step 2: 实现** `kv_nvs.c`

```c
// components/platform/platform_esp32/src/kv_nvs.c —— hal_kv 的 NVS 实现
#include "platform/platform_factory.h"
#include "hal/hal_kv.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "esp_log.h"
#include <string.h>

static const char *TAG = "kv_nvs";
#define KV_NS "trae_cfg"

static int kv_get(hal_kv_t *self, const char *key, void *buf, int cap, int *out_len) {
    (void)self;
    nvs_handle_t h;
    if (nvs_open(KV_NS, NVS_READONLY, &h) != ESP_OK) return -1;
    size_t sz = (size_t)cap;
    esp_err_t e = nvs_get_blob(h, key, buf, &sz);
    nvs_close(h);
    if (e != ESP_OK) return -1;
    if (out_len) *out_len = (int)sz;
    return 0;
}

static int kv_set(hal_kv_t *self, const char *key, const void *data, int len) {
    (void)self;
    nvs_handle_t h;
    if (nvs_open(KV_NS, NVS_READWRITE, &h) != ESP_OK) return -1;
    esp_err_t e = nvs_set_blob(h, key, data, (size_t)len);
    if (e == ESP_OK) e = nvs_commit(h);
    nvs_close(h);
    if (e != ESP_OK) { ESP_LOGW(TAG, "kv_set(%s) failed: 0x%x", key, e); return -1; }
    return 0;
}

static const hal_kv_api_t API = { .get = kv_get, .set = kv_set };
static hal_kv_t s_kv = { .api = &API, .impl = NULL };

hal_kv_t *platform_create_kv(const board_config_t *cfg) {
    (void)cfg;
    // NVS 由 ble_config 的 nvs_flash_init 负责初始化;若单独用可自行确保已初始化。
    return &s_kv;
}
```

- [ ] **Step 3: 声明工厂 + 头**：`platform_factory.h` 顶部加 `#include "hal/hal_kv.h"`，并在末尾加：

```c
// 键值持久化(NVS 实现)。始终可用(不受 PERIPH_* 影响)。
hal_kv_t *platform_create_kv(const board_config_t *cfg);
```

- [ ] **Step 4: 改 CMakeLists**：`components/platform/platform_esp32/CMakeLists.txt` 的 SRCS 追加 `"src/kv_nvs.c"`（`nvs_flash` 已在 PRIV_REQUIRES 中）。

- [ ] **Step 5: 验证**

Run: `idf.py build`。
Expected: 编译通过（`platform_create_kv` 可链接）。此模块无独立单测（依赖 NVS 硬件），靠 Task 8 硬件联调验证持久化。

- [ ] **Step 6: Commit**：非 git，跳过。

---

## Task 5: hal_config 改造 + ble_config 新通道

**Files:**
- Modify: `components/core/ports/include/hal/hal_config.h`
- Modify: `components/platform/platform_esp32/src/ble_config.c`

- [ ] **Step 1: 改写 hal_config.h**（消息回调 + notify_status，移除 cfg_field_t）

```c
// components/core/ports/include/hal/hal_config.h
#pragma once
#include <stdint.h>

// 收到一条完整应用层帧(已重组)。payload 为原始字节(可能无 NUL)。
typedef void (*cfg_msg_cb_t)(uint8_t type, const uint8_t *payload, int len, void *user);

typedef struct hal_config_s hal_config_t;
typedef struct {
    void (*on_message)(hal_config_t *self, cfg_msg_cb_t cb, void *user);
    void (*notify_status)(hal_config_t *self, uint8_t type, uint8_t status);  // 回报处理结果
    void (*set_export)(hal_config_t *self, const uint8_t *data, int len);     // 头像导出 blob
} hal_config_api_t;
struct hal_config_s { const hal_config_api_t *api; void *impl; };

static inline void hal_config_on_message(hal_config_t *c, cfg_msg_cb_t cb, void *user) {
    c->api->on_message(c, cb, user);
}
static inline void hal_config_notify_status(hal_config_t *c, uint8_t type, uint8_t status) {
    if (c->api->notify_status) c->api->notify_status(c, type, status);
}
static inline void hal_config_set_export(hal_config_t *c, const uint8_t *data, int len) {
    if (c->api->set_export) c->api->set_export(c, data, len);
}

// 通道消息类型与 notify 状态码(A/B/C 共用)
#define CFG_MSG_JSON     0x01
#define CFG_STATUS_OK        0
#define CFG_STATUS_FRAME_ERR 1
#define CFG_STATUS_PARSE_ERR 2
#define CFG_STATUS_NO_FIELD  3
#define CFG_STATUS_TOO_LONG  4
```

- [ ] **Step 2: 改写 ble_config.c** —— 完整替换特征表/回调/GATT 相关部分。分小步：

- [ ] **Step 2a:** 顶部 include 追加，删除旧字段特征数组，加新状态与句柄：

```c
#include "services/frame_reasm.h"     // 放在其它 include 之后

// 删除: g_chr_uuid[CFG_FIELD_COUNT]、g_chr_name[CFG_FIELD_COUNT]
// 保留: g_svc_uuid、g_export_uuid、s_export_ptr/len、s_addr_type

// 新增命令写 / notify 特征 UUID(沿用 TRAECARD 基址,末字节 0x10/0x11)
static const ble_uuid128_t g_cmd_uuid    = TRAE_UUID128(0x10);
static const ble_uuid128_t g_notify_uuid = TRAE_UUID128(0x11);

static cfg_msg_cb_t   s_msg_cb;
static void          *s_msg_user;
static frame_reasm_t  s_reasm;
static uint16_t       s_conn = BLE_HS_CONN_HANDLE_NONE;
static uint16_t       s_notify_handle = 0;    // notify 特征 val handle(GATT 注册时回填)
static int64_t        s_last_write_us = 0;
```

- [ ] **Step 2b:** 用「重组写回调」替换旧 `chr_write`（保留 `cud_read`、`export_read`）：

```c
// 命令写:累积字节 → 重组 → 完整帧交给上层消息回调。运行于 NimBLE host task。
static int cmd_write(uint16_t conn, uint16_t attr, struct ble_gatt_access_ctxt *ctxt, void *arg) {
    (void)conn; (void)attr; (void)arg;
    if (ctxt->op != BLE_GATT_ACCESS_OP_WRITE_CHR) return BLE_ATT_ERR_UNLIKELY;
    uint8_t seg[FRAME_HDR_LEN + FRAME_MAX_PAYLOAD];   // 容纳单次最大 ATT 写(可能整帧一次到)
    uint16_t got = 0;
    uint16_t avail = OS_MBUF_PKTLEN(ctxt->om);
    if (avail > sizeof seg) avail = sizeof seg;
    if (ble_hs_mbuf_to_flat(ctxt->om, seg, avail, &got) != 0) return BLE_ATT_ERR_UNLIKELY;

    int64_t now = esp_timer_get_time();
    if (s_reasm.have > 0 && (now - s_last_write_us) > 2000000) frame_reasm_reset(&s_reasm); // >2s 超时重置
    s_last_write_us = now;

    uint8_t type; const uint8_t *pl; int pl_len;
    frame_status_t st = frame_reasm_push(&s_reasm, seg, got, &type, &pl, &pl_len);
    if (st == FRAME_READY) {
        if (s_msg_cb) s_msg_cb(type, pl, pl_len, s_msg_user);
    } else if (st == FRAME_ERR_VER || st == FRAME_ERR_LEN) {
        // 帧头非法:直接回报(type 未知,用 0)
        cfg_notify(0, st == FRAME_ERR_LEN ? CFG_STATUS_TOO_LONG : CFG_STATUS_FRAME_ERR);
    }
    return 0;
}
```
（`esp_timer_get_time` 需 `#include "esp_timer.h"`；`cfg_notify` 见 2d。）

- [ ] **Step 2c:** 重建 GATT 表 `build_gatt_table()`（命令写 + notify + AvatarExport 三个特征）：

```c
static struct ble_gatt_chr_def g_chrs[4];    // cmd, notify, export, 结束哨兵
static struct ble_gatt_svc_def g_svcs[2];

static void build_gatt_table(void) {
    // 0: 命令写
    g_chrs[0].uuid = &g_cmd_uuid.u;
    g_chrs[0].access_cb = cmd_write;
    g_chrs[0].arg = NULL;
    g_chrs[0].flags = BLE_GATT_CHR_F_WRITE;
    g_chrs[0].descriptors = NULL;
    // 1: notify 状态
    g_chrs[1].uuid = &g_notify_uuid.u;
    g_chrs[1].access_cb = NULL;               // 纯 notify,无读写回调
    g_chrs[1].arg = NULL;
    g_chrs[1].flags = BLE_GATT_CHR_F_NOTIFY;
    g_chrs[1].val_handle = &s_notify_handle;  // 注册后回填 val handle
    g_chrs[1].descriptors = NULL;
    // 2: AvatarExport 只读
    g_chrs[2].uuid = &g_export_uuid.u;
    g_chrs[2].access_cb = export_read;
    g_chrs[2].arg = NULL;
    g_chrs[2].flags = BLE_GATT_CHR_F_READ;
    g_chrs[2].descriptors = NULL;
    memset(&g_chrs[3], 0, sizeof g_chrs[3]);  // 结束哨兵

    g_svcs[0].type = BLE_GATT_SVC_TYPE_PRIMARY;
    g_svcs[0].uuid = &g_svc_uuid.u;
    g_svcs[0].characteristics = g_chrs;
    memset(&g_svcs[1], 0, sizeof g_svcs[1]);
}
```
删除旧 `g_dscs[CFG_FIELD_COUNT+1][2]`、旧 `g_chrs[CFG_FIELD_COUNT+2]` 定义与其填充循环。

- [ ] **Step 2d:** notify 发送 + gap_event 保存 conn_handle：

```c
// 从 host task 发 notify [type][status]。未连接或无句柄则静默跳过。
static void cfg_notify(uint8_t type, uint8_t status) {
    if (s_conn == BLE_HS_CONN_HANDLE_NONE || s_notify_handle == 0) return;
    uint8_t pkt[2] = { type, status };
    struct os_mbuf *om = ble_hs_mbuf_from_flat(pkt, sizeof pkt);
    if (om) ble_gatts_notify_custom(s_conn, s_notify_handle, om);
}
```
（`cfg_notify` 需在 `cmd_write` 之前声明或前置原型；把定义放在 `cmd_write` 上方。）

修改 `gap_event`：

```c
static int gap_event(struct ble_gap_event *event, void *arg) {
    (void)arg;
    switch (event->type) {
        case BLE_GAP_EVENT_CONNECT:
            if (event->connect.status == 0) s_conn = event->connect.conn_handle;
            break;
        case BLE_GAP_EVENT_DISCONNECT:
            s_conn = BLE_HS_CONN_HANDLE_NONE;
            frame_reasm_reset(&s_reasm);
            start_adv();
            break;
        case BLE_GAP_EVENT_ADV_COMPLETE:
            start_adv();
            break;
        default: break;
    }
    return 0;
}
```

- [ ] **Step 2e:** vtable 与工厂：把 `cfg_on_write` 换成 `cfg_on_message`，新增 `cfg_notify_status`，更新 API 结构与 `on_sync`：

```c
static void cfg_on_message(hal_config_t *self, cfg_msg_cb_t cb, void *user) {
    (void)self; s_msg_cb = cb; s_msg_user = user;
}
static void cfg_notify_status(hal_config_t *self, uint8_t type, uint8_t status) {
    (void)self; cfg_notify(type, status);
}
static void cfg_set_export(hal_config_t *self, const uint8_t *data, int len) {
    (void)self; s_export_ptr = data; s_export_len = len;
}
static const hal_config_api_t API = {
    .on_message = cfg_on_message,
    .notify_status = cfg_notify_status,
    .set_export = cfg_set_export,
};
static hal_config_t s_handle = { .api = &API, .impl = NULL };
```
在 `platform_create_ble_config` 里 `nimble_port_init` 成功后加 `frame_reasm_reset(&s_reasm);`（`on_sync` 不变，仍 `start_adv(); ble_scan_start(...)`）。

- [ ] **Step 3: 验证**

Run: `idf.py build`。
Expected: 编译通过；无 `CFG_FIELD_COUNT`/`chr_write`/`cfg_write_cb_t` 残留引用（此时 app.c 尚未改，会因旧 `hal_config_on_write`/`on_cfg_write` 报错 → 由 Task 7 一并修复；可先只编 platform 组件或接受 app.c 报错，Task 7 后整体 build 通过）。

- [ ] **Step 4: Commit**：非 git，跳过。

---

## Task 6: ui_profile 积分余额 + dock 4 格

**Files:**
- Modify: `components/ui/presentation/include/presentation/ui_profile.h`
- Modify: `components/ui/presentation/src/ui_profile.c`

- [ ] **Step 1: 头文件加声明**：`ui_profile.h` 在 `ui_profile_set_avatar_seed` 后加：

```c
void ui_profile_set_token(int token);               // 积分余额显示
```
并把注释里「item_id: 0换头像 1亮度 2 LED 3在线」更新为「item_id: 1亮度 2 LED 3在线 4游戏」。

- [ ] **Step 2: profile_t 加 token label 字段**：在 `struct { ... lv_obj_t *lvl_num, *xp_lbl, *bar; ...}` 里追加 `lv_obj_t *token_lbl;`

- [ ] **Step 3: create 里加积分余额 label**：在 `P.xp_lbl` 之后（`mid_y0 + 124` 行下方）加：

```c
    P.token_lbl = make_label(scr, "TOKEN 0", &lv_font_montserrat_14, COL_GREEN2);
    lv_obj_set_style_text_letter_space(P.token_lbl, 1, 0);
    lv_obj_set_pos(P.token_lbl, 118, mid_y0 + 142);
```

- [ ] **Step 4: dock 改 4 格**（移除 AVATAR，保留 id 1/2/3/4）：把 `ui_profile_create` 末尾 dock 磁贴创建段替换为：

```c
    // 4 格均分:磁贴宽 54,步进 58,左边距 5(5 + i*58,末格 5+3*58+54=233<240)
    const int TW = 54, TSTEP = 58, TX0 = 5;
    make_dock_tile(dock, TX0 + 0 * TSTEP, TW, 1, "BRIGHT", "60%");
    make_dock_tile(dock, TX0 + 1 * TSTEP, TW, 2, "LED",    "OFF");
    make_dock_tile(dock, TX0 + 2 * TSTEP, TW, 3, "ONLINE", "YES");
    make_dock_tile(dock, TX0 + 3 * TSTEP, TW, 4, "GAME",   LV_SYMBOL_PLAY);
```
（`make_dock_tile` 内 `if (id >= 0 && id < 4) P.dock_val[id] = vl;` 保持不变：id 1/2/3 存入 dock_val，id 4 不存，符合现有 `ui_profile_set_dock_value` 边界。）

- [ ] **Step 5: 实现 set_token**：在文件末尾运行时 setter 区加：

```c
void ui_profile_set_token(int token) {
    if (token < 0) token = 0;
    if (P.token_lbl) lv_label_set_text_fmt(P.token_lbl, "TOKEN %d", token);
}
```

- [ ] **Step 6: 验证**

Run: `idf.py build`。
Expected: 编译通过；硬件上主界面身份区多出「TOKEN 0」，dock 为 4 格 BRIGHT/LED/ONLINE/GAME。

- [ ] **Step 7: Commit**：非 git，跳过。

---

## Task 7: app.c 接线（加载/持久化/消息回调/notify）

**Files:**
- Modify: `components/app/src/app.c`

- [ ] **Step 1: include 调整**：`#if PERIPH_BLE` 块加 config_json/frame 常量来源：

```c
#if PERIPH_BLE
#include "services/profile_ctl.h"
#include "services/config_json.h"
#endif
```
（`CFG_MSG_JSON`/`CFG_STATUS_*` 来自 `hal/hal_config.h`，已随 `platform_factory.h` 间接包含。）

- [ ] **Step 2: 加 kv 句柄 + profile 加载函数**：在 `static profile_data_t s_profile;` 附近加：

```c
#if PERIPH_BLE
static hal_kv_t *s_kv;
static void save_profile(void) {
    if (!s_kv) return;
    uint8_t blob[PROFILE_BLOB_SIZE];
    int n = profile_serialize(&s_profile, blob, sizeof blob);
    if (n > 0) hal_kv_set(s_kv, "profile", blob, n);
}
#endif
```

- [ ] **Step 3: 替换 on_cfg_write 为 on_cfg_message**（消息回调 + 部分刷新 + 存 + notify）：

```c
// BLE 消息回调(NimBLE host task)。type=JSON → 部分更新 → 存 NVS → 刷新 UI → notify。
static void on_cfg_message(uint8_t type, const uint8_t *payload, int len, void *user) {
    (void)user;
    if (type != CFG_MSG_JSON) return;
    cfg_changed_t chg;
    int n = config_json_apply(&s_profile, (const char *)payload, len, &chg);
    uint8_t status = (n > 0) ? CFG_STATUS_OK
                   : (n == 0) ? CFG_STATUS_NO_FIELD
                              : CFG_STATUS_PARSE_ERR;
    if (n > 0) {
        save_profile();
#if PERIPH_DISPLAY
        if (platform_lvgl_lock(0)) {
            if (chg.name)     ui_profile_set_name(s_profile.name);
            if (chg.role)     ui_profile_set_role(s_profile.role);
            if (chg.subtitle) ui_profile_set_subtitle(s_profile.subtitle);
            if (chg.battery)  ui_profile_set_battery(s_profile.battery);
            if (chg.online)   ui_profile_set_online(s_profile.online);
            if (chg.token)    ui_profile_set_token(s_profile.token);
            if (chg.level)    ui_profile_set_level(s_profile.level, s_profile.xp, s_profile.xp_max);
            if (chg.avatar) { s_avatar_seed = s_profile.avatar_seed;
                              ui_profile_set_avatar_seed(s_avatar_seed); publish_export(); }
            platform_lvgl_unlock();
        }
#endif
    }
    if (s_cfg) hal_config_notify_status(s_cfg, CFG_MSG_JSON, status);
}
```
删除旧 `on_cfg_write` 整个函数。

- [ ] **Step 4: 移除 dock 换头像**：`on_dock_action` 删除 `case 0:` 整段（换头像）。其余 case 不变。

- [ ] **Step 5: app_run 内 BLE 段改写**：把 `#if PERIPH_BLE` 段（原 202–212 行）替换为：

```c
#if PERIPH_BLE
    profile_ctl_init(&s_profile);
    s_kv = platform_create_kv(&board);
    {   // 开机从 NVS 载入上次配置(含头像 seed / token);无记录则用出厂默认
        uint8_t blob[PROFILE_BLOB_SIZE]; int got = 0;
        if (s_kv && hal_kv_get(s_kv, "profile", blob, sizeof blob, &got) == 0)
            profile_deserialize(&s_profile, blob, got);
    }
#if PERIPH_DISPLAY
    if (platform_lvgl_lock(0)) {   // 用载入后的 profile 初始化界面
        ui_profile_set_name(s_profile.name);
        ui_profile_set_role(s_profile.role);
        ui_profile_set_subtitle(s_profile.subtitle);
        ui_profile_set_battery(s_profile.battery);
        ui_profile_set_online(s_profile.online);
        ui_profile_set_level(s_profile.level, s_profile.xp, s_profile.xp_max);
        ui_profile_set_token(s_profile.token);
        s_avatar_seed = s_profile.avatar_seed;
        ui_profile_set_avatar_seed(s_avatar_seed);
        platform_lvgl_unlock();
    }
#endif
    hal_config_t *cfg_src = platform_create_ble_config(&board);
    hal_config_on_message(cfg_src, on_cfg_message, NULL);
    s_cfg = cfg_src;
    publish_export();               // 推送头像导出 blob(用载入的 seed)
#if PERIPH_DISPLAY
    s_scan = platform_create_ble_scan(&board);
    hal_ble_scan_on_match(s_scan, on_ble_match, NULL);
#endif
#endif
```

- [ ] **Step 6: 处理无 BLE 时的头像 seed**：`#if PERIPH_DISPLAY` 建 UI 段里原有 `s_avatar_seed = platform_random_seed(); ui_profile_set_avatar_seed(s_avatar_seed);`（190–192 行附近）——当 `PERIPH_BLE` 时头像由上面 BLE 段设定，会重复设一次（无害）。为清晰，把该两行用 `#if !PERIPH_BLE` 包裹，仅无 BLE 时随机：

```c
        ui_profile_create();
#if !PERIPH_BLE
        s_avatar_seed = platform_random_seed();
        ui_profile_set_avatar_seed(s_avatar_seed);
#endif
        ui_profile_set_on_action(on_dock_action, NULL);
        ui_profile_set_dock_value(1, "60%");
        ui_profile_set_dock_value(2, "OFF");
        ui_profile_set_dock_value(3, "YES");
```
（`ui_profile_create` 内部已 `avatar_generate(0u,...)` 画了默认头像占位，BLE 段随后用载入 seed 覆盖；无 BLE 时走随机。）

- [ ] **Step 7: 验证**

Run: `idf.py build`（整机，此时全部改动齐备，应链接通过）。
Expected: 编译链接通过，无未定义符号、无 `cfg_field_t`/`on_write` 残留。

- [ ] **Step 8: Commit**：非 git，跳过。

---

## Task 8: 整机构建 + 硬件联调

**Files:** 无（验证）

- [ ] **Step 1: 全量构建**：`idf.py build`。Expected: 成功，无 error/警告级别符号问题。

- [ ] **Step 2: 检查 DRAM 未被撑爆**（C3 对静态缓冲敏感）：`idf.py size`，确认 DRAM 余量与改动前相近（新增静态：`frame_reasm_t s_reasm` ≈ 260B、接收 `seg[128]` 在栈上、token label 属 LVGL 堆——均小）。Expected: BLE 仍能初始化（无 `hci inits failed`）。

- [ ] **Step 3: 烧录 + 监视**：`idf.py -p <PORT> flash monitor`。Expected: 正常进主界面，显示「TOKEN 0」与 4 格 dock；无 BLE 初始化崩溃。

- [ ] **Step 4: JSON 下发联调**：用微信小程序或 PC BLE 工具，向命令写特征（UUID 末字节 0x10）分包写入一帧：
  - 帧 = `01 01 <len 小端> <payload>`，payload = `{"name":"NEO","level":7,"token":1580,"avatar_seed":12345}`。
  - Expected: 界面 name→NEO、LV.7、TOKEN 1580、头像变为 seed=12345 的图案；notify 特征（0x11）收到 `01 00`（OK）。

- [ ] **Step 5: 部分更新验证**：再发 `{"token":9999}`，Expected: 仅 TOKEN 变 9999，其它不变；notify `01 00`。

- [ ] **Step 6: 错误路径**：发坏 JSON `{bad`，Expected: notify `01 02`（PARSE_ERR），界面不变。发 `{"zzz":1}`，Expected: notify `01 03`（NO_FIELD）。

- [ ] **Step 7: 持久化验证**：断电重启，Expected: 上次下发的 name/level/token/头像 seed 均保留（从 NVS 载入）。

- [ ] **Step 8: 回归**：确认 dock 亮度/LED/在线/游戏、BLE 扫描爱心屏（Spec 前序功能）不受影响。

---

## Self-Review（对照 spec）

- **传输层协议**（spec §4）→ Task 3（frame_reasm）+ Task 5（cmd_write/超时/notify）✅
- **JSON 部分更新 + schema**（spec §5）→ Task 2（config_json，含全部键 + 校验 + 类型跳过）✅
- **avatar_seed / token 字段**（spec §5）→ Task 1（数据）+ Task 2（解析）+ Task 6（token 显示）+ Task 7（seed 应用）✅
- **NVS 持久化**（spec §6）→ Task 1（序列化）+ Task 4（hal_kv/nvs）+ Task 7（load/save）✅
- **积分余额显示**（spec §7）→ Task 6 ✅
- **移除项**（spec §8）：8 特征 → Task 5；dock 换头像 → Task 6（格）+ Task 7（case 0）；开机随机 seed → Task 7 Step 6 ✅
- **notify 状态码**（spec §4）→ hal_config.h 常量（Task 5 Step 1）+ Task 7 映射 + Task 5 帧错回报 ✅
- **线程/锁**（spec §9）→ Task 7 消息回调持 `platform_lvgl_lock`；notify 在 host task 直发 ✅
- **类型一致性**：`config_json_apply` 签名/`cfg_changed_t` 字段（Task 2）与 Task 7 用法一致；`frame_reasm_push` 签名（Task 3）与 Task 5 用法一致；`hal_config_on_message`/`notify_status`（Task 5）与 Task 7 一致；`PROFILE_BLOB_SIZE`/`profile_serialize`（Task 1）与 Task 7 一致 ✅
- **占位符**：无 TBD/TODO，代码均完整 ✅

已知取舍：`config_json` 依赖 cJSON，其 host 单测受 host_test 损坏影响，主验证走固件 build + Task 8 硬件联调（符合项目既有验证方式）。
