# Spec C：BLE 下发 RTTTL 乐谱 + 8bit 方波播放

- 日期：2026-07-17
- 目标芯片：ESP32-C3（无 FPU → 整数方波合成；ES8311 + I2S 16bit）
- 前置：Spec A 传输通道（[ver][type][len] 分帧 + frame_reasm + notify）已实现
- 状态：设计定稿，待实现

## 1. 背景与目标

在结构化通道上新增 `type=0x03`：小程序/上位机下发一段 **RTTTL**（诺基亚铃声格式，文本、单音、通用）乐谱，卡片解析后用**整数方波合成 PCM**（8bit 芯片音味），经 `audio_service_play` 播放一遍。支持**长曲**（分片累积到 RAM 缓冲）。乐谱不持久化（掉电即丢）。

同时**移除**当前占用音频的麦克风直通自检（`platform_audio_passthrough` 死循环），改为常驻 music player 任务，音频空闲时不发声、收到乐谱才播。

## 2. 范围

**包含**：type=0x03 分片子协议（BEGIN/DATA/END → RAM 缓冲，支持长曲）；RTTTL 解析（纯逻辑）；整数方波合成（纯逻辑）；music player 后台任务；移除 passthrough 自检；notify 回报；上位机 `ble_card_client.py` 加「乐谱」页（RTTTL 文本 + 内置示例）。

**不包含**：多音/和弦（RTTTL 是单音）；乐谱持久化；MIDI。

## 3. 架构（六边形分层）

| 层 | 文件 | 责任 |
|---|---|---|
| core/services | `score_rx.c`（新增）| 纯逻辑：BEGIN/DATA/END 分片累积到调用方给的 RAM 缓冲，收齐置 done。可单测 |
| core/services | `rtttl.c`（新增）| 纯逻辑：解析 RTTTL header + 逐音符迭代 `(freq_hz, ms)`。可单测 |
| core/services | `square_synth.c`（新增）| 纯逻辑：给 freq/ms/采样率，逐块填 int16 方波(相位跨块保持)。可单测 |
| app | `app.c` | 移除 passthrough 自检；起 music player 任务；`on_cfg_message` 加 type=0x03；开机接线 |
| 工具 | `tools/ble_card_client.py` | 新增「乐谱」页：RTTTL 文本框 + 内置示例 + 分片发送 |

数据流：
```
小程序分片写(type=0x03) → frame_reasm 收单帧 → app → score_rx 累积到 s_rx_buf
  → (END 收齐) rtttl_init 校验 → 复制到 play_buf + 给信号量 → notify [0x03][status]
  → music task: rtttl 逐音符 → square 分块合成 → audio_service_play
```

## 4. 传输子协议（type=0x03，支持长曲）

每个 type=0x03 帧的 payload（与 JPG 子协议同构，但目标是 RAM 而非 flash）：
```
BEGIN: [0x00][total_len:2 小端]   → 校验 total_len ≤ 缓冲容量,重置写指针
DATA:  [0x01][chunk...]           → 顺序追加到 RAM 缓冲
END:   [0x02]                     → 校验 written==total_len → 收齐
```
- RAM 缓冲 `s_rx_buf[SCORE_MAX]`，`SCORE_MAX = 4096`（长曲 RTTTL 一般 ≤2KB，4KB 留余量；常驻 DRAM 4KB 可接受）。
- 短曲同样走此协议（BEGIN + 1~N 个 DATA + END）。
- notify `[0x03][status]`：0=收齐且 RTTTL 合法(开始播)，1=时序/帧错(未BEGIN先DATA、END长度不符)，2=RTTTL 解析失败，3=超长(total_len>SCORE_MAX)。
- 相邻帧 >3s 未收齐 → 丢弃重置。

## 5. score_rx（core/services，纯逻辑）
```c
typedef struct { int receiving; uint32_t total, written; uint8_t *buf; int cap; } score_rx_t;
void score_rx_init(score_rx_t *r, uint8_t *buf, int cap);
// 处理一个 type=0x03 帧。返回状态码 0/1/3(见上,2 由上层 rtttl 校验产生)。收齐 → *done=1,此时 buf[0..written)。
int  score_rx_frame(score_rx_t *r, const uint8_t *payload, int len, int *done);
```
- 无 sink：直接写自带 `buf`（RAM，比 JPG 的 flash 简单）。
- 校验：未 BEGIN 收 DATA/END → 1；BEGIN 的 total>cap → 3；END 时 written≠total → 1。

## 6. RTTTL 解析（`rtttl.c`，纯逻辑）
```c
typedef struct { const char *p, *end; int def_dur, def_oct, bpm, whole_ms; } rtttl_t;
int rtttl_init(rtttl_t *r, const char *s, int len);   // 解析 header,定位音符段;格式错<0
int rtttl_next(rtttl_t *r, int *freq_hz, int *ms);    // 1=有音符 0=结束 <0=错误; freq=0 为休止
```
- header：`名:d=4,o=5,b=100:` → def_dur/def_oct/bpm；`whole_ms = 4*60000/bpm`。缺省 d=4,o=6,b=63（RTTTL 默认）。
- 音符：`[时值][音名a-g|p][#][八度][.]`，`ms = whole_ms/dur`，附点 ×3/2。
- 频率：整数频率表（八度4 的 12 个半音 Hz：c=262…b=494），其它八度按 ×2 移位（`>>`/`<<`，无 FPU、无 pow）。八度范围约 1–8。

## 7. 方波合成（`square_synth.c`，无 FPU）
```c
// 逐块填方波:半周期采样数 half=sr/(2*freq);计数到 half 翻转符号。相位 *phase 跨块保持。
void square_fill(int16_t *buf, int n, int freq_hz, int sr, int *phase, int amp);
```
- 采样率 8000Hz / 16bit / 单声道。休止(freq=0) → 填 0。幅度 amp 固定(如 ±6000)，响度由 `audio_service_set_volume` 控制。
- 块缓冲 1024 采样(2KB)；长音符分多块播；DRAM 友好。

## 8. music player 任务 + 线程
- `music_player_task`（常驻）：等二值信号量 → `audio_service_configure(8000,16,1)` → 遍历 play_buf 里的 RTTTL：每音符分块 `square_fill` + `audio_service_play` → 播完回到等待。
- `on_cfg_message`（host task）type=0x03：`score_rx_frame` 累积；`done` 时 `rtttl_init` 校验，失败 notify=2；成功则把 s_rx_buf 复制到 play_buf、置"中断当前"标志、`xSemaphoreGive` 唤醒、notify=0。**不在 host task 合成/播放**（避免阻塞 BLE）。
- 抢占：新曲到来时置中断标志，music task 在音符边界检查 → 停当前、切新曲。
- 双缓冲：接收缓冲 `s_rx_buf` 与播放缓冲 `play_buf` 分开，END 时复制，避免 host 写与 music 读竞争。

## 9. 移除 passthrough
- `app.c`：删除 `audio_selftest_task`（跑 `platform_audio_passthrough` 死循环独占音频）。改为 `xTaskCreate(music_player_task,...)`。音频初始化后空闲。

## 10. 上位机「乐谱」页（`tools/ble_card_client.py`）
- 新增 Notebook 页：RTTTL 多行文本框 + 内置示例下拉（Mario / Tetris / Nokia 等，预置几首）+ 发送按钮。
- 发送：把 RTTTL(UTF-8) 按 type=0x03 分片：BEGIN `[0x00][total_len:2]` → 多个 DATA `[0x01][chunk]`(chunk≤220) → END `[0x02]`，每帧走现有 `_write_frame`。

## 11. 依赖 / 内存 / 风险
- 无新组件依赖（纯软件合成 + 现有 audio_service/ES8311）。
- 静态 DRAM 增量：`s_rx_buf` 4KB + `play_buf` 4KB + 块缓冲 2KB ≈ 10KB。当前剩 ~68KB 堆/DRAM 余量，可接受（远小于压垮 BLE 的阈值）。
- 无 FPU：方波用整数移位/计数，避免 `sinf`/`pow`（参考 audio_service.c 中 sinf 看门狗规避的教训，方波无此问题）。
- 播放期间 music task 持续 `audio_service_play`（阻塞写 I2S），是独立任务不影响 BLE/LVGL。

## 12. 测试
- `score_rx`（Unity）：BEGIN→DATA×N→END 收齐；未 BEGIN 先 DATA/END → 1；END 长度不符 → 1；BEGIN 超长 → 3。
- `rtttl`（Unity）：header d/o/b 解析(含缺省)、各音符(时值/八度/#/附点/休止)、频率表值、坏格式 <0、迭代到 0 结束。
- `square_synth`（Unity）：半周期翻转正确、相位跨块连续、freq=0 全零。
- 硬件联调：上位机发 `Mario:d=4,o=5,b=100:...` → 扬声器播放；长曲(>256B 多帧)完整播放；坏 RTTTL → notify=2；音量随卡片音量；确认移除 passthrough 后麦克风不再自动回放。

## 13. 环境备注
- 本仓库非 git，spec 不做 git 提交。
- host_test 环境已知损坏，纯逻辑单测执行以固件 build + 硬件联调为准。
- 无新增分区，烧录无需 erase-flash（除非同时首次带 Spec B 的 imgstore 分区）。
