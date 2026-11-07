# 同一词流入口与残缺源句约束

适用于 MAI、WhisperX 及同格式真实词时间的 rebuild 任务。ASR 选型只改变原始识别词与时间；显示制作不按后端分叉。固定轴不调用重组，只有句级时间也不伪造内部词界。

## 源稿与制作岗位

保留只读 reference：schema_version 1，segments 内 id、source、start_ms/end_ms、asr_words；词含 word、start/end 秒数，父段 asr_words_offset_ms 仅加一次。先解决音频切块重叠、词序倒退等输入问题；脚本拒绝不明重复或倒序，不自动删词。没有时间的内部词可包含在有真实首尾边界的组内；边界不能定位时回源稿处理，不均摊时间。

1. 按完整片段读词流，写 boundary plan。回答、否定转折、自我纠正、后揭晓信息、场景切换、说话人交接、明确独立反应前，用 hard；普通可跨句读可用 soft。不得以“所有父段都是 hard”代替语义判断。无 plan 的 prepare 只是未审初案。
2. 同一 `unified_cues.py prepare` 对全词流动态规划，保留精确 source_word_ranges。父段只作溯源，不成为显示边界。实际源句改字需另有证据记录，不能覆盖原始词。
3. 译者按完整语义单位保存 target，不必每个 ASR 小段一个译句；拆译只用已有词界。自然承接的单位可标 `join_next:true` 与具体 `join_reason`；下一条 `break_before:true` 否决合并。审校检查该边界是否提前答案或包袱。
4. 同一 `compose` 仅在明确允许的边界合并完整已译单位，单行模式不折行，多行模式按配置折行；不改写、不自动删文、不按目标字数拆轴。仅按下节有效批准的填充声省略显示。输出保存全量原 units、每条 unit_ids、真实 speech_start_ms/speech_end_ms 和显示 start_ms/end_ms。issues 的稳定 unit_ids 保留，cue_ids 随制作更新；再次改译文回到 units，再 compose。

默认偏好：0.8–7秒、54显示列、单行、12 CPS；双语或用户明确多行时显式配置。实际宽度另用真实字体像素与渲染检查。prepare 只做源流初案，不能评价尚未出现的译文宽度。参数写入文件，prepare/compose 和最终 display_profile 使用相同阈值；不因换后端改阈值。版本、参数、输入规范化 JSON 哈希记录在 meta.unified_cues。长语义单位不能自动合法拆开时保留风险，交译审局部拆分；不能靠放宽阈值或清空台词过检。

显示默认从真实语音起点到词尾，不自动补停顿。尾留在最后一个 unit 写 `tail_evidence:{reviewed:true,reason,evidence,start_ms,end_ms,display_end_ms}`；已审区间须覆盖词尾到显示尾，不越过下一真实词起点或场景边界，默认不超过750ms。未知下一词起点不延尾；合并后内部 unit 的尾留不沿用。字段只记录实际审阅声明，机器不认证听看。真正同时发言的重叠应保留、分轨或留风险，不裁词尾凑零重叠。

r4.8：短条先判断语义，普通连续句才可局部改合并许可；独立回答、自我纠正和后揭晓仍保留出现时机。尾留可用 `tail_proposals.propose(units_doc, reference, intervals, parameters)` 生成未审建议。intervals 每项为 `unit_id,start_ms,end_ms,reason,evidence`，必须是源岗实际取证得到的可用显示区间；不能仅把 ASR 词间空隙宣称静音。算法只补到当前最小停留/阅读所需时长，并受证据终点、750ms上限、下一词前80ms间隙和最大时长共同限制；不足时仍报剩余风险。建议默认 `reviewed:false`，不能直接 compose。另一位审校核对证据和语义节奏后才可采用，保留实际审阅范围；声学测量、切镜检测或静态图不能冒充听辨/动态验收。无足够证据就不延尾。

新提议的 binding_sha256 绑定该 unit 的源文、非空白译文、词范围和发声区间；改动后拒绝沿用旧尾留。它只防失配，不认证证据真实性。`keep_together` 从 units 传到实际 cues，compose 用均衡折行保护显式短语；check 新增 orphan_line/split_phrase 风险，不能以新阈值隐藏旧风险。MAI 与 WhisperX 使用同一流程和参数。

```text
PYTHON SKILL/scripts/unified_cues.py prepare reference.json --plan boundaries.json --parameters parameters.json --output source.v1.json
PYTHON SKILL/scripts/unified_cues.py compose reviewed-units.json --reference reference.json --parameters parameters.json --output display.v1.json
PYTHON SKILL/scripts/subtitles.py check display.v1.json --reference reference.json --status draft
```

plan 例：`{"boundaries":[{"before":{"parent_id":"p3","word_index":4},"kind":"hard","reason":"回答在这里才出现"}]}`。参数 JSON 为平面对象，如 `{"max_lines":1}`；其他字段见脚本 DEFAULT_PARAMETERS。所有输出要求新路径。reference 已有源疑点由源岗先迁移到新语义单位；prepare 拒绝静默丢弃旧 issues/source_constraints。

## r4.12 原词保留与显示省略

只允许源岗或译者结合完整语义组提出、另一位审校确认无信息作用的填充声省略显示。不按 `oh / um / uh / well` 等词表自动删除；回答、否定、限定、态度、犹豫表现、人物反应和笑点不能因短促或难排版被删。句内自然压缩沿用语言规则；整条无显示须有以下明确记录。含实质内容的 unit 先在真实词界拆出填充声，再逐个核对范围与边界，不能把整条置空来解决显示风险。

在拟省略 unit 保留 `source`、`source_word_ranges`、时间及其他原字段，令 `target` 精确为 `""`，增加：

```json
{"display_omission":{"category":"nonsemantic_filler","reason":"本处为何不承载信息或表现作用","evidence":"实际读到的源语邻文或实际音画证据定位","proposed_by":"实际提出者","source_context":{"before":[],"words":[],"after":[]},"reviewed_by":"另一位实际审校","reviewed_sha256":"核对实际保存稿后取得的哈希"}}
```

使用 `display_omissions.propose(unit, reference, reason, proposed_by, evidence)` 生成未审对象，自动保存对应原始词及前后各最多12词（含原始 ASR 数据、偏移和实际时间）。这些邻词是可核验的最小记录，不替代完整语义组阅读；更远的指代、铺垫或问答证据记在 evidence。独立审校核对后填 reviewed_by，再运行 `PYTHON SKILL/scripts/display_omissions.py reviewed-units.json --reference reference.json --fingerprint 单位ID`，或调用 `fingerprint(unit, reference)`，填入 reviewed_sha256。计算哈希不表示批准。绑定包括原词、范围、时间、邻文、实际源文、理由、证据和双方身份；相关内容改变须复核，过期绑定会拒收。

`compose` 保留全部 units，并将有效省略单元完整复制到 `omitted_units`；这些单元不产生空 cue，也不把其时间分配给前后句。省略位置阻止跨越并接，原有 hard 边界继续有效，后句仍从自身真实语音开始。显示词范围与有效省略词范围的并集必须恰好覆盖 reference 一次，不重复、不缺词；check/export 重新验证批准及联合覆盖，不信任文件里的 coverage 声明，草稿也须给原始 reference。报告区分 displayed_words、omitted_words 和 words_checked，映射完整不证明没有语义遗漏。

未决问题涉及的单元不得通过显示省略绕过 source_guard；先按证据解决原问题，保留 issues 原记录与独立处置绑定。已解决问题保留 unit_ids，cue_ids 仅指向仍显示的部分，另存 omitted_unit_ids；全省略作用域的 cue_ids 可为空。单独运行 source_guard 审核省略稿时也须给 `--reference reference.json`；计算问题 fingerprint 的原接口不变。全片都无显示时只保存工作稿并说明，不导出空字幕。普通空 target、缺批准、同人复核或过期记录均拒收；这不是语义判定器，最终判断仍由独立保真审校承担。

## 一个疑点，从源稿约束到实际译文

仅实质不确定源句增加以下字段，仍放原 issues 队列，不增加岗位或正常句逐条审批：

```json
{
  "id":"q1", "unit_ids":["u12"], "cue_ids":["u12"],
  "status":"provisional", "detail":"动作谓语未能辨认",
  "source_constraint":{
    "unknown":"哪个动作未知", "safe_content":"有证据的已知部分，或明确无可用部分",
    "forbidden_inference":["不得从邻句主题补出该动作"],
    "risk":"predicate", "material":true
  },
  "disposition":{
    "action":"safe_partial", "translator":"实际译者身份", "reviewer":"另一位实际审校身份",
    "rationale":"保存译文如何只表达已知部分", "evidence":"实际证据位置，缺失则如实说明",
    "reviewed_sha256":"审校核对保存稿后取得的内容哈希"
  }
}
```

源岗写 unknown、safe_content、forbidden_inference、risk、material；审校先读原话与证据，再读解释。译者在同一 issue 选处置，审校直接修订实际 target，不能只写“保留疑点”却保留原猜测：

- `hold`：目标留空，仅保留工作 JSON，整份字幕导出被阻止。已知极性冲突、关键动作/对象没有可支持表达时使用；不要用“也许”或模糊话消解相反含义。确定邻接单位可另存为明确标范围的片段，不能暗删问题区间后声称全片完成。
- `safe_partial`：只表达稳定已知部分，不补动作、对象、因果、程度；保持 open/provisional，仅草稿。不能用省略号伪装说话人实际中断；ASR 残缺是制作限制，交付说明标明。未知极性 risk=polarity 不允许此方式。信息不够形成可理解且有用的部分时 hold。
- `verified_repair`：有针对性的实际听辨、可信作者台词等证据，独立审校确认，status=resolved。多模型一致、邻句主题或大家赞成不等于原声证据。仅有文本时明确其能支持的限度。

人物疑点按[实体证据规则](entity-evidence.md)处理：原始词和词时间照旧保留，若证据已经消除影响译文的指称歧义，可以仅解析指称而不宣称原声词形已确认。此类实质身份修订在 disposition 内保存 entity_evidence，声明 resolution_scope；只解决部分槽位仍用 safe_partial，未知动作/极性/因果不能因人名解析而被补出。独立审校必须检查已知信息是否被过度删除，也检查新增姓名是否有实际支撑。

独立审校核对实际保存的源文、译文、词范围和禁补内容后，运行 `source_guard.py 文件 --fingerprint 问题ID`，将值填入 disposition.reviewed_sha256。命令只算哈希，不代替审校、也不自动批准。改变相关源文、译文、范围、约束或处置，复核该完整语义组；无关条目、显示时间调整、等价断行不触发全片重审。

subtitles.py check/export/assemble 集成检查，source_blockers 与结构/显示问题分开。旧稿 open/provisional 源问题缺少处置时必须迁移，不能以 draft 绕过；纯显示问题用 kind=display。正常口语碎句、反讽、敏感题材和有上下文的省略不因关键词或句长自动阻塞。未被岗位发现的语义错误仍可能漏检；脚本不是语义判真器，meaning_checked 始终 false。
