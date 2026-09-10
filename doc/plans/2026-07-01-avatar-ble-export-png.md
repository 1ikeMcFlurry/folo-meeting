# 头像 BLE 导出 + PC GUI 存 PNG —— 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把设备当前头像序列化成 blob,经一个只读 BLE 特征导出;PC 端 Python GUI 连接读取、显示并可存透明 PNG。

**Architecture:** `services/avatar` 加纯逻辑 `avatar_serialize`(可 gcc 单测);`hal_config` 端口加 `set_export`;`platform/ble_config` 加只读 `AvatarExport` 特征;`app` 持当前种子,开机/换头像时生成 blob 推给 BLE;`tools/avatar_export_gui.py`(bleak+tkinter+Pillow)读取并存 PNG。

**Tech Stack:** C99、ESP-IDF 5.5.2、NimBLE;Python3 + bleak + Pillow + tkinter。

**本仓库特殊约定(务必先读):**
- 非 git 仓库,"提交"用**检查点**代替。
- 纯逻辑 `avatar_serialize` 用 **gcc 独立编译**做 TDD;Unity 测试加入仓库。
- 固件整机用 `idf.py build`(项目根 `/home/cjiio/trae_card`,先 `source "$IDF_PATH/export.sh"`)。
- Python 脚本无法在此环境跑 BLE(无设备),用 `python3 -m py_compile` 做**语法验证**;运行验收在用户机器。
- scratchpad:`/tmp/claude-1000/-home-cjiio-trae-card/82e5d0c8-4331-4280-9c6f-510076c1242a/scratchpad`

---

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `components/core/services/include/services/avatar.h` | 加 `AVATAR_EXPORT_SIZE` + `avatar_serialize` |
| `components/core/services/src/avatar.c` | 实现 `avatar_serialize` |
| `components/core/services/test/test_avatar.c` | 加序列化 Unity 测试 |
| `components/core/ports/include/hal/hal_config.h` | api 加 `set_export` + inline |
| `components/platform/platform_esp32/src/ble_config.c` | 加 `AvatarExport` 只读特征 + `set_export` |
| `components/app/src/app.c` | 持种子/blob;`publish_export`;include avatar.h |
| `tools/avatar_export_gui.py` | PC GUI(新增) |
| `tools/requirements.txt` | 依赖(新增) |

---

## Task 1: `avatar_serialize`(纯逻辑,gcc TDD)

**Files:** Modify `avatar.h`、`avatar.c`;Temp test `.../scratchpad/as_main.c`

- [ ] **Step 1: 头文件加声明**

在 `components/core/services/include/services/avatar.h` 里,`void avatar_generate(...)` 声明之后加:
```c
// 序列化导出:[ver=1][cols][rows][role_count][palette:role_count×4 大端0x00RRGGBB][grid:rows×cols role]
#define AVATAR_EXPORT_SIZE (4 + AV_ROLE_COUNT * 4 + AVATAR_ROWS * AVATAR_COLS)   // 460
// 成功返回写入字节数(==AVATAR_EXPORT_SIZE);buf 太小返回 0。
int avatar_serialize(const avatar_t *a, uint8_t *buf, int max);
```

- [ ] **Step 2: 写临时 gcc 测试 `as_main.c`**

Create `/tmp/claude-1000/-home-cjiio-trae-card/82e5d0c8-4331-4280-9c6f-510076c1242a/scratchpad/as_main.c`:
```c
#include "services/avatar.h"
#include <assert.h>
#include <stdio.h>
int main(void) {
    avatar_t a; avatar_generate(42u, &a);
    unsigned char buf[AVATAR_EXPORT_SIZE];
    int n = avatar_serialize(&a, buf, sizeof buf);
    assert(n == AVATAR_EXPORT_SIZE);
    assert(buf[0] == 1);                 // version
    assert(buf[1] == AVATAR_COLS);       // 16
    assert(buf[2] == AVATAR_ROWS);       // 26
    assert(buf[3] == AV_ROLE_COUNT);     // 10
    // palette[AV_SKIN] 应等于 a.palette[AV_SKIN](大端 0x00RRGGBB)
    int off = 4 + AV_SKIN * 4;
    unsigned int v = ((unsigned)buf[off] << 24) | ((unsigned)buf[off+1] << 16)
                   | ((unsigned)buf[off+2] << 8) | buf[off+3];
    assert(v == a.palette[AV_SKIN]);
    // grid[0][0] 应等于 a.cell[0][0]
    assert(buf[4 + AV_ROLE_COUNT * 4] == a.cell[0][0]);
    // buf 太小返回 0
    assert(avatar_serialize(&a, buf, 10) == 0);
    printf("avatar_serialize tests passed\n");
    return 0;
}
```

- [ ] **Step 3: 运行确认失败**

Run:
```bash
cd /home/cjiio/trae_card && gcc /tmp/claude-1000/-home-cjiio-trae-card/82e5d0c8-4331-4280-9c6f-510076c1242a/scratchpad/as_main.c components/core/services/src/avatar.c -I components/core/services/include -std=c99 -o /tmp/claude-1000/-home-cjiio-trae-card/82e5d0c8-4331-4280-9c6f-510076c1242a/scratchpad/as 2>&1 | head
```
Expected: 链接错误 `undefined reference to 'avatar_serialize'`。

- [ ] **Step 4: 实现 `avatar_serialize`**

在 `components/core/services/src/avatar.c` 末尾追加:
```c
int avatar_serialize(const avatar_t *a, uint8_t *buf, int max) {
    if (max < AVATAR_EXPORT_SIZE) return 0;
    int k = 0;
    buf[k++] = 1;                       // version
    buf[k++] = (uint8_t)AVATAR_COLS;    // 16
    buf[k++] = (uint8_t)AVATAR_ROWS;    // 26
    buf[k++] = (uint8_t)AV_ROLE_COUNT;  // 10
    for (int i = 0; i < AV_ROLE_COUNT; i++) {
        uint32_t v = a->palette[i];     // 0x00RRGGBB
        buf[k++] = (uint8_t)(v >> 24);
        buf[k++] = (uint8_t)(v >> 16);
        buf[k++] = (uint8_t)(v >> 8);
        buf[k++] = (uint8_t)(v);
    }
    for (int r = 0; r < AVATAR_ROWS; r++)
        for (int c = 0; c < AVATAR_COLS; c++)
            buf[k++] = a->cell[r][c];
    return k;   // == AVATAR_EXPORT_SIZE
}
```

- [ ] **Step 5: 运行确认通过**

Run:
```bash
cd /home/cjiio/trae_card && gcc /tmp/claude-1000/-home-cjiio-trae-card/82e5d0c8-4331-4280-9c6f-510076c1242a/scratchpad/as_main.c components/core/services/src/avatar.c -I components/core/services/include -std=c99 -Wall -Wextra -o /tmp/claude-1000/-home-cjiio-trae-card/82e5d0c8-4331-4280-9c6f-510076c1242a/scratchpad/as && /tmp/claude-1000/-home-cjiio-trae-card/82e5d0c8-4331-4280-9c6f-510076c1242a/scratchpad/as
```
Expected: 零警告,输出 `avatar_serialize tests passed`。清理:`rm -f .../scratchpad/as`。

- [ ] **Step 6: 检查点** —— 序列化就位、gcc 测试通过。

---

## Task 2: Unity 测试(工程约定)

**Files:** Modify `components/core/services/test/test_avatar.c`

- [ ] **Step 1: 追加序列化用例**

在 `test_avatar.c` 末尾追加:
```c
TEST_CASE("avatar_serialize header+len", "[avatar]") {
    avatar_t a; avatar_generate(7u, &a);
    uint8_t buf[AVATAR_EXPORT_SIZE];
    TEST_ASSERT_EQUAL_INT(AVATAR_EXPORT_SIZE, avatar_serialize(&a, buf, sizeof buf));
    TEST_ASSERT_EQUAL_UINT8(1, buf[0]);
    TEST_ASSERT_EQUAL_UINT8(AVATAR_COLS, buf[1]);
    TEST_ASSERT_EQUAL_UINT8(AVATAR_ROWS, buf[2]);
    TEST_ASSERT_EQUAL_UINT8(AV_ROLE_COUNT, buf[3]);
    TEST_ASSERT_EQUAL_INT(0, avatar_serialize(&a, buf, 10));   // 太小
}
```
(`test_avatar.c` 已在 services/test 与 host_test 的 CMake 中注册,无需再改 CMake。)

- [ ] **Step 2: 检查点** —— Task 6 整机构建会编译它。

---

## Task 3: 端口 `hal_config` 加 `set_export`

**Files:** Modify `components/core/ports/include/hal/hal_config.h`

- [ ] **Step 1: api 加方法 + inline**

在 `hal_config.h` 顶部(`#pragma once` 后)确保有 `#include <stdint.h>`(若无则加)。把 `hal_config_api_t` 改为:
```c
typedef struct {
    void (*on_write)(hal_config_t *self, cfg_write_cb_t cb, void *user);
    void (*set_export)(hal_config_t *self, const uint8_t *data, int len);
} hal_config_api_t;
```
并在 `hal_config_on_write` 静态内联旁加:
```c
// app 推送“当前头像导出 blob”给 BLE(只读特征返回它)
static inline void hal_config_set_export(hal_config_t *c, const uint8_t *data, int len) {
    if (c->api->set_export) c->api->set_export(c, data, len);
}
```

- [ ] **Step 2: 检查点** —— 声明齐了。

---

## Task 4: 适配器 `ble_config.c` 加只读导出特征

**Files:** Modify `components/platform/platform_esp32/src/ble_config.c`

- [ ] **Step 1: 加导出 UUID + 读回调 + 状态**

在 `g_chr_name[...]` 定义之后加:
```c
static const ble_uuid128_t g_export_uuid = TRAE_UUID128(0x09);   // AvatarExport 只读特征
static const uint8_t *s_export_ptr;
static int            s_export_len;
```
在 `cud_read` 函数之后加导出读回调:
```c
// 导出读:返回 app 推入的当前头像 blob(NimBLE 自动按 MTU 分段完成 read-long)。
static int export_read(uint16_t conn, uint16_t attr, struct ble_gatt_access_ctxt *ctxt, void *arg) {
    (void)conn; (void)attr; (void)arg;
    if (s_export_ptr == NULL || s_export_len <= 0) return 0;
    return os_mbuf_append(ctxt->om, s_export_ptr, s_export_len) == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
}
```

- [ ] **Step 2: 扩容 GATT 数组**

把:
```c
static struct ble_gatt_dsc_def g_dscs[CFG_FIELD_COUNT][2];
static struct ble_gatt_chr_def g_chrs[CFG_FIELD_COUNT + 1];
```
改为(为导出特征各 +1):
```c
static struct ble_gatt_dsc_def g_dscs[CFG_FIELD_COUNT + 1][2];
static struct ble_gatt_chr_def g_chrs[CFG_FIELD_COUNT + 2];
```

- [ ] **Step 3: build_gatt_table 追加导出特征**

把 build_gatt_table 里这行(8 个写特征后的结束哨兵):
```c
    memset(&g_chrs[CFG_FIELD_COUNT], 0, sizeof(g_chrs[CFG_FIELD_COUNT]));  // 结束哨兵
```
替换为(先建导出只读特征,再把哨兵后移一格):
```c
    // 追加只读 AvatarExport 特征(索引 CFG_FIELD_COUNT)
    int ex = CFG_FIELD_COUNT;
    g_dscs[ex][0].uuid = BLE_UUID16_DECLARE(0x2901);
    g_dscs[ex][0].att_flags = BLE_ATT_F_READ;
    g_dscs[ex][0].access_cb = cud_read;
    g_dscs[ex][0].arg = (void *)"AvatarExport";
    memset(&g_dscs[ex][1], 0, sizeof(g_dscs[ex][1]));
    g_chrs[ex].uuid = &g_export_uuid.u;
    g_chrs[ex].access_cb = export_read;
    g_chrs[ex].arg = NULL;
    g_chrs[ex].flags = BLE_GATT_CHR_F_READ;
    g_chrs[ex].descriptors = g_dscs[ex];
    memset(&g_chrs[ex + 1], 0, sizeof(g_chrs[ex + 1]));  // 结束哨兵
```

- [ ] **Step 4: 实现 set_export 并加入 API**

把:
```c
static const hal_config_api_t API = { .on_write = cfg_on_write };
```
替换为:
```c
static void cfg_set_export(hal_config_t *self, const uint8_t *data, int len) {
    (void)self; s_export_ptr = data; s_export_len = len;
}
static const hal_config_api_t API = { .on_write = cfg_on_write, .set_export = cfg_set_export };
```

- [ ] **Step 5: 检查点** —— 编译在 Task 6 验证。

---

## Task 5: 组装层 `app.c` 接线导出

**Files:** Modify `components/app/src/app.c`

- [ ] **Step 1: include avatar.h**

在 app.c 顶部 include 区(`#include "services/ui_model.h"` 之后)加:
```c
#include "services/avatar.h"
```

- [ ] **Step 2: 加导出状态 + publish_export**

在 `#if PERIPH_BLE ... on_cfg_write ... #endif` 这一整块**之后**、`#if PERIPH_LED` 之前,加:
```c
static uint32_t s_avatar_seed;
#if PERIPH_BLE
static hal_config_t *s_cfg;
static uint8_t s_export[AVATAR_EXPORT_SIZE];
static void publish_export(void) {
    if (!s_cfg) return;
    avatar_t t;
    avatar_generate(s_avatar_seed, &t);
    int n = avatar_serialize(&t, s_export, (int)sizeof s_export);
    hal_config_set_export(s_cfg, s_export, n);
}
#else
static void publish_export(void) { }
#endif
```

- [ ] **Step 3: 换头像动作同时刷新导出**

在 `on_dock_action` 里,把 case 0:
```c
        case 0:  // 换头像
            ui_profile_set_avatar_seed(platform_random_seed());
            break;
```
改为:
```c
        case 0:  // 换头像
            s_avatar_seed = platform_random_seed();
            ui_profile_set_avatar_seed(s_avatar_seed);
            publish_export();
            break;
```

- [ ] **Step 4: 开机记录种子 + BLE 建好后推首帧**

在 `app_run` 的 `#if PERIPH_DISPLAY` 块里,把:
```c
        ui_profile_create();
        ui_profile_set_avatar_seed(platform_random_seed());
```
改为:
```c
        ui_profile_create();
        s_avatar_seed = platform_random_seed();
        ui_profile_set_avatar_seed(s_avatar_seed);
```
在 `#if PERIPH_BLE` 块里,把:
```c
    hal_config_t *cfg_src = platform_create_ble_config(&board);
    hal_config_on_write(cfg_src, on_cfg_write, NULL);
```
改为:
```c
    hal_config_t *cfg_src = platform_create_ble_config(&board);
    hal_config_on_write(cfg_src, on_cfg_write, NULL);
    s_cfg = cfg_src;
    publish_export();               // 推送开机头像的导出 blob
```

- [ ] **Step 5: 检查点** —— Task 6 构建验证。

---

## Task 6: 整机构建

- [ ] **Step 1: 全量构建**

Run:
```bash
cd /home/cjiio/trae_card && source "$IDF_PATH/export.sh" >/dev/null 2>&1 && idf.py build 2>&1 | tail -20
```
Expected: `Project build complete`,无 error/warning;`avatar.c`/`ble_config.c`/`app.c` 均编过。

- [ ] **Step 2: 检查点** —— 构建通过即固件侧完成。

---

## Task 7: PC 端 GUI 脚本

**Files:** Create `tools/avatar_export_gui.py`、`tools/requirements.txt`

- [ ] **Step 1: 写 `tools/requirements.txt`**

Create `tools/requirements.txt`:
```
bleak
pillow
```

- [ ] **Step 2: 写 `tools/avatar_export_gui.py`**

Create `tools/avatar_export_gui.py`:
```python
#!/usr/bin/env python3
# TRAE-CARD 头像导出 GUI:连接设备 BLE、读取头像、显示并保存为透明 PNG。
# 用法:pip install -r tools/requirements.txt && python3 tools/avatar_export_gui.py
import asyncio
import threading
import tkinter as tk
from tkinter import filedialog
from PIL import Image, ImageTk
from bleak import BleakScanner, BleakClient

DEVICE_NAME = "TRAE-CARD"
EXPORT_UUID = "54524145-4341-5244-0000-000000000009"
SCALE = 12   # 显示/保存的放大倍数(最近邻)


def decode(blob: bytes) -> Image.Image:
    if len(blob) < 4:
        raise ValueError("数据太短")
    _ver, cols, rows, rc = blob[0], blob[1], blob[2], blob[3]
    off = 4
    pal = []
    for _ in range(rc):
        # 大端 0x00RRGGBB
        r, g, b = blob[off + 1], blob[off + 2], blob[off + 3]
        pal.append((r, g, b))
        off += 4
    if len(blob) < off + rows * cols:
        raise ValueError("数据不完整")
    img = Image.new("RGBA", (cols, rows), (0, 0, 0, 0))
    px = img.load()
    for y in range(rows):
        for x in range(cols):
            role = blob[off]
            off += 1
            if role != 0 and role < len(pal):   # role 0 = 背景,透明
                r, g, b = pal[role]
                px[x, y] = (r, g, b, 255)
    return img


async def ble_read() -> bytes:
    dev = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=10.0)
    if dev is None:
        raise RuntimeError(f"未找到设备 {DEVICE_NAME}")
    async with BleakClient(dev) as client:
        data = await client.read_gatt_char(EXPORT_UUID)
    return bytes(data)


class BleWorker:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro, on_done):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        fut.add_done_callback(on_done)


class App:
    def __init__(self, root):
        self.root = root
        self.worker = BleWorker()
        self.img = None
        root.title("TRAE-CARD 头像导出")
        self.view = tk.Label(root, bg="#0E1512")
        self.view.pack(padx=12, pady=12)
        bar = tk.Frame(root)
        bar.pack(pady=6)
        tk.Button(bar, text="连接并读取", command=self.on_read).pack(side="left", padx=6)
        self.save_btn = tk.Button(bar, text="保存为 PNG", command=self.on_save, state="disabled")
        self.save_btn.pack(side="left", padx=6)
        self.status = tk.Label(root, text="未连接", fg="#888")
        self.status.pack(pady=4)

    def on_read(self):
        self.status.config(text="扫描并读取中…")
        self.worker.submit(ble_read(), self._read_done)

    def _read_done(self, fut):
        try:
            img = decode(fut.result())
        except Exception as e:
            self.root.after(0, lambda e=e: self.status.config(text=f"失败: {e}"))
            return

        def apply():
            self.img = img
            disp = img.resize((img.width * SCALE, img.height * SCALE), Image.NEAREST)
            self.tkimg = ImageTk.PhotoImage(disp)
            self.view.config(image=self.tkimg)
            self.save_btn.config(state="normal")
            self.status.config(text=f"已读取 {img.width}×{img.height}")
        self.root.after(0, apply)

    def on_save(self):
        if self.img is None:
            return
        path = filedialog.asksaveasfilename(defaultextension=".png",
                                            filetypes=[("PNG", "*.png")])
        if not path:
            return
        out = self.img.resize((self.img.width * SCALE, self.img.height * SCALE), Image.NEAREST)
        out.save(path)
        self.status.config(text=f"已保存 {path}")


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
```

- [ ] **Step 3: 语法验证(无 BLE 运行)**

Run:
```bash
cd /home/cjiio/trae_card && python3 -m py_compile tools/avatar_export_gui.py && echo "py_compile OK"
```
Expected: `py_compile OK`(仅校验语法;bleak/pillow 未装也不影响 py_compile)。

- [ ] **Step 4: 最终检查点** —— 固件构建通过 + 脚本语法通过即完成;真机端到端(读出→显示→存 PNG)为手动验收。

---

## 真机/PC 验收(手动)
1. `idf.py -p <串口> flash monitor` 烧录固件。
2. PC:`pip install -r tools/requirements.txt` → `python3 tools/avatar_export_gui.py` → 点"连接并读取" → 显示当前头像 → "保存为 PNG"。
3. 设备上 dock 按"换头像"后,PC 再"连接并读取",得到新头像。

---

## 附:验收对照(spec → 计划)
| spec | 对应 |
| --- | --- |
| blob 格式 + avatar_serialize + 单测 | Task 1 / Task 2 |
| hal_config set_export | Task 3 |
| ble_config 只读 AvatarExport 特征 | Task 4 |
| app 持种子 + 开机/换头像推送 | Task 5 |
| 整机构建 | Task 6 |
| PC GUI(连接/读取/显示/存透明 PNG)| Task 7 |
