# 设计:BLE 控制档案卡信息(阶段一)

> 日期:2026-07-01
> 目标:用手机通用 BLE 工具(nRF Connect / LightBlue)通过写 GATT 特征,实时修改设备档案卡上的文字与数值信息(名字/角色/副标题/电量/等级/经验/在线)。
> 范围:**仅信息控制**。图片传输是独立的阶段二,不在本 spec。

---

## 1. 目标与验收标准

| 项 | 要求 |
| --- | --- |
| 手机端 | 通用 BLE 工具即可,无需自研 App。每个字段一个可写特征,写 UTF-8 文本 |
| 可控字段 | Name / Role / Subtitle(文字);Battery / Level / XP / XP_Max(整数);Online(布尔) |
| 分层合规 | NimBLE/IDF 只在 `platform_esp32`;解析校验在 `core/services`(可 PC 单测);渲染复用 `presentation` |
| 健壮 | 非法数字→忽略;超长字符串→截断;越界数值→钳位;断连→自动重广播 |
| 持久化 | 本期**不做**(临时,重启回默认) |
| 安全 | 本期**开放无配对**(任何设备连上即可写) |

---

## 2. 架构落点(六边形分层)

```
手机(nRF Connect)写特征
        │  NimBLE GATT(IDF 依赖锁在平台层)
platform_esp32/ble_config.c  ── 广播 + GATT 服务;特征写入 → 回调(field, bytes, len)
        │  实现
core/ports/hal/hal_config.h  ── 端口:cfg_field_t 枚举 + 写回调签名(纯头文件,零依赖)
        │
app/app.c(组装层)           ── 注册回调:profile_ctl_apply() 校验 → 持 LVGL 锁调 ui_profile_set_*
        │                         (回调在 NimBLE host task 上下文,操作 LVGL 须加锁,与按键回调一致)
core/services/profile_ctl.c  ── 纯逻辑:文本解析/校验/裁剪 + 字段缓存(REQUIRES ports;可 PC 单测)
        │
presentation/ui_profile.*     ── 复用现成 set_name/role/subtitle/battery/online/level
```

**依赖方向**:端口 `hal_config` 零依赖;`profile_ctl` 只依赖 `ports`;NimBLE 只出现在 `platform_esp32`;`app` 是唯一粘合处。

---

## 3. 端口:`core/ports/include/hal/hal_config.h`

```c
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

---

## 4. 业务:`core/services/profile_ctl.{h,c}`

纯逻辑:持有可编辑档案数据,把一次文本写入**解析 + 校验 + 裁剪**后落到缓存;不含 LVGL/BLE。

```c
// profile_ctl.h
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

void profile_ctl_init(profile_data_t *d);   // 填默认值(与界面初始一致)

// 应用一次字段写入。成功返回被改字段(cfg_field_t);非法/无变化返回 -1。
int  profile_ctl_apply(profile_data_t *d, cfg_field_t field, const char *text, int len);
```

**校验规则**
- 文本→整数:自写一个带边界的 `parse_int`(遇非数字字符→失败)。失败返回 -1,不改缓存。
- `battery` 钳位 0..100;`level` 钳位 0..999;`xp` ≥0;`xp_max` ≥1(避免进度条除零)。
- 字符串:按目标数组大小**截断**(保证 NUL 结尾)。
- `online`:`"1"/"on"/"true"`(大小写不敏感)→ true;`"0"/"off"/"false"` → false;其它→ -1。

---

## 5. 适配器:`platform_esp32/src/ble_config.c`(NimBLE)

- 初始化 NimBLE,设广播名 `TRAE-CARD`,注册一个 **128-bit 自定义 GATT 服务**,其下 8 个**可写特征**(每字段一个)。
- 特征 `access_cb` 收到写:取 `ctxt->om`(mbuf)数据与长度,调用已注册的 `cfg_write_cb_t(field, data, len, user)`。
- 每个特征加 **Characteristic User Description(0x2901)** 描述符,写友好名(如 "Name"、"Battery"),便于 nRF Connect 识别。
- 断连后自动重新广播。
- 工厂函数(平台 `platform_factory.h` 新增):
  ```c
  hal_config_t *platform_create_ble_config(const board_config_t *cfg);
  ```
- 受 `PERIPH_BLE` 编译开关门控(见 §7)。

**UUID 方案(ASCII "TRAECARD" 作基址,末字节为字段号)**
```
基址:54524145-4341-5244-0000-0000000000NN
服务   NN=00
Name   01   Role     02   Subtitle 03   Battery 04
Level  05   XP       06   XP_Max   07   Online  08
```

---

## 6. 组装层:`app/src/app.c`

```c
#if PERIPH_BLE
static profile_data_t s_profile;

static void on_cfg_write(cfg_field_t field, const char *text, int len, void *user) {
    (void)user;
    if (profile_ctl_apply(&s_profile, field, text, len) < 0) return;   // 非法忽略
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
在 `app_run()` 中(UI 建好之后):
```c
#if PERIPH_BLE
    profile_ctl_init(&s_profile);
    hal_config_t *cfg = platform_create_ble_config(&board);
    hal_config_on_write(cfg, on_cfg_write, NULL);
#endif
```

---

## 7. 编译开关与依赖

- `board_config.h` 增:`#define PERIPH_BLE 1` 与 `#define BLE_DEVICE_NAME "TRAE-CARD"`。
- `platform_esp32/CMakeLists.txt`:`SRCS` 加 `src/ble_config.c`;`PRIV_REQUIRES` 加 `bt`。
- `core/services/CMakeLists.txt`:`SRCS` 加 `src/profile_ctl.c`。
- `app` 已 `REQUIRES ports services presentation platform_esp32`,无需改。
- `sdkconfig.defaults` 增(启用 NimBLE,esp32c3):
  ```
  CONFIG_BT_ENABLED=y
  CONFIG_BT_NIMBLE_ENABLED=y
  ```
  (Bluedroid 默认关;NimBLE 外设角色默认开。约 +30~40KB RAM。)

---

## 8. 测试

- **`core/services/test/test_profile_ctl.c`(gcc + Unity,纯逻辑)**
  - `"82"`→battery 82;`"300"`→100;`"-5"`→0;`"abc"`→ 返回 -1 且不改。
  - `level/xp/xp_max` 各自写入后缓存正确;`xp_max "0"`→ 钳到 1。
  - 长字符串写 `name` → 截断到 23 且 NUL 结尾。
  - `online`:`"on"/"1"/"true"`→true;`"off"/"0"`→false;`"x"`→ -1。
  - `profile_ctl_apply` 返回被改字段号。
- **整机**:`idf.py build` 通过;真机用 nRF Connect 连 `TRAE-CARD`,逐个写特征看档案卡实时变化。

> `profile_ctl` 纯 C、零 IDF 依赖,gcc 独立编译即可跑(host_test 因缺 libbsd 暂不可用,同 avatar 模块)。

---

## 9. 明确不做(YAGNI / 留给后续)

- **图片传输**——阶段二单独设计。
- 特征**可读回显**(本期只写);持久化 NVS;配对/鉴权;通知(notify)。
- 时间/日期/头像种子的 BLE 控制(本期字段集不含;结构可平滑扩展)。

---

## 10. 涉及文件清单

| 文件 | 改动 |
| --- | --- |
| `components/core/ports/include/hal/hal_config.h` | 新增(端口) |
| `components/core/services/include/services/profile_ctl.h` | 新增 |
| `components/core/services/src/profile_ctl.c` | 新增(解析/校验) |
| `components/core/services/CMakeLists.txt` | 加 `src/profile_ctl.c` |
| `components/core/services/test/test_profile_ctl.c` | 新增单测 |
| `components/core/services/test/CMakeLists.txt` + `host_test/main/CMakeLists.txt` | 挂测试源 |
| `components/platform/platform_esp32/src/ble_config.c` | 新增(NimBLE GATT) |
| `components/platform/platform_esp32/include/platform/platform_factory.h` | 加 `platform_create_ble_config` |
| `components/platform/platform_esp32/include/platform/board_config.h` | 加 `PERIPH_BLE` / `BLE_DEVICE_NAME` |
| `components/platform/platform_esp32/CMakeLists.txt` | 加 `ble_config.c` + `PRIV_REQUIRES bt` |
| `components/app/src/app.c` | 创建 ble_config + 注册回调 + 粘合到 ui_profile |
| `sdkconfig.defaults` | 启用 NimBLE |
