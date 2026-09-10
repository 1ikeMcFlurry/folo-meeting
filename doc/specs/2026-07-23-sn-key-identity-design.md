# 设计：设备身份（SN / Key）存储、产线烧录与读取

- 日期：2026-07-23
- 目标芯片：ESP32-C3（量产 8MB / 开发板 4MB，同一份构建产物）
- 参考：`doc/sn-key-storage.md`（folotoy-firmware-idf 的三元组架构）
- 下游：`doc/specs/2026-07-23-ble-token-broadcast-design.md`（依赖本设计的 SN 与 product_key）
- 状态：设计定稿，待实现

## 1. 背景与目标

卡片需要一个**永不丢失**的设备身份，供两件事使用：

1. **小程序绑定校验** —— 小程序连上 BLE 后读到 SN 和 Key，据此把这张卡绑定到用户账号
2. **token 广播验签** —— 用全厂共享的 `product_key` 校验网关广播的 HMAC 签名

### 与 folotoy 的三处关键差异

| | folotoy | trae_card |
|---|---|---|
| 存储层次 | 两层（NVS + config.json 回填） | **一层**（业务配置不会被"恢复出厂"清空，不需要回填） |
| 产线写入方式 | AT 指令逐台下发 | **生成 NVS 分区 bin 直接烧录**，AT 指令降级为返修补写 |
| SN 来源 | 产线分配，缺省时用 MAC 兜底 | **固定取 `esp_read_mac(ESP_MAC_WIFI_STA)`**，产线生成 bin 时写入，固件发现为空时自补 |

## 2. 范围

**包含**：`cardid` 独立 NVS 分区；产线分区 bin 生成流程；固件端 SN 自补；`hal_identity` 只读端口 + `hal_identity_provision` 写入端口（物理分离）；BLE 只读特征 `…0012`；USB 串口返修指令；开机自检；给 token 广播用的 HMAC 接口。

**不包含**：云端接口（未建设）；OTA 保留 SN（无 OTA）；每卡独立广播密钥；NVS 加密 / Flash 加密 / Secure Boot。

## 3. 字段模型

| 字段 | 来源 | NVS 键 | 长度 | BLE 可读 | AT 可改 | 用途 |
|---|---|---|---|---|---|---|
| SN | WIFI_STA MAC，产线写入 bin；固件可自补 | `sn` | 12 字符 | ✅ | ❌ | 设备唯一标识。小程序绑定；token 广播 `target = SHA256(SN)[0..5]` |
| Key | **烧录时随机生成** | `key` | 32 字符 | ✅ | ✅ | 小程序绑定校验口令 |
| ProductKey | 全厂固定，产线写入 bin | `pk` | ≤16 字节 | ❌ **永不可读** | ✅ | **全厂共享的 token 广播 HMAC 密钥** |
| HwVer | 产线写入 bin | `hw` | ≤8 字节 | ✅ | ✅ | 硬件版本，如 `A1.2` |

均为 UTF-8 可打印字符串，NVS 用 `nvs_set_str` 存储。

### SN 的格式与口径

```c
uint8_t mac[6];
esp_read_mac(mac, ESP_MAC_WIFI_STA);
snprintf(sn, 13, "%02x%02x%02x%02x%02x%02x",
         mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
// → "8cbfea89f22c"：小写、无冒号、12 字符、按 mac[0] 在最左的打印顺序
```

`esptool.py read_mac` 打印的就是这个 MAC（base MAC = WIFI_STA MAC），产线工装去掉冒号转小写即可，无需换算。

### ⚠️ SN 的 MAC 与 BLE 广播地址不是同一个

`sdkconfig:1257` 为 `CONFIG_ESP_MAC_UNIVERSAL_MAC_ADDRESSES=4`，对应 IDF `mac_addr.c:17` 与 `:396`：

```
SN 用的     ESP_MAC_WIFI_STA = base MAC
BLE 广播地址 ESP_MAC_BT       = base MAC，mac[5] += 2
```

**两者末字节差 2。** 因此：

- **网关不能靠扫描广播地址来推 SN**。理论上减 2 可得，但偏移量由 sdkconfig 决定（若改为 `..._TWO` 则变成 +1），把它当成跨设备协议依据是脆弱的。
- 正确做法：**产线生成分区 bin 时已持有每台设备的 SN，导出台账给网关**（见 §7）。这条路径零推导、零耦合。

另注：IDF 的实现是 `mac[5] += 2` 的裸字节加法，**不向 `mac[4]` 进位**。乐鑫按 4 个一组分配 base MAC（末字节 4 对齐），实际不会溢出，但不要假设它是完整的 6 字节整数加法。

### ⚠️ ProductKey 的语义与 folotoy 不同

folotoy 的 `product_key` 是公开的产品名（字面值 `"folotoy"`），会随身份信息一起下发给业务层。**本设计把它当成密钥用**，因此：

> **ProductKey 绝不能出现在 BLE 特征、日志明文、或任何对外接口中。**

否则任何人连上卡的 BLE 读一次，就拿到了伪造全场 token 广播的密钥——8 字节 MAC 和整个防伪设计瞬间归零，连 dump flash 都不需要。

设计上用**接口形状**强制这一点，而不是靠纪律：`hal_identity` **不提供 `get_product()`**，只提供 `hmac()`。密钥不出适配器边界，BLE 层即使写错代码也拿不到。

曾提议新增独立的 `bcast_key` 字段把"产品标识"和"广播密钥"拆开，已否决——少一个产线字段，且小程序本来就不需要产品标识。代价是名字与 folotoy 语义不符，靠本节说明和接口约束防误用。

## 4. 分区表

在 `partitions.csv` 末尾追加一行（沿用现有的自动偏移写法）：

```csv
# Name,   Type, SubType, Offset,  Size
nvs,      data, nvs,     ,        0x6000,
phy_init, data, phy,     ,        0x1000,
factory,  app,  factory, ,        0x300000,
imgstore, data, spiffs,  ,        0x20000,
imgframe, data, spiffs,  ,        0x26000,
cardid,   data, nvs,     ,        0x4000,     ← 新增
```

实际偏移落在 **`0x356000`**，占 16KB，结束于 `0x35A000`。这个偏移量产线烧录 bin 时要用到，分区表若变动必须同步更新产线脚本。

**刻意放在 4MB 以内**（4MB = `0x400000`）。量产板是 8MB，但手头开发板是 4MB；若把身份分区放到 flash 尾部（folotoy 的做法，`0xF20000`），开发板上**根本没有这个地址，产线烧录流程无法在开发板上验证**。放在 `0x356000` 让同一份分区表在两种板子上都能跑通，与 `CONFIG_ESPTOOLPY_HEADER_FLASHSIZE_UPDATE=y` 的"一份产物通吃"思路一致。

**为什么必须是独立分区**：与系统 `nvs` 分区物理隔离。将来任何"恢复出厂设置""擦除配置"的操作擦的是 `nvs`，碰不到 `cardid`，SN/Key 因此不丢。这是 folotoy 架构里最值得移植的一点。

**烧录不覆盖身份**：`idf.py flash` 只写 bootloader / 分区表 / app，不碰 `cardid`。**但 `idf.py erase-flash` 会擦掉一切**——产线和调试必须避免，`README.md` 要显式警告。

## 5. 架构（六边形分层）

| 层 | 文件 | 责任 |
|---|---|---|
| core/ports | `hal_identity.h`（新增） | **只读**身份端口：get_sn / get_key / get_hw_ver / is_provisioned / **hmac** |
| core/ports | `hal_identity_provision.h`（新增） | **写入**端口，独立头文件 |
| platform | `identity_nvs.c`（新增） | 两个端口的唯一实现。持有 `cardid` 分区句柄与 product_key；开机自补 SN |
| platform | `provision_console.c`（新增） | USB 串口返修指令解析任务 |
| platform | `ble_config.c`（改动） | 新增只读特征 `…0012` |
| app | `app.c`（改动） | 开机自检、接线、把身份注入给 token_bcast |
| tools | `make_cardid.py`（新增） | 产线：读 MAC → 生成 NVS CSV → 生成分区 bin → 烧录 → 追加台账 |

### 读写端口为什么拆成两个头文件

```c
// hal_identity.h            ← BLE 层、token 层 include 这个
hal_identity_get_sn / get_key / get_hw_ver / is_provisioned / hmac

// hal_identity_provision.h  ← 只有 provision_console.c 与 identity_nvs.c 自身 include
hal_identity_set_fields(key, pk, hw)
```

这不是洁癖。写入能力是**物理接触即授权**的高权限操作；如果读写挤在一个头文件里，BLE 层 include 之后就随时可能被"顺手"调用写入——一次 review 疏忽就等于开放了远程改身份的通道。拆开之后，**BLE 层在编译期就看不见写入函数**，误用需要主动加 include，会在 review 中显眼地暴露。

### 接口定义

```c
// core/ports/include/hal/hal_identity.h
typedef struct hal_identity_s hal_identity_t;

typedef struct {
    // 取字符串到 buf，返回实际长度；缺失或 cap 不足返回 <0。始终以 '\0' 结尾。
    int  (*get_sn)     (hal_identity_t *self, char *buf, int cap);
    int  (*get_key)    (hal_identity_t *self, char *buf, int cap);
    int  (*get_hw_ver) (hal_identity_t *self, char *buf, int cap);
    bool (*is_provisioned)(hal_identity_t *self);

    // 用 product_key 对 msg[0..len) 算 HMAC-SHA256，截断写入 out[0..out_cap)。
    // 成功返回 0。product_key 不出这个函数。
    int  (*hmac)(hal_identity_t *self, const uint8_t *msg, int len,
                 uint8_t *out, int out_cap);
} hal_identity_api_t;

struct hal_identity_s { const hal_identity_api_t *api; void *impl; };
```

```c
// core/ports/include/hal/hal_identity_provision.h
// 局部更新：非 NULL 的字段才写。成功返回 0。
// 没有 sn 参数 —— SN 由产线 bin 或固件自补决定，运行时不可改。
int hal_identity_set_fields(hal_identity_t *self,
                            const char *key, const char *pk, const char *hw);
```

### NVS 布局

- 分区：`cardid`（`nvs_open_from_partition`）
- 命名空间：`id`
- 键：`sn` / `key` / `pk` / `hw`，均 `nvs_set_str`

`product_key` 在 `identity_nvs.c` 初始化时读入 static 缓冲，之后只被 `hmac()` 使用，**不提供任何 getter**。

### ⚠️ 写入不能先擦分区

folotoy 的 `WriteTripleTuple` 是"先 `nvs_flash_erase_partition` 再写全部字段"，用整分区擦除换取幂等。**本设计不能这么做**——AT 指令只改 `key`/`pk`/`hw`，擦分区会连 `sn` 一起抹掉。

改为**逐键写入，不擦分区**：`hal_identity_set_fields` 只对非 NULL 的参数调 `nvs_set_str`，其余键原样保留。幂等性由"整分区 bin 覆盖烧录"这条主路径保证（见 §7），AT 指令是补丁路径，不需要幂等。

## 6. BLE 只读特征

在现有档案卡服务下新增一个只读特征：

```
UUID:  54524145-4341-5244-0000-000000000012
属性:  READ（无 WRITE、无 NOTIFY）
返回:  固定 72 字节
```

```
[0]      ver = 0x01
[1]      flags        bit0 = 1 表示 Key 与 ProductKey 均已写入
[2..25]  SN      24B  实际占 12 字符，右侧补 0x00
[26..57] Key     32B  不足右侧补 0x00；未写入时全 0
[58..65] HwVer    8B  不足右侧补 0x00；未写入时全 0
[66..71] 保留     6B  全 0
```

定长而非变长：小程序侧解析简单，字段偏移固定，不需要处理 TLV。72 字节超过默认 ATT MTU（23），NimBLE 对静态特征值会自动走 ATT_READ_BLOB 分段，客户端无感知。

SN 字段保留 24 字节而非缩到 12——将来若改用产线分配的长 SN，布局不用动，小程序不用改。

**ProductKey 不在其中**（见 §3）。

**未烧录身份分区的板子**：`flags bit0 = 0`，Key/HwVer 全 0，**但 SN 仍然有效**（固件自补，见 §8）。小程序必须查 `flags` 而不是"SN 非空"来判断设备是否激活——SN 永远非空。

### 安全性说明

这个特征**任何人连上 BLE 都能读**，没有配对、没有鉴权。这是需求确认过的选择（"只需要小程序能获取到 SN 和 key"），其含义是：

> Key 从"防伪凭证"降级为"**近场口令**"——证明持有者曾物理靠近过这张卡（BLE 有效距离约 10 米），不能证明持有者拥有这张卡。

云端建设时若需要更强的绑定语义，应改为挑战-应答（设备用 Key 签名云端下发的随机数，Key 不出设备）。本设计保留了改造空间：`hal_identity` 已有 `hmac()`，届时增加一个可写特征即可，NVS 布局不用动。

## 7. 产线流程（主路径：烧录分区 bin）

### 步骤

```
① 烧录固件      idf.py flash（bootloader + 分区表 + app）
② 读 MAC        esptool.py -p <PORT> read_mac
                → "MAC: 8c:bf:ea:89:f2:2c"，即 WIFI_STA MAC
③ 生成 CSV      sn   = 去冒号转小写 = "8cbfea89f22c"
                key  = 随机生成（见下）
                pk   = 全厂固定值（从产线配置读，不硬编码在脚本里）
                hw   = 本批次硬件版本
④ 生成分区 bin  nvs_partition_gen.py generate cardid.csv cardid.bin 0x4000
⑤ 烧录分区      esptool.py -p <PORT> write_flash 0x356000 cardid.bin
⑥ 追加台账      向 CSV 台账追加一行 (sn, key, hw, 时间戳, 工单号)
⑦ 复位验证      重启后串口应出现 "身份自检通过 SN=8cbfea89f22c"
```

`cardid.csv` 格式（`nvs_partition_gen.py` 的标准格式）：

```csv
key,type,encoding,value
id,namespace,,
sn,data,string,8cbfea89f22c
key,data,string,3f8a1c...（32 个十六进制字符）
pk,data,string,<全厂共享密钥>
hw,data,string,A1.0
```

`tools/make_cardid.py` 把 ②~⑥ 串成一条命令，产线工装单次调用即可。

### Key 的随机生成

**每台一个，用密码学安全随机源**：

```python
key = secrets.token_hex(16)      # 16 字节熵 → 32 个十六进制字符，正好填满字段
```

必须用 `secrets` 而不是 `random`。`random` 是梅森旋转，**从少量输出即可反推内部状态、预测出全部后续 Key**——如果拿它生成量产密钥，攻击者只要买几张卡读出 Key，就能算出整批设备的 Key。这是个静默失效的错误：生成出来的东西看起来一样随机，问题只在被攻击时才暴露。

### SN 台账是网关的唯一数据来源

步骤 ⑥ 产出的台账（`sn` 列）就是网关需要的 SN 清单。**网关不推导、不扫描、不猜**——见 §3 关于 MAC 偏移的说明。

台账同时是**排查依据**：设备返修时凭 SN 查工单，Key 泄漏时凭 SN 定位批次。

### 台账里的 Key 是敏感数据

台账含明文 Key，等价于一份"全批设备的绑定口令表"。存放和传输应比照密钥材料处理（限权限、不进公共仓库、不随邮件明文发）。`pk` **不进台账**——它是全厂共享的，写进每份台账等于把泄漏面放大到每个产线批次。

## 8. 固件端：SN 自补与开机自检

### SN 自补

`identity_nvs.c` 初始化时：

```c
if (nvs 里 sn 为空或缺失) {
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    format_sn(mac, sn);              // 见 §3
    nvs_set_str(h, "sn", sn);
    nvs_commit(h);
    ESP_LOGW(TAG, "SN 缺失，已按 WIFI_STA MAC 自补: %s", sn);
}
```

覆盖三种情况：身份分区没烧（开发板）、产线漏烧、bin 生成时 MAC 字段为空。自补后 SN 与产线 bin 里写的完全一致（同一个 MAC、同一个格式），两条路径殊途同归。

### SN 与实际 MAC 不一致时告警

```c
if (nvs 里 sn 非空 && sn != format_sn(esp_read_mac(WIFI_STA))) {
    ESP_LOGE(TAG, "SN(%s) 与本机 MAC(%s) 不符！"
                  "可能是分区 bin 烧错设备，请核对台账", nvs_sn, mac_sn);
}
```

**只告警，不覆盖**——NVS 里的 SN 是权威值，云端可能已按它建立绑定关系，固件擅自改写会让绑定凭空失效。

这条检查专门拦一类真实的产线事故：分区 bin 是逐台生成的，如果工装把 A 机的 bin 烧到了 B 机，两台设备就会有相同的 SN。这种错误在出厂测试里完全看不出来，要等用户绑定冲突才暴露。一行比对就能在产线当场拦住。

### 开机自检

`app_run()` 早期执行，在 BLE 和 token 广播初始化之前：

```c
s_id = platform_create_identity(&board);
if (!hal_identity_is_provisioned(s_id)) {
    ESP_LOGW(TAG, "未写入 Key/ProductKey，小程序绑定与 token 广播不可用。SN=%s", sn);
}
```

判定条件：`key` 与 `pk` **两者均非空**（`hw` 可缺省，`sn` 恒有效，均不参与判定）。

**未写入时的降级行为**（全部静默降级，不阻止开机）：

| 模块 | 行为 |
|---|---|
| BLE 特征 `…0012` | `flags bit0 = 0`，Key/HwVer 全 0，SN 有效 |
| token 广播 | `token_bcast_t.enabled = false`，所有广播 IGNORE，不响任何音 |
| 其余功能 | 完全不受影响（显示、按键、乐谱、图片、心跳爱心屏照常） |

不播报语音、不弹屏（folotoy 会播 `three-tuple-error.mp3`）——trae_card 是展示型设备，开机对着用户报"身份错误"没有意义，产线看串口日志即可。

## 9. USB 串口指令（补丁路径）

产线主路径是烧分区 bin，串口指令只用于**返修补写和现场排查**。

```
AT+CARDID=key=<KEY>[,pk=<PK>][,hw=<HW>]
    局部更新，只写给出的字段，不擦分区。
    ⚠ 不接受 sn= 参数：SN 由 MAC 决定，运行时不可改。
      传了 sn= 一律回 +ERR=sn_readonly，不静默忽略。
    回 +OK 或 +ERR=<原因>

AT+CARDID?
    回读校验。回:
    +CARDID: sn=<12位十六进制>,hw=<HW>,pk_fp=<8位十六进制>,provisioned=<0|1>


### 回读为什么只给指纹不给明文

`AT+CARDID?` **不回显 Key，也不回显 ProductKey 明文**，`pk_fp` 是 `SHA256(product_key)` 前 4 字节的十六进制。

产线/返修需要的是"确认写对了"，指纹足以核对。而串口输出常被工装记录、上传到 MES 系统，明文密钥落到日志里就等于泄漏。指纹成本几乎为零，收益是把密钥挡在日志之外。

### 解析器实现

控制台已配置为 **USB-Serial-JTAG**（`CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y`，GPIO18/19），单根 USB-C 既看日志又能烧录，指令复用同一条链路。

**不引入 `esp_console`**，自己写一个约 80 行的行解析器（`provision_console.c`），起一个 3KB 栈的低优先级任务读串口、按行解析。理由：

- `esp_console` REPL 会带来 linenoise 缓冲 + 补全/历史表 + 默认 4KB 栈的任务，flash 和 RAM 都不便宜。这块板子的**静态 RAM 余量直接决定 BLE 能否初始化成功**（有过大块静态缓冲压垮 NimBLE 堆的事故），能省则省。
- 本项目一贯自己写小解析器（`rtttl.c` / `jpeg_rx.c` / `frame_reasm.c` / `config_json.c`），行解析器与既有风格一致。
- 只有 3 条固定格式指令，不需要历史、补全、参数校验框架。

## 10. 给 token 广播的接口

```c
// app.c 开机时
uint8_t target[6];
esp_read_mac(target, ESP_MAC_WIFI_STA);   // target 就是 SN 的原始字节，不做变换
token_bcast_init(&s_tb, target, s_profile.token_seq, token_mac_esp, s_id);
```

```c
// platform/token_mac_esp.c —— 注入给纯服务层的 MAC 函数，约 20 行
static int token_mac_esp(const uint8_t *msg, int len, uint8_t out[8], void *user) {
    hal_identity_t *id = (hal_identity_t *)user;
    return hal_identity_hmac(id, msg, len, out, 8);
}
```

`product_key` 全程不出 `identity_nvs.c`。`token_bcast.c`（core/services）拿到的只是一个函数指针，连密钥存在都不知道——这正是把 HMAC 做成注入而非直接调用的收益。

### ⚠️ HMAC 密钥的编码口径

> **HMAC 密钥 = ProductKey 字符串的原始 ASCII 字节**，不做 hex 解码、不含结尾 `'\0'`、不做任何变换。

```python
# 网关侧
mac = hmac.new(pk.encode(), msg, hashlib.sha256).digest()[:8]   # ✅
mac = hmac.new(bytes.fromhex(pk), msg, hashlib.sha256).digest()[:8]   # ❌
```

这条必须与下面 target 的口径一样郑重对待。ProductKey 如果长得像十六进制串（例如 `deadbeefcafe1234`），网关方极易顺手写成 `bytes.fromhex(pk)`——两边签名永不相符，而现象是"全场卡毫无反应、两边都没有日志"，正是本节其余部分要防的那类失败。

### target 与 SN 是同一个值的两种表示

```
WIFI_STA MAC 原始 6 字节  ── 十六进制小写 ─→  SN 字符串（存 NVS、给小程序、进台账）
        │
        └─ 原样使用 ────────────────────→  token 广播的 target 字段
```

**不做哈希、不做变换。** 网关侧 `bytes.fromhex(sn)`，设备侧 `esp_read_mac(ESP_MAC_WIFI_STA)`。

早期草案是 `target = SHA256(SN)[0..5]`。改掉的原因：哈希只在两边对"输入是什么"理解完全一致时才能对上——12 个 ASCII 字符还是 6 个原始字节？大写还是小写？含不含结尾 `'\0'`？任何一处理解不同，现象都是"全场卡毫无反应、两边都没有日志"，没有任何线索指向根因。用原始字节后，两边比对的是同一串肉眼可读的值，抓包工具里一眼可辨。

哈希唯一的收益是不在广播里暴露 MAC，但**卡片本来就在持续广播自己的 BT MAC**（与 SN 只差末字节 2，一眼可关联），这层遮蔽等于没有。

**SN 仍然来自产线台账**，不是从扫描到的广播地址推导——广播地址是 BT MAC，与 SN 差 2 且字节序相反，见 §3。

## 11. 安全模型与已知风险

本设计的安全边界是**明确且刻意收窄**的。以下每一条都是确认过的选择，不是疏漏：

| 风险 | 影响 | 状态 |
|---|---|---|
| Key 明文存 NVS | 物理 dump flash 即可克隆设备身份 | **已确认接受**（与 folotoy 一致，未启用 NVS/Flash 加密） |
| Key 经 BLE 明文可读，无鉴权 | 任何人在 10 米内连上即可读走 SN+Key。Key 只能证明"曾靠近过"，不能证明"拥有" | **已确认接受**。改造路径见 §6 |
| ProductKey 全厂共享 | dump 任意一张卡即可伪造全场 token 广播 | **已确认接受**（token 广播设计中选定的防伪级别） |
| 产线台账含明文 Key | 台账泄漏 = 整批设备绑定口令泄漏 | 按密钥材料管理：限权限、不进公共仓库。`pk` 不入台账 |
| USB 可无限次改写 Key/ProductKey | 物理接触 USB 口即可改写 | 符合"物理接触即授权"模型。**SN 改不了**（由 MAC 决定），这是一层额外保护 |
| 换主板 → SN 变 | 云端绑定关系失效，用户需重新绑定 | MAC 作 SN 的固有代价，**已确认接受**。返修 SOP 需覆盖：换板后通知用户重新绑定 |
| 分区 bin 烧错设备 → SN 重复 | 两台设备同 SN，云端绑定冲突，出厂测试看不出来 | §8 的 SN↔MAC 比对在开机时当场告警 |
| `idf.py erase-flash` 擦掉身份 | 需重新走产线烧录 | 文档警告 + 产线 SOP 约束。无技术手段防护 |
| 卡片用 public static 地址，未启用 BLE privacy | 可被长期追踪 | 展示型设备场景下接受。注意：**若将来启用 privacy，广播地址会变成随机地址**，不影响 SN（SN 来自 eFuse 而非广播地址） |
| `product_key` 语义与 folotoy 不同 | 移植者按 folotoy 惯例把它当公开标识下发 → 密钥泄漏 | 靠 §3 说明 + **接口不提供 getter** 双重约束 |

### 这套设计实际能防住什么

能防：网关广播被随手伪造（需要 product_key）；远程改写设备身份（写入只走 USB / 烧录）；身份被"恢复出厂设置"误擦（独立分区）；SN 被运行时篡改（由 MAC 决定）。

**防不住**：拿到实体卡的人。这是明确的设计边界——威胁模型是"路人和远程攻击者"，不是"卡的持有者"。

## 12. 测试

### host 单测

`test_sn_format.c`（`sn_from_mac(mac, buf)`，纯逻辑）：
- `{0x8c,0xbf,0xea,0x89,0xf2,0x2c}` → `"8cbfea89f22c"`（小写、无冒号、12 字符、**不反转顺序**）
- 含 `0x00` / `0x0f` / `0xff` 字节 → 补零正确（`"000fff..."` 而非 `"0fff..."`）
- 输出恒为 12 字符 + `'\0'`

`test_provision_parse.c`（行解析器的纯解析部分）：
- `AT+CARDID=key=B2,pk=C3,hw=D4` → 三字段正确切分
- `AT+CARDID=key=B2` → 只有 key，pk/hw 为 NULL（**局部更新**，锁死 §5 不擦分区的语义）
- 参数顺序打乱 → 仍正确
- 缺 `key` 且缺 `pk` 且缺 `hw` → `missing_param`
- **含 `sn=` 参数 → `sn_readonly`**（锁死 §9："报错而非静默忽略"）
- 值含大写 `AT+cardid=key=AbCd` → Key 保留为 `AbCd` 而非 `abcd`（锁死大小写约定）
- 超长值（Key > 32 字节）→ `too_long`
- 空值 `key=,` → `missing_param`
- `AT+CARDID?` → 正确识别指令类型；`AT+TOKENSEQ=0`（已作废的老指令）→ `unknown`
- 未知指令 / 空行 / 只有 `AT+` → 不崩溃，回 `+ERR=unknown`

### 硬件联调

1. **未烧身份分区的空白板**开机 → 日志出现"SN 缺失，已按 WIFI_STA MAC 自补"，且该 SN 与 `esptool.py read_mac` 一致
2. 同上，`AT+CARDID?` → `provisioned=0`，但 `sn` 非空
3. 同上，BLE 读 `…0012` → `flags=0`，Key/HwVer 全 0，**SN 字段有效**
4. 重启第 1 步的板子 → **不再打自补日志**（验证已落盘）
5. 走完整产线流程（`make_cardid.py`）→ `+OK`，重启后 `provisioned=1`
6. `AT+CARDID?` → 回显 sn/hw/pk_fp；**确认不回显 key 和 pk 明文**
7. BLE 读 `…0012` → 72 字节，SN/Key 正确，`flags bit0=1`；**确认 72 字节里找不到 pk**
8. `AT+CARDID=key=<新值>` → 只有 key 变，**sn/pk/hw 全部保留**（验证 §5 不擦分区）
9. `AT+CARDID=sn=deadbeef` → `+ERR=sn_readonly`，且 NVS 里 sn 未变
10. `idf.py flash` 重烧固件 → 身份全部仍在（验证分区不被覆盖）
11. **烧错设备检测**：把 A 板生成的 `cardid.bin` 烧到 B 板 → B 板开机打 ERROR"SN 与本机 MAC 不符"，且**不覆盖** NVS 里的 SN
12. 4MB 开发板上完整跑通 1~11（验证 §4 的偏移选择）
13. **MAC 差值验证**：记录 `esptool.py read_mac` 的结果与 nRF Connect 扫到的广播地址，确认**末字节相差 2**（验证 §3 的判断，也是"网关不能靠扫描推 SN"的实证依据）
14. 与 token 广播联调：网关从台账取 SN → `bytes.fromhex(sn)` 作 target → 用同一 pk 签名 → 卡片正确响应。抓包确认广播 `adv[9..14]` 与串口日志里的 SN 逐字节对应

## 13. 环境备注

- 本仓库非 git 仓库，spec 不做 git 提交。
- `host_test` 工程环境已知损坏，纯逻辑单测执行以固件 build + 硬件联调为准；单测代码仍要写。
- **新增 `cardid` 分区，首次烧录需要 `idf.py erase-flash` 一次**（分区表变更）。此后严禁再 erase-flash（会擦掉身份）。
- `identity_nvs.c` 与 `token_mac_esp.c` 引入 mbedtls（HMAC-SHA256、SHA256），需在 `platform_esp32/CMakeLists.txt` 的 `REQUIRES` 中加 `mbedtls`。`core/services` 的 `REQUIRES ports json` 保持不变。
- `nvs_partition_gen.py` 位于 `$IDF_PATH/components/nvs_flash/nvs_partition_generator/`，产线机器需要装 ESP-IDF 或至少这个脚本 + esptool。
- `provision_console.c` 从 USB-Serial-JTAG 读输入的具体方式（`usb_serial_jtag_driver_install` + `usb_serial_jtag_read_bytes`，还是 `esp_vfs_usb_serial_jtag_use_driver` + `fgets`）**需上板验证**，重点确认安装驱动后 `ESP_LOG` 输出不受影响。两种方式的接口差异局限在单个文件内，选错改动成本低。
