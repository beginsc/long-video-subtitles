# WhisperX 服务器后端

本文件针对 SSH 服务器。本机 GPU/离线运行请用 [本机 WhisperX](whisperx-local.md) 的独立入口。

选择 WhisperX 时读此文档。配置在 `assets/asr-backends.json` 的 `whisperx` 项；全局 `default_backend` 保持空，不替用户自动选择上传后端。本后端默认 `large-v3`（正确 ID，不是 `v3-large`）、CUDA `float16`、batch 16、`pyannote` VAD、词级 forced alignment。英语项目显式传 `--language en`；未知语言可自动检测，多语言片须分段核查。不默认说话人分离；VAD 不等于 diarization。

本机通过 OpenSSH 密钥建立到指定服务器的临时回环隧道，再以 API 密钥认证。主机密钥必须已验证并登记；不关闭 StrictHostKeyChecking。密钥只从 `WHISPERX_API_KEY` 或 CurrentUser DPAPI 读取，不把密码、API 密钥写进配置、日志、技能包或任务文件。不自动对外开放端口。

## 检查与调用

先执行以下检查，要求服务封装协议 `1.1.0`，支持真实 VAD、异步任务及结果查询。服务中的 WhisperX 库版本和 API 封装版本是两回事；仅 `/health` 正常不代表 GPU 推理已验证。

```text
python scripts/asr_backend.py check --backend whisperx
python scripts/asr_backend.py submit --backend whisperx --file sample.wav --job sample.job.json --language en
python scripts/asr_backend.py status --job sample.job.json --wait-seconds 30 --output-dir sample-asr
```

路径为命令形状，执行时用实际技能和项目绝对路径。submit 返回任务 ID，随后用 status 取结果；处理中不生成 source.json。每次 `--output-dir` 使用未存在目录。任务文件含素材 SHA256、块起点、模型配置和任务 ID，不含密钥。连接中断或提交结果不明时先查询原任务，禁止自动重复上传；服务重启会将未完成任务标为 interrupted，确认旧任务不会继续后，才用新任务文件重试。模型报错、VAD/对齐失败必须报告，不静默退回小模型、关闭 VAD 或伪造词时间。

## 长视频分块与质量

先用 FFmpeg 按实际讲话音轨生成 16 kHz 单声道音频工作副本。每块最多 15 分钟、64 MiB；实际优先 5–10 分钟，边界适当重叠，保存提取起点。用 `--offset-ms` 传入块在原片的起点；source.json 的条目时间已转为整片毫秒，asr_words 保留块内秒值并携带 asr_words_offset_ms，合并时不可再次加偏移。先依据词时间及文本核查重叠区再去重，不能按相似句盲删重复口播。

默认首次启用先以合成语音验证连通、真实 VAD 与对齐，再在用户授权的代表性素材上验质。VAD 和对齐权重可能首次下载；large-v3 已缓存不代表全部辅助模型齐备。显存不足时可显式降低 batch_size 并记录，默认模型仍为 large-v3；修改 VAD 方法可用 `--vad-method silero`，须记录选择原因和实际方法。其他 Whisper 模型可通过 `--model` 或后端配置选择，先核查服务器版本支持及权重来源。large-v3 是质量优先的默认值，不承诺每个样本得分最高。

结果保留原始 whisperx.result.json、raw_segments、对齐 words、实际模型/VAD/对齐状态、源语言与素材摘要。未对齐的词保留原文及缺失标记，不插值编造。空结果另存 no-speech.json，不能假造空白字幕；须检查音轨、静音和 VAD 漏检。ASR 只负责原文，所有条目仍为 pending，继续音画核验、术语、翻译、校对与编辑流程。

服务任务结果保存在服务端任务目录，完成/失败后自动删除该任务的输入音频；结果和状态保留供续取。维护时按任务 ID 清理已取回的历史工件，先核对范围，不在推理中重启服务。

## 版本维护

优先核查 [官方 Releases](https://github.com/m-bain/whisperX/releases) 与已安装包版本。稳定版用于常规任务；仓库 main 超前不意味着应切到开发版。升级先记录当前库/封装版本、备份配置与源文件，并做小样验证。更换服务器时重新配置主机、密钥和 API 凭据，不复用其他主机的信任记录。

词级对齐给出讲话时间，不保证每个导出片段适合阅读。对极短 yes/no、重复短句、自我纠正和笑声附近片段，先检查含义连续性；不要机械一段 ASR 对应一条最终字幕。按 data-and-tools.md 的 timing_mode 与 timing-plan 制作观看版，原始 words、偏移和原声疑点记录保留。视觉模型支持图像也不意味着具备音频听校能力。
