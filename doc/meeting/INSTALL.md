# 安装与固件升级

适用版本：Folo Meeting v0.1.1 试用版。请从同一条 Release 下载 APK 与固件。

## 1. 下载与校验

本仓库已公开，可以直接在 [Releases](https://github.com/1ikeMcFlurry/folo-meeting/releases) 页面下载，也可使用已配置的 GitHub CLI：

```powershell
gh release download v0.1.1 --repo 1ikeMcFlurry/folo-meeting --dir release-v0.1.1
Get-FileHash release-v0.1.1/folo-meeting-v0.1.1-android.apk -Algorithm SHA256
Get-FileHash release-v0.1.1/folo-meeting-v0.1.1-esp32c3-app.bin -Algorithm SHA256
```

将输出与下载的 `SHA256SUMS.txt` 比较。版本与源码提交可在 `release-manifest.json` 中核对。

## 2. 安装 App

支持 Android 8.0 / API 26 及以上，需要 BLE。将 APK 传到手机后打开安装，按系统提示允许当前文件来源安装应用。也可以使用 Android SDK Platform Tools：

```powershell
adb devices
adb install --no-streaming -r release-v0.1.1/folo-meeting-v0.1.1-android.apk
```

多台手机连接时在 `adb` 后添加 `-s <设备序列号>`。`-r` 用于覆盖安装并保留数据，前提是包名和签名一致。当前包名为 `com.folotoy.meeting`。

本 Release 沿用当前试用签名，关闭可调试标记并启用 R8，不是应用商店正式发行包。自行构建的 debug 签名通常不同；遇到 `INSTALL_FAILED_UPDATE_INCOMPATIBLE` 先核对签名。卸载会丢失 App 内保存的设置、编辑稿和声纹姓名映射，不要将卸载作为默认升级步骤。

APK 没有内置测试账号。安装后在 App 填入自己的听悟、讯飞和飞书配置。

## 3. 确认硬件兼容

| 项目 | 本版要求 |
| --- | --- |
| 芯片 | ESP32-C3 |
| Flash | 8 MB；分区表末端超出 4 MB |
| 屏幕 | ST7789P3，240 × 320，项目定义的 SPI 引脚 |
| 音频 | ES8311，项目定义的 I2C / I2S 引脚 |
| 按键 | GPIO0 ADC 三键：上、下、确定 |
| 已有布局 | 与仓库 `partitions.csv` 兼容；应用起点 `0x10000`，大小 `0x300000` |

这是基于现有工牌硬件的应用升级包。**不要把它当成通用开发板固件或空白芯片全量镜像。** 新板首次初始化需要另外准备 bootloader、分区表、基础资源和每台设备独有的身份材料，按原硬件生产流程处理。

## 4. 只升级应用分区

停止录音，关闭占用串口的监视器，使用稳定 USB 供电。以下以 `COM3` 为例，请替换为实际端口。

先在项目虚拟环境或 ESP-IDF 自带 Python 环境中准备 `esptool` 4.x。不要安装到全局 Python：

```powershell
py -m venv .venv
.venv/Scripts/python.exe -m pip install "esptool>=4.11,<5"
.venv/Scripts/python.exe -m esptool --chip esp32c3 --port COM3 flash_id
```

确认显示 8 MB，且设备现有分区布局符合上表，再升级：

```powershell
.venv/Scripts/python.exe -m esptool --chip esp32c3 --port COM3 --baud 460800 write_flash --flash_size detect 0x10000 release-v0.1.1/folo-meeting-v0.1.1-esp32c3-app.bin
```

该命令只写应用范围，不写 NVS、`cardid`、bootloader 或分区表。应用镜像必须小于等于 `0x300000` 字节。不要使用 `erase_flash` / `erase-flash`；`cardid` 内有设备身份，不能用公共 Release 文件恢复。

若设备设置了 Secure Boot、Flash 加密或非本项目分区布局，先使用该设备自己的升级流程，不直接套用上面的命令。

## 5. 升级后检查

1. 设备能正常进入 FoloToy 会议页面。
2. 长按上键，手机能配对并连接。
3. 检查设备原 Wi-Fi 与服务配置；必要时通过 App 重新发送。
4. 开启所需的会后音频选项，录一场 30–60 秒测试。
5. 获取最终纪要、核对逐字稿；需要声纹时测试候选确认。

## 回退

保留已知可用的上一版 APK 和应用 BIN。固件可按相同 `0x10000` 地址回刷兼容旧版本，仍然保留设备数据分区。App 回退受 Android 版本号、签名和数据兼容规则限制，不承诺所有跨版本回退都能直接安装；不要为回退擅自清空用户数据。

会后音频功能需要 App 和固件同时支持，升级或回退时优先使用同一 Release 的配套文件。
