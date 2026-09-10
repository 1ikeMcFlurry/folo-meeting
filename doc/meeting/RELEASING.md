# 配套发布流程

固件和 App 使用同一个仓库版本标签。首个发布为 `v0.1.0`，标记为 Pre-release，面向当前硬件试用。

当前更新为 `v0.1.1`：设备称呼统一使用「AI通行证」，Android `versionCode` 递增为 2，仓库和附件已公开。下面保留首发流程示例；发布新版本时替换为实际版本号，不覆盖旧附件。

## 发布内容

- `folo-meeting-v0.1.0-android.apk`：使用现有试用签名、关闭可调试标记的 Android 安装包。
- `folo-meeting-v0.1.0-esp32c3-app.bin`：ESP32-C3 应用镜像，只升级 `0x10000` 应用分区。
- `SHA256SUMS.txt`：两个二进制文件与发布 manifest 的校验值。
- `release-manifest.json`：版本、源码提交、构建参数、签名证书指纹、文件大小与 SHA-256。

GitHub 另外自动提供标签对应的源码 ZIP / tar.gz。二进制放在 Release Assets 中，不加入源码 Git 历史。

## 版本规则

发布前保持以下信息一致：

| 位置 | 字段 |
| --- | --- |
| 根 `CMakeLists.txt` | `PROJECT_VER` |
| `companion/android/app/build.gradle` | `versionName`；新安装版本还应递增 `versionCode` |
| Git 标签 | `v<版本>` |
| Release 附件 | 文件名中的版本 |
| Release manifest | 版本和实际源码提交 SHA |

设备现有状态协议不回报完整版本组合，更新时通过 Release 文件与 manifest 追踪配套关系。涉及 BLE 字段、NVS 格式和草稿结构变化时，还需单独说明升级 / 回退兼容性。

## 构建与核验

1. 在待发布提交上，按 [BUILD.md](BUILD.md) 重新构建会议固件与 App，运行 Android 单元测试和 lint。
2. 确认使用 `--companion` 生成的固件，应用大小不超过 `0x300000`，芯片为 ESP32-C3。
3. 用 `apksigner verify --print-certs` 检查 APK，确认使用预期签名；不要上传签名私钥。
4. 确认 APK `debuggable=false`，R8 的敏感 SDK 日志清理规则仍有效。
5. 完成与改动相称的真机验证，记录哪些是此前实测、哪些是这次重新执行，不能把构建成功当成完整硬件验收。
6. 提交源码，生成包含该提交 SHA 的 manifest，计算 SHA-256。
7. 本地标签指向同一提交，推送源码与标签，上传附件，最后回读校验附件大小和哈希。

签名私钥、服务凭证、网络密码、真实录音、手机导出数据和完整日志不进入源码仓库或 Release。设备 `cardid` 分区镜像也不属于公开或共享发布包。

## GitHub CLI 示例

提交源码后，可用标准库打包脚本生成附件与 manifest：

```powershell
python tools/meeting_trial/package_release.py --version 0.1.0 --apk-certificate-sha256 <apksigner验证后的64位证书SHA256>
```

脚本检查工作区干净、APK 输出版本、固件芯片 / 版本 / 分区大小，复制构建产物并生成校验文件；它不构建、不签名、不烧录、不上传。需要先完成上面的构建与签名核验。文件生成在被 Git 忽略的 `releases/v0.1.0/`，同名目录存在时拒绝覆盖。

以下命令仅供已完成构建和审核后的维护者执行，版本和提交应使用实际值：

```powershell
git tag -a v0.1.0 -m "Folo Meeting v0.1.0 trial"
git push origin main
git push origin v0.1.0
gh release create v0.1.0 --repo 1ikeMcFlurry/folo-meeting --verify-tag --prerelease --title "Folo Meeting v0.1.0 · 会议与声纹试用版" --notes-file RELEASE_NOTES.md
gh release upload v0.1.0 releases/v0.1.0/folo-meeting-v0.1.0-android.apk releases/v0.1.0/folo-meeting-v0.1.0-esp32c3-app.bin releases/v0.1.0/SHA256SUMS.txt releases/v0.1.0/release-manifest.json --repo 1ikeMcFlurry/folo-meeting
```

自动化发布可以先创建 Draft，上传并检查附件后再转为已发布，以免用户下载到不完整的配套文件。不要覆盖已经发布的同名二进制；修正代码或包应发布新版本。

## v0.1.0 的验证来源

功能验证继承自 2026-09-10 的实际开发与真机测试：BLE 配网、19 秒实录、独立片段声纹匹配、确认姓名与草稿保存。首次仓库发布在整理后的目录重新构建 APK、固件，重新运行 JVM 测试与 lint；没有因此再次录音或修改云端声纹。

发布固件的应用元数据版本统一为 `0.1.0`。此前设备测试使用相同功能实现及旧工程版本元数据；不要将其描述为本次发布二进制重新刷机验收。当前仍保留一次录音起始重启未查明、长会议和多人识别未完成验收的已知限制。
