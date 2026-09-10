# 会议固件与 App 开发工具

当前产品流程与安装方法以 [根 README](../../README.md) 为准。目录名称沿用开发阶段命名；部分脚本是早期接口探索或特定手机的验证工具。

## 日常入口

| 工具 | 用途 |
| --- | --- |
| `build_android.py` | 使用 Gradle Wrapper 构建 App、执行 JVM 单元测试和 lint |
| `build_trial.py --companion` | 使用 ESP-IDF v5.5.3 构建配套会议固件 |
| `package_release.py` | 校验配套版本并生成 Release 附件、manifest 与 SHA-256 文件 |
| `sdkconfig.defaults` / `sdkconfig.companion` | 会议固件与独立控制模式的配置覆盖 |
| `recorder_diagnostics.py` | 串口诊断，只记录允许的状态与崩溃标记 |
| `generate_decoder_fixture.py` | 生成真机解码测试用合成音频；需要 FFmpeg |

完整环境设置见 [BUILD.md](../../doc/meeting/BUILD.md)。构建日志位于仓库 `build/`，不提交 Git。

## 需要本机适配的验证工具

`provision_check.py`、`navigation_check.py`、`meeting_speaker_ui_check.py` 等用于已连接手机的 UI 验证；部分脚本仍保留开发机的 adb 路径、设备序列号或预期页面。运行前先阅读源代码并适配本机，避免误操作其他设备。可能请求录音或云服务的工具需要真实账号和明确测试意图。

`seed_android.py` 属于可调试开发包导入工具。当前发布 APK 已关闭可调试标记，不能通过它或 `run-as` 导入配置；请在 App 中填写凭证。

## 历史实验

`tingwu_trial.py`、`device_trial.py`、`cloud_capture.py`、`export_feishu.py` 等保留早期方案评估与验证能力，参见 [早期实验记录](README.experiments.md)。其中的服务开通状态、价格、测试结果和开发进度是当时记录，不代表当前产品默认流程或最新云服务权益。

Python 实验依赖位于 `requirements.txt`，应安装到项目虚拟环境。只构建 Android 不需要这些依赖；固件使用已安装 ESP-IDF 的 Python 环境。

设备备份、账户配置、真实录音、导出稿、截图和本机测试证据不随本仓库发布。
