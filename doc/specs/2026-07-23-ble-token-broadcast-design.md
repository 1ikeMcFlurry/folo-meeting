# 设计：监听蓝牙广播 → token 余额显示 + 三态提示音

- 日期：2026-07-23
- 目标芯片：ESP32-C3（无 PSRAM；NimBLE observer 已常驻扫描）
- 前置：**`doc/specs/2026-07-23-sn-key-identity-design.md` 必须先实现**（`hal_identity` 端口 + `cardid` 分区 + USB 产线写入）
- 状态：**已实现**（v2 去重机制：签名滑动窗口）

## 1. 背景与目标

场地网关（签到机 / 兑换机 / 活动主控）对全场卡片**一对多广播** token 变动消息。每张卡从广播中筛出属于自己的那条，把广播携带的 **token 余额绝对值**显示到界面上，并按广播指定的操作类型作出反应：**增加成功 / 扣除成功 / 失败**三种播放对应提示音；另有**静默同步**（`op=0x04`，用于周期性对账）只刷新余额、**不响任何提示音**。

由于 BLE 广播的送达不确定，网关会把同一条消息**重复广播多次**；卡片必须保证**同一条消息只处理一次**（只响一次音、只刷一次 UI），且**只处理属于自己的广播**。

### 设备不记账

卡片**不做 token 增减运算**。广播携带的是网关账本里的**余额绝对值**，卡片直接赋值显示：

```c
profile.token = balance;   // 不是 profile.token += delta
```

这是刻意的选择。增量记账下，卡片漏收任意一条广播，本地余额就与网关**永久错位且无法自愈**；绝对值广播是幂等的——漏收只是漏一次刷新，下一条广播自动纠正。

**连带影响**：需求确认阶段曾把"余额不足（本地判定）"列为失败音的触发条件之一。改为绝对值广播后，卡片不再持有可信账本，**这条判定自动废除**——"扣分扣不动"只有网关知道，须由网关下发 `op=0x03` 告知。最终失败音的触发条件是三种：网关下发的业务失败、签名校验失败、NVS 落盘失败。

## 2. 范围

**包含**：token 广播帧格式（26 字节厂商数据，适配 Android 预算）；六级过滤管线（结构 → 目标 → 验签 → 值域 → 去重 → 分发）；HMAC-SHA256 截断 8 字节防伪；**按消息签名的滑动窗口去重抗重放（仅 RAM，不持久化，原生支持多网关）**；三段内置 RTTTL 提示音，覆盖四种 op（加分/扣分/失败/静默同步，其中静默同步不响音）；与现有心跳广播的共存；网关侧参考实现。

**不包含**：卡片主动上报（广播是单向的）；token 增减运算；云端对账；BLE 连接态下的 token 通道（走现有 JSON 通道即可）。多网关并存**已原生支持**（见 §5"去重:按消息签名的滑动窗口"），不再是限制项。

## 3. 前置依赖

| 依赖项 | 用途 | 来源 |
|---|---|---|
| 本机 MAC | 即 `target`，判定广播是否属于自己 | `esp_read_mac(ESP_MAC_WIFI_STA)` 的**原始 6 字节**。与身份设计里的 SN 是同一个值的两种表示：SN 是它的小写十六进制串，`target` 是它的原始字节 |
| `product_key` | 验证广播 MAC 签名 | `hal_identity_hmac()` —— **注意没有 getter**。`product_key` 不出 `identity_nvs.c`，也不通过 BLE 特征下发，否则任何人读一次 BLE 就能伪造全场广播。详见身份设计 §3 |

**未烧录 Key/ProductKey 的板子**：`token_bcast_t.enabled = false`，所有 token 广播一律 `IGNORE`，开机打一条 WARN 日志。不响任何音，不刷 UI。开发板在产线烧录身份分区前就是这个状态，属于预期行为而非故障。

（注意 `target` 不受影响——它直接来自 eFuse MAC，任何板子上都有效。禁用的原因是**没有 `product_key` 就无法验签**。）

## 4. 架构（六边形分层）

| 层 | 文件 | 责任 |
|---|---|---|
| core/services | `token_bcast.c`（新增） | 纯逻辑：解析 → 筛目标 → 验签 → 值域 → 查重（签名滑动窗口）→ 出决策。零平台依赖，可 host 单测 |
| core/services | `ble_match.c`（改动） | `is_heartbeat` → `classify`，区分心跳 / token / 无关 |
| core/services | `tone_presets.h`（新增） | 三段 RTTTL 常量 |
| core/services | `profile_ctl.c/.h`（改动） | `profile_data_t` 不再有 `token_seq`（v2 改动删除），blob 版本升至 v5 |
| core/ports | `hal_identity.h`（已在设计） | 提供 `hmac()` 与 `is_provisioned()`。**`target` 不经过它**，直接 `esp_read_mac` |
| platform | `ble_scan.c`（小改） | 用 `classify` 分类，原始 mfg data 原样上抛 |
| platform | `token_mac_esp.c`（新增，约 20 行） | mbedtls HMAC-SHA256 包一层，注入给服务层 |
| app | `app.c`（改动） | 开机读 self_target（`esp_read_mac`）、注入 mac 函数；`on_ble_match` 分流；决策落地（存 NVS / 刷 UI / 播音） |

### 数据流

```
网关广播（Android，不可连接，厂商数据 26 字节）
   ↓ 射频
ble_scan.c  scan_gap_event()                    ← NimBLE host 任务
   ↓ ble_hs_adv_parse_fields → f.mfg_data[26]
   ↓ ble_match_classify()  →  NONE / HEARTBEAT / TOKEN
app.c  on_ble_match(kind, mfg, len)
   ├ kind==HEARTBEAT → 现有爱心屏逻辑（不变）
   └ kind==TOKEN     → token_bcast_handle(&s_tb, mfg, len, now_ms, &out)
                            ↑ 纯函数，无副作用
   ↓ out = { action, reason, balance, seq }
app.c 落地副作用：
   ├ IGNORE                       → 什么都不做（重复广播、别人的广播、畸形包都走这条）
   ├ FAIL(BAD_MAC)                 → 只播失败音（签名不可信，不碰任何状态）
   └ ADD / SUB / SYNC / FAIL(业务) → profile.token = out.balance   ← 四者都赋值
                                     save_profile()                 ← 一次 NVS 写
                                     tone = 按 action 选定（SYNC → 不响任何音）
                                     save_profile 失败 → tone 一律改成失败音
                                     （这是唯一能让 SYNC 出声的情况，见下表）
                                     ui_profile_set_token()          ← try-lock LVGL
                                     播放 tone（非 NULL 时）
```

**四类结果的落地路径不同**，容易混淆，这里说清：

| 来源 | 赋值 balance | 记入去重窗口 | 落盘成功时播放的音 |
|---|---|---|---|
| `op=0x01` / `0x02`（ADD / SUB） | ✅ | ✅ | 加分音 / 扣分音 |
| `op=0x03`（网关下发的业务失败） | ✅ | ✅ | 失败音 |
| `op=0x04`（静默同步 SYNC） | ✅ | ✅ | **不响音** |
| 签名校验失败（`BAD_MAC`） | ❌ | ❌ | 失败音（3 秒限速） |

**落盘失败会覆盖上表最后一列**：无论原本该响哪种音（包括 SYNC 的"不响音"），只要 `save_profile()` 失败，一律改播失败音（见 §8）——SYNC 因此出声，恰好是在报"余额刷新了，但没能存住"。

区别的理由：`op=0x01/0x02/0x03/0x04` 都是**网关签过名的可信消息**，携带的 balance 和签名都是权威值，验签通过后一律记入去重窗口；`BAD_MAC` 的包**整体不可信**，它的签名和 balance 都是攻击者可控的，一个字节都不能采信——尤其是签名，**未验签的签名绝不能进入去重窗口**，否则攻击者随便发几条伪造包就能把窗口刷满、把真实消息的记录挤出去（详见 §6）。

### LVGL 锁拿不到时怎么办

沿用现有 `on_ble_match` 的做法：`platform_lvgl_lock(0)` 是**非阻塞 try**，拿不到就跳过 UI 刷新，**其余步骤照常**（NVS 已写、提示音已响）。

不重试、不阻塞。这在本设计里是安全的，因为 **balance 是绝对值**——屏幕短暂显示旧值，下一条广播或任何一次 UI 刷新都会带出正确值，不会像增量记账那样越差越远。绝不能为了刷 UI 而在 NimBLE host 任务里阻塞等锁。

### 为什么 `token_bcast.c` 不碰 NVS 和 LVGL

它是**纯决策函数**：吃字节流 + 当前状态，吐一个决策结构体。去重窗口（`seen[]`）就活在 `token_bcast_t` 结构体内部，**仅 RAM，不落盘**——调用方唯一需要持久化的只有 `profile.token`（见 §8）。这样：

- host 上能覆盖全部分支：畸形长度、魔数错、版本错、target 不匹配、MAC 错、**重复签名 / 窗口轮转后旧签名再次通过 / 坏签名不进窗口**、未知 op
- **"验签必须排在去重之前"这类顺序性安全约束能被测试锁死**——将来谁重构调换了顺序，测试立刻变红（这条约束的理由见 §6）
- 副作用集中在 `app.c` 一处，"NVS 写失败时不能已经响过成功音"这类时序问题只有一个地方要审

## 5. 帧格式（26 字节厂商数据）

### 发送方是 Android，字节预算由它决定

网关（pad）是 **Android 设备**。Android 只能通过 `addManufacturerData(int, byte[])` 设置数据，**无法设置原始 31 字节负载**，各层开销固定：

```
BLE legacy 广播总容量                        31
- Flags AD（可连接广播时系统强制添加）        3   →  28
- Manufacturer AD 头（长度 + 类型 0xFF）      2   →  26
- 公司 ID（由 manufacturerId 参数提供）       2   →  24
────────────────────────────────────────────────────
  addManufacturerData 的 byte[] 上限          24 字节
  卡片侧看到的 f.mfg_data                     26 字节（含公司 ID）
```

**早期草案按"可设置原始 31 字节负载"设计，厂商数据 29 字节——在 Android 上超 3 字节，发不出去。** 现按 24 字节 byte[] 重新设计。

```
卡片侧 f.mfg_data（26 字节）
mfg[0..1]   FF FF        公司 ID(由 manufacturerId=0xFFFF 提供,不占 Android 的 byte[])
─────────── 以下 24 字节即 addManufacturerData 的 byte[] ───────────
mfg[2..3]   'H' 'B'      魔数 0x48 0x42
mfg[4]      hdr = 0x12   高 4 位 = ver(1),低 4 位 = type(2=token 消息)
mfg[5]      op           0x01=加分  0x02=扣分  0x03=失败  0x04=静默同步(不响音)
mfg[6..11]  target   6B  目标卡的 WIFI_STA MAC 原始字节,mac[0] 在最左
mfg[12..14] seq      3B  uint24 小端。**仅作 nonce**,卡片不校验其数值
mfg[15..17] balance  3B  uint24 小端,token 余额绝对值,⚠ 有效范围 0..999999
mfg[18..25] mac      8B  HMAC-SHA256(product_key, mfg[0..17]) 前 8 字节
```

### 3 个字节从哪里省出来的

| 字段 | 原 | 现 | 理由 |
|---|---|---|---|
| `ver` + `type` | 2B | **1B**（合并为 `hdr`） | 各自只需 4 位，本就浪费 |
| `seq` | 4B | **3B** | 上限 16,777,215。按每卡每天 100 笔算可用 459 年 |
| `balance` | 4B | **3B** | 上限 16,777,215，远超协议自身的 999999 限制 |
| `target` | 6B | **6B 不动** | 缩短会重新引入碰撞（别人的加分响到你卡上） |
| `mac` | 8B | **8B 不动** | 缩到 5B 会把伪造难度从 2⁶⁴ 降到 2⁴⁰，GPU 可暴力破解 |

砍的是冗余位宽，**没有动任何一个安全相关字段**。这是刻意的取舍顺序：宁可牺牲计数器和余额的理论上限（都还有几个数量级的余量），也不削弱防伪。

- MAC 覆盖 `mfg[0..17]` 共 18 字节，**含公司 ID、魔数和 hdr**——防止签名被跨协议复用。
  注意公司 ID 那 2 字节不在 Android 的 `byte[]` 里，网关计算签名时要自己补上。
- 全部多字节整数用**小端**，与现有 BLE 协议（`ble-protocol-spec.md` 的 `total_len`）一致。
- `op=0x03`（失败）时 `balance` 仍然填当前余额，卡片**照常赋值显示**。网关是权威账本，它给的值总是对的；失败通常意味着余额没变（赋值幂等无害），万一确实变了（如部分扣除），显示正确值比保留旧值更好。

### 去重:按消息签名的滑动窗口(v2 改动)

**初版设计是"seq 单调递增 + 持久化 last_seq",那在多网关场景下是错的。**
各 pad 的计数器互不相通:pad A 发到 seq=500 之后,pad B 从 seq=1 开始发,
会被卡片全部当成重复包**静默丢弃**,且现场毫无线索。而 pad 之间无法同步计数器。

改为:卡片记住**最近 `TOKEN_SEEN_MAX`(16) 条消息的 8 字节签名**(环形缓冲,仅 RAM)。

| | 结果 |
|---|---|
| 网关重发 30 次 | 内容相同 → 签名相同 → 只处理第一条 ✅ |
| 多台 pad 交叉发送 | 消息不同 → 签名不同 → 都能收到 ✅ **零协调** |
| pad 计数器回退/重启归零 | 无影响 ✅ |
| 伪造超大 seq 顶死计数器 | **该攻击不再存在** ✅ |
| 隔久了的旧消息被重放 | 窗口轮转后可再次生效 ⚠️ 见下 |

**`seq` 降级为 nonce**:它唯一的作用是让"内容相同但确实是两次不同操作"的消息
产生不同签名。数值本身卡片不看,0 也合法。pad 侧照旧本地递增即可,**不用改代码**。

**验签必须排在去重之前**:签名本身就是去重的键,未经验证的签名不能进窗口,
否则攻击者随便发几条伪造包就能把窗口刷满、把真实消息挤出去。

### 接受的代价:重放窗口

旧设计的"绝对单调"能挡住任意久远的重放,新设计只能挡住最近 16 条。
**这个降级是刻意的**,理由是卡片**只是显示端,账本在 pad/后台**:
重放一条旧消息最多让某人的工牌显示一个过期数字,下次同步即被纠正,
**没有人能因此多花一分钱**。用这点风险换掉"必须跨设备协调计数器"的部署约束,值得。

窗口只放 RAM 不落盘:重启后余额本来就从 NVS 读回正确值,此时被重放最多多响一声,
不值得为它每条消息多写 128 字节 flash。

### ⚠️ `balance` 上限 999999

`profile_ctl.h` 里 `token` 是 `int`，`ui_profile_set_token()` 会把负数钳成 0。若广播里塞一个 `0xFFFFFF`，赋给 `int` 是负数，屏幕会显示 **TOKEN 0**——"余额爆表反而显示 0"的静默错误。

**协议限定 `balance ≤ 999999`**（UI 最多 6 位数），超出一律 `IGNORE` 并打 WARN。选择在协议层拦而不是在赋值处钳位：钳位会把网关的 bug 变成"显示了一个看似合理但错误的数"，拦截并告警才能当场暴露问题。

`profile.token` 保持 `int` 不动，不改动 `profile_ctl` / `config_json` / `ui_profile` 三处类型。

### 为什么 `target` 和 `mac` 一个字节都不能省

字节预算紧张时，最容易被拿来开刀的恰恰是这两个字段。它们必须保住：

| 字段 | 若缩短 | 后果 |
|---|---|---|
| `target` 6B | 4B（哈希截断） | 重新引入碰撞——别人的加分会响到你卡上。用原始 MAC 且保留全 6 字节时，**碰撞根本不存在**（MAC 全球唯一） |
| `mac` 8B | 5B | 伪造一条合法签名的离线爆破难度从 2⁶⁴ 降到 2⁴⁰，GPU 几小时可撞出。攻击者**不需要拿到 product_key** 就能伪造 |

配合"全厂共享密钥"这一防伪级别，8 字节签名把攻击面收敛为"物理 dump 一张卡拿到 product_key"这一条路。缩到 5 字节就等于白送一条爆破通道，防伪设计整体失效。

所以省字节只能从 `ver`/`type`/`seq`/`balance` 这些**位宽冗余**的字段下手（见上一节的表），它们即便砍掉一半仍有几个数量级的余量。

### 与现有心跳广播的共存 ⚠️

现有心跳判定（`ble_match.c`）是 `len >= 4 && 前 4 字节 == FF FF 48 42`，**多余负载忽略**。token 广播前 4 字节完全相同，若不改动，**每条 token 广播都会被误判成心跳、触发爱心屏跳动**。

改为分类函数，靠 `mfg[4]`（hdr = ver<<4 | type）区分：

```c
typedef enum { BLE_MATCH_NONE = 0, BLE_MATCH_HEARTBEAT, BLE_MATCH_TOKEN } ble_match_kind_t;

ble_match_kind_t ble_match_classify(const uint8_t *mfg, int len);
//   前 4 字节不匹配 ...................... NONE
//   len >= 5 且 mfg[4] == 0x12 ........... TOKEN
//   否则 ................................. HEARTBEAT
```

判定顺序保证向后兼容：nRF Connect 手动发的 4 字节 `FFFF4842` 仍然是心跳；将来若要加第三类消息，占用一个新的 type 值即可。`ble_match_is_heartbeat()` 保留为 `classify() == HEARTBEAT` 的包装，现有单测不改。

**对心跳发送方补一条约定**：心跳设计文档（`2026-07-09-ble-heartbeat-screen-design.md` §2）写的是"多余负载忽略"，即发送方可以在 `FFFF4842` 后带任意字节。这与新的 type 字段冲突——若某个心跳发送方的第 5 字节恰好是 `0x12`，它就会被误判成 token 广播（然后死在目标筛选里，表现为"心跳不跳了"）。

**约定：心跳广播只发 4 字节 `FF FF 48 42`，不带负载。** 若确实需要带负载，第 5 字节禁止使用 `0x12`。这条要同步回心跳文档。

## 6. 六级过滤管线

```
① 结构校验   len == 26 && 魔数匹配 && mfg[4] == 0x12（ver=1 且 type=2）
                  ↓ 不过 → IGNORE（静默，仅 DEBUG 日志）
② 目标筛选   memcmp(mfg[6..11], self_target, 6) == 0
                  ↓ 不过 → IGNORE（静默，无日志——场地里全是别人的广播）
③ 签名校验   mac_fn(mfg[0..17], 18) 前 8 字节 == mfg[18..25]
                  ↓ 不过 → FAIL（响失败音，3 秒限速；不碰任何状态）
④ 值域校验   balance <= 999999
                  ↓ 不过 → IGNORE + **WARN 日志**（此时消息已验签，是网关 bug）
⑤ 去重       该消息签名不在最近 16 条里
                  ↓ 不过 → IGNORE（静默，仅 DEBUG 日志）
⑥ 分发       op → ADD / SUB / FAIL / SYNC；同时把签名记进窗口
                  ↓ op 未知 → IGNORE + WARN（同样是已验签的网关 bug）
```

### 值域校验为什么放在验签之后

放在①（结构校验）里看着更自然，但那样**场地里别人的广播也会触发 WARN 日志**——一台网关配错，全场几百张卡一起刷屏。

放在③之后，能进到这一步的包已经证明是**发给我的、且网关签过名的**。此时 `balance` 超限只可能是网关的真实 bug，值得用 WARN 喊出来（`seq` 本身没有"超限"这回事——它只是 nonce，任何 uint24 值包括 `0` 都合法，见 §5）。这样日志的信噪比才有意义：**静默的是常态流量，出声的是真问题。**

### 顺序不可调换的两条硬约束

**② 必须排在 ③ 之前。** 否则场地里每一条别人的广播（签名对不上本机 product_key 之外的任何篡改、或干脆是噪声）都会让卡片叫一声。目标筛选先把 99.99% 的无关流量静默挡掉，只有"声称是发给我的"才值得报警。

**③ 必须排在 ⑤ 之前。** 签名本身就是去重窗口的键：若先去重再验签，攻击者随手发几条内容各异、签名从未验证过的伪造包，就能把它们的（无意义）签名塞进只有 16 条容量的窗口，把真实消息的记录挤出去——真正的合法广播反而可能因为窗口被垃圾占满而重复响铃或被误挡。必须先证明消息可信，才允许它的签名进窗口。

这也是为什么签名失败（`BAD_MAC`）的包**只响音、一个字节都不采信**：它的签名和 balance 全在攻击者控制之下，绝不能记入去重窗口。

### 重复广播必须完全静默

**签名已在去重窗口里**的包：**不响音、不刷 UI、不写 NVS、不打 WARN 日志**（DEBUG 级即可）。网关为了送达会把同一条消息重发几十次，这是设计预期的正常流量，不是异常。

## 7. `token_bcast` 服务接口

**以下照 `components/core/services/include/services/token_bcast.h` 原样摘录**：

```c
// core/services/include/services/token_bcast.h

#define TOKEN_MFG_LEN         26      // 厂商数据必须恰好这么长
#define TOKEN_HDR             0x12    // mfg[4]:高4位=ver(1),低4位=type(2)
#define TOKEN_BALANCE_MAX     999999u
#define TOKEN_BAD_MAC_RATE_MS 3000u   // 签名失败音的最小间隔
#define TOKEN_MAC_LEN         8       // 签名长度,同时也是去重的键
#define TOKEN_SEEN_MAX        16      // 去重窗口:记住最近多少条消息签名

typedef enum {
    TOKEN_ACT_IGNORE = 0,   // 静默:不是给我的 / 重复 / 畸形
    TOKEN_ACT_ADD,          // 加分成功
    TOKEN_ACT_SUB,          // 扣分成功
    TOKEN_ACT_FAIL,         // 失败(网关业务失败,或签名校验失败)
    TOKEN_ACT_SYNC,         // 静默同步:刷新余额但**不响提示音**
} token_action_t;

// 仅用于日志与测试断言,不影响铃声选择
typedef enum {
    TOKEN_R_OK = 0,
    TOKEN_R_DISABLED,           // 未烧录 product_key,功能禁用
    TOKEN_R_BAD_LEN,
    TOKEN_R_BAD_MAGIC,          // 魔数错,或 hdr 低 4 位不是类型 2
    TOKEN_R_BAD_VER,            // hdr 高 4 位不是版本 1
    TOKEN_R_NOT_MINE,
    TOKEN_R_BAD_MAC,
    TOKEN_R_MAC_RATE_LIMITED,
    TOKEN_R_BAD_BALANCE,        // balance > 999999
    TOKEN_R_REPLAY,             // 签名已在去重窗口里 —— 网关重发的那些死在这里
    TOKEN_R_BAD_OP,
} token_reason_t;

// 注入的 MAC 计算:对 msg[0..len) 算 HMAC-SHA256(product_key,·),写前 8 字节到 out。
// 成功返回 0。ESP32 侧注入 mbedtls 实现;host 测试注入确定性假实现。
typedef int (*token_mac_fn)(const uint8_t *msg, int len, uint8_t out[8], void *user);

typedef struct {
    uint8_t      self_target[6];  // 本机 MAC,开机读一次
    bool         enabled;         // 未烧录 product_key → false,全部 IGNORE
    token_mac_fn mac;
    void        *mac_user;
    uint32_t     bad_mac_ms;      // 上次响签名失败音的时刻
    bool         bad_mac_seen;    // 是否已响过至少一次(见 .c 里的注释)

    // ---- 去重窗口(环形缓冲,仅 RAM)----
    // 键是消息的 8 字节签名:同一条消息重发多少次签名都一样 → 只处理一次;
    // 不同 pad 发的是不同消息 → 签名不同 → 互不干扰,**pad 之间无需任何协调**。
    uint8_t      seen[TOKEN_SEEN_MAX][TOKEN_MAC_LEN];
    uint8_t      seen_n;          // 已填条数(0..TOKEN_SEEN_MAX)
    uint8_t      seen_next;       // 下一个写入位置
} token_bcast_t;

typedef struct {
    token_action_t action;
    token_reason_t reason;
    uint32_t       balance;   // ADD/SUB/SYNC/FAIL(业务) 有效;FAIL(BAD_MAC) 恒为 0
    uint32_t       seq;       // 同上。仅供日志,卡片不依赖它的数值
} token_result_t;

// 初始化。enabled 置 true;调用方随后可按"是否已烧录 product_key"改写它。
void token_bcast_init(token_bcast_t *t, const uint8_t self_target[6],
                      token_mac_fn mac, void *mac_user);

// 处理一条 token 广播。now_ms 用于签名失败限速。out 必填,函数内部先清零。
// 全部校验通过后把该消息的签名记进去重窗口。
void token_bcast_handle(token_bcast_t *t, const uint8_t *mfg, int len,
                        uint32_t now_ms, token_result_t *out);
```

对比 v1：`token_bcast_init` 不再接收 `last_seq` 参数（没有可传的了）；`token_bcast_t` 里的 `uint32_t last_seq` 换成了三个去重窗口字段 `seen[]` / `seen_n` / `seen_next`；`token_reason_t` 删除了 `TOKEN_R_BAD_SEQ`（`seq` 降级为 nonce 后不再有"无效值"这回事）；`token_action_t` 新增 `TOKEN_ACT_SYNC`。

### 签名失败限速

签名校验失败会响音（这是需求确认的选择），但它**不走去重窗口**——伪造包的签名本身就不可信，不能把它记进 `seen[]`（见 §6 的顺序约束）。若不限速，一个持续发送坏包的干扰源会让卡片不停叫。

规则：`TOKEN_ACT_FAIL` 且原因是 `BAD_MAC` 时，距 `bad_mac_ms` 不足 **3000ms** 则降级为 `IGNORE`（reason 记 `MAC_RATE_LIMITED`）。网关下发的业务失败（`op=0x03`）签名已验证，正常记入去重窗口，不受限速影响。

`now_ms` 作为参数传入而非内部取时钟，是为了让限速逻辑在 host 上可测（喂固定时间戳即可断言）。

**⚠️ 比较必须写成无符号减法**：

```c
if ((uint32_t)(now_ms - t->bad_mac_ms) < 3000)   // ✅ 跨回绕正确
if (now_ms < t->bad_mac_ms + 3000)               // ❌ 回绕后永久成立
```

`uint32_t` 毫秒在 **49.7 天**后回绕。第二种写法在回绕点会让 `bad_mac_ms + 3000` 溢出成一个极小值，条件永久为真——**失败音从此再也不响**，而且要等设备连续运行 50 天才会出现，几乎不可能在测试中撞到。这类"看起来对、只在特定时刻炸"的写法必须在 spec 里点名。

**⚠️ 需要一个 `bad_mac_seen` 标志，不能只靠 `bad_mac_ms == 0` 判断"从未响过"**：

`token_bcast_init` 把 `bad_mac_ms` 清零，而开机后几秒内 `now_ms` 本身就是个小数值。若只写 `(now_ms - bad_mac_ms) < 3000`，开机 3 秒内的**第一条**坏签名包会被误判成"距上次不足 3 秒"而被静默吃掉——限速把它该响的第一声也吞了。加一个 `bad_mac_seen` 布尔，首次一律放行。

### 去重窗口与 NVS 写入是两件独立的事

`token_bcast_handle` 内部在校验全部通过后立即把签名记入 `seen[]`——这个动作是**纯内存操作**，与调用方随后是否成功把 `profile.token` 写进 NVS 完全无关。这跟 v1 设计里的耦合关系不一样，值得对比着讲清楚：

- **v1（已废弃）**：`last_seq` 若不落盘，重启后退回旧值，之前已经"消费"过的那条广播就能被重放——`last_seq` 的持久化不是可选优化，是安全需求，必须和余额同一次 NVS 写一起完成，写失败还要接受"该条广播下次重启可被重放一次"的窗口。
- **v2（当前）**：去重窗口本来就只活在 RAM 里、重启即清空（见 §5"接受的代价：重放窗口"），压根不追求跨重启的抗重放能力。`profile.token` 落盘失败只影响余额显示能不能扛过重启，**不影响本次运行期间的去重是否生效**——同一条消息重放依然会被窗口挡住，只是重启后窗口清空了，那另当别论。

这意味着**不再存在"先落盘后推进"还是"先推进后落盘"的顺序纠结**：没有 seq 要推进，去重窗口在校验通过的一瞬间就已经更新完毕，是一个自然、不可能出错的时序。

唯一保留下来的顺序约束是 §8 里的**"先写 NVS 成功、再播成功音"**——这条约束保护的是"用户听到的提示音要不要相信余额已经存住"，与去重窗口无关，理由不变。

## 8. 状态持久化

`token` 余额沿用现有 profile blob 落盘；**去重窗口不落盘**（原因见 §5"接受的代价：重放窗口"、§7"去重窗口与 NVS 写入是两件独立的事"），因此 blob 里不再需要任何与去重相关的字段：

```c
typedef struct {
    /* ... 现有字段不变 ... */
    int      token;        // 余额（现有字段，语义从"本地记账"改为"网关广播的快照"）
    uint8_t  img_mode;
} profile_data_t;

#define PROFILE_BLOB_VER  5   // v5：删除 v4 曾新增的 token_seq(去重改为签名窗口,不再持久化)
```

**版本变迁**：v1 去重设计上线时，`profile_data_t` 曾新增 `uint32_t token_seq` 字段用于持久化 `last_seq`，blob 版本随之升到 v4。v2 把去重机制改成签名滑动窗口后，去重状态整体搬进了 `token_bcast_t`（仅 RAM），`token_seq` 字段因此**删除**，版本再升到 v5。

**版本升级影响**：`profile_deserialize` 对版本不符的记录返回 -1，`app.c` 回落到出厂默认。因此每次升版本号（v3→v4、v4→v5 各发生过一次），**已烧录设备上次保存的 profile（名字/角色/头像 seed/余额）都会被重置为出厂默认一次**。开发阶段可接受；若已有量产设备在外，需要改成按旧版本号读入后做字段迁移的兼容分支。本设计按开发阶段处理，不写兼容分支。

**写入频率**：token 广播由人工操作触发（签到、兑换），频率是分钟级，profile blob 约 200 字节。NVS 磨损不构成问题。

### 落盘失败 → 改播失败音

`save_profile()` 返回失败时，`app.c` **不播原本该响的音（加分 / 扣分 / SYNC 的静默），改播失败音**，并打 WARN 日志。UI 仍然刷新（内存里的值是对的，用户看到的余额准确），只是这个值撑不过重启。

顺序上必须**先写 NVS，成功后才播成功音**。反过来会出现"响了加分音但值没存住"的情况——用户听到成功、重启后余额退回，比直接听到失败音更难排查。

## 9. 提示音

三段 RTTTL 常量编进固件（`core/services/include/services/tone_presets.h`），零存储成本，开机即可用，不会被误配置成静音：

```c
#define TONE_TOKEN_ADD   "add:d=16,o=6,b=200:c,e,g,c7"    // 上行琶音，明快
#define TONE_TOKEN_SUB   "sub:d=16,o=6,b=200:c7,g,e,c"    // 下行琶音，与加分成镜像
#define TONE_TOKEN_FAIL  "err:d=8,o=4,b=120:a#,p,a#"      // 低沉双短音
```

三段均 < 1 秒。加分 / 扣分互为倒序，用户不用记规则也能凭方向感分辨。

### 播放调度

复用现有 `music_player_task` 通路，**不新起任务**：

```c
// 运行在 NimBLE host 任务：只做复制 + 置标志 + 给信号量，不阻塞
memcpy(s_score_play_buf, tone, len);
s_score_play_len = len;
s_music_interrupt = 1;          // 抢占当前正在播的曲子
xSemaphoreGive(s_music_sig);
```

这与现有 `on_cfg_message` 处理 RTTTL 下发的做法完全一致。**绝不能在 NimBLE host 任务里直接合成播放**——`audio_service_play` 阻塞写 I2S，会卡死 BLE 协议栈。

**抢占语义**：提示音抢占正在播放的乐谱（`s_music_interrupt`）。短促提示音优先于背景音乐是合理的；反过来让提示音排队等一首歌播完则完全失去意义。

**连续多条广播**：若两条不同 seq 的广播在 1 秒内到达（网关连续操作），后一条会抢占前一条的提示音。这是预期行为，不做排队。

## 10. 网关侧（Android pad）

**发给对方团队的接口文档：`doc/ble-broadcast-protocol-for-gateway.md`**（不含内部实现细节，含测试向量）。本节只记与卡片侧强相关的约束。

网关必须关掉三个开关，否则系统自动追加的内容会挤爆 31 字节：

| 项 | 值 |
|---|---|
| `AdvertiseSettings.setConnectable` | `false` |
| `AdvertiseData.setIncludeDeviceName` | `false` |
| `AdvertiseData.setIncludeTxPowerLevel` | `false` |
| `addManufacturerData` 的 `manufacturerId` | `0xFFFF` |

### 测试向量（卡片侧单测可直接复用）

用公开假密钥 `"TESTPK0123456789"`，SN `f0f5bd84a86c`，`op=0x01`，`seq=1`，`balance=1234`：

```
签名输入  mfg[0..17] 共 18 字节
  ff ff 48 42 12 01 f0 f5 bd 84 a8 6c 01 00 00 d2 04 00
签名      HMAC-SHA256(key, 上面 18 字节)[0..7]
  19 3a 4e 54 c4 dc 27 eb
完整 mfg_data（26 字节）
  ff ff 48 42 12 01 f0 f5 bd 84 a8 6c 01 00 00 d2 04 00 19 3a 4e 54 c4 dc 27 eb
```

这组数据同时锁死了四件最易错的事：字节序、字段偏移、签名范围、密钥编码。

### 需要广播多久

当前固件为满足快速档案广播的 5 秒目标，扫描占空比已调整为 **约 112ms 窗口 / 160ms 间隔 ≈ 70%**。网关按 100ms 间隔广播；下表仅为独立命中理论值：

| 持续时长 | 广播次数 | 全部漏收概率 |
|---|---|---|
| 1 秒 | 10 | ≈ 0.0006% |
| 2 秒 | 20 | ≈ 3×10⁻¹¹ |
| **3 秒** | **30** | **≈ 2×10⁻¹⁶** |

**建议网关每条 v1 消息持续广播 3 秒**。只要网关重发时**内容（含 `seq`）原样不变**，卡片侧靠签名去重，重复 30 次也只处理一次（见 §5"去重:按消息签名的滑动窗口"）——这也是为什么"重发同一条消息"要求 `seq` 不变：换一个 `seq` 会产生不同签名，等于变成了一条新消息。理论概率不能代替目标机型和现场射频验收。

### target 与 product_key 的获取

网关需要两样东西，都来自产线：

1. **SN 台账**（产线烧录身份分区时逐台产出的 CSV，含 `sn` 列）
2. **该 SN 对应的 DeviceSecret**（每卡唯一；签名时直接使用字符串 ASCII 字节）

```python
sn = "8cbfea89f22c"          # 从台账取
target = bytes.fromhex(sn)   # → b'\x8c\xbf\xea\x89\xf2\x2c'，直接填进 mfg[6..11]
```

`target` **就是 SN 的原始字节，不做任何哈希或变换**。设备侧对应地直接 `esp_read_mac(self_target, ESP_MAC_WIFI_STA)`，不格式化字符串、不算哈希。

这样消掉了两整类只在联调时才暴露、且毫无日志线索的 bug：哈希输入的编码口径（12 个 ASCII 字符还是 6 个原始字节？大小写？含不含 `'\0'`？）和字节序。现在两边比对的是同一串肉眼可读的字节，抓包工具里一眼就能看出对不对。

### ⚠️ 不要试图从扫描到的广播地址推 target

台账是唯一正确的来源。想靠"扫到卡片再推算"会同时踩两个坑，且失败现象毫无线索（"全场卡都没反应，两边都没日志"）：

**陷阱一：MAC 不是同一个。** `target` 用 `ESP_MAC_WIFI_STA`（base MAC），BLE 广播地址是 `ESP_MAC_BT` = base MAC **末字节 +2**（`sdkconfig:1257` 是 `UNIVERSAL_MAC_ADDRESSES=4`，见 IDF `mac_addr.c:17,396`）。这个偏移由 sdkconfig 决定，改成 `TWO` 就变成 +1——不能作为跨设备协议的依据。

**陷阱二：字节序相反。** 即便换算对了，NimBLE 的 `ble_addr_t.val` 也是**反序**的（`val[0]` 对应 MAC 最右字节）。

两个陷阱叠加，随手写的推导代码几乎必然是错的。**用台账，不要推导。**

## 11. 风险与已知限制

| 风险 | 影响 | 处置 |
|---|---|---|
| `product_key` 全局共享 | 物理 dump 任意一张卡的 flash 即可伪造全场任意卡的加分广播 | **已确认接受**（与 SN/Key 设计中"明文存"的选择一致）。若将来要求提高，改为每卡独立 Key 签名，代价是网关须持有全量 SN→Key 映射 |
| ~~单网关假设~~ 已解决 | v1 设计里两台网关各自维护 `last_seq` 会互相覆盖：pad A 发到 seq=500 后 pad B 从 1 开始发，会被全部当成重放丢弃 | **v2 起原生支持多网关**：去重键从"这个 seq 是否比记住的更大"改成"这条消息的签名有没有见过"。不同网关发的内容不同（至少 target/balance/op 组合不同），签名自然不同，互不干扰，**零协调，无需引入 `gateway_id` 字段**（见 §5"去重:按消息签名的滑动窗口"） |
| 重放保护从"绝对单调"降级为"最近 16 条窗口" | 窗口轮转后，隔得够久的旧消息理论上可以被再次重放一次而不被拦下 | **已确认接受**：卡片只是显示端，账本在 pad / 后台。重放一条旧消息最多让工牌显示一个过期数字，下一次同步（含周期性 `op=0x04` SYNC）即被纠正，没有人能因此多花一分钱。用这点风险换掉 v1"必须跨设备协调计数器"的部署约束，是刻意的取舍，详见 §5"接受的代价：重放窗口" |
| 未烧录 Key/ProductKey 的开发板 | 功能完全不工作 | 预期行为，开机 WARN 日志说明。见 §3 |
| 广播时长的概率表是估算 | 现场实际漏收率可能与表中数字有出入 | 该表假设"每次广播独立被捕获"。实际 BLE 每个广播事件有 0~10ms 随机延迟、在 37/38/39 三信道轮发，而扫描器同一时刻只监听一个信道。**3 秒的建议值仍然稳妥，但不要把 0.2% 写进验收标准** |
| `save_profile()` 跑在 NimBLE host 任务 | NVS 遇页擦除可阻塞上百毫秒，连接态下可能导致连接超时 | 现有 `on_cfg_message` 已有同样做法且能跑，算既有先例。若联调中出现连接断开，把落盘挪到独立任务 |
| 广播明文可见 | 旁观者能看到某张卡的余额和操作 | 余额不是隐私敏感信息，不加密。若需要，可用 product_key 派生流密钥异或 balance 字段（后续扩展） |
| 提示音抢占乐谱 | 正在播放的乐谱被打断 | 预期行为，见 §9 |
| profile blob 版本升级（v3→v4→v5，去重机制两次改造各带来一次） | 已烧录设备的 profile 重置为出厂默认一次 | 开发阶段接受，见 §8 |

## 12. 测试

### host 单测（Unity，`test_token_bcast.c`）

MAC 注入确定性假实现（如 `out[i] = msg[i] ^ 0xA5`），使签名可在测试中构造。

**结构校验**：长度 25 / 27 / 0 → `BAD_LEN`；魔数任一字节错 → `BAD_MAGIC`；`hdr` 低 4 位 != 2 → `BAD_MAGIC`；`hdr` 高 4 位 != 1（如 `0x22`）→ `BAD_VER`。

**目标筛选**：target 6 字节全错 → `NOT_MINE`；只错最后一字节 → `NOT_MINE`（确认比的是全 6 字节而非前缀）；完全匹配 → 进入下一级。

**签名**：MAC 任一字节错 → `FAIL / BAD_MAC`；MAC 正确 → 通过。
- **坏签名不进窗口**：把同一条 MAC 错误的包喂两次（用不同 `now_ms` 避开限速，比如相隔 4 秒）→ **两次都是** `FAIL / BAD_MAC`，第二次**不能**变成 `REPLAY`。若变成了 `REPLAY`，说明未验签的签名被错误地记进了 `seen[]`，是 §6 顺序约束被破坏的信号。

**顺序性约束（关键）**：
- 构造一个 **target 不匹配且 MAC 也错**的包 → 必须是 `IGNORE / NOT_MINE`，**不能**是 `FAIL / BAD_MAC`。锁死"目标筛选在验签之前"。
- 构造一个 **target 不匹配且 balance 超限（0xFFFFFF）** 的包 → 必须是 `NOT_MINE` 而**不是** `BAD_BALANCE`。锁死"值域校验排在目标筛选之后"，防止别人的广播触发本机 WARN 刷屏。
- 构造一个 **MAC 错误、但 op 是未知值（如 `0x99`）** 的包 → 必须是 `FAIL / BAD_MAC`，**不是** `IGNORE / BAD_OP`。锁死"验签排在 op 分发之前"。

**值域校验**：`seq == 0`（签名正确）→ **合法，正常通过**（`seq` 只是 nonce，`0` 与其他值一样合法，见 §5——这条与 v1 设计的 `BAD_SEQ` 判定正相反，务必显式覆盖，防止有人凭直觉把它当异常值处理）；`balance == 999999` → 通过；`balance == 1000000` → `IGNORE / BAD_BALANCE`；`balance == 0xFFFFFFFF` → `IGNORE / BAD_BALANCE`（**不能**变成负数赋给 `int`）。

**去重（签名滑动窗口，`test_token_bcast.c` 的核心）**：
- 同一条合法包连续处理 3 次 → 第一次为对应 action（如 `ADD`），后两次 `REPLAY`（网关重发只处理一次）。
- **seq 相同、balance 不同的两条广播都要通过**：签名覆盖了 `balance`（见 §5 帧格式），`balance` 一变签名就变，不会被误判为重复。这条直接推翻 v1 靠 `seq` 判重复的思路。
- **多 pad 交叉（核心场景，替代原先的单网关用例）**：先处理一条 target 相同、`seq=500` 的合法包 → 正常通过；紧接着处理另一条 target 相同、`seq=1`、`balance` 不同的合法包（模拟另一台 pad 从头开始计数）→ **同样正常通过**，不会因为"`seq` 比上一条小"而被丢弃。这是 v1 设计下必然失败、v2 设计要保证成立的用例。
- **窗口轮转**：连续喂 17 条内容互不相同（因此签名互不相同）的合法包，使第 1 条的签名被挤出 `TOKEN_SEEN_MAX=16` 大小的窗口；再重发第 1 条 → **不再是 `REPLAY`**，而是当作新消息正常处理。这是刻意接受的代价（见 §5"接受的代价：重放窗口"），测试要断言它确实发生，而不是假装窗口无限大。
- **首包边界**：全新初始化（`token_bcast_init` 刚调用完，`seen_n == 0`）的实例喂第一条合法包 → 必须正常通过。

**op 分发**：`op=0x01` → `ADD`；`0x02` → `SUB`；`0x03` → `FAIL`（**签名记入窗口、balance 有效**）；`0x04` → `SYNC`（**同样记入窗口、balance 有效**）；未知值（如 `0x00`、`0x05`）→ `IGNORE / BAD_OP`，且**不记入窗口**——验证方式：用同一条 `BAD_OP` 包重发一次，第二次仍是 `BAD_OP` 而不是 `REPLAY`。

**两种 FAIL 与 SYNC 的区别（对应 §4 的表）**：
- `op=0x03` 且签名正确 → `FAIL`，`out.balance` **等于包里的值**，签名**已记入窗口**
- `op=0x04` 且签名正确 → `SYNC`，`out.balance` **等于包里的值**，签名**已记入窗口**（`token_bcast` 本身不知道、也不关心"响不响音"，那是 app.c 层的事，见 §4）
- 签名错误 → `FAIL / BAD_MAC`，`out.balance == 0`，`out.seq == 0`，**不记入窗口**

**限速**：`now_ms=1000` 坏 MAC → `FAIL`；`now_ms=2000` 坏 MAC → `IGNORE / MAC_RATE_LIMITED`；`now_ms=4500` 坏 MAC → `FAIL`。
- **首包不限速**：`token_bcast_init` 之后，`now_ms=0` 时喂第一条坏 MAC 包 → 必须是 `FAIL`，不能被误判成"距上次不足 3 秒"而吞掉（验证 `bad_mac_seen` 哨兵，见 §7）。
- **回绕**：`bad_mac_ms = 0xFFFFF000`，喂 `now_ms = 0x00000100`（已跨回绕，实际间隔约 4.3 秒）→ **必须是 `FAIL` 而非被限速**。锁死无符号减法写法。

**enabled=false**：任何合法包 → `IGNORE / DISABLED`。

### `ble_match_classify` 单测（补进 `test_ble_match.c`）

`FFFF4842`（len=4）→ `HEARTBEAT`（向后兼容，现有用例不变）；`FFFF4842` + `[0x12]...` → `TOKEN`；`FFFF4842` + `[0x02]...` → `HEARTBEAT`（旧 type 值不再是 token）；`FFFF5842` → `NONE`；len=3 → `NONE`；NULL → `NONE`。

### 硬件联调

1. 用 Android pad 或 nRF Connect 按 §5 发 26 字节厂商数据的广播 → 卡片显示 balance、响加分音
2. **同一条广播连发 30 次 → 只响一次、只刷一次**（核心需求验证）
3. 改 target 一个字节 → 卡片完全无反应（不响音、不打 WARN）
4. 改 mac 一个字节 → 响失败音一次；3 秒内连发 → 只响一次
5. `op=0x02` → 扣分音；`op=0x03` → 失败音；`op=0x04` → **屏幕刷新为新余额，但不响任何音**（SYNC，核心需求验证）
6. **两台 pad（或用两条 target 相同但内容不同的包模拟）交替各发一条广播** → 两条都能正常处理、都响对应的音（验证多网关零协调，见 §5、§11；替代 v1 基于单网关 `last_seq` 的用例）
7. 断电重启后重发**同一条**之前处理过的广播 → **仍会正常响音、刷新一次**。这不是 bug：去重窗口只在 RAM、重启即清空，是刻意接受的代价（见 §5"接受的代价：重放窗口"）。**不要**按 v1 的预期去验证"无反应"
8. 发 4 字节 `FFFF4842` 心跳 → 爱心屏跳动，**不触发 token 逻辑**；发 token 广播 → **不触发爱心屏**（验证 §5 共存改动）
9. 播放乐谱途中收到 token 广播 → 提示音抢占
10. 未烧录 Key/ProductKey 的板子收到 token 广播 → 无反应，开机有 WARN 日志
11. **窗口轮转**：连续发送 17 条以上互不相同的合法广播后，重发第 1 条 → **正常再处理一次**（验证去重只护住最近 16 条，这也是刻意的取舍，不是缺陷）
12. `op=0x03` 且 balance 与当前显示不同 → 屏幕**更新为新值**并响失败音；重启后新值仍在（验证失败路径也落盘）
13. 连接着蓝牙（小程序在线）时收到 token 广播 → 正常处理，**且连接不断开**（验证 §11 里 `save_profile` 阻塞 host 任务的风险）
14. 若联调脚本或串口工具里还留着 `AT+TOKENSEQ=<n>` 相关按钮/命令，敲一次 → **应返回"不认识的指令"**。该指令随 v1 去重机制一起被完全删除，不应有任何残留分支响应它

## 13. 环境备注

- 本仓库非 git 仓库，spec 不做 git 提交。
- `host_test` 工程环境已知损坏，纯逻辑单测的实际执行以固件 build + 硬件联调为准；单测代码仍然要写，环境修复后可直接跑。
- 无新增分区（复用 SN/Key 设计中的 `cardid` 分区），但 **profile blob 版本升级（当前 v5）会重置一次现有配置**。
- `token_mac_esp.c` 引入 mbedtls 依赖，需在 `platform_esp32/CMakeLists.txt` 的 `REQUIRES` 中加 `mbedtls`。`core/services` 的 `REQUIRES ports json` **保持不变**。

## 变更记录

| 版本 | 变更 |
|---|---|
| v2 | **去重机制由"单调 seq + 持久化 last_seq"改为"消息签名滑动窗口"**。起因：多台 pad 各自维护本地计数器且无法同步，pad A 发到 seq=500 后 pad B 从 1 开始发会被全部当成重复包静默丢弃。改动后 pad 之间零协调。`seq` 降级为 nonce；`profile.token_seq` 与 `AT+TOKENSEQ` 指令一并删除（`PROFILE_BLOB_VER` 4→5）；新增 `op=0x04` 静默同步。**帧格式与字节布局不变。** |
| v1 | 初版 |
