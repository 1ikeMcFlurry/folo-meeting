# 工厂产测方案(trae_card)

面向工厂生产后的整机功能测试。复用固件已有的 USB-Serial-JTAG `AT+` 指令通道,
新增 `AT+TEST` 系列指令,配合 PC 治具脚本 `tools/factory_test.py`,**一根 USB 线**完成
烧录、身份写入、功能测试全流程。

## 设计原则

1. **复用 AT 指令通道**:不引入 `esp_console`(其 REPL 缓冲 + 4KB 栈会压 NimBLE 的静态 RAM),
   沿用手写解析,加一族 `AT+TEST` 指令。命令解析在 `services/factory_test.c`(纯逻辑,已单测)。
2. **能自动判的全自动**,人眼/人手项最少化(仅屏幕目视、按键逐键)。
3. **机器可解析输出**:每项一行 `+TEST:<项>,<k=v>...,<PASS|FAIL>`,末尾 `+TEST:RESULT,PASS,fails=0`。
4. **命令常驻出货固件**:RMA 返修可直接复测;命令需显式下发,普通用户不会误触。

## 测试项与判据

| 项目 | 方式 | 底层依据 | PASS 判据 | 类型 |
|---|---|---|---|---|
| INFO/系统 | 版本/SN/堆/复位原因 | `esp_app_get_description` / `esp_get_free_heap_size` / `esp_reset_reason` | 堆≥20000 且非 panic/brownout/wdt 复位 | 自动 |
| I2C | ES8311(0x18)/CW2017(0x63) 在位 | `audio_service_configure`(重配 codec,走 I2C 不出声)/ `s_batt!=NULL` | 两者都在位 | 自动 |
| AUDIO | **声学自回环**:边放 1kHz 边录,Goertzel 测该频点能量 | `audio_service_loopback_test` 返回码 + `tone_mag` | ret==0 且 `tone_mag≥300` | 自动 |
| BATT | CW2017 SOC/电压 | `hal_battery_read_soc/mv` | 在位且 SOC∈[1,100] | 自动 |
| BLE | 初始化 + 自身 SN | `s_scan`/`s_cfg` 非 NULL | 均已就绪 | 自动 |
| ID | 身份已烧录 + pk 指纹 | `hal_identity_is_provisioned` + `identity_pk_fingerprint` | provisioned=1 | 自动 |
| DISP | 全屏红/绿/蓝/白/黑 + 网格 | 无软件返回值 | 操作员目视确认 | 人工 |
| BTN | 逐键捕获 上/下/确定 | ADC 电压窗口(`on_btn_raw`) | 10s 内集齐 3 键 | 半自动 |

> **音频声学自回环是重点**:麦克风听自己扬声器的 1kHz 音,用 Goertzel 测该频点能量 —— 一次
> 同时验证**扬声器出声 + 麦克风收音**,全自动无需人工拍手/说话。这正好拦截历史上那个
> 「麦克风模拟通路断(耦合电容开路)→ 录音全静音」的缺陷(断了就收不到自己的音 → 能量为 0)。
> 判据用特定频点能量而非原始峰值,可排除纯电气串扰/环境噪声的误判。
> ⚠ 阈值 `FT_TONE_MAG_MIN=300(已真机标定)` 必须金板实测标定:读 `+TEST:AUDIO` 的 `tone_mag`,取良品分布下限的
> ~50%(见 `services/factory_test.h`)。前提:麦克风与扬声器在板上能声学耦合(小卡片一般成立)。

## 串口指令(`AT+TEST` 族)

```
AT+TEST?  / AT+TEST=AUTO   跑全部自动项 + RESULT 汇总
AT+TEST=INFO   版本/SN/堆/复位原因
AT+TEST=I2C    ES8311/CW2017 在位
AT+TEST=AUDIO  声学自回环自检(阻塞 ~0.6s;期间有诊断 printf,治具按 "+TEST:" 前缀过滤)
AT+TEST=BATT   CW2017 SOC/电压
AT+TEST=BLE    BLE 就绪 + SN
AT+TEST=ID     身份已烧录 + pk 指纹
AT+TEST=DISP   屏幕一屏铺满 红/绿/蓝/白/黑 色带 + 网格(单眼可判,~15s 自动撤),返回 +TEST:DISP,shown,OK
AT+TEST=BTN    逐键捕获,10s;每识别一键回 +TEST:BTN,key=UP;集齐回 PASS,超时回 FAIL,mask=<n>
```

应答示例:
```
+TEST:INFO,ver=1.4.0,sn=f85b1b72a574,heap=58720,rst=1,PASS
+TEST:I2C,es8311=1,cw2017=1,PASS
+TEST:AUDIO,ret=0,tone_mag=1800,peak=2400,PASS
+TEST:BATT,present=1,soc=83,mv=4021,PASS
+TEST:BLE,started=1,sn=f85b1b72a574,PASS
+TEST:ID,provisioned=1,pk_fp=1a2b3c4d,PASS
+TEST:RESULT,PASS,fails=0
```

## 设备自助自检(推荐主线:工人只碰设备)

**上电按住"确定"键**即进入设备端自助自检 —— 结果/进度/交互提示全画在**设备屏**上,
工人**不碰电脑**:

1. 通电时按住确定键 → 固件读一次按键 ADC 命中确定键窗口 → 进自检模式(松开则正常开机)。
2. 设备屏显示自检列表,自动项(INFO/I2C/AUDIO/BATT/BLE/ID)逐行跑出 `OK`(绿)/`NG`(红)。
3. **屏幕自检**:铺满红/绿/蓝/白/黑色带 + 网格,屏上提示"确定键=正常 / 上键=异常",工人按**设备键**判。
4. **按键自检**:屏上提示"依次按 上 下 确定",工人按**设备键**,逐键点亮。
5. 屏上整屏大字 **PASS(良品·可下线)/ FAIL(不良·请复检)**。
6. 全程固件把同样的 `+TEST:` 行 printf 到 USB 串口 —— 旁边的 PC 治具**被动记台账**(工人不点电脑)。

> 触发靠 `platform_button_boot_index()`(建按键前读一次 ADC,用完释放);自检 UI 在
> `ui_selftest.c`;编排在 `app.c` 的 `run_onboard_selftest()`,复用与 `AT+TEST` 同一套判定原语。

### 自检后的开机按键指引

只要通过“上电按住确定键”进入过设备自检模式，固件就会持久化重置按键指引计数。默认后续
`5` 次正常开机先显示外围按键位置，按任意功能键后进入主页；每次展示前先落盘扣减，意外断电不会
重复占用同一次计数。指引页标明左上电源键，以及右侧由上至下的上键、下键、确定键。

可通过 USB 串口调整并持久化：

```text
at+config=common,guide_count,5   # 默认/当前剩余展示次数，0 表示关闭
at+config=?                      # 查询 guide_count/guide_remaining
```

修改 `guide_count` 会同时重置当前剩余次数；以后再次进入自检模式时，也会按该值重新开始计数。

## 上位机治具:两种形态

同一套判定逻辑(`tools/factory_test.py` 内的 `run_auto/run_disp/run_btn`)两处复用:

- **命令行** `tools/factory_test.py`:单台、可脚本化、可接 MES 流水线。
- **GUI 治具** `tools/factory_gui.py`:8 工位超大色块、多设备并行、整块绿/红一眼可辨。
  两种工作模式(顶部可切):
  - **被动监听(默认,配合设备自助自检)**:PC 只读设备 printf 的 `+TEST` 行、自动记台账,工人只碰设备。
  - **主动模式**:PC 驱动 `AT+TEST?`,屏幕/按键人工项在 PC 上的色块内确认。
  依赖 `pyserial` + 标准库 `tkinter`(见 `tools/requirements-factory.txt`)。运行:`python tools/factory_gui.py`
  命令行被动版:`python tools/factory_test.py --port <PORT> --listen`

### GUI 治具行为
- **热插拔即测**:每 ~0.8s 轮询串口,按 Espressif VID(0x303A)过滤;新设备插入 →
  自动分配空闲工位 → 后台线程开测;拔出 → 工位释放待下一台。
- **8 台并行**:每台一个后台线程做串口 I/O,结果经线程安全队列回主线程刷 UI。
- **超大色块**:等待(灰)/ 测试中(琥珀)/ 良品(绿·✓)/ 不良(红·✗·失败项)/ 异常(橙)。
- **人工项(可选)**:勾选“含人工项”后,自动项全过的设备会在**色块内**弹出巨大
  “通过/不通过”按钮做屏幕目视确认,再逐键捕获——每工位独立,天然一台台过。
- **良率看板**:顶部实时 总数 / ✓ / ✗ / 良率。
- **台账**:所有工位并发写同一 `factory_test_log.csv`(加锁,仅结果无 Key)。

## 产线工位流程

```
1. 夹具上电 → USB 连接(8 口 USB Hub 接一台产测 PC)
2. 烧 app 固件(esptool;严禁 erase-flash,会毁 cardid 身份)
3. 写身份:  python tools/make_cardid.py --port <PORT> --hw A1.0 [--order T001]
             (读 MAC→生成 cardid bin→烧录→追加 cardid_ledger.csv;ProductKey 走 CARDID_PK/--pk-file)
4. 复位设备
5. 功能测试(二选一):
   - GUI:  python tools/factory_gui.py    → 插入即测,看色块
   - CLI:  python tools/factory_test.py --port <PORT> --hw A1.0 --order T001
   均写 factory_test_log.csv,判总 PASS/FAIL
```

- **顺序**:先烧身份(第 3 步)再功能测试(第 5 步),这样 `AT+TEST=ID` 能一并校验身份写入成功。
- **解耦**:`make_cardid.py` 负责 Key + 台账(含明文 Key,0600、gitignore);
  `factory_test.py` 只做功能判定 + 测试台账(仅结果,无 Key)。

## 安全约束(维持不变)

- `cardid_ledger.csv` 含明文 Key → 0600、gitignore、绝不入库。
- ProductKey 只走 USB 串口 / cardid bin,**绝不进 BLE / 日志**;`AT+TEST=ID` 只回 pk 指纹不回明文。
- `factory_test_log.csv` 仅含 SN(=MAC,非机密)+ 结果,已 gitignore。

## 涉及文件

| 文件 | 作用 |
|---|---|
| `components/core/services/{include/services,src}/factory_test.{h,c}` | AT+TEST 解析 + 判据阈值(纯逻辑,gcc 单测) |
| `components/platform/platform_esp32/src/provision_console.c` | `handle_line` 拦截 AT+TEST → 回调;任务栈放宽 4096 |
| `components/app/src/app.c` | `on_factory_test`:硬件访问 + 报告拼装;`ft_btn_test` 逐键捕获 |
| `components/ui/presentation/src/ui_factory.c` | `AT+TEST=DISP` 全屏色块自检屏 |
| `tools/factory_test.py` | PC 功能测试(CLI + 共享库 run_auto/run_disp/run_btn) |
| `tools/factory_gui.py` | PC 治具 GUI(8 工位并行 · 插入即测 · 超大色块) |
| `tools/requirements-factory.txt` | 治具依赖(pyserial;tkinter 标准库) |

## 阈值标定(上线前建议)

- `FT_MIC_PEAK_MIN`:用金板实测回环峰值,取良品分布下限的 ~50% 作阈值。
- `FT_HEAP_MIN`:按当前开机空闲堆余量留足裕度(当前 ~55–68KB,阈值 20000 偏保守,可上调)。
