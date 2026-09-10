# 从源码构建

本仓库支持分别构建 Android App 与会议固件。开发环境路径通过环境变量或参数传入，主构建入口不依赖原开发机的盘符。

## 获取源码

```powershell
gh repo clone 1ikeMcFlurry/folo-meeting
cd folo-meeting
```

仓库为私有，需要先完成 GitHub 登录。源码仓库不包含设备配置、API 密钥、签名私钥、真实录音或用户编辑稿。

## Android

### 工具链

| 组件 | 版本 |
| --- | --- |
| JDK | 17 |
| Gradle | 8.13，仓库自带 Wrapper 并锁定分发包 SHA-256 |
| Android Gradle Plugin | 8.13.2 |
| Android SDK Platform | 36 |
| Build Tools | 35.0.0；以 AGP 的实际选择为准 |
| 最低 / 目标系统 | minSdk 26 / targetSdk 35 |
| 乐鑫配网 Android SDK | `lib-2.4.4` |

安装 Android Studio 的 SDK Manager 或官方命令行工具，准备 SDK Platform 36、Build Tools 35.0.0、Platform Tools，并接受本机 SDK 许可。构建需要访问 Google Maven、Maven Central、JitPack 和 Gradle 分发站点。

在 PowerShell 中设置自己的安装路径：

```powershell
$env:JAVA_HOME = 'C:/path/to/jdk-17'
$env:ANDROID_HOME = 'C:/path/to/Android/Sdk'
python tools/meeting_trial/build_android.py
```

`python` 只使用标准库，不需要为 Android 构建安装 Python 依赖。也可用 Android Studio 打开 `companion/android`，让 IDE 创建本机 `local.properties`，此文件不入库。

构建脚本执行 `assembleDebug`、`testDebugUnitTest` 和 `lintDebug`。直接用 Wrapper 也可以：

```powershell
cd companion/android
./gradlew.bat :app:assembleDebug :app:testDebugUnitTest :app:lintDebug
```

macOS / Linux 使用 `./gradlew`。发布开发机上的完整构建与真机验证使用 Windows；其他宿主平台未做完整实测。

| 产物 | 相对仓库路径 |
| --- | --- |
| APK | `companion/android/app/build/outputs/apk/debug/app-debug.apk` |
| 单元测试报告 | `companion/android/app/build/reports/tests/testDebugUnitTest/index.html` |
| lint 报告 | `companion/android/app/build/reports/lint-results-debug.html` |
| 脚本构建日志 | `build/meeting-android-build.log` |

### 签名与日志

当前试用 APK 使用构建机 debug 签名，但 `debuggable=false`，同时启用 R8 去除第三方配网 SDK 中可能带凭证的 Android Log 调用。请保留这一组合，不要为了方便调试直接取消日志优化后分发。

仓库不会上传当前签名私钥。其他机器自动生成的 debug 签名通常不同，无法覆盖原试用 APK；正式发行需要管理独立签名密钥及后续升级策略。目前 `release` buildType 没有配置签名，不能把未签名输出直接发给用户安装。

### 测试

60 项 JVM 测试覆盖文档、草稿、Wi-Fi 规则、声纹与音频取样。单独执行：

```powershell
cd companion/android
./gradlew.bat :app:testDebugUnitTest :app:lintDebug
```

真机编解码测试使用仓库中的 **合成** MP3，不调用麦克风或云服务：

```powershell
./gradlew.bat :app:assembleDebugAndroidTest
adb install --no-streaming -r app/build/outputs/apk/debug/app-debug.apk
adb install --no-streaming -r app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
adb shell am instrument -w com.folotoy.meeting.test/com.folotoy.meeting.AudioInstrumentation
adb uninstall com.folotoy.meeting.test
```

最后一条仅卸载测试包，保留主 App。连接多台设备时显式指定 adb serial。测试合成音频可用 `tools/meeting_trial/generate_decoder_fixture.py` 重新生成，需要 FFmpeg；日常构建无需重新生成。

## ESP32-C3 会议固件

### 环境准备

安装 **ESP-IDF v5.5.3** 及 ESP32-C3 工具链，使用其安装器 / 安装脚本创建的 Python 虚拟环境。启用 ESP-IDF 终端环境，确认 `IDF_PATH` 和 `IDF_TOOLS_PATH` 指向本机安装目录。

头像资源构建需要 Pillow，请安装到当前 ESP-IDF 虚拟环境，不要全局安装：

```powershell
python -m pip install "Pillow>=10,<13"
python tools/meeting_trial/build_trial.py --companion
```

也可以显式指定工具路径：

```powershell
python tools/meeting_trial/build_trial.py --companion --idf C:/Espressif/esp-idf-v5.5.3 --tools C:/Espressif
```

脚本使用当前 Python 环境，生成独立 `build/meeting_companion/sdkconfig`，合并根配置与会议配置，再调用官方 `idf.py`。`dependencies.lock` 锁定组件版本；`managed_components/` 在首次构建时由组件管理器恢复。

主要固件依赖包括乐鑫 `network_provisioning 1.2.4`、NimBLE、ESP WebSocket Client、LVGL 与音频驱动，实际完整版本以依赖锁文件为准。

### 输出与配置

| 文件 / 目录 | 用途 |
| --- | --- |
| `build/meeting_companion/trae_card.bin` | 会议应用镜像，Release 使用此文件 |
| `build/meeting_companion/trae_card.elf` | 本地调试符号，不作为设备刷写文件 |
| `build/meeting_companion/sdkconfig` | 本次会议固件的最终配置 |
| `tools/meeting_trial/sdkconfig.defaults` | 会议实时推流与内存配置 |
| `tools/meeting_trial/sdkconfig.companion` | 独立 App 控制、BLE 配网与安全配置 |
| `partitions.csv` | 应用起点 `0x10000`、大小 `0x300000`；整板要求 8 MB Flash |

内部工程名 `trae_card` 沿用上游，Release 文件会重命名为 `folo-meeting-<版本>-esp32c3-app.bin`。应用版本由根 `CMakeLists.txt` 的 `PROJECT_VER` 控制。

`--normal` 用于检查原工牌功能，输出至 `build/normal_validation`；不带 `--companion` 时是早期会议实验配置。这两种输出都不能冒充 Android 配套会议固件发布。

**构建命令不会烧录设备。** 安装时仅升级应用分区，见 [安装与烧录](INSTALL.md)。不要把普通 `idf.py flash` 当作本 Release 的保留数据升级命令。

## 代码修改定位

| 修改目标 | 主要入口 |
| --- | --- |
| App 页面 / 返回逻辑 | `MainActivity.java`、`BrandActivity.java` |
| 品牌色与按钮样式 | `BrandUi.java`、`app/src/main/res/values/` |
| 乐鑫配网 | `WifiProvisionActivity.java`、`meeting_provision.c` |
| 设备控制协议 | `BleClient.java`、`meeting_ble.c`、`meeting_companion.c` |
| 听悟任务与音频 | `Tingwu.java`、`MeetingAudio.java`、`meeting_cloud.c` |
| 声纹与取样 | `Voiceprints.java`、`VoiceprintClient.java`、`SpeakerSamples.java`、`MeetingSpeakerActivity.java` |
| 纪要编辑与导出 | `MeetingDraft.java`、`MeetingDocument.java`、`DocumentWriter.java`、`Feishu.java` |
| 录音状态与设备显示 | `meeting_trial.c`、`ui_meeting_trial.c` |

Java 文件位于 `companion/android/app/src/main/java/com/folotoy/meeting`；平台 C 文件位于 `components/platform/platform_esp32/src`，设备 UI 位于 `components/ui/presentation/src`。

`tools/meeting_trial` 中保留了历次实验和本机 UI 自动化工具，部分工具依赖本机端口、已配置账号或特定手机，不属于无需配置即可运行的通用测试。首次开发从本文两个构建入口开始。
