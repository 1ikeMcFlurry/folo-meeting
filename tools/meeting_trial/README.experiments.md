# 两条会议纪要试验链路

当前已进入[Wi-Fi 独立录音、蓝牙配置与安卓会后编辑](../../doc/specs/2026-09-09-meeting-wifi-ble-companion.md)原型实施。使用 `build_trial.py --companion` 构建独立设备固件；安卓说明见 [App README](../../companion/android/README.md)，新链路验收见 [协作版实测](RESULT-COMPANION-2026-09-09.md)。以下仍保留之前电脑控制任务的试验记录。

更新时间：2026-09-09。听悟免费试用已开通，项目「Folo会议流式试验」当前含屏幕、AP 配网和批次发送的修正版已完整上传 **180 秒、5,760,000 字节**，实时文字与会后摘要均返回，设备回读“纪要已生成”状态。当前为电脑控制任务的实验固件，尚未完成完全独立设备与多厂商产品化；长会议、弱网和正式纪要质量仍待验收。[首轮转写和摘要](RESULT-2026-09-08.md)；[当前版本复测](RESULT-2026-09-09.md)。

## 本轮实板进展

飞书试验暂缓，先做阿里听悟。COM3 原固件 1.0.3 的 INFO、I2C、AUDIO 自检通过；esptool 实测 ESP32-C3 revision 1.1、8 MB Flash。已备份原应用，并仅向 `0x10000` 写入测试应用，保留实板分区表与设备身份。

新增默认关闭的 `CONFIG_FOLO_MEETING_TRIAL` 固件入口；试验模式启动会议状态页、实体按键、音频、USB 控制与 Wi-Fi/WSS。**设备开机显示中文待机页，录音时显示红色状态、计时和真实麦克风音量条；按确定键可提前结束。** USB 只传控制、识别文本及主动请求的屏幕截图；电脑创建/结束云任务，因此这轮尚不等于设备脱离电脑独立管理整个会议。

官方 ESP-IDF 已安装于 `D:/Codex/esp-idf-v5.5.3` 与 `D:/Codex/espressif`，Python 依赖按 IDF 官方约束安装于本项目 `.venv`。它们是后续开发环境，保留供继续构建。构建使用 `build/meeting_trial/sdkconfig`，不覆盖仓库主配置；`build_trial.py --normal` 验证普通工牌构建。

云端凭证配置在本目录被 Git 忽略的 `.env`，可用同一阿里云主账号或其已授权 RAM 用户的 AccessKey；AppKey 已填写。所需字段为 `ALIBABA_CLOUD_ACCESS_KEY_ID`、`ALIBABA_CLOUD_ACCESS_KEY_SECRET`、`TINGWU_APP_KEY`。设备 Wi-Fi 优先通过下述 AP 页面配置；保存后，控制脚本不再下发 `.env` 中的 Wi-Fi 密码。`MEETING_WIFI_SSID`、`MEETING_WIFI_PASSWORD` 保留为 USB 实验配置的兼容路径。凭证状态检查只输出字段是否已设置，不回显值。可选的 `tingwu-ram-policy.json` 只授予创建/结束任务和查询结果；听悟目前不支持把 RAM 权限进一步限制到单一 AppKey。

```text
.venv/Scripts/python.exe tools/meeting_trial/device_trial.py --check-config
.venv/Scripts/python.exe tools/meeting_trial/build_trial.py
# 先确认编译成功、备份设备原应用并核对分区，再仅烧录试验 app；禁止 erase-flash。
.venv/Scripts/python.exe tools/meeting_trial/device_trial.py --port COM3 --seconds 60
```

Python 侧共 27 项测试通过，含旧固件或未完成 AP 配网时不创建云任务、串口发送失败仍结束任务、串口半行保留、长期密钥不进入设备配置、使用设备已保存 Wi-Fi 时不发送环境密码、提前停止时核对采集与发送帧数、完成回执查询及不同任务回执隔离。另行完成实际编译、烧录和云端转写验证；各项证据见实测报告。

## 2026-09-09：时长配置与飞书在线文档

已将设备及电脑控制协议的五分钟限制调整为 **1..86,400 秒**，通过 `--seconds` 指定。默认仍为 60 秒试录，确定键可提前停止；超过一小时的屏幕计时采用 `HH:MM:SS`。固件回报 `max_recording_seconds`，电脑对仍只有五分钟能力的旧固件在创建云任务前拒绝长时请求。当前完整耐久实测仍为三分钟，24 小时是配置/厂商上限，不能当作硬件验收结果。

```text
# 示例命令，不会在构建或开机时自动开始一小时录音。
.venv/Scripts/python.exe tools/meeting_trial/device_trial.py --port COM3 --seconds 3600 --feishu
```

`--feishu` 在设备确认音频完整、听悟摘要非空后，用电脑上配置好的飞书 CLI 用户身份创建在线文档，并回读实际摘要验证。使用者需要自行配置云账号和飞书授权。原始音频仍不落设备/电脑，也不附加到飞书；只生成文字摘要和待办。

新录音默认标题为 `日期时间｜会议主题`，例如 `2026-09-09 14:25｜产品需求评审`；时间来自电脑收到设备开始录音回执的时刻，主题来自听悟摘要。也可以在首次发布前用 `--title` 指定完整标题：

```text
.venv/Scripts/python.exe tools/meeting_trial/device_trial.py --port COM3 --seconds 3600 --feishu --title "产品需求评审"
```

已完成任务也可单独准备/导出：

```text
.venv/Scripts/python.exe tools/meeting_trial/export_feishu.py <TASK_ID>
.venv/Scripts/python.exe tools/meeting_trial/export_feishu.py <TASK_ID> --publish
# 手动指定完整标题：
.venv/Scripts/python.exe tools/meeting_trial/export_feishu.py <TASK_ID> --title "产品需求评审" --publish
```

第一条只准备 Markdown；第二条创建并回读。单独导出会读取已完成的云任务，不等于已经核对设备采集完整性，判断是否完整仍应依据设备回执。`exports/<TASK_ID>/minutes.md` 和 `receipt.json` 保留作可查看的文本与导出去重记录，并被 Git 忽略。创建结果不确定时停止自动重试；已创建后重试只查询同一份文档。

历史任务没有已保存的录音时间时，默认使用 `导出 日期时间｜会议主题`，避免把导出时间当成会议时间。已创建的文档可直接在飞书改名；脚本不重新创建文档，也不会覆盖用户后来修改的标题。首次创建后会回读标题、任务 ID 和实际摘要。

真实 CLI 导出及原文档改名已通过：[2026-09-09｜会议录音测试（3分钟）](https://huitong-tech.feishu.cn/docx/Ncz0dBpYHoKPpZxbHGnc4ykZnyd)。这是一份普通飞书在线文档，原生妙记路线继续暂缓。包含时长、长字节计数、旧固件兼容、内容转义、凭证拒绝、导出去重、标题命名与回读检查的 Python 测试共 **41 项**通过。

用户已选择“手机网页配置、设备随后独立工作”的产品方向；手机云账号设置、设备自行鉴权/创建/结束任务和设备直连飞书尚未实现。费用由使用者配置的账号承担，出厂固件不带共享付费凭证。[具体页面、屏幕与实施方案](../../doc/specs/2026-09-09-meeting-self-service.md)。

进一步确定“一个硬件 + 手机、设备直接调用飞书”的主路径。已核对安克录音豆说明书及飞书官方 Device Flow 源码；无需自建回调服务器的授权流程是待实板验证的候选，不能把原生录音豆接入或扫码创建应用的可用性当作已经验证。[竞品参考与飞书直连方案](../../doc/specs/2026-09-09-meeting-feishu-direct.md)。

## AP 配网

1. 首次开机，或开机连接已保存网络失败时，设备显示热点名和配网地址。热点名为 `Folo-Meeting-` 加设备 AP MAC 后四位；按用户要求，配网热点开放，连接无需密码。
2. 手机直接连接屏幕上的热点，系统检测到配网入口后弹出页面，部分安卓系统会显示“登录网络”通知。保持使用该网络，即使手机提示它没有互联网。未弹出时可点“登录网络”，或浏览器打开 `http://192.168.4.1`。更新固件后需要断开再连接热点，让手机重新获取 DHCP 配置和执行检测。
3. 选择附近的 2.4 GHz Wi-Fi，也可手动填写隐藏网络；输入密码，点击“验证并保存”。页面全部嵌入固件，无需互联网下载资源。
4. 设备拿到目标网络 IP 后才写入配置，并回读核对；连接失败会保留原配置，页面允许重试。成功后等待约 5 秒关闭设备热点，回到待机页。
5. 后续开机自动连接已保存 Wi-Fi。长按设备上键可重新配网；如果正在录音，会先结束当前录音再进入配网。

如果操作配网的手机同时提供目标热点，切换 Wi-Fi 时可能关闭原热点；实测可用另一部手机配网，或选择路由器网络。

只将联网配置保存到 NVS 的独立 `meeting_wifi/network` 项，未启用 NVS 加密。设备身份 `cardid` 不改动，音频仍只在 RAM 中周转。配网服务仅接受设备 AP 网段的请求，使用页面令牌校验提交，不回传路由器密码；开始录音前关闭 HTTP/DNS 服务，释放内存。

自动发现使用 DHCP 下发设备 DNS（Option 6）、DNS A 查询统一返回 AP 地址、HTTP 检测请求返回带正文且禁止缓存的 302 跳转。HTTP/DNS 就绪后才开启热点，避免漏掉手机首次联网探测；涵盖 iOS 的 `hotspot-detect.html`、Android 的 `generate_204`、Windows 的 `connecttest.txt` 及其他 HTTP 路径。USB 状态仅回传服务就绪、连接数和请求计数，不记录手机地址或访问域名。用户已现场确认手机自动弹出并可配网，设备也已回读配置保存和联网成功。

已修复手动访问也返回 403 的问题：IDF 在启用 IPv6 时使用双栈 HTTP 监听，IPv4 客户端由 `getpeername()` 返回为 IPv4-mapped IPv6 地址。现在使用足够大的地址结构并规范化地址，再校验客户端网段和设备 AP 接口。

设备内 HTTP 自检通过 USB 发起，不切换电脑 Wi-Fi。它验证首页、状态接口、三类系统检测跳转及无效令牌拒绝；只读状态或发送必定被拒绝的空配置请求，不修改设备网络。它验证设备真实 HTTP 监听和地址校验，手机无线链路及自动弹窗仍须现场确认：

```text
.venv/Scripts/python.exe -X utf8 tools/meeting_trial/portal_probe.py --port COM3
```

实现依据：[ESP-IDF 官方 captive portal 示例](https://github.com/espressif/esp-idf/tree/v5.5.3/examples/protocols/http_server/captive_portal)、[Android 联网检测说明](https://developer.android.com/about/versions/11/features/captive-portal)、[Apple 强制门户网络说明](https://support.apple.com/en-gb/102554)。当前离线设备使用 DNS/HTTP 探测方式；没有把普通 HTTP 页面当作需要可信 HTTPS 的 RFC 8908 CAPPORT API 来声明。

开发预览：`.venv/Scripts/python.exe tools/meeting_trial/portal_preview.py`，仅监听电脑回环地址，模拟网络列表和成功/失败，不能用来证明实板已配网。实板现已验证手机自动弹页、提交联网配置、保存后自动关闭热点，以及重启后自动连接已保存 Wi-Fi。

## 设备页面与操作

- 开机：先连接已保存的 Wi-Fi；无配置或连接失败时进入 AP 配网页，连接成功后待机。由电脑运行 `device_trial.py` 发起试录。
- 连接：先显示连接网络，再显示连接听悟；尚未采集会议音频。
- 录音：红色“录音中”、采集时长及麦克风峰值音量条，每 200 ms 更新。不是随机动画。
- 停止：按确定键或达到脚本指定时长；停止新采集，排空 RAM 中已采集的帧，等待听悟确认。上传完成后显示“录音已结束”。
- 会后：电脑继续查询云任务，实际取得非空摘要后回传状态，屏幕显示“纪要已生成”。空结果、查询失败不会假报成功；录音失败也不会被后来的摘要覆盖成成功。
- 状态回读：电脑每 10 秒发送 `?`，设备录音时返回进度，空闲时重发最近一场的完成计数。回执带任务 ID，防止误用上一场结果。回执只放 RAM，重启后不保留。

实时识别消息先在固定 16 KiB 缓冲中筛选必需字段，再解析文字和状态，避免逐词时间/置信度数组展开后挤占音频上传内存。文字输出复用接收缓冲；中间结果最多每秒 5 次，完整句子仍全部输出。USB 空闲命令 `{"cmd":"result_check"}` 可运行 130 项合成消息检查，包括 13 KiB 长消息、中文/转义、格式错误和消息截断，不采集音频或创建云任务。[听悟官方推流消息说明](https://help.aliyun.com/zh/tingwu/real-time-stream-push)。

界面使用原有 240×320 LCD、20 行单缓冲，试验配置让 LVGL 按需使用系统堆，避免固定占用 48 KiB 私有池。普通工牌配置保持原有分配方式。设备身份、bootloader、分区表均保留；音频队列固定为 32,000 字节（1 秒），发送任务优先于绘制，WSS 启用独立收发锁，不写本地录音文件。USB 输出改用驱动缓冲以改善完成回执和截图突发输出；含屏幕版本的测试与首轮无屏幕版本分别记录。

2026-09-09 最终测试版已烧录：采集仍为 20 ms 一帧，发送每批最多合并 5 帧（100 ms / 3,200 字节），WSS 缓冲为 4,096 字节。批次等待改为 40 ms，避免时钟取整后与采集竞争而产生短包。队列和批次缓冲均为固定 RAM；使用 `max_send_ms`、`send_calls`、`queue_peak` 指标定位积压。最终三分钟实测采集和发送均为 5,760,000 字节，1,800 次发送，队列峰值 39/50 帧，最长单次发送 790 ms，最低空闲堆 7,200 字节。实时文字和非空摘要均返回；这一结果只覆盖本次短测，内存及弱网余量仍有限。

实际屏幕诊断仅在空闲时运行，输出 PNG 是 LCD 像素，不含音频：

```text
.venv/Scripts/python.exe -X utf8 tools/meeting_trial/screen_preview.py tools/meeting_trial/meeting-ended.png
```

## 试验与成本

| 路线 | 会中 | 会后 | 服务成本 |
| --- | --- | --- | --- |
| A：听悟实时 | ESP32/RAM → Wi-Fi/WSS → 听悟，持续返回文字 | 停止任务后听悟提炼摘要和待办 | 新版转写含说话人分离 0.6 元/小时；全文摘要 0.064 + 待办 0.064，合计约 0.728 元/小时 |
| B：云端汇录 → 飞书原生妙记 | ESP32/RAM → Wi-Fi/WSS → 云端接收进程，增量保存云端 WAV | 云端官方 CLI 上传飞书云空间 → 生成妙记 → 读取 AI 总结 | 飞书账号转写/智能纪要权益 + 云端存储、计算和流量；不能按 A 的单价估计 |

A 方案选择通义听悟开发套件 **2023-09-30 / v2 API**，与百炼“听悟 Agent”及旧版高价接口区分。新开通服务有 90 天免费试用：实时每天 48 小时、2 路并发，文件每天 2 小时、1 路并发。账户开通页面/实际额度优先于宣传页；只在确认试用状态后做真实调用，不自动升级商用。

按付费标准作预算：A 方案 5 分钟约 0.061 元，1 小时约 0.728 元，100 小时约 72.8 元。不开翻译；额外启用章节、关键词等按项叠加。是否选中的待办参数严格对应单项计费，仍需短测后核对用量。

飞书官方 2026-01 的个人基础权益说明列出每月 300 分钟转写、2 篇智能纪要；当前企业账号/API 实际可用权益尚未验证。这不是“妙记 API 永久免费”的承诺，也不把转写不限时长等同于智能纪要不限篇数。

来源：[听悟价格](https://help.aliyun.com/zh/tingwu/pricing-and-billing-rules)、[听悟实时接口](https://help.aliyun.com/zh/tingwu/interface-and-implementation)、[飞书权益说明](https://www.feishu.cn/content/article/7597268954498763996)、[飞书妙记上传](https://github.com/larksuite/cli/blob/main/skills/lark-meeting/references/lark-minutes-upload.md)。

## 无本地录音的约束

- 设备音频仅通过固定大小 RAM 缓冲，不写 Flash、NVS、SPIFFS 或 SD 卡。
- 16 kHz / 16 bit / mono PCM：实板每 20 ms 640 字节，当前队列固定 50 帧（首轮无屏幕版为 16 帧）；原 Python 流输入工具每 100 ms 3200 字节。一小时数据量 115.2 MB，但不在设备上累积。
- 听悟脚本从标准输入/HTTPS 流读取原始 PCM，不生成本地音频文件。WAV/MP3 不能直接当 PCM 输入。
- B 方案的完整音频**只在指定云端 Linux 主机**形成。接收程序在 Windows 上拒绝运行；不能把开发电脑的接收服务当成独立 Wi-Fi 硬件链路已验证。
- 云端汇录不是等待开完会再从设备上传。每包采样在会中就发送，会后只补 WAV 头、结束上传并提交妙记。
- 超出 RAM 可承受时长的断网会产生缺口。本轮把断流标为 `interrupted`，不静默提交为完整会议；重连续传、跨段话者对齐留待实板阶段。

## 环境准备

开发机已有项目 `.venv`，所有 Python 依赖装在该环境。云端部署时创建独立虚拟环境，再安装本目录 `requirements.txt`。官方 NLS wheel 来自听悟安装文档链接；无需把整套 SDK 移植到 ESP32。

```text
.venv/Scripts/python.exe -m pip install -r tools/meeting_trial/requirements.txt
.venv/Scripts/python.exe -m unittest discover -s tools/meeting_trial -p "test*.py" -v
```

需要在本机安全环境配置 `ALIBABA_CLOUD_ACCESS_KEY_ID`、`ALIBABA_CLOUD_ACCESS_KEY_SECRET`、`TINGWU_APP_KEY`；临时身份额外配置 `ALIBABA_CLOUD_SECURITY_TOKEN`。不把值写入文档、源码或聊天。

听悟项目选“不设置回调，主动轮询”。新版实时流程无需为任务创建强制配置 OSS。收尾后通过 TaskId 查询，不需要自建回调服务器。

## A：实时推流试验

```text
.venv/Scripts/python.exe tools/meeting_trial/tingwu_trial.py stream --pcm-stdin --max-seconds 300
.venv/Scripts/python.exe tools/meeting_trial/tingwu_trial.py stream --pcm-url <HTTPS_RAW_PCM_URL> --max-seconds 300
.venv/Scripts/python.exe tools/meeting_trial/tingwu_trial.py result <TASK_ID>
.venv/Scripts/python.exe tools/meeting_trial/tingwu_trial.py stop <TASK_ID>
```

标准输入由音频采集器/测试信号源提供，不能在 PowerShell 中用文本管道输送 PCM。不要对 `--pcm-url` 传本地路径，也不要公开含签名的 URL。脚本默认最多送 5 分钟，打印任务 ID、转写和结果；会后打印已生成的摘要/待办 JSON，不打印签名推流地址。语音流异常时仍尝试结束听悟任务；进程被强杀时使用记录的 TaskId 手动执行 `stop`。

以上为 Python 流输入工具。实板使用 `device_trial.py` 控制已烧录的试验固件，Wi-Fi、TLS/WSS 和固定缓冲已实测；设备页面及按键停止已实现，独立任务管理与按键开始仍待实现。

## B：云端汇录与飞书

首轮使用一个云端接收进程及其云盘，减少组件数量。云服务器资源还未创建、尚未产生部署费用；优先复用明确指定的测试主机或验证可用的免费试用资源。后续可改成对象存储与按需任务，届时单独验证分片、请求次数和流量费用。

仅在选定的云端 Linux 主机配置：

```sh
export MEETING_TRIAL_CLOUD_HOST=1
# MEETING_TRIAL_INGRESS_TOKEN 通过该主机的安全配置注入，随机值至少 32 字符。
python tools/meeting_trial/cloud_capture.py --cloud-data-dir /srv/meeting-trial serve
```

接收进程只监听 `127.0.0.1:8765`，由云主机已有的 TLS 反向代理暴露 `/v1/recordings`，提供有效证书的 WSS；不要公开无鉴权 WS 端口。会中使用一条 WSS，不需要设备同时直传两个厂商。

设备/测试客户端的协议：

1. WSS 握手带鉴权头，值来自设备单独配置。
2. 发送 JSON：`{"type":"start","format":"pcm_s16le","sample_rate":16000,"channels":1}`。
3. 收到 `ready` 后发送原始 PCM 二进制帧，每帧不超过 6400 字节且必须为偶数字节。
4. 会后发送 `{"type":"stop"}`，等待 `captured`，核对字节数与 SHA-256。
5. 本轮人工从已收完的 session 提交飞书，以便分别测量上传、妙记创建、产物完成时间。生产版再接自动任务队列。

测试发送器也只读标准输入，不保存音频：

```text
.venv/Scripts/python.exe tools/meeting_trial/push_capture.py --url wss://<CLOUD_HOST>/v1/recordings --max-seconds 300
```

云端安装官方飞书 CLI，以用户身份完成授权后执行：

```sh
python tools/meeting_trial/cloud_capture.py --cloud-data-dir /srv/meeting-trial submit <SESSION_ID>
```

程序依次调用 `drive +upload`、`minutes +upload`、`minutes +detail --wait-ready --summary --todo --chapter`，全程 `--as user`。官方 CLI 的 `--wait-ready` 是隐藏参数，帮助页未展示但官方实现与预检支持。

每个阶段保留 `file_token` / `minute_token`，创建回执不等于产物完成。`drive_upload_inflight` / `minute_create_inflight` 遇到超时保留为“结果不确定”，不得盲重试造成重复上传/扣额度；已到 `minute_created` 可重复运行 submit 继续只读查询。

当前缺少妙记权限：`minutes:minutes.upload:write`、`minutes:minutes.basic:read`，产物读取还需 `minutes:minutes.artifacts:read`。云端 CLI 与开发机是不同登录环境，开发机授权不会自动同步到云端；不复制隐藏登录缓存或烧入固件。

## 七天保留

接收程序每 60 秒巡检自己生成的 session，清理过期云端 WAV；停止服务期间不会巡检，恢复服务后补清理。它是实验实现，持续运行与部署监控是自动清理的前提。

**飞书云空间源文件和妙记内音频是另外两份副本。当前 CLI 的 Drive 删除进入回收站，且其删除类型不包括 minutes；尚未验证妙记永久删除及企业保留策略。不能宣称已经实现跨厂商的七天彻底删除。** 首轮使用无敏感内容的短测试录音，验证结果后尽早在飞书界面清理，并确认回收站/保留策略；保留文本纪要与测量指标。正式会议接入前完成这项验收。

## 当前验收状态

| 项目 | 状态 |
| --- | --- |
| 听悟免费试用 | 用户已在页面完成协议与开通；已回读试用状态，实时项 48 小时/天、2 路并发，文件 2 小时/天、1 路并发；页面明确不升级商用不会后付费扣费 |
| 听悟项目 | 已配置凭证，创建、结束、查询均成功；主动轮询，不配置旧版 OSS |
| 听悟 SDK | 已安装在项目虚拟环境 |
| 飞书 Drive 上传预检 | 通过，未真的上传测试文件 |
| 飞书妙记上传/读取预检 | 缺少上述权限，待用户授权 |
| 内存流、WAV 尾包、异常结束任务、WSS 鉴权/断流、重复提交保护、空摘要不误报成功、设备控制协议及 AP 前置检查 | 27 项 Python 测试通过，另有 130 项板内识别消息解析检查通过 |
| AP 配网 | 已修复双栈地址校验导致的 403；板内 6 项 HTTP 检查通过，用户确认手机自动弹页并配网成功，设备配置保存、自动关闭热点和重启联网均已验证 |
| 阿里云真实 STT/AI 结果 | 当前版本三分钟录音已实时转写并生成摘要；本轮待办为空。另一次不完整会议样例提取到 2 条待办，正式会议质量未验收 |
| 飞书真实妙记 | 暂缓，未测试 |
| 云端接收服务/TLS 部署 | 未部署 |
| 实际 ESP32 Wi-Fi 直传 | 当前版本已完成 180 秒、5,760,000 字节，无采集/发送字节差；USB 不传音频，电脑负责云任务控制 |
| 设备固件/Flash | 试验及普通构建通过；原应用已备份，试验 app 已烧录并校验 |
