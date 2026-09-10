# TRAE-CARD · 赛博工牌固件

一张基于 **ESP32-C3** 的可交互电子工牌:LVGL 彩屏主页(头像 / 昵称 / Token 积分 / 电量 / 时间)、蓝牙配置与内容下发(内置头像、全屏图、RTTTL 乐谱)、外部广播加发/扣积分、小游戏、宠物、低功耗休眠、产线自检。

> 先读本文(约 5 分钟)跑起来 + 建立地图,再按需深入 **[`doc/ARCHITECTURE.md`](doc/ARCHITECTURE.md)**(分层/依赖/内存纪律的权威说明)。

---

> # ⛔ 严禁 `idf.py erase-flash`
>
> 本项目有独立分区 **`cardid`(`0x356000`,16KB)**,存放**产线一次性写入的设备身份**:`sn / key / pk / hw`。
>
> - `idf.py flash` 只写 bootloader / 分区表 / app,**不碰**此分区 —— 日常烧固件是安全的。
> - **`idf.py erase-flash` 会连它一起擦掉,不可恢复。** `key` 每台随机、只存在产线台账里;擦掉后必须重走产线烧录(`python3 tools/make_cardid.py --port <PORT> --hw <版本>`,ProductKey 经 `CARDID_PK` 环境变量或 `--pk-file` 提供)。
> - 唯一允许 erase-flash 的时机:**首次引入 `cardid` 分区时**(分区表变更须擦一次),此后不要再执行。
>
> 设计:[`doc/specs/2026-07-23-sn-key-identity-design.md`](doc/specs/2026-07-23-sn-key-identity-design.md)

---

## 硬件一览

| | |
| --- | --- |
| MCU | ESP32-C3(RISC-V 单核 @160MHz,**无 PSRAM**,内部 RAM 紧张,可用堆约 50~60KB) |
| Flash | 见下方 ⚠️,部分批次为 **4MB**,部分工程按 8MB 配置 —— 烧录前务必确认 |
| 屏幕 | ST7789P3,240×320,4-line SPI(走 `esp_lcd`) |
| GUI | LVGL `^9.5.0`(经 `esp_lvgl_port` 接入,只在平台层) |
| 音频 | ES8311 codec + I2S(I2C 0x18);支持 RTTTL 方波播放、持久化开机音乐和麦克风回环自检 |
| 按键 | ADC 三键(上/下/确定,共用 GPIO0 = ADC1_CH0) |
| 蓝牙 | NimBLE(GATT 配置通道 + observer 扫描广播) |
| 电量 | CW2017 电量计(I2C 0x63,与 ES8311 共用 `I2C_NUM_0`) |
| 灯带 | WS2812(默认关闭 `PERIPH_LED=0`) |
| 工具链 | ESP-IDF **v5.5.3** |

---

## 快速开始

```bash
# 1) 一次性:配置 ESP-IDF v5.5.x 环境(. $IDF_PATH/export.sh)
idf.py set-target esp32c3

# 2) 构建 + 烧录 + 串口日志
idf.py build flash monitor        # 板子是原生 USB-Serial-JTAG,-p 通常自动

# 3) 纯逻辑单测(不需要硬件,秒级反馈)
#    见 doc/ARCHITECTURE.md §14:linux target + Unity,或 gcc 直接编译某业务源
```

**发布出厂固件**(合并单文件镜像):

```bash
python3 scripts/release.py        # build → esptool merge_bin → 命名 zip + MANIFEST 放 releases/
```

> ⚠️ **烧录 merged 镜像必须带 `--flash_size detect`**(尤其网页版 esptool-js):
> ```bash
> esptool.py --chip esp32c3 write_flash --flash_mode dio --flash_freq 80m --flash_size detect 0x0 merged-firmware.bin
> ```
> 合并镜像的 bootloader 头里 flash 大小是打包时按 sdkconfig 写死的;`detect` 会在烧录时按**实际芯片**重新探测并回写该头。**否则芯片实际 4MB 而头写 8MB → app 启动 `spi_flash` 探测到"实际 < 头" → assert 无限重启。** `idf.py flash` 默认就带 detect,所以不会遇到;写死 8MB 的 merged 直烧才会踩。

---

## AT 指令(USB 串口)

板子是原生 USB-Serial-JTAG,连上串口(115200 或任意,JTAG 不校验波特率)后**逐行**输入指令、回车执行。分两类:**FoloToy 风格分组指令**(运行期配置,全小写)与**产线专用指令**(身份/产测,`AT+` 大写,PC 脚本依赖)。

### FoloToy 风格分组指令(全小写,大小写不敏感)

| 指令 | 作用 | 响应示例 |
| --- | --- | --- |
| `at+config=?` | 查询全部配置(含身份、音量、深睡和按键指引) | `+CONFIG: ...,guide_count=5,guide_remaining=3,name=..` |
| `at+config=common,volume,<0-100>` | 设输出音量(存 profile + 立即生效) | `+OK volume=80` |
| `at+config=common,standby_time,<秒>` | 设空闲进入深睡时间(**单位秒**,粒度 60s,0=永不) | `+OK standby_time=600` |
| `at+config=common,guide_count,<0-100>` | 设置自检后连续显示按键指引的开机次数；同时重置当前剩余次数，`0` 表示关闭 | `+OK guide_count=5,remaining=5` |
| `at+command=?` | 列出支持的指令 | `+COMMAND: ...` |
| `at+command=restart,now` | 重启设备 | `+OK reboot` 后复位 |
| `at+reboot` | 重启设备 | `+OK reboot` 后复位 |

- 配置**立即生效并持久化**到 profile(NVS),不需重启(与 FoloToy"改配置需重启"不同)。
- `standby_time` 对齐 FoloToy 用**秒**;内部按分钟存(`profile.sleep_min`),粒度 60s、四舍五入。默认 10 分钟=`600`。超时后进入深度睡眠，任意上/下/确定功能键均可唤醒；深睡唤醒直接进入主页，不显示也不扣减按键指引次数。
- 按键指引默认配置为 `guide_count=5`。上电按住确定键进入设备自检后，
  会把 `guide_remaining` 重置为 `guide_count`；后续正常开机每显示一次便持久化扣减一次，归零后直接进入主页。
- 指引页不自动退出，按上、下或确定任意功能键后进入主页；用于退出的这次按键会被吞掉，不会误操作主页。
- 修改 `guide_count` 会立即把 `guide_remaining` 重置成相同值。若只想关闭后续指引，设置
  `at+config=common,guide_count,0`。
- ⚠️ `at+config=?` 会**回显每设备 DeviceSecret(key)明文**,便于产线核对台账。串口需物理接触、且该值本就在 `cardid_ledger.csv` 里;**仍属敏感,勿把输出转贴到公共渠道**。ProductKey 不回显(与 `AT+CARDID?` 一致)。

### 产线专用指令(FoloToy 无对应;`make_cardid.py`/`factory_test.py` 依赖)

| 指令 | 作用 | 响应 |
| --- | --- | --- |
| `AT+CARDID?` | 查身份:SN/HW/是否已烧录 + **pk 指纹**(不给明文) | `+CARDID: sn=..,hw=..,pk_fp=..,provisioned=1` |
| `AT+CARDID=key=<v>,pk=<v>,hw=<v>` | 返修补写身份字段(`sn` 只读,不可改;逗号分隔,可只写部分) | `+OK` / `+ERR=..` |
| `AT+TEST?` 或 `AT+TEST=AUTO` | 跑全部自动自检项 | 多行 `+TEST:...` + `+TEST:RESULT,PASS/FAIL` |
| `AT+TEST=<项>` | 单项自检:`INFO`/`I2C`/`AUDIO`/`BATT`/`BLE`/`ID`/`DISP`/`BTN` | `+TEST:<项>,..,PASS/FAIL` |

- `AT+TEST=ID` / 自检 ID 项:逐字段检测 cardid(`sn/key/pk/hw`),缺任一判 FAIL,屏上以 `SKPH`/`S---` 标记(缺的显示 `-`)。
- 身份写入详见 [`doc/sn-key-storage.md`](doc/sn-key-storage.md);产测方案详见 [`doc/factory-test-plan.md`](doc/factory-test-plan.md)。
- 上电按住**确定键**开机 → 进入设备端图形化自检(结果全画在屏上,工人只碰设备)。

---

## 代码地图

架构是 **端口-适配器(六边形)**:业务层纯 C、不碰任何 IDF/LVGL/NimBLE 头,可在 PC 上单测;第三方依赖全部锁在 `platform_esp32` 一个组件里。**所有依赖箭头都指向 `ports`。**

```
components/
├── core/
│   ├── ports/          端口(纯头文件,命名空间 hal/):hal_display / hal_button /
│   │                   hal_audio / hal_config / hal_ble_scan / hal_battery /
│   │                   hal_kv / hal_identity(+provision) …  —— 业务层唯一依赖
│   └── services/       业务层(纯 C,REQUIRES ports json,可 PC 单测):
│                       profile_ctl 数据模型/持久化 · config_json 配置解析 ·
│                       frame_reasm 帧重组 · jpeg_rx/probe 收图 · rtttl/score_rx/
│                       square_synth 乐谱 · adpcm/audio_rx/audio_service 语音 ·
│                       token_bcast/ble_match 广播积分 · battery 电量曲线 ·
│                       factory_test 产测 · sn_format/prov_cmd 产线指令 · ui_model
├── ui/presentation/    表现层(把 model 渲染到 LVGL,REQUIRES services,私有 lvgl):
│                       ui_profile 主页 · ui_game 小游戏 · ui_pet 宠物 ·
│                       ui_reward 加/扣分动画 · ui_selftest/ui_factory 产测界面 ·
│                       lv_font_cn_16/24 GB2312 中文字体(在 flash)
├── platform/platform_esp32/   适配器层(实现 hal,独占所有三方依赖):
│                       disp_st7789 · lvgl_port_setup · btn_iot_button ·
│                       audio_es8311/audio_store · battery_cw2017 · ble_config ·
│                       ble_scan · kv_nvs · jpeg_store/view · png_decode ·
│                       identity_nvs · provision_console · token_mac_esp
│                       (board_config.h = 引脚/分辨率/PERIPH 开关;idf_component.yml = 三方依赖)
└── app/                组装层(composition root):app.c 是唯一调用 platform_create_*()
                        的地方,用 PERIPH_* 宏条件组装。main/main.c 仅 app_main()→app_run()
```

**新人务必先建立的三个认知:**

1. **依赖铁律** —— `services/` 永不 `#include` IDF/NimBLE/厂商/lvgl 头;三方依赖只出现在 `platform_esp32/idf_component.yml`;只有 `app/` 知道用哪个具体实现。改代码前先想"这属于哪一层"。
2. **C3 内存纪律(无 PSRAM)** —— 大位图放 flash(`const`/`lv_image`),不用 LVGL 阴影/发光/大块静态缓冲(会挤垮 NimBLE 堆导致 BLE 初始化崩溃);动画只用 translate/leaf-opacity,别对大容器做 transform_scale(会强制分配整屏 layer buffer → OOM)。详见 `ARCHITECTURE.md §1/§8`。
3. **外设编译期开关** —— `board_config.h` 的 `PERIPH_*` 宏在编译期裁剪外设,任意组合都应能编译。关掉 `PERIPH_BLE` 会连带没有身份/串口指令/token。

---

## 分区表(`partitions.csv` 为准)

| 分区 | 类型 | 大小 | 用途 |
| --- | --- | --- | --- |
| `nvs` | data/nvs | 0x6000 | 通用 NVS(含 profile blob) |
| `phy_init` | data/phy | 0x1000 | RF 校准 |
| `factory` | app | 0x300000 | 应用固件 |
| `imgstore` | data/spiffs | 0x20000 | 全屏图(JPEG) |
| `imgframe` | data/spiffs | 0x26000 | 解码帧 / 头像 ARGB8888 |
| `cardid` | data/nvs | 0x4000 | ⛔ 设备身份(SN/Key/HwVer) |
| `audio` | data/spiffs | 0x80000 | 语音片段(ADPCM)，固定地址 0x37A000 以兼容升级 |
| `imgava` | data/spiffs | 0x100000 | 构建生成的 AVA1 本地头像包，地址 0x3FA000 |

> 头像包结束于 **0x4FA000**；目标板为 8MB flash。

---

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [`doc/ARCHITECTURE.md`](doc/ARCHITECTURE.md) | **权威**架构:分层/依赖方向/端口清单/内存纪律/单测/构建 |
| [`doc/ble-protocol-spec.md`](doc/ble-protocol-spec.md) | 蓝牙协议(小程序/App 对接:帧格式、特征、图片/乐谱/token) |
| [`doc/ble-broadcast-protocol-for-gateway.md`](doc/ble-broadcast-protocol-for-gateway.md) | 网关侧 BLE Manufacturer Data 广播协议(token 操作、档案字段、HMAC 签名) |
| [`doc/factory-test-plan.md`](doc/factory-test-plan.md) | 产线测试方案(AT 指令自检 / 按键触发自检) |
| [`doc/sn-key-storage.md`](doc/sn-key-storage.md) | 身份存储与产线烧录 |
| `doc/plans/` · `doc/specs/` | 各功能的规划(计划)与设计(实现细节)配对文档 |

---

## 工具(`tools/`)

| 脚本 | 用途 |
| --- | --- |
| `make_cardid.py` | 产线写身份(SN/Key/HwVer 进 `cardid` 分区) |
| `trae_studio.py` | 一体化上位机工具集(打包成独立 exe:`build_studio.bat`) |
| `factory_gui.py` | 工厂治具 GUI(`build_factory_gui.bat`) |
| `token_broadcast_gui.py` | 模拟网关发 token 广播(加分/扣分/开关宠物) |
| `ble_card_client.py` | BLE 客户端调试(bleak + Tk) |
| `avatar_export_gui.py` / `mp3_to_rtttl.py` / `wav_to_adpcm.py` | 头像/铃声/语音素材转换 |
| `gen_cn_font.py` | 生成 GB2312 中文字体 C 源 |

> ⚠️ `cardid_ledger.csv`(含明文 Key)与 `token_seq_state.json` 已 gitignore,**永不提交**。ProductKey 绝不出现在 BLE / 日志 / 只读特征里。

---

## 常见坑速查

| 现象 | 原因 / 处理 |
| --- | --- |
| 烧 merged 镜像后无限重启,日志 `Detected size(4096k) smaller than ... header(8192k)` | 芯片实际 4MB、镜像头写死 8MB。用 `--flash_size detect` 烧,或把工程改 4MB 构建 |
| 网页 esptool-js 报 `Cannot read properties of null (reading 'BOOTLOADER_FLASH_OFFSET')` | 芯片没被识别(未进 ROM 下载模式)。先 Connect 看到 `ESP32-C3` 再烧;必要时用 CLI esptool |
| BLE 初始化崩溃 / 堆不足 | 有大块静态 LVGL 缓冲挤垮 NimBLE 堆;大位图改放 flash |
| 头像/看图后 `Cache error` 崩溃 | 解除 `imgframe` mmap 前未清 `lv_image` 引用,或收图与解码并发擦分区(查 `jpeg_view_decode_busy()`) |
| 改了 `profile_data_t` 结构后开机数据全回默认 | 正常:`PROFILE_BLOB_VER` 递增触发一次性重置(防按错位解释旧 blob) |
| 一行内 `if(...) a++; b+=c;` 编译报 misleading-indentation | 本项目开 `-Werror`,拆成多行或加大括号 |
