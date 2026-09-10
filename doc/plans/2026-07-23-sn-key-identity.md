# SN/Key 设备身份 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给卡片增加出厂身份（SN/Key/ProductKey/HwVer），SN 取 `esp_read_mac(ESP_MAC_WIFI_STA)`，产线烧录独立 `cardid` NVS 分区，小程序经 BLE 只读特征拿到 SN+Key，ProductKey 只用于 HMAC 且永不外泄。

**Architecture:** 纯逻辑（SN 格式化、AT 指令解析）放 `core/services` 可单测；`hal_identity`（只读）与 `hal_identity_provision`（写入）拆成两个头文件做编译期隔离；`identity_nvs.c` 是唯一实现，持有 `cardid` 分区并在 SN 缺失时按 MAC 自补；BLE 特征的 72 字节 blob 由 `app.c` 组装后经 `hal_config_set_identity` 推给 `ble_config.c`——app 层拿不到 ProductKey，所以物理上不可能把它塞进 blob。

**Tech Stack:** ESP-IDF v5.5.3、ESP32-C3、NimBLE GATT、NVS（独立分区）、mbedtls（HMAC-SHA256/SHA256）、USB-Serial-JTAG、Unity。

**Spec:** `doc/specs/2026-07-23-sn-key-identity-design.md`

---

## ⚠️ 执行后修订（2026-07-23，代码审查结果）

**本计划已执行完毕（T1–T9）。若要重新执行，下列代码块必须按此修订，否则会重新引入已修掉的缺陷。**
以实际仓库代码为准。

| # | 位置 | 计划里的写法 | 问题 | 已改为 |
|---|---|---|---|---|
| **C1** | T5 `identity_nvs.c` `platform_create_identity()` | `NO_FREE_PAGES`/`NEW_VERSION_FOUND` → `nvs_flash_erase_partition(cardid)` → 重新 init | **严重**：这是系统 `nvs` 分区的标准恢复模式，但 `cardid` 存的是产线一次性写入的身份，擦掉永久丢失。触发场景：产线 IDF 比固件新 → NVS 格式被读成 `NEW_VERSION_FOUND` → 开机静默擦除 → SN 从 MAC 自补回来（日志看着正常）→ key/pk 永久没了，台账说已激活、卡说没有 | **不擦除**，直接 `ESP_LOGE` 降级返回 |
| I5 | 同上 | `esp_read_mac` 在两处早退 `return` 之后 | 早退路径上 SN 为空，违反"SN 恒有效"约定 | MAC 读取提到函数最前，NVS 值读进临时变量后再覆盖 |
| I3 | T5 `copy_out()` | `if (n+1 > cap) return -1;` 不碰 buf | 违反头文件"始终以 `'\0'` 结尾"承诺 | `buf[0]='\0'` 提到所有 return 之前 |
| I2 | T5 `load_str()` | 所有非 OK 都置空 | `INVALID_LENGTH`（值超长）与"键不存在"无法区分，操作员会反复重烧同一个超长 bin | 单独 `ESP_LOGE` 报出键名/实际长度/上限 |
| I4 | T5 `hal_identity_set_fields()` | 无长度校验 | NVS 存全长而内存副本被 `strncpy` 截断，重启前后签名行为不一致 | 入口 `strlen >= CAP` 校验，超长拒绝 |
| I6 | T3 `prov_cmd.c` `copy_val()` | 接受任意字节 | 操作员按退格键 → `key` 里混进 `0x08`，而 `AT+CARDID?` 从不回显 key，**永远发现不了** | 只接受 `0x21..0x7E`，新增 `PROV_ERR_BAD_CHAR` |
| — | T3 `parse_u32()` | 无溢出保护 | `AT+TOKENSEQ=99999999999` 回 `+OK` 却写入 1215752191 | 加溢出检查，超限返回 `BAD_VALUE` |
| — | T7 `PROV_LINE_MAX` | 原名 `LINE_MAX` | 与工具链 `sys/syslimits.h` 的 `LINE_MAX=2048` 冲突 | 已在正文改名（见 T7） |
| — | T8 `app.c` 接线顺序 | 串口任务启动在 profile 载入之前 | 该窗口内的 `AT+TOKENSEQ=` 回了 `+OK` 但随即被 `profile_deserialize` 覆盖 | 移到 profile 载入之后 |
| — | T8/T7 | `publish_identity()` 只在开机调一次 | `AT+CARDID=key=<新值>` 后 BLE `…0012` 仍吐旧 key 直到重启（T10 Step 2→4 会读到脏数据） | 新增 `provision_console_set_changed_cb`，写入成功后回调 app 重推 blob |
| I8 | T9 `make_cardid.py` | `--pk` 必填命令行参数 | 全厂共享密钥进 `ps aux`/shell 历史/MES 作业日志 | 改为 `--pk-file` > `CARDID_PK` 环境变量 > `--pk`（带告警）；台账改 `0600` |
| I9 | — | HMAC 密钥编码口径未写明 | 网关拿到形似 hex 的 pk 会顺手 `bytes.fromhex()`，签名永不相符且两边都无日志 | 已写进 `hal_identity.h` 与 spec §10 |
| I7 | — | README 缺 erase-flash 警告（spec §4 要求） | 计划 T8 Step 6 刚让人执行过 erase-flash，习惯已养成 | 已在 `README.md` 顶部加醒目警告 |

**已知未处理**（评估后接受）：
- `on_set_token_seq`（串口任务）与 `on_cfg_message`（NimBLE host 任务）都会改 `s_profile` 并写 NVS，
  并发时存在竞态。概率极低（都需人工触发且要同时发生），暂不加锁。
- `provision_console.c` 任务栈 3072 字节，含 `printf` + `nvs_commit` 调用链，
  未实测余量。**T10 联调时应加一次 `uxTaskGetStackHighWaterMark` 读数。**

**环境约定：**
- 本仓库**非 git**：所有 Commit 步骤跳过，以 `idf.py -C /home/cjiio/trae_card build` 通过为判据。
- `host_test` 已知损坏：纯逻辑测试文件照写并挂进 `test/CMakeLists.txt`，验证以固件 build + 硬件联调为准。
- **新增分区 → 首次烧录必须 `idf.py -C /home/cjiio/trae_card erase-flash` 一次**（分区表变更）。**此后严禁再 erase-flash**，否则身份被擦。

---

## 文件结构

| 文件 | 责任 | 动作 |
|---|---|---|
| `partitions.csv` | 增加 `cardid` 分区 | 修改 |
| `components/core/services/include/services/sn_format.h` / `src/sn_format.c` | MAC→SN 字符串（纯逻辑） | 新增 |
| `components/core/services/include/services/prov_cmd.h` / `src/prov_cmd.c` | AT 指令行解析（纯逻辑） | 新增 |
| `components/core/services/test/test_sn_format.c` / `test_prov_cmd.c` | Unity 测试 | 新增 |
| `components/core/services/CMakeLists.txt` / `test/CMakeLists.txt` | 注册源/测试 | 修改 |
| `components/core/ports/include/hal/hal_identity.h` | 只读身份端口（含 hmac，**无 get_product**） | 新增 |
| `components/core/ports/include/hal/hal_identity_provision.h` | 写入端口（独立头，BLE 层看不见） | 新增 |
| `components/core/ports/include/hal/hal_config.h` | 加 `set_identity` | 修改 |
| `components/platform/platform_esp32/src/identity_nvs.c` | 唯一实现：读 cardid 分区、SN 自补、MAC 比对、HMAC | 新增 |
| `components/platform/platform_esp32/src/provision_console.c` | USB 串口指令任务 | 新增 |
| `components/platform/platform_esp32/src/ble_config.c` | 加只读特征 `…0012` + `set_identity` | 修改 |
| `components/platform/platform_esp32/include/platform/platform_factory.h` | 声明 `platform_create_identity` / console 启动 | 修改 |
| `components/platform/platform_esp32/CMakeLists.txt` | 加源文件 + mbedtls / esp_driver_usb_serial_jtag | 修改 |
| `components/app/src/app.c` | 接线、自检日志、组装 72 字节 blob、TOKENSEQ 回调 | 修改 |
| `tools/make_cardid.py` | 产线：读 MAC → 生成 bin → 烧录 → 追加台账 | 新增 |

**依赖顺序：** T1（分区）→ T2（sn_format）→ T3（prov_cmd）→ T4（端口头）→ T5（identity_nvs）→ T6（BLE 特征）→ T7（串口任务）→ T8（app 接线）→ T9（产线脚本）→ T10（联调）。

T2、T3 是纯新增，独立可 build。T9 是 Python，与固件无依赖，可随时做。

**不在本计划范围内**：spec §10「给 token 广播的接口」提到的 `token_mac_esp.c` 与
`token_bcast_init()` 接线属于 **token 广播特性**，由那份计划实现。本计划的交付边界是
把 `hal_identity_hmac()` 做出来并验证可用——token 侧只需注入一个 20 行的函数指针包装。

---

## Task 1: 增加 `cardid` 分区

**Files:** Modify `partitions.csv`

- [ ] **Step 1: 追加分区行**

`partitions.csv` 改为：

```csv
# Name,   Type, SubType, Offset,  Size
nvs,      data, nvs,     ,        0x6000,
phy_init, data, phy,     ,        0x1000,
factory,  app,  factory, ,        0x300000,
imgstore, data, spiffs,  ,        0x20000,
imgframe, data, spiffs,  ,        0x26000,
cardid,   data, nvs,     ,        0x4000,
```

沿用现有的自动偏移写法（Offset 列留空）。

- [ ] **Step 2: build 并确认偏移量**

```bash
idf.py -C /home/cjiio/trae_card build
```

Expected: build 成功。构建日志里会打印分区表。

- [ ] **Step 3: 核对 cardid 的实际偏移必须是 0x356000**

```bash
python3 /home/cjiio/.espressif/v5.5.3/esp-idf/components/partition_table/gen_esp32part.py \
    /home/cjiio/trae_card/build/partition_table/partition-table.bin
```

Expected 输出中包含这一行（偏移必须是 `0x356000`，产线脚本硬编码了这个值）：

```
cardid,data,nvs,0x356000,16K,
```

若偏移不是 `0x356000`，**不要改脚本去迁就**，先检查前面的分区是否被改动过——`0x356000` 必须落在 4MB 以内，否则 4MB 开发板上无法验证产线流程。

---

## Task 2: `sn_format` —— MAC 转 SN 字符串（纯逻辑）

**Files:**
- Create: `components/core/services/include/services/sn_format.h`
- Create: `components/core/services/src/sn_format.c`
- Test: `components/core/services/test/test_sn_format.c`
- Modify: `components/core/services/CMakeLists.txt`、`components/core/services/test/CMakeLists.txt`

- [ ] **Step 1: 写头文件**

`components/core/services/include/services/sn_format.h`：

```c
// components/core/services/include/services/sn_format.h —— MAC → SN 字符串(纯逻辑)
#pragma once
#include <stdint.h>

#define SN_STR_LEN 12   // 不含结尾 '\0'

// 把 6 字节 MAC 转成 SN 字符串:小写十六进制、无冒号、恒 12 字符 + '\0'。
// 顺序按 mac[0] 在最左(与 esptool.py read_mac 的打印顺序一致),不反转。
void sn_from_mac(const uint8_t mac[6], char out[SN_STR_LEN + 1]);
```

- [ ] **Step 2: 写失败的测试**

`components/core/services/test/test_sn_format.c`：

```c
#include "unity.h"
#include "services/sn_format.h"
#include <string.h>

TEST_CASE("typical mac formats lowercase without colons", "[sn_format]") {
    const uint8_t mac[6] = {0x8c, 0xbf, 0xea, 0x89, 0xf2, 0x2c};
    char sn[SN_STR_LEN + 1];
    sn_from_mac(mac, sn);
    TEST_ASSERT_EQUAL_STRING("8cbfea89f22c", sn);
}

TEST_CASE("zero and high bytes are zero-padded", "[sn_format]") {
    const uint8_t mac[6] = {0x00, 0x0f, 0xff, 0x10, 0xa0, 0x01};
    char sn[SN_STR_LEN + 1];
    sn_from_mac(mac, sn);
    TEST_ASSERT_EQUAL_STRING("000fff10a001", sn);
}

TEST_CASE("output is always 12 chars and NUL terminated", "[sn_format]") {
    const uint8_t mac[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
    char sn[SN_STR_LEN + 2];
    memset(sn, 'X', sizeof sn);
    sn_from_mac(mac, sn);
    TEST_ASSERT_EQUAL_INT(SN_STR_LEN, (int)strlen(sn));
    TEST_ASSERT_EQUAL_CHAR('\0', sn[SN_STR_LEN]);
    TEST_ASSERT_EQUAL_CHAR('X', sn[SN_STR_LEN + 1]);   // 不越界写
}

TEST_CASE("byte order is not reversed", "[sn_format]") {
    const uint8_t mac[6] = {0x01, 0x02, 0x03, 0x04, 0x05, 0x06};
    char sn[SN_STR_LEN + 1];
    sn_from_mac(mac, sn);
    TEST_ASSERT_EQUAL_STRING("010203040506", sn);   // 不是 "060504030201"
}
```

- [ ] **Step 3: 写实现**

`components/core/services/src/sn_format.c`：

```c
// components/core/services/src/sn_format.c
#include "services/sn_format.h"

static const char HEX[] = "0123456789abcdef";

void sn_from_mac(const uint8_t mac[6], char out[SN_STR_LEN + 1]) {
    for (int i = 0; i < 6; i++) {
        out[i * 2]     = HEX[(mac[i] >> 4) & 0x0F];
        out[i * 2 + 1] = HEX[mac[i] & 0x0F];
    }
    out[SN_STR_LEN] = '\0';
}
```

用查表而不是 `snprintf("%02x")`：无 libc 依赖、无格式化开销、行为完全确定，且 host 测试与固件行为逐字节一致。

- [ ] **Step 4: 注册到构建**

`components/core/services/CMakeLists.txt` 的 `SRCS` 追加 `"src/sn_format.c"`：

```cmake
# components/services/CMakeLists.txt
idf_component_register(
    SRCS "src/led_service.c" "src/audio_service.c" "src/ui_model.c"
         "src/avatar.c" "src/profile_ctl.c" "src/ble_match.c" "src/config_json.c"
         "src/frame_reasm.c" "src/jpeg_rx.c"
         "src/score_rx.c" "src/rtttl.c" "src/square_synth.c"
         "src/sn_format.c"
    INCLUDE_DIRS "include"
    REQUIRES ports json)
```

`components/core/services/test/CMakeLists.txt` 的 `SRCS` 追加 `"test_sn_format.c"`。

- [ ] **Step 5: build 验证**

```bash
idf.py -C /home/cjiio/trae_card build
```

Expected: build 成功，无警告。

---

## Task 3: `prov_cmd` —— AT 指令行解析（纯逻辑）

**Files:**
- Create: `components/core/services/include/services/prov_cmd.h`
- Create: `components/core/services/src/prov_cmd.c`
- Test: `components/core/services/test/test_prov_cmd.c`
- Modify: 两个 `CMakeLists.txt`

- [ ] **Step 1: 写头文件**

`components/core/services/include/services/prov_cmd.h`：

```c
// components/core/services/include/services/prov_cmd.h —— 产线/返修 AT 指令解析(纯逻辑)
#pragma once
#include <stdint.h>
#include <stdbool.h>

#define PROV_KEY_MAX 32
#define PROV_PK_MAX  16
#define PROV_HW_MAX  8

typedef enum {
    PROV_CMD_NONE = 0,   // 空行 / 无法识别
    PROV_CMD_SET,        // AT+CARDID=key=..[,pk=..][,hw=..]
    PROV_CMD_QUERY,      // AT+CARDID?
    PROV_CMD_TOKENSEQ,   // AT+TOKENSEQ=<n>
} prov_kind_t;

typedef enum {
    PROV_ERR_NONE = 0,
    PROV_ERR_UNKNOWN,         // 不认识的指令
    PROV_ERR_MISSING_PARAM,   // 一个可写字段都没给,或某字段值为空
    PROV_ERR_TOO_LONG,        // 某字段值超长
    PROV_ERR_SN_READONLY,     // 传了 sn= —— SN 由 MAC 决定,运行时不可改
    PROV_ERR_BAD_VALUE,       // TOKENSEQ 的值不是十进制数字
} prov_err_t;

typedef struct {
    prov_kind_t kind;
    prov_err_t  err;
    bool     has_key, has_pk, has_hw;
    char     key[PROV_KEY_MAX + 1];
    char     pk [PROV_PK_MAX  + 1];
    char     hw [PROV_HW_MAX  + 1];
    uint32_t token_seq;       // 仅 PROV_CMD_TOKENSEQ 有效
} prov_cmd_t;

// 解析一行(可含结尾 CR/LF,会被去掉)。out 必填,函数内部先清零。
// 指令前缀不区分大小写;**参数值原样保留大小写**(Key 大小写敏感)。
void prov_parse(const char *line, int len, prov_cmd_t *out);

// 错误码 → 回给串口的字符串(用于 "+ERR=<原因>")。
const char *prov_err_str(prov_err_t e);
```

- [ ] **Step 2: 写失败的测试**

`components/core/services/test/test_prov_cmd.c`：

```c
#include "unity.h"
#include "services/prov_cmd.h"
#include <string.h>

static void parse(const char *s, prov_cmd_t *c) { prov_parse(s, (int)strlen(s), c); }

TEST_CASE("set with three fields", "[prov_cmd]") {
    prov_cmd_t c; parse("AT+CARDID=key=B2,pk=C3,hw=D4", &c);
    TEST_ASSERT_EQUAL_INT(PROV_CMD_SET, c.kind);
    TEST_ASSERT_EQUAL_INT(PROV_ERR_NONE, c.err);
    TEST_ASSERT_TRUE(c.has_key); TEST_ASSERT_EQUAL_STRING("B2", c.key);
    TEST_ASSERT_TRUE(c.has_pk);  TEST_ASSERT_EQUAL_STRING("C3", c.pk);
    TEST_ASSERT_TRUE(c.has_hw);  TEST_ASSERT_EQUAL_STRING("D4", c.hw);
}

TEST_CASE("partial update: only key given", "[prov_cmd]") {
    prov_cmd_t c; parse("AT+CARDID=key=B2", &c);
    TEST_ASSERT_EQUAL_INT(PROV_CMD_SET, c.kind);
    TEST_ASSERT_EQUAL_INT(PROV_ERR_NONE, c.err);
    TEST_ASSERT_TRUE(c.has_key);
    TEST_ASSERT_FALSE(c.has_pk);      // 局部更新:未给的字段不写,不擦分区
    TEST_ASSERT_FALSE(c.has_hw);
}

TEST_CASE("param order does not matter", "[prov_cmd]") {
    prov_cmd_t c; parse("AT+CARDID=hw=D4,key=B2,pk=C3", &c);
    TEST_ASSERT_EQUAL_STRING("B2", c.key);
    TEST_ASSERT_EQUAL_STRING("C3", c.pk);
    TEST_ASSERT_EQUAL_STRING("D4", c.hw);
}

TEST_CASE("no writable field at all is missing_param", "[prov_cmd]") {
    prov_cmd_t c; parse("AT+CARDID=", &c);
    TEST_ASSERT_EQUAL_INT(PROV_ERR_MISSING_PARAM, c.err);
}

TEST_CASE("empty value is missing_param", "[prov_cmd]") {
    prov_cmd_t c; parse("AT+CARDID=key=,pk=C3", &c);
    TEST_ASSERT_EQUAL_INT(PROV_ERR_MISSING_PARAM, c.err);
}

TEST_CASE("sn parameter is rejected loudly, not ignored", "[prov_cmd]") {
    prov_cmd_t c; parse("AT+CARDID=sn=deadbeef,key=B2", &c);
    TEST_ASSERT_EQUAL_INT(PROV_ERR_SN_READONLY, c.err);
    TEST_ASSERT_FALSE(c.has_key);     // 整条指令拒绝,不做部分生效
}

TEST_CASE("command prefix is case insensitive but values are not", "[prov_cmd]") {
    prov_cmd_t c; parse("at+cardid=key=AbCd", &c);
    TEST_ASSERT_EQUAL_INT(PROV_CMD_SET, c.kind);
    TEST_ASSERT_EQUAL_STRING("AbCd", c.key);   // 不能被小写化
}

TEST_CASE("over-long key is too_long", "[prov_cmd]") {
    // 33 个字符,超过 PROV_KEY_MAX=32
    prov_cmd_t c; parse("AT+CARDID=key=123456789012345678901234567890123", &c);
    TEST_ASSERT_EQUAL_INT(PROV_ERR_TOO_LONG, c.err);
}

TEST_CASE("key of exactly max length is accepted", "[prov_cmd]") {
    prov_cmd_t c; parse("AT+CARDID=key=12345678901234567890123456789012", &c);
    TEST_ASSERT_EQUAL_INT(PROV_ERR_NONE, c.err);
    TEST_ASSERT_EQUAL_INT(32, (int)strlen(c.key));
}

TEST_CASE("query command", "[prov_cmd]") {
    prov_cmd_t c; parse("AT+CARDID?", &c);
    TEST_ASSERT_EQUAL_INT(PROV_CMD_QUERY, c.kind);
    TEST_ASSERT_EQUAL_INT(PROV_ERR_NONE, c.err);
}

TEST_CASE("tokenseq command", "[prov_cmd]") {
    prov_cmd_t c; parse("AT+TOKENSEQ=0", &c);
    TEST_ASSERT_EQUAL_INT(PROV_CMD_TOKENSEQ, c.kind);
    TEST_ASSERT_EQUAL_INT(PROV_ERR_NONE, c.err);
    TEST_ASSERT_EQUAL_UINT32(0u, c.token_seq);
}

TEST_CASE("tokenseq with non-numeric value", "[prov_cmd]") {
    prov_cmd_t c; parse("AT+TOKENSEQ=abc", &c);
    TEST_ASSERT_EQUAL_INT(PROV_ERR_BAD_VALUE, c.err);
}

TEST_CASE("trailing CRLF is stripped", "[prov_cmd]") {
    prov_cmd_t c; parse("AT+CARDID=key=B2\r\n", &c);
    TEST_ASSERT_EQUAL_STRING("B2", c.key);
}

TEST_CASE("garbage inputs do not crash", "[prov_cmd]") {
    prov_cmd_t c;
    parse("", &c);        TEST_ASSERT_EQUAL_INT(PROV_CMD_NONE, c.kind);
    parse("AT+", &c);     TEST_ASSERT_EQUAL_INT(PROV_ERR_UNKNOWN, c.err);
    parse("hello", &c);   TEST_ASSERT_EQUAL_INT(PROV_ERR_UNKNOWN, c.err);
    parse("\r\n", &c);    TEST_ASSERT_EQUAL_INT(PROV_CMD_NONE, c.kind);
}
```

- [ ] **Step 3: 写实现**

`components/core/services/src/prov_cmd.c`：

```c
// components/core/services/src/prov_cmd.c
#include "services/prov_cmd.h"
#include <string.h>

// 大小写不敏感的前缀比较。s 长度 len,pfx 以 '\0' 结尾。
static int ci_prefix(const char *s, int len, const char *pfx) {
    int n = (int)strlen(pfx);
    if (len < n) return 0;
    for (int i = 0; i < n; i++) {
        char a = s[i], b = pfx[i];
        if (a >= 'A' && a <= 'Z') a = (char)(a + 32);
        if (b >= 'A' && b <= 'Z') b = (char)(b + 32);
        if (a != b) return 0;
    }
    return 1;
}

// 键名精确匹配(长度也要等),大小写不敏感。
static int key_is(const char *k, int klen, const char *name) {
    return klen == (int)strlen(name) && ci_prefix(k, klen, name);
}

// 拷贝值。返回 0=空值(missing) / -1=超长 / 1=成功。**原样拷贝,不改大小写。**
static int copy_val(char *dst, int cap, const char *v, int vlen) {
    if (vlen <= 0)   return 0;
    if (vlen > cap)  return -1;
    memcpy(dst, v, (size_t)vlen);
    dst[vlen] = '\0';
    return 1;
}

static uint32_t parse_u32(const char *s, int len, int *ok) {
    uint32_t v = 0;
    if (len <= 0) { *ok = 0; return 0; }
    for (int i = 0; i < len; i++) {
        if (s[i] < '0' || s[i] > '9') { *ok = 0; return 0; }
        v = v * 10u + (uint32_t)(s[i] - '0');
    }
    *ok = 1;
    return v;
}

void prov_parse(const char *line, int len, prov_cmd_t *out) {
    memset(out, 0, sizeof *out);
    if (line == NULL) { out->err = PROV_ERR_UNKNOWN; return; }

    // 去首尾空白与 CR/LF
    while (len > 0 && (line[len - 1] == '\r' || line[len - 1] == '\n' ||
                       line[len - 1] == ' '  || line[len - 1] == '\t')) len--;
    while (len > 0 && (*line == ' ' || *line == '\t')) { line++; len--; }
    if (len == 0) { out->kind = PROV_CMD_NONE; return; }

    if (len == 10 && ci_prefix(line, len, "AT+CARDID?")) {
        out->kind = PROV_CMD_QUERY;
        return;
    }
    if (ci_prefix(line, len, "AT+TOKENSEQ=")) {
        int ok = 0;
        out->kind = PROV_CMD_TOKENSEQ;
        out->token_seq = parse_u32(line + 12, len - 12, &ok);
        if (!ok) out->err = PROV_ERR_BAD_VALUE;
        return;
    }
    if (!ci_prefix(line, len, "AT+CARDID=")) {
        out->kind = PROV_CMD_NONE;
        out->err  = PROV_ERR_UNKNOWN;
        return;
    }

    out->kind = PROV_CMD_SET;
    const char *p = line + 10, *end = line + len;
    while (p < end) {
        const char *seg_end = (const char *)memchr(p, ',', (size_t)(end - p));
        if (seg_end == NULL) seg_end = end;
        const char *eq = (const char *)memchr(p, '=', (size_t)(seg_end - p));
        if (eq != NULL) {
            int         klen = (int)(eq - p);
            const char *v    = eq + 1;
            int         vlen = (int)(seg_end - v);
            int r = 1;
            if (key_is(p, klen, "sn")) {
                // SN 由 MAC 决定。报错而不是静默忽略 —— 静默会让操作员
                // 以为改成功了,直到后面绑定对不上才发现。
                out->err = PROV_ERR_SN_READONLY;
                return;
            } else if (key_is(p, klen, "key")) {
                r = copy_val(out->key, PROV_KEY_MAX, v, vlen);
                if (r == 1) out->has_key = true;
            } else if (key_is(p, klen, "pk")) {
                r = copy_val(out->pk, PROV_PK_MAX, v, vlen);
                if (r == 1) out->has_pk = true;
            } else if (key_is(p, klen, "hw")) {
                r = copy_val(out->hw, PROV_HW_MAX, v, vlen);
                if (r == 1) out->has_hw = true;
            }
            // 未知键静默忽略(向后兼容将来新增字段)
            if (r == 0)  { out->err = PROV_ERR_MISSING_PARAM; return; }
            if (r == -1) { out->err = PROV_ERR_TOO_LONG;      return; }
        }
        p = (seg_end < end) ? seg_end + 1 : end;
    }
    if (!out->has_key && !out->has_pk && !out->has_hw) out->err = PROV_ERR_MISSING_PARAM;
}

const char *prov_err_str(prov_err_t e) {
    switch (e) {
        case PROV_ERR_NONE:          return "none";
        case PROV_ERR_MISSING_PARAM: return "missing_param";
        case PROV_ERR_TOO_LONG:      return "too_long";
        case PROV_ERR_SN_READONLY:   return "sn_readonly";
        case PROV_ERR_BAD_VALUE:     return "bad_value";
        default:                     return "unknown";
    }
}
```

- [ ] **Step 4: 注册到构建**

`components/core/services/CMakeLists.txt` 的 `SRCS` 追加 `"src/prov_cmd.c"`。
`components/core/services/test/CMakeLists.txt` 的 `SRCS` 追加 `"test_prov_cmd.c"`。

- [ ] **Step 5: build 验证**

```bash
idf.py -C /home/cjiio/trae_card build
```

Expected: build 成功。

---

## Task 4: `hal_identity` 与 `hal_identity_provision` 端口

**Files:**
- Create: `components/core/ports/include/hal/hal_identity.h`
- Create: `components/core/ports/include/hal/hal_identity_provision.h`
- Modify: `components/core/ports/include/hal/hal_config.h`

- [ ] **Step 1: 只读端口**

`components/core/ports/include/hal/hal_identity.h`：

```c
// components/core/ports/include/hal/hal_identity.h —— 设备身份(只读)
// 注意:**没有 get_product()**。ProductKey 是 token 广播的 HMAC 共享密钥,
// 一旦被读走就能伪造全场广播,因此只暴露 hmac(),密钥不出适配器边界。
#pragma once
#include <stdint.h>
#include <stdbool.h>

typedef struct hal_identity_s hal_identity_t;

typedef struct {
    // 取字符串到 buf,返回实际长度(不含 '\0');缺失或 cap 不足返回 <0。始终以 '\0' 结尾。
    // get_sn 例外:SN 来自 eFuse MAC(缺失时自补),恒可用,与是否烧录 Key 无关。
    int  (*get_sn)     (hal_identity_t *self, char *buf, int cap);
    int  (*get_key)    (hal_identity_t *self, char *buf, int cap);
    int  (*get_hw_ver) (hal_identity_t *self, char *buf, int cap);

    // Key 与 ProductKey 均非空 → true。SN/HwVer 不参与判定。
    bool (*is_provisioned)(hal_identity_t *self);

    // 用 ProductKey 对 msg[0..len) 算 HMAC-SHA256,截断写入 out[0..out_cap)。
    // out_cap 最大 32。成功返回 0;未烧录 ProductKey 或参数非法返回 <0。
    int  (*hmac)(hal_identity_t *self, const uint8_t *msg, int len,
                 uint8_t *out, int out_cap);
} hal_identity_api_t;

struct hal_identity_s { const hal_identity_api_t *api; void *impl; };

static inline int hal_identity_get_sn(hal_identity_t *s, char *b, int c) {
    return s->api->get_sn(s, b, c);
}
static inline int hal_identity_get_key(hal_identity_t *s, char *b, int c) {
    return s->api->get_key(s, b, c);
}
static inline int hal_identity_get_hw_ver(hal_identity_t *s, char *b, int c) {
    return s->api->get_hw_ver(s, b, c);
}
static inline bool hal_identity_is_provisioned(hal_identity_t *s) {
    return s->api->is_provisioned(s);
}
static inline int hal_identity_hmac(hal_identity_t *s, const uint8_t *m, int len,
                                    uint8_t *out, int out_cap) {
    return s->api->hmac(s, m, len, out, out_cap);
}
```

- [ ] **Step 2: 写入端口（独立头文件）**

`components/core/ports/include/hal/hal_identity_provision.h`：

```c
// components/core/ports/include/hal/hal_identity_provision.h —— 设备身份(写入)
//
// 刻意与 hal_identity.h 分开:写入是"物理接触即授权"的高权限操作。
// BLE 层只 include hal_identity.h,于是在**编译期就看不见**写入函数,
// 误用需要主动加 include,会在 review 中显眼地暴露出来。
//
// 只有 provision_console.c 与 identity_nvs.c 自身应该 include 本文件。
#pragma once
#include "hal/hal_identity.h"

// 局部更新:仅非 NULL 的字段写入 NVS,其余键原样保留。**不擦分区**
// (擦分区会连 sn 一起抹掉)。成功返回 0,失败返回 <0。
// 没有 sn 参数 —— SN 由 eFuse MAC 决定,运行时不可改。
int hal_identity_set_fields(hal_identity_t *self,
                            const char *key, const char *pk, const char *hw);
```

- [ ] **Step 3: `hal_config` 增加 `set_identity`**

`components/core/ports/include/hal/hal_config.h`，在 `set_export` 后追加一项，并加对应 inline：

```c
typedef struct {
    void (*on_message)(hal_config_t *self, cfg_msg_cb_t cb, void *user);
    void (*notify_status)(hal_config_t *self, uint8_t type, uint8_t status);  // 回报处理结果
    void (*set_export)(hal_config_t *self, const uint8_t *data, int len);     // 头像导出 blob
    void (*set_identity)(hal_config_t *self, const uint8_t *data, int len);   // 身份只读 blob
} hal_config_api_t;
```

```c
static inline void hal_config_set_identity(hal_config_t *c, const uint8_t *data, int len) {
    if (c->api->set_identity) c->api->set_identity(c, data, len);
}
```

放在 `hal_config_set_export` 的 inline 定义之后。

**blob 由 app 层组装再推进来**，而不是让 `ble_config.c` 自己去读 `hal_identity`。这样 ProductKey 泄漏在结构上就不可能发生：app 层手里只有 `get_sn/get_key/get_hw_ver`，**根本拿不到 ProductKey**，所以它组装出的 blob 里不可能含有 ProductKey。

- [ ] **Step 4: build 验证**

```bash
idf.py -C /home/cjiio/trae_card build
```

Expected: build 成功（新增的 `set_identity` 字段在 `ble_config.c` 的 `API` 初始化里暂时缺省为 NULL，C 的部分初始化会补 0，合法）。

---

## Task 5: `identity_nvs.c` —— 唯一实现

**Files:**
- Create: `components/platform/platform_esp32/src/identity_nvs.c`
- Modify: `components/platform/platform_esp32/include/platform/platform_factory.h`
- Modify: `components/platform/platform_esp32/CMakeLists.txt`

- [ ] **Step 1: 写实现**

`components/platform/platform_esp32/src/identity_nvs.c`：

```c
// components/platform/platform_esp32/src/identity_nvs.c —— hal_identity 的 NVS 实现
// 独立 cardid 分区,与系统 nvs 物理隔离:任何"恢复出厂设置"擦的是 nvs,碰不到身份。
#include "platform/platform_factory.h"
#include "hal/hal_identity.h"
#include "hal/hal_identity_provision.h"
#include "services/sn_format.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "esp_mac.h"
#include "esp_log.h"
#include "mbedtls/md.h"
#include <string.h>

static const char *TAG = "identity";

#define CARDID_PART "cardid"
#define CARDID_NS   "id"

#define SN_CAP   (SN_STR_LEN + 1)   // 13
#define KEY_CAP  33
#define PK_CAP   17
#define HW_CAP   9

static char s_sn [SN_CAP];
static char s_key[KEY_CAP];
static char s_pk [PK_CAP];
static char s_hw [HW_CAP];
static bool s_ready;                 // 分区打开成功

// 读一个字符串键;不存在或为空 → dst[0]='\0'
static void load_str(nvs_handle_t h, const char *key, char *dst, size_t cap) {
    size_t sz = cap;
    dst[0] = '\0';
    if (nvs_get_str(h, key, dst, &sz) != ESP_OK) dst[0] = '\0';
}

static int copy_out(const char *src, char *buf, int cap) {
    if (buf == NULL || cap <= 0) return -1;
    int n = (int)strlen(src);
    if (n == 0) { buf[0] = '\0'; return -1; }
    if (n + 1 > cap) return -1;
    memcpy(buf, src, (size_t)n + 1);
    return n;
}

static int id_get_sn (hal_identity_t *s, char *b, int c) { (void)s; return copy_out(s_sn,  b, c); }
static int id_get_key(hal_identity_t *s, char *b, int c) { (void)s; return copy_out(s_key, b, c); }
static int id_get_hw (hal_identity_t *s, char *b, int c) { (void)s; return copy_out(s_hw,  b, c); }

static bool id_provisioned(hal_identity_t *s) {
    (void)s;
    return s_key[0] != '\0' && s_pk[0] != '\0';
}

// ProductKey 只在这里被使用,不提供任何 getter。
static int id_hmac(hal_identity_t *s, const uint8_t *msg, int len,
                   uint8_t *out, int out_cap) {
    (void)s;
    if (msg == NULL || len < 0 || out == NULL || out_cap <= 0 || out_cap > 32) return -1;
    if (s_pk[0] == '\0') return -2;

    const mbedtls_md_info_t *info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    if (info == NULL) return -3;

    uint8_t full[32];
    int rc = mbedtls_md_hmac(info, (const uint8_t *)s_pk, strlen(s_pk),
                             msg, (size_t)len, full);
    if (rc != 0) return -4;
    memcpy(out, full, (size_t)out_cap);
    return 0;
}

static const hal_identity_api_t API = {
    .get_sn         = id_get_sn,
    .get_key        = id_get_key,
    .get_hw_ver     = id_get_hw,
    .is_provisioned = id_provisioned,
    .hmac           = id_hmac,
};
static hal_identity_t s_handle = { .api = &API, .impl = NULL };

// 局部更新:只写非 NULL 的字段。**不擦分区** —— 擦了会连 sn 一起没。
int hal_identity_set_fields(hal_identity_t *self,
                            const char *key, const char *pk, const char *hw) {
    (void)self;
    if (!s_ready) return -1;
    nvs_handle_t h;
    if (nvs_open_from_partition(CARDID_PART, CARDID_NS, NVS_READWRITE, &h) != ESP_OK) return -1;

    esp_err_t e = ESP_OK;
    if (key && e == ESP_OK) e = nvs_set_str(h, "key", key);
    if (pk  && e == ESP_OK) e = nvs_set_str(h, "pk",  pk);
    if (hw  && e == ESP_OK) e = nvs_set_str(h, "hw",  hw);
    if (e == ESP_OK) e = nvs_commit(h);
    nvs_close(h);
    if (e != ESP_OK) { ESP_LOGE(TAG, "写入失败: 0x%x", e); return -1; }

    // 同步内存副本
    if (key) { strncpy(s_key, key, KEY_CAP - 1); s_key[KEY_CAP - 1] = '\0'; }
    if (pk)  { strncpy(s_pk,  pk,  PK_CAP  - 1); s_pk [PK_CAP  - 1] = '\0'; }
    if (hw)  { strncpy(s_hw,  hw,  HW_CAP  - 1); s_hw [HW_CAP  - 1] = '\0'; }
    ESP_LOGI(TAG, "身份字段已更新 (key=%s pk=%s hw=%s)",
             key ? "改" : "-", pk ? "改" : "-", hw ? "改" : "-");
    return 0;
}

// ProductKey 的 SHA256 前 4 字节十六进制,供 AT+CARDID? 回读校验。
// 回显指纹而非明文:串口输出常被产线工装记录、上传 MES,明文密钥落日志即泄漏。
void identity_pk_fingerprint(char out[9]) {
    out[0] = '\0';
    if (s_pk[0] == '\0') { strcpy(out, "--------"); return; }
    const mbedtls_md_info_t *info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    uint8_t d[32];
    if (info == NULL || mbedtls_md(info, (const uint8_t *)s_pk, strlen(s_pk), d) != 0) {
        strcpy(out, "--------");
        return;
    }
    static const char HEX[] = "0123456789abcdef";
    for (int i = 0; i < 4; i++) {
        out[i * 2]     = HEX[(d[i] >> 4) & 0x0F];
        out[i * 2 + 1] = HEX[d[i] & 0x0F];
    }
    out[8] = '\0';
}

hal_identity_t *platform_create_identity(const board_config_t *cfg) {
    (void)cfg;

    esp_err_t e = nvs_flash_init_partition(CARDID_PART);
    if (e == ESP_ERR_NVS_NO_FREE_PAGES || e == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "cardid 分区需要格式化 (0x%x)", e);
        nvs_flash_erase_partition(CARDID_PART);
        e = nvs_flash_init_partition(CARDID_PART);
    }
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "cardid 分区初始化失败 0x%x,身份功能不可用", e);
        return &s_handle;   // 句柄仍返回,所有字段为空 → is_provisioned()=false
    }
    s_ready = true;

    nvs_handle_t h;
    if (nvs_open_from_partition(CARDID_PART, CARDID_NS, NVS_READWRITE, &h) != ESP_OK) {
        ESP_LOGE(TAG, "cardid 命名空间打开失败");
        return &s_handle;
    }
    load_str(h, "sn",  s_sn,  SN_CAP);
    load_str(h, "key", s_key, KEY_CAP);
    load_str(h, "pk",  s_pk,  PK_CAP);
    load_str(h, "hw",  s_hw,  HW_CAP);

    // 本机 MAC 推导出的 SN,用于自补与一致性比对
    uint8_t mac[6];
    char mac_sn[SN_CAP];
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    sn_from_mac(mac, mac_sn);

    if (s_sn[0] == '\0') {
        // 自补:覆盖"分区没烧 / 产线漏烧 / bin 里 sn 字段为空"三种情况。
        // 自补值与产线 bin 写的完全一致(同一个 MAC、同一个格式),两条路径殊途同归。
        memcpy(s_sn, mac_sn, sizeof mac_sn);
        if (nvs_set_str(h, "sn", s_sn) == ESP_OK && nvs_commit(h) == ESP_OK) {
            ESP_LOGW(TAG, "SN 缺失,已按 WIFI_STA MAC 自补: %s", s_sn);
        } else {
            ESP_LOGE(TAG, "SN 自补写入失败,本次开机仅内存有效: %s", s_sn);
        }
    } else if (strcmp(s_sn, mac_sn) != 0) {
        // 只告警,不覆盖 —— NVS 里的 SN 是权威值,云端可能已按它建立绑定关系,
        // 固件擅自改写会让绑定凭空失效。
        // 这条专门拦"逐台生成的分区 bin 被烧到了另一台设备"这类产线事故:
        // 两台设备会有相同 SN,出厂测试完全看不出来,要等用户绑定冲突才暴露。
        ESP_LOGE(TAG, "SN(%s) 与本机 MAC(%s) 不符!可能是分区 bin 烧错设备,请核对台账",
                 s_sn, mac_sn);
    }
    nvs_close(h);

    ESP_LOGI(TAG, "身份载入: SN=%s hw=%s provisioned=%d",
             s_sn, s_hw[0] ? s_hw : "-", (int)id_provisioned(&s_handle));
    return &s_handle;
}
```

- [ ] **Step 2: 声明工厂函数**

`components/platform/platform_esp32/include/platform/platform_factory.h`：

顶部 include 区追加：

```c
#include "hal/hal_identity.h"
```

文件末尾追加：

```c
// 设备身份(cardid 分区)。始终可用(不受 PERIPH_* 影响)。
// 内部会在 SN 缺失时按 esp_read_mac(ESP_MAC_WIFI_STA) 自补。
hal_identity_t *platform_create_identity(const board_config_t *cfg);

// ProductKey 的 SHA256 前 4 字节十六进制(8 字符 + '\0'),供串口回读校验。
// 刻意只给指纹不给明文,见 identity_nvs.c 注释。
void identity_pk_fingerprint(char out[9]);
```

- [ ] **Step 3: 注册源文件与依赖**

`components/platform/platform_esp32/CMakeLists.txt`：

```cmake
# components/platform_esp32/CMakeLists.txt
idf_component_register(
    SRCS "src/disp_st7789.c" "src/lvgl_port_setup.c" "src/btn_iot_button.c"
         "src/led_ws2812.c" "src/audio_es8311.c" "src/platform_sys.c" "src/ble_config.c"
         "src/ble_scan.c" "src/kv_nvs.c" "src/jpeg_store.c" "src/jpeg_view.c"
         "src/identity_nvs.c"
    INCLUDE_DIRS "include"
    REQUIRES ports services driver esp_lcd
    PRIV_REQUIRES esp_lvgl_port led_strip button esp_codec_dev esp_hw_support bt nvs_flash
                  esp_driver_i2c esp_driver_i2s esp_partition esp_new_jpeg esp_adc
                  mbedtls)
```

`core/services` 的 `REQUIRES ports json` **保持不变**——mbedtls 只进 platform 层。

- [ ] **Step 4: build 验证**

```bash
idf.py -C /home/cjiio/trae_card build
```

Expected: build 成功。若报 `mbedtls/md.h` 找不到，确认 `PRIV_REQUIRES` 里加了 `mbedtls`。

---

## Task 6: BLE 只读特征 `…0012`

**Files:** Modify `components/platform/platform_esp32/src/ble_config.c`

- [ ] **Step 1: 加 UUID 与状态变量**

在 `ble_config.c` 的 UUID 定义区（第 26–29 行附近）追加：

```c
static const ble_uuid128_t g_ident_uuid  = TRAE_UUID128(0x12);   // 身份只读特征
```

在 `s_export_ptr` / `s_export_len` 声明之后追加：

```c
static const uint8_t *s_ident_ptr;
static int            s_ident_len;
```

- [ ] **Step 2: 加读回调**

在 `export_read()` 函数之后追加：

```c
// 身份读:返回 app 推入的 72 字节 blob(ver/flags/SN/Key/HwVer)。
// blob 由 app 组装 —— app 层只有 get_sn/get_key/get_hw_ver,拿不到 ProductKey,
// 所以结构上不可能把 ProductKey 泄漏到这里。
static int ident_read(uint16_t conn, uint16_t attr, struct ble_gatt_access_ctxt *ctxt, void *arg) {
    (void)conn; (void)attr; (void)arg;
    if (s_ident_ptr == NULL || s_ident_len <= 0) return 0;
    return os_mbuf_append(ctxt->om, s_ident_ptr, s_ident_len) == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
}
```

- [ ] **Step 3: 把特征加进 GATT 表**

把 `g_chrs` 数组扩容并注册新特征。`ble_config.c` 第 129 行：

```c
static struct ble_gatt_chr_def g_chrs[5];    // cmd, notify, export, identity, 结束哨兵
```

`build_gatt_table()` 里，把原来的哨兵那两行替换为：

```c
    // 3: 身份只读
    g_chrs[3].uuid = &g_ident_uuid.u;
    g_chrs[3].access_cb = ident_read;
    g_chrs[3].arg = NULL;
    g_chrs[3].flags = BLE_GATT_CHR_F_READ;
    g_chrs[3].descriptors = NULL;
    memset(&g_chrs[4], 0, sizeof g_chrs[4]);  // 结束哨兵
```

（原本是 `memset(&g_chrs[3], 0, sizeof g_chrs[3]);`，注意哨兵下标要跟着改到 4。忘记改哨兵下标会让 NimBLE 把未初始化内存当成特征定义，`ble_gatts_start` 直接 assert。）

- [ ] **Step 4: 实现并挂上 `set_identity`**

在 `cfg_set_export()` 之后追加：

```c
static void cfg_set_identity(hal_config_t *self, const uint8_t *data, int len) {
    (void)self; s_ident_ptr = data; s_ident_len = len;
}
```

并把它加进 API 表：

```c
static const hal_config_api_t API = {
    .on_message    = cfg_on_message,
    .notify_status = cfg_notify_status,
    .set_export    = cfg_set_export,
    .set_identity  = cfg_set_identity,
};
```

- [ ] **Step 5: build 验证**

```bash
idf.py -C /home/cjiio/trae_card build
```

Expected: build 成功。

---

## Task 7: `provision_console.c` —— USB 串口指令任务

**Files:**
- Create: `components/platform/platform_esp32/src/provision_console.c`
- Modify: `platform_factory.h`、`platform_esp32/CMakeLists.txt`

- [ ] **Step 1: 写实现**

`components/platform/platform_esp32/src/provision_console.c`：

```c
// components/platform/platform_esp32/src/provision_console.c
// USB-Serial-JTAG 上的产线/返修指令。主产线路径是烧录 cardid 分区 bin,
// 这里只用于返修补写与现场排查。
//
// 不引入 esp_console:它的 REPL 会带来 linenoise 缓冲 + 补全/历史表 + 4KB 栈任务,
// 而这块板子的静态 RAM 余量直接决定 NimBLE 能否初始化成功(有过大块静态缓冲
// 压垮 BLE 堆的事故)。3 条固定格式指令不需要那套框架。
#include "platform/platform_factory.h"
#include "hal/hal_identity.h"
#include "hal/hal_identity_provision.h"
#include "services/prov_cmd.h"
#include "driver/usb_serial_jtag.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include <stdio.h>
#include <string.h>

static const char *TAG = "prov";

// 不能叫 LINE_MAX —— 工具链的 sys/syslimits.h 已定义它为 2048,
// 经 board_config.h → spi_master.h → FreeRTOS → sys/param.h 传递进来,重定义会告警。
#define PROV_LINE_MAX 160

static hal_identity_t   *s_id;
static token_seq_set_fn  s_seq_cb;
static void             *s_seq_user;

void provision_console_set_token_seq_cb(token_seq_set_fn cb, void *user) {
    s_seq_cb = cb; s_seq_user = user;
}

static void handle_line(const char *line, int len) {
    prov_cmd_t c;
    prov_parse(line, len, &c);

    if (c.kind == PROV_CMD_NONE && c.err == PROV_ERR_NONE) return;   // 空行,静默

    if (c.err != PROV_ERR_NONE) {
        printf("+ERR=%s\r\n", prov_err_str(c.err));
        return;
    }

    switch (c.kind) {
        case PROV_CMD_SET: {
            int rc = hal_identity_set_fields(s_id,
                                             c.has_key ? c.key : NULL,
                                             c.has_pk  ? c.pk  : NULL,
                                             c.has_hw  ? c.hw  : NULL);
            printf(rc == 0 ? "+OK\r\n" : "+ERR=nvs_write\r\n");
            break;
        }
        case PROV_CMD_QUERY: {
            char sn[16] = {0}, hw[16] = {0}, fp[9];
            hal_identity_get_sn(s_id, sn, sizeof sn);
            hal_identity_get_hw_ver(s_id, hw, sizeof hw);
            identity_pk_fingerprint(fp);
            // 刻意不回显 key 与 pk 明文,只给 pk 指纹。
            printf("+CARDID: sn=%s,hw=%s,pk_fp=%s,provisioned=%d\r\n",
                   sn[0] ? sn : "-", hw[0] ? hw : "-", fp,
                   hal_identity_is_provisioned(s_id) ? 1 : 0);
            break;
        }
        case PROV_CMD_TOKENSEQ: {
            // provision_console 在 platform 层,profile.token_seq 在 app 层。
            // platform 不能反向依赖 app,故走 app 注册的回调。
            if (s_seq_cb == NULL) { printf("+ERR=not_supported\r\n"); break; }
            int rc = s_seq_cb(c.token_seq, s_seq_user);
            printf(rc == 0 ? "+OK\r\n" : "+ERR=nvs_write\r\n");
            break;
        }
        default:
            printf("+ERR=unknown\r\n");
            break;
    }
}

static void console_task(void *arg) {
    (void)arg;
    static char line[PROV_LINE_MAX];
    int have = 0;
    for (;;) {
        uint8_t ch;
        int n = usb_serial_jtag_read_bytes(&ch, 1, pdMS_TO_TICKS(100));
        if (n <= 0) continue;
        if (ch == '\n' || ch == '\r') {
            if (have > 0) { handle_line(line, have); have = 0; }
            continue;
        }
        if (have < PROV_LINE_MAX - 1) line[have++] = (char)ch;
        else have = 0;   // 超长行整条丢弃,避免半截指令被当成完整指令解析
    }
}

void provision_console_start(hal_identity_t *id) {
    s_id = id;
    usb_serial_jtag_driver_config_t cfg = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    esp_err_t e = usb_serial_jtag_driver_install(&cfg);
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "USB-Serial-JTAG 驱动安装失败 0x%x,产线指令不可用", e);
        return;
    }
    if (xTaskCreate(console_task, "prov", 3072, NULL, 3, NULL) != pdPASS) {
        ESP_LOGE(TAG, "指令任务创建失败(堆不足),产线指令不可用");
        return;
    }
    ESP_LOGI(TAG, "产线指令就绪: AT+CARDID= / AT+CARDID? / AT+TOKENSEQ=");
}
```

- [ ] **Step 2: 声明接口**

`platform_factory.h` 末尾追加：

```c
// AT+TOKENSEQ 的落地回调。profile.token_seq 属于 app 层,platform 不能反向依赖,
// 故由 app 在开机时注册。未注册时该指令回 +ERR=not_supported。
typedef int (*token_seq_set_fn)(uint32_t seq, void *user);
void provision_console_set_token_seq_cb(token_seq_set_fn cb, void *user);

// 启动 USB 串口产线/返修指令任务。须在 platform_create_identity 之后调用。
void provision_console_start(hal_identity_t *id);
```

**注意顺序**：`token_seq_set_fn` 的 typedef 必须出现在 `provision_console.c` include `platform_factory.h` 之后能看到的位置——把这两段放在文件末尾即可。

- [ ] **Step 3: 注册源文件与依赖**

`platform_esp32/CMakeLists.txt` 的 `SRCS` 追加 `"src/provision_console.c"`，`PRIV_REQUIRES` 追加 `esp_driver_usb_serial_jtag`：

```cmake
    SRCS ... "src/identity_nvs.c" "src/provision_console.c"
    ...
    PRIV_REQUIRES ... mbedtls esp_driver_usb_serial_jtag
```

- [ ] **Step 4: build 验证**

```bash
idf.py -C /home/cjiio/trae_card build
```

Expected: build 成功。

- [ ] **Step 5: 上板验证驱动与日志不冲突（这一步不能跳）**

烧录后接串口，确认两件事**同时**成立：

1. `ESP_LOG` 仍然正常输出（开机日志、`identity` 的 `身份载入:` 那行都能看到）
2. 敲 `AT+CARDID?` 回车，能收到 `+CARDID: ...` 响应

Spec §13 把这一步标为"需上板验证"：`usb_serial_jtag_driver_install()` 与
`CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y` 的默认输出路径是否冲突，无法在构建期确定。

**若日志消失或指令无响应**，改用 VFS 方案：去掉 `usb_serial_jtag_driver_install`，
改为在 `provision_console_start` 里调用 `usb_serial_jtag_vfs_use_driver()`（需
`#include "driver/usb_serial_jtag_vfs.h"`），并把 `console_task` 的读取换成
`fgets(line, PROV_LINE_MAX, stdin)`。两种方式的差异局限在这一个文件内。

---

## Task 8: `app.c` 接线

**Files:** Modify `components/app/src/app.c`

- [ ] **Step 1: 加 include 与静态变量**

在 `#if PERIPH_BLE` 的 include 区（`#include "services/profile_ctl.h"` 附近）追加：

```c
#include "hal/hal_identity.h"
```

在 `static hal_kv_t *s_kv;` 附近追加：

```c
static hal_identity_t *s_id;
#define IDENT_BLOB_SIZE 72
static uint8_t s_ident_blob[IDENT_BLOB_SIZE];
```

- [ ] **Step 2: 组装 72 字节身份 blob**

在 `publish_export()` 之后追加：

```c
// 组装并推送身份只读 blob(BLE 特征 …0012)。
// 布局: [0]ver [1]flags [2..25]SN(24B) [26..57]Key(32B) [58..65]HwVer(8B) [66..71]保留
// 未烧录时 flags bit0=0 且 Key/HwVer 全 0,**但 SN 仍然有效**(来自 MAC)。
// 小程序必须查 flags 判断是否激活,不能用"SN 非空"——SN 永远非空。
static void publish_identity(void) {
    if (!s_cfg || !s_id) return;
    memset(s_ident_blob, 0, sizeof s_ident_blob);
    s_ident_blob[0] = 0x01;                                        // ver
    s_ident_blob[1] = hal_identity_is_provisioned(s_id) ? 0x01 : 0x00;

    char tmp[40];
    if (hal_identity_get_sn(s_id, tmp, sizeof tmp) > 0)
        memcpy(&s_ident_blob[2], tmp, strnlen(tmp, 24));
    if (hal_identity_get_key(s_id, tmp, sizeof tmp) > 0)
        memcpy(&s_ident_blob[26], tmp, strnlen(tmp, 32));
    if (hal_identity_get_hw_ver(s_id, tmp, sizeof tmp) > 0)
        memcpy(&s_ident_blob[58], tmp, strnlen(tmp, 8));

    hal_config_set_identity(s_cfg, s_ident_blob, IDENT_BLOB_SIZE);
}
```

这里**没有也不可能有 ProductKey**——`hal_identity.h` 根本没提供读它的函数。

- [ ] **Step 3: 加 TOKENSEQ 回调**

在 `save_profile()` 之后追加：

```c
// AT+TOKENSEQ=<n> 的落地:写 profile.token_seq。
// token_seq 字段由 token 广播设计引入;若那部分尚未实现,本函数仍可编译
// (字段已在 profile_data_t 中)。
static int on_set_token_seq(uint32_t seq, void *user) {
    (void)user;
    s_profile.token_seq = seq;
    return save_profile() ? 0 : -1;
}
```

**若 `profile_data_t` 里还没有 `token_seq` 字段**（token 广播计划尚未执行），先在
`components/core/services/include/services/profile_ctl.h` 的结构体末尾追加
`uint32_t token_seq;` 并把 `PROFILE_BLOB_VER` 从 3 改为 4。这会让已烧录设备的
profile 重置为出厂默认一次，是 spec §8 已接受的代价。

- [ ] **Step 4: 在 `app_run()` 里接线**

在 `#if PERIPH_BLE` 块内、`profile_ctl_init(&s_profile);` 之前插入：

```c
    // 身份要在 BLE 之前建立:BLE 特征和 token 广播都依赖它。
    s_id = platform_create_identity(&board);
    {
        char sn[16] = {0};
        hal_identity_get_sn(s_id, sn, sizeof sn);
        if (!hal_identity_is_provisioned(s_id)) {
            ESP_LOGW(TAG, "未写入 Key/ProductKey,小程序绑定与 token 广播不可用。"
                          "请用 AT+CARDID= 指令写入或烧录 cardid 分区。SN=%s", sn);
        } else {
            ESP_LOGI(TAG, "身份自检通过 SN=%s", sn);
        }
    }
    provision_console_set_token_seq_cb(on_set_token_seq, NULL);
    provision_console_start(s_id);
```

在 `publish_export();` 这一行之后追加：

```c
    publish_identity();             // 推送身份 blob(BLE 特征 …0012)
```

- [ ] **Step 5: build 验证**

```bash
idf.py -C /home/cjiio/trae_card build
```

Expected: build 成功。

- [ ] **Step 6: 首次烧录（含 erase）**

```bash
idf.py -C /home/cjiio/trae_card erase-flash
idf.py -C /home/cjiio/trae_card flash
```

**这是最后一次允许 erase-flash。** 分区表变更必须擦一次；此后再 erase 会连身份一起擦掉。

- [ ] **Step 7: 验证开机自检与自补**

接串口看日志，Expected 出现（SN 为你这块板子的实际 MAC）：

```
W (xxx) identity: SN 缺失,已按 WIFI_STA MAC 自补: 8cbfea89f22c
I (xxx) identity: 身份载入: SN=8cbfea89f22c hw=- provisioned=0
W (xxx) app: 未写入 Key/ProductKey,... SN=8cbfea89f22c
I (xxx) prov: 产线指令就绪: AT+CARDID= / AT+CARDID? / AT+TOKENSEQ=
```

用 `esptool.py -p <PORT> read_mac` 读出的 MAC，去掉冒号转小写后**必须与日志里的 SN 完全一致**。

- [ ] **Step 8: 验证自补只发生一次**

重启设备（按复位键或重新上电）。Expected：**不再出现**"SN 缺失,已按 ... 自补"这条 WARN，只有 `身份载入: SN=...`。若每次开机都自补，说明 `nvs_commit` 没生效。

---

## Task 9: `tools/make_cardid.py` —— 产线脚本

**Files:** Create `tools/make_cardid.py`

- [ ] **Step 1: 写脚本**

`tools/make_cardid.py`：

```python
#!/usr/bin/env python3
# tools/make_cardid.py —— 产线:读 MAC → 生成 cardid 分区 bin → 烧录 → 追加台账
#
# 用法:
#   python3 tools/make_cardid.py --port /dev/ttyACM0 --pk <全厂共享密钥> --hw A1.0
#
# 依赖: ESP-IDF 环境(提供 esptool.py 与 nvs_partition_gen.py)
import argparse
import csv
import os
import re
import secrets
import subprocess
import sys
import tempfile
from datetime import datetime

# 必须与 partitions.csv 生成出的实际偏移一致。改动分区表后请重新核对:
#   python3 $IDF_PATH/components/partition_table/gen_esp32part.py build/partition_table/partition-table.bin
CARDID_OFFSET = "0x356000"
CARDID_SIZE   = "0x4000"
LEDGER = "cardid_ledger.csv"


def read_mac(port: str) -> str:
    """用 esptool 读 base MAC(= ESP_MAC_WIFI_STA),返回去冒号小写的 12 字符 SN。"""
    out = subprocess.run(
        [sys.executable, "-m", "esptool", "-p", port, "read_mac"],
        capture_output=True, text=True, check=True).stdout
    m = re.search(r"MAC:\s*((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})", out)
    if not m:
        raise RuntimeError("未能从 esptool 输出里解析出 MAC:\n" + out)
    return m.group(1).replace(":", "").lower()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True, help="串口,如 /dev/ttyACM0 或 COM3")
    ap.add_argument("--pk", required=True, help="全厂共享的 ProductKey(≤16 字符)")
    ap.add_argument("--hw", required=True, help="硬件版本,如 A1.0(≤8 字符)")
    ap.add_argument("--order", default="", help="工单号,写进台账")
    ap.add_argument("--dry-run", action="store_true", help="只生成 bin,不烧录不写台账")
    ap.add_argument("--keep-bin", default="", metavar="PATH",
                    help="把生成的 bin 另存到该路径(默认用临时目录,跑完即删)。"
                         "验证'烧错设备检测'时需要保留 bin,用这个参数")
    args = ap.parse_args()

    if len(args.pk) > 16:
        print("错误: pk 超过 16 字符", file=sys.stderr); return 2
    if len(args.hw) > 8:
        print("错误: hw 超过 8 字符", file=sys.stderr); return 2

    idf = os.environ.get("IDF_PATH")
    if not idf:
        print("错误: 未设置 IDF_PATH,请先 source export.sh", file=sys.stderr); return 2
    gen = os.path.join(idf, "components", "nvs_flash",
                       "nvs_partition_generator", "nvs_partition_gen.py")

    sn = read_mac(args.port)
    # 必须用 secrets 而不是 random:random 是梅森旋转,从少量输出即可反推内部状态、
    # 预测出整批设备的 Key。攻击者买几张卡读出 Key 就能算出全批 —— 这是个静默失效的
    # 错误,生成出来的东西看起来一样随机,只在被攻击时才暴露。
    key = secrets.token_hex(16)          # 16 字节熵 → 32 个十六进制字符,正好填满字段

    with tempfile.TemporaryDirectory() as tmp:
        csv_path = os.path.join(tmp, "cardid.csv")
        bin_path = os.path.join(tmp, "cardid.bin")
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["key", "type", "encoding", "value"])
            w.writerow(["id", "namespace", "", ""])
            w.writerow(["sn",  "data", "string", sn])
            w.writerow(["key", "data", "string", key])
            w.writerow(["pk",  "data", "string", args.pk])
            w.writerow(["hw",  "data", "string", args.hw])

        subprocess.run([sys.executable, gen, "generate",
                        csv_path, bin_path, CARDID_SIZE], check=True)

        # 临时目录跑完即删,要保留 bin 必须在退出 with 之前拷出去。
        if args.keep_bin:
            import shutil
            shutil.copyfile(bin_path, args.keep_bin)
            print(f"bin 已另存: {args.keep_bin}")

        if args.dry_run:
            print(f"[dry-run] SN={sn} KEY={key}")
            if not args.keep_bin:
                print("提示: 未加 --keep-bin,生成的 bin 已随临时目录删除")
            return 0

        subprocess.run([sys.executable, "-m", "esptool", "-p", args.port,
                        "write_flash", CARDID_OFFSET, bin_path], check=True)

    # 台账含明文 Key,等价于"全批设备的绑定口令表",按密钥材料管理:
    # 限权限、不进公共仓库、不随邮件明文发。pk 刻意不入台账 —— 它是全厂共享的,
    # 写进每份台账等于把泄漏面放大到每个产线批次。
    new = not os.path.exists(LEDGER)
    with open(LEDGER, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["sn", "key", "hw", "order", "timestamp"])
        w.writerow([sn, key, args.hw, args.order, datetime.now().isoformat(timespec="seconds")])

    print(f"OK  SN={sn}  已写入台账 {LEDGER}")
    print("请复位设备,串口应出现: 身份自检通过 SN=" + sn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 干跑验证（不接设备也能测生成逻辑）**

先单独验证 `nvs_partition_gen.py` 能吃下我们的 CSV：

```bash
cd /home/cjiio/trae_card
printf 'key,type,encoding,value\nid,namespace,,\nsn,data,string,8cbfea89f22c\nkey,data,string,0123456789abcdef0123456789abcdef\npk,data,string,testpk\nhw,data,string,A1.0\n' > /tmp/cardid_test.csv
python3 $IDF_PATH/components/nvs_flash/nvs_partition_generator/nvs_partition_gen.py \
    generate /tmp/cardid_test.csv /tmp/cardid_test.bin 0x4000
ls -l /tmp/cardid_test.bin
```

Expected: 生成成功，`cardid_test.bin` 大小为 16384 字节。

- [ ] **Step 3: 接设备实跑**

```bash
cd /home/cjiio/trae_card
python3 tools/make_cardid.py --port /dev/ttyACM0 --pk testpk123 --hw A1.0 --order T001
```

Expected: 输出 `OK  SN=<你的MAC>  已写入台账 cardid_ledger.csv`，且生成 `cardid_ledger.csv`。

- [ ] **Step 4: 复位并验证**

复位设备，串口 Expected：

```
I (xxx) identity: 身份载入: SN=8cbfea89f22c hw=A1.0 provisioned=1
I (xxx) app: 身份自检通过 SN=8cbfea89f22c
```

**不应**出现"SN 缺失,已按 ... 自补"，也**不应**出现"SN 与本机 MAC 不符"。

---

## Task 10: 整机联调验收

**Files:** 无（纯验证）

按 spec §12 的硬件联调清单逐条执行。每条都要实际看到预期输出才算过。

- [ ] **Step 1: 串口回读，确认不泄漏明文**

敲 `AT+CARDID?` 回车。Expected：

```
+CARDID: sn=8cbfea89f22c,hw=A1.0,pk_fp=<8位十六进制>,provisioned=1
```

**逐字检查输出里不含 key 的明文、也不含 pk 的明文。** 只有 `pk_fp` 指纹。

- [ ] **Step 2: 局部更新不擦其它字段**

```
AT+CARDID=key=newkey0123456789
```

Expected `+OK`。然后 `AT+CARDID?`，Expected：**sn 与 hw 保持不变**，`provisioned=1`。

这一步验证 spec §5 的"不擦分区"约束——若实现里误用了 `nvs_flash_erase_partition`，sn 会变空（下次开机被自补回来，但 hw 会永久丢失）。

- [ ] **Step 3: sn 只读**

```
AT+CARDID=sn=deadbeef1234
```

Expected：`+ERR=sn_readonly`。然后 `AT+CARDID?` 确认 sn **未改变**。

- [ ] **Step 4: BLE 特征内容**

用手机 nRF Connect（或小程序）连上 `TRAE-CARD`，读特征
`54524145-4341-5244-0000-000000000012`。Expected：

- 返回 **72 字节**
- `[0] = 0x01`（ver），`[1] = 0x01`（flags，已烧录）
- `[2..13]` 是 SN 的 ASCII（`38 63 62 66 ...` 对应 `8cbfea...`），`[14..25]` 为 `00`
- `[26..57]` 是 Key 的 ASCII
- **在这 72 字节里搜不到 pk 的任何字节**

最后一条是本任务最重要的验收点：ProductKey 一旦经 BLE 泄漏，token 广播的整个防伪设计归零。

- [ ] **Step 5: 固件重烧不丢身份**

```bash
idf.py -C /home/cjiio/trae_card flash
```

（**注意：不加 erase-flash**）复位后 Expected：`身份载入: SN=... hw=A1.0 provisioned=1`，
`AT+CARDID?` 内容与烧录前一致。

- [ ] **Step 6: 烧错设备检测**

把属于 A 板的 `cardid.bin` 烧到**另一块** B 板：

```bash
# 1) 接 A 板,生成并保留 bin(--dry-run 不烧录、不写台账)
python3 tools/make_cardid.py --port <A板端口> --pk testpk123 --hw A1.0 \
        --dry-run --keep-bin /tmp/a_board.bin

# 2) 把 A 板的 bin 烧到 B 板
python3 -m esptool -p <B板端口> write_flash 0x356000 /tmp/a_board.bin
```

B 板复位后 Expected 出现 ERROR：

```
E (xxx) identity: SN(<A板SN>) 与本机 MAC(<B板SN>) 不符!可能是分区 bin 烧错设备,请核对台账
```

且 `AT+CARDID?` 显示的仍是 **A 板的 SN**（只告警不覆盖——NVS 里的 SN 是权威值，
云端可能已按它建立绑定，固件擅自改写会让绑定凭空失效）。

- [ ] **Step 7: 4MB 开发板全流程**

在 4MB 开发板上重跑 Task 8 Step 6 → Task 10 Step 5。Expected 全部通过。

这一步验证 spec §4 把分区放在 `0x356000`（4MB 以内）的决定——若放在 flash 尾部，
开发板上根本没有那个地址，产线流程无法验证。

- [ ] **Step 8: MAC 差值实证（为 token 广播联调铺路）**

记录两个值：

1. `esptool.py -p <PORT> read_mac` 的输出（= WIFI_STA MAC = SN）
2. nRF Connect 扫描到 `TRAE-CARD` 时显示的蓝牙地址（= BT MAC）

Expected：**末字节相差 2**（如 SN 是 `...f22c`，广播地址是 `...F2:2E`）。

这不是可有可无的一步。它是"网关不能靠扫描广播来推 SN"这条结论的现场实证——
token 广播联调时如果对方想走捷径，把这两个值摆出来就能立刻说清。

---

## 完成标准

全部满足才算完成：

1. `idf.py -C /home/cjiio/trae_card build` 通过，无新增警告
2. Task 10 的 8 个步骤全部实测通过
3. BLE 72 字节 blob 里**搜不到 ProductKey**（Task 10 Step 4）
4. `AT+CARDID?` 输出里**没有任何明文密钥**（Task 10 Step 1）
5. 4MB 开发板与量产 8MB 板行为一致（Task 10 Step 7）
6. `cardid_ledger.csv` 已产生，含 sn/key/hw/order/timestamp 五列
