# 数据协议与脚本

`scripts/subtitle_workbench.py` 需要 Python 3.9+ 和标准库，无网络调用。下文 `python` 指实际可用解释器，脚本路径应解析为本技能所在目录；项目文件路径以当前任务目录为准。Windows 有空格的绝对路径需引号，PowerShell 调用带引号解释器用 `&`。先执行 `--help` 查看真实参数。

S12 的岗位调度见 [subagent-workflow.md](subagent-workflow.md)：员工在独占任务目录写阶段响应，主代理串行执行下列对正式稿件的 apply/bind。阶段响应协议和摘要算法不变；assignment/handoff/代理身份放调度侧文件，不插入已签名批次。并行同阶段响应按最新累计稿逐批 apply，下一阶段须重新 chunks；这些脚本不会自动创建子代理。

## 素材入口

`scripts/media_intake.py` 提供 `configure/check/acquire/verify`：本地文件直接登记，视频网站链接用已选的 yt-dlp 或 SnapAny 下载，输出独立的 media-manifest.json；详细依赖、首次选择和失败重试见 [media-intake.md](media-intake.md)。此工具不启动 ASR、不提取自动字幕、不替代实际音画核验，也不改下文字幕数据协议。

## 源数据

新任务另加 `meta.quality_contract="evidence-v2"`，每条检查后写 `source_checks: []` 或具体疑点，结构见 [review-evidence.md](review-evidence.md)。旧稿不会自动冒充满足新协议。source_checks 计入校对摘要；开放疑点阻止 PASS/reviewed；UNCLEAR 转 reviewed 需要编辑响应中的 uncertainty_resolution 证据记录。triage 汇总待决队列。

```json
{
  "schema_version": 1,
  "meta": {"workflow": "vidove-inspired-v1", "source_language": "en", "target_language": "zh-Hans", "glossary_version": 1, "duration_ms": 7200000},
  "segments": [
    {"id": "s000001", "start_ms": 1000, "end_ms": 4200,
     "source": "Keep the original meaning.", "draft": "", "target": "",
     "review_status": "pending", "review_note": "", "issues": [],
     "vision_status": "skipped", "visual_cues": [], "source_notes": ["本示例仅有 SRT，无原视频。"]}
  ]
}
```

时间为整片整数毫秒。source 是此版本原文，原件另存；draft 初译，target 编辑定稿，review_status 为 pending/needs_review/reviewed。只有真正完成校对与编辑并写说明才标 reviewed；脚本不验证语言质量。拆合时增加 parent_ids，追溯最初 source.json ID。ASR words 原样保留在 asr_words，其中秒数仍相对块起点；asr_words_offset_ms 给出偏移，消费者须换算一次。

字符级拆分另按 [字符级时间证据](character-timing-evidence.md) 保存对齐摘录、字符索引/偏移、构轴公式和实际参数，由主代理作为相关工单的默认附件传递。`asr_words` 保留原始词时间，不为匹配显示尾改写含标点的词尾；证据包属于项目侧文件，不改现有字幕、chunks/apply 或 semantic_windows 的数据形状，这些脚本也不自动补齐附件。

import-srt/import-asr 以及 AssemblyAI 导出保持通用数据协议，不会自动填入 workflow 或声称看过视频。执行代理在导入后另存 source.context.v01.json，设置 `meta.workflow=vidove-inspired-v1`，按实际结果补充 vision_status、visual_cues、source_notes，以及上下文摘要；按 [领域术语协议](terminology.md) 完成 prepare、全片发现/联网和 bind，生成 source.terms.vNN.json 再分批。保留原始 source.json 作为 ID 覆盖基线。

有视频时先按 [GPT 视觉执行协议](multimodal-context.md) 使用 vision_workbench.py 抽帧/打包→视觉子代理实际看图→主代理验收并 apply 写回。可逐批更新 source.context.vNN.json，未处理行保留原状态；全片该阶段处理完毕后再进入翻译。视觉 apply 保留现有 draft，但清空受影响的 target 和校对/编辑记录，需要核查初译是否应重译。

## 导入与批次

```text
python <skill>/scripts/subtitle_workbench.py import-srt original.srt source.json --duration-ms 7200000
python <skill>/scripts/subtitle_workbench.py import-asr asr.json source.json --offset-ms 0
python <skill>/scripts/subtitle_workbench.py chunks source.terms.v01.json batches-translate --max-cues 60 --max-chars 6000 --context 3
```

SRT 导入支持 UTF-8/BOM、CRLF、小时数超过两位和点/逗号毫秒；拒绝格式异常和空字幕，不静默跳过。其他编码先确定编码再转 UTF-8。脚本不原生导入 VTT/ASS；遇到这两种输入先用已验证工具转换，同时保留说话人/样式等需要的信息。ASR import 不静默删除空识别块，先检查其是否真的无人声并记录排除理由。多个 ASR 块导入后需执行代理加偏移、去重、重编号和核查再形成全片 source.json，脚本不自动拼接。

游戏项目生成批次前，按 [workflow.md](workflow.md) 在 meta 记录 `translation_prompt` 的 profile/version/sha256，并实际读取 [游戏字幕提示词 v2](gameplay-translation-prompt-v2.md)。此元数据会随 project_meta 进入各阶段，但不代表脚本自动执行提示词。

每批 JSON 包含 batch_id、input_digest、本批 segments、只读 context_before/context_after、project_meta，片段携带的 visual_cues/audio_cues 原样进入批次。每个源 ID 只属于一个本批区间。单条超预算保留完整。术语由 glossary_workbench.py bind 冻结为 terminology 快照，摘要写入 meta；每条 term_context 自动进入批次并参与有效性校验，外部库更新须重新 bind。概要/视觉记忆的外部文件变化仍需执行代理更新 meta 并重建批次。

翻译响应的 JSON 形状如下，batch_id 和 input_digest 必须从实际批次原样复制，不使用示例值：

```json
{"batch_id":"b00001","input_digest":"copy-from-batch","segments":[
  {"id":"s000001","draft":"保留原意"}
]}
```

```text
python <skill>/scripts/subtitle_workbench.py apply source.terms.v01.json batches-translate/b00001.json result-translate-1.json draft.v01.json --stage draft
```

后续批基于最新 draft.vNN.json 依次写入新版本。核心条目和原文上下文不能改变；邻批草稿变化不阻止应用。上下文译文如有实质纠正，执行代理须主动重审相关批次。每次写入均要求本批 ID 完整唯一，不接受多条/少条/重复条，不改时间轴或原文。整批验证失败不保存目标文件。已存在的文件/输出目录均拒绝覆盖。

## 校对建议与编辑定稿

从最新完整 draft 重新生成 `batches-proofread`。校对建议响应：

```json
{"batch_id":"b00001","input_digest":"copy-from-batch","segments":[
  {"id":"s000001","proofreading_status":"PASS","suggestions":[],
   "proofreading_note":"原文含义及语气准确，无需修改。"}
]}
```

```text
python <skill>/scripts/subtitle_workbench.py chunks draft.v10.json batches-proofread
python <skill>/scripts/subtitle_workbench.py apply draft.v10.json batches-proofread/b00001.json result-proofread-1.json proofread.v01.json --stage proofread
```

PASS 要求 suggestions 为空；REVISE/UNCLEAR 要求提供具体建议/疑点。proofread 阶段拒绝 draft/target 替换字段，仅写建议并记录其输入摘要，不能让文件通过最终字幕验收。

所有校对批完成后，从最新 proofread.vNN.json 重新生成 `batches-edit`。编辑响应：

```json
{"batch_id":"b00001","input_digest":"copy-from-batch","segments":[
  {"id":"s000001","target":"保留原意","review_status":"reviewed",
   "review_note":"复核 PASS 建议成立，保留译法；该任务只有原文字幕，未做音画核验。","issues":[]}
]}
```

```text
python <skill>/scripts/subtitle_workbench.py chunks proofread.v10.json batches-edit
python <skill>/scripts/subtitle_workbench.py apply proofread.v10.json batches-edit/b00001.json result-edit-1.json edited.v01.json --stage edit
```

编辑输入应含校对建议、音画证据、term_context 和相关记忆；编辑说明写出采纳/驳回依据。未解决疑点用 needs_review 和 issues，target 保留暂定译法；reviewed 不允许未决 issues。edit 要求已有与当前输入一致的校对记录。脚本内部记录 proofreading_input_digest 和 editing_digest，不由模型生成这两个值。

重写 draft 清空旧 target、建议和编辑记录；手工修改 source/timing/visual_cues/meta 后重新校对，再编辑。最终稿改变 target/说明也会触发编辑摘要失效；仅断行后同样需要复核。历史版本保持不变。

兼容性：旧任务未设置 `meta.workflow` 时仍可用旧 `--stage review` 合并审校。新 ViDove 流程拒绝直接 review，要求 proofread→edit；qa/export 检查二者摘要及实际 vision_status。迁移旧任务时另存副本、设置 workflow 和视觉状态，然后基于已有 draft 执行新校对/编辑，不把旧 reviewed 自动等同于新流程完成。

## 检查与导出

默认主交付为单语；游戏中文单行模式使用以下参数，不加 `--wrap`，保留编辑稿每条 target 的一个文本行：

```text
python <skill>/scripts/subtitle_workbench.py qa edited.vNN.json qa-main.vNN.json --baseline source.json --max-lines 1
python <skill>/scripts/subtitle_workbench.py export edited.vNN.json exports-main-zh-Hans-vNN --baseline source.json --max-lines 1
python <skill>/scripts/subtitle_workbench.py verify-export exports-main-zh-Hans-vNN
```

可选双语审查版从同一编辑稿导出，目标在上、原文在下，各一个文本行；先在版本稿处理原文内部换行且保留文本、ID 和时间，并重新审校，不在导出文件上手工修改：

```text
python <skill>/scripts/subtitle_workbench.py qa edited.vNN.json qa-review.vNN.json --baseline source.json --bilingual --bilingual-lines 1 --bilingual-total-lines 2
python <skill>/scripts/subtitle_workbench.py export edited.vNN.json exports-review-en-zh-Hans-vNN --baseline source.json --bilingual --bilingual-lines 1 --bilingual-total-lines 2
python <skill>/scripts/subtitle_workbench.py verify-export exports-review-en-zh-Hans-vNN
```

`--max-lines` 和 `--bilingual-lines` 是检查阈值，不会自动压缩或消除换行。逐条比较两版目标语言正文及时间，改稿后两版同步从同一新版本生成。10–15 个汉字是措辞偏好，不应把 max-width 改成硬性删字上限；超宽、CPS 及播放器自动折行仍需检查并记录。已有 SRT/VTT 的序号/标识和有效设置应与原件逐条核对：本脚本会按输出顺序编号且不保留全部 VTT 元数据，若不能无损保留，应走保留原结构的正文替换路径并另外质检，不能宣称通用导出已保留这些信息。

只有项目允许多行（如非游戏内容或用户显式要求）时，才使用共用显示整形：

```text
python <skill>/scripts/subtitle_workbench.py qa edited.vNN.json qa.vNN.json --baseline source.json --bilingual --wrap --target-width 42 --source-width 72 --bilingual-lines 2
python <skill>/scripts/subtitle_workbench.py export edited.vNN.json exports-vNN --baseline source.json --bilingual --wrap --target-width 42 --source-width 72 --bilingual-lines 2
```

qa 和 export 使用相同排版函数，layout.json 保存实际显示轨道及时间、文本摘要，并纳入导出哈希。`--wrap` 仅增减显示空白、保留非空白字符，不修改 source、target、术语绑定、校对批准或时间；review.csv 仍记录语义原稿。原稿已有显式断行予以保留，自动新增折行避开绑定术语、拉丁词和行首标点。超过宽度的不可拆名字保留完整并报告，不能截断或自动换称呼。42/72 是示例偏好，不是所有分辨率的固定规范；像素效果仍要渲染。

`--target-width` / `--source-width` 未指定时沿用 `--max-width`。不加 `--wrap` 保留旧显示行为。总行数、速度、宽度按同一显示结果检查，原始控制字符/空行仍检查。折行不会降低 CPS；出现 source_lines 或 bilingual_total_layout 时，在启用时间轴重制时可用真实词级时间重分段并重走校对/编辑，或明确交付为暂停阅读对照版，不声称排版已全部验收。

双语还检查 `--bilingual-total-lines`（默认 4）：按每行宽度估计自动折行后的两轨总行数，作为排版警告，不是像素级认证。必须渲染最拥挤及高速句核对字体、总行数和 HUD 遮挡；增加每轨允许行数不能自动解决屏幕拥挤。英文过快时可交付适合暂停阅读的对照版并说明，不通过任意延长时间或漏掉英文消除提示。

export 额外生成 delivery-manifest.json，将字幕、CSV、QA 和术语快照绑定到本次导出。交付/冻结前运行 `python <skill>/scripts/subtitle_workbench.py verify-export <exports-dir>`；发现改动须回到版本稿更新、重新导出及检查，不能在旧 QA 旁手改 SRT。渲染记录另存，并注明所用字幕文件 SHA-256；verify-export 只验证文件未变，不声称已看过画面。

```text
python <skill>/scripts/subtitle_workbench.py qa edited.v10.json qa.v01.json --baseline source.json --max-lines 1
python <skill>/scripts/subtitle_workbench.py export edited.v10.json exports-srt --baseline source.json --max-lines 1
python <skill>/scripts/subtitle_workbench.py export edited.v10.json exports-vtt --baseline source.json --max-lines 1 --format vtt
python <skill>/scripts/subtitle_workbench.py export edited.v10.json exports-ass --baseline source.json --max-lines 1 --format ass
```

示例目录名已区分单语主版与双语审查版；保留目录内脚本文件名以维持 delivery-manifest.json 的校验，不在导出后单独改名。交付说明先列主版，审查版另列；用户要求语言后缀时可另做交付副本并记录与已验证导出文件的哈希对应关系。

每次导出产出 final.srt/vtt/ass、UTF-8 BOM review.csv（含校对建议、编辑说明、视觉状态和术语依据/例外）、qa.json；已绑定术语时另附 glossary.snapshot.json。领域术语 CSV 用 glossary_workbench.py export 生成。`--source-first` 调换双语顺序，不加 `--bilingual` 导出目标单语。`--draft` 允许未完成校对/编辑的预览并命名 draft。最终版必须提供最初 source.json 作为 baseline 并且无错误。缺 baseline 的 qa 只自检，不能证明完整覆盖。视觉检查只判断状态和记录存在，不验证帧内容或模型判断。

qa 返回 0 为无技术/字面术语错误（可能有警告）、2 为存在检查错误、1 为命令/数据结构错误。全部输出拒绝覆盖；改用新版本文件/目录。脚本不自动做翻译、语义切分、时间拉伸、字体检测、听校或视频压制。最早源 ID 完整覆盖仍不能证明内容语义无漏译。

可调参数：`--duration-ms`、`--min-seconds`、`--max-seconds`、`--max-width`、`--max-lines`、`--bilingual-lines`、`--target-cps`、`--source-cps`。单语行数由 max-lines 控制，双语为每轨 bilingual-lines。重叠列警告，以便保留真实同时讲话；只有确认错误才改时间。

ASS 导出把反斜杠/花括号替换成全角字符以免解释成覆盖标签；源标签在 SRT/VTT 中按字面文本转义。检查 markup 警告并在排版稿清理无用标签，复杂样式应使用专门工具。CSV 对可能被表格软件执行的公式前缀加单引号；原 JSON 保留原文。

## 可读性修订计划与最终导出门槛

精简/整形前保存含 draft 的原稿；改写或拆合后使用 [保真复核及 revision_audit.py](semantic-preservation.md) 比较前后完整语义组。该工具只读、拒绝覆盖，使用显式 ID/parent_ids；不检测语义正确性，不自动修改审定状态。结构错误返回 2；没有结构错误返回 0 也不能代替 GPT 对差异的具体复核。

新增只读辅助工具：`evidence_windows.py` 按词级覆盖列出未转写空隙及可选局部抽帧计划，使用方法和证据限制见[疑点证据协议](review-evidence.md)与[音画证据](multimodal-context.md)；`timing_anchor.py` 提供唯一文本/原始词锚点定位函数，使用方法见[保真复核](semantic-preservation.md)。两者都不自动翻译、听校、看图或设置审定状态。

命令行的原 SRT 导入默认 `meta.timing_mode=fixed`，但作者字幕配合原媒体制作时，执行代理须传 `--timing-mode rebuild` 或在新版本设置 rebuild，不能将导入器默认当成制作要求。原始 ASR 导入默认 rebuild；可用 `import-*/--timing-mode` 覆盖。后端直接生成 source.json 时，由执行代理根据用户任务写入该字段。旧稿缺失字段时计划工具保守按 fixed 处理，不暗改历史。

```text
python <skill>/scripts/subtitle_workbench.py timing-plan edited.vNN.json timing-plan.vNN.json
```

该命令只写建议，不改输入。以源版本摘要绑定问题；fixed 不建议改轴，rebuild 可建议检查最多 500 毫秒的尾部显示延长，或相邻片段语义重分段。延长受下一条开始、实测媒体末尾及已提供的 `meta.scene_boundaries_ms` 限制；缺少实测媒体时长时不建议延长。候选合并需要执行者核对说话人、句义及真实停顿，缺少 speaker 字段不等于已经证明同一人。建议不是听校结论，不能直接自动应用整个计划。

接受显示延长时不改变 `asr_words` 的真实讲话时间；另存版本并重做受影响的校对/编辑和视觉关联。拆合还需保持 parent_ids、重新发现并绑定术语。SRT 时间轴固定的任务仍有不可读内容时交付说明充分的草稿，不擅自改轴。

qa/export 新增 `flash_duration` 错误：默认不足 0.5 秒阻止 final 导出，`--draft` 仍可交付审查稿。0.5–0.8 秒、超 7 秒、行宽及 CPS 保留可调整的制作提示。`--flash-seconds` 必须大于零且不超过 `--min-seconds`；只有项目明确采用不同规范时才调整并记录理由，不能为让测试通过而降低门槛。`summary.evidence_coverage_warnings` 与 `other_warnings` 分开汇总，计数是检查项数，不能称为误译条数。
