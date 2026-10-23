# AssemblyAI 在线后端

这是长视频字幕工作流的可选预录音 ASR 后端。只负责源语言识别、时间戳、说话人标签；翻译与校对继续由字幕工作流执行。代码直接调用 REST API，仅依赖 Python 标准库，不要求安装 SDK。配置请求本身不上传媒体、不创建收费识别任务。

## 配置与密钥

默认配置：`assets/asr-backends.json`。`default_backend: null` 保留选择权；实际使用时指定 `--backend assemblyai`。已配置官方 US API 地址 `https://api.assemblyai.com`，需要 EU 时改为 `https://api.eu.assemblyai.com`。适配器拒绝其他主机以及 HTTP 重定向，避免把鉴权头发给非服务商地址。

默认模型顺序 `universal-3-5-pro`、`universal-2`；语言自动检测，说话人区分、标点及格式化开启。模型数组是服务端选择/回退顺序，不是两次独立识别；实际采用的模型记录在 meta.asr_model_used。已知语言时传 `--language en` 等代码以关闭自动检测。模型账户权限、语言覆盖和价格以当时服务商为准，先短片验证。

凭据读取顺序：`ASSEMBLYAI_API_KEY` 环境变量，然后 `~/.codex/credentials/long-video-subtitles/assemblyai.dpapi`。Windows DPAPI 绑定当前用户，包内不包含该文件；换电脑/账户需重新配置。非 Windows 使用环境变量。不要打印环境变量值、解密后的值或 HTTP 请求头。

桌面沙箱可能无法访问当前用户的 DPAPI 或网络；遇到该限制时按执行环境规则申请在当前用户上下文运行，不能退回明文存储或关闭 TLS 校验。

需要新增/更换密钥时运行 `scripts/asr_credentials.py assemblyai`，在隐藏输入提示中输入；轮换现有密钥明确加 `--replace`。自动化可通过私有 stdin 管道加 `--stdin`，不要把密钥放在 shell 参数或临时明文文件。

## 操作

下文 `<skill>` 解析为技能实际目录，`python` 解析为实际解释器。任务文件写在项目目录，不能写进技能目录。

```text
python <skill>/scripts/asr_backend.py check --backend assemblyai
python <skill>/scripts/asr_backend.py submit --backend assemblyai --file speech.wav --job jobs/asr-01.json
python <skill>/scripts/asr_backend.py status --job jobs/asr-01.json --wait-seconds 30 --output-dir asr-01
```

`check` 调用官方 GET `/v2/transcript?limit=1`，仅输出鉴权状态，不展示历史任务数据，不创建识别任务。鉴权成功不证明余额、特定模型权限或整条转写链路已通过。

`submit` 执行媒体上传与任务提交。只有用户选择该在线后端处理相应素材时才执行；已有授权不重复询问。可以用 `--audio-url` 代替 `--file`，URL 必须是服务可访问的 HTTPS 媒体直链。YouTube 网页不等于媒体直链。长视频优先提取讲话音轨减少上传；本地上传保守限制 2 GB，超过则提取、压缩或分块。提供素材前核查当前服务时长/大小限制。

`status` 每次只做最多 45 秒的轮询窗口（网络超时另计），queued/processing 就保留任务 ID，下次继续查询；不重新上传或提交。completed 时写 `assemblyai.transcript.json`、`assemblyai.sentences.json` 和工作台可直接分批的 `source.json`。句/词级毫秒转为技能标准，词记录采用秒 + asr_words_offset_ms；块的 `--offset-ms` 只加一次。保留原始置信度和说话人，不生成伪译文。

ASR 句子可能过长，source.json 是翻译输入，不是已排版字幕。仍需处理疑难发音、说话人交界、漏听、分块重叠和语义分句。没有识别到有效语音时保留原始服务响应，规范化不生成伪造 cue。服务 audio_duration 保留为独立字段，不能冒充 ffprobe 测得的整片时长。

job 文件含任务 ID、输入摘要、提交配置和用于恢复的媒体 URL，属于项目内部文件，不随技能打包或公开分享；接口原始输出可能包含用户内容，也留在项目内。凭据只在内存中读取，job 仅保存凭据名称和环境变量名称。

## 中断和失败

所有任务开始前以独占方式创建 job 文件；同一路径再次 submit 会拒绝，避免重复计费。上传/提交没有自动重试。提交超时可能已经被服务接受，此时标记 submission_uncertain；先到服务商后台找到同一次任务，再运行：

```text
python <skill>/scripts/asr_backend.py attach --job jobs/asr-01.json --transcript-id VERIFIED_ID
```

attach 会核对媒体 URL，成功后 status 续做，不创建新任务。上传失败或提交明确被拒绝时保留旧记录；修复后以新的 job 文件尝试，确认没有已接受的重复任务。GET 查询出现临时错误可以再次运行；401/403 检查密钥/权限，429 等待限流恢复，余额或模型权限问题按账户状态处理。不得把“已配置”描述为“可转写成功”。

## 扩展

- **同服务新模型**：在项目内复制配置，修改 speech_models，或提交时使用 `--model MODEL_ID`；多次指定形成有序列表。无需修改凭据或下游字幕数据协议。
- **同服务多方案**：backends 下增加如 `assemblyai-accurate`、`assemblyai-economy`，复用 adapter/credential_name，分别指定模型；用 `--config project-asr.json --backend NAME` 选择。
- **新增服务商**：实现与鉴权、上传、提交、状态、规范化对应的服务商适配器，登记 ADAPTERS，并添加专属配置校验/标准数据转换与测试。仅添加一个名字不会自动获得新协议支持。当前 config_for、job 的 attach/status 和 normalize 含 AssemblyAI 约定，新增服务需相应抽离/路由，不能把别家的响应当成 AssemblyAI。

## 官方依据

核对日期 2026-09-06：

- [模型选择](https://www.assemblyai.com/docs/pre-recorded-audio/select-the-speech-model)
- [上传](https://www.assemblyai.com/docs/pre-recorded-audio/api-reference/files/upload)
- [提交任务](https://www.assemblyai.com/docs/pre-recorded-audio/api-reference/transcripts/submit)
- [句级结果](https://www.assemblyai.com/docs/pre-recorded-audio/api-reference/transcripts/get-sentences)
- [只读鉴权所用任务列表接口](https://www.assemblyai.com/docs/api-reference/transcripts/list)
