# Folo Meeting v0.1.1 · AI通行证文案统一

设备称呼统一为 **AI通行证**。本版更新 App、固件内配网页面、README、操作说明和相关验证工具，并同步说明仓库与 Release 已公开。

## 本次变化

- App 的设备连接、配置发送、Wi-Fi 配网、声纹提示和飞书授权提示统一使用「AI通行证」。
- 固件内配网页面和对应自检文案同步更新。
- 安装与构建文档改为公开访问说明，README 指向本版配套附件。
- 固件版本和 APK 版本更新为 0.1.1，Android versionCode 递增为 2。

## 下载与升级

| 文件 | 用途 |
| --- | --- |
| `folo-meeting-v0.1.1-android.apk` | Android 8.0+；沿用当前试用签名，可覆盖同签名旧版 |
| `folo-meeting-v0.1.1-esp32c3-app.bin` | 兼容的 8 MB ESP32-C3 AI通行证应用固件，只写入 `0x10000` |
| `SHA256SUMS.txt` | SHA-256 校验值 |
| `release-manifest.json` | 源码提交、版本、签名指纹与文件清单 |

[安装与烧录](https://github.com/1ikeMcFlurry/folo-meeting/blob/v0.1.1/doc/meeting/INSTALL.md) · [完整使用说明](https://github.com/1ikeMcFlurry/folo-meeting/blob/v0.1.1/README.md)

固件是应用升级包，保留设备身份、网络与记录；不要擦除整片 Flash。本版 APK 仍为试用签名，尚未配置商店正式发行签名。

## 验证与边界

本版重新构建 APK 和会议固件，运行 Android 单元测试与 lint，并检查文案替换范围和 APK 签名。未因此再次刷机、录音或修改云端声纹。

既有短录音、BLE 配网、会后候选确认的真机验证可参考项目说明。长会议稳定性、多人声纹准确率及此前一次起始重启的根因仍待验证；声纹候选继续需要人工确认。
