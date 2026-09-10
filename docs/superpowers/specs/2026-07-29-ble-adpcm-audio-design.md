# 设计：BLE 流式 ADPCM 音频 + 本地音频片段（可 BLE 写入/替换）

- 日期：2026-07-29
- 目标平台：ESP32-C3（单核 RISC-V @160MHz，无 PSRAM，8MB flash），ES8311 + I2S，NimBLE GATT
- 关联：`doc/ble-protocol-spec.md`（协议）、`doc/ARCHITECTURE.md`（分层）

## 1. 目标

1. **BLE 流式播放**：上位机把音频经 BLE 边传边放（实时），格式 16kHz 单声 IMA-ADPCM。
2. **本地音频片段**：C 函数 `audio_clip_play(clip_id)` 播放事件音（开机 / BLE 连接 / 游戏结束）。
3. **纯 BLE 写入片段**：**默认无任何音效**；片段只能经 BLE 写入 SPIFFS，**文件存在才播放，否则静音跳过**。
4. **兼容现有 RTTTL**：不破坏现有方波乐谱播放；三种音源共用一个播放任务、相互抢占。
5. 上位机 `tools/ble_card_client.py` 支持发送音频流、写入/替换片段。

## 2. 非目标（本次不做）

- **复音/混音**：设备任一时刻只放一个声音，三种音源顺序播放、互斥抢占，不叠加。
- **MP3/真实录音流**：只做 ADPCM（对小喇叭足够，且解码近零开销）。
- **构建时打包默认片段**：无默认音效，`audio` 分区开机为空。
- **低延迟实时**（<100ms）：流播放允许 ~0.5s 预缓冲，音乐播放场景无需低延迟。

## 3. 总体架构

```
上位机(client)                    设备(ESP32-C3)
  wav → 16k单声 → IMA-ADPCM
        │                          NimBLE host 任务          音频播放任务(单一,多路复用)
        │  BLE GATT 帧              ┌─────────────┐          ┌──────────────────────────┐
        ├─ 0x04 流 BEGIN/DATA/END ─▶│ on_cfg_msg  │──ADPCM──▶│ 环形缓冲 → adpcm解码 → I2S │
        │                          │ (frame_reasm │  入环    │  (STREAM 源)             │
        ├─ 0x05 片段 BEGIN/DATA/END▶│  已重组)     │──写──────│                          │
        │                          └──────┬──────┘  SPIFFS   │ RTTTL 源: 方波合成 → I2S  │
        │                                 │ (audio_store)     │ CLIP  源: 读SPIFFS→解码→I2S│
        └─ 0x01 JSON / 0x03 乐谱(不变)     ▼                  └──────────────────────────┘
                                     audio 分区(512KB SPIFFS,可写)
```

三种音源、一个消费者（播放任务），任一新请求抢占当前播放（沿用现有 `s_music_interrupt` 机制，泛化为 `s_play_interrupt`）。

## 4. ADPCM 编解码（可复用核心，纯逻辑）

**格式**：IMA-ADPCM，单声道，**连续流**（无 WAV 那种分块头），初始 `predictor=0, index=0`。4bit/样本。
- 步长表 89 项、索引表 16 项为标准 IMA 值。
- 解码单 nibble `n`：`step=STEP[index]; diff=step>>3; if(n&4)diff+=step; if(n&2)diff+=step>>1; if(n&1)diff+=step>>2; if(n&8)pred-=diff else pred+=diff;` 之后 `pred` 限幅 `[-32768,32767]`，`index+=INDEX[n]` 限幅 `[0,88]`。
- **nibble 打包顺序：低 nibble = 先出现的样本**，高 nibble 次之。编码器（Python）与解码器（C）必须一致，用 round-trip 单测锁死。
- 编码为标准逆过程（选使误差最小的 nibble）。

**放置**：
- C 解码器：`components/core/services/src/adpcm.c`（纯逻辑、无 IDF 依赖、gcc 可单测）。流与片段共用。
- Python 编码器：`tools/adpcm_codec.py`（纯 Python，**不依赖 `audioop`**——它在 Py3.13 已移除）。`wav_to_adpcm.py` 与 `ble_card_client.py` 共用它。

**接口（C）**：
```c
typedef struct { int16_t predictor; int8_t index; } adpcm_state_t;
void adpcm_state_reset(adpcm_state_t *st);
// 解码 n 个字节(2n 个样本)到 out;返回写入样本数。
int  adpcm_decode(adpcm_state_t *st, const uint8_t *in, int nbytes, int16_t *out);
```

## 5. 本地片段存储（可写 SPIFFS）

**分区**（`partitions.csv` 新增一行）：
```
audio, data, spiffs, , 0x80000,     # 512KB 可写,存 BLE 写入的音频片段
```
- 不进 `merge_bin`（纯运行时数据，如 imgstore）→ **`release.py` 不改**。
- 首次挂载**自动格式化成空**（`esp_vfs_spiffs_register` 带 `format_if_mount_failed=true`）。

**片段容器**（写入 SPIFFS 的文件内容，小端）：
```
[0..3]  magic  "ADP1"
[4..5]  sample_rate u16   (16000)
[6]     channels u8       (1)
[7]     reserved u8
[8..11] num_samples u32   (精确样本数,末 nibble 可能是 padding)
[12..]  ADPCM nibble 流
```

**clip_id 枚举 + 文件名**（固定集合，避免任意路径；`services/audio_clip.h`）：
```c
typedef enum { CLIP_BOOT=0, CLIP_CONNECT=1, CLIP_GAMEOVER=2, CLIP_COUNT } clip_id_t;
// 文件名: "/audio/clip%d.adpcm" % id
```
加新事件音只需扩枚举 + 在 app.c 事件点调用，一行改动。

**播放语义**：`audio_clip_play(clip_id)` → 若 `clip<id>.adpcm` 存在且 magic 合法 → 交播放任务；否则**静音返回**（无回退 beep）。

## 6. BLE 协议（在现有 `[ver][type][len]` 帧之上，payload 为子协议）

两种**独立**音频操作，别混：

**`type=0x04` 音频流（实时播放，不存盘）**
| op | payload | 说明 |
|----|---------|------|
| BEGIN `0x00` | `[0x00][sr u16 LE]` | 开流:复位解码器状态,进入 STREAM 源,预缓冲 |
| DATA `0x01`  | `[0x01][adpcm 字节…]` | 追加进环形缓冲 |
| END `0x02`   | `[0x02]` | 收流结束:排空缓冲后停 |

**`type=0x05` 片段写入/替换（存 SPIFFS,持久）**
| op | payload | 说明 |
|----|---------|------|
| BEGIN `0x00` | `[0x00][clip_id u8][total_len u16 LE]` | 开始写:total_len=容器文件总字节。clip_id≥CLIP_COUNT 拒绝 |
| DATA `0x01`  | `[0x01][字节…]` | 追加写入临时文件 `clip<id>.tmp` |
| END `0x02`   | `[0x02]` | 校验 magic + 长度 → 原子 `rename` 覆盖 `clip<id>.adpcm` |

- 状态回报复用现有 `CFG_ST_*`（ACK/ERR_SEQ/ERR_TOO_LONG/ERR_PARSE/ERR_STORAGE/DONE）经 notify `…0011`。
- **原子写**：写 `.tmp` → END 时校验通过再 `rename`；上传中断/失败则旧片段不受影响。
- 需在 `components/core/ports/include/hal/hal_config.h` 新增常量 `CFG_MSG_AUDIO=0x04`、`CFG_MSG_AUDIO_CLIP=0x05`（紧接现有 `CFG_MSG_SCORE=0x03`）。

## 7. 播放任务多路复用与抢占

将现有 RTTTL 专用的 `music_player_task` 泛化为 `audio_player_task`：
- 音源枚举 `PLAY_RTTTL / PLAY_STREAM / PLAY_CLIP`。请求经 `s_play_req`（源+参数）+ 信号量提交；`s_play_interrupt` 抢占当前。
- **RTTTL**：不变（方波合成，其原采样率）。
- **STREAM**：从环形缓冲取 ADPCM → `adpcm_decode` 到静态 PCM 块 → `audio_service_play`；预缓冲 ~0.5s 再起播，欠载补静音；END+排空或被抢占则停。
- **CLIP**：打开 SPIFFS 文件 → 逐块读 ADPCM → 解码 → 播放。
- 每次切源调 `audio_service_configure(&s_au, 16000, 16, 1)`（ADPCM 源）或 RTTTL 原配置。
- PCM 输出缓冲用**静态区**（沿用 `s_music_block`,勿放任务栈——避免 I2S 调用链撑爆栈）。

**线程规则（沿用现有铁律）**：
- 流 DATA 在 NimBLE host 任务里**只入环形缓冲**（非阻塞;满则背压/丢弃并回 ERR），绝不在 host 任务里阻塞写 I2S。
- 片段写入沿用现有收图(`jpeg_store`)在 host 任务里增量写 flash 的既有模式（图片上传已验证可行,片段更小）。若实测影响 BLE 稳定性,退路是加一个专用写盘任务经队列消费（spec 记为备选,不默认做）。

## 8. 事件接入（app.c）

在以下事件点调用 `audio_clip_play(...)`（未写入片段则静音）：
- 开机初始化完成 → `CLIP_BOOT`
- BLE 连接成功回调 → `CLIP_CONNECT`
- 游戏结束 `on_game_result` → `CLIP_GAMEOVER`

从 BLE host 回调触发时须非阻塞（只入队+给信号量，播放任务干活）。

## 9. 上位机 `ble_card_client.py`

- **发送音频流**：选 wav → 用 `soundfile`/`wave` 读 → 重采样 16k 单声（优先 `numpy` 线性重采样，避免重依赖）→ `adpcm_codec` 编码 → 按 0x04 BEGIN/DATA/END 分片限速发送（按 MTU 分包）。
- **写入/替换片段**：选 clip_id + wav → 编码 → 封容器头 → 按 0x05 BEGIN/DATA/END 上传。
- 编码器与 `wav_to_adpcm.py` 共用 `tools/adpcm_codec.py`。
- 依赖尽量轻：wav 用标准库 `wave`；mp3 输入非本次目标（要则提示先转 wav）。

## 10. 组件与文件清单

**新增**
- `components/core/services/include/services/adpcm.h` / `src/adpcm.c`（IMA-ADPCM 解码,纯逻辑）
- `components/core/services/include/services/audio_rx.h` / `src/audio_rx.c`（0x04/0x05 子协议帧解析,纯逻辑,仿 `score_rx`;不含环形缓冲/SPIFFS）
- `components/core/services/include/services/audio_clip.h`（clip_id 枚举 + 容器头定义,共享）
- `components/platform/platform_esp32/src/audio_store.c`（SPIFFS 读/写/原子替换/存在性检查）
- `components/platform/platform_esp32/src/audio_stream.c`（流式环形缓冲）
- `tools/adpcm_codec.py`（纯 Python IMA-ADPCM 编/解码,与 C 对齐）
- `tools/wav_to_adpcm.py`（wav→容器文件,离线工具/可选）

**修改**
- `partitions.csv`（+audio 512KB spiffs）
- `components/app/src/app.c`（播放任务泛化、事件接入、`on_cfg_message` 处理 0x04/0x05）
- 共享头（`CFG_MSG_AUDIO`/`CFG_MSG_AUDIO_CLIP` 常量）
- `components/platform/platform_esp32/include/platform/platform_factory.h`（音频存储/流/播放片段的平台 API 声明）
- `components/platform/platform_esp32/CMakeLists.txt`（+`spiffs` 依赖、新源文件）
- `components/core/services/CMakeLists.txt`（+adpcm/audio_rx 源）
- `components/platform/platform_esp32/include/platform/board_config.h`（采样率常量、环形缓冲大小、SPIFFS 挂载点）
- `tools/ble_card_client.py`（发送流 + 写入片段 UI/逻辑）
- `doc/ble-protocol-spec.md`（新增 0x04/0x05 音频协议章节,版本 +1）

## 11. 测试

**纯逻辑(gcc,秒级)**
- ADPCM round-trip：Python 编码已知 PCM(正弦/扫频/静音)→ C 解码 → 与原 PCM 比对（IMA 有量化误差,校验相关性/RMS 在阈内 + 头尾对齐）。
- ADPCM 决定性：同输入编码稳定、解码可复现。
- `audio_rx` 帧解析：BEGIN/DATA/END 时序、越界、clip_id 非法、长度不符 → 返回码正确。

**设备/集成**
- 客户端发流 → 能听到声音；BLE 连接期间上传/流播不断连。
- 上传片段 → 触发事件 → 出声；未上传 → 静音无报错。
- 存在性：无文件静音；原子替换：上传中断后旧片段仍可播。
- RTTTL 与音频源相互抢占正确；播放任务不撑栈。
- 构建：分区表在 8MB 内合法；`idf.py build` 通过；`release.py` 打包（audio 分区不在镜像内,运行时格式化）。

## 12. 风险与 RAM 预算

- **RAM 紧**（无 PSRAM,开机空闲堆 ~58KB）：新增 SPIFFS 挂载(~几 KB,含 cache) + 流环形缓冲(~8KB ADPCM) + 静态 PCM 块(复用现有 2KB) + 解码状态(极小)，合计约 +15KB 量级。**实现后必须实测空闲堆**；不够则调小 SPIFFS cache / 环形缓冲。
- **host 任务写 flash**：沿用图片上传的既有模式；若 BLE 断连,启用备选写盘任务。
- **ADPCM 互操作**：Python 编码器与 C 解码器算法/打包必须逐位一致 → round-trip 单测为准入门槛。
- 512KB@16k 单声 ADPCM ≈ 64s 总容量,分几个片段够用。

## 13. 分阶段实现建议（交给 writing-plans 细化）

1. ADPCM 编解码器（C 解码 + Python 编码）+ round-trip 单测（先锁互操作）。
2. audio 分区 + `audio_store`（SPIFFS 读写/原子替换/存在性）。
3. 播放任务泛化 + `audio_clip_play` + 事件接入（CLIP 源先跑通）。
4. 流式：`audio_stream` 环形缓冲 + 0x04 协议 + STREAM 源。
5. 片段替换：0x05 协议 + 写盘。
6. 上位机：`adpcm_codec.py` + 发流 + 写片段。
7. 协议文档 + 集成联调 + RAM 实测。
