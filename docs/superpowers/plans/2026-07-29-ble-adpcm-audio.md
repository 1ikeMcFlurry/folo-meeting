# BLE 流式 ADPCM 音频 + 可 BLE 写入本地片段 —— 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有工牌固件上加一套单声道 16kHz IMA-ADPCM 音频能力：BLE 实时流播放、经 BLE 写入/替换的本地事件音（无默认、存在才播），且不破坏现有 RTTTL 乐谱播放。

**Architecture:** 新增纯逻辑 ADPCM 解码器（C）与配套 Python 编码器（互操作用 gcc 驱动逐字节校验）；音频片段存进新的 512KB 可写 SPIFFS 分区；把现有 RTTTL 播放任务泛化为多路复用 RTTTL/STREAM/CLIP 三音源、互斥抢占的统一播放任务；两条新 BLE 子协议 0x04（流）/0x05（片段替换）。

**Tech Stack:** ESP-IDF 5.5.x（esp32c3）、NimBLE、ES8311+I2S、SPIFFS、Unity/host_test（linux target）、gcc 直编纯逻辑测试、Python 3（纯标准库编码器）。

**测试约定：** 纯逻辑用 **gcc 直编**（沿用 `tools/token_interop_check.sh` 模式），不走 host_test；每个纯逻辑单元配一个独立 `tests/` 下的 C 驱动 + Python 互操作脚本。集成层（SPIFFS/播放任务/BLE 分派/上位机）以 `idf.py build` 通过 + 明确的真机自检为验收。

---

## 文件结构（先锁边界）

**新增**
- `components/core/services/include/services/adpcm.h` / `src/adpcm.c` — IMA-ADPCM 单声道连续流**解码器**（纯逻辑）
- `components/core/services/include/services/audio_clip.h` — clip_id 枚举 + 片段容器头结构（共享，无 .c）
- `components/core/services/include/services/audio_rx.h` / `src/audio_rx.c` — 0x05 片段接收状态机（带 sink，镜像 jpeg_rx）+ 0x04 流 BEGIN 解析辅助（纯逻辑）
- `components/platform/platform_esp32/src/audio_store.c` — SPIFFS 挂载/片段读/原子写替换/存在性
- `components/platform/platform_esp32/src/audio_stream.c` — 流式环形缓冲（RAM）
- `tools/adpcm_codec.py` — 纯 Python IMA-ADPCM 编/解码（与 C 对齐）
- `tools/wav_to_adpcm.py` — wav → 片段容器文件（离线工具）
- `tests/adpcm/` — gcc 驱动 + 互操作脚本
- `tests/audio_rx/` — gcc 驱动

**修改**
- `partitions.csv` — 新增 audio 512KB spiffs 分区
- `components/core/ports/include/hal/hal_config.h` — 新增 `CFG_MSG_AUDIO 0x04` / `CFG_MSG_AUDIO_CLIP 0x05`
- `components/core/services/CMakeLists.txt` — 加 adpcm.c / audio_rx.c
- `components/platform/platform_esp32/CMakeLists.txt` — 加 audio_store.c / audio_stream.c + `spiffs` 依赖
- `components/platform/platform_esp32/include/platform/platform_factory.h` — 音频存储/流/播放片段 API 声明
- `components/platform/platform_esp32/include/platform/board_config.h` — 采样率/环形缓冲/挂载点常量
- `components/app/src/app.c` — 播放任务泛化、事件接入、on_cfg_message 分派 0x04/0x05
- `tools/ble_card_client.py` — 发送音频流 + 写入片段
- `doc/ble-protocol-spec.md` — 新增音频协议章节，版本 +1

---

## Task 1: IMA-ADPCM 解码器（C，纯逻辑）

**Files:**
- Create: `components/core/services/include/services/adpcm.h`
- Create: `components/core/services/src/adpcm.c`
- Test: `tests/adpcm/test_adpcm.c`

- [ ] **Step 1: 写头文件**

Create `components/core/services/include/services/adpcm.h`:
```c
// components/core/services/include/services/adpcm.h —— IMA-ADPCM 单声道连续流解码(纯逻辑)
#pragma once
#include <stdint.h>

typedef struct { int16_t predictor; int16_t index; } adpcm_state_t;

void adpcm_state_reset(adpcm_state_t *st);           // predictor=0, index=0

// 解码 nbytes 字节(每字节 2 个 4bit 样本,低 nibble 在前)。
// out 需能容纳 2*nbytes 个 int16。返回写入的样本数(=2*nbytes)。
int  adpcm_decode(adpcm_state_t *st, const uint8_t *in, int nbytes, int16_t *out);
```

- [ ] **Step 2: 写失败测试**

Create `tests/adpcm/test_adpcm.c`:
```c
// gcc 直编:验证解码器对已知向量的逐样本输出
#include "services/adpcm.h"
#include <assert.h>
#include <stdio.h>

int main(void) {
    // 已知向量:predictor=0,index=0,输入 nibble 序列 {0x08,0x00}(一个字节=两个样本)
    // 低 nibble 先:code=8(负,diff=step>>3=7>>3=0 → predictor=0),然后 code=0(diff=0 → predictor=0)
    adpcm_state_t st; adpcm_state_reset(&st);
    uint8_t in[1] = {0x08};          // 样本0=code0=0x8, 样本1=code1=0x0
    int16_t out[2] = {123,123};
    int n = adpcm_decode(&st, in, 1, out);
    assert(n == 2);
    assert(out[0] == 0);             // code8: diff=0,pred 0-0=0
    assert(out[1] == 0);             // code0: pred 0+0=0
    // 递增:输入若干最大正 code(0x07)应让 predictor 单调上升并最终被限幅
    adpcm_state_reset(&st);
    uint8_t up[8]; for (int i=0;i<8;i++) up[i]=0x77;   // 16 个 code=7(最大正步进)
    int16_t o2[16];
    adpcm_decode(&st, up, 8, o2);
    assert(o2[0] > 0);
    assert(o2[15] >= o2[0]);         // 单调不降
    printf("test_adpcm PASS\n");
    return 0;
}
```

- [ ] **Step 3: 跑测试确认失败(未实现)**

Run:
```bash
gcc -std=c11 -Icomponents/core/services/include \
    components/core/services/src/adpcm.c tests/adpcm/test_adpcm.c -o /tmp/t_adpcm 2>&1 | head
```
Expected: 链接失败 `undefined reference to adpcm_decode`（因为 adpcm.c 还是空/未写）。

- [ ] **Step 4: 写实现**

Create `components/core/services/src/adpcm.c`:
```c
// components/core/services/src/adpcm.c —— IMA-ADPCM 单声道连续流解码(纯逻辑,无 FPU)
#include "services/adpcm.h"

static const int16_t STEP[89] = {
    7,8,9,10,11,12,13,14,16,17,19,21,23,25,28,31,34,37,41,45,50,55,60,66,73,80,88,97,
    107,118,130,143,157,173,190,209,230,253,279,307,337,371,408,449,494,544,598,658,
    724,796,876,963,1060,1166,1282,1411,1552,1707,1878,2066,2272,2499,2749,3024,3327,
    3660,4026,4428,4871,5358,5894,6484,7132,7845,8630,9493,10442,11487,12635,13899,
    15289,16818,18500,20350,22385,24623,27086,29794,32767
};
static const int8_t INDEX[16] = { -1,-1,-1,-1,2,4,6,8, -1,-1,-1,-1,2,4,6,8 };

static int16_t decode_code(adpcm_state_t *st, int code) {
    int step = STEP[st->index];
    int diff = step >> 3;
    if (code & 4) diff += step;
    if (code & 2) diff += step >> 1;
    if (code & 1) diff += step >> 2;
    int pred = st->predictor;
    if (code & 8) pred -= diff; else pred += diff;
    if (pred > 32767) pred = 32767; else if (pred < -32768) pred = -32768;
    st->predictor = (int16_t)pred;
    int idx = st->index + INDEX[code & 0x0F];
    if (idx < 0) idx = 0; else if (idx > 88) idx = 88;
    st->index = (int16_t)idx;
    return st->predictor;
}

void adpcm_state_reset(adpcm_state_t *st) { st->predictor = 0; st->index = 0; }

int adpcm_decode(adpcm_state_t *st, const uint8_t *in, int nbytes, int16_t *out) {
    int k = 0;
    for (int i = 0; i < nbytes; i++) {
        out[k++] = decode_code(st, in[i] & 0x0F);        // 低 nibble 先
        out[k++] = decode_code(st, (in[i] >> 4) & 0x0F); // 再高 nibble
    }
    return k;
}
```

- [ ] **Step 5: 跑测试确认通过**

Run:
```bash
gcc -std=c11 -Icomponents/core/services/include \
    components/core/services/src/adpcm.c tests/adpcm/test_adpcm.c -o /tmp/t_adpcm && /tmp/t_adpcm
```
Expected: `test_adpcm PASS`

- [ ] **Step 6: 注册进构建 + 提交**

修改 `components/core/services/CMakeLists.txt`，在 `SRCS` 列表加入 `"src/adpcm.c"`。

```bash
git add components/core/services/include/services/adpcm.h components/core/services/src/adpcm.c \
        components/core/services/CMakeLists.txt tests/adpcm/test_adpcm.c
git commit -m "feat(audio): IMA-ADPCM 单声道解码器(纯逻辑)"
```

---

## Task 2: Python ADPCM 编解码器 + C↔Python 互操作校验

**Files:**
- Create: `tools/adpcm_codec.py`
- Create: `tests/adpcm/interop_check.sh`

- [ ] **Step 1: 写 Python 编解码器**

Create `tools/adpcm_codec.py`:
```python
# tools/adpcm_codec.py —— 纯 Python IMA-ADPCM 单声道连续流编/解码(与 C 侧 adpcm.c 逐位对齐)
# 不依赖 audioop(Py3.13 已移除)。低 nibble = 先出现的样本。
STEP = [
    7,8,9,10,11,12,13,14,16,17,19,21,23,25,28,31,34,37,41,45,50,55,60,66,73,80,88,97,
    107,118,130,143,157,173,190,209,230,253,279,307,337,371,408,449,494,544,598,658,
    724,796,876,963,1060,1166,1282,1411,1552,1707,1878,2066,2272,2499,2749,3024,3327,
    3660,4026,4428,4871,5358,5894,6484,7132,7845,8630,9493,10442,11487,12635,13899,
    15289,16818,18500,20350,22385,24623,27086,29794,32767]
INDEX = [-1,-1,-1,-1,2,4,6,8,-1,-1,-1,-1,2,4,6,8]

def _decode_code(st, code):
    step = STEP[st['index']]
    diff = step >> 3
    if code & 4: diff += step
    if code & 2: diff += step >> 1
    if code & 1: diff += step >> 2
    pred = st['predictor'] - diff if (code & 8) else st['predictor'] + diff
    pred = max(-32768, min(32767, pred))
    st['predictor'] = pred
    idx = st['index'] + INDEX[code & 0x0F]
    st['index'] = max(0, min(88, idx))
    return pred

def _encode_sample(st, sample):
    step = STEP[st['index']]
    diff = sample - st['predictor']
    code = 8 if diff < 0 else 0
    if diff < 0: diff = -diff
    if diff >= step: code |= 4; diff -= step
    if diff >= step >> 1: code |= 2; diff -= step >> 1
    if diff >= step >> 2: code |= 1
    _decode_code(st, code)          # 与解码同步更新 predictor/index
    return code & 0x0F

def encode(samples):
    """samples: int16 序列 → bytes(低 nibble 先)。奇数样本末尾补 0 nibble。"""
    st = {'predictor': 0, 'index': 0}
    out = bytearray()
    codes = [_encode_sample(st, int(s)) for s in samples]
    if len(codes) % 2: codes.append(0)
    for i in range(0, len(codes), 2):
        out.append((codes[i] & 0x0F) | ((codes[i+1] & 0x0F) << 4))
    return bytes(out)

def decode(data):
    st = {'predictor': 0, 'index': 0}
    out = []
    for b in data:
        out.append(_decode_code(st, b & 0x0F))
        out.append(_decode_code(st, (b >> 4) & 0x0F))
    return out

if __name__ == "__main__":
    # 自测:编码→解码近似原信号
    import math
    pcm = [int(16000*math.sin(2*math.pi*440*i/16000)) for i in range(2000)]
    dec = decode(encode(pcm))
    err = sum(abs(a-b) for a,b in zip(pcm,dec))/len(pcm)
    print("mean abs err =", round(err,1))
```

- [ ] **Step 2: 跑 Python 自测**

Run: `python3 tools/adpcm_codec.py`
Expected: 打印 `mean abs err = <一个较小的数,通常 < 400>`（IMA 对 440Hz 正弦的量化误差量级）。

- [ ] **Step 3: 写互操作脚本(Python 编码 → C 解码 → 比对)**

Create `tests/adpcm/interop_check.sh`:
```bash
#!/usr/bin/env bash
# Python 编码器产出的 ADPCM,喂给固件真实 C 解码器(adpcm.c),验证两侧逐样本一致。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# C 驱动:从 stdin 读 ADPCM 字节,解码后把 int16 样本按十进制逐行打印
cat > "$TMP/drv.c" <<'EOF'
#include "services/adpcm.h"
#include <stdio.h>
int main(void){
    unsigned char buf[65536]; int n=0,c;
    while((c=getchar())!=EOF && n<(int)sizeof buf) buf[n++]=(unsigned char)c;
    static int16_t out[131072];
    adpcm_state_t st; adpcm_state_reset(&st);
    int m=adpcm_decode(&st,buf,n,out);
    for(int i=0;i<m;i++) printf("%d\n",out[i]);
    return 0;
}
EOF
gcc -std=c11 -I"$ROOT/components/core/services/include" \
    "$ROOT/components/core/services/src/adpcm.c" "$TMP/drv.c" -o "$TMP/drv"

# Python 生成 PCM → 编码为 ADPCM(bytes 写文件) → 同时算出 Python 解码参考
python3 - "$TMP" <<'PY'
import sys, math
sys.path.insert(0, "tools")
import adpcm_codec as a
pcm=[int(16000*math.sin(2*math.pi*440*i/16000)) for i in range(4000)]
adp=a.encode(pcm)
open(sys.argv[1]+"/adp.bin","wb").write(adp)
open(sys.argv[1]+"/pyref.txt","w").write("\n".join(str(x) for x in a.decode(adp))+"\n")
PY

"$TMP/drv" < "$TMP/adp.bin" > "$TMP/cout.txt"
if diff -q "$TMP/pyref.txt" "$TMP/cout.txt" >/dev/null; then
    echo "✓ ADPCM 互操作一致:Python 编码 → C 解码 == Python 解码"
else
    echo "✗ C 解码与 Python 解码不一致(检查 STEP/INDEX 表或 nibble 顺序)"; exit 1
fi
```

- [ ] **Step 4: 跑互操作校验**

Run: `bash tests/adpcm/interop_check.sh`
Expected: `✓ ADPCM 互操作一致:Python 编码 → C 解码 == Python 解码`

- [ ] **Step 5: 提交**

```bash
chmod +x tests/adpcm/interop_check.sh
git add tools/adpcm_codec.py tests/adpcm/interop_check.sh
git commit -m "test(audio): ADPCM Python 编码器 + C↔Python 互操作校验"
```

---

## Task 3: clip 容器定义 + 片段接收状态机(0x05,带 sink)

**Files:**
- Create: `components/core/services/include/services/audio_clip.h`
- Create: `components/core/services/include/services/audio_rx.h`
- Create: `components/core/services/src/audio_rx.c`
- Test: `tests/audio_rx/test_audio_rx.c`

- [ ] **Step 1: 写共享定义 audio_clip.h**

Create `components/core/services/include/services/audio_clip.h`:
```c
// components/core/services/include/services/audio_clip.h —— 片段枚举 + 容器头(共享)
#pragma once
#include <stdint.h>

typedef enum { CLIP_BOOT=0, CLIP_CONNECT=1, CLIP_GAMEOVER=2, CLIP_COUNT } clip_id_t;

#define AUDIO_CLIP_MAGIC 0x31504441u   // "ADP1" 小端('A'=0x41,'D'=0x44,'P'=0x50,'1'=0x31)
// 容器头(12 字节,小端);其后紧跟 ADPCM nibble 流
typedef struct {
    uint32_t magic;        // AUDIO_CLIP_MAGIC
    uint16_t sample_rate;  // 16000
    uint8_t  channels;     // 1
    uint8_t  reserved;
    uint32_t num_samples;  // 精确样本数
} audio_clip_hdr_t;
```

- [ ] **Step 2: 写 audio_rx.h**

Create `components/core/services/include/services/audio_rx.h`:
```c
// components/core/services/include/services/audio_rx.h —— 0x05 片段接收(带 sink,镜像 jpeg_rx);
// 以及 0x04 流 BEGIN 的解析辅助(纯逻辑)
#pragma once
#include <stdint.h>

#define AUDIO_OP_BEGIN 0x00
#define AUDIO_OP_DATA  0x01
#define AUDIO_OP_END   0x02

// 片段落盘回调(platform 实现:写临时文件,end 时原子 rename)。
// begin 返回 0=成功 -1=clip_id非法/超长 -2=打开失败;write/end 返回 0/-2。
typedef struct {
    int  (*begin)(void *user, int clip_id, uint32_t total_len);
    int  (*write)(void *user, const uint8_t *d, int n);
    int  (*end)(void *user);
    void *user;
} audio_clip_sink_t;

typedef struct { int receiving; int clip_id; uint32_t total, written; } audio_clip_rx_t;

void audio_clip_rx_init(audio_clip_rx_t *r);
// 处理一个 type=0x05 帧。返回 notify 状态码:0=OK/进行中 1=时序错 2=落盘失败 3=非法/超长。
// END 收齐且成功 → *done=1。BEGIN 载荷: [0x00][clip_id u8][total_len u16 小端]。
int  audio_clip_rx_frame(audio_clip_rx_t *r, const uint8_t *payload, int len,
                         const audio_clip_sink_t *sink, int *done);

// 0x04 流 BEGIN 解析:从 [0x00][sr u16 小端] 取采样率。成功返回 0 并写 *sr,失败返回 -1。
int  audio_stream_parse_begin(const uint8_t *payload, int len, uint16_t *sr);
```

- [ ] **Step 3: 写失败测试**

Create `tests/audio_rx/test_audio_rx.c`:
```c
#include "services/audio_rx.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static uint8_t g_buf[256]; static int g_n; static int g_began_id; static int g_ended;
static int s_begin(void*u,int id,uint32_t t){(void)u;(void)t; if(id<0||id>=3)return -1; g_began_id=id; g_n=0; return 0;}
static int s_write(void*u,const uint8_t*d,int n){(void)u; memcpy(g_buf+g_n,d,n); g_n+=n; return 0;}
static int s_end(void*u){(void)u; g_ended=1; return 0;}

int main(void){
    audio_clip_sink_t sink={s_begin,s_write,s_end,0};
    audio_clip_rx_t r; audio_clip_rx_init(&r); int done;
    // BEGIN clip_id=2, total=3
    uint8_t begin[4]={0x00, 2, 3, 0};
    assert(audio_clip_rx_frame(&r,begin,4,&sink,&done)==0); assert(g_began_id==2);
    uint8_t data[4]={0x01,'x','y','z'};
    assert(audio_clip_rx_frame(&r,data,4,&sink,&done)==0); assert(done==0);
    uint8_t end[1]={0x02};
    assert(audio_clip_rx_frame(&r,end,1,&sink,&done)==0); assert(done==1);
    assert(g_ended==1); assert(g_n==3); assert(memcmp(g_buf,"xyz",3)==0);

    // 非法 clip_id → 3
    audio_clip_rx_init(&r);
    uint8_t bad[4]={0x00, 9, 1, 0};
    assert(audio_clip_rx_frame(&r,bad,4,&sink,&done)==3);

    // DATA 先于 BEGIN → 1
    audio_clip_rx_init(&r);
    uint8_t d2[2]={0x01,'a'};
    assert(audio_clip_rx_frame(&r,d2,2,&sink,&done)==1);

    // 流 BEGIN 解析
    uint16_t sr=0; uint8_t sb[3]={0x00,0x80,0x3E}; // 0x3E80=16000
    assert(audio_stream_parse_begin(sb,3,&sr)==0); assert(sr==16000);
    printf("test_audio_rx PASS\n");
    return 0;
}
```

- [ ] **Step 4: 跑测试确认失败**

Run:
```bash
gcc -std=c11 -Icomponents/core/services/include \
    components/core/services/src/audio_rx.c tests/audio_rx/test_audio_rx.c -o /tmp/t_arx 2>&1 | head
```
Expected: 编译/链接失败（`audio_rx.c` 不存在或符号未定义）。

- [ ] **Step 5: 写实现**

Create `components/core/services/src/audio_rx.c`:
```c
// components/core/services/src/audio_rx.c —— 0x05 片段接收 + 0x04 流 BEGIN 解析(纯逻辑)
#include "services/audio_rx.h"
#include "services/audio_clip.h"

void audio_clip_rx_init(audio_clip_rx_t *r){ r->receiving=0; r->clip_id=-1; r->total=0; r->written=0; }

int audio_clip_rx_frame(audio_clip_rx_t *r, const uint8_t *payload, int len,
                        const audio_clip_sink_t *sink, int *done){
    if(done) *done=0;
    if(len<1) return 1;
    uint8_t op=payload[0]; const uint8_t *b=payload+1; int bl=len-1;
    if(op==AUDIO_OP_BEGIN){
        if(bl<3) return 1;
        int id=b[0];
        uint32_t total=(uint32_t)b[1]|((uint32_t)b[2]<<8);
        int rc=sink->begin(sink->user, id, total);
        if(rc!=0){ r->receiving=0; return 3; }   // clip_id 非法/超长/打开失败一律 3
        r->receiving=1; r->clip_id=id; r->total=total; r->written=0;
        return 0;
    }
    if(op==AUDIO_OP_DATA){
        if(!r->receiving) return 1;
        if(bl>0){
            if(r->written+(uint32_t)bl > r->total){ r->receiving=0; return 3; }
            if(sink->write(sink->user,b,bl)!=0){ r->receiving=0; return 2; }
            r->written+=(uint32_t)bl;
        }
        return 0;
    }
    if(op==AUDIO_OP_END){
        if(!r->receiving) return 1;
        r->receiving=0;
        if(r->written!=r->total) return 1;
        if(sink->end(sink->user)!=0) return 2;
        if(done) *done=1;
        return 0;
    }
    return 1;
}

int audio_stream_parse_begin(const uint8_t *payload, int len, uint16_t *sr){
    if(len<3 || payload[0]!=AUDIO_OP_BEGIN) return -1;
    *sr=(uint16_t)payload[1] | ((uint16_t)payload[2]<<8);
    return 0;
}
```

- [ ] **Step 6: 跑测试确认通过**

Run:
```bash
gcc -std=c11 -Icomponents/core/services/include \
    components/core/services/src/audio_rx.c tests/audio_rx/test_audio_rx.c -o /tmp/t_arx && /tmp/t_arx
```
Expected: `test_audio_rx PASS`

- [ ] **Step 7: 注册构建 + 提交**

`components/core/services/CMakeLists.txt` 的 `SRCS` 加 `"src/audio_rx.c"`。

```bash
git add components/core/services/include/services/audio_clip.h \
        components/core/services/include/services/audio_rx.h \
        components/core/services/src/audio_rx.c components/core/services/CMakeLists.txt \
        tests/audio_rx/test_audio_rx.c components/core/services/CMakeLists.txt
git commit -m "feat(audio): 片段容器定义 + 0x05 接收状态机 + 0x04 流BEGIN解析(纯逻辑)"
```

---

## Task 4: audio 分区 + 常量 + 消息类型

**Files:**
- Modify: `partitions.csv`
- Modify: `components/core/ports/include/hal/hal_config.h:41`
- Modify: `components/platform/platform_esp32/include/platform/board_config.h`

- [ ] **Step 1: 加分区**

在 `partitions.csv` 末尾追加一行：
```
audio,    data, spiffs,  ,        0x80000,
```

- [ ] **Step 2: 加消息类型常量**

`components/core/ports/include/hal/hal_config.h` 在 `#define CFG_MSG_SCORE 0x03` 之后加：
```c
#define CFG_MSG_AUDIO      0x04   // 音频流(ADPCM 实时播放,不存盘)
#define CFG_MSG_AUDIO_CLIP 0x05   // 音频片段写入/替换(存 SPIFFS)
```

- [ ] **Step 3: 加板级常量**

`components/platform/platform_esp32/include/platform/board_config.h` 在文件常量区加：
```c
// ===== 音频(ADPCM 流 + 本地片段)=====
#define AUDIO_ADPCM_SR       16000   // 采样率(流与片段统一)
#define AUDIO_STREAM_RING    8192    // 流式环形缓冲字节数(存 ADPCM)
#define AUDIO_SPIFFS_MOUNT   "/audio"
#define AUDIO_SPIFFS_LABEL   "audio"
```

- [ ] **Step 4: 编译确认分区表合法**

Run: `idf.py build 2>&1 | tail -5`
Expected: `Project build complete`（分区表在 8MB 内、无重叠）。

- [ ] **Step 5: 提交**

```bash
git add partitions.csv components/core/ports/include/hal/hal_config.h \
        components/platform/platform_esp32/include/platform/board_config.h
git commit -m "feat(audio): 新增 512KB audio 分区 + 消息类型 0x04/0x05 + 板级常量"
```

---

## Task 5: SPIFFS 片段存储(audio_store.c)

**Files:**
- Create: `components/platform/platform_esp32/src/audio_store.c`
- Modify: `components/platform/platform_esp32/include/platform/platform_factory.h`
- Modify: `components/platform/platform_esp32/CMakeLists.txt`

- [ ] **Step 1: 声明平台 API**

`platform_factory.h` 末尾加：
```c
// 音频片段存储(SPIFFS 分区 "audio")。init 挂载(不存在则格式化)。
#include "services/audio_rx.h"
int  audio_store_init(void);
bool audio_store_clip_exists(int clip_id);
// 读整个片段容器到调用者缓冲;返回字节数(含 12B 头),失败/不存在返回 -1。
int  audio_store_clip_read(int clip_id, uint8_t *out, int cap);
// 提供给 audio_clip_rx 的落盘 sink(写临时文件 → end 原子 rename)。
const audio_clip_sink_t *audio_store_sink(void);
```

- [ ] **Step 2: 写实现**

Create `components/platform/platform_esp32/src/audio_store.c`:
```c
// components/platform/platform_esp32/src/audio_store.c —— 片段存 SPIFFS(可写,原子替换)
#include "platform/platform_factory.h"
#include "platform/board_config.h"
#include "services/audio_clip.h"
#include "esp_spiffs.h"
#include "esp_log.h"
#include <stdio.h>
#include <string.h>

static const char *TAG = "audio_store";
static int s_mounted = 0;

int audio_store_init(void){
    if(s_mounted) return 0;
    esp_vfs_spiffs_conf_t c = {
        .base_path = AUDIO_SPIFFS_MOUNT, .partition_label = AUDIO_SPIFFS_LABEL,
        .max_files = 2, .format_if_mount_failed = true,
    };
    esp_err_t e = esp_vfs_spiffs_register(&c);
    if(e != ESP_OK){ ESP_LOGW(TAG,"SPIFFS 挂载失败: %s", esp_err_to_name(e)); return -1; }
    s_mounted = 1;
    ESP_LOGI(TAG,"audio 分区已挂载 %s", AUDIO_SPIFFS_MOUNT);
    return 0;
}

static void clip_path(int id, char *out, int cap){ snprintf(out,cap,"%s/clip%d.adpcm",AUDIO_SPIFFS_MOUNT,id); }
static void tmp_path (int id, char *out, int cap){ snprintf(out,cap,"%s/clip%d.tmp",  AUDIO_SPIFFS_MOUNT,id); }

bool audio_store_clip_exists(int clip_id){
    if(!s_mounted || clip_id<0 || clip_id>=CLIP_COUNT) return false;
    char p[48]; clip_path(clip_id,p,sizeof p);
    FILE *f=fopen(p,"rb"); if(!f) return false;
    audio_clip_hdr_t h; size_t n=fread(&h,1,sizeof h,f); fclose(f);
    return (n==sizeof h && h.magic==AUDIO_CLIP_MAGIC);
}

int audio_store_clip_read(int clip_id, uint8_t *out, int cap){
    if(!s_mounted || clip_id<0 || clip_id>=CLIP_COUNT) return -1;
    char p[48]; clip_path(clip_id,p,sizeof p);
    FILE *f=fopen(p,"rb"); if(!f) return -1;
    int n=(int)fread(out,1,(size_t)cap,f); fclose(f);
    if(n<(int)sizeof(audio_clip_hdr_t)) return -1;
    audio_clip_hdr_t *h=(audio_clip_hdr_t*)out;
    if(h->magic!=AUDIO_CLIP_MAGIC) return -1;
    return n;
}

// ---- sink:写临时文件,end 校验头 + 原子 rename ----
static int s_id=-1; static FILE *s_fp=NULL; static uint32_t s_first4=0; static int s_hdrgot=0;
static int sink_begin(void *u,int id,uint32_t total){
    (void)u;(void)total;
    if(!s_mounted || id<0 || id>=CLIP_COUNT) return -1;
    char p[48]; tmp_path(id,p,sizeof p);
    if(s_fp){ fclose(s_fp); s_fp=NULL; }
    s_fp=fopen(p,"wb"); if(!s_fp) return -2;
    s_id=id; s_hdrgot=0; s_first4=0; return 0;
}
static int sink_write(void *u,const uint8_t *d,int n){
    (void)u; if(!s_fp) return -2;
    if(!s_hdrgot && n>=4){ memcpy(&s_first4,d,4); s_hdrgot=1; }  // 记住 magic 供 end 校验
    return (fwrite(d,1,(size_t)n,s_fp)==(size_t)n)?0:-2;
}
static int sink_end(void *u){
    (void)u; if(!s_fp) return -2;
    fflush(s_fp); fclose(s_fp); s_fp=NULL;
    if(!s_hdrgot || s_first4!=AUDIO_CLIP_MAGIC){          // 头非法 → 删临时,不替换
        char t[48]; tmp_path(s_id,t,sizeof t); remove(t); return -2;
    }
    char t[48],p[48]; tmp_path(s_id,t,sizeof t); clip_path(s_id,p,sizeof p);
    remove(p);                                            // SPIFFS rename 不覆盖已存在,先删
    return (rename(t,p)==0)?0:-2;
}
static const audio_clip_sink_t SINK = { sink_begin, sink_write, sink_end, NULL };
const audio_clip_sink_t *audio_store_sink(void){ return &SINK; }
```

- [ ] **Step 3: 注册源文件 + 依赖**

`components/platform/platform_esp32/CMakeLists.txt`：`SRCS` 加 `"src/audio_store.c"`，`PRIV_REQUIRES` 加 `spiffs`。

- [ ] **Step 4: 编译**

Run: `idf.py build 2>&1 | tail -5`
Expected: `Project build complete`

- [ ] **Step 5: 提交**

```bash
git add components/platform/platform_esp32/src/audio_store.c \
        components/platform/platform_esp32/include/platform/platform_factory.h \
        components/platform/platform_esp32/CMakeLists.txt
git commit -m "feat(audio): SPIFFS 片段存储(挂载/读/原子写替换/存在性)"
```

---

## Task 6: 流式环形缓冲(audio_stream.c)

**Files:**
- Create: `components/platform/platform_esp32/src/audio_stream.c`
- Modify: `components/platform/platform_esp32/include/platform/platform_factory.h`
- Modify: `components/platform/platform_esp32/CMakeLists.txt`

- [ ] **Step 1: 声明 API**

`platform_factory.h` 末尾加：
```c
// 音频流环形缓冲(ADPCM 字节)。BLE host 任务写,播放任务读。线程安全(临界区)。
void audio_stream_reset(void);                       // 开流:清空 + 复位
int  audio_stream_push(const uint8_t *d, int n);     // 写(host 任务);满则丢弃返回实际写入
int  audio_stream_pop(uint8_t *out, int max);        // 读(播放任务);返回读到字节数(可能 0)
int  audio_stream_level(void);                       // 当前缓冲字节数
void audio_stream_set_eos(void);                     // 标记收流结束
int  audio_stream_eos(void);                         // 是否已 EOS 且缓冲排空
```

- [ ] **Step 2: 写实现**

Create `components/platform/platform_esp32/src/audio_stream.c`:
```c
// components/platform/platform_esp32/src/audio_stream.c —— 流式 ADPCM 环形缓冲
#include "platform/platform_factory.h"
#include "platform/board_config.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <string.h>

static uint8_t s_ring[AUDIO_STREAM_RING];
static volatile int s_head=0, s_tail=0;   // head=写入位置, tail=读取位置
static volatile int s_eos=0;
static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;

static int ring_count_nolock(void){ int d=s_head-s_tail; return d<0? d+AUDIO_STREAM_RING : d; }

void audio_stream_reset(void){
    portENTER_CRITICAL(&s_mux); s_head=s_tail=0; s_eos=0; portEXIT_CRITICAL(&s_mux);
}
int audio_stream_push(const uint8_t *d, int n){
    int w=0; portENTER_CRITICAL(&s_mux);
    int space=AUDIO_STREAM_RING-1-ring_count_nolock();
    if(n>space) n=space;
    for(w=0; w<n; w++){ s_ring[s_head]=d[w]; s_head=(s_head+1)%AUDIO_STREAM_RING; }
    portEXIT_CRITICAL(&s_mux); return w;
}
int audio_stream_pop(uint8_t *out, int max){
    int r=0; portENTER_CRITICAL(&s_mux);
    int avail=ring_count_nolock(); if(max>avail) max=avail;
    for(r=0; r<max; r++){ out[r]=s_ring[s_tail]; s_tail=(s_tail+1)%AUDIO_STREAM_RING; }
    portEXIT_CRITICAL(&s_mux); return r;
}
int  audio_stream_level(void){ int c; portENTER_CRITICAL(&s_mux); c=ring_count_nolock(); portEXIT_CRITICAL(&s_mux); return c; }
void audio_stream_set_eos(void){ portENTER_CRITICAL(&s_mux); s_eos=1; portEXIT_CRITICAL(&s_mux); }
int  audio_stream_eos(void){ int e,c; portENTER_CRITICAL(&s_mux); e=s_eos; c=ring_count_nolock(); portEXIT_CRITICAL(&s_mux); return (e && c==0); }
```

- [ ] **Step 3: 注册 + 编译**

`CMakeLists.txt` 的 `SRCS` 加 `"src/audio_stream.c"`。
Run: `idf.py build 2>&1 | tail -5` → Expected: `Project build complete`

- [ ] **Step 4: 提交**

```bash
git add components/platform/platform_esp32/src/audio_stream.c \
        components/platform/platform_esp32/include/platform/platform_factory.h \
        components/platform/platform_esp32/CMakeLists.txt
git commit -m "feat(audio): 流式 ADPCM 环形缓冲(host写/播放读,临界区保护)"
```

---

## Task 7: 播放任务泛化 + audio_clip_play + 事件接入(app.c)

**Files:**
- Modify: `components/app/src/app.c`（播放任务 `music_player_task`、`app_run` 初始化、事件点）
- Modify: `components/platform/platform_esp32/include/platform/platform_factory.h`（`audio_clip_play` 声明）

> 说明：现有 `music_player_task`(app.c 约 391 行)只处理 RTTTL。这里泛化为多路复用：新增 `s_play_src`、`s_stream_active`、片段播放缓冲，用现有 `s_music_sig`/`s_music_interrupt` 抢占。ADPCM 源以 16k 配置 I2S,RTTTL 源保持原 8k。

- [ ] **Step 1: 声明 audio_clip_play**

`platform_factory.h` 加：
```c
// 播放本地片段(异步,交播放任务)。文件不存在则静音返回。可从任意任务调用(非阻塞)。
void audio_clip_play(int clip_id);
```

- [ ] **Step 2: app.c 顶部加播放状态 + 片段请求(在 music player 区域附近)**

在 `static int16_t s_music_block[1024];` 附近加：
```c
// 音源多路复用:RTTTL(现有) / STREAM(BLE流) / CLIP(本地片段)
typedef enum { PLAY_RTTTL=0, PLAY_STREAM=1, PLAY_CLIP=2 } play_src_t;
static volatile play_src_t s_play_src = PLAY_RTTTL;
static volatile int s_clip_req = -1;               // 待播片段 id(-1=无)
static uint8_t  s_clip_buf[65536];                 // 片段容器读入缓冲(≤64KB)
static int16_t  s_pcm_block[1024];                 // ADPCM 解码 PCM 输出块(静态,勿放栈)
```

`audio_clip_play` 实现（放在 music player 任务之前，`#if PERIPH_AUDIO && PERIPH_BLE` 区块内）：
```c
void audio_clip_play(int clip_id){
    if(!s_music_sig) return;
    if(!audio_store_clip_exists(clip_id)) return;   // 存在才播
    s_clip_req = clip_id;
    s_play_src = PLAY_CLIP;
    s_music_interrupt = 1;
    xSemaphoreGive(s_music_sig);
}
```

- [ ] **Step 3: 泛化播放任务**

把 `music_player_task` 主体改为按 `s_play_src` 分派（保留原 RTTTL 分支，新增 STREAM/CLIP）：
```c
static void music_player_task(void *arg){
    (void)arg;
    for(;;){
        xSemaphoreTake(s_music_sig, portMAX_DELAY);
        s_music_interrupt = 0;
        play_src_t src = s_play_src;

        if(src == PLAY_RTTTL){
            audio_service_configure(&s_au, 8000, 16, 1);
            rtttl_t rt;
            if(rtttl_init(&rt,(const char*)s_score_play_buf,s_score_play_len)<0) continue;
            int freq,ms;
            while(!s_music_interrupt && rtttl_next(&rt,&freq,&ms)==1){
                int total=8000*ms/1000, phase=0;
                while(total>0 && !s_music_interrupt){
                    int n=total<1024?total:1024;
                    square_fill(s_music_block,n,freq,8000,&phase,6000);
                    audio_service_play(&s_au,s_music_block,(size_t)n*sizeof(int16_t));
                    total-=n;
                }
            }
        } else if(src == PLAY_CLIP){
            int id=s_clip_req;
            int len=audio_store_clip_read(id,s_clip_buf,sizeof s_clip_buf);
            if(len<(int)sizeof(audio_clip_hdr_t)) continue;
            audio_service_configure(&s_au, AUDIO_ADPCM_SR, 16, 1);
            const uint8_t *adp=s_clip_buf+sizeof(audio_clip_hdr_t);
            int adp_len=len-(int)sizeof(audio_clip_hdr_t);
            adpcm_state_t st; adpcm_state_reset(&st);
            for(int i=0;i<adp_len && !s_music_interrupt;){
                int chunk=(adp_len-i)<512?(adp_len-i):512;      // 512B→1024 样本
                int ns=adpcm_decode(&st,adp+i,chunk,s_pcm_block);
                audio_service_play(&s_au,s_pcm_block,(size_t)ns*sizeof(int16_t));
                i+=chunk;
            }
        } else if(src == PLAY_STREAM){
            audio_service_configure(&s_au, AUDIO_ADPCM_SR, 16, 1);
            adpcm_state_t st; adpcm_state_reset(&st);
            // 预缓冲:等到 ~0.5s ADPCM(0.5*16k/2=4000B)或已 EOS
            while(!s_music_interrupt && audio_stream_level()<4000 && !audio_stream_eos())
                vTaskDelay(pdMS_TO_TICKS(10));
            uint8_t adp[256];
            while(!s_music_interrupt && !audio_stream_eos()){
                int got=audio_stream_pop(adp,sizeof adp);
                if(got<=0){ vTaskDelay(pdMS_TO_TICKS(5)); continue; }  // 欠载:略等
                int ns=adpcm_decode(&st,adp,got,s_pcm_block);
                audio_service_play(&s_au,s_pcm_block,(size_t)ns*sizeof(int16_t));
            }
        }
    }
}
```

在 app.c 顶部 `#include` 区加 `#include "services/adpcm.h"`、`#include "services/audio_clip.h"`、`#include "services/audio_rx.h"`（若未包含）。

- [ ] **Step 4: app_run 挂载 audio 分区**

在 `app_run` 里创建播放任务附近（`#if PERIPH_AUDIO`）加：
```c
    audio_store_init();   // 挂载 audio SPIFFS(不存在自动格式化)
```

- [ ] **Step 5: 事件接入**

- 开机：`app_run` 末尾（播放任务就绪后）加 `audio_clip_play(CLIP_BOOT);`
- BLE 连接成功回调（找到 `已连接 conn=` 日志对应的 on_connect 回调；若在 ble_config 内部，则通过现有连接事件回调链）：加 `audio_clip_play(CLIP_CONNECT);`
- 游戏结束：`on_game_result` 函数体开头加 `audio_clip_play(CLIP_GAMEOVER);`

> 若"BLE 连接成功"没有现成的 app 层回调钩子，本步在 app.c 已有的连接状态通知处调用；如无则记为后续 TODO 并在 on_cfg_message 首次收到帧时触发一次（退化方案）。实现时先查 `ble_config`/`hal_config` 是否已暴露连接回调。

- [ ] **Step 6: 编译**

Run: `idf.py build 2>&1 | tail -5`
Expected: `Project build complete`

- [ ] **Step 7: 提交**

```bash
git add components/app/src/app.c components/platform/platform_esp32/include/platform/platform_factory.h
git commit -m "feat(audio): 播放任务多路复用(RTTTL/STREAM/CLIP)+ audio_clip_play + 事件接入"
```

---

## Task 8: BLE 分派 0x04/0x05(app.c on_cfg_message)

**Files:**
- Modify: `components/app/src/app.c`（`on_cfg_message` 内，紧接 `CFG_MSG_SCORE` 分支后）

- [ ] **Step 1: 加片段接收状态与流处理**

在 app.c 静态区加：
```c
static audio_clip_rx_t s_clip_rx;   // 0x05 片段接收
```
`app_run` 里加 `audio_clip_rx_init(&s_clip_rx);`。

- [ ] **Step 2: on_cfg_message 加两个分支**

在 `if (type == CFG_MSG_SCORE) { ... return; }` 之后、`#endif`(PERIPH_AUDIO) 之前插入：
```c
    if (type == CFG_MSG_AUDIO) {          // 0x04 实时流(不存盘)
        if (len>=1 && payload[0]==AUDIO_OP_BEGIN){
            uint16_t sr=AUDIO_ADPCM_SR; audio_stream_parse_begin(payload,len,&sr);
            audio_stream_reset();
            s_play_src=PLAY_STREAM; s_music_interrupt=1;
            if(s_music_sig) xSemaphoreGive(s_music_sig);
            notify_status(CFG_MSG_AUDIO, CFG_ST_ACK);
        } else if (len>=1 && payload[0]==AUDIO_OP_DATA){
            audio_stream_push(payload+1, len-1);      // 满则丢弃(背压),不阻塞 host
            notify_status(CFG_MSG_AUDIO, CFG_ST_ACK);
        } else if (len>=1 && payload[0]==AUDIO_OP_END){
            audio_stream_set_eos();
            notify_status(CFG_MSG_AUDIO, CFG_ST_DONE);
        } else {
            notify_status(CFG_MSG_AUDIO, CFG_ST_ERR_SEQ);
        }
        return;
    }
    if (type == CFG_MSG_AUDIO_CLIP) {     // 0x05 片段写入/替换(存 SPIFFS)
        int done=0;
        int st=audio_clip_rx_frame(&s_clip_rx, payload, len, audio_store_sink(), &done);
        uint8_t ns=(st==0)?CFG_ST_ACK:(st==1)?CFG_ST_ERR_SEQ:(st==2)?CFG_ST_ERR_STORAGE:CFG_ST_ERR_TOO_LONG;
        if(done && ns==CFG_ST_ACK){ ns=CFG_ST_DONE; ESP_LOGI(TAG,"音频片段写入完成"); }
        if(ns>=CFG_ST_ERR_FRAME) ESP_LOGW(TAG,"音频片段错误 状态0x%02X",ns);
        notify_status(CFG_MSG_AUDIO_CLIP, ns);
        return;
    }
```

- [ ] **Step 3: 编译**

Run: `idf.py build 2>&1 | tail -5`
Expected: `Project build complete`

- [ ] **Step 4: 提交**

```bash
git add components/app/src/app.c
git commit -m "feat(audio): on_cfg_message 分派 0x04 流 / 0x05 片段替换"
```

---

## Task 9: 上位机 —— 发送音频流 + 写入片段

**Files:**
- Modify: `tools/ble_card_client.py`
- Create: `tools/wav_to_adpcm.py`

- [ ] **Step 1: wav→容器工具**

Create `tools/wav_to_adpcm.py`:
```python
#!/usr/bin/env python3
# tools/wav_to_adpcm.py —— wav → 16k 单声 ADPCM 片段容器(magic "ADP1")
import sys, wave, struct
sys.path.insert(0, __file__.rsplit("/",1)[0])
import adpcm_codec as A

def load_wav_16k_mono(path):
    w=wave.open(path,"rb"); ch=w.getnchannels(); sw=w.getsampwidth(); sr=w.getframerate()
    raw=w.readframes(w.getnframes()); w.close()
    assert sw==2, "只支持 16-bit PCM wav"
    # 转单声道(取左声道)
    samp=list(struct.unpack("<%dh"%(len(raw)//2), raw))
    if ch==2: samp=samp[0::2]
    # 线性重采样到 16k
    if sr!=16000:
        out=[]; step=sr/16000.0; i=0.0
        while int(i)<len(samp): out.append(samp[int(i)]); i+=step
        samp=out
    return samp

def build_clip(path):
    samp=load_wav_16k_mono(path)
    adp=A.encode(samp)
    hdr=struct.pack("<IHBBI", 0x31504441, 16000, 1, 0, len(samp))
    return hdr+adp

if __name__=="__main__":
    if len(sys.argv)<3: sys.exit("用法: wav_to_adpcm.py in.wav out.adpcm")
    open(sys.argv[2],"wb").write(build_clip(sys.argv[1]))
    print("已写入", sys.argv[2])
```
（注：删除顶部 `import audioop_shim` 那行占位——实现时不要它，只保留 `import adpcm_codec`。）

- [ ] **Step 2: client 常量与编码复用**

`ble_card_client.py` 顶部常量区加：
```python
TYPE_AUDIO      = 0x04   # 音频流
TYPE_AUDIO_CLIP = 0x05   # 片段写入
AUDIO_OP_BEGIN, AUDIO_OP_DATA, AUDIO_OP_END = 0x00, 0x01, 0x02
```
并在文件内 `import adpcm_codec`（与 wav_to_adpcm 共用编码器）；复用其 `load_wav_16k_mono`/`build_clip`（可从 wav_to_adpcm 导入或内联同款函数）。

- [ ] **Step 3: worker 发送方法**

`BleWorker` 加两个协程 + 提交包装（镜像现有 `_send_score`/`send_score`）：
```python
async def _send_audio_stream(self, path):
    from wav_to_adpcm import load_wav_16k_mono
    import adpcm_codec as A
    adp = A.encode(load_wav_16k_mono(path))
    await self._write_frame(TYPE_AUDIO, bytes([AUDIO_OP_BEGIN, 16000 & 0xFF, (16000>>8)&0xFF]))
    step = self._write_chunk_size() - 8
    for i in range(0, len(adp), step):
        await self._write_frame(TYPE_AUDIO, bytes([AUDIO_OP_DATA]) + adp[i:i+step])
    await self._write_frame(TYPE_AUDIO, bytes([AUDIO_OP_END]))
    self.on_event("log", f"▶ 音频流发送完毕({len(adp)}B ADPCM)")

async def _send_clip(self, clip_id, path):
    from wav_to_adpcm import build_clip
    blob = build_clip(path)                        # 含 12B 容器头
    total = len(blob)
    await self._write_frame(TYPE_AUDIO_CLIP,
        bytes([AUDIO_OP_BEGIN, clip_id & 0xFF, total & 0xFF, (total>>8)&0xFF]))
    step = self._write_chunk_size() - 8
    for i in range(0, total, step):
        await self._write_frame(TYPE_AUDIO_CLIP, bytes([AUDIO_OP_DATA]) + blob[i:i+step])
    await self._write_frame(TYPE_AUDIO_CLIP, bytes([AUDIO_OP_END]))
    self.on_event("log", f"▶ 片段 clip{clip_id} 写入完毕({total}B)")

def send_audio_stream(self, path):
    if not self.client: self.on_event("log","✗ 未连接"); return
    self.submit(self._send_audio_stream(path)).add_done_callback(self._err_cb)
def send_clip(self, clip_id, path):
    if not self.client: self.on_event("log","✗ 未连接"); return
    self.submit(self._send_clip(clip_id, path)).add_done_callback(self._err_cb)
```

- [ ] **Step 4: GUI 按钮**

在配置页按钮行加：
```python
ttk.Button(row, text="发送音频流", command=self._on_send_audio).pack(side="left", padx=6)
ttk.Button(row, text="写入片段", command=self._on_send_clip).pack(side="left")
```
并加处理函数（`filedialog.askopenfilename` 选 wav；片段用 `simpledialog.askinteger` 选 clip_id 0/1/2）：
```python
def _on_send_audio(self):
    p=filedialog.askopenfilename(filetypes=[("WAV","*.wav")]);
    if p: self.worker.send_audio_stream(p)
def _on_send_clip(self):
    p=filedialog.askopenfilename(filetypes=[("WAV","*.wav")]);
    if not p: return
    cid=simpledialog.askinteger("片段","clip_id (0=开机 1=连接 2=游戏结束)",minvalue=0,maxvalue=2)
    if cid is not None: self.worker.send_clip(cid,p)
```
（确保顶部 `from tkinter import simpledialog`。）

- [ ] **Step 5: 语法自检**

Run: `python3 -m py_compile tools/adpcm_codec.py tools/wav_to_adpcm.py tools/ble_card_client.py && echo OK`
Expected: `OK`

- [ ] **Step 6: 提交**

```bash
git add tools/wav_to_adpcm.py tools/ble_card_client.py
git commit -m "feat(tools): 上位机发送音频流 + 写入片段(复用 adpcm_codec)"
```

---

## Task 10: 协议文档更新

**Files:**
- Modify: `doc/ble-protocol-spec.md`

- [ ] **Step 1: 版本 + 变更块 + 新章节**

- 顶部版本 v11→v12，加 v12 变更块（新增 0x04 音频流 / 0x05 片段替换）。
- 在消息类型表加 `0x04 音频流(ADPCM)`、`0x05 音频片段写入`。
- 新增「音频（ADPCM）」章节：
  - 格式：16kHz 单声 IMA-ADPCM 连续流，低 nibble 先，初始 predictor=0/index=0。
  - `0x04` 流：`BEGIN[sr u16 LE]/DATA[adpcm]/END`，边收边放不存盘。
  - `0x05` 片段：`BEGIN[clip_id u8][total_len u16 LE]/DATA/END`，写 SPIFFS，容器头 `[magic"ADP1"][sr u16][ch u8][rsv u8][num_samples u32]`。clip_id：0=开机 1=连接 2=游戏结束。
  - 语义：无默认音效，写入后存在才播；原子替换，上传失败不毁旧片段。

- [ ] **Step 2: 提交**

```bash
git add doc/ble-protocol-spec.md
git commit -m "docs(ble-protocol): v12 新增音频流 0x04 / 片段替换 0x05"
```

---

## Task 11: 真机集成联调 + RAM 实测

**Files:** 无（验证任务）

- [ ] **Step 1: 烧录并观察挂载/堆**

Run: `idf.py flash monitor`
Expected: 日志见 `audio 分区已挂载 /audio`；记录 `空闲堆` 数值（对比接入前，确认新增占用在预算内，无 BLE 初始化崩溃）。

- [ ] **Step 2: 片段写入 + 事件播放**

用上位机「写入片段」传一个 wav 到 clip2(游戏结束)。玩一局游戏结束 → 应听到该片段。未写入的 clip0/clip1 触发时静音、无报错。

- [ ] **Step 3: 原子替换验证**

传片段中途断开连接 → 重连后旧片段仍可播（临时文件未替换正式文件）。

- [ ] **Step 4: 流播放 + BLE 稳定性**

用「发送音频流」传一段 wav → 应连续播放；传输期间连接不断开、无重启。

- [ ] **Step 5: RTTTL 兼容**

发送一段 RTTTL 乐谱 → 正常播放；播放中发音频流 → 被抢占切换。互不死锁。

- [ ] **Step 6: 发布打包验证**

Run: `python3 scripts/release.py --no-build`
Expected: 正常出包；确认 audio 分区不在 merged-firmware.bin 内（属运行时数据），MANIFEST 无需改动。

- [ ] **Step 7: 记录结论**

把 RAM 实测数值与联调结论追加到本计划末尾或 spec，若堆偏紧则按 spec §12 调小 `AUDIO_STREAM_RING` 或 SPIFFS cache。

---

## 自检记录（写计划后核对 spec）

- **Spec 覆盖**：ADPCM(T1/2)、片段容器+接收(T3)、分区+常量(T4)、SPIFFS存储(T5)、流缓冲(T6)、播放多路复用+事件+API(T7)、BLE分派(T8)、上位机(T9)、文档(T10)、联调+RAM(T11) —— spec 各节均有对应任务。
- **类型一致**：`adpcm_state_t{predictor,index}`、`adpcm_decode`、`audio_clip_sink_t{begin,write,end}`、`audio_clip_rx_frame`、`audio_stream_parse_begin`、`audio_clip_hdr_t`、`clip_id_t{CLIP_BOOT/CONNECT/GAMEOVER/COUNT}`、`CFG_MSG_AUDIO/AUDIO_CLIP`、`PLAY_RTTTL/STREAM/CLIP` 在各任务间一致。
- **已知待实现时确认点**：T7 Step5 的"BLE 连接成功回调"钩子需实现时查 `ble_config`/`hal_config` 是否已暴露；无则用退化方案（已在步骤中写明），不阻塞其余任务。
- **占位清理**：已复查各代码块，无 TBD/占位残留。
