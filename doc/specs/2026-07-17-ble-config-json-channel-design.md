# Spec A：BLE 结构化配置通道（传输层 + JSON 配置 + 头像 seed）

- 日期：2026-07-17
- 目标芯片：ESP32-C3（无 PSRAM，约 150KB 内部 DRAM，BLE/I2S DMA/LVGL 共享）
- 状态：设计定稿，待实现

## 1. 背景与目标

当前 BLE 配置用 8 个可写文本特征（`ble_config.c`），每个特征写入被截断到 63 字节、无分包重组；头像靠 dock 磁贴的"换头像"动作用硬件 RNG 随机切换；配置与头像均为内存单例，掉电回默认值。

本 spec 把配置通道重构为**一条结构化 JSON 通道**：小程序发送 JSON 字符串，含哪些键就改哪些字段（部分更新）；头像改由蓝牙下发的 `avatar_seed` 确定性生成，移除按键/dock 换头像；新增 `token`（积分余额）字段并在界面显示；配置掉电保留（NVS）；卡片通过 notify 回报处理结果。

本通道的分帧机制是后续 Spec B（BLE 传 JPG）、Spec C（BLE 传乐谱）复用的地基。

## 2. 范围

**包含（Spec A）**
- BLE 命令写特征 + notify 状态特征 + 分包重组状态机
- JSON 部分更新（cJSON 解析，字段校验/裁剪）
- 新增 `avatar_seed`（头像走蓝牙）与 `token`（积分余额，界面显示）字段
- NVS 持久化（整个 profile blob）
- 移除 8 个可写特征、dock 换头像动作

**不包含（后续 spec）**
- Spec B：JPG 图片分块传输 + TJpgDec 解码显示（复用本通道 type=0x02）
- Spec C：8bit 通用乐谱格式 + 方波合成播放（复用本通道 type=0x03）

## 3. 架构（沿用现有六边形分层）

| 层 | 改动 |
|---|---|
| `platform_esp32/src/ble_config.c` | 移除 8 个可写文本特征及 `g_chr_uuid/g_chr_name/chr_write`；新增 **命令写特征**（重组）+ **notify 状态特征**；保留只读 AvatarExport |
| `core/services/src/config_json.c`（新增）| cJSON 解析整条 JSON，部分更新 `profile_data_t`，含字段校验/裁剪；返回处理状态码 |
| `core/services/src/profile_ctl.c` | `profile_data_t` 新增 `uint32_t avatar_seed` 与 `int token`；提供序列化/反序列化给 NVS blob |
| `core/ports/include/hal/hal_kv.h`（新增）| key-value 存储端口：`get(key, buf, cap, *out_len)` / `set(key, data, len)` |
| `platform_esp32/src/kv_nvs.c`（新增）| 用 NVS 实现 `hal_kv`；工厂 `platform_create_kv` |
| `core/ports/include/hal/hal_config.h` | `cfg_write_cb_t` 改为消息回调 `cfg_msg_cb_t(type, payload, len, user)`；新增 `notify_status(self, type, status)`；移除 `cfg_field_t` 枚举 |
| `ui/presentation/.../ui_profile.{h,c}` | 新增 `ui_profile_set_token(int token)` 与积分余额显示元素；移除 dock 换头像项 |
| `app/src/app.c` | 开机从 kv 读 profile→填 UI；收到配置消息→`config_json` 部分更新→存 kv→刷新 UI→发 notify；移除 dock `case 0` 换头像 |

数据流：
```
小程序分包写 → ble_config 重组整帧 → app 消息回调
  → config_json 解析 + 部分更新 profile_data_t
  → hal_kv 存 blob → ui_profile 刷新 → ble notify 回报
```

## 4. 传输层协议（A/B/C 共用）

```
应用层帧:  [ver=1 :1] [type :1] [len :2 小端] [payload : len 字节]
           type: 0x01=JSON配置   (后续 0x02=JPG, 0x03=乐谱)

分包:      小程序把整帧切成 ≤20B 的块，依次 write 命令写特征
重组:      卡片状态机先读满 4 字节头，再按 len 累积 payload
上限:      payload ≤ 256 字节（A 阶段接收缓冲固定 256）；超出 → 丢弃 + notify status=4
超时:      相邻写包间隔 > 2s 仍未收齐 → 丢弃并重置（防半包卡死）
notify:    收齐并处理完 → notify [type:1][status:1]
           status: 0=OK  1=帧/超时错  2=JSON解析失败  3=无合法字段  4=过长
```

**特征布局**（沿用现有 `TRAECARD` UUID 基址约定，末字节为字段号）
- 命令写特征：UUID 末字节 `0x10`，`BLE_GATT_CHR_F_WRITE`
- notify 状态特征：UUID 末字节 `0x11`，`BLE_GATT_CHR_F_NOTIFY`
- AvatarExport 只读：保留原 `0x09`，`BLE_GATT_CHR_F_READ`

**重组状态机**（`ble_config.c` 内静态状态，运行于 NimBLE host task）
- 字段：`uint8_t buf[4+256]`、`int have`、`uint16_t need`、`last_write_ms`。
- 每次写：追加字节到 `buf`；`have<4` 时先凑头，凑齐后解析 `ver/type/len`，校验 `ver==1`、`len≤256`（否则 notify 错误并重置）；`have==4+len` 时把 `type/payload` 交给 app 回调，然后重置。
- 超时：进入回调前检查 `now-last_write_ms>2000` → 重置后按新帧起始处理。

## 5. JSON 配置 schema（部分更新：出现哪个键改哪个）

```json
{ "name":"NEO", "role":"NETRUNNER", "subtitle":"DATA STALKER",
  "battery":82, "level":7, "xp":40, "xp_max":100, "token":1580,
  "online":true, "avatar_seed":12345 }
```

| 键 | 类型 | 校验/裁剪 | 落点 |
|---|---|---|---|
| `name` | string | 按缓冲 24 截断 | `profile.name` → `ui_profile_set_name` |
| `role` | string | 按缓冲 20 截断 | `profile.role` → `ui_profile_set_role` |
| `subtitle` | string | 按缓冲 28 截断 | `profile.subtitle` → `ui_profile_set_subtitle` |
| `battery` | number | clamp 0–100 | `profile.battery` → `ui_profile_set_battery` |
| `level` | number | clamp 0–999 | `profile.level` → `ui_profile_set_level` |
| `xp` | number | clamp ≥0 | `profile.xp` → `ui_profile_set_level` |
| `xp_max` | number | clamp ≥1 | `profile.xp_max` → `ui_profile_set_level` |
| `token` | number | clamp ≥0（饱和到 1e9）| `profile.token` → `ui_profile_set_token` **（新增显示）** |
| `online` | bool | — | `profile.online` → `ui_profile_set_online` |
| `avatar_seed` | number | uint32，任意值合法 | `profile.avatar_seed` → `ui_profile_set_avatar_seed` |

规则：
- 部分更新——只处理 JSON 中出现的键，其余保持原值。
- 类型不匹配的键（如 `battery` 传字符串）跳过该键，不影响其它键。
- 至少改动 1 个合法键 → notify status=0；一个都没改（全非法/无已知键）→ status=3；JSON 根不是对象或解析失败 → status=2。

## 6. NVS 持久化

- `profile_ctl` 提供 `profile_serialize(const profile_data_t*, uint8_t *buf, int cap)` / `profile_deserialize(profile_data_t*, const uint8_t*, int len)`，定长/带版本头的 blob（含全部字段 + `avatar_seed` + `token`）。
- 每次成功部分更新后，app 把整个 profile 序列化，经 `hal_kv_set("profile", blob, n)` 写回。
- 开机 `app_run`：`hal_kv_get("profile", ...)` 成功且版本匹配 → `profile_deserialize` 填充；否则 `profile_ctl_init` 出厂默认（`avatar_seed` 用固定默认值，不再开机随机；`token` 默认 0）。
- UI 初值取自加载后的 profile，包括头像 seed。

## 7. 界面：积分余额显示

- `ui_profile` 新增一处"积分余额"元素（label），由 `ui_profile_set_token(int)` 更新。
- 位置：放在角色卡信息区（与 level/xp 同区域，具体排布实现时依现有布局微调），文案形如 `TOKEN 1580` 或 `¢ 1580`（实现时定，保持与现有赛博朋克风格一致）。
- 开机由加载的 profile 初始化；蓝牙下发 `token` 后实时刷新。

## 8. 移除项

- `ble_config.c`：8 个可写特征、`g_chr_uuid[]`、`g_chr_name[]`、`chr_write`、`build_gatt_table` 中对应循环。
- `hal_config.h`：`cfg_field_t` 枚举、旧 `cfg_write_cb_t` 语义（改为消息回调）。
- `app.c`：`on_dock_action` 的 `case 0`（换头像）；开机/换头像对 `platform_random_seed()` 的头像用途调用。
- `ui_profile`：dock "换头像"项（dock 其余项保留，item_id 布局相应调整）。

## 9. 线程/上下文（重要）

- 命令写、重组、notify 均在 **NimBLE host task**。app 消息回调在此上下文运行：解析 JSON 与写 NVS 可在此完成；更新 UI 前必须 `platform_lvgl_lock()`（沿用现有 `on_cfg_write` 模式）。
- notify 从 host task 直接发（`ble_gatts_notify_custom`），需缓存已连接的 `conn_handle` 与 notify 特征 `attr_handle`（在 GATT 注册/连接事件里保存）。
- 未连接时的 notify 静默跳过。

## 10. 内存与依赖注意

- 接收缓冲固定 260 字节（4 头 + 256 payload）静态分配，避免动态碎片；cJSON 解析在栈/堆的临时占用要控制（256B JSON 解析开销可控）。
- 谨记 C3 DRAM 紧张：不引入大静态缓冲（见既往 BLE 被大缓冲饿死的教训）。
- cJSON 随 ESP-IDF 提供；`config_json.c` 依赖 cJSON，故其 host 单测需要 cJSON（host_test 环境已知损坏，最终验证靠固件 build + 硬件联调）。

## 11. 测试策略

- `config_json`（纯逻辑，Unity）：部分更新、缺键保持、非法值裁剪、类型不匹配跳过、`avatar_seed`/`token` 解析、坏 JSON/非对象根、无有效键 → 状态码正确。
- 重组状态机：抽成可测纯函数（喂分片字节序列 → 输出整帧或错误/超时重置），覆盖跨包头、跨包 payload、超长、超时、连续多帧。
- `profile_ctl` 序列化/反序列化往返一致（含新字段）。
- 硬件联调：小程序（或 PC 工具）分包下发 JSON → 界面对应字段与头像刷新、token 显示、掉电重启后保留、notify 状态码正确。

## 12. 环境备注

- 本仓库非 git 仓库，spec 不做 git 提交（记录在此文件）。
- 构建需在已激活的 IDF shell 内 `idf.py build`；本会话无法编译，代码改动的编译/硬件验证由用户执行。
