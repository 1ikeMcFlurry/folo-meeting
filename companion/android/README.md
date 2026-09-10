# FoloToy 会议 · Android

原生 Java App，Android 8.0 / API 26 及以上。设备管理、会议记录、服务设置构成主导航，配网、声纹和编辑页按父级返回。

完整使用流程见 [项目 README](../../README.md)，构建见 [BUILD.md](../../doc/meeting/BUILD.md)。

## 主要实现

| 模块 | 入口 |
| --- | --- |
| 页面、设置与编辑 | `MainActivity`、`BrandActivity`、`BrandUi` |
| 安全存储 | `Vault`，Android Keystore + 加密本地文件 |
| AI通行证连接 | `BleClient` |
| 乐鑫官方 BLE 配网 | `WifiProvisionActivity`、`WifiProvisionRules` |
| 听悟调用 | `Tingwu`、`AcsSigner`、`CloudHttp` |
| 声纹注册与恢复 | `VoiceprintActivity`、`Voiceprints`、`VoiceprintClient` |
| 会后声纹识别 | `MeetingSpeakerActivity`、`SpeakerSamples` |
| 云端音频取样 | `MeetingAudio`、`PcmWindow` |
| 编辑稿与飞书文档 | `MeetingDraft`、`MeetingDocument`、`DocumentWriter`、`Feishu` |

## 使用边界

当前先接入 **阿里云听悟**和**讯飞声纹识别（新版）**两家语音服务。听悟负责转写、区分不同发言人并生成 AI 纪要，结果中的“发言人 1、2……”是本场编号；讯飞负责声纹库的创建、录入和管理，以及将本场声音与已登记声纹匹配。

App 将讯飞匹配结果对应到本机保存的姓名，显示候选，由用户确认后应用到编辑稿。不配置讯飞也能区分发言人、获取纪要并手动改名；要通过声纹库匹配具体人员身份，当前实现需要配置讯飞。飞书是另外的文档归档目标。

注册声纹不会直接修改已有会议，取样与云查询需要用户在会后主动执行。低相似度候选不自动选中，也不作为身份认证结果。

账号设置、原稿、编辑稿和候选报告保存在手机。手机与设备独立持有飞书授权，不复用同一条轮换 refresh token。云端音频在内存中分段读取，不生成本地会议录音文件。

飞书导出先预览再发布；纪要上限 5 万字符、逐字稿 50 万字符、5000 个排版块，超限明确提示。更新时先写入并校验，再移除旧版内容；检测到他人修改或附加结构时停止直接覆盖。

## 构建与测试

```powershell
# 从仓库根目录；JAVA_HOME 指向 JDK 17，ANDROID_HOME 指向 SDK
python tools/meeting_trial/build_android.py
```

输出为 `app/build/outputs/apk/debug/app-debug.apk`。当前试用包使用 debug 签名，但关闭可调试标记并启用 R8 去除配网 SDK 日志；勿以 `run-as` 导入配置。

`src/test` 提供 JVM 单元测试，`src/androidTest` 提供仅使用合成 MP3 的真机编解码测试。详细命令、签名与安装说明见构建文档。
