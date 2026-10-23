# 本机 WhisperX（可选 ASR 后端）

公开包只包含 `scripts/whisperx_local.py` 轻量入口，不包含维护者的 WhisperX 虚拟环境、模型权重、FFmpeg、`run_local.py` 或机器安装报告。此入口目前按 Windows 独立运行环境布局调用，不是通用 WhisperX 安装器；新机器不能只克隆仓库就运行本机 ASR。

已有兼容运行环境时，将 `assets/whisperx-local.json` 中 `runtime_root` 改为它的实际绝对路径。目录必须包含 `venv/Scripts/python.exe` 和兼容的 `run_local.py`；先验证 check，再处理代表性短片。

```text
python <skill>/scripts/whisperx_local.py check
python <skill>/scripts/whisperx_local.py transcribe --file sample.wav --language en --offline --output-dir asr-v01
```

兼容运行器需支持 check / transcribe、`--file`、`--language`、`--offline`、`--output-dir`、`--offset-ms`，保存 job、原始识别、词对齐与版本化 source 工件。没有兼容运行器时，可使用自行验证的 WhisperX 部署，再按 [数据与工具](data-and-tools.md) 导入带真实时间戳的输出；不要宣称此轻量入口会自动安装运行器。

建议 large-v3、真实 VAD 和词级对齐；设备、精度、batch_size、VAD 方法和版本按实际环境记录。先检查 FFmpeg、模型及 GPU 能力，不把维护者的硬件测试当成本机已通过。首次使用某些语言或模型可能需要联网下载；只有权重齐全且验证通过后才可声称离线可用。

本机入口为独立同步进程，不使用 `asr_backend.py --backend whisperx-local`。云端接口见 [WhisperX 服务器](whisperx.md) 和 [AssemblyAI](assemblyai.md)。本机 ASR 不代表 BLEURT/BERTScore 已安装。

长片按 [媒体后端](media-backends.md) 使用 audio_timeline.py 建立真实 PCM 时间轴，并按带重叠的块处理；逐块记录绝对偏移，不重复偏移已经换算为整片时间的 source。保留缺失词时间，不能插值伪造听校证据。重叠区按词时序和语义核查，不能机械拼接各块 SRT。

中断后核对 job 状态、工件散列和原进程；已完成块复用，失败块保留并使用新的输出目录。切换 VAD、模型或精度后先检查漏词、专名和背景音乐，段数或词数的差异本身不代表质量。
