# 设计:UI 重构 —— 大头像/身份 + 底部横向 dock + 按键导航

> 日期:2026-07-01
> 目标:把档案卡界面重排为「上区大头像 + 身份信息、底部横向 dock 菜单」;头像去动画只静态显示;支持上/下键在 dock 上导航、确定键执行。

---

## 1. 目标与验收标准

| 项 | 要求 |
| --- | --- |
| 布局 | 240×296:状态栏 / 大头像(左) + 身份(右) / 底部横向 dock(4 项) |
| 头像 | **静态**,无 bob/blink 动画,仅生成时渲染一次 |
| 导航 | 上键=上一项、下键=下一项、确定键=执行当前 dock 项 |
| dock 项 | 换头像 / 屏幕亮度 / LED / 在线,各"确定即执行"(循环或切换),磁贴显示当前值 |
| 分层 | 导航/UI 在 presentation;动作由 app 执行(调 platform/services);BLE 改字段仍生效 |
| 稳定 | 去掉每 180ms 全量重渲染(彻底消除此前看门狗根因) |

---

## 2. 布局(LVGL,240×296)

```
┌───────────────────────────────┐
│ 12:00 MON              ● 82%   │  状态栏:时间+星期 / 在线点+电量%
├───────────────────────────────┤
│ ┌───────┐  FoloToy             │  hero 区:
│ │       │  NETRUNNER           │   左:头像画布 96×150(静态)
│ │ 头像  │  DATA STALKER        │   右:名字(大) / 角色 / 副标题 /
│ │       │  LV.9 ▓▓▓▓▓░░         │       等级 + 经验条
│ └───────┘                      │
├───────────────────────────────┤
│   [☻换头像] [☀亮度] [◉LED] [⦿在线] │  dock:4 磁贴,焦点绿色发光上浮
└───────────────────────────────┘
```

- 头像画布沿用 `ava_buf`(64×104 网格 → 之前 cell=4;此处按 hero 区放大到 96×150,即 **AVA_CELL 由 4 调到 6**,画布 `16*6=96 × 26*6=156`,略裁到 150 视觉区;`ava_buf` 增大到 `96*156*2`)。
- dock 磁贴用可聚焦的 `lv_button`,内含图标(符号)+ 文本 + 当前值。

---

## 3. 导航模型(复用现成 LVGL 分组导航)

- 复用已存在但未启用的 `platform_lvgl_attach_buttons(btn, lvdisp)`:它把 **btn0=prev(上)、btn1=next(下)、btn2=enter(确定)** 接成 LVGL 编码器 indev,并创建一个 group 设为默认。
- `ui_profile_create()` 里创建的 4 个 dock 磁贴(可聚焦 `lv_button`)会**自动加入默认 group**;因此调用次序必须是:先 `attach_buttons`(建 group 设默认)→ 再 `ui_profile_create`(建磁贴)。
- 上/下 → 编码器 prev/next 在 group 内移动焦点;确定 → 对焦点磁贴发 `LV_EVENT_CLICKED`。
- 焦点样式:用 `LV_STATE_FOCUSED` 的 style(绿色边+发光+上移+放大);非焦点为暗色磁贴。
- **删除**现有"上/下键换头像"(`on_button`);按键全部交给 LVGL 导航。

> 备注:LVGL 编码器对"不可编辑"控件(普通 button)按下即 CLICKED,不进入编辑模式,符合我们需求。若真机上发现进入编辑模式,给磁贴所在 group 设 `lv_group_set_editing(g,false)` 或确保磁贴无 `LV_OBJ_FLAG_...` 编辑属性(实现阶段真机核对)。

---

## 4. dock 动作与状态

每个磁贴一个 `item_id`;确定触发 presentation 的回调 → app 执行:

| id | 磁贴 | 确定行为 | 当前值显示 | 由谁执行 |
| --- | --- | --- | --- | --- |
| 0 | 换头像 | `ui_profile_set_avatar_seed(platform_random_seed())` | —(图标) | app→presentation |
| 1 | 屏幕亮度 | 循环 30→60→100→30 %,调 `hal_display_set_backlight(disp,%)` | "30/60/100%" | app→platform |
| 2 | LED | 循环 关→绿→青→品红→关,调 led_service | "关/绿/青/品红" | app→services |
| 3 | 在线 | 切换 是/否,调 `ui_profile_set_online(bool)` | "是/否" | app→presentation |

- 状态(亮度档位、LED 档位、在线布尔)由 **app 持有**;每次动作后 app 调 `ui_profile_set_dock_value(id, text)` 更新磁贴上的值文本。
- 初值:亮度 60%(与开机 `set_backlight(80)` 对齐→改为 60 档)、LED 关、在线 是。

---

## 5. 接口改动

### presentation:`ui_profile.h` 新增
```c
// dock 某项被"确定"时回调(item_id: 0换头像 1亮度 2 LED 3在线)
typedef void (*ui_action_cb_t)(int item_id, void *user);
void ui_profile_set_on_action(ui_action_cb_t cb, void *user);

// 更新某 dock 磁贴显示的当前值文本(如 "60%"、"绿"、"是")
void ui_profile_set_dock_value(int item_id, const char *text);
```
`ui_profile.c`:
- 重排 `ui_profile_create()`:状态栏 + hero(头像+身份)+ dock(4 个可聚焦 `lv_button`,各注册 `LV_EVENT_CLICKED` → 调用已注册的 `ui_action_cb_t(item_id)`)。
- **删除** bob `lv_anim` 与 `avatar_anim_cb` blink 定时器及其创建代码;`render_avatar` 不再需要 blink 分支(保留静态渲染 + 批量失效)。保留时间时钟(`clock_cb`,每秒更新状态栏时间)。
- 保留现有 BLE 字段 setter(`set_name/role/subtitle/battery/online/level`),更新到新布局对应控件。

### app:`app.c`
- 次序:创建按钮 → 创建显示 + `lvdisp = platform_lvgl_init(...)`(捕获返回)→ `platform_lvgl_attach_buttons(btn, lvdisp)` → 持锁 `ui_profile_create()` + `ui_profile_set_avatar_seed(...)` + `ui_profile_set_on_action(on_dock_action, NULL)` + 初始化各 dock 值 → 解锁。
- **删除** `on_button`(换头像逻辑)。
- 新增 `on_dock_action(int id, void*)`:switch 执行 §4 动作,持有亮度/LED/在线状态,变更后调 `ui_profile_set_dock_value(...)`;涉及 LVGL 的调用已在 LVGL 事件上下文(确定来自编码器,在 LVGL 任务内),无需再加锁。
- `lvdisp` 用 `struct _lv_display_t *` 持有(platform_factory 已前置声明,app 无需 lvgl.h)。

---

## 6. 明确不做(YAGNI)

- 不做 dock 的二级菜单/编辑模式;每项都是"确定即循环/切换"。
- 不持久化亮度/LED/在线(重启回默认)。
- 头像不做任何动画。

---

## 7. 测试

- 动作状态循环逻辑极简(索引取模),内联在 app,不单列服务;靠整机构建 + 真机验收。
- 整机 `idf.py build` 通过。
- 真机:上/下移动 dock 焦点、确定分别触发换头像/亮度循环/LED 循环/在线切换;BLE 改名字/角色/电量仍更新上区;**长时间运行不再触发 task_wdt**。

---

## 8. 涉及文件

| 文件 | 改动 |
| --- | --- |
| `components/ui/presentation/src/ui_profile.c` | 重写布局 + dock + 去动画 + on_action/dock_value |
| `components/ui/presentation/include/presentation/ui_profile.h` | 加 `ui_action_cb_t` / `ui_profile_set_on_action` / `ui_profile_set_dock_value` |
| `components/app/src/app.c` | 去 on_button;调 attach_buttons;注册并实现 on_dock_action |

> 头像放大(`AVA_CELL` 4→6、`ava_buf` 增大到 `96*156*2`)都在 `ui_profile.c` 内,无需改 board_config。
