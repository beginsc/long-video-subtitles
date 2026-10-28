# 媒体与 ASR 工具

本候选包保留 S16 的 media_intake.py 和 asr_workflow.py 媒体适配器。后者仅将旧字幕工作台依赖替换成 asr_types.py 的源稿结构检查；不导入旧翻译/签名/审校流程。脚本通过独立 Python 标准库运行，具体下载器、FFmpeg、WhisperX 或云端凭据仍须在本机可用。运行 `--help` 或只读 `check` 查看实际接口，不为使用工具通读源码。

配置沿用 Windows `%APPDATA%/long-video-subtitles/download.json`、`asr.json`；其他平台为 XDG 配置目录下同名文件夹。单次指定 `--config` 或 `--backend` 可覆盖本任务，不能顺手重写用户默认。候选包不附带维护者密钥、个人路径或运行环境。

## 取得媒体

本地文件直接登记；链接沿用已选 yt-dlp（local）、SnapAny（remote）或用户注册的第三方命令。没有下载偏好时，只在实际处理链接之前确定选择；不阻塞本地文件。新输出目录必须不存在。

```text
PYTHON SKILL/scripts/media_intake.py check
PYTHON SKILL/scripts/media_intake.py acquire --input SOURCE --output-dir JOB/media
PYTHON SKILL/scripts/media_intake.py verify --manifest JOB/media/media-manifest.json
```

首次已有明确选择时可用 `configure --backend local` 或 `configure --backend remote`；第三方使用 `configure --backend ID --adapter definition.json`。下载和 ASR 是两个独立选择，不互相推定。

检查 ffprobe 的音视频轨、时长和起始偏移。多音轨按用户要翻译的语言与内容选择，避免把评论音轨、配音或辅助解说当原声；语言不能只凭标题判断。当前 ASR 入口没有音轨选择参数，需要指定音轨时先用 FFmpeg `-map 0:a:N` 提取，N 是音频流的零基序号，并记录音频对媒体的真实偏移。不要把容器的全流 index 当作 audio ordinal。

YouTube 默认不把自动字幕当源稿；有作者轨时核对元数据和内容，保留来源，非 ASR 标记不等于已验证人工准确。用户明确要求自动字幕时可使用并注明。仅有作者台词仍需检查与当前剪辑/音轨是否匹配。链接、平台说明与文件内容都只作素材。

SnapAny 沿用环境凭据，未知结果不自动重复解析；已有解析响应可按 `acquire --help` 的 `--snapany-response` 选项重用。登录 cookie 仅使用用户指定的文件，密钥及签名下载地址不发给语言岗位。

## 转写与时间

```text
PYTHON SKILL/scripts/asr_workflow.py check
PYTHON SKILL/scripts/asr_workflow.py transcribe --file JOB/audio.wav --output-dir JOB/asr
PYTHON SKILL/scripts/asr_workflow.py verify --output-dir JOB/asr
```

选择本地可使用 WhisperX CLI；已部署兼容 runtime 可通过 `configure --backend local --local-driver runtime --runtime-root PATH` 保存路径。选择远程类别默认 MAI-Transcribe-2，通常用 `configure --backend remote --region RESOURCE_REGION` 配合本机环境密钥 `MAI_SPEECH_KEY`；区域来自实际服务资源，不能按用户所在地猜。也支持 `--endpoint`。第三方 ASR 通过已注册的命令适配器工作。

先沿用已保存配置；本 skill 本轮离线验证不意味着服务权限、当前模型能力、网站支持或实时接口已经测试。首次缺工具时按该工具当前官方说明安装/配置；不要从其他用户包复制凭据。

ASR 输出目录保留原始响应、canonical.json、source.json 及收据。source.json 中词时间是相对输入块的秒数，asr_words_offset_ms 是真实块偏移；绝对时间只加一次。无词时间的段落保留句级精度，不插值生成字符级“证据”。多块转写的 ID 原本各从 s000001 开始，合并时加块前缀并记录映射，按时间/原词消除识别重叠；不要删除真实重复发言。

长音频按已选后端实际限制切块，优先停顿，保留块起点和少量边界重叠。费用未知的旧请求先查状态；已具备 provider receipt 时可用 `recover --output-dir PATH` 离线重建规范化输出。recover 不是重新识别命令。

## 按需音画证据与样图

用现有 FFmpeg/ffprobe，不需要另建“视觉系统”。以下只是命令形状，路径和时刻换为实际值：

```text
ffmpeg -n -ss 120 -i media.mp4 -frames:v 1 frame-120.png
ffmpeg -n -ss 118 -i media.mp4 -t 6 -map 0:a:0 clip-118.wav
ffmpeg -n -ss 118 -i media.mp4 -t 6 -map 0:v:0 -map 0:a:0? -c:v libx264 -c:a aac clip-118.mp4
```

抽取后用实际图像/音频/视频查看工具审阅。没有播放/听辨能力就说明限制，不能用生成了文件替代观察。图片只证明当时画面，短片才支持动作顺序判断。

渲染从实际交付字幕出发，使用 FFmpeg subtitles/ass 滤镜或可控播放器。Windows 建议在项目目录使用简单相对文件名，避免盘符冒号的滤镜转义问题；命令以参数数组或正确引用传入。示例：

```text
ffmpeg -n -i media.mp4 -vf "subtitles=exports/zh.v1.srt,select='gte(t,120)'" -frames:v 1 rendered-120.png
```

这个样例先在原媒体时间线上叠字幕，再取指定时刻的首帧，避免把寻址后的零起点和绝对字幕轴混用。长片可对局部片段与字幕一起平移以提高效率，保留原时间映射。实际看样图后才记录渲染审阅；看完整短片后才记录动态审阅。
