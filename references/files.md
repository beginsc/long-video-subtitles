# 轻量文件约定与离线工具

项目目录建议：project.json（范围、偏好、章节状态、执行者、下一步）、source/（原始 ASR 和整理稿）、context.md、glossary.json、work/（各岗位输出）、exports/、issues.json。按实际需要创建。不要为每个 cue 建目录或逐条 PASS 文件。

持久领域库的位置由用户配置统一保存，不放在单部视频目录或随技能更新覆盖。`terminology/loaded.vN.json` 保存本片加载快照，`glossary.json` 保存实际相关词义与片内约定；有新增时保存 `terminology/new-confirmed.vN.json` 和写回结果 `promotion.vN.json`。具体命令和合并规则见[持久领域库](terminology.md)。

## 当前稿

UTF-8 JSON，整稿或片段使用同一结构：

```json
{"schema_version":1,"timing_mode":"fixed","segments":[
  {"id":"1","start_ms":1000,"end_ms":3500,"source":"It doesn't work that way.","target":"不是这么用的"}
]}
```

source.json 原件只读，修订写 source.v2.json 等新文件。工作片段保留核心条目的原 ID；邻条另放上下文文件，不写入本段结果。原文修订另存 notes.json，仅包括 ID、改前/改后和依据。rebuild 拆合产生新 ID 并记录 parent_ids；同一个父 ID 可拆成数条，不能把这种覆盖当成没有丢信息的证明。

已有 ASR 词列表时，源稿/制作岗位同时保留 `source_word_ranges`，相对原始父段 `asr_words` 的零起始索引，左闭右开。例如 `{"parent_id":"a01","start_word_index":0,"end_word_index_exclusive":6}` 表示该父段前六个词；跨父段的 cue 可有多个范围。用原始源稿作 reference 检查缺口和重复，后续拆合同步更新映射。词级元数据集中在源稿和制作文件，语言岗位可接收轻量稿；组装时由制作员恢复对应关系。仅有句级证据时保留父段映射和检查限制，不为凑字段伪造词对齐。

project.json 记录当前稿路径及各章翻译/审校/制作状态，员工身份、实际覆盖范围与重要未决项；同时记录所选领域、源语言、目标地区、语域、库根目录与加载版本、快照路径和有新增时的写回结果。组长更新当前指针前读取新文件并做检查。无需给每个正常句填写 review_status、摘要或签名；旧后端输出中的 pending 等旧字段不是新版的审校状态，不用旧字段驱动新流程。

## 工具命令

`PYTHON` 表示当前可用的 Python 3，`SKILL`/`JOB` 替换为实际绝对路径；在 PowerShell 中用 `& 'python绝对路径' '脚本绝对路径' ...`。所有写命令要求新输出路径，保留上版。

```text
PYTHON SKILL/scripts/subtitles.py import input.srt --output JOB/source/original.json
PYTHON SKILL/scripts/subtitles.py check JOB/source/original.json --source-only
PYTHON SKILL/scripts/subtitles.py assemble JOB/work/reviewer/ch01.json JOB/work/reviewer/ch02.json --reference JOB/source/original.json --mode fixed --output JOB/final.v1.json
PYTHON SKILL/scripts/subtitles.py check JOB/final.v1.json --reference JOB/source/original.json --mode fixed
PYTHON SKILL/scripts/subtitles.py export JOB/final.v1.json --format srt --output JOB/exports/zh.v1.srt
PYTHON SKILL/scripts/subtitles.py index-map JOB/final.v1.json --exported JOB/exports/zh.v1.srt --output JOB/exports/zh.v1.index-map.json
PYTHON SKILL/scripts/subtitles.py export JOB/final.v1.json --format vtt --bilingual --output JOB/exports/bilingual.v1.vtt
PYTHON SKILL/scripts/subtitles.py export JOB/final.v1.json --format ass --output JOB/exports/zh.v1.ass
```

工具提供 SRT、基本 VTT、后端 source.json 导入，检查、片段组装和基本 SRT/VTT/ASS 导出。fixed 检查 ID、数量、顺序和时间；rebuild 检查 parent_ids，并在当前稿有词范围、reference 有原始词列表时检查词索引缺口、重复和越界。报告中的 coverage 区分词范围、混合与仅父段覆盖；没有词范围时不能声称已查到词级。映射完整仍不证明译文没有遗漏，meaning_checked 始终为 false，语言质量来自实际审校。长短、阅读速度和重叠是警告，由制作员按场景判断。

SRT 在 fixed 模式保留数字编号，rebuild 模式按导出顺序编号；JSON/VTT 保留当前 cue ID。成品重新编号时，用 index-map 从实际导出文件建立当前 cue ID 到成品编号的映射，便于定位疑点。带 VTT settings/STYLE/REGION/NOTE 或复杂 ASS 请使用支持保留这些内容的转换/编辑工具，本脚本会拒绝不支持的导入。ASS 输出为基础新样式，不承诺往返保留源 ASS；大括号、反斜杠等控制语法需专用制作工具处理。

## 导出文件检查

导出后至少读取实际文件；index-map 会回读 SRT/VTT 并核对条目数、实际文本和时间，双语加 --bilingual，通过后无需再重复 import 同一文件。也可用既有等效回读工具完成检查和映射。ASS 检查实际渲染，注意毫秒到厘秒的精度变化。双语与单语从同一当前稿生成。不要只检查 JSON 就说成品检查过。

脚本不裁定疑点，不自动判 final，也不提供精确模型账单、全自动强制对齐或无人值守调度。组长依据真实业务结果设置项目完成状态。
