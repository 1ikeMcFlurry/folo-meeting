# Spec B：BLE 传 JPG 全屏看图（解码显示）

- 日期：2026-07-17
- 目标芯片：ESP32-C3（无 PSRAM，约 150KB 内部 DRAM；240×320 RGB565 全帧=150KB 放不下）
- 前置：Spec A 传输通道（[ver][type][len] 分帧 + frame_reasm + notify）已实现
- 状态：设计定稿，待实现

## 1. 背景与目标

在 Spec A 结构化通道基础上，新增「通过蓝牙下发一张全屏 JPG，卡片解码后全屏显示」。约束：C3 无 PSRAM、全屏整帧放不下，故**不落地整帧**，用 TJpgDec 分块解码、每块直接刷到屏。JPG 持久化到 flash 分区，掉电保留，开机自动显示一次。

复用 Spec A 传输层：JPG 是许多个 `type=0x02` 的小帧（每帧 ≤256B，走现有 `frame_reasm`），不改传输层。

## 2. 范围

**包含**：新增 `imgstore` flash 分区；type=0x02 流式子协议（BEGIN/DATA/END）；flash 读写模块；TJpgDec 解码 + `hal_display_flush` 全屏分块刷屏；独立看图界面（收到即显示 / 开机自动显示一次，超时或按键退回）；notify 回报。

**不包含**：图片缩放（非全屏尺寸先「居中+黑边」）；多图管理（只存最新一张）；Spec C（音乐）。

## 3. 架构（沿用六边形分层）

| 层 | 文件 | 责任 |
|---|---|---|
| 分区 | `partitions.csv` | 新增 `imgstore` data 分区 128KB |
| core/services | `jpeg_rx.c`（新增）| 纯逻辑：解析 type=0x02 子协议帧，维护接收状态机，通过回调驱动 flash 写/完成/错误 |
| ports | `hal_display.h` | 复用现有 `flush(x1,y1,x2,y2,px565)`，无需改 |
| platform | `jpeg_store.c`（新增）| `imgstore` 分区：擦除、流式追加写、写有效性 header、mmap 读、has_valid/get_len |
| platform | `jpeg_view.c`（新增）| TJpgDec 解码 + `hal_display_flush` 全屏分块刷 + 与 LVGL 协调（进入持锁独占、退出重绘）|
| platform | `platform_factory.h` | 声明 `platform_create_jpeg_store` 等工厂 / 显示器句柄传入 |
| app | `app.c` | type=0x02 → jpeg_rx；END 有效 → 看图界面；开机有效 → 显示一次；超时/按键退回 |
| 构建 | `main/idf_component.yml` 或组件依赖 | 引入 `espressif/esp_jpeg` |

数据流：
```
小程序分片写(type=0x02) → frame_reasm 收单帧 → app → jpeg_rx 解析子协议
  → jpeg_store 擦/写/收齐 → (END) jpeg_view_show: mmap → TJpgDec 分块 → hal_display_flush
  → notify [0x02][status]
```

## 4. 传输子协议（type=0x02）

每个 type=0x02 帧的 payload：
```
BEGIN: [0x00][total_len:4 小端]   → 擦 imgstore data 区,标记无效,写指针=0,记 total_len
DATA:  [0x01][chunk...]           → 顺序追加写 flash(chunk ≤250B),写指针 += chunk
END:   [0x02]                     → 校验 写指针==total_len → 写 header(magic+len)标记有效 → 触发显示
```
- GATT write 有序可靠 → 顺序追加，无需 offset。
- 相邻帧间隔 >3s 未收齐 → 丢弃并标记无效（防半图卡死）。
- notify `[0x02][status]`：
  - 0=OK（END 校验通过）
  - 1=时序错（未 BEGIN 先 DATA/END，或 END 时长度不符）
  - 2=写 flash 失败
  - 3=超长（total_len > data 区容量）
  - 4=解码失败

## 5. jpeg_rx 纯逻辑状态机（core/services）

```c
typedef enum { JPEG_RX_IDLE, JPEG_RX_RECV } jpeg_rx_state_t;
typedef struct {
    jpeg_rx_state_t state;
    uint32_t total;      // BEGIN 声明的总长
    uint32_t written;    // 已写字节
} jpeg_rx_t;

// 动作回调(由 platform 实现 flash 操作)：
typedef struct {
    int  (*begin)(void *u, uint32_t total_len);   // 擦区,返回0成功/-1超长/-2失败
    int  (*write)(void *u, const uint8_t *d, int n);
    int  (*end)(void *u);                          // 写 header 标记有效
    void *user;
} jpeg_rx_sink_t;

void jpeg_rx_init(jpeg_rx_t *r);
// 处理一个 type=0x02 帧。返回 notify 状态码(0=OK/进行中,>0=错误)。收齐(END OK)时 *done=1。
int  jpeg_rx_frame(jpeg_rx_t *r, const uint8_t *payload, int len,
                   const jpeg_rx_sink_t *sink, int *done);
```
- 纯逻辑：不含 flash/解码，全部通过 `sink` 回调。可 Unity 单测（喂 BEGIN/DATA/END 序列，mock sink）。
- 校验：未 BEGIN 收到 DATA/END → status 1；END 时 `written != total` → status 1；begin/write/end 回调返回负 → 对应 2/3。

## 6. jpeg_store（platform，`imgstore` 128KB）

- 分区查找：`esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, "imgstore")`。
- 布局：`[header: 1 sector 4KB][JPG data: 124KB]`。header = `["JPG1":4][len:4 小端]`。
- 接口：
  - `int jpeg_store_begin(uint32_t total_len)` — total_len>124KB 返回 -1；擦 header+需要的 data sectors；清有效标记。
  - `int jpeg_store_write(const uint8_t *d, int n)` — 顺序追加写 data 区（内部维护写偏移）。
  - `int jpeg_store_end(void)` — 写 header magic+len。
  - `bool jpeg_store_has_valid(void)` — 读 header 判断 magic 正确且 len 合理。
  - `int jpeg_store_mmap(const uint8_t **ptr, int *len)` — `esp_partition_mmap` data 区，返回只读指针+len。
- 写 flash 需按 sector 擦除后写；`esp_partition_write` 支持任意偏移/长度。

## 7. jpeg_view（platform，解码 + 刷屏，非阻塞）

**关键约束：解码+刷屏+超时不得在 NimBLE host task 里阻塞**（会卡死 BLE host task）。采用与 `ui_heart` 相同的 pending+tick 模式：host task 只置标志，实际解码与超时管理在 LVGL 任务侧的 tick timer 里做。

- `void jpeg_view_init(hal_display_t *disp)`：app 初始化时（持 LVGL 锁）调用，保存 `disp`，创建常驻 tick timer（周期 100ms）。
- `void jpeg_view_request(void)`：线程安全（host task 或 app 调），置 `pending=1`，请求显示 flash 中当前有效 JPG。
- `void jpeg_view_exit(void)`：线程安全置退出标志（按键调）。
- `bool jpeg_view_is_active(void)`：供爱心广播/按键判定互斥。
- tick timer 回调（**LVGL 任务上下文**，与 LVGL flush 天然串行、无需额外加锁）：
  1. 若 `pending`：清 pending → `jpeg_store_mmap(&ptr,&len)` → `esp_jpeg`(TJpgDec) 以 RGB565 块输出解码，每块 `hal_display_flush(disp, x1,y1,x2,y2, block565)`；非 240×320 则居中，四周先 flush 填黑 → `active=1, idle_ms=0`。解码失败 → notify status=4，不进 active。
  2. 若 `active`：`idle_ms += 100`；超时(≥6000ms) 或退出标志置位 → teardown：`lv_obj_invalidate(lv_screen_active())` 触发主界面重绘 → `active=0`。
- 注：tick 在 LVGL 任务里，解码期间占用该任务几百 ms（一次性，看图时主界面本就不显示），可接受。刷屏与 LVGL flush 同任务串行，不并发争 panel。

## 8. 看图界面进入/退出

- 进入：收到 END 且有效 → host task 调 `jpeg_view_request()`；开机有效 → app 调 `jpeg_view_request()`。tick 接管解码显示。
- 退出：超时 6s 自动退回；期间按任意键 → `on_btn_raw` 调 `jpeg_view_exit()`（加 `jpeg_view_is_active()` 判定，类似游戏态）。
- 互斥：看图期间 `on_ble_match` 忽略匹配爱心广播（增加 `jpeg_view_is_active()` 判定，与 `ui_game_is_active()` 并列）。

## 9. 开机自动显示

- `app_run` 在 DISPLAY+BLE 初始化后：`jpeg_view_init(s_disp)`（建 tick）；`if (jpeg_store_has_valid()) jpeg_view_request();`。主界面先建好，tick 随后解码显示覆盖，超时退回主界面。

## 10. app 接线

- `on_cfg_message`（host task）增加 `type==0x02` 分支：
  ```c
  int done = 0;
  int st = jpeg_rx_frame(&s_jpeg_rx, payload, len, &s_jpeg_sink, &done);
  hal_config_notify_status(s_cfg, 0x02, (uint8_t)st);
  if (done) jpeg_view_request();
  ```
- `s_jpeg_sink` 三回调转发 `jpeg_store_begin/write/end`。
- `on_btn_raw`：看图 active 时任意键 → `jpeg_view_exit()`。
- `on_ble_match`：开头加 `if (jpeg_view_is_active()) return;`。
- 所有 jpeg_rx/store 操作在 host task；解码显示统一延到 tick（LVGL 任务），无跨任务持锁重入问题。

## 11. 依赖 / 内存 / 风险

- 新增 managed 组件 `espressif/esp_jpeg`（TJpgDec 移植，提供块输出解码）。
- 解码内存：TJpgDec 工作区 ~3KB + 单块 RGB565 缓冲（~1–2KB），不落地整帧 → DRAM 友好，远小于当初压垮 BLE 的 57KB。
- flash mmap 读 JPG（C3 支持 data-bus mmap），零拷贝喂解码器。
- 直接 `hal_display_flush` 与 LVGL 刷新竞争同一 SPI panel：靠持 LVGL 锁期间独占解决；退出必 invalidate 重绘。
- flash 擦写慢（124KB 擦 ~百 ms 级），BEGIN 时一次擦除；写入分帧进行，可接受。

## 12. 测试

- `jpeg_rx`（纯逻辑，Unity）：正常 BEGIN→DATA×N→END 收齐；未 BEGIN 先 DATA/END 报 status 1；END 长度不符报 1；begin 超长报 3；write/end 失败透传。用 mock sink 累计字节校验。
- 硬件联调：PC/小程序分片下发一张 240×320 JPG → 全屏显示、6s/按键退回；断电重启开机自动显示；坏数据/超长 → notify 状态码正确；看图期间爱心广播被忽略。

## 13. 环境备注

- 本仓库非 git，spec 不做 git 提交。
- 改 `partitions.csv` 后需 `idf.py build`（分区表随之更新）；首次烧录建议 `idf.py erase-flash` 后全烧，确保新分区就位。
- host_test 环境已知损坏，`jpeg_rx` 单测执行以固件 build + 硬件联调为准。
