# BLE 传 JPG 全屏看图 实现计划（Spec B）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 通过 BLE（复用 Spec A 通道，type=0x02）分片下发一张 JPG，流式写入新 flash 分区，用 esp_new_jpeg 块模式解码后经 `hal_display_flush` 全屏刷屏；独立看图界面，收到即显示 / 开机自动显示一次，超时或按键退回。

**Architecture:** 纯逻辑 `jpeg_rx`（core/services，解析 BEGIN/DATA/END 子协议，可单测）通过 sink 回调驱动平台层 `jpeg_store`（esp_partition 读写 imgstore 分区）；`jpeg_view`（platform）用 esp_new_jpeg `block_enable` 块模式解码、逐条带 `hal_display_flush`，采用 ui_heart 同款 pending+tick 非阻塞模式（host task 只置标志，解码/超时在 LVGL 任务侧）。app 组合根接线。

**Tech Stack:** ESP-IDF v5.5.2、ESP32-C3、NimBLE、`espressif/esp_new_jpeg`（软件 JPEG，块模式，直出 RGB565）、esp_partition + mmap、LVGL 9.5、Unity。

**环境约定（贯穿全计划）：**
- 本仓库**非 git**：所有「Commit」步骤跳过 git，以 `idf.py build` 通过为完成判据。
- host_test 已知损坏：`jpeg_rx` 的 Unity 测试文件照写并挂到 `host_test`，执行验证以 `idf.py build` + 硬件联调为准。
- IDF 已激活（`IDF_PATH=/home/cjiio/.espressif/v5.5.2/esp-idf`，`idf.py -C /home/cjiio/trae_card build`）。
- **改分区表后首次烧录需 `idf.py -p <PORT> erase-flash` 再 flash**，确保新分区就位。
- **屏幕字节序**：`disp_st7789` 的 flush 直送 `esp_lcd_panel_draw_bitmap`，panel 期望大端 → JPEG 解码 `output_type = JPEG_PIXEL_FORMAT_RGB565_BE`。若实测颜色异常（字节序反）改 `RGB565_LE`。
- **notify 状态码**：本通道只回报「接收结果」0=OK/1=时序错/2=flash失败/3=超长；**解码失败（spec 曾列 status 4）MVP 不单独回报**（收齐后异步解码，失败仅退回主界面 + log），因为 END 帧收到时已 notify、而解码在其后的 tick 里。

---

## 文件结构

| 文件 | 责任 | 动作 |
|---|---|---|
| `partitions.csv` | 新增 `imgstore` data 分区 128KB | 修改 |
| `components/platform/platform_esp32/idf_component.yml` | 加 `espressif/esp_new_jpeg` 依赖 | 修改 |
| `components/core/services/include/services/jpeg_rx.h` | JPG 分片子协议接口 + sink | 新增 |
| `components/core/services/src/jpeg_rx.c` | BEGIN/DATA/END 状态机（纯逻辑）| 新增 |
| `components/core/services/test/test_jpeg_rx.c` | Unity 测试 | 新增 |
| `components/platform/platform_esp32/src/jpeg_store.h` | flash 读写内部接口 | 新增 |
| `components/platform/platform_esp32/src/jpeg_store.c` | imgstore 分区擦/写/header/mmap | 新增 |
| `components/platform/platform_esp32/src/jpeg_view.c` | esp_new_jpeg 块解码 + tick + 刷屏 | 新增 |
| `components/platform/platform_esp32/include/platform/platform_factory.h` | 声明 jpeg_store_* / jpeg_view_* | 修改 |
| `components/platform/platform_esp32/CMakeLists.txt` | 加源文件 + esp_partition/esp_new_jpeg | 修改 |
| `components/core/services/CMakeLists.txt` / `test/CMakeLists.txt` | 加 jpeg_rx.c / 测试 | 修改 |
| `components/app/src/app.c` | type=0x02 接线、开机显示、按键/爱心互斥 | 修改 |

依赖顺序：T1（分区+依赖）→ T2（jpeg_rx）→ T3（jpeg_store）→ T4（jpeg_view）→ T5（app 接线）→ T6（整机）。各任务均纯新增或末尾接线，每步可整工程 build。

---

## Task 1: 分区表 + esp_new_jpeg 依赖

**Files:**
- Modify: `partitions.csv`
- Modify: `components/platform/platform_esp32/idf_component.yml`

- [ ] **Step 1: 加分区**。`partitions.csv` 改为：

```csv
# Name,   Type, SubType, Offset,  Size
nvs,      data, nvs,     ,        0x6000,
phy_init, data, phy,     ,        0x1000,
factory,  app,  factory, ,        0x300000,
imgstore, data, spiffs,  ,        0x20000,
```
（`imgstore` 128KB，offset 自动接在 factory 后；总占用远小于 4MB flash。subtype 用 `spiffs` 仅为占位，代码按 label "imgstore" 查找、当裸 flash 用。）

- [ ] **Step 2: 加 esp_new_jpeg（本环境无外网，用本地组件而非 idf_component.yml 依赖）**。
  改 `idf_component.yml` 加依赖会触发 component manager 联网重解析依赖图 → 本环境失败。**实际做法**：把缓存里的预编译组件拷进 `components/esp_new_jpeg/`（本地组件，IDF 自动发现，不经 manager）：
  ```bash
  D=/home/cjiio/.cache/Espressif/ComponentManager/service_d92d8f1e/espressif__esp_new_jpeg_1.0.1_98823384
  DST=/home/cjiio/trae_card/components/esp_new_jpeg
  mkdir -p $DST/lib
  cp $D/CMakeLists.txt $D/LICENSE $DST/
  cp -r $D/include $DST/
  cp -r $D/lib/esp32c3 $DST/lib/     # 只需 c3 预编译库;不拷 idf_component.yml 以免 manager 介入
  ```
  组件名 = 目录名 `esp_new_jpeg`；其 CMakeLists 用 `add_prebuilt_library` 链接 `lib/esp32c3/libesp_new_jpeg.a`，头在 `include/`。Task 4 的 `PRIV_REQUIRES esp_new_jpeg` 引用此本地组件。（已完成）

- [ ] **Step 3: 验证**。Run: `idf.py -C /home/cjiio/trae_card build`。
Expected: 成功。component manager 从本地缓存拉取 esp_new_jpeg（无外网也可，缓存已存在于 `~/.cache/Espressif/ComponentManager`）；分区表更新无报错。此时尚无代码用它。

- [ ] **Step 4: Commit**：非 git，跳过。

---

## Task 2: jpeg_rx 纯逻辑（core/services）

**Files:**
- Create: `components/core/services/include/services/jpeg_rx.h`
- Create: `components/core/services/src/jpeg_rx.c`
- Test: `components/core/services/test/test_jpeg_rx.c`
- Modify: `components/core/services/CMakeLists.txt` / `test/CMakeLists.txt`

- [ ] **Step 1: 头文件** `jpeg_rx.h`

```c
// components/core/services/include/services/jpeg_rx.h
#pragma once
#include <stdint.h>

#define JPEG_RX_OP_BEGIN 0x00   // [0x00][total_len:4 小端]
#define JPEG_RX_OP_DATA  0x01   // [0x01][chunk...]
#define JPEG_RX_OP_END   0x02   // [0x02]

// flash 落盘回调(由 platform 实现)。返回 0 成功;begin 返回 -1=超长 -2=失败;write/end 返回 -2=失败。
typedef struct {
    int  (*begin)(void *user, uint32_t total_len);
    int  (*write)(void *user, const uint8_t *d, int n);
    int  (*end)(void *user);
    void *user;
} jpeg_rx_sink_t;

typedef struct {
    int      receiving;   // 是否已 BEGIN
    uint32_t total;
    uint32_t written;
} jpeg_rx_t;

void jpeg_rx_init(jpeg_rx_t *r);

// 处理一个 type=0x02 帧。返回 notify 状态码:
//   0=OK/进行中  1=时序错(未BEGIN先DATA/END,或END长度不符,或未知op)  2=flash失败  3=超长
// END 收齐且成功 → *done=1(其余情形 *done=0)。
int  jpeg_rx_frame(jpeg_rx_t *r, const uint8_t *payload, int len,
                   const jpeg_rx_sink_t *sink, int *done);
```

- [ ] **Step 2: 测试** `test_jpeg_rx.c`

```c
#include "unity.h"
#include "services/jpeg_rx.h"
#include <string.h>

static uint8_t g_buf[1024];
static int g_written, g_ended, g_begin_ret, g_write_ret, g_end_ret;
static int m_begin(void*u,uint32_t t){(void)u;(void)t; g_written=0; return g_begin_ret;}
static int m_write(void*u,const uint8_t*d,int n){(void)u; memcpy(g_buf+g_written,d,n); g_written+=n; return g_write_ret;}
static int m_end(void*u){(void)u; g_ended=1; return g_end_ret;}
static jpeg_rx_sink_t SINK = { m_begin, m_write, m_end, NULL };
static void reset(void){ g_written=0; g_ended=0; g_begin_ret=0; g_write_ret=0; g_end_ret=0; }

TEST_CASE("jpeg_rx normal begin/data/end", "[jpegrx]") {
    reset(); jpeg_rx_t r; jpeg_rx_init(&r); int done;
    uint8_t begin[5]={0x00,4,0,0,0};                 // total=4
    TEST_ASSERT_EQUAL_INT(0, jpeg_rx_frame(&r,begin,5,&SINK,&done)); TEST_ASSERT_EQUAL_INT(0,done);
    uint8_t data[5]={0x01,'a','b','c','d'};
    TEST_ASSERT_EQUAL_INT(0, jpeg_rx_frame(&r,data,5,&SINK,&done)); TEST_ASSERT_EQUAL_INT(0,done);
    uint8_t end[1]={0x02};
    TEST_ASSERT_EQUAL_INT(0, jpeg_rx_frame(&r,end,1,&SINK,&done)); TEST_ASSERT_EQUAL_INT(1,done);
    TEST_ASSERT_EQUAL_INT(4,g_written); TEST_ASSERT_TRUE(g_ended);
    TEST_ASSERT_EQUAL_INT(0,memcmp(g_buf,"abcd",4));
}
TEST_CASE("jpeg_rx data/end before begin is sequence error", "[jpegrx]") {
    reset(); jpeg_rx_t r; jpeg_rx_init(&r); int done;
    uint8_t data[2]={0x01,'x'};
    TEST_ASSERT_EQUAL_INT(1, jpeg_rx_frame(&r,data,2,&SINK,&done));
    uint8_t end[1]={0x02};
    TEST_ASSERT_EQUAL_INT(1, jpeg_rx_frame(&r,end,1,&SINK,&done));
}
TEST_CASE("jpeg_rx end length mismatch → status 1, done 0", "[jpegrx]") {
    reset(); jpeg_rx_t r; jpeg_rx_init(&r); int done;
    uint8_t begin[5]={0x00,10,0,0,0};
    jpeg_rx_frame(&r,begin,5,&SINK,&done);
    uint8_t data[3]={0x01,'a','b'};
    jpeg_rx_frame(&r,data,3,&SINK,&done);
    uint8_t end[1]={0x02};
    TEST_ASSERT_EQUAL_INT(1, jpeg_rx_frame(&r,end,1,&SINK,&done)); TEST_ASSERT_EQUAL_INT(0,done);
}
TEST_CASE("jpeg_rx begin oversize returns 3", "[jpegrx]") {
    reset(); g_begin_ret=-1; jpeg_rx_t r; jpeg_rx_init(&r); int done;
    uint8_t begin[5]={0x00,0,0,2,0};                 // total=0x20000
    TEST_ASSERT_EQUAL_INT(3, jpeg_rx_frame(&r,begin,5,&SINK,&done));
}
```

- [ ] **Step 3: 实现** `jpeg_rx.c`

```c
// components/core/services/src/jpeg_rx.c —— JPG 分片子协议状态机(纯逻辑)
#include "services/jpeg_rx.h"

void jpeg_rx_init(jpeg_rx_t *r) { r->receiving = 0; r->total = 0; r->written = 0; }

int jpeg_rx_frame(jpeg_rx_t *r, const uint8_t *payload, int len,
                  const jpeg_rx_sink_t *sink, int *done) {
    if (done) *done = 0;
    if (len < 1) return 1;
    uint8_t op = payload[0];
    const uint8_t *body = payload + 1;
    int blen = len - 1;

    if (op == JPEG_RX_OP_BEGIN) {
        if (blen < 4) return 1;
        uint32_t total = (uint32_t)body[0] | ((uint32_t)body[1] << 8)
                       | ((uint32_t)body[2] << 16) | ((uint32_t)body[3] << 24);
        int rc = sink->begin(sink->user, total);
        if (rc == -1) { r->receiving = 0; return 3; }   // 超长
        if (rc < 0)   { r->receiving = 0; return 2; }   // flash 失败
        r->receiving = 1; r->total = total; r->written = 0;
        return 0;
    }
    if (op == JPEG_RX_OP_DATA) {
        if (!r->receiving) return 1;
        if (blen > 0) {
            if (sink->write(sink->user, body, blen) < 0) { r->receiving = 0; return 2; }
            r->written += (uint32_t)blen;
        }
        return 0;
    }
    if (op == JPEG_RX_OP_END) {
        if (!r->receiving) return 1;
        r->receiving = 0;
        if (r->written != r->total) return 1;           // 长度不符
        if (sink->end(sink->user) < 0) return 2;
        if (done) *done = 1;
        return 0;
    }
    return 1;   // 未知 op
}
```

- [ ] **Step 4: CMakeLists**。`components/core/services/CMakeLists.txt` 的 SRCS 追加 `"src/jpeg_rx.c"`。`components/core/services/test/CMakeLists.txt` 的 SRCS 追加 `"test_jpeg_rx.c"`。

- [ ] **Step 5: 验证**。Run: `idf.py -C /home/cjiio/trae_card build`。Expected: 编译通过（纯新增）。host 可运行时 `[jpegrx]` 4 用例 PASS。

- [ ] **Step 6: Commit**：非 git，跳过。

---

## Task 3: jpeg_store（platform，flash 读写）

**Files:**
- Create: `components/platform/platform_esp32/src/jpeg_store.h`
- Create: `components/platform/platform_esp32/src/jpeg_store.c`
- Modify: `components/platform/platform_esp32/include/platform/platform_factory.h`
- Modify: `components/platform/platform_esp32/CMakeLists.txt`

- [ ] **Step 1: 内部头** `jpeg_store.h`

```c
// components/platform/platform_esp32/src/jpeg_store.h —— imgstore 分区读写(platform 内部)
#pragma once
#include <stdint.h>
#include <stdbool.h>

int  jpeg_store_begin(uint32_t total_len);          // 0 / -1超长 / -2失败:擦区,清有效标记
int  jpeg_store_write(const uint8_t *d, int n);     // 0 / -2:顺序追加写
int  jpeg_store_end(void);                          // 0 / -2:写 header(magic+len)
bool jpeg_store_has_valid(void);                    // header 有效且 len 合理
int  jpeg_store_mmap(const uint8_t **ptr, int *len);// 0:映射 data 区为只读指针
void jpeg_store_unmap(void);                        // 释放上次 mmap
```

- [ ] **Step 2: 实现** `jpeg_store.c`

```c
// components/platform/platform_esp32/src/jpeg_store.c —— imgstore 分区: header + JPG data
#include "platform/board_config.h"
#if PERIPH_DISPLAY
#include "jpeg_store.h"
#include "esp_partition.h"
#include <string.h>

#define HDR_OFF   0
#define HDR_SZ    0x1000                 // 1 sector 保留给 header
#define DATA_OFF  0x1000
static const char MAGIC[4] = {'J','P','G','1'};

static uint32_t s_wr;                    // 当前写偏移(相对 DATA_OFF)
static esp_partition_mmap_handle_t s_mh;
static bool s_mapped;

static const esp_partition_t *part(void) {
    return esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, "imgstore");
}
static uint32_t rd_len(const esp_partition_t *p) {
    uint8_t h[8];
    if (esp_partition_read(p, HDR_OFF, h, sizeof h) != ESP_OK) return 0;
    if (memcmp(h, MAGIC, 4) != 0) return 0;
    return (uint32_t)h[4] | ((uint32_t)h[5]<<8) | ((uint32_t)h[6]<<16) | ((uint32_t)h[7]<<24);
}

int jpeg_store_begin(uint32_t total_len) {
    const esp_partition_t *p = part(); if (!p) return -2;
    uint32_t cap = p->size - DATA_OFF;
    if (total_len > cap) return -1;
    uint32_t data_erase = (total_len + 0xFFFu) & ~0xFFFu;   // 向上取整到 sector
    if (esp_partition_erase_range(p, 0, HDR_SZ + data_erase) != ESP_OK) return -2;
    s_wr = 0;
    return 0;
}
int jpeg_store_write(const uint8_t *d, int n) {
    const esp_partition_t *p = part(); if (!p) return -2;
    if (esp_partition_write(p, DATA_OFF + s_wr, d, (size_t)n) != ESP_OK) return -2;
    s_wr += (uint32_t)n;
    return 0;
}
int jpeg_store_end(void) {
    const esp_partition_t *p = part(); if (!p) return -2;
    uint8_t h[8];
    memcpy(h, MAGIC, 4);
    h[4]=s_wr&0xFF; h[5]=(s_wr>>8)&0xFF; h[6]=(s_wr>>16)&0xFF; h[7]=(s_wr>>24)&0xFF;
    if (esp_partition_write(p, HDR_OFF, h, sizeof h) != ESP_OK) return -2;
    return 0;
}
bool jpeg_store_has_valid(void) {
    const esp_partition_t *p = part(); if (!p) return false;
    uint32_t l = rd_len(p);
    return l > 0 && l <= (p->size - DATA_OFF);
}
int jpeg_store_mmap(const uint8_t **ptr, int *len) {
    const esp_partition_t *p = part(); if (!p) return -2;
    uint32_t l = rd_len(p);
    if (l == 0 || l > (p->size - DATA_OFF)) return -2;
    if (s_mapped) { esp_partition_munmap(s_mh); s_mapped = false; }
    const void *m = NULL;
    if (esp_partition_mmap(p, DATA_OFF, l, ESP_PARTITION_MMAP_DATA, &m, &s_mh) != ESP_OK) return -2;
    s_mapped = true;
    *ptr = (const uint8_t *)m; *len = (int)l;
    return 0;
}
void jpeg_store_unmap(void) {
    if (s_mapped) { esp_partition_munmap(s_mh); s_mapped = false; }
}
#endif // PERIPH_DISPLAY
```

- [ ] **Step 3: factory.h 声明**。`platform_factory.h` 末尾追加（这些供 app 组合根把 BLE 分片写 flash）：

```c
// JPG 接收存储(imgstore 分区)。供组合根把 BLE 分片写入 flash(仅 PERIPH_DISPLAY 下有定义)。
int  jpeg_store_begin(uint32_t total_len);
int  jpeg_store_write(const uint8_t *d, int n);
int  jpeg_store_end(void);
bool jpeg_store_has_valid(void);
```

- [ ] **Step 4: CMakeLists**。`components/platform/platform_esp32/CMakeLists.txt`：SRCS 追加 `"src/jpeg_store.c"`；`REQUIRES` 里（或 PRIV_REQUIRES）追加 `esp_partition`。

- [ ] **Step 5: 验证**。Run: `idf.py -C /home/cjiio/trae_card build`。Expected: 编译通过（纯新增，无独立单测，硬件验证见 Task 6）。

- [ ] **Step 6: Commit**：非 git，跳过。

---

## Task 4: jpeg_view（platform，块解码 + tick + 刷屏）

**Files:**
- Create: `components/platform/platform_esp32/src/jpeg_view.c`
- Modify: `components/platform/platform_esp32/include/platform/platform_factory.h`
- Modify: `components/platform/platform_esp32/CMakeLists.txt`

- [ ] **Step 1: 实现** `jpeg_view.c`

```c
// components/platform/platform_esp32/src/jpeg_view.c —— JPG 全屏看图(esp_new_jpeg 块模式 + tick)
#include "platform/board_config.h"
#if PERIPH_DISPLAY
#include "platform/platform_factory.h"
#include "hal/hal_display.h"
#include "jpeg_store.h"
#include "esp_jpeg_dec.h"
#include "esp_jpeg_common.h"
#include "esp_log.h"
#include "lvgl.h"
#include <string.h>

static const char *TAG = "jpeg_view";
#define SCR_W 240
#define SCR_H 320
#define VIEW_TIMEOUT_MS 6000
#define TICK_MS 100

static hal_display_t *s_disp;
static lv_timer_t    *s_timer;
static volatile int   s_pending;
static volatile int   s_exit_req;
static bool           s_active;
static int            s_idle_ms;

static void fill_black(void) {
    uint16_t line[SCR_W];
    memset(line, 0, sizeof line);
    for (int y = 0; y < SCR_H; y++) hal_display_flush(s_disp, 0, y, SCR_W - 1, y, line);
}

// 解码 flash 中的 JPG 并全屏刷出。成功返回 0，失败 <0。
static int decode_and_show(void) {
    const uint8_t *jpg; int jlen;
    if (jpeg_store_mmap(&jpg, &jlen) != 0) return -1;

    jpeg_dec_config_t cfg = DEFAULT_JPEG_DEC_CONFIG();
    cfg.output_type  = JPEG_PIXEL_FORMAT_RGB565_BE;   // ST7789 大端;颜色反则改 RGB565_LE
    cfg.block_enable = true;
    jpeg_dec_handle_t dec = NULL;
    if (jpeg_dec_open(&cfg, &dec) != JPEG_ERR_OK) { jpeg_store_unmap(); return -1; }

    jpeg_dec_io_t io; memset(&io, 0, sizeof io);
    io.inbuf = (uint8_t *)jpg; io.inbuf_len = jlen;
    jpeg_dec_header_info_t info;
    int rc = -1;
    if (jpeg_dec_parse_header(dec, &io, &info) == JPEG_ERR_OK
        && info.width <= SCR_W && info.height <= SCR_H) {
        int outlen = 0, count = 0;
        jpeg_dec_get_outbuf_len(dec, &outlen);
        jpeg_dec_get_process_count(dec, &count);
        uint8_t *out = jpeg_calloc_align(outlen, 16);
        if (out) {
            io.outbuf = out;
            int block_h = info.width > 0 ? (outlen / (info.width * 2)) : 16;
            int x0 = (SCR_W - info.width) / 2;
            int y  = (SCR_H - info.height) / 2;
            rc = 0;
            for (int i = 0; i < count; i++) {
                if (jpeg_dec_process(dec, &io) != JPEG_ERR_OK) { rc = -1; break; }
                int h = block_h;
                if (y + h > (SCR_H - (SCR_H - info.height) / 2)) h = (SCR_H - (SCR_H - info.height) / 2) - y;
                if (h > 0) hal_display_flush(s_disp, x0, y, x0 + info.width - 1, y + h - 1, out);
                y += block_h;
            }
            jpeg_free_align(out);
        }
    } else {
        ESP_LOGW(TAG, "parse fail or img too big (%dx%d)", info.width, info.height);
    }
    jpeg_dec_close(dec);
    jpeg_store_unmap();
    return rc;
}

static void tick(lv_timer_t *t) {
    (void)t;
    if (s_pending) {
        s_pending = 0;
        fill_black();
        if (decode_and_show() == 0) { s_active = true; s_idle_ms = 0; s_exit_req = 0; }
        else lv_obj_invalidate(lv_screen_active());   // 解码失败:重画主界面
    } else if (s_active) {
        s_idle_ms += TICK_MS;
        if (s_exit_req || s_idle_ms >= VIEW_TIMEOUT_MS) {
            s_active = false;
            lv_obj_invalidate(lv_screen_active());
        }
    }
}

void jpeg_view_init(hal_display_t *disp) {
    s_disp = disp;
    s_timer = lv_timer_create(tick, TICK_MS, NULL);
}
void jpeg_view_request(void) { s_pending = 1; }
void jpeg_view_exit(void)    { s_exit_req = 1; }
bool jpeg_view_is_active(void) { return s_active || s_pending; }
#endif // PERIPH_DISPLAY
```
注：`jpeg_calloc_align`/`jpeg_free_align`/`JPEG_ERR_OK`/`JPEG_PIXEL_FORMAT_RGB565_BE` 来自 `esp_jpeg_common.h`；实现时先 Read 该头确认这些确切名字（若不同按实际改，如 `esp_jpeg_calloc_align`）。

- [ ] **Step 2: factory.h 声明**。`platform_factory.h` 末尾追加：

```c
// JPG 全屏看图(非阻塞,tick 驱动;仅 PERIPH_DISPLAY 下有定义)。
void jpeg_view_init(hal_display_t *disp);
void jpeg_view_request(void);
void jpeg_view_exit(void);
bool jpeg_view_is_active(void);
```

- [ ] **Step 3: CMakeLists**。`components/platform/platform_esp32/CMakeLists.txt`：SRCS 追加 `"src/jpeg_view.c"`；`PRIV_REQUIRES` 追加 `esp_new_jpeg`。

- [ ] **Step 4: 验证**。Run: `idf.py -C /home/cjiio/trae_card build`。Expected: 编译通过；若 `esp_jpeg_common.h` 里函数名不同会报错，据实修正后再 build。

- [ ] **Step 5: Commit**：非 git，跳过。

---

## Task 5: app.c 接线

**Files:**
- Modify: `components/app/src/app.c`

- [ ] **Step 1: jpeg_rx include + sink（必须放在 `on_cfg_message` 之前，否则引用在定义之前会编译失败）**。

(a) 顶部 include：把 app.c 现有的
```c
#if PERIPH_BLE
#include "services/profile_ctl.h"
#include "services/config_json.h"
#endif
```
改为：
```c
#if PERIPH_BLE
#include "services/profile_ctl.h"
#include "services/config_json.h"
#if PERIPH_DISPLAY
#include "services/jpeg_rx.h"
#endif
#endif
```

(b) 在 `save_profile` 函数之后、`on_cfg_message` 函数之前（都在同一个 `#if PERIPH_BLE` 块内）插入 jpeg 接收状态与 sink：
```c
#if PERIPH_DISPLAY
static jpeg_rx_t s_jpeg_rx;
static int jsink_begin(void *u, uint32_t t) { (void)u; return jpeg_store_begin(t); }
static int jsink_write(void *u, const uint8_t *d, int n) { (void)u; return jpeg_store_write(d, n); }
static int jsink_end(void *u) { (void)u; return jpeg_store_end(); }
static const jpeg_rx_sink_t s_jsink = { jsink_begin, jsink_write, jsink_end, NULL };
#endif
```
（`jpeg_store_*`/`jpeg_view_request` 来自 `platform_factory.h`，app.c 顶部已 include，无条件声明可用。**不要**把这些定义放到文件后部的 `s_scan` 块——那在 `on_cfg_message` 之后。）

- [ ] **Step 2: on_cfg_message 加 type=0x02 分支**。当前函数体开头是 `if (type != CFG_MSG_JSON) return;`。替换为先处理 JPG：

```c
static void on_cfg_message(uint8_t type, const uint8_t *payload, int len, void *user) {
    (void)user;
#if PERIPH_DISPLAY
    if (type == 0x02) {   // JPG 分片(BEGIN/DATA/END)
        int done = 0;
        int st = jpeg_rx_frame(&s_jpeg_rx, payload, len, &s_jsink, &done);
        if (s_cfg) hal_config_notify_status(s_cfg, 0x02, (uint8_t)st);
        if (done) jpeg_view_request();
        return;
    }
#endif
    if (type != CFG_MSG_JSON) return;
```
（其余 JSON 处理逻辑不变。注意 `s_jpeg_rx`/`s_jsink`/`jpeg_view_request` 只在 `PERIPH_BLE && PERIPH_DISPLAY` 下定义；on_cfg_message 在 `#if PERIPH_BLE` 内，故此 0x02 分支用 `#if PERIPH_DISPLAY` 包裹即可，二者同时满足时有定义。）

- [ ] **Step 3: on_btn_raw 加看图退出**。当前 `on_btn_raw` 开头 `if (!ui_game_is_active()) return;`。改为：

```c
static void on_btn_raw(int index, hal_btn_event_t e, void *user) {
    (void)user;
    if (jpeg_view_is_active()) { if (e == HAL_BTN_PRESS || e == HAL_BTN_CLICK) jpeg_view_exit(); return; }
    if (!ui_game_is_active()) return;
```
（看图态任意键按下即退出，且不再往游戏转发。`jpeg_view_is_active` 在 PERIPH_DISPLAY 下有定义；`on_btn_raw` 在 `#if PERIPH_DISPLAY && PERIPH_BUTTON` 内，满足。）

- [ ] **Step 4: on_ble_match 加互斥**。当前开头 `if (ui_game_is_active()) return;`。其前加一行：

```c
static void on_ble_match(const uint8_t *data, int len, void *user) {
    (void)data; (void)len; (void)user;
    if (jpeg_view_is_active()) return;
    if (ui_game_is_active()) return;
```

- [ ] **Step 5: app_run 初始化 + 开机显示**。`app_run` 里 `#if PERIPH_BLE` → `#if PERIPH_DISPLAY` 段当前是：

```c
#if PERIPH_DISPLAY
    s_scan = platform_create_ble_scan(&board);
    hal_ble_scan_on_match(s_scan, on_ble_match, NULL);
#endif
```
替换为：

```c
#if PERIPH_DISPLAY
    if (platform_lvgl_lock(0)) { jpeg_view_init(s_disp); platform_lvgl_unlock(); }
    jpeg_rx_init(&s_jpeg_rx);
    s_scan = platform_create_ble_scan(&board);
    hal_ble_scan_on_match(s_scan, on_ble_match, NULL);
    if (jpeg_store_has_valid()) jpeg_view_request();   // 开机若有保留图,自动显示一次
#endif
```

- [ ] **Step 6: 验证**。Run: `idf.py -C /home/cjiio/trae_card build`。Expected: 整工程编译链接通过，无未定义符号。

- [ ] **Step 7: Commit**：非 git，跳过。

---

## Task 6: 整机 build + 硬件联调

**Files:** 无（验证）

- [ ] **Step 1: 全量构建**。Run: `idf.py -C /home/cjiio/trae_card build`。Expected: 成功。
- [ ] **Step 2: DRAM 检查**。Run: `idf.py -C /home/cjiio/trae_card size`。Expected: DRAM 静态占用与 Spec A 后相近（jpeg 解码缓冲是运行时 `jpeg_calloc_align`，非常驻；`fill_black` 的 480B 在栈）；剩余堆仍充裕，BLE 可初始化。
- [ ] **Step 3: 擦写烧录**（因新增分区）。Run: `idf.py -C /home/cjiio/trae_card -p <PORT> erase-flash && idf.py -C /home/cjiio/trae_card -p <PORT> flash monitor`。Expected: 正常进主界面，无崩溃，日志无 `imgstore` 找不到。
- [ ] **Step 4: 下发 JPG**。用 PC/小程序把一张 **240×320（或更小、宽高均 8 的倍数）JPG** 按子协议分片写命令特征(0x10)：先 `01 02 05 00 00 <total_len:4小端>`(BEGIN)，再多帧 `01 02 <len> 01 <chunk...>`(DATA，chunk≤250B)，最后 `01 02 01 00 02`(END)。Expected: 收齐后全屏显示该图；notify(0x11) 依次收到 `02 00`。
- [ ] **Step 5: 退出**。等 6s 自动退回主界面；或按任意键立即退回。Expected: 主界面正常重绘。
- [ ] **Step 6: 持久化**。断电重启。Expected: 开机自动全屏显示上次的图一次，超时/按键后进主界面。
- [ ] **Step 7: 错误路径**。发超大 total_len(>124KB) 的 BEGIN → notify `02 03`；未 BEGIN 直接 DATA → `02 01`。Expected: 状态码正确，不崩溃。
- [ ] **Step 8: 回归**。JSON 配置(type 0x01)、爱心广播、dock 功能不受影响；看图期间爱心广播被忽略。

---

## Self-Review（对照 spec）

- **传输子协议 BEGIN/DATA/END**（spec §4）→ Task 2 jpeg_rx ✅
- **flash 分区 imgstore + header 有效性**（spec §3,§6）→ Task 1 分区 + Task 3 jpeg_store ✅
- **块解码 + hal_display_flush 不落地整帧**（spec §1,§7）→ Task 4 jpeg_view（esp_new_jpeg block_enable，RGB565_BE）✅
- **非阻塞 pending+tick 看图 + 超时/按键退出**（spec §7,§8）→ Task 4 tick + Task 5 on_btn_raw ✅
- **开机自动显示一次**（spec §9）→ Task 5 Step 5 ✅
- **互斥（游戏/爱心）**（spec §8）→ Task 5 Step 4 on_ble_match ✅
- **notify 状态码**（spec §4）→ jpeg_rx 返回 0/1/2/3 + Task 5 回报；**解码失败 status 4 MVP 不回报**（已在环境约定说明，与 spec 的偏差已标注）
- **依赖 esp_new_jpeg**（spec §11）→ Task 1 ✅
- **类型一致性**：`jpeg_rx_sink_t`/`jpeg_rx_frame`（Task2）与 Task5 sink 用法一致；`jpeg_store_*`（Task3）与 sink/factory 声明一致；`jpeg_view_*`（Task4）与 Task5/factory 一致 ✅
- **占位符**：无 TBD；`esp_jpeg_common.h` 的 3 个符号名在 Task4 注明「实现时 Read 确认」，非占位而是校验步骤 ✅

已知取舍：解码失败不单独 notify（时机所限）；图须 ≤240×320 且块模式要求宽高 8 的倍数，否则解码失败退回主界面。
