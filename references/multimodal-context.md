# 音画证据与记忆

## 当前能力边界

默认由当前会话中的 GPT 执行视觉理解，与翻译、校对使用同一会话模型，不要求另配 OpenAI API 密钥。配置在 assets/vision-config.json：runner=current-session-gpt、model=inherit-current-session。该名称是技能的执行约定，不是 OpenAI API 模型 ID；脚本不会切换会话模型。

scripts/vision_workbench.py 负责 FFmpeg 抽帧/已有帧打包、帧哈希、时间戳和结果写回。GPT 通过实际可用的图像查看工具读取文件并执行画面理解及文字识读。脚本本身不推理、不调用付费 Vision API、不构建向量索引。当前模型必须支持图像输入；不能将“只有 ASR 密钥”或“文本模型能读图片文件名”视为视觉能力。不要用生成图片替代真实视频帧。

## GPT 视觉执行协议

1. 准备视觉批次，读取其中的帧路径、frame_id、采样时间、相关源文和输入摘要。
2. 用当前环境实际提供的本地图像查看工具加载每张帧。例如可用时调用 view_image，并将返回的图像内容交给模型；只拿到路径或 base64 文本而未显示图像不算完成。默认使用 original 细节，读小字时查看原始帧；必要时另生成局部裁剪并记录其来源。
3. 当前 GPT 根据实际看到的图片生成场景/动作 observations、逐字可辨的 onscreen_text 和 uncertainties。ASR 只提供上下文，不是画面文字的答案。稀疏帧无法证明完整动作过程，勿自行补足。
4. 逐条输出结构化结果，写 viewed_frame_ids、实际图像工具、会话模型可知名称；不知道准确型号时写 current-session，不猜模型 ID。
5. 用 apply 写回最新源文件。记录进入 visual_cues，后续翻译、校对、编辑共享。结果不能覆盖原文或时间轴；补充/改变视觉证据会清空相关定稿与校对记录，保留初译供重新核查。

当前会话无法接收图像时保存 unavailable 原因并继续不依赖视觉的工作。更换会话模型由用户/应用设置完成，不把 config 的 model 字符串当成模型切换功能。

## 命令与批次

下面的 `<skill>` 和 python 替换为实际路径；脚本只处理本地媒体，不接收视频网址。需要 FFmpeg 和 ffprobe 可执行文件，可分别用 --ffmpeg、--ffprobe 指定完整路径；缺少时仍可打包已有真实帧。

```text
python <skill>/scripts/vision_workbench.py extract --source source.context.v01.json --media input.mp4 --output-dir vision/batch01 --start-index 0
```

默认每批 12 条字幕、每条 2 张帧，通过配置调整。命令返回 next_start_index 和 total_cues，按批续做；先完成当前批实际看图和写回，再从返回索引处理下一批。每次使用新目录，失败时保留部分帧；不把不完整目录视为已完成。对子片段指定 --media-offset-ms（该文件在整片中的开始时间）；字幕仍用整片时间。time_ms 为请求的采样位置，画面受源帧率/解码定位精度影响，不能把它当作逐词声学对齐结果。

已有帧可建立 frame-map.json：

```json
{"media":{"identity_note":"说明帧来自哪份原视频，时间是否已加整片偏移"},"segments":[
  {"id":"s000042","frames":[{"path":"frames/clip-004-f02.jpg","time_ms":123450}]}
]}
```

```text
python <skill>/scripts/vision_workbench.py pack-frames --source source.context.v01.json --frames-map frame-map.json --output vision-batch.json
```

帧路径相对 frame-map 所在目录，输出包使用绝对路径和 SHA-256。map 中 ID 必须来自 source 并按时间排序，帧时间须位于相应字幕区间。如果采用较粗的场景证据，先核查其适用时间，再按原来的人工协议明确关联，不伪造帧时间。

GPT 读取包内所有图片后，保存以下形状的结果；摘要与 ID 必须复制真实包值，内容必须来自实际看图：

```json
{"batch_id":"copy-from-packet","input_digest":"copy-from-packet",
 "producer":{"runner":"current-session-gpt","model":"current-session","image_tool":"actual-image-viewer"},
 "segments":[{"id":"s000042","vision_status":"analyzed","viewed_frame_ids":["f000001"],
 "observations":["课件展示一条向下的曲线。"],"onscreen_text":["Gradient Descent"],
 "uncertainties":["小号公式无法辨认。"],"note":"根据本批实际读取的图片记录。"}]}
```

```text
python <skill>/scripts/vision_workbench.py apply --source source.context.v01.json --packet vision-batch.json --result vision-result.json --output source.context.v02.json
```

apply 校验摘要、唯一完整 ID、帧哈希、实际声明读取的帧列表以及源数据是否改变。analyzed 至少需要一个观察或画面文字；no_relevant_evidence/unavailable 不允许夹带正面观察。已准备帧的批次不提供 skipped 结果：只有实际未要求/无视频的任务，才在项目初始化时明确跳过。脚本只能验证声明与数据完整性，不能证明模型确实看图，执行代理须保持真实记录。

## 取得帧与观察

粗采样用于建立全片概况，不能等同于疑难句核验。QA 的 `coarse_visual_only` 表示关联帧都不在字幕区间内；这种证据只作背景。对指代、关键单位、音画冲突和源文异常，在准确时间及相邻动作边界补帧，并对实际读到的实体记录置信依据。无法区分时记不确定，不以 ASR 中出现的单位名替代图像判断。把影响翻译的疑点写入 source_checks，按 [疑点证据协议](review-evidence.md) 解决或保留待审状态。

先读取媒体实际时长，按 VAD/说话人区间、ASR 时间或场景变更建立绝对起止时间。脚本默认每条取 2 张有序代表帧，可按需增加；这是预算起点，不是全片观看。对课件切换、屏幕操作、指代不明、专名和 ASR 疑点增加针对性取样。长片分批执行，已完成批次的帧与结果保留以便续做，不遍历每一帧。

后期吐槽、短暂说明牌可能只显示一秒，邻近两张粗采样都没有字不能证明没有视觉文本。对编辑效果明显或台词提及画面文字的窗口，增加局部密采样；若实际可用，可用文字变化检测辅助定位，但记录真实工具，不能宣称本技能已部署自动 OCR 检测。`evidence_windows.py --source source.context.vNN.json --output evidence-windows.vNN.json --focus-window 120000:126000 --step-ms 500` 可规划媒体毫秒采样点，尚未抽帧或看图；按该计划提取真实图片并查看后再写证据。0.5秒只是可调起点，不保证捕获所有短文本。后期文字应单独标为屏幕作者的内容，不能并入讲者口播；需要译出时检查字幕层次和遮挡。

已有 FFmpeg 时可通过参数数组执行如下形状的单帧提取；具体时刻先从媒体区间计算，输出不得覆盖原素材：

```text
ffmpeg -n -ss 123.450 -i input.mp4 -frames:v 1 -q:v 2 frames/clip-004-f02.jpg
```

抽帧成功只代表有图像文件，尚未完成视觉分析。再用实际可用的图像查看工具/视觉模型读取该文件，记录画面文字、物体/场景、动作和不确定性；提供多张图像时说明时间顺序。OCR 只抄写可辨认文字，不根据领域常识补齐模糊词。不得仅凭外貌认定画中人身份。

## 数据与关联

视觉记录保存到 visual-memory.json，关键相关记录复制到 source.context.vNN.json 的对应 segments.visual_cues；批次脚本将字段原样带入翻译、校对和编辑。示例展示结构，不能当成真实观察：

```json
{
  "id": "s000042",
  "vision_status": "analyzed",
  "visual_cues": [
    {
      "cue_id": "v000017",
      "clip_id": "clip-004",
      "start_ms": 120000,
      "end_ms": 126000,
      "frames": [{"time_ms": 123450, "path": "frames/clip-004-f02.jpg", "sha256": "actual-frame-sha256"}],
      "observations": ["课件标题可以辨认为 Gradient Descent。"],
      "uncertainties": ["右下角的小字无法辨认。"],
      "producer": "actual-tool-or-provider-and-model"
    }
  ],
  "audio_cues": [],
  "source_notes": []
}
```

人工建立的路径相对项目目录；脚本输出使用绝对路径。记录媒体 SHA-256、时间轴基准、帧哈希、工具/模型和提示版本于 project/manifest；不把 API 密钥放入记录。关联依靠媒体身份与时间区间，不依靠文件名排序。保留 parent_ids，拆合之后重建关联。

官方依据（2026-09-06）：[图像与视觉输入](https://developers.openai.com/api/docs/guides/images-vision)、[模型输入能力](https://developers.openai.com/api/docs/models)。这些文档说明 GPT 图像输入能力；本技能的 current-session-gpt 是当前任务编排方式，并不等于已经配置独立 Responses API 客户端。

每条字幕的 vision_status 表示实际处理状态：

- analyzed：有实际读取的相关帧和可追溯观察记录。
- no_relevant_evidence：看过覆盖该区间的帧，但没有辅助翻译的信息；source_notes 写检查范围。
- unavailable：有视频但工具、素材或模型不可用；source_notes 写原因。
- skipped：纯音频/纯 SRT，或用户明确不需要视觉；source_notes 写原因。

不要把未读取、读取失败标成 no_relevant_evidence。质检只能验证状态/记录存在，不能证明模型真的看过图像；执行代理必须在交付说明中如实列出实际抽查范围。用户未要求视觉、缺少能力时可以完成文本流程并注明降级；明确要求完整视觉核验时不可把降级结果标为全部完成。

## 视觉事实如何使用

先区分事实来源：ASR 原文来自音频；画面文字来自 OCR/观察；物体动作来自视觉观察；含义推断单独标注。字幕翻译以讲话内容为主，画面用于解释“这里”“这个按钮”等指代、校核术语拼写及发现音画冲突。屏幕文字不自动成为讲者说过的话，不能悄悄插入对白字幕。

针对冲突，记录原 ASR、视觉证据、回听结果和最终决定。仅凭画面不能解决的音频疑点进入 UNCLEAR；编辑依据更多证据决定，不把视觉模型输出视为无误事实。

否定、数量和建筑状态冲突应优先精查：先取实际 cue 中点及动作前后帧，再确认阵营颜色、选中实体与界面所显示的状态。附近截图可能选中了另一座基地，采集到的帧也可能落在字幕间隙；这两种情况都不能证明该句。分别记录“原文声称未完成”和“所指建筑已生产单位”等冲突，不以画面直接替换讲者用词；无法听校时保留待审草稿并在报告中突出该项。

检查现在和未来的转换：画面已有工人或建筑已经落地，不能让译文只剩未来计划；反过来正在研究或即将出现也不能变成已完成。游戏回放倒带、暂停或快进会改变游戏内时钟，字幕必须继续使用媒体时间。检查烧录预览时只把底层画面作为视觉证据，自己的译文不是验证自身含义的证据。记录实际被遮挡的 UI、单位或片尾姓名，不因单行未截断就宣称无任何遮挡。

抽帧的请求时刻不总是实际返回帧的时间戳，尤其视频流晚于音轨开始时。记录音视频流起点；若目标时刻无视频帧，不能把返回的首张可用帧算作已看见该时刻字幕。与原音不一致时先排查工作音轨切片和时间基准，再判断解说是否说错；不要仅凭format时长相同或视觉画面相似认证同步。

## 长短期记忆

- 短期：当前章节、最近已确认的称谓/译法、相邻已审定译文、未解决指代。按字幕顺序更新；分批并行不保证滚动历史，默认按顺序处理。
- 领域：用户术语表、可信资料及允许的查询结果，每条带来源和版本。
- 视觉：按媒体、时间、cue_id 索引的观察记录；先取时间相关，再取主题相关内容。

没有向量数据库时直接按 ID、时间和关键字读取文件即可。不能把这种文件读取描述为已安装 RAG。每批上下文冻结版本和摘要；视觉修正后更新源证据，按 [领域术语协议](terminology.md) 重新检查受影响的发现记录并 bind，然后重建批次。术语快照摘要由 bind 管理，不手改摘要来保留旧状态；重新 bind 当前会保守清空全片定稿和校对/编辑有效状态。修改局部证据会使已有校对摘要失效；不要复用旧编辑结论。
