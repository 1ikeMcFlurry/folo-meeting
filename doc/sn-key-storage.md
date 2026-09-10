# SN / Key 存储与写入机制（移植文档）

本文完整描述 folotoy-firmware-idf 中设备唯一标识（SN）与密钥（Key）的存储、写入、读取、校验链路，以及移植到其他 ESP-IDF 项目所需的全部改动点。

---

## 1. 设计概览

设备身份采用**三元组（TripleTuple）**模型，分两层存储：

```
                 产线 AT 指令
                      │
                      ▼
        ┌─────────────────────────────┐
        │  第一层：folotoy NVS 分区    │   ← 出厂数据，永久保存
        │  独立分区 / 明文 / 5 个键     │
        └─────────────┬───────────────┘
                      │  首次开机回填（Board 初始化时）
                      ▼
        ┌─────────────────────────────┐
        │  第二层：config.json         │   ← 运行时配置，可被重置
        │  littlefs storage 分区       │
        └─────────────┬───────────────┘
                      │
                      ▼
          MQTT 鉴权 / HTTP API / OTA
```

**为什么分两层：**

- 第一层放在**独立的 `folotoy` NVS 分区**，与系统 `nvs` 分区物理隔离。WiFi 重置、恢复出厂设置会擦除系统 `nvs`，但不会碰 `folotoy`，SN/Key 因此不丢。
- 第二层 `config.json` 是业务配置，可以随时 `RestoreDefaultConfig()` 重置；重置后下次开机会自动从第一层回填。
- 业务代码（MQTT、HTTP）**只读第二层**，不直接访问 NVS，解耦。

---

## 2. 分区表

`partitions.csv`（ESP32-S3，16MB flash）：

```csv
# Name,   Type, SubType,  Offset,   Size, Flags
nvs,      data, nvs,      0x9000,   0x4000,
otadata,  data, ota,      0xD000,   0x2000,
phy_init, data, phy,      0xF000,   0x1000,
storage,  data, littlefs, 0x10000,  2M,
app0,     app,  ota_0,    0x210000, 6M,
app1,     app,  ota_1,    0x810000, 6M,
log0,     data, 0x40,     0xE10000, 0xF0000,
coredump, data, coredump, 0xF00000, 0x20000,
folotoy,  data, nvs,      0xF20000, 0x4000,     ← SN/Key 分区
log1,     data, 0x41,     0xF24000, 0xDC000,
```

`partitions-huoshan.csv`（带 factory 分区的变体）同样包含 `folotoy,data,nvs,0xF20000,0x4000`。

**关键点：**

| 项 | 值 | 说明 |
|---|---|---|
| 分区名 | `folotoy` | 代码中硬编码，`nvs_open_from_partition` 用 |
| 类型 | `data` / `nvs` | 标准 NVS 分区 |
| 偏移 | `0xF20000` | 放在 flash 尾部，远离 app 区 |
| 大小 | `0x4000`（16KB） | NVS 最小可用为 3 个扇区（12KB），16KB 足够存 5 个短字符串 |
| 加密 | 无 | 未启用 NVS encryption，数据明文 |

**移植时**：偏移量必须按目标项目的 flash 布局重新分配，只要保证是 `data/nvs` 类型、大小 ≥ 0x3000、且不与其他分区重叠即可。名字建议保持 `folotoy`，否则要同步改 `config_util.cpp` 中的字符串。

### 2.1 烧录不覆盖 SN

`scripts/merge.sh` 生成的 `merged-binary.bin` 只包含：

```
0x0       bootloader.bin
0x8000    partition-table.bin
0xd000    ota_data_initial.bin
0x10000   storage.bin              (littlefs, 含 config.json)
0x210000  folotoy-firmware-idf.bin (app)
```

**没有 0xF20000 的内容** —— 这是刻意设计：重新烧录整机固件不会覆盖已写入的 SN/Key，产线只需写一次。

---

## 3. 数据模型

### 3.1 TripleTuple 结构

`main/common/utils/config_util.hpp:15`

```cpp
struct TripleTuple {
    std::string product_key;
    std::string device_key;
    std::string device_secret;
    std::string hardware_version;
    std::string lamp_version;
};
```

### 3.2 NVS 中的存储布局

- 分区：`folotoy`
- namespace：`folotoy-key`
- 类型：全部为 `nvs_set_str` 明文字符串

| NVS Key | 结构体字段 | 典型值 | 说明 |
|---|---|---|---|
| `ProductKey` | `product_key` | `"folotoy"` | 产品标识，AT 写入时固定为 `folotoy` |
| `DeviceKey` | `device_key` | `"8cbfea89f22c"` | **SN**，MAC 去冒号小写 |
| `DeviceSecret` | `device_secret` | `"xxxxxxxx"` | **Key / 密码** |
| `HardwareVersion` | `hardware_version` | `"1.2"` | 硬件版本，AT 指令传入 |
| `LampVersion` | `lamp_version` | `"1.1"` | 灯板版本，AT 写入时固定为 `1.1` |

### 3.3 config.json 中的映射

`main/data/config/config-default.json`（以及 `config.json`，共 4 份，见 §7）：

```json
{
  "common": {
    "hardware_ver": "",
    "software_ver": "",
    "lamp_ver": "",
    "productkey": "",
    "devicekey": "",
    ...
  },
  "mqtt": {
    "cid": "",
    "username": "",
    "password": "",
    ...
  }
}
```

回填映射关系：

| NVS | → | config.json 路径 |
|---|---|---|
| `HardwareVersion` | → | `common.hardware_ver` |
| `LampVersion` | → | `common.lamp_ver` |
| `ProductKey` | → | `common.productkey` |
| `DeviceKey` | → | `common.devicekey` |
| `DeviceKey` | → | `mqtt.cid` |
| `DeviceKey` | → | `mqtt.username` |
| `DeviceSecret` | → | `mqtt.password` |

注意 `DeviceKey` 一对三：既是设备标识，也是 MQTT 的 client id 和用户名。

---

## 4. 代码结构

### 4.1 文件清单

| 文件 | 作用 |
|---|---|
| `main/core/setting.hpp` | `Setting` 抽象基类，定义 Get/Set String/Int + EraseKey |
| `main/settings/nvs_setting.hpp/.cpp` | `NvsSetting`：NVS 后端实现，支持指定分区+namespace |
| `main/settings/storage_setting.hpp/.cpp` | `StorageSetting`：littlefs + cJSON 后端，点分路径访问 |
| `main/common/utils/config_util.hpp/.cpp` | `ConfigUtil` 单例：三元组读写校验，日志密钥，RFID UID |
| `main/application.cpp:5568` | AT 指令 `AT+CONFIG=folotoy,...` 写入分支 |
| `main/boards/wifi_board.cpp:38` | WiFi 板首次开机回填 |
| `main/boards/lte_board.cpp:74` | LTE 板首次开机回填 |
| `main/boards/dual_network_board.cpp:218` | 双网板首次开机回填 |
| `main/boards/*/xxx.cpp` | 开机自检调用 `CheckTripleTuple()` |
| `components/folo_at_command/` | AT 指令 UART/USB 收发与解析 |

### 4.2 Setting 抽象基类

`main/core/setting.hpp`

```cpp
class Setting {
public:
    virtual ~Setting() = default;

    virtual std::string GetString(const std::string& key, const std::string& default_value, bool encrypt) = 0;
    virtual void SetString(const std::string& key, const std::string& value, bool encrypt) = 0;
    virtual int32_t GetInt(const std::string& key, int32_t default_value, bool encrypt) = 0;
    virtual void SetInt(const std::string& key, int32_t value, bool encrypt) = 0;
    virtual void EraseKey(const std::string& key) = 0;

protected:
    bool read_write_;
    bool dirty_ = false;
    Setting(bool read_write) : read_write_(read_write) {}
};
```

`dirty_` 标志由子类在写入时置位，析构时据此决定是否 commit。

### 4.3 NvsSetting

`main/settings/nvs_setting.hpp`

```cpp
class NvsSetting : public Setting {
public:
    NvsSetting(const std::string& ns, bool read_write = false);                    // 默认 nvs 分区
    NvsSetting(const std::string& partition_name,
               const std::string& namespace_name, bool read_write);                // 指定分区
    ~NvsSetting() override;

    std::string GetString(const std::string& key, const std::string& default_value = "", bool encrypt = false) override;
    void SetString(const std::string& key, const std::string& value, bool encrypt = false) override;
    int32_t GetInt(const std::string& key, int32_t default_value = 0, bool encrypt = false) override;
    void SetInt(const std::string& key, int32_t value, bool encrypt = false) override;

    bool GetBlob(const std::string& key, std::vector<uint8_t>& out, bool encrypt = false);
    std::vector<uint8_t> GetBlob(const std::string& key);
    void SetBlob(const std::string& key, const void* data, size_t len, bool encrypt = false);

    void EraseKey(const std::string& key) override;
    void EraseAll();

private:
    std::string ns_;
    std::string partition_name_;
    nvs_handle_t nvs_handle_ = 0;
};
```

**RAII 语义**（`nvs_setting.cpp:41`）：

```cpp
NvsSetting::~NvsSetting() {
    if (nvs_handle_ != 0) {
        if (read_write_ && dirty_) {
            ESP_ERROR_CHECK(nvs_commit(nvs_handle_));
        }
        nvs_close(nvs_handle_);
    }
}
```

构造即打开，析构自动 commit + close。调用方只需在作用域内用完即走。

**XOR 混淆（可选）**：`encrypt = true` 时，字符串会先 XOR 再以 blob 形式存（因为 XOR 后可能含 `0x00`，不能用 `nvs_set_str`）。密钥硬编码：

```cpp
// nvs_setting.cpp:7
static const char* NVS_SECRET = "p9Z$mL2#vK8!qX5&bN4*wR7^yT1@jU0%";
```

> ⚠️ 这只是防止 flash dump 后肉眼直读的混淆，不是加密。**三元组本身没有用这个开关，是明文存储的。** 如果目标项目对 SN/Key 保密性有要求，应改用 ESP-IDF 的 NVS Encryption（需要 flash encryption + `nvs_keys` 分区），而非这个 XOR。

**GetString 的坑**（`nvs_setting.cpp:82`）：`nvs_get_str` 返回的 length 含结尾 `\0`，所以读出后要 `pop_back()`，否则字符串尾部会多一个 `\0`，拼接 URL/topic 时会出问题。移植时不要漏掉这段。

### 4.4 ConfigUtil

单例，构造时初始化 `folotoy` 分区：

```cpp
// config_util.cpp:13
ConfigUtil::ConfigUtil() {
    event_group_ = xEventGroupCreate();

    esp_err_t err = nvs_flash_init_partition("folotoy");
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Error initializing NVS partition 'folotoy' (%s)", esp_err_to_name(err));
    }
}
```

对外 API：

```cpp
std::string GetCommonConfigString(const std::string& key);   // 读 folotoy-key 下任意键
TripleTuple GetTripleTuple();                                // 读取全部 5 个字段
bool CheckTripleTuple();                                     // 校验前 3 个字段非空
bool WriteTripleTuple(const TripleTuple& triple);            // 擦除分区后整体写入
```

（同一个类里还有 `LoadLogEncryptKey` / `SaveLogEncryptKey` / `EraseLogEncryptKey` 和 `LoadRFIDUid` / `SaveRFIDUid` / `EraseRFIDUid`，它们用的是**默认 nvs 分区**下的 `log_enc` / `rfid` namespace，与 SN/Key 无关，移植时可按需裁剪。）

---

## 5. 三条主链路

### 5.1 写入链路（产线）

**AT 指令格式：**

```
AT+CONFIG=folotoy,sn=8cbfea89f22c,key=abcdef123456,hardware_ver=1.2
```

**解析**（`components/folo_at_command/folo_at_command.cpp:208`）：

```cpp
if (lower_cmd.find("at+config=") == 0) {
    type = Type::CONFIG;
    parseConfig(cleaned_cmd.substr(10));   // 注意：用 cleaned_cmd 而非 lower_cmd
    return;
}
```

> `AT+CONFIG=` 分支传的是 `cleaned_cmd`（保留大小写），而 `AT+CTRL=` 传的是 `lower_cmd`。因为 SN/Key 可能大小写敏感，**移植时务必保留这个区别**。

`parseConfig` 按 `,` 或 `;` 切分，第一段是 module（这里是 `folotoy`），后面按 `key,value` 成对填入 `params`（`folo_at_command.cpp:236`）。

**处理**（`main/application.cpp:5568`）：

```cpp
} else if (type == FoloATCommand::Type::CONFIG &&
           at_command->getModule() == "folotoy") {
  if (at_command->hasParam("sn") && at_command->hasParam("key") &&
      at_command->hasParam("hardware_ver")) {
    std::string sn = at_command->getParamAsString("sn");
    std::string key = at_command->getParamAsString("key");
    std::string hardware_ver = at_command->getParamAsString("hardware_ver");

    // sn 缺省 → 用 MAC 兜底
    if (sn.empty() || sn == "-1") {
      sn = SystemInfo::GetMacAddress();
      sn.erase(std::remove(sn.begin(), sn.end(), ':'), sn.end());
      ESP_LOGI(TAG, "SN not provided, using MAC address as SN: %s", sn.c_str());
    } else {
      // AT 指令长度受限，统一去冒号
      if (sn.find(':') != std::string::npos) {
        sn.erase(std::remove(sn.begin(), sn.end(), ':'), sn.end());
        ESP_LOGI(TAG, "SN contains colon, converted to: %s", sn.c_str());
      }
    }

    TripleTuple triple = {.product_key     = "folotoy",
                          .device_key      = sn,
                          .device_secret   = key,
                          .hardware_version = hardware_ver,
                          .lamp_version    = "1.1"};
    ConfigUtil &config = ConfigUtil::GetInstance();
    config.WriteTripleTuple(triple);
    ESP_LOGI(TAG, "Config FoloToy Info success");

    StorageSetting flash_setting(StorageType::FLASH, true);
    ESP_LOGI(TAG, "Restore default config");
    flash_setting.RestoreDefaultConfig();     // ← 关键：清空 config.json 触发下次开机回填
    ESP_LOGI(TAG, "All config set success");
  }
}
```

**三个约定：**

1. `sn` 传空或 `-1` → 用设备 MAC 兜底，产线可以不预分配 SN。
2. SN 一律去冒号（AT 指令有长度上限，冒号是浪费）。
3. 写完 NVS 后**必须** `RestoreDefaultConfig()`，把 config.json 里的旧身份清掉，否则第二层还是老数据。

**WriteTripleTuple 实现**（`config_util.cpp:71`）——先擦分区再写，保证幂等，重复烧录不会残留旧键：

```cpp
bool ConfigUtil::WriteTripleTuple(const TripleTuple& triple) {
    nvs_handle_t handle;

    esp_err_t err = nvs_flash_erase_partition("folotoy");
    if (err != ESP_OK) { /* 失败返回 false */ }

    err = nvs_flash_init_partition("folotoy");
    if (err != ESP_OK) { /* 失败返回 false */ }

    err = nvs_open_from_partition("folotoy", "folotoy-key", NVS_READWRITE, &handle);
    if (err != ESP_OK) { /* 失败返回 false */ }

    bool success = true;
    success &= (nvs_set_str(handle, "ProductKey",      triple.product_key.c_str())      == ESP_OK);
    success &= (nvs_set_str(handle, "DeviceKey",       triple.device_key.c_str())       == ESP_OK);
    success &= (nvs_set_str(handle, "DeviceSecret",    triple.device_secret.c_str())    == ESP_OK);
    success &= (nvs_set_str(handle, "HardwareVersion", triple.hardware_version.c_str()) == ESP_OK);
    success &= (nvs_set_str(handle, "LampVersion",     triple.lamp_version.c_str())     == ESP_OK);

    err = nvs_commit(handle);
    if (err != ESP_OK) { success = false; }

    nvs_close(handle);
    return success;
}
```

> 这里直接用裸 `nvs_*` API 而非 `NvsSetting`，是因为需要先做分区级 erase + re-init，`NvsSetting` 的 RAII 封装做不到。

### 5.2 回填链路（首次开机）

三个 Board 基类各有一份**完全相同**的逻辑（`wifi_board.cpp:38`、`lte_board.cpp:74`、`dual_network_board.cpp:218`）：

```cpp
StorageSetting flash_setting(StorageType::FLASH, true);

hardware_ver = flash_setting.GetString("common.hardware_ver", "");
productkey   = flash_setting.GetString("common.productkey", "");
devicekey    = flash_setting.GetString("common.devicekey", "");
cid          = flash_setting.GetString("mqtt.cid", "");
username     = flash_setting.GetString("mqtt.username", "");
password     = flash_setting.GetString("mqtt.password", "");

// 任一字段为空即认为需要回填
if (hardware_ver.empty() || productkey.empty() || devicekey.empty() ||
    cid.empty() || username.empty() || password.empty())
{
    ConfigUtil& config = ConfigUtil::GetInstance();
    TripleTuple triple = config.GetTripleTuple();

    flash_setting.SetString("common.hardware_ver", triple.hardware_version);
    flash_setting.SetString("common.lamp_ver",     triple.lamp_version);
    flash_setting.SetString("common.productkey",   triple.product_key);
    flash_setting.SetString("common.devicekey",    triple.device_key);
    flash_setting.SetString("mqtt.cid",            triple.device_key);
    flash_setting.SetString("mqtt.username",       triple.device_key);
    flash_setting.SetString("mqtt.password",       triple.device_secret);
    ESP_LOGI(TAG, "Config FoloToy Info success");

    flash_setting.SetString("common.software_ver", SOFTWARE_VER);
    ESP_LOGI(TAG, "Config Software Version success");
}
```

触发条件是**或**关系 —— 任意一个字段空就整体重写，容忍部分写入失败的情况。

> 移植提示：这段代码在三个 Board 中重复了三次。新项目建议抽成一个 `SyncTripleTupleToConfig()` 公共函数，放在 `ConfigUtil` 或 Board 基类里。

### 5.3 校验链路（开机自检）

`CheckTripleTuple()` 只校验前 3 个字段非空（`config_util.cpp:53`）：

```cpp
bool ConfigUtil::CheckTripleTuple() {
    NvsSetting nvs_settings("folotoy", "folotoy-key", false);

    std::string product_key   = nvs_settings.GetString("ProductKey");
    std::string device_key    = nvs_settings.GetString("DeviceKey");
    std::string device_secret = nvs_settings.GetString("DeviceSecret");

    bool valid = !(product_key.empty() || device_key.empty() || device_secret.empty());

    if (!valid) {
        ESP_LOGE(TAG, "Triple tuple is incomplete! Missing ProductKey / DeviceKey / DeviceSecret.");
    } else {
        ESP_LOGI(TAG, "Triple tuple check passed.");
    }
    return valid;
}
```

各板自检时调用，失败播报语音（`m301_v12.cpp:517`）：

```cpp
// check sn and key
ConfigUtil &config = ConfigUtil::GetInstance();
if (!config.CheckTripleTuple()) {
    ESP_LOGE(TAG, "ProductKey or DeviceKey is not set, please check the config.json file in the flash");
    app.PlayLocalAudio("/audio/default/three-tuple-error.mp3", true);
}
```

调用点：`m301_v12.cpp:517`、`m501_v15.cpp:1207`、`fn20_v14.cpp:604`、`fn20_v12.cpp:601`、`mn101_v10.cpp:334`。

---

## 6. 下游消费者

移植后需要确认这些地方的取值来源：

### MQTT 鉴权与 topic

`main/protocols/mqtt_protocol.cpp:79`

```cpp
std::string product_key = flash_setting.GetString("common.productkey", "folotoy");
std::string device_key  = flash_setting.GetString("common.devicekey", "folotoy");

topic_post_     = "/user/" + product_key + "/" + device_key + "/thing/event/post";
topic_data_     = "/user/" + product_key + "/" + device_key + "/thing/data/post";
topic_call_ack_ = "/user/" + product_key + "/" + device_key + "/thing/command/callAck";
topic_cmd_      = "/user/" + product_key + "/" + device_key + "/thing/command/call";
topic_post_ack_ = "/user/" + product_key + "/" + device_key + "/thing/event/postAck";
```

`mqtt_protocol.cpp:280`

```cpp
client_id_ = flash_setting.GetString("mqtt.cid", "folotoy");
username_  = flash_setting.GetString("mqtt.username", "folotoy");
password_  = flash_setting.GetString("mqtt.password", "folotoy");
```

### HTTP API

`main/application.cpp:4160`（另有 4181、4198 三处同型）

```cpp
std::string device_sn = flash_setting.GetString("common.devicekey", "");
std::string url = "https://api." + base_domain + "/v1/toys/sn/" + device_sn + ...;
```

### Coze 协议

`main/protocols/coze_protocol.cpp:432`

```cpp
const char* user_id = flash_setting.GetString("common.devicekey", "").c_str();
```

> 注意：这行是**悬垂指针**（临时 `std::string` 析构后 `user_id` 失效）。移植时顺手修掉，改成 `std::string user_id = ...`。

---

## 7. config.json 的四份副本

项目中 config 文件共 **4 份**，改动必须全部同步：

```
main/assets/config/config.json
main/assets/config/config-default.json
main/data/config/config.json
main/data/config/config-default.json
```

- `assets/` 是编译期嵌入（`LoadEmbeddedDefaultJson()` 从二进制读取）。
- `data/` 是打包进 littlefs `storage.bin` 的。
- `config.json` 是用户可写的运行配置，`config-default.json` 是 `RestoreDefaultConfig()` 的模板。

`StorageSetting` 用点分路径访问嵌套 JSON（`"common.devicekey"` → `root["common"]["devicekey"]`），并提供 `CompleteConfigsAddOnly()` 在 OTA/启动时做"只补不改"的字段补全 —— 新增配置项时老设备不会丢用户设置。

---

## 8. 移植清单

按顺序执行：

### 步骤 1 — 分区表

在目标项目的 `partitions.csv` 加一行：

```csv
folotoy,  data, nvs,      <按目标 flash 布局分配>, 0x4000,
```

若目标项目用 `sdkconfig` 里的默认分区表，需先切到 custom partition table（`CONFIG_PARTITION_TABLE_CUSTOM=y`）。

### 步骤 2 — 基础设施

复制以下文件（若目标项目已有等价物则做适配）：

```
main/core/setting.hpp                    ← 基类，无依赖
main/settings/nvs_setting.hpp/.cpp       ← 依赖 setting.hpp + nvs_flash
```

`NvsSetting` 只依赖 `nvs_flash`，可以独立移植。

### 步骤 3 — ConfigUtil

复制 `main/common/utils/config_util.hpp/.cpp`，然后：

- 裁掉不需要的部分（`LoadLogEncryptKey` 系列、`LoadRFIDUid` 系列、`HexToBytes`）。
- 若分区改名，同步替换 `"folotoy"` 与 `"folotoy-key"` 两个字符串（共出现在构造函数、`GetCommonConfigString`、`GetTripleTuple`、`CheckTripleTuple`、`WriteTripleTuple` 中）。
- `event_group_` 成员目前未被三元组逻辑使用，可以删。

确认 `CMakeLists.txt` 的 `REQUIRES` 含 `nvs_flash`。

### 步骤 4 — 第二层存储（二选一）

**方案 A：完整搬 config.json 体系** —— 需要 `storage_setting.hpp/.cpp` + `folo_storage` 组件 + littlefs 分区 + 四份 json。改动大，但获得点分路径、默认值合并、OTA 字段补全等能力。

**方案 B：只要 NVS 一层** —— 业务代码直接调 `ConfigUtil::GetTripleTuple()`，砍掉回填逻辑。改动最小，但每次读取都要开关 NVS handle，且失去"重置配置不丢身份"之外的其他特性。

若走方案 B，把 §5.2 的回填代码整段删除，业务侧改为：

```cpp
TripleTuple triple = ConfigUtil::GetInstance().GetTripleTuple();
client_id_ = triple.device_key;
username_  = triple.device_key;
password_  = triple.device_secret;
```

### 步骤 5 — 写入入口

若目标项目已有 AT/串口指令框架，把 §5.1 的处理逻辑接到对应分支即可。若没有，需要一并移植 `components/folo_at_command/`（依赖 `driver/uart`，支持 UART 与 USB CDC 两种通道）。

也可以换成其他写入通道 —— 本质只是调用 `WriteTripleTuple()`，通道是 UART / BLE / HTTP 都无所谓。

### 步骤 6 — 校验入口

在目标项目的开机自检流程中加：

```cpp
if (!ConfigUtil::GetInstance().CheckTripleTuple()) {
    // 报错 / 播报 / 点灯
}
```

### 步骤 7 — 产线流程验证

1. 烧录固件（merged bin 不含 `folotoy` 分区）
2. 串口下发 `AT+CONFIG=folotoy,sn=xxx,key=xxx,hardware_ver=x.x`
3. 日志出现 `Triple tuple written successfully.` 与 `All config set success`
4. 重启，日志出现 `Config FoloToy Info success`（回填成功）与 `Triple tuple check passed.`
5. 重新烧录固件，确认 SN 仍在（验证分区隔离生效）

---

## 9. 已知问题与改进建议

移植时值得一并处理的点：

| 位置 | 问题 | 建议 |
|---|---|---|
| `config_util.cpp:98-102` | 三元组明文存储，flash dump 可直读 | 有保密需求则启用 NVS Encryption（flash encryption + `nvs_keys` 分区） |
| `nvs_setting.cpp:7` | XOR 密钥硬编码在固件里 | 同上；XOR 只当混淆，不要当加密用 |
| `wifi_board.cpp` / `lte_board.cpp` / `dual_network_board.cpp` | 回填逻辑三处完全重复 | 抽成 `ConfigUtil::SyncToConfig()` 或 Board 基类方法 |
| `coze_protocol.cpp:432` | `.c_str()` 悬垂指针 | 改为 `std::string user_id = ...` |
| `config_util.cpp:75` | `WriteTripleTuple` 先擦分区，掉电窗口内会丢身份 | 可先读旧值备份，或改为只覆盖 5 个键不擦分区 |
| `config_util.cpp:54` | 每次 `CheckTripleTuple` 都重开 NVS handle | 低频调用，可接受；高频场景应缓存 |
| `config_util.hpp:54` | `event_group_` 创建后未使用 | 删除 |
