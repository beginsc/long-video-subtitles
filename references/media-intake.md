# 素材入口与视频下载

输入先分流：**本地视频文件直接登记、检查和分析；视频网站链接先下载为本地视频，再进入同一分析流程**。音频文件沿用本地媒体入口；已有字幕走字幕导入，不交给视频下载器。下载完成仅表示素材可用，不代表已做视觉理解、听校、ASR 或字幕轨审查。

## 首次安装：选择一次，以后沿用

安装引导结束前，由主代理向用户提供两个选项，取得选择后运行 `configure` 保存偏好：

| 选项 | 行为与准备 |
| --- | --- |
| 本地 `local` | 用 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 在用户机器上解析和下载；需要 yt-dlp、FFmpeg/ffprobe，部分网站另需 JS 运行时或用户提供登录 cookie。无需 SnapAny API key；仍需联网访问视频网站。 |
| 远程 `remote` | 将用户提供的视频链接发给 [SnapAny 官方 API](https://platform.snapany.com/docs)，取得下载地址后将媒体下载到本机；需要自己的 API key，解析可能计费。下载偏好不决定后续 ASR 是否上传音频。 |

只询问下载方式及当前选择缺少的必要信息。用户已明确选择时直接保存；不因本机有某个工具或密钥而代替用户选择。不要求把密钥粘贴到对话中。

```text
python <skill>/scripts/media_intake.py configure
python <skill>/scripts/media_intake.py check
```

交互终端中的 `configure` 显示两项且不预选；智能体已取得选择时用 `configure --backend local` 或 `configure --backend remote`。无终端、也未给参数时返回待选择错误，不猜默认值。技能文件没有通用的安装后自动执行钩子：支持安装引导的环境在安装时执行；仅克隆/复制文件的环境，安装说明要求随后配置，首次调用发现缺配置时由主代理补做引导。首次任务只有本地文件时仍可直接分析，选择下载方式不阻塞这项工作；处理链接前必须取得选择。

偏好保存在安装目录以外：

- Windows：`%APPDATA%/long-video-subtitles/download.json`。
- macOS / Linux：`${XDG_CONFIG_HOME:-~/.config}/long-video-subtitles/download.json`。
- `--config <path>` 或环境变量 `LVS_DOWNLOAD_CONFIG` 可指定位置；命令参数优先。

配置仅保存 schema、`backend`、`max_height` 和配置时间，不保存密钥、cookie 或维护者路径。更新/重装技能不会覆盖用户偏好。再次运行 `configure --backend …` 可切换默认；单次 `acquire --backend …` 只覆盖本任务，不修改偏好。没有选择时 URL 任务返回配置提示，不创建下载目录、不联网。

默认最高画质偏好为 1080p，可用 `configure --max-height 720 --backend local` 或本次任务的 `--max-height` 调整。优先保留原始音频，不先做有损转码或剪辑。没有标注画质的 SnapAny 直链以实际探测结果为准，超偏好时写警告；没有符合上限的已标注档位则明确返回，不能偷偷选更高画质。

## 本地方式准备

复用现有 Python、yt-dlp 和 FFmpeg。缺少 yt-dlp 时，按其 [安装说明](https://github.com/yt-dlp/yt-dlp#installation) 在用户选择的运行环境安装，常见入口如下：

```text
python -m pip install -U "yt-dlp[default]"
```

也可使用官方可执行文件并将其加入 PATH，或传 `--yt-dlp <绝对路径>`。FFmpeg/ffprobe 是系统可执行文件，安装同名 Python 包不能代替它们。YouTube 的 JS 挑战按 [官方 EJS 说明](https://github.com/yt-dlp/yt-dlp/wiki/EJS) 配置支持的运行时；脚本支持 `--js-runtime node`、`--js-runtime deno:/path/to/deno` 等 yt-dlp 参数形式。`check` 只检查本机工具/版本与密钥是否存在，不测试网站、账号或剩余额度。

下载器不读取用户全局 yt-dlp 配置，不启用额外插件，不下载自动字幕、不写观看历史。需登录时仅在用户指定后使用 `--cookies-file <Netscape-cookie文件>`，先复制到任务私有目录供 yt-dlp 使用，不改用户原件、不自动提取浏览器登录态。cookie 内容不进入工单、交付包或日志。

## 远程方式准备

在 [SnapAny 控制台](https://platform.snapany.com/console/keys) 创建自己的 key，通过本机环境变量 `SNAPANY_API_KEY` 提供；不要写入配置、脚本、GitHub 或工单。脚本仅从环境取值，标准输出不会显示密钥。API 使用 `POST https://api.snapany.com/openapi/v1/extract/post`、Bearer 身份认证和 `{"url": "用户的视频链接"}`。不调用网页的非公开签名接口，也不自动启用代理下载、转写或字幕提取等额外收费服务。

根据 [官方 Extract Post 文档](https://platform.snapany.com/docs/extract-post) 读取 `medias`、`resource_url`、`variants` 和下载 `headers`。保留所需请求头，包括空字符串值；SnapAny API key 不转发给视频 CDN。按画质选择可用视频档位；有 `video_url` 和 `audio_url` 时分别流式下载，再由 FFmpeg 无损封装到 MKV。最终必须检查视频与音频流，不能把无声视频误交给转写。静音素材只有在确认为预期后使用 `--allow-silent`。

解析响应可能带临时签名地址和访问头，保留为项目私有的 `snapany-response.private.json`，不传到翻译链、不放入公开仓库。链接通常会过期，应及时下载。当前适配器支持直连媒体文件；HLS/DASH 播放清单返回明确提示，另选直链档位或在用户选择后改用本地方式，不将清单当视频。不要把一次成功推断为所有网站都可用。

## 素材员执行与主代理验收

主代理完成用户偏好配置；素材员只读取冻结的本任务选择，在自己的独占任务目录取素材。工单写明输入文件/链接、选用后端、画质、所选视频/分 P、必要依赖和许可范围，不包含 key/cookie/签名直链。为避免偏好在派单后变化，主代理把选择转为工单参数 `--backend`、`--max-height`；员工不修改用户全局配置。

```text
# 文件：原件只读；不需要下载配置、yt-dlp 或 API key
python <skill>/scripts/media_intake.py acquire --input "<本地视频路径>" --output-dir "<项目>/jobs/intake/whole/a01/media"

# 链接：沿用本机已保存的方式；视频下载到本任务目录
python <skill>/scripts/media_intake.py acquire --input "https://www.youtube.com/watch?v=<id>" --output-dir "<项目>/jobs/intake/whole/a01/media"

# 本任务覆盖选择（B 站等其他受支持视频网站同样处理）
python <skill>/scripts/media_intake.py acquire --input "<视频链接>" --backend local --max-height 720 --output-dir "<项目>/jobs/intake/whole/a02/media"

# 续用前核对实际媒体
python <skill>/scripts/media_intake.py verify --manifest "<任务>/media/media-manifest.json"
```

每个输出目录须尚不存在。返回 `ready` 后主代理读取 `media-manifest.json`，核验实际媒体路径、SHA-256、大小、时长、音轨/视频流、下载器/来源和警告，再登记共享 manifest 的素材阶段。文件存在、下载进程退出或页面标题均不能代替验收。ffprobe 是媒体结构检查，不证明已完整观看或听辨；素材内容与用户链接是否对应仍按需要抽查。

清单的 `analysis_status=not_started` 明确表示后续尚未执行。主代理将已验收本地媒体路径和摘要交给转写/视觉岗位，进入 [媒体后端](media-backends.md) 与 [音画上下文](multimodal-context.md) 流程。源媒体时长、音视频起点来自 ffprobe；下载偏好、网站字幕或容器时长不构成字符级构轴依据，仍按原有时间证据协议生成相关工单附件。

仅处理一个视频。yt-dlp 预检拒绝合集/多视频/直播任务；提供具体单集或分 P 链接后再运行。SnapAny 一次返回多个视频时先保留响应，再选 1 起算的 `--media-index`（按 `medias` 中的视频顺序，忽略图片），通过缓存重用命令执行。用户明确要求整合集时由主代理逐片建立任务，不让默认入口意外下载全集。已有 YouTube 作者字幕的核验继续遵循技能入口；下载视频不表示获准使用自动字幕。

## 中断和失败

成功素材续做前 `verify`，校验通过直接复用，不重复下载。输出目录拒绝覆盖，旧下载进程状态不明时先查状态，不并发写同一目录。失败保存 `request.json`、`failure.json` 及已有文件；`.part`、没有 ready 清单的文件均不能进入 ASR。

SnapAny 每次解析仅一次 POST，不自动重提。有解析响应时，用新 attempt 重用它，避免为选档位或下载重试再付解析费：

```text
python <skill>/scripts/media_intake.py acquire --input "<同一个视频链接>" --backend remote --snapany-response "<旧任务>/media/snapany-response.private.json" --media-index 1 --output-dir "<项目>/jobs/intake/whole/a02/media"
```

缓存校验源链接摘要与 API 入口，不匹配则拒用。直链过期、HTTP 403 或网络失败时，先报告原因和可用选择；再次调用计费解析或切换后端须符合用户已有授权，缺少时才询问。超时只有请求记录、无响应时视为结果未知，不能假定未计费。当前远程失败重试为新目录重新下载，不承诺字节级断点续传；已验收素材的续用和字幕批次恢复仍适用。[断点恢复](checkpoint-recovery.md) 继续约束后续制作阶段。

核对日期：2026-10-24。接口字段与依赖依据上方官方文档；网站支持和登录要求可能变化。
