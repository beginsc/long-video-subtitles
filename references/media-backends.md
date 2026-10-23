# 媒体与后端

## 检查与抽样

有可信字幕就复用。ffprobe JSON 检查媒体时长、音轨语言/数量和字幕轨，原片只读，选择真实讲话音轨。辅助脚本仅依赖 Python 标准库；FFmpeg/ffprobe、ASR 和模型权重另行提供。

优先用户指定的 ASR，或复用可用的本地模型、ViDove、VideoLingo。已提供可选 [AssemblyAI 在线适配器](assemblyai.md) 和 [WhisperX SSH 服务适配器](whisperx.md)，配置位于 assets/asr-backends.json；默认不自动选择云端后端。密钥从环境变量或本机用户的 DPAPI 加密凭据读取，配置与分享包不含密钥。均无可用后端时说明缺少的依赖；不为创建技能安装 GPU 驱动或大型模型。不能伪造 ASR JSON。

命令形状如下；实际执行使用参数数组或正确 shell 引号，替换路径/音轨，不拼接不可信命令文本：

```text
ffprobe -v error -show_format -show_streams -of json input.mp4
ffmpeg -n -i input.mp4 -map 0:a:0 -vn -ac 1 -ar 16000 speech.wav
python <skill>/scripts/audio_timeline.py --media input.mp4 --output-dir audio-v01 --block-ms 600000 --overlap-ms 5000
```

16 kHz 单声道仅是常见 ASR 工作副本，按真实后端要求调整并保留原片。长片切块/流式处理，勿一次将全片 PCM 加载入内存。ASR 时间常相对块起点：只加一次 offset_ms，保留原 JSON 与起点。用词时间和文本核查重叠边界，拼接后检查尾段及遗漏。仅有 segment 级时间时明确精度限制，不伪造 word 时间。

长片优先一次解码为保留媒体时间的 PCM，再按采样位置切块。`audio_timeline.py` 不使用输入端 seek（包括 `-ss 0`），用容器 format.start_time 作为媒体零点，保留音轨延迟和时间空隙，末尾补静音至媒体时长；16 kHz 单声道是工作副本，原片保持不变。输出 manifest 记录原片与PCM散列、音视频起点、实际采样数、切块采样区间与 offset；全片解码和切片均流式处理，不把整片音频读进内存。输出目录拒绝覆盖。`slice_pcm()` 也可用于从该时间轴抽取短样本。

某些音视频起点不同的容器，在输入端 `-ss 0` 时也可能漏掉开头音频；计划时长、参数起点及文件名不能证明实际内容起点。上传ASR前验证真实WAV采样数、起始区间和重叠PCM；有明显时长缺口时先定位，不能把“少了多少秒”直接作为校正量。已有缓存需校验其内容与切片方法，不能仅因哈希与旧清单一致就认为旧时间轴正确。证实工作音频缺失或偏移后另存正确切块，重做受影响ASR、词时间及证据关联；不整体平移所有后续块，也不改已冻结成绩。

FFmpeg的时间戳和seek选项依据：[官方命令文档](https://www.ffmpeg.org/ffmpeg.html)、[重采样first_pts/async说明](https://www.ffmpeg.org/ffmpeg-resampler.html)。这些参数保留时间轴，不等于确认每个音素、VAD或词对齐准确。

## 本机 WhisperX

本机 GPU 部署已提供独立入口，运行前读 [whisperx-local.md](whisperx-local.md)。本地无需 SSH、API 密钥或上传，使用单独进程输出与服务器兼容的 source.json；长片沿用上方真实时间轴切块。

## 独立 ASR

Whisper 系列或其他工具导出 SRT，或 `{ "segments": [{ "start": 0.0, "end": 1.2, "text": "..." }] }` 的秒单位 JSON。import-asr 保留 words、speaker 及相关置信字段。WhisperX 可按此结构导入，不同语言的对齐能力需查实际版本。

运行前读安装版本的 --help/官方文档，确认语言、设备、精度、模型路径和输出格式，不猜 CLI 参数。先用包含专名、数字、噪声或多人讲话的 1–3 分钟片段测时验质，估计整片耗时。无人声、音乐、重叠讲话、混合语言均需检查。

## ViDove

读现有 checkout README、entries/run.py --help 和输出实现。README 入口形状：

```text
python entries/run.py --video_file input.mp4 --launch_cfg configs/local_launch.yaml --task_cfg configs/task_config.yaml
python entries/run.py --srt_file input.srt --launch_cfg configs/local_launch.yaml --task_cfg configs/task_config.yaml
```

复制任务配置到工作目录再设置语言、后端、上下文和校对输出，不改全局默认配置。完整后端结果仍进入源译核验和技术质检。只取中间阶段时核查真实接口，不假设 --asr-only 等参数存在。

## VideoLingo

读现有 checkout README、config.yaml、入口和 core/ 实现。README 以 Streamlit 应用为主，不假设统一 headless CLI。使用已有应用或据真实函数接口编写适配器，取得可核验的原始时间轴/文本后继续。

每片独立工作副本或使用后端实际支持的隔离机制，避免共用 output/cache。缓存记录输入、模型、配置及术语摘要。后端结果作为 draft，不因其声称反思/润色就标为人工审校。不要为续做删除整个输出目录。

## 输出视频

仅在用户需要时软封装或烧录。核查容器、编码、FFmpeg 能力。ASS 烧录需要 libass 和有效字体；Windows 盘符/空格的字幕滤镜路径需正确转义。先做短预览并检查图像，未经渲染不称成片已验证。视频导出失败不影响字幕和审校工件交付。
