# 设计:种子驱动的确定性人形像素小人

> 日期:2026-07-01
> 目标:传入一个随机因子(种子),生成独一无二的人形像素小人;**同一种子必然得到同一个小人**(确定性)。

---

## 1. 目标与验收标准

| 项 | 要求 |
| --- | --- |
| 确定性 | `avatar_generate(seed)` 对同一 `seed` 每次产出**逐格完全相同**的网格 |
| 独特性 | 不同种子产出结构 + 配色都不同的**正常人形**小人(镜像对称,协调) |
| 分层合规 | 生成是纯逻辑,落在 `core/services`,不含 LVGL;渲染留在 `presentation` |
| 可单测 | 生成器能在 `host_test`(linux target)上跑确定性 / 边界测试 |
| 无副作用 | 生成使用**局部 PRNG**,绝不调用全局 `rand()/srand()`,不干扰眨眼等既有随机 |

---

## 2. 架构落点(契合现有六边形分层)

```
app (组装层)
  └─ ui_profile_set_avatar_seed(seed)
        │
presentation/ui_profile.c  ── 调 avatar_generate(seed) → 角色网格 → role→lv_color 调色板 → canvas
        │ (依赖 services)
core/services/avatar.c     ── 纯逻辑:seed → 16×26 "颜色角色"网格 (无 LVGL, 可 PC 单测)
```

- **新增纯逻辑模块 `avatar`**,放 `components/core/services`:
  - `include/services/avatar.h`
  - `src/avatar.c`
  - 加入 `services` 组件的 `SRCS`(`REQUIRES` 不变,仍只依赖 `ports`;`avatar` 甚至不依赖 `ports`,纯 C 标准库)。
- **`presentation/ui_profile.c`** 改为消费 `avatar` 的输出;删除写死的 `AVATAR[]`。

---

## 3. 模块接口:`services/avatar.h`

```c
#pragma once
#include <stdint.h>

#define AVATAR_COLS 16
#define AVATAR_ROWS 26

// 每格的“颜色角色”(赛博朋克半身像)。颜色由 avatar_generate 按种子写入 palette[]。
typedef enum {
    AV_BG = 0,     // 透明/背景(不绘制)
    AV_OUTLINE,    // 描边/嘴/护目框
    AV_SKIN,       // 皮肤
    AV_SHADE,      // 皮肤阴影/鼻
    AV_HAIR,       // 头发
    AV_SHIRT,      // 夹克主色
    AV_SHIRT2,     // 夹克金属/面罩/领口
    AV_EYE,        // 发光眼/HUD 面罩(眨眼时按 AV_OUTLINE 熄灭)
    AV_ACCENT,     // 霓虹点缀(唇/义体/线路/挂绳)
    AV_BACKDROP,   // 可选背景底色
    AV_ROLE_COUNT
} avatar_role_t;

typedef struct {
    uint8_t  cell[AVATAR_ROWS][AVATAR_COLS];  // 每格取值为 avatar_role_t
    uint32_t palette[AV_ROLE_COUNT];          // 每个角色的颜色 0xRRGGBB(种子派生)
} avatar_t;

// 确定性生成:同一 seed → 同一 avatar_t(网格 + 调色板都一致)
void avatar_generate(uint32_t seed, avatar_t *out);
```

**设计要点**
- 网格存**颜色角色枚举**、颜色单独放 `palette[role]`(纯 `uint32_t`,无 LVGL)—— 让"生成什么形状"与"用什么颜色"都留在逻辑层且可确定性单测,表现层只做 `palette[role] → lv_color_hex` 的一次转换。眨眼=把 `AV_EYE` 格临时按 `palette[AV_OUTLINE]` 上色(HUD 瞬灭)。
- PRNG 用 `mulberry32`(种子初始化的 32 位状态),在 `avatar.c` 内部实现;形状随机轴与调色板色相/明度都从该 PRNG 顺序派生。

---

## 4. 生成器逻辑(赛博朋克半身像,16 宽,左右镜像对称)

只画**上半身(头 + 肩胸,无腿)**,只生成左半(列 0..7)镜像到右半 → 天然对称。风格为**赛博朋克/网络客**,并按种子随机**性别(男/女约各半)**。随机轴(全部由内部 mulberry32 PRNG 顺序派生):

| 维度 | 随机内容 |
| --- | --- |
| 性别 | 男 / 女(约 50/50),下列多项按性别取不同集合 |
| 头/脸 | 头半宽 4..5、脸高;女性下颌略尖 |
| 肤色 | 自然暖色域,或约 28% 苍白义体色(偏蓝) |
| 发型 | 男:削边/莫西干/油头/长发/寸头/连帽/头盔;女:长发/波波/双丸子/侧长发/前置马尾/削边。约 66% 霓虹发色,否则暗色 |
| 护目/HUD | 一字发光条 / 双镜护目 / 独眼横条 / 纯发光眼(用 `AV_EYE`,供眨眼熄灭) |
| 面罩 | 约 25%(女)/40%(男)戴呼吸面罩(金属半脸 + 发光呼吸口) |
| 义体 | 太阳穴发光/侧脑金属板、脸颊线路点(各按概率) |
| 上装 | 机能高领夹克(领口金属 + 中央发光缝 + 肩章线路/胸甲板),或女性约 45% 露肩装 |
| 配饰 | 霓虹唇(女)、耳饰(女 60%/男 20%)、颈部 choker(女) |
| 背景 | 约 50% 铺一块霓虹调深色背景(`AV_BACKDROP`) |

**调色板生成**:霓虹主色相 `neon` 从青/绿/品红/紫集合取;发色高概率取霓虹、否则暗色;夹克取低饱和科技色(炭灰/藏青/青/墨绿/钢蓝);肤色自然或苍白义体。`AV_EYE` 与 `AV_ACCENT` 同为 `neon` 色(区别只在眨眼行为)。整数 **HSL→RGB** 在 `avatar.c` 内实现(无浮点)。

> 说明:像素排布已用 gcc ASCII dump 自查、并在浏览器原型中确认过观感;本节定义的是**随机轴与角色语义**,是接口契约的一部分。真机可再按屏幕观感微调。

---

## 5. 表现层改动:`presentation/ui_profile.c`

- **删除** 写死的 `static const char *AVATAR[AVA_ROWS]` 及 `ROW_EYE_BLINK`/喷焰相关常量。
- **上色改为查 `avatar_t.palette`**:每格角色 `role` → `lv_color_hex(P.avatar.palette[role])`(`AV_BG` 跳过)。颜色随种子变化,表现层不再自己定义固定色板。
- profile 运行时状态 `P` 增加 `avatar_t avatar;`(当前小人的角色网格)。
- `draw_grid()` 改为遍历 `P.avatar.cell[r][c]`,查调色板上色(`AV_BG` 跳过)。网格仍是 `16×26`。
- **放大小人**:`AVA_CELL` 由 `3` 调到 `4` → 画布 `48×78` 变为 `64×104`(`AVA_W=64, AVA_H=104`);`ava_buf` 随之增大到 `64*104*2` 字节(静态)。
- **布局微调**:头像变高 26px,会顶到下方身份文字。将 `NETRUNNER`(role)、`FoloToy`(name)、`DATA STALKER`(sub)三行的 `LV_ALIGN_TOP_MID` y 偏移各下移约 20~26px,给头像让位;头像自身的 `TOP_MID` y 偏移可略上移。具体像素以真机 172/240×320 屏观感为准微调,底部等级卡片不动。
- **眨眼**:渲染时若 `blink`,把 `AV_EYE`(发光眼/HUD)的格子按 `AV_OUTLINE` 暗色画 → HUD 瞬灭闪烁。
- **去掉喷焰**:`render_avatar()` 去掉 `flame` 参数与最后两行清空逻辑;`avatar_anim_cb` 只驱动眨眼;**bob(上下浮动)保留不变**。
- **新增 API**(`presentation/ui_profile.h`):
  ```c
  void ui_profile_set_avatar_seed(uint32_t seed);  // 重算 avatar 并重绘画布
  ```

---

## 6. 组装层:`app/src/app.c` + 种子来源

- 在 `ui_profile_create()` 之后(同一把 LVGL 锁内)调用一次:
  `ui_profile_set_avatar_seed(platform_random_seed());`
- **种子来源为"每次开机随机"**:平台层新增 `uint32_t platform_random_seed(void)`(`platform_esp32/src/platform_sys.c`,包 `esp_random()` 硬件 RNG),与外设编译开关无关、始终可用;IDF 依赖仍锁在平台层。业务/表现层不感知种子怎么来的。
- 生成器本身仍是确定性(同种子同小人),只是种子每次开机变 → 每次重启换一个小人。日后若想"固定某台设备的小人",把 `platform_random_seed()` 换成设备唯一 ID / NVS 读值即可,不动生成器与表现层。

---

## 7. 确定性与单元测试(`core/services/test`,host_test/linux 运行)

新增 `test_avatar.c`:

1. **确定性**:`avatar_generate(42, &a)` 与再次 `avatar_generate(42, &b)` → `memcmp(&a,&b)==0`。
2. **独特性**:`avatar_generate(1)` 与 `avatar_generate(2)` → 网格不相等。
3. **值域合法**:任意种子(含 `0`、`0xFFFFFFFF`)每格 `< AV_ROLE_COUNT`,不崩。

接线:把 `test_avatar.c` 加入 `host_test/main/CMakeLists.txt` 的 `SRCS`,以及 `components/core/services/test/CMakeLists.txt`。

---

## 8. 明确不做(YAGNI)

- 不做多套原型(机器人/怪物/史莱姆)——只做正常人形。
- 不做非对称姿态动画、行走帧。
- 不做运行期从网络/存储拉取形象;种子来源为开机硬件随机(`esp_random()`),不做持久化(重启即换新小人)。

---

## 9. 涉及文件清单

| 文件 | 改动 |
| --- | --- |
| `components/core/services/include/services/avatar.h` | 新增 |
| `components/core/services/src/avatar.c` | 新增(生成器 + mulberry32) |
| `components/core/services/CMakeLists.txt` | `SRCS` 加 `src/avatar.c` |
| `components/ui/presentation/src/ui_profile.c` | 删 AVATAR/喷焰,改 role 渲染,加 set_seed |
| `components/ui/presentation/include/presentation/ui_profile.h` | 加 `ui_profile_set_avatar_seed` |
| `components/app/src/app.c` | 调用 `ui_profile_set_avatar_seed(AVATAR_SEED)` |
| `components/core/services/test/test_avatar.c` | 新增单测 |
| `components/core/services/test/CMakeLists.txt` | 加测试源 |
| `host_test/main/CMakeLists.txt` | 加 `test_avatar.c` |
