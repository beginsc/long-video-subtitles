# 待听校音频证据

当 triage 有语音/语义疑点时使用，不要求普通清晰句全部重新识别。优先处理否定、数量、主客体和混入的背景台词。先检查当前会话是否有真实音频输入能力：能接受图片不等于能听音频，能播放给用户也不等于模型能听见。当前技能提供本地切片和证据包，不附带独立音频理解 API；不得凭工具名称或模型名称宣称已听校。

```text
python <skill>/scripts/subtitle_workbench.py triage edited.vNN.json review-queue.vNN.json
python <skill>/scripts/audio_review.py prepare --source edited.vNN.json --queue review-queue.vNN.json --media video.mp4 --output-dir audio-review-v01 --ffmpeg <ffmpeg路径> --ffprobe <ffprobe路径>
python <skill>/scripts/audio_review.py verify audio-review-v01/packet.json
```

队列须与源版本摘要完全一致。默认每处前后各 2.5 秒，按实测媒体边界裁剪；保留原音轨通道数，输出 16 kHz PCM WAV。音轨用 `--audio-stream`（按音频流从零编号）指定。媒体为子片时传 `--media-offset-ms`，包中同时保留全片时间、片内时间与疑点在切片内的范围。上下文用 `--before-ms` / `--after-ms` 调整；单片上限 90 秒，超出时重新整理疑点，不静默截掉句尾。输出目录拒绝覆盖，失败切片保留，后续使用新目录。

packet.json 记录原媒体/切片 SHA256、源文摘要、问题及相邻文本。准备和验证切片不会把 source_checks 改成 resolved，也不会调用 ASR 或付费模型。review-template.json 初始全部 pending。

实际具备音频输入工具时，把 WAV 输入该工具/模型，填写 reviewer、audio_input_tool、heard_text、speech_origin、alternatives、decision、note。明确内容来自解说、背景游戏语音、其他说话人还是未知；不能把混入的单位台词硬接成解说正文。听不清时保持 pending/unclear，草稿可采用待听校标记或注明暂译，不把猜测写成已确认台词。原声有口误或自我更正时保留其语义过程，不能为了逻辑顺畅自动反转判断。

没有音频输入能力时，仍交付音频包与草稿，说明需要哪种证据。第二个 ASR 可在已有授权范围内作辅助比较，但不自动解决疑点，不自动重复提交收费任务。用户或实际音频模型给出可追溯结论后，用 [review-evidence.md](review-evidence.md) 的 resolution 结构回写新源版本，并重建术语发现、校对和编辑；evidence 指向切片及具体听校记录。背景声音、单次误词或参考答案都不应自动提升成可复用领域术语。

未实际听取原声时，记录“转写待核／尚未听校”，不要写成“听辨不清”。无法形成可靠暂译的碎片，可在明确标为草稿的观看文件中保留简短的 `[转写待核]` 占位，连同原时间位置和独立未决记录；已有可靠的部分仍翻译。不要将形似人名的 ASR 碎片直接认证为人物，也不要为字幕整洁或评测分数删掉该条。占位不是原台词或听校结论，不能随草稿升格为正式审定字幕；详细理由和原始读法仍放审查文件。

游戏解说中还要留意自我更正、故意口误、谐音、笑声及反应句之间的因果关系。若笑点无法从 ASR 文本解释，把该段及前后铺垫加入 source_checks；不要依靠“识别结果很流畅”判为没有疑点。数字与另一份转写/参考一致只能提高待核查假设的一致性，不构成原音证据。只有后期评测才发现的口误线索另存 post-score 诊断，不回写已冻结稿或其待审数量，也不将参考注释当作听到的内容。

词对齐空档的切片清单也保留源版本路径／散列、原始绝对区间及媒体／切片散列。后续拆合使邻接 cue ID 改名时，旧清单仍属于其原源稿；按时间和真实 parent_ids 关联新稿，并另存关联记录，不能把旧 ID 直接当成最终序号。只有时间范围、媒体与切片内容一致时才复用音频文件；复用不继承未经实际执行的听校状态。

`audio_review.py prepare` 先在输出目录生成保留容器时间的 `timeline.wav`，再按采样位置生成各片段，避免每个片段各自seek导致开头错位。packet含原始媒体和标准PCM的散列、采样区间；新增PCM占用额外磁盘，原始文件不改。媒体起点规则见[媒体与后端](media-backends.md)。PCM字节一致性是切片证据，不是听懂了原声；若源字幕时间本来错误，精确切片也不能替它证明时间正确。

毫秒四舍五入可让队列末尾比PCM长不足一毫秒。疑点包现在用 `slice_pcm(..., allow_rounded_end=True)`，只容许末端超出0.5毫秒加一个采样点，记录请求/实际区间及裁掉的采样数；开始时间和较大偏差仍报错。空档音频等使用同一函数的流程可显式启用该选项。不得用无限制min裁剪掩盖素材错轴；默认切片仍严格拒绝越界。
