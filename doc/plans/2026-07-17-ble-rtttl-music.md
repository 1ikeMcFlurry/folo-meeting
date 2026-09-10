# BLE RTTTL 乐谱 + 8bit 方波播放 实现计划（Spec C）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 通过 BLE（复用 Spec A 通道，type=0x03）分片下发 RTTTL 乐谱，累积到 RAM 缓冲后由常驻 music 任务用整数方波合成 PCM 播放一遍；移除占用音频的麦克风直通自检；上位机加「乐谱」页。

**Architecture:** 三个纯逻辑模块（`score_rx` 分片累积、`rtttl` 解析、`square_synth` 整数方波）在 core/services 可单测；`app.c` 起 `music_player_task`（等信号量→解析→分块合成→`audio_service_play`），`on_cfg_message` 加 type=0x03 分支（host task 只累积/校验/唤醒，不阻塞 BLE）；双缓冲避免竞争，新曲抢占旧曲。

**Tech Stack:** ESP-IDF v5.5.2、ESP32-C3（无 FPU，整数方波）、ES8311 + I2S 16bit、FreeRTOS 信号量、Unity、Python(bleak/tkinter 上位机)。

**环境约定：**
- 本仓库**非 git**：Commit 步骤跳过，以 `idf.py -C /home/cjiio/trae_card build` 通过为判据。
- host_test 已知损坏：纯逻辑测试文件照写并挂 host_test，验证以固件 build + 硬件联调为准。
- 无新增分区/依赖，烧录无需 erase-flash。

---

## 文件结构

| 文件 | 责任 | 动作 |
|---|---|---|
| `components/core/services/include/services/score_rx.h` / `src/score_rx.c` | type=0x03 分片累积到 RAM 缓冲(纯逻辑) | 新增 |
| `components/core/services/include/services/rtttl.h` / `src/rtttl.c` | RTTTL 解析→逐音符(freq,ms)(纯逻辑) | 新增 |
| `components/core/services/include/services/square_synth.h` / `src/square_synth.c` | 整数方波填充(纯逻辑) | 新增 |
| `components/core/services/test/test_score_rx.c` / `test_rtttl.c` / `test_square_synth.c` | Unity 测试 | 新增 |
| `components/core/services/CMakeLists.txt` / `test/CMakeLists.txt` | 注册源/测试 | 修改 |
| `components/app/src/app.c` | 移除 passthrough；music 任务；type=0x03 接线 | 修改 |
| `tools/ble_card_client.py` | 加「乐谱」页 | 修改 |

依赖顺序：T1（score_rx）→ T2（rtttl）→ T3（square_synth）→ T4（app 接线）→ T5（上位机）→ T6（整机）。T1–T3 纯新增可 build；T5 独立(python)。

---

## Task 1: score_rx（分片累积，core/services）

**Files:** Create `score_rx.h`/`score_rx.c`/`test_score_rx.c`；Modify services CMakeLists + test CMakeLists

- [ ] **Step 1: 头文件** `components/core/services/include/services/score_rx.h`
```c
// components/core/services/include/services/score_rx.h
#pragma once
#include <stdint.h>

#define SCORE_OP_BEGIN 0x00   // [0x00][total_len:2 小端]
#define SCORE_OP_DATA  0x01   // [0x01][chunk...]
#define SCORE_OP_END   0x02   // [0x02]

typedef struct { int receiving; uint32_t total, written; uint8_t *buf; int cap; } score_rx_t;

void score_rx_init(score_rx_t *r, uint8_t *buf, int cap);
// 处理一个 type=0x03 帧。返回 0=OK/进行中 1=时序/长度错 3=超长。收齐→*done=1,buf[0..written)。
int  score_rx_frame(score_rx_t *r, const uint8_t *payload, int len, int *done);
```

- [ ] **Step 2: 测试** `components/core/services/test/test_score_rx.c`
```c
#include "unity.h"
#include "services/score_rx.h"
#include <string.h>

TEST_CASE("score_rx normal begin/data/end", "[scorerx]") {
    uint8_t buf[64]; score_rx_t r; score_rx_init(&r, buf, sizeof buf); int done;
    uint8_t begin[3]={0x00,4,0};                 // total=4
    TEST_ASSERT_EQUAL_INT(0, score_rx_frame(&r,begin,3,&done)); TEST_ASSERT_EQUAL_INT(0,done);
    uint8_t data[5]={0x01,'a','b','c','d'};
    TEST_ASSERT_EQUAL_INT(0, score_rx_frame(&r,data,5,&done)); TEST_ASSERT_EQUAL_INT(0,done);
    uint8_t end[1]={0x02};
    TEST_ASSERT_EQUAL_INT(0, score_rx_frame(&r,end,1,&done)); TEST_ASSERT_EQUAL_INT(1,done);
    TEST_ASSERT_EQUAL_UINT32(4, r.written);
    TEST_ASSERT_EQUAL_INT(0, memcmp(buf,"abcd",4));
}
TEST_CASE("score_rx data before begin → 1", "[scorerx]") {
    uint8_t buf[64]; score_rx_t r; score_rx_init(&r, buf, sizeof buf); int done;
    uint8_t data[2]={0x01,'x'};
    TEST_ASSERT_EQUAL_INT(1, score_rx_frame(&r,data,2,&done));
}
TEST_CASE("score_rx end length mismatch → 1", "[scorerx]") {
    uint8_t buf[64]; score_rx_t r; score_rx_init(&r, buf, sizeof buf); int done;
    uint8_t begin[3]={0x00,10,0}; score_rx_frame(&r,begin,3,&done);
    uint8_t data[3]={0x01,'a','b'}; score_rx_frame(&r,data,3,&done);
    uint8_t end[1]={0x02};
    TEST_ASSERT_EQUAL_INT(1, score_rx_frame(&r,end,1,&done)); TEST_ASSERT_EQUAL_INT(0,done);
}
TEST_CASE("score_rx begin oversize → 3", "[scorerx]") {
    uint8_t buf[8]; score_rx_t r; score_rx_init(&r, buf, sizeof buf); int done;
    uint8_t begin[3]={0x00, 100, 0};             // total=100 > cap 8
    TEST_ASSERT_EQUAL_INT(3, score_rx_frame(&r,begin,3,&done));
}
```

- [ ] **Step 3: 实现** `components/core/services/src/score_rx.c`
```c
// components/core/services/src/score_rx.c —— type=0x03 分片累积到 RAM(纯逻辑)
#include "services/score_rx.h"
#include <string.h>

void score_rx_init(score_rx_t *r, uint8_t *buf, int cap) {
    r->receiving = 0; r->total = 0; r->written = 0; r->buf = buf; r->cap = cap;
}

int score_rx_frame(score_rx_t *r, const uint8_t *payload, int len, int *done) {
    if (done) *done = 0;
    if (len < 1) return 1;
    uint8_t op = payload[0];
    const uint8_t *b = payload + 1;
    int bl = len - 1;

    if (op == SCORE_OP_BEGIN) {
        if (bl < 2) return 1;
        uint32_t total = (uint32_t)b[0] | ((uint32_t)b[1] << 8);
        if (total > (uint32_t)r->cap) { r->receiving = 0; return 3; }
        r->receiving = 1; r->total = total; r->written = 0;
        return 0;
    }
    if (op == SCORE_OP_DATA) {
        if (!r->receiving) return 1;
        if (bl > 0) {
            if (r->written + (uint32_t)bl > (uint32_t)r->cap) { r->receiving = 0; return 3; }
            memcpy(r->buf + r->written, b, (size_t)bl);
            r->written += (uint32_t)bl;
        }
        return 0;
    }
    if (op == SCORE_OP_END) {
        if (!r->receiving) return 1;
        r->receiving = 0;
        if (r->written != r->total) return 1;
        if (done) *done = 1;
        return 0;
    }
    return 1;
}
```

- [ ] **Step 4: CMakeLists**：`components/core/services/CMakeLists.txt` SRCS 追加 `"src/score_rx.c"`；`test/CMakeLists.txt` SRCS 追加 `"test_score_rx.c"`。先 Read 再改。

- [ ] **Step 5: 验证**：`idf.py -C /home/cjiio/trae_card build`。Expected: 编译通过。

- [ ] **Step 6: Commit**：非 git，跳过。

---

## Task 2: rtttl 解析（core/services）

**Files:** Create `rtttl.h`/`rtttl.c`/`test_rtttl.c`；Modify CMakeLists

- [ ] **Step 1: 头文件** `components/core/services/include/services/rtttl.h`
```c
// components/core/services/include/services/rtttl.h
#pragma once

typedef struct {
    const char *p, *end;     // 音符段游标 / 结束
    int def_dur, def_oct, bpm, whole_ms;
} rtttl_t;

// 解析 RTTTL header(名:d,o,b:音符...),定位音符段。格式错返回 <0,成功 0。
int rtttl_init(rtttl_t *r, const char *s, int len);
// 取下一音符:1=有(填 *freq_hz,*ms;freq=0 为休止) 0=结束 <0=错误。
int rtttl_next(rtttl_t *r, int *freq_hz, int *ms);
```

- [ ] **Step 2: 测试** `components/core/services/test/test_rtttl.c`
```c
#include "unity.h"
#include "services/rtttl.h"
#include <string.h>

TEST_CASE("rtttl header defaults + whole_ms", "[rtttl]") {
    rtttl_t r; const char *s = "x:d=8,o=5,b=120:c";
    TEST_ASSERT_EQUAL_INT(0, rtttl_init(&r, s, (int)strlen(s)));
    TEST_ASSERT_EQUAL_INT(8, r.def_dur);
    TEST_ASSERT_EQUAL_INT(5, r.def_oct);
    TEST_ASSERT_EQUAL_INT(120, r.bpm);
    TEST_ASSERT_EQUAL_INT(2000, r.whole_ms);          // 4*60000/120
}
TEST_CASE("rtttl notes freq/ms + rest", "[rtttl]") {
    rtttl_t r; const char *s = "x:d=4,o=5,b=100:8c6,4a,p";   // whole=2400
    TEST_ASSERT_EQUAL_INT(0, rtttl_init(&r, s, (int)strlen(s)));
    int f, m;
    TEST_ASSERT_EQUAL_INT(1, rtttl_next(&r, &f, &m));
    TEST_ASSERT_EQUAL_INT(1048, f); TEST_ASSERT_EQUAL_INT(300, m);   // c6=262<<2, 2400/8
    TEST_ASSERT_EQUAL_INT(1, rtttl_next(&r, &f, &m));
    TEST_ASSERT_EQUAL_INT(880, f);  TEST_ASSERT_EQUAL_INT(600, m);   // a5=440<<1, 2400/4
    TEST_ASSERT_EQUAL_INT(1, rtttl_next(&r, &f, &m));
    TEST_ASSERT_EQUAL_INT(0, f);    TEST_ASSERT_EQUAL_INT(600, m);   // rest, dur4
    TEST_ASSERT_EQUAL_INT(0, rtttl_next(&r, &f, &m));
}
TEST_CASE("rtttl sharp and dotted", "[rtttl]") {
    rtttl_t r; const char *s = "x:d=4,o=5,b=120:c#6,8a.";   // whole=2000
    rtttl_init(&r, s, (int)strlen(s));
    int f, m;
    rtttl_next(&r, &f, &m);
    TEST_ASSERT_EQUAL_INT(1108, f);                 // c#6 = 277<<2
    TEST_ASSERT_EQUAL_INT(500, m);                  // 2000/4
    rtttl_next(&r, &f, &m);
    TEST_ASSERT_EQUAL_INT(880, f);                  // a5
    TEST_ASSERT_EQUAL_INT(375, m);                  // (2000/8)*3/2
}
```

- [ ] **Step 3: 实现** `components/core/services/src/rtttl.c`
```c
// components/core/services/src/rtttl.c —— RTTTL 解析(纯逻辑,无 FPU)
#include "services/rtttl.h"

// 八度4 的 12 个半音整数频率(c..b)
static const int OCT4[12] = {262,277,294,311,330,349,370,392,415,440,466,494};

static int is_digit(char c) { return c >= '0' && c <= '9'; }
static int rd_int(const char **pp, const char *end) {
    int v = 0, any = 0; const char *p = *pp;
    while (p < end && is_digit(*p)) { v = v * 10 + (*p - '0'); p++; any = 1; }
    *pp = p; return any ? v : -1;
}
static int note_semi(char c) {
    switch (c) {
        case 'c': return 0; case 'd': return 2; case 'e': return 4; case 'f': return 5;
        case 'g': return 7; case 'a': return 9; case 'b': return 11; default: return -1;
    }
}

int rtttl_init(rtttl_t *r, const char *s, int len) {
    const char *end = s + len, *p = s;
    while (p < end && *p != ':') p++;      // 跳过曲名
    if (p >= end) return -1;
    p++;                                    // 越过第一个 ':'
    r->def_dur = 4; r->def_oct = 6; r->bpm = 63;   // RTTTL 缺省
    while (p < end && *p != ':') {
        while (p < end && (*p == ',' || *p == ' ')) p++;
        if (p >= end || *p == ':') break;
        char key = *p; p++;
        if (p < end && *p == '=') p++;
        int v = rd_int(&p, end);
        if (key == 'd' && v > 0) r->def_dur = v;
        else if (key == 'o' && v > 0) r->def_oct = v;
        else if (key == 'b' && v > 0) r->bpm = v;
    }
    if (p < end && *p == ':') p++;
    r->p = p; r->end = end;
    if (r->bpm <= 0) r->bpm = 63;
    r->whole_ms = 4 * 60000 / r->bpm;
    return 0;
}

int rtttl_next(rtttl_t *r, int *freq_hz, int *ms) {
    const char *p = r->p, *end = r->end;
    while (p < end && (*p == ',' || *p == ' ')) p++;
    if (p >= end) { r->p = p; return 0; }

    int dur = rd_int(&p, end);
    if (dur <= 0) dur = r->def_dur;
    if (p >= end) { r->p = p; return -1; }

    char nc = *p; if (nc >= 'A' && nc <= 'Z') nc += 32; p++;
    int dot = 0, freq;
    if (nc == 'p') {
        freq = 0;
        for (int k = 0; k < 2; k++) {
            if (p < end && *p == '.') { dot = 1; p++; }
            else if (p < end && is_digit(*p)) { rd_int(&p, end); }   // 休止忽略八度
            else break;
        }
    } else {
        int semi = note_semi(nc);
        if (semi < 0) { r->p = p; return -1; }
        if (p < end && *p == '#') { semi++; p++; }
        int oct = r->def_oct;
        for (int k = 0; k < 2; k++) {
            if (p < end && *p == '.') { dot = 1; p++; }
            else if (p < end && is_digit(*p)) { oct = rd_int(&p, end); }
            else break;
        }
        int base = OCT4[semi % 12];
        int oo = oct + semi / 12;              // c# 之类不进位;b# 才进位
        if (oo > 4) base <<= (oo - 4);
        else if (oo < 4) base >>= (4 - oo);
        freq = base;
    }
    if (p < end && *p == '.') { dot = 1; p++; }   // 尾随附点

    int m = r->whole_ms / dur;
    if (dot) m = m * 3 / 2;
    *freq_hz = freq; *ms = m;
    r->p = p;
    return 1;
}
```

- [ ] **Step 4: CMakeLists**：services SRCS 追加 `"src/rtttl.c"`；test SRCS 追加 `"test_rtttl.c"`。

- [ ] **Step 5: 验证**：`idf.py -C /home/cjiio/trae_card build`。Expected: 通过。

- [ ] **Step 6: Commit**：跳过。

---

## Task 3: square_synth（整数方波，core/services）

**Files:** Create `square_synth.h`/`square_synth.c`/`test_square_synth.c`；Modify CMakeLists

- [ ] **Step 1: 头文件** `components/core/services/include/services/square_synth.h`
```c
// components/core/services/include/services/square_synth.h
#pragma once
#include <stdint.h>

// 逐块填方波:半周期采样数 half=sr/(2*freq);相位 *phase 跨块保持。freq<=0 → 全 0。
void square_fill(int16_t *buf, int n, int freq_hz, int sr, int *phase, int amp);
```

- [ ] **Step 2: 测试** `components/core/services/test/test_square_synth.c`
```c
#include "unity.h"
#include "services/square_synth.h"

TEST_CASE("square half-period + phase continuity", "[square]") {
    int16_t b[16]; int ph = 0;
    square_fill(b, 16, 1000, 8000, &ph, 6000);      // half = 8000/2000 = 4
    for (int i = 0; i < 16; i++) {
        int expect = ((i / 4) % 2 == 0) ? 6000 : -6000;
        TEST_ASSERT_EQUAL_INT16(expect, b[i]);
    }
    TEST_ASSERT_EQUAL_INT(0, ph);                   // 16 % (2*4) == 0
}
TEST_CASE("square rest is zero", "[square]") {
    int16_t b[8]; int ph = 5;
    square_fill(b, 8, 0, 8000, &ph, 6000);
    for (int i = 0; i < 8; i++) TEST_ASSERT_EQUAL_INT16(0, b[i]);
    TEST_ASSERT_EQUAL_INT(0, ph);
}
```

- [ ] **Step 3: 实现** `components/core/services/src/square_synth.c`
```c
// components/core/services/src/square_synth.c —— 整数方波合成(无 FPU)
#include "services/square_synth.h"

void square_fill(int16_t *buf, int n, int freq_hz, int sr, int *phase, int amp) {
    if (freq_hz <= 0) {
        for (int i = 0; i < n; i++) buf[i] = 0;
        *phase = 0;
        return;
    }
    int half = sr / (2 * freq_hz);
    if (half < 1) half = 1;
    int ph = *phase;
    for (int i = 0; i < n; i++) {
        buf[i] = (ph < half) ? (int16_t)amp : (int16_t)(-amp);
        ph++;
        if (ph >= 2 * half) ph = 0;
    }
    *phase = ph;
}
```

- [ ] **Step 4: CMakeLists**：services SRCS 追加 `"src/square_synth.c"`；test SRCS 追加 `"test_square_synth.c"`。

- [ ] **Step 5: 验证**：`idf.py -C /home/cjiio/trae_card build`。Expected: 通过。

- [ ] **Step 6: Commit**：跳过。

---

## Task 4: app.c 接线（music 任务 + type=0x03 + 移除 passthrough）

**Files:** Modify `components/app/src/app.c`

- [ ] **Step 1: 顶部加 include**。在现有 `#if PERIPH_AUDIO` include 块（`#include "services/audio_service.h"` 等）里追加 music 相关头。把
```c
#if PERIPH_AUDIO
#include "services/audio_service.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#endif
```
改为：
```c
#if PERIPH_AUDIO
#include "services/audio_service.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#if PERIPH_BLE
#include "services/score_rx.h"
#include "services/rtttl.h"
#include "services/square_synth.h"
#endif
#endif
```

- [ ] **Step 2: 乐谱接收状态(放 on_cfg_message 之前)**。在 `save_profile` 之后、`on_cfg_message` 之前（即紧挨 Spec B 的 jpeg sink 块之后）插入：
```c
#if PERIPH_AUDIO
#define SCORE_MAX 4096
static uint8_t s_score_rx_buf[SCORE_MAX];
static uint8_t s_score_play_buf[SCORE_MAX];
static int s_score_play_len;
static score_rx_t s_score_rx;
static SemaphoreHandle_t s_music_sig;
static volatile int s_music_interrupt;
#endif
```

- [ ] **Step 3: on_cfg_message 加 type=0x03 分支**。在现有 `#if PERIPH_DISPLAY if (type == 0x02) {...} #endif` 之后、`if (type != CFG_MSG_JSON) return;` 之前插入：
```c
#if PERIPH_AUDIO
    if (type == 0x03) {   // RTTTL 乐谱分片
        int done = 0;
        int st = score_rx_frame(&s_score_rx, payload, len, &done);
        if (done) {
            rtttl_t probe;
            if (rtttl_init(&probe, (const char *)s_score_rx_buf, (int)s_score_rx.written) < 0) {
                st = 2;   // RTTTL 格式错
            } else if (s_music_sig) {
                memcpy(s_score_play_buf, s_score_rx_buf, s_score_rx.written);
                s_score_play_len = (int)s_score_rx.written;
                s_music_interrupt = 1;                 // 抢占当前曲
                xSemaphoreGive(s_music_sig);
            }
        }
        if (s_cfg) hal_config_notify_status(s_cfg, 0x03, (uint8_t)st);
        return;
    }
#endif
```

- [ ] **Step 4: 删除 passthrough 任务，加 music 任务**。把现有
```c
#if PERIPH_AUDIO
static audio_service_t s_au;
// 音频自检循环任务:反复 播放测试音 → 录音 → 回放,便于调试期间一边动接线/出声,
// 一边实时观察每轮的 mic_peak。每轮之间隔 1.5s。
static void audio_selftest_task(void *arg) {
    (void)arg;
    // 音频直通:麦克风采集实时输出到扬声器(对着麦说话,扬声器同步放出)。此调用不返回。
    platform_audio_passthrough(s_au.audio);
}
#endif
```
替换为：
```c
#if PERIPH_AUDIO
static audio_service_t s_au;
#if PERIPH_BLE
// 乐谱播放任务:等信号 → RTTTL 逐音符 → 分块方波合成 → 播放。新曲通过 s_music_interrupt 抢占。
static void music_player_task(void *arg) {
    (void)arg;
    int16_t block[1024];
    for (;;) {
        xSemaphoreTake(s_music_sig, portMAX_DELAY);
        s_music_interrupt = 0;
        audio_service_configure(&s_au, 8000, 16, 1);
        rtttl_t rt;
        if (rtttl_init(&rt, (const char *)s_score_play_buf, s_score_play_len) < 0) continue;
        int freq, ms;
        while (!s_music_interrupt && rtttl_next(&rt, &freq, &ms) == 1) {
            int total = 8000 * ms / 1000;       // 采样数
            int phase = 0;
            while (total > 0 && !s_music_interrupt) {
                int n = total < 1024 ? total : 1024;
                square_fill(block, n, freq, 8000, &phase, 6000);
                audio_service_play(&s_au, block, (size_t)n * sizeof(int16_t));
                total -= n;
            }
        }
    }
}
#endif
#endif
```

- [ ] **Step 5: app_run 内 PERIPH_AUDIO 段改造**。把现有
```c
#if PERIPH_AUDIO
    hal_audio_t *audio = platform_create_audio(&board);
    audio_service_init(&s_au, audio);
    audio_service_set_volume(&s_au, 100);   // 默认音量最大
    // 诊断自检放到后台任务里持续循环(不阻塞 app_run/其它外设)。
    xTaskCreate(audio_selftest_task, "audio_selftest", 8192, NULL, 5, NULL);
#endif
```
替换为：
```c
#if PERIPH_AUDIO
    hal_audio_t *audio = platform_create_audio(&board);
    audio_service_init(&s_au, audio);
    audio_service_set_volume(&s_au, 100);   // 默认音量最大
#if PERIPH_BLE
    score_rx_init(&s_score_rx, s_score_rx_buf, SCORE_MAX);
    s_music_sig = xSemaphoreCreateBinary();
    xTaskCreate(music_player_task, "music", 8192, NULL, 5, NULL);
#endif
#endif
```

- [ ] **Step 6: 验证**：`idf.py -C /home/cjiio/trae_card build`。Expected: 整工程编译链接通过，无未定义符号，无 `platform_audio_passthrough`/`audio_selftest_task` 残留引用。

- [ ] **Step 7: Commit**：跳过。

---

## Task 5: 上位机「乐谱」页（`tools/ble_card_client.py`）

**Files:** Modify `components/../tools/ble_card_client.py`（路径 `/home/cjiio/trae_card/tools/ble_card_client.py`）

- [ ] **Step 1: 加常量**。在文件顶部 `TYPE_JPG = 0x02` 附近加：
```python
TYPE_SCORE  = 0x03
SCORE_BEGIN = 0x00
SCORE_DATA  = 0x01
SCORE_END   = 0x02
STATUS_SCORE = {0: "OK", 1: "帧/时序错", 2: "RTTTL解析失败", 3: "过长"}
RTTTL_SAMPLES = {
    "Super Mario": "Mario:d=4,o=5,b=100:16e6,16e6,32p,16e6,32p,16c6,16e6,16g6,8p,16g,8p",
    "Tetris": "Tetris:d=4,o=5,b=160:e6,8b,8c6,8d6,16e6,16d6,8c6,8b,a,8a,8c6,e6,8d6,8c6,b,8b,8c6,d6,e6,c6,a,2a",
    "Nokia": "Nokia:d=4,o=5,b=125:8e6,8d6,f#,g#,8c#6,8b,d,e,8b,8a,c#,e,2a",
}
```
并把 `status_text` 里加分支:在 `STATUS_JPG if msg_type == TYPE_JPG` 后面加 `else STATUS_SCORE if msg_type == TYPE_SCORE`：
```python
def status_text(msg_type, status):
    table = (STATUS_JSON if msg_type == TYPE_JSON
             else STATUS_JPG if msg_type == TYPE_JPG
             else STATUS_SCORE if msg_type == TYPE_SCORE
             else {})
    return table.get(status, f"未知({status})")
```

- [ ] **Step 2: worker 加发送方法**。在 `BleWorker` 类里(`_send_image` 之后)加：
```python
    async def _send_score(self, text):
        data = text.encode("utf-8")
        total = len(data)
        self.on_event("log", f"▶ 发送乐谱 {total}B ...")
        await self._write_frame(TYPE_SCORE, bytes([SCORE_BEGIN]) + total.to_bytes(2, "little"))
        off = 0
        while off < total:
            c = data[off:off + JPG_CHUNK]
            await self._write_frame(TYPE_SCORE, bytes([SCORE_DATA]) + c)
            off += len(c)
        await self._write_frame(TYPE_SCORE, bytes([SCORE_END]))
        self.on_event("log", "▶ 乐谱发送完成,等待 notify...")

    def send_score(self, text):
        if not self.client:
            self.on_event("log", "✗ 未连接")
            return
        fut = self.submit(self._send_score(text))
        fut.add_done_callback(self._err_cb)
```

- [ ] **Step 3: GUI 加乐谱页**。在 `_build_ui` 里图片页(`nb.add(p2, text="图片(JPG)")` 那段)之后、日志区之前，加：
```python
        # --- 乐谱页 ---
        p3 = ttk.Frame(nb)
        nb.add(p3, text="乐谱(RTTTL)")
        r3 = ttk.Frame(p3); r3.pack(fill="x", padx=6, pady=6)
        ttk.Label(r3, text="示例:").pack(side="left")
        self.sample_combo = ttk.Combobox(r3, width=18, state="readonly",
                                          values=list(RTTTL_SAMPLES.keys()))
        self.sample_combo.pack(side="left", padx=4)
        self.sample_combo.bind("<<ComboboxSelected>>", self._on_pick_sample)
        ttk.Label(p3, text="RTTTL 文本:").pack(anchor="w", padx=6)
        self.score_text = tk.Text(p3, height=6)
        self.score_text.pack(fill="x", padx=6)
        self.score_text.insert("1.0", RTTTL_SAMPLES["Super Mario"])
        ttk.Button(p3, text="发送乐谱", command=self._on_send_score).pack(anchor="w", padx=6, pady=6)
```

- [ ] **Step 4: GUI 加回调**。在 `App` 类里(`_on_send_image` 附近)加：
```python
    def _on_pick_sample(self, _e):
        name = self.sample_combo.get()
        if name in RTTTL_SAMPLES:
            self.score_text.delete("1.0", "end")
            self.score_text.insert("1.0", RTTTL_SAMPLES[name])

    def _on_send_score(self):
        text = self.score_text.get("1.0", "end").strip()
        if text:
            self.worker.send_score(text)
```

- [ ] **Step 5: 验证**：`python3 -m py_compile tools/ble_card_client.py` → 语法通过；并用独立脚本核对乐谱帧字节：
```python
V=1
def f(t,p): return bytes([V,t,len(p)&0xFF,(len(p)>>8)&0xFF])+p
print(f(3,bytes([0])+(50).to_bytes(2,'little')).hex(' '))   # BEGIN total=50 → 01 03 03 00 00 32 00
print(f(3,bytes([1])+b'abc').hex(' '))                       # DATA → 01 03 04 00 01 61 62 63
print(f(3,bytes([2])).hex(' '))                              # END  → 01 03 01 00 02
```
Expected: 与固件 score_rx 解析一致（type=0x03,BEGIN total 小端）。

- [ ] **Step 6: Commit**：跳过。

---

## Task 6: 整机 build + 硬件联调

- [ ] **Step 1: 全量构建**：`idf.py -C /home/cjiio/trae_card build`。Expected: 成功。
- [ ] **Step 2: DRAM 检查**：`idf.py -C /home/cjiio/trae_card size`。Expected: DRAM 静态较 Spec B 后增 ~8KB(两个 4KB 缓冲)，剩余堆仍充裕，BLE 可初始化。
- [ ] **Step 3: 烧录**：`idf.py -C /home/cjiio/trae_card -p <PORT> flash monitor`（无新分区，不必 erase）。Expected: 正常进主界面，**麦克风不再自动回放**(passthrough 已移除)。
- [ ] **Step 4: 播放联调**：上位机「乐谱」页选 Super Mario → 发送。Expected: 扬声器播放马里奥主题一遍；notify(0x11) 收 `03 00`。
- [ ] **Step 5: 长曲**：发一首 >256B 的多帧 RTTTL(如 Tetris)。Expected: 完整播放，无截断；notify `03 00`。
- [ ] **Step 6: 抢占**：一曲播放中再发另一曲。Expected: 立即切到新曲。
- [ ] **Step 7: 错误/回归**：发坏 RTTTL(如 `abc`) → notify `03 02`；确认 JSON 配置(0x01)、JPG(0x02)、爱心广播仍正常。

---

## Self-Review（对照 spec）

- **RTTTL 格式 + 解析**（spec §6）→ Task 2 rtttl（header/音符/#/附点/休止/八度移位）✅
- **type=0x03 分片累积 RAM(支持长曲)**（spec §4,§5）→ Task 1 score_rx + Task 4 缓冲 ✅
- **整数方波合成**（spec §7）→ Task 3 square_synth ✅
- **music 任务 + host 不阻塞 + 抢占 + 双缓冲**（spec §8）→ Task 4 music_player_task + on_cfg_message 0x03 分支 + s_score_rx_buf/s_score_play_buf ✅
- **移除 passthrough**（spec §9）→ Task 4 Step 4/5 ✅
- **notify 状态码 0/1/2/3**（spec §4）→ score_rx 返回 0/1/3 + rtttl 校验产生 2 + Task 4 回报 ✅
- **上位机乐谱页**（spec §10）→ Task 5 ✅
- **采样率 8000/16/mono**（spec §7）→ Task 4 music task configure(8000,16,1) ✅
- **类型一致性**：`score_rx_t`/`score_rx_frame`（T1）与 T4 用法一致；`rtttl_t`/`rtttl_init`/`rtttl_next`（T2）与 T4 一致；`square_fill`（T3）与 T4 一致；帧 type=0x03 与上位机(T5)一致 ✅
- **占位符**：无 TBD，代码完整 ✅

已知取舍：music task 与 host task 通过 s_music_interrupt(volatile) + 双缓冲协调，抢占在音符/块边界生效（最坏延迟一个块 128ms，可接受）；音符间无显式间隙(legato)，如需断音后续再加。
