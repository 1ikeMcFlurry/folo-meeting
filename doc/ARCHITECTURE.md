# TRAE-CARD 架构设计（ESP32-C3 赛博工牌）

> 芯片：**ESP32-C3**（RISC-V 单核，**无 PSRAM**，8MB flash） · 屏幕：**ST7789P3**（240×320，4-line SPI，内置 `esp_lcd`） · GUI：`lvgl/lvgl` ^9.5.0
> 外设：显示 + 音频(ES8311) + 按钮(ADC 三键) + BLE(NimBLE) + 电量计(CW2017) + 灯带(WS2812，默认关)
> 架构：端口-适配器（Ports & Adapters / 六边形），平台层与业务层分离，业务层可在 PC 上单元测试

> 本文档描述**当前实际实现**。协议细节见 `doc/ble-protocol-spec.md`；本文只讲分层与依赖。

---

## 1. 设计目标与核心思想

| 目标 | 实现手段 |
| --- | --- |
| 充分解耦 | 业务层只依赖抽象接口（端口），不含任何 ESP-IDF / LVGL / NimBLE / 厂商驱动头文件 |
| 可单元测试 | 端口用“vtable + 上下文”结构体表示，测试时注入 fake/mock；纯逻辑用 gcc 直接编译跑 |
| 平台层与业务层分离 | 第三方组件依赖全部锁在 `platform_esp32` 一个组件里，`REQUIRES` 强制依赖方向 |
| 可移植 | 把 `platform_esp32` 换成 `platform_host`（SDL + 假外设）即可在桌面跑整套 UI |
| 安全边界内聚 | ProductKey / 身份写入等高权限操作只暴露最小接口，密钥不出适配器边界（见 §11） |

C 语言没有 interface，这里用 **结构体函数指针表（vtable）** 模拟接口，用 **工厂函数返回抽象句柄** 隐藏实现细节，用 **组装层（composition root）** 做依赖注入。

ST7789P3 是 SPI 接口控制器，走标准 `esp_lcd` panel driver 模式，天然适合封装成一个“显示适配器”。LVGL 通过 `esp_lvgl_port` 接入，但整个关在平台层，业务层不感知 LVGL。BLE 用 IDF 内置 **NimBLE**，同样只以 `hal_config` / `hal_ble_scan` 端口的形式出现在业务层。

> **C3 内存约束（贯穿全设计）：** 无 PSRAM，内部 RAM 紧张（曾观测堆低至约 1KB）。因此：大位图放 flash（`const` / `lv_image`），不用 LVGL 阴影/发光（瞬时 mask 缓冲易 OOM），大块静态 LVGL 缓冲会压垮 NimBLE 堆导致 BLE 初始化崩溃。

---

## 2. 分层与依赖方向

```
            ┌─────────────────────────────────────────────┐
   依赖方向 │   app  (composition root 组装层)              │
     ↑      │   - 创建适配器、注入到业务、组织生命周期         │
     │      └───────────────┬─────────────────┬────────────┘
     │                      │ 注入             │ 注入
     │      ┌───────────────▼──────┐   ┌───────▼───────────────────┐
     │      │  services (业务层)    │   │  platform_esp32 (适配器层) │
     │      │  纯C, 只 #include hal │   │  实现 hal 接口             │
     │      │  可在 PC 上单测       │   │  依赖 IDF + 第三方组件      │
     │      └───────────────┬──────┘   └───────┬───────────────────┘
     │       ▲ presentation │ 依赖             │ 依赖(实现)
     │       │(REQUIRES      │                 │
     │       │ services)     │                 │
     │      ┌┴──────────────▼──────────────────▼────────────┐
     └──────┤   ports (端口 / 抽象接口, 纯头文件, 命名空间 hal/) │
            │   hal_display / hal_button / hal_audio /        │
            │   hal_config / hal_ble_scan / hal_battery /     │
            │   hal_kv / hal_identity / hal_led_strip …       │
            └─────────────────────────────────────────────────┘
```

**唯一的铁律：所有箭头都指向 `ports`（命名空间 `hal/`）。**

- `services` 永远不 `#include` 任何 IDF / NimBLE / 厂商 / lvgl 头。
- `presentation` 依赖 `services`（读 `ui_model` 等），私有依赖 `lvgl`。
- `platform_esp32` 实现 `hal` 接口，独占所有第三方依赖。
- `app` 是唯一知道“具体用哪个实现”的地方。

---

## 3. 目录结构

> **三点实现约定（务必先读）：**
> 1. **端口组件名为 `ports` 而非 `hal`。** ESP-IDF 自带内置组件叫 `hal`，本地组件若也叫 `hal` 会覆盖它，导致 mbedtls 等找不到 `hal/sha_types.h` 全面编译失败。故端口目录改名 `ports`；**头文件命名空间仍是 `hal/`**，业务层 `#include "hal/hal_display.h"` 不变。
> 2. **组件按架构层分组存放。** `components/` 下分成 `core/`（`ports`+`services`）、`ui/`（`presentation`）、`platform/`（`platform_esp32`）、`app/`（`app`）。ESP-IDF 默认只扫描 `components/` 直接子目录，故根 `CMakeLists.txt` 用 `EXTRA_COMPONENT_DIRS` 把这 4 个分组目录加入搜索路径。**组件名 = 叶子目录名**，`REQUIRES` 依赖链不受影响。
> 3. **外设编译期开关。** `board_config.h` 里的 `PERIPH_*` 宏在编译期裁剪外设，任意组合都应能编译（见 §16）。

```
trae_card/
├── CMakeLists.txt                    # 顶层:EXTRA_COMPONENT_DIRS + PROJECT_VER
├── partitions.csv                    # 8MB 分区(见 §15)
├── sdkconfig.defaults
├── components/
│   ├── core/                         # ── 内核层:平台无关、可移植、可 PC 单测 ──
│   │   ├── ports/                    # 端口:只有头文件,零依赖(组件名 ports,不可叫 hal)
│   │   │   └── include/hal/          #   命名空间 hal/
│   │   │       ├── hal_types.h           公共类型
│   │   │       ├── hal_display.h         显示:区域刷新/背光/尺寸
│   │   │       ├── hal_button.h          按钮:PRESS/CLICK/DOUBLE/LONG + 索引
│   │   │       ├── hal_audio.h           音频:I2S 读写/音量/采样率
│   │   │       ├── hal_led_strip.h       WS2812 灯带
│   │   │       ├── hal_battery.h         电量:SOC% + 档位
│   │   │       ├── hal_config.h          BLE 档案卡配置通道(下发 JSON/图片/token/身份/积分只读)
│   │   │       ├── hal_ble_scan.h        BLE observer:监听外部厂商广播
│   │   │       ├── hal_kv.h              键值持久化(NVS)
│   │   │       ├── hal_identity.h        设备身份 SN/HwVer/HMAC 签名(无 get_product)
│   │   │       └── hal_identity_provision.h  身份写入(串口物理接触授权,刻意分开)
│   │   │
│   │   └── services/                 # 业务层:纯C,只依赖 hal (REQUIRES ports json)
│   │       ├── include/services/ + src/
│   │       │   ├── ui_model            UI 状态/视图模型(无 LVGL)
│   │       │   ├── profile_ctl         档案卡数据模型 + 持久化 blob(见 §7)
│   │       │   ├── config_json         BLE 下发 JSON → 更新 profile(cJSON)
│   │       │   ├── battery             电量纯逻辑(曲线/EMA 平滑/迟滞档位)
│   │       │   ├── frame_reasm         [ver][type][len] 帧重组
│   │       │   ├── jpeg_rx / jpeg_probe  JPG 分片接收状态机 / 兼容性预检
│   │       │   ├── score_rx / rtttl / square_synth / tone_presets  乐谱接收/解析/合成/内置音
│   │       │   ├── ble_match / token_bcast / profile_bcast  广播匹配与签名业务(纯逻辑)
│   │       │   ├── sn_format / prov_cmd   MAC→SN 字符串 / 产线 AT 指令解析
│   │       │   └── led_service / audio_service
│   │       └── test/                 # ★ 业务层单测(linux target / gcc 直接编)
│   │
│   ├── ui/
│   │   └── presentation/             # 表现层:把 model 渲染到 LVGL (REQUIRES services, PRIV lvgl)
│   │       ├── src/ui_profile.c        主页:头像/昵称/token 进度/dock/状态栏
│   │       ├── src/ui_game.c           小游戏:横向代码关键字障碍
│   │       ├── src/ui_mario.c(+bitmap) 备用游戏素材
│   │       └── src/lv_font_cn_16.c / lv_font_cn_24.c  GB2312 2bpp 中文字体(在 flash)
│   │
│   ├── platform/
│   │   └── platform_esp32/           # 适配器层:实现 hal,锁所有三方依赖 (REQUIRES ports services)
│   │       ├── include/platform/
│   │       │   ├── board_config.h      引脚/分辨率/PERIPH 开关/电池 profile 选择
│   │       │   └── platform_factory.h  工厂函数声明
│   │       ├── src/
│   │       │   ├── disp_st7789.c       ST7789P3 + esp_lcd
│   │       │   ├── lvgl_port_setup.c   esp_lvgl_port 接入 + LVGL 锁 + 按键导航
│   │       │   ├── btn_iot_button.c    ADC 三键(上/下/确定,共用 GPIO0=ADC1_CH0)
│   │       │   ├── led_ws2812.c        WS2812(默认关)
│   │       │   ├── audio_es8311.c      ES8311 + I2S(+ 麦克风直通测试)
│   │       │   ├── battery_cw2017.c(+cw2017_profiles.h)  CW2017 电量计(I2C 0x63)
│   │       │   ├── ble_config.c(+ble_internal.h)  NimBLE GATT 档案卡控制通道
│   │       │   ├── ble_scan.c          NimBLE observer 扫描
│   │       │   ├── kv_nvs.c            NVS 键值
│   │       │   ├── jpeg_store.c/.h     BLE 全屏 JPG 存 imgstore
│   │       │   ├── avatar_store.c      mmap/查询固件内置 AVA1 头像包
│   │       │   ├── jpeg_view.c         全屏 JPG + 内置 PNG 头像解码(tick 驱动)
│   │       │   ├── png_decode.c/.h     PNG 解码(ROM miniz tinfl → ARGB8888)
│   │       │   ├── identity_nvs.c      身份 cardid 分区 + HMAC 签名
│   │       │   ├── provision_console.c USB 串口产线/返修指令
│   │       │   ├── token_mac_esp.c     token HMAC(mbedtls,注入 core/services)
│   │       │   └── platform_sys.c      随机种子等系统工具
│   │       ├── idf_component.yml       ★ 所有第三方依赖集中在此
│   │       └── CMakeLists.txt
│   │
│   └── app/                          # ── 组装层(composition root)──
│       ├── include/app/app.h
│       ├── src/app.c                 # 唯一调用 platform_create_*() 的地方,用 PERIPH_* 条件组装
│       └── CMakeLists.txt            # REQUIRES ports services presentation platform_esp32 esp_timer
│
├── main/                             # app_main() 仅调用 app_run()
├── tools/ble_card_client.py          # 上位机测试工具(bleak + Tk)
├── scripts/release.py                # 8MB 固件发布(build → merge_bin → 命名 zip)
└── doc/
    ├── ARCHITECTURE.md               # 本文
    └── ble-protocol-spec.md          # 蓝牙协议对接文档(小程序/App 端)
```

> **组件发现机制：** 顶层 `CMakeLists.txt` 声明 `set(EXTRA_COMPONENT_DIRS components/core components/ui components/platform components/app)`；每个分组目录被当“组件容器”扫描，其下叶子目录即组件，**组件名 = 叶子目录名**，故 `REQUIRES ports/services/...` 不受分组影响。

---

## 4. 端口清单（`ports`，命名空间 `hal/`，业务层唯一依赖）

每个端口都是“vtable + 上下文指针”的两段式结构，再用 `static inline` 包装函数让调用方写得干净。样例（其余端口同构，此处不逐一贴代码）：

```c
// components/core/ports/include/hal/hal_display.h
#pragma once
#include <stdint.h>
typedef struct hal_display_s hal_display_t;

typedef struct {
    void (*flush)(hal_display_t *self, int x1, int y1, int x2, int y2, const void *px565);
    void (*set_backlight)(hal_display_t *self, uint8_t percent);
    void (*get_size)(hal_display_t *self, uint16_t *w, uint16_t *h);
} hal_display_api_t;

struct hal_display_s { const hal_display_api_t *api; void *impl; };

static inline void hal_display_flush(hal_display_t *d, int x1, int y1, int x2, int y2, const void *px) {
    d->api->flush(d, x1, y1, x2, y2, px);
}
```

**端口清单：**

| 端口 | 职责 | 备注 |
| --- | --- | --- |
| `hal_display` | 区域刷新(RGB565) / 背光 / 尺寸 | 由 ST7789P3 适配器实现 |
| `hal_button` | 按钮事件回调(带索引) | 事件含 `PRESS`(按下即触发,低延迟) / `CLICK` / `DOUBLE` / `LONG` |
| `hal_audio` | I2S PCM 读写 / 音量 / 采样率 | ES8311 实现 |
| `hal_led_strip` | 灯带像素/刷新/清除 | WS2812，默认外设关 |
| `hal_battery` | 读 SOC% + 档位 | CW2017 实现；无芯片时上层回退虚拟电量 |
| `hal_config` | BLE 档案卡配置通道 | 下发 JSON/图片分片/token；只读特征暴露身份 blob 与游戏积分 blob(见 §9) |
| `hal_ble_scan` | BLE observer | 监听外部厂商广播，匹配 → 回调 |
| `hal_kv` | 键值持久化 | NVS 实现，始终可用 |
| `hal_identity` | 设备身份 | 提供 SN/HwVer 与 `hmac()`；**没有 get_product()**——ProductKey 不出适配器边界(见 §11) |
| `hal_identity_provision` | 身份写入 | 与只读身份**刻意分开**：写入是“物理接触即授权”的高权限操作，仅串口通道 |

> 端口组件 `CMakeLists.txt`：`idf_component_register(INCLUDE_DIRS include)` —— 纯接口，无 SRCS，无 REQUIRES。

---

## 5. 适配器层（`platform_esp32`）

适配器实现端口 vtable，工厂函数只对外暴露 `hal_*` 抽象句柄，内部细节完全隐藏。模式示例（ST7789P3）：

```c
// components/platform/platform_esp32/src/disp_st7789.c (节选)
static void st_flush(hal_display_t *self, int x1, int y1, int x2, int y2, const void *px) {
    st_ctx_t *c = self->impl;
    esp_lcd_panel_draw_bitmap(c->panel, x1, y1, x2 + 1, y2 + 1, px);  // esp_lcd end 为开区间,+1
}
static const hal_display_api_t API = { .flush = st_flush, .get_size = st_get_size, .set_backlight = st_backlight };

hal_display_t *platform_create_display(const board_config_t *cfg) {
    // spi_bus_initialize → esp_lcd_new_panel_io_spi → esp_lcd_new_panel_st7789
    // reset/init/inv(LCD_INVERT_COLOR)/disp_on → 组装 hal_display_t 返回
}
```

**适配器 → 端口 → 依赖 映射：**

| 适配器源文件 | 实现端口 / 提供能力 | 第三方依赖 | 开关 |
| --- | --- | --- | --- |
| `disp_st7789.c` | `hal_display` | `esp_lcd`(内置) | `PERIPH_DISPLAY` |
| `lvgl_port_setup.c` | LVGL 接入 + 锁 + 按键导航 | `esp_lvgl_port` `lvgl` | `PERIPH_DISPLAY` |
| `btn_iot_button.c` | `hal_button`(ADC 三键) | `button` `esp_adc` | `PERIPH_BUTTON` |
| `audio_es8311.c` | `hal_audio` | `esp_codec_dev` `esp_driver_i2s/i2c` | `PERIPH_AUDIO` |
| `led_ws2812.c` | `hal_led_strip` | `led_strip` | `PERIPH_LED`(默认 0) |
| `battery_cw2017.c` | `hal_battery` | `esp_driver_i2c` | `PERIPH_BATTERY` |
| `ble_config.c` | `hal_config`(GATT 服务) | `bt`(NimBLE) `mbedtls` | `PERIPH_BLE` |
| `ble_scan.c` | `hal_ble_scan`(observer) | `bt` | `PERIPH_BLE` |
| `kv_nvs.c` | `hal_kv` | `nvs_flash` | 始终 |
| `jpeg_store.c` / `jpeg_view.c` | 图片存储/看图(见 §10) | `esp_partition` `esp_new_jpeg` | `PERIPH_DISPLAY` |
| `png_decode.c` | 头像 PNG 解码(见 §10) | `esp_rom`(ROM miniz) | `PERIPH_DISPLAY` |
| `identity_nvs.c` | `hal_identity` | `nvs_flash` `mbedtls` | `PERIPH_BLE`(接线所致) |
| `provision_console.c` | 串口产线指令(见 §11) | `esp_driver_usb_serial_jtag` | `PERIPH_BLE` |
| `token_mac_esp.c` | token HMAC(注入 core) | `mbedtls` | `PERIPH_BLE` |

> **总线复用：** CW2017 电量计（I2C 0x63）与 ES8311 **共用 `I2C_NUM_0` 总线**，通过 `i2c_master_get_bus_handle` 取已建总线，不重复初始化。

平台层 `CMakeLists.txt` 的 `PRIV_REQUIRES` 集中列出全部三方依赖，`REQUIRES ports services driver esp_lcd`——注意**允许 `REQUIRES services`**：适配器会把 `token_mac_esp` 注入给 `token_bcast`、用 `jpeg_probe` 结构体等，这是平台层向业务层“提供实现/共享纯类型”，不违反“业务不依赖平台”的方向。

---

## 6. 业务层（`services`，纯 C，可单测）

业务层只见到 `hal_*` 与彼此，完全不知道 ST7789 / NimBLE / ES8311 / CW2017 的存在。除注入 HMAC 等少数回调外，全部可在 PC 上用 fake HAL 或 gcc 直接编译验证。

| 业务模块 | 职责 | 可测点 |
| --- | --- | --- |
| `ui_model` | UI 状态/视图模型(无 LVGL)，观察者回调 | 状态机 |
| `profile_ctl` | 档案卡数据 + 持久化 blob(见 §7) | 字段默认/round-trip/版本拒绝 |
| `config_json` | BLE 下发 JSON → 部分更新 profile(cJSON)，返回 changed 集 | 键解析/裁剪/动作键 |
| `battery` | 电压→SOC 曲线、整数 EMA(带最小步长防死区)、迟滞档位 | 平滑/档位 |
| `frame_reasm` | `[ver][type][len]` 帧重组 | 分片边界 |
| `jpeg_rx` / `jpeg_probe` | JPG 分片接收状态机 / 兼容性预检 | 时序/预检分类 |
| `score_rx` / `rtttl` / `square_synth` / `tone_presets` | 乐谱接收/解析/方波合成/内置提示音 | 解析/合成 |
| `ble_match` / `token_bcast` | 广播匹配 / token 广播决策 | 纯逻辑 |
| `sn_format` / `prov_cmd` | MAC→SN 字符串 / 产线 AT 指令解析 | 格式/解析 |
| `led_service` / `audio_service` | LED / 音频业务封装 | 算法 |

`REQUIRES ports json`（`json` = IDF 内置 cJSON）。

---

## 7. 档案卡数据模型与持久化（`profile_ctl`）

`profile_data_t` 是主页显示的数据源，也是 BLE 同步的落点，整体 `memcpy` 存入 `hal_kv`（NVS）掉电保留：

- 字段：`name` / `avatar_name` / `token` / `token_max` / `battery` / `sleep_min` / `game_total` / `game_best`（及若干兼容旧字段）。
- **版本化：** 当前 blob v14；提供 v13 显式迁移并补入默认 `avatar_name=default`，而非按错位解释旧数据。
- **初始化时机铁律：** 开机读默认 NVS 必须在 `nvs_flash_init` 之后，故 `platform_create_kv` 自己先 `nvs_flash_init` 再供 load，避免早读全回默认。

数据来源：BLE 下发 JSON（`config_json` 解析）、游戏每局结束回调（`game_total` 累加 / `game_best` 取最高）。BLE 侧“先读后清”的游戏积分交接见 `ble-protocol-spec.md` 的“游戏积分同步”。

---

## 8. 表现层（`presentation`）

把业务状态渲染到 LVGL，是唯一（除 `lvgl_port_setup`）出现 `lvgl.h` 的业务侧组件。

- `ui_profile.c` —— 主页：头像（左）+ 昵称 / Token 进度条（右）+ 2 格 dock + 顶部状态栏（时间/电量/在线点）。头像空缺则留白。头像显示区 96×156。
- `ui_game.c` —— 小游戏：横向滚动的**多语言代码关键字**作为障碍（无外框，VS Code Dark+ 配色），每局结束通过回调把分数交给组装根记账。
- `lv_font_cn_16/24.c` —— GB2312 2bpp 中文字体，编入 flash（不占 RAM）。

> **RAM 纪律（C3 无 PSRAM）：** 不用阴影/发光；大位图（头像/全屏图）走 flash 的 `lv_image_dsc_t`，不做大块静态 LVGL 缓冲，否则挤垮 NimBLE 堆。视觉靠渐变/描边/圆角/排版，而非 mask 特效。

---

## 9. BLE 档案卡通道（`hal_config` / `ble_config.c`）

NimBLE GATT 服务，128 位 UUID 基址 `TRAECARD` + 末字节 = 字段号。**协议细节以 `doc/ble-protocol-spec.md`（当前 v11）为准**，本文只记架构要点：

- **帧协议：** 命令写特征 `…0010` 收 `[ver][type][len]` 帧，`frame_reasm` 重组后分派：JSON 配置 / JPG 分片 / 乐谱分片 / token。
- **状态回报：** notify 特征 `…0011`。
- **只读特征：** `…0012` 身份 blob（SN/Key/HwVer，绑定用）；`…0013` 游戏积分 blob（9 字节 `[ver][game_total u32 LE][game_best u32 LE]`）。
- **游戏积分“先读后清”：** 小程序读 `…0013` → 记录 → 下发 `{"game_clear":true}` 清零工牌端，批量交接（数据不实时同步）。
- **头像/图片分流：** JSON 只传 `avatar_name` 选择内置头像；BLE `type=0x02` 只接收全屏 JPEG。
- **广播双协议：** `token_bcast` 保持 v1 加减分/宠物行为；`profile_bcast` 处理 26B v2 定向分片，可静默更新 token、上限、时间、昵称和头像。两者均由 `token_mac_esp` 调身份层 HMAC，实际签名密钥是每设备 DeviceSecret。

> **接线约束：** 身份（`hal_identity`）、串口产线、token HMAC 三者的消费方都在 BLE 侧，故 `app.c` 仅在 `PERIPH_BLE` 打开时才创建它们——关掉 BLE 就没有身份/串口指令/SN 自补。

---

## 10. 图片子系统（全屏上传 + 内置头像包）

- **全屏接收：** `jpeg_store_begin/write/end` 把 BLE 分片串行写入 `imgstore`；仅支持基线 JPEG。
- **全屏看图：** `jpeg_view` 非阻塞、tick 驱动，用 `esp_new_jpeg` 块模式直出 RGB565；进入时可叠加文字提示（如“可以扫码了解更多哦”）。
- **头像包：** 构建时动态扫描 `assets/avatars/*.png`（非空即可，1MB 容量按规范至少容纳 10 张），由 `tools/avatar_pack.py` 打成 AVA1 `imgava.bin`；目录表含名称、偏移、长度和 CRC。`avatar_store` 启动时 mmap 分区并零堆查询，`jpeg_view_request_avatar(name)` 再把 PNG 流式解到 `imgframe`。
- **交付：** `audio` 固定保留历史地址 `0x37a000`，CMake 将 1MB `imgava.bin` 注册到 `0x3fa000`，并生成 `avatar_catalog.json`；`scripts/release.py` 把头像包写入合并固件/发布 ZIP，上位机据清单动态显示头像名称。

> **两条并发/生命周期铁律（都曾导致 Cache error 崩溃）：**
> 1. 解码跑在 LVGL 任务、收图跑在 NimBLE host 任务，两者无锁。组装根在放行新图 `BEGIN` 前必须查 `jpeg_view_decode_busy()`，否则收图擦分区会把解码器脚下数据抽走。
> 2. 解除 `imgframe` mmap 前必须先清掉 `lv_image` 引用，否则重绘读悬空 flash。头像容器勿用 `clip_corner`。

---

## 11. 身份与产线安全（`hal_identity` / `provision_console`）

- **身份存储：** `cardid` 分区（独立 NVS，0x4000），存 SN / HwVer / 每设备 Key。SN 缺失时按 `esp_read_mac(ESP_MAC_WIFI_STA)` 自补。
- **只暴露签名接口：** `hal_identity` 只向业务层暴露 `hmac()`；实现使用每设备 DeviceSecret，网关按 SN 从受保护台账取相同密钥。ProductKey 仍不经 BLE 或日志暴露。
- **写入通道隔离：** `hal_identity_provision` 与只读身份刻意分文件——写入是“物理接触即授权”的高权限操作，**仅 USB 串口**（`provision_console`，`AT+CARDID=`）。改写后回调组装根重推 `…0012` 身份 blob。

> **安全红线（长期有效）：**
> - `cardid_ledger.csv` 含明文 Key → 已 gitignore（0600），**永不提交**。
> - ProductKey 绝不出现在 BLE / 日志里；Key 写入仅走 USB 串口。
> - **永不 `idf.py erase-flash`**（会毁掉 `cardid` 身份）。
> - `…0012` 身份 blob 含的是**每设备 Key**（供绑定读），不是共享 ProductKey。

---

## 12. 电量（`battery` + `battery_cw2017`）

- **CW2017 电量计**（I2C 0x63，3.7V 锂电/4.2V 满），自带 FastCali 直接给 SOC%，无需分压电阻。
- 与 ES8311 **共用 `I2C_NUM_0`**；采样周期 `BATT_SAMPLE_MS`（默认 3000ms）。
- **电池 profile 可切换：** 80 字节 profile 表在 `cw2017_profiles.h`，`CW2017_PROFILE_SEL` 选第几个、`CW2017_WRITE_PROFILE` 决定是否写芯片（0 = 用芯片自带 Li-Poly）。换电池只改这两行。
- **纯逻辑在 `battery`（services）：** 电压→SOC 曲线、整数 EMA（**带最小步长 1 防死区**，避免 `(20-21)/4=0` 卡死造成恒定偏移）、迟滞分档。
- 关闭 `PERIPH_BATTERY` 或总线上无芯片 → 上层回退虚拟电量（`BATTERY_VIRTUAL_PCT/LEVEL`）。

---

## 13. 组装层与入口（`app`）

`app.c` 是唯一知道“具体实现”的地方，用 `PERIPH_*` 宏条件组装：创建适配器 → 注入业务 → 注册回调 → 启动生命周期。要点：

- `PERIPH_DISPLAY=0` 连带跳过 LVGL 初始化、presenter、图片子系统。
- 身份/串口/token/BLE 只在 `PERIPH_BLE` 下创建（见 §9）。
- 游戏结束回调 → `profile_ctl` 记账 → `save_profile()` → `publish_gamescore()` 刷新 BLE 只读特征。
- 入口 `main/main.c`：`app_main()` 仅调用 `app_run()`。

---

## 14. 单元测试（在 PC 上跑）

业务层只依赖 `hal`，两种跑法：

1. **IDF `linux` target + Unity**：注入 fake HAL（`services/test/mocks/mock_hal.c`），`idf.py --preview set-target linux && idf.py build` 后跑 `build/test_app.elf`。
2. **gcc 直接编译纯逻辑**：`config_json` / `profile_ctl` / `battery` 等无 IDF 依赖的模块可直接 `gcc` 编译测试源跑，秒级反馈（本项目常用此法验证 profile round-trip、game_clear 等）。

**测试边界：** 业务逻辑（曲线/状态机/JSON/帧重组）在 PC 上测；显示/音频/BLE 的 DMA/I2S/射频时序隔离在适配器层，用真机集成测试；LVGL 只测 `ui_model` 状态机，不测像素。

---

## 15. 依赖、分区与构建

### 15.1 第三方依赖（集中在 `platform_esp32/idf_component.yml`）

```yaml
dependencies:
  idf: ">=5.5.0"
  # ST7789P3 用 IDF 内置 esp_lcd,无需额外组件
  lvgl/lvgl: "^9.5.0"
  espressif/esp_lvgl_port: "*"
  espressif/button: "*"             # iot_button
  espressif/led_strip: "*"          # WS2812 (RMT)
  espressif/esp_codec_dev: "*"      # 含 ES8311 codec 驱动
  espressif/esp_new_jpeg: "^1.0.0"  # 软件 JPEG 解码(块模式,直出 RGB565)
```

IDF 内置组件（`PRIV_REQUIRES`）：`bt`(NimBLE)、`nvs_flash`、`mbedtls`、`esp_adc`、`esp_driver_i2c/i2s`、`esp_partition`、`esp_rom`、`esp_driver_usb_serial_jtag`、`json`(cJSON)。

> **版本提醒：** `esp_lvgl_port` / `button` / `led_strip` / `esp_codec_dev` 主版本随时间变化，建议用 `idf.py add-dependency` 让 IDF 解析并写进 `dependencies.lock`，再把 `"*"` 收紧成确切版本。

### 15.2 分区表（8MB flash，`partitions.csv`）

| 分区 | 类型 | 大小 | 用途 |
| --- | --- | --- | --- |
| `nvs` | data/nvs | 0x6000 | 通用 NVS（含 profile blob） |
| `phy_init` | data/phy | 0x1000 | RF 校准 |
| `factory` | app | 0x300000 | 应用固件 |
| `imgstore` | data/spiffs | 0x20000 | 全屏图（JPEG） |
| `imgframe` | data/spiffs | 0x26000 | 解码帧 / 头像 ARGB8888 |
| `cardid` | data/nvs | 0x4000 | 设备身份（SN/Key/HwVer） |
| `imgava` | data/spiffs | 0x100000 | AVA1 动态头像包（容量至少容纳 10 张，构建生成、随固件烧录） |

### 15.3 常用命令

```bash
idf.py set-target esp32c3
idf.py build flash monitor

# 发布单文件固件(build → esptool merge_bin 8MB → 命名 zip + MANIFEST)
python3 scripts/release.py

# 纯逻辑单测(示例):直接 gcc 编译某业务源 + 测试源
```

> **merge_bin 说明：** `release.py` 直接调 esptool（而非 `idf.py merge-bin`），把 `flash_args` 里的 `detect` 换成 sdkconfig 的 8MB；合并镜像含 boot+ptable+app 和构建生成的 `imgava` 头像包。`cardid/imgstore/imgframe/nvs` 仍是每设备或运行时数据，不预填。

---

## 16. 外设编译期开关

`board_config.h` 里的宏在**编译期**决定各外设是否包含，任意组合都应能编译：

```c
#define PERIPH_DISPLAY  1   // ST7789P3 + LVGL + 表现层 + 图片子系统
#define PERIPH_LED      0   // WS2812
#define PERIPH_AUDIO    1   // ES8311
#define PERIPH_BUTTON   1   // ADC 三键
#define PERIPH_BLE      1   // BLE 档案卡控制 + 身份/串口/token
#define PERIPH_BATTERY  1   // CW2017 电量计
```

两处生效：

1. **各适配器源文件**整段（含第三方 `#include`）包在 `#if PERIPH_xxx ... #endif` 内——关闭时该文件编译为空，不引用对应驱动。
2. **组装层 `app.c`** 用同样的宏决定是否创建/注入该外设。

**取舍：** 宏方案不裁剪依赖的**下载**（`idf_component.yml` 的组件始终会拉取，只是关闭时代码不参与编译）。若要连下载一并裁剪，需改用 Kconfig（`CONFIG_PERIPH_*`）在 `CMakeLists.txt` 条件化 `SRCS` 与 `PRIV_REQUIRES`。

---

## 附：依赖方向自检清单

| 检查项 | 期望 |
| --- | --- |
| `services/` 任一文件是否 `#include` 了 IDF / NimBLE / 厂商 / lvgl 头？ | 否 |
| `presentation/` 是否只依赖 `services`（+ 私有 `lvgl`）？ | 是 |
| 端口组件（`ports`）的 `REQUIRES` 是否为空？ | 是 |
| 端口组件是否避免叫 `hal`（防止与 IDF 内置 `hal` 冲突）？ | 是 |
| 第三方组件是否只出现在 `platform_esp32/idf_component.yml`？ | 是 |
| ProductKey 是否绝不出现在 BLE / 日志 / 只读特征里？ | 是 |
| 业务层测试是否能在 PC（linux target / gcc）上编过并运行？ | 是 |
| 是否只有 `app/` 调用了 `platform_create_*()` 工厂？ | 是 |
| 关闭任一外设（`PERIPH_*=0`）后是否仍能编译？ | 是 |
