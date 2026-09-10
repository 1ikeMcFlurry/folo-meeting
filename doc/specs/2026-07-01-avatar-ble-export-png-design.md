# 设计:头像 BLE 导出 + PC GUI 存 PNG

> 日期:2026-07-01
> 目标:把设备当前生成的像素小人通过 BLE 导出;一个 PC 端 Python GUI 连接设备读取、显示,并可"保存为 PNG"。

---

## 1. 目标与验收标准

| 项 | 要求 |
| --- | --- |
| 导出内容 | 设备**当前**头像(开机随机的,或按 dock「换头像」后的最新那个) |
| 传输 | 单个**可读** BLE 特征返回自描述 blob(≈460 字节,<512 一次读完,无需分片/通知) |
| 分层 | 序列化在 `services/avatar`(纯逻辑,可 PC 单测);BLE 特征在 `platform/ble_config`;app 组装 |
| PC 端 | Python GUI:连 `TRAE-CARD` → 读特征 → 显示头像 → "保存为 PNG"(透明背景) |
| 一致 | 换头像后重读,导出的就是屏上新头像 |

---

## 2. 数据格式:`avatar` 序列化 blob(自描述)

```
偏移  长度  内容
0     1     version = 1
1     1     cols = 16 (AVATAR_COLS)
2     1     rows = 26 (AVATAR_ROWS)
3     1     role_count = 10 (AV_ROLE_COUNT)
4     40    palette:10 个角色 × 4 字节,大端 0x00RRGGBB(字节序 00,R,G,B)
44    416   grid:26×16 role 字节,行主序(role[r][c] 在 44 + r*16 + c)
----------
合计 460 字节 (AVATAR_EXPORT_SIZE)
```

- 传"角色网格 + palette"而非直接 RGB(1248B):更省,且两端都由我们实现。
- PC 端还原:每格颜色 = `palette[role]`;`role==AV_BG(0)` → 透明。

---

## 3. `services/avatar`(新增序列化)

`avatar.h`:
```c
#define AVATAR_EXPORT_SIZE (4 + AV_ROLE_COUNT * 4 + AVATAR_ROWS * AVATAR_COLS)  // 460
// 把 avatar 序列化成自描述 blob(见 spec §2)。成功返回写入字节数,buf 太小返回 0。
int avatar_serialize(const avatar_t *a, uint8_t *buf, int max);
```
`avatar.c` 实现:按 §2 布局写 header + palette(大端)+ grid。纯逻辑,无 IDF。

---

## 4. 端口:`hal/hal_config.h`(加导出推送)

给 `hal_config_api_t` 增加一个方法,让 app 把"当前导出 blob"交给 BLE:
```c
typedef struct {
    void (*on_write)(hal_config_t *self, cfg_write_cb_t cb, void *user);
    void (*set_export)(hal_config_t *self, const uint8_t *data, int len);   // 新增
} hal_config_api_t;

static inline void hal_config_set_export(hal_config_t *c, const uint8_t *data, int len) {
    if (c->api->set_export) c->api->set_export(c, data, len);
}
```

---

## 5. 适配器:`platform/ble_config.c`(加可读特征)

- 新增**可读**特征 `AvatarExport`,UUID `TRAE_UUID128(0x09)`,CUD 描述 "AvatarExport"。
- `set_export(data,len)`:把 app 传入的**指针 + 长度**存入静态变量(app 的 blob 缓冲是静态、地址稳定)。
- 该特征的读回调:`os_mbuf_append(ctxt->om, s_export_ptr, s_export_len)` 返回当前 blob;NimBLE 自动按 MTU 分段完成 read-long。
- GATT 表:在原 8 个可写特征后追加这 1 个只读特征(数组容量相应 +1)。
- 并发:BLE 读(host task)与 app 换头像时重写 blob(LVGL 任务)存在极小的撕裂窗口;因是外观导出,可接受(重读即可)。

---

## 6. 组装层:`app.c`

- 持有当前种子 `static uint32_t s_avatar_seed;`、导出缓冲 `static uint8_t s_export[AVATAR_EXPORT_SIZE];`、BLE 句柄 `static hal_config_t *s_cfg;`。
- 新增 `static void publish_export(void)`(仅 `#if PERIPH_BLE`):`avatar_generate(s_avatar_seed,&t)` → `avatar_serialize(&t,s_export,sizeof)` → `hal_config_set_export(s_cfg, s_export, n)`。
- 种子更新点都走统一流程:
  - 开机:`s_avatar_seed = platform_random_seed(); ui_profile_set_avatar_seed(s_avatar_seed);`(显示);BLE 建好后 `s_cfg = cfg_src; publish_export();`。
  - dock「换头像」(`on_dock_action` case 0):`s_avatar_seed = platform_random_seed(); ui_profile_set_avatar_seed(s_avatar_seed); publish_export();`。
- app 增 `#include "services/avatar.h"`(app 已 REQUIRES services)。

> 说明:`ui_profile_set_avatar_seed(seed)` 与 `avatar_generate(seed,&t)` 都是确定性的,同种子结果一致;显示与导出各生成一次(廉价),避免把 avatar_t 在层间来回传。

---

## 7. PC 端:`tools/avatar_export_gui.py`

- 依赖:`bleak`(BLE)、`pillow`(PNG)、`tkinter`(GUI,Python 自带)。
- 功能:
  - **连接并读取**:扫描/连接名为 `TRAE-CARD` 的设备,读 `AvatarExport` 特征(UUID `54524145-4341-5244-0000-000000000009`)。
  - **解析**:按 §2 拆 header/palette/grid;构建 `Pillow` `RGBA` 图(16×26),每格 = `palette[role]`,`role==0` → `(0,0,0,0)` 透明。
  - **显示**:窗口内以最近邻放大(默认 ×12 → 192×312)显示;可调放大倍数。
  - **保存为 PNG**:文件对话框选路径,`image.save(path)`(保存**原始 16×26 或放大后**,默认按当前放大倍数,透明背景)。
- BLE 异步:bleak 用 asyncio;GUI 用后台线程跑事件循环,读到数据后回主线程刷新 tkinter。
- 附 `tools/requirements.txt`(`bleak`、`pillow`)与顶部用法注释(`pip install -r tools/requirements.txt && python tools/avatar_export_gui.py`)。

---

## 8. 明确不做(YAGNI)

- 不做设备端 PNG 编码(设备只出原始 blob,PNG 在 PC 生成)。
- 不做手机 App;不做分片/通知(单可读特征足够)。
- 不导出多帧/历史;只导当前头像。

---

## 9. 测试

- `avatar_serialize`:gcc + Unity 单测——序列化已知 avatar,校验 header(1/16/26/10)、palette 首个角色字节、grid 某格、总长 460。
- 整机 `idf.py build` 通过(含新特征)。
- PC:手动——连真机读出、GUI 显示、存 PNG 打开正常;换头像后重读得到新图。

---

## 10. 涉及文件

| 文件 | 改动 |
| --- | --- |
| `components/core/services/include/services/avatar.h` | 加 `AVATAR_EXPORT_SIZE` / `avatar_serialize` |
| `components/core/services/src/avatar.c` | 实现 `avatar_serialize` |
| `components/core/services/test/test_avatar.c` | 加序列化单测 |
| `components/core/ports/include/hal/hal_config.h` | api 加 `set_export` + inline |
| `components/platform/platform_esp32/src/ble_config.c` | 加 AvatarExport 只读特征 + `set_export` 实现 |
| `components/app/src/app.c` | 持种子/blob;`publish_export`;include avatar.h |
| `tools/avatar_export_gui.py` | 新增 PC GUI |
| `tools/requirements.txt` | 新增(bleak、pillow) |
