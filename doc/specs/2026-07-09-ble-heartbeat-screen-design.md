# 设计：监听蓝牙广播 → 爱心跳动屏

日期：2026-07-09
状态：已确认，待实现

## 1. 目标

设备在扫描到**特定厂商自定义蓝牙广播**时，把界面从主界面（个人名片/dock）切换到一个新的爱心屏；此后每再次收到一个匹配广播包，爱心就跳动一下。连续一段时间收不到匹配广播则自动退回主界面。

## 2. 需求确认（已定）

- **识别方式**：广播包中的 Manufacturer Specific Data（AD 类型 `0xFF`）。
- **默认厂商数据格式**：公司 ID `0xFFFF`（BLE SIG 测试保留值）+ 魔数 ASCII `"HB"`。
  - 广播里的厂商数据字节序列 = `FF FF 48 42 [任意负载...]`（公司 ID 小端在前）。
  - 匹配条件：`mfg_data_len >= 4 && mfg_data[0]==0xFF && mfg_data[1]==0xFF && mfg_data[2]==0x48 && mfg_data[3]==0x42`。多余负载忽略。
- **跳动触发**：收到就跳，不去重。扫描时关闭重复过滤（`filter_duplicates=0`），广播每约 100ms 重复一次 → 爱心连续跳动。
- **进入时机**：处于主界面时收到首个匹配包 → 切到爱心屏。**游戏进行中忽略**匹配（不打断小游戏）。
- **退出时机**：连续 **5 秒**无匹配广播 → 自动退回主界面（不需要按键）。

### 发送端参考（供联调）

- 手机 nRF Connect → Advertiser：添加 Manufacturer data，公司 ID `0xFFFF`，附加数据 `4842`（即整体 `FFFF4842`）。
- 或另一块 ESP32：`ble_hs_adv_fields.mfg_data = {0xFF,0xFF,0x48,0x42}; mfg_data_len=4;`，非连接可扫描广播即可。

## 3. 架构（遵循现有六边形分层）

```
外部设备广播 FF FF 48 42 ...
  → NimBLE 扫描 disc 回调 (platform: ble_scan.c) 解析 + 匹配
  → app 的 on_ble_match (NimBLE host 任务上下文)
  → [首次/主界面] ui_heart_open()  然后  ui_heart_beat()
  → ui_heart 内部 lv_timer (LVGL 任务) 播放缩放动画、复位 5s 空闲计时
  → 5s 无新匹配包 → 自动载回原屏 + 回调 exit_cb
```

### ① 新 HAL 端口 `core/ports/include/hal/hal_ble_scan.h`

纯接口，不含 LVGL / NimBLE，可被 host_test mock。

```c
#pragma once
#include <stdint.h>

// 一次匹配命中：命中的厂商数据原始字节 + 长度（含公司 ID 两字节）。
typedef void (*ble_match_cb_t)(const uint8_t *data, int len, void *user);

typedef struct hal_ble_scan_s hal_ble_scan_t;
typedef struct {
    void (*on_match)(hal_ble_scan_t *self, ble_match_cb_t cb, void *user);
} hal_ble_scan_api_t;
struct hal_ble_scan_s { const hal_ble_scan_api_t *api; void *impl; };

static inline void hal_ble_scan_on_match(hal_ble_scan_t *s, ble_match_cb_t cb, void *user) {
    s->api->on_match(s, cb, user);
}
```

`platform_factory.h` 增加：`hal_ble_scan_t *platform_create_ble_scan(const board_config_t *);`

### ② 平台实现 `platform_esp32/src/ble_scan.c` + 内部头 `ble_internal.h`

- **与 `ble_config.c` 共用同一个 NimBLE host**，不重复 `nimble_port_init`。
- 新增内部头 `ble_internal.h` 暴露 `void ble_scan_start(uint8_t addr_type);`。
- `ble_config.c` 的 `on_sync()` 里，在 `start_adv()` 之后追加一行 `ble_scan_start(s_addr_type);`（`#if` 保护，仅在编入扫描时）。
- `ble_scan.c`：
  - `platform_create_ble_scan()` 只登记 app 回调、返回静态句柄。
  - `ble_scan_start()` 调 `ble_gap_disc()`：被动扫描（`passive=1`）、`filter_duplicates=0`、`BLE_HS_FOREVER`，disc 回调 `scan_gap_event`。
  - `scan_gap_event`：仅处理 `BLE_GAP_EVENT_DISC`，用 `ble_hs_adv_parse_fields` 取 `mfg_data`/`mfg_data_len`，调纯函数 `ble_scan_match()` 判定，命中则调 app 回调。
  - 抽出纯函数 `bool ble_scan_match(const uint8_t *mfg, int len)`（匹配规则，见 §2），便于单测，不依赖 NimBLE。
- 常量集中定义：`BLE_MATCH_COMPANY_ID 0xFFFF`、魔数 `"HB"`。

> 启动顺序与竞态：app 先 `platform_create_ble_config()`（初始化 NimBLE、注册 `on_sync`、起 host_task），再 `platform_create_ble_scan()` 登记回调。`on_sync` 为 host 同步后异步触发，届时回调已登记。极早期个别包丢失无害（广播持续重复）。

### ③ 新表现层屏幕 `ui/presentation/ui_heart.{c,h}`

照抄 `ui_game` 范式（独立 LVGL 屏 + 线程安全的输入函数 + 退出回调 + 自动载回原屏）。

```c
// ui_heart.h
#pragma once
#include <stdbool.h>
typedef void (*ui_heart_exit_cb_t)(void *user);

// 建爱心屏并载入。须在 LVGL 任务上下文/持锁时调用。
void ui_heart_open(ui_heart_exit_cb_t on_exit, void *user);
// 触发一次跳动。线程安全：仅置 pending 计数并复位空闲计时，可从 NimBLE host 任务调用。
void ui_heart_beat(void);
// 是否处于爱心屏（app 据此决定开屏还是仅跳一下）。
bool ui_heart_is_active(void);
```

- **爱心图形**：红色爱心，用 LVGL image（嵌入的小位图 C 数组，RGB565 或 A8）作为一个 `lv_image` 对象居中显示。
- **跳动动画**：`lv_anim` 驱动 `lv_image_set_scale`（放大到约 130% 再回弹到 100%），单次约 150–200ms；若上一跳未结束又来新包，重启动画。
- **空闲/退出**：内部 `lv_timer`（如每 100ms）消费 pending 跳动计数、累加空闲毫秒；`ui_heart_beat()` 把空闲清零；空闲满 5000ms → 载回记录的上一屏（`lv_scr_load`）、停 timer、调 `on_exit`、清 active 标志。
- `ui_heart_beat()` 只做置标志/复位计时（原子/volatile），真正动画在 timer 里跑，避免跨线程碰 LVGL 对象。

`ui/presentation/CMakeLists.txt` 增加 `ui_heart.c`。

### ④ app 接线 `app/src/app.c`

在 `#if PERIPH_BLE && PERIPH_DISPLAY` 下：

```c
static hal_ble_scan_t *s_scan;

// NimBLE host 任务上下文。
static void on_ble_match(const uint8_t *data, int len, void *user) {
    (void)data; (void)len; (void)user;
    if (ui_game_is_active()) return;                  // 游戏中不打断
    if (platform_lvgl_lock(0)) {
        if (!ui_heart_is_active()) ui_heart_open(on_heart_exit, NULL);
        ui_heart_beat();
        platform_lvgl_unlock();
    }
}
static void on_heart_exit(void *user) { (void)user; } // 爱心屏不占按键导航，无需恢复
```

在现有 `PERIPH_BLE` 块（`platform_create_ble_config` 之后）追加：

```c
s_scan = platform_create_ble_scan(&board);
hal_ble_scan_on_match(s_scan, on_ble_match, NULL);
```

## 4. 数据流总结

见 §3 顶部时序图。关键上下文边界：匹配发生在 NimBLE host 任务 → 通过 LVGL 锁把开屏动作交给表现层 → 跳动动画实际在 LVGL 任务的 `lv_timer` 里执行。这与现有 `on_cfg_write` 的跨线程处理一致。

## 5. 测试

- **纯函数单测**：`ble_scan_match()` 在 host_test 中覆盖：正好 4 字节命中、带负载命中、公司 ID 不符、魔数不符、长度不足。
- **接线逻辑（可选）**：host_test 里 mock `hal_ble_scan`，触发 match 回调，断言首次开屏、后续仅跳动、游戏态忽略。
- **硬件联调**：手机 nRF Connect 广播 `FFFF4842` → 观察进爱心屏、连续跳动；停播 → 5 秒后自动退回主界面。

## 6. 注意事项 / 风险

### 与手机 BLE 连接的共存性（multi-role）

设备同时扮演两个角色，二者可并存（ESP32 系列 + NimBLE 明确支持多角色）：

- **外设/被连方**：广播 + GATT 服务端，手机连上来写配置（现有功能，peripheral/broadcaster 角色）。
- **观察者**：只扫描广播、**从不发起连接**（新功能，observer 角色，是中心侧最轻的角色）。

一个是"被别人连"，一个是"只听别人广播"，方向不冲突；手机连接期间设备仍可扫描。现实约束：

1. **单天线单射频，靠时分复用。** 手机连接的周期性"连接事件"与"扫描窗口"由控制器交错排班，二者不能同一瞬间上空口，因此**扫描期间可能漏掉个别广播包**。但本功能"收到就跳、广播每约 100ms 重复"，漏几个无害。
2. **扫描占空比要温和**，给手机连接留时间，避免抢射频导致连接卡顿或漏包过多。采用被动扫描 `window≈30ms / interval≈160ms`（约 19% 占空）。
3. **NimBLE 角色须全开**（实现时核对 sdkconfig）：
   - `CONFIG_BT_NIMBLE_ROLE_PERIPHERAL`、`CONFIG_BT_NIMBLE_ROLE_BROADCASTER`（广播/被连，现已开）。
   - `CONFIG_BT_NIMBLE_ROLE_OBSERVER`（**扫描，必须开**，默认一般已开）；未开则补上。
4. 手机一旦连上，现有代码会停止广播（仅在断开时重启广播）——与扫描无关，扫描全程照常跑。

### 其他

- 广播 + 扫描并存对 CPU 有一定占用；被动扫描 + 上面的温和占空比即可。
- 爱心位图体积很小，直接嵌入源码（C 数组），不占用文件系统/分区。

## 7. YAGNI（明确不做）

- 不做去重/序列号（已定"收到就跳"）。
- 不做按键退出（已定"超时自动退回"）。
- 不引入新的 core service（跳动/超时逻辑放在 `ui_heart` 内部的 `lv_timer`，足够）。
- 不解析广播负载内容（仅用于匹配判定），后续如需"按负载调节跳动强度"再扩展回调已预留 `data/len`。
