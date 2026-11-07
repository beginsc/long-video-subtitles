# 轻量文件约定与离线工具

项目目录建议：project.json（范围、偏好、章节状态、执行者、下一步）、source/（原始 ASR 和整理稿）、context.md、glossary.json、work/（各岗位输出）、exports/、issues.json。按实际需要创建。不要为每个 cue 建目录或逐条 PASS 文件。

持久领域库的位置由用户配置统一保存，不放在单部视频目录或随技能更新覆盖。`terminology/loaded.vN.json` 保存本片加载快照，`glossary.json` 保存实际相关词义与片内约定；有新增时保存 `terminology/new-confirmed.vN.json` 和写回结果 `promotion.vN.json`。具体命令和合并规则见[持久领域库](terminology.md)。

## 当前稿

r4.12 的无信息填充声显示省略按[原词保留与显示省略](unified-workflow.md#r412-原词保留与显示省略)执行：全量 units 与独立审校后的 omitted_units 一起保存，成品 segments 只含有显示文字的条目。覆盖检查联合验证显示与省略词范围，不能删掉原词或问题记录来凑齐 coverage；省略稿的 draft/final 检查与导出都需要原始 reference。

r4.7 有真实词时间的 rebuild 使用[统一后处理及源句处置格式](unified-workflow.md)。issues 仍为单一队列，残缺源句要带 source_constraint 和 disposition；未处置的源问题即使 draft 也不能导出。经独立审校的 safe_partial 可以导出草稿，hold 只能保存工作 JSON；纯显示问题标 kind=display。下述一般 draft 规则不能绕过源句约束。check 新增 source_blockers，compose 同时保留原 units 与当前显示 cue 的 unit_ids；不要只编辑显示副本或丢弃原 units。

UTF-8 JSON，整稿或片段使用同一结构：

```json
{"schema_version":1,"timing_mode":"fixed","segments":[
  {"id":"1","start_ms":1000,"end_ms":3500,"source":"It doesn't work that way.","target":"不是这么用的"}
]}
```

source.json 原件只读，修订写 source.v2.json 等新文件。工作片段保留核心条目的原 ID；邻条另放上下文文件，不写入本段结果。原文修订另存 notes.json，仅包括 ID、改前/改后和依据。rebuild 拆合产生新 ID 并记录 parent_ids；同一个父 ID 可拆成数条，不能把这种覆盖当成没有丢信息的证明。

已有 ASR 词列表时，源稿/制作岗位同时保留 `source_word_ranges`，相对原始父段 `asr_words` 的零起始索引，左闭右开。例如 `{"parent_id":"a01","start_word_index":0,"end_word_index_exclusive":6}` 表示该父段前六个词；跨父段的 cue 可有多个范围。用原始源稿作 reference 检查缺口和重复，后续拆合同步更新映射。词级元数据集中在源稿和制作文件，语言岗位可接收轻量稿；组装时由制作员恢复对应关系。仅有句级证据时保留父段映射和检查限制，不为凑字段伪造词对齐。

project.json 记录当前稿路径及各章翻译/审校/制作状态，员工身份、实际覆盖范围与重要未决项；同时记录所选领域、源语言、目标地区、语域、库根目录与加载版本、快照路径和有新增时的写回结果。组长更新当前指针前读取新文件并做检查。无需给每个正常句填写 review_status、摘要或签名；旧后端输出中的 pending 等旧字段不是新版的审校状态，不用旧字段驱动新流程。

新任务同时记录准确 skill 分支/版本、规则快照、实际模型/推理设置（不可取时记 unknown）、ASR 实际配置，避免把安装目录与候选混用。显示配置放当前稿 `display_profile` 或单独 JSON 用 `--profile` 传入；简中游戏推荐 `{"preset":"zh-game"}`。可覆盖 `max_lines、max_width、target_cps、min_duration_ms、max_duration_ms、flash_ms、bilingual_target_lines、source_max_lines、source_max_width、source_cps`；全部为正数，时间单位毫秒，宽度为近似显示列。未知字段及无效数值报错，不静默忽略。修改阈值要有项目理由，不为过检查放宽。

仅实质未决事项放 `issues`：如 `{"cue_ids":["1"],"status":"open","detail":"否定所指对象待核验"}`，状态为 open/provisional/resolved；拆合后同步改为当前 cue ID。无问题可省略，不为每条建状态。resolved 是执行者的判断，不是工具核实。影响原话/解释的改变按 team.md 重查实际关系。

## 工具命令

`PYTHON` 表示当前可用的 Python 3，`SKILL`/`JOB` 替换为实际绝对路径；在 PowerShell 中用 `& 'python绝对路径' '脚本绝对路径' ...`。所有写命令要求新输出路径，保留上版。

```text
PYTHON SKILL/scripts/subtitles.py import input.srt --output JOB/source/original.json
PYTHON SKILL/scripts/subtitles.py check JOB/source/original.json --source-only
PYTHON SKILL/scripts/subtitles.py assemble JOB/work/reviewer/ch01.json JOB/work/reviewer/ch02.json --reference JOB/source/original.json --mode fixed --output JOB/final.v1.json
PYTHON SKILL/scripts/subtitles.py check JOB/final.v1.json --reference JOB/source/original.json --mode fixed --status final
PYTHON SKILL/scripts/subtitles.py export JOB/final.v1.json --reference JOB/source/original.json --mode fixed --status final --format srt --output JOB/exports/zh.v1.srt
PYTHON SKILL/scripts/subtitles.py index-map JOB/final.v1.json --exported JOB/exports/zh.v1.srt --output JOB/exports/zh.v1.index-map.json
PYTHON SKILL/scripts/subtitles.py export JOB/final.v1.json --reference JOB/source/original.json --status final --format vtt --bilingual --output JOB/exports/bilingual.v1.vtt
PYTHON SKILL/scripts/subtitles.py export JOB/final.v1.json --status draft --format ass --output JOB/exports/draft.zh.v1.ass
```

工具提供 SRT、基本 VTT、后端 source.json 导入，检查、片段组装和基本 SRT/VTT/ASS 导出。fixed 检查 ID、数量、顺序和时间；rebuild 检查 parent_ids，并在当前稿有词范围、reference 有原始词列表时检查词索引缺口、重复和越界。报告中的 coverage 区分词范围、混合与仅父段覆盖；没有词范围时不能声称已查到词级。映射完整仍不证明译文没有遗漏，meaning_checked 始终为 false，语言质量来自实际审校。

`check` 默认 draft；`export` CLI 必须显式选择 `--status draft/final`。final 必须带原始 reference，不能用当前稿自己作为 reference 来证明保留原轴或全覆盖。结构错误两种状态都拒收；final 还阻止未处置显示风险及 open/provisional 问题。draft 可保留风险用于审阅，但要用草稿文件名和交付说明。单语通过不代表双语通过，双语需以 `--bilingual` 独立检查原文轨。

例外数组可放在稿内 `display_exceptions`，或独立 JSON 文件通过 `--exceptions` 提供，例如 `[{"risk_id":"复制check的实际risk_id","reason":"保留原轴且此处为独立短反应","evidence":"原件序号和实际查看区间；未动态观看时如实注明"}]`。不得照抄示例当真实证据。risk_id 只针对相关 cue 与配置，改字改轴后会失效；无关修改不会使全部例外失效。返回 `structural_ok、display_risks、unhandled_risks、unresolved_issues、final_blockers` 分开说明情况，ok 不代表语义正确。导出 stdout 包含实际 status 和 check，可由组长保留到项目记录，不另起证明工单。

SRT 在 fixed 模式保留数字编号，rebuild 模式按导出顺序编号；JSON/VTT 保留当前 cue ID。成品重新编号时，用 index-map 从实际导出文件建立当前 cue ID 到成品编号的映射，便于定位疑点。带 VTT settings/STYLE/REGION/NOTE 或复杂 ASS 请使用支持保留这些内容的转换/编辑工具，本脚本会拒绝不支持的导入。ASS 输出为基础新样式，不承诺往返保留源 ASS；大括号、反斜杠等控制语法需专用制作工具处理。

## 导出文件检查

导出后至少读取实际文件；index-map 会回读 SRT/VTT 并核对条目数、实际文本和时间，双语加 --bilingual，通过后无需再重复 import 同一文件。也可用既有等效回读工具完成检查和映射。ASS 检查实际渲染，注意毫秒到厘秒的精度变化。双语与单语从同一当前稿生成。不要只检查 JSON 就说成品检查过。

脚本执行所请求状态的结构和显示准入，不裁定疑点或认证独立审校，也不提供精确模型账单、全自动强制对齐或无人值守调度。组长依据实际语言审校、音画范围及工具结果设置项目完成状态。
