# DoveBench 离线评测

用户要求 DoveBench 时使用 `scripts/dovebench.py`。默认缓存 `~/.codex/datasets/DoveBench`，可用 `DOVEBENCH_HOME` 或全局参数 `--cache` 指定其他目录。完整缓存含 data/dovebench.jsonl、catalog.json、cache.json、deps、runtime.json 和 run.ps1。缓存不是翻译资料，不把参考译文写入领域词库或记忆。

维护时核对的数据版本包含 13,704 条、28 个视频；公开包不包含数据缓存、媒体或评测运行环境，需另行配置。该版本的官方 Git blob SHA-1 为 `9b1c02892e960b7760e09698a7820f3492ee9cd0`。数据来自 [DoveBench](https://huggingface.co/datasets/StarPigeon/DoveBench)，许可 GPL-3.0，原视频不包含在内。以 cache.json 的实际记录和 verify 结果为准，不只依赖此文档计数。后续默认不联网，不自动更新数据。

全库有 14 条英文对齐文本为空，中文参考完整；中文字幕评测保留这些行，不因英文缺失丢弃中文参考。若未来增加需要英文参考的指标，应单独报告其覆盖与缺失策略，不把这些对齐文本当成无误的 ASR 金标准。

```text
python <skill>/scripts/dovebench.py list
python <skill>/scripts/dovebench.py verify
python <skill>/scripts/dovebench.py freeze exports/final.srt --video-id sc2/12_39 --output eval-v01 --prior-exposure known --hypothesis-status final
python <skill>/scripts/dovebench.py score --run eval-v01
```

若自行部署对应启动脚本，也可通过 `~/.codex/datasets/DoveBench/run.ps1` 调用已配置 Python。list 只读取标识和时长目录，不展示参考译文。没有可信 YouTube ID 时结合标题、时长、开头/中段/片尾核对，不能仅靠类似时长认定同一视频。目录的原视频 ID 也可能有误，实际媒体与内容证据优先，修复映射时保留原目录值和依据，不改原评测缓存。

可在翻译前使用 `media_identity.py` 对绝对时间 ASR 与缓存英文做开头、中段、结尾各 90 秒的词三元组指纹检查。工具校验缓存散列、音轨和时间，记录素材及 ASR 文件散列；仅输出重合数值，不保留参考词句、不使用中文字段。多个 ASR 分块重复传 `--source`，必须已换算为整片毫秒，不能传局部从零开始的时间。

```text
python <skill>/scripts/media_identity.py --video-id sc2/16_08 --media input.mp4 --source asr/a01/source.json --source asr/a02/source.json --ffprobe <ffprobe路径> --output identity.v01.json
```

读取 JSON 的 status 后再决定继续：`consistent_fingerprint` 只表示启发式内容一致；`requires_identity_review` 暂停该素材的翻译和计分，核查选手、地图、内容、剪辑与全片偏移；`insufficient_content` 表示缺少有效样本，不能当通过。工具退出成功只表示记录写出，不等于身份通过。默认重合阈值是筛查规则，不是身份认证或 ASR 准确率；没有足够英文参考的片段可能无法判断。源 ASR 与媒体的来源关系仍由执行管线保证，散列不能证明一份文本确实来自该音频。

该步骤读取过英文参考做自动比较，必须记录这一事实与工具输出范围；不能因此认证盲测。执行者仍不得查看中英文参考句；只有冻结后的诊断阶段才读取它们。不能用反复试译文或修改 ASR 来优化指纹。已有任务没有前置检查时，可补做并如实标为事后检查，不倒填前置通过记录。

发现错片时，把该次候选及原始成绩另存为无效素材尝试，从质量汇总剔除并保留理由；不可把错片分数下降归因于翻译技能。正确素材使用新音频、ASR、视觉、译稿和评估记录。替换素材前后分数不是版本收益，修复过程中接触了参考的重跑须标 known。

freeze 接收**目标语言单语 SRT**，严格解析，复制至新评测目录并保存散列；不得把双语字幕当作中文候选。`--hypothesis-status` 记录 final/draft/unreviewed，默认 unreviewed；`--prior-exposure` 为 none/known/unknown，默认 unknown。已看过参考、用其调试译稿或继承过相关上下文，必须 known。none 是执行者的真实声明，不是脚本认证盲测。

score 才读取参考，校验缓存和冻结文件哈希，使用本地 SubER 0.4.0、sacreBLEU 2.5.1 及中文参数 zh。不同分段使用 SubER、t-* 和 AS-*，不把两个字幕表按序号 zip 后直接算 BLEU。全片拼接分数忽略时间与分段，仅作补充诊断。输出 scores/metrics.json、错误日志和此次参考 SRT；后者仅供评测诊断，评分之后也不能回流到同次待测译文。每次重跑建立新 run，已有结果不覆盖。

指标依照 [SubER 官方说明](https://github.com/apptek/SubER)，报告保留 SubER、t-BLEU、AS-BLEU 等前缀。SubER 越低越好，BLEU/chrF 越高越好；均不是翻译准确率，不能使用 100−SubER 得出“准确率”。记录视频范围、输入是自 ASR 还是标准转写、工具版本、参考来源/哈希、参考接触状态和未运行指标。基础 score 命令不执行神经指标；BLEURT/BERTScore 可按下述可选模块在已配置环境运行。COMET/SubSONAR/独立语义评审未由该模块提供，不能声称完整复现官方全部评测。

低分先检查素材身份、全片时间偏移、目标语言、文本编码、截断和参考覆盖，再区分 ASR、理解/否定/指代、术语风格、译文表达、参考省略、分段时轴等误差。参考也可能有错，但不能只凭几个例子把低分全归因于参考。改进后使用独立的新评测版本；训练/调试用视频与盲测视频分开。

冻结并评分之后，逐条核对重要疑点的英中参考。英文对齐文本可能含 ASR 错误，中文参考也可能省略或意译；英中肯定/否定冲突时两列都不能直接充当原音裁决。把疑点另存诊断文件，回查真实音频和连续画面；没有证据时标 suspected，不能据此修正原缓存、排除低分行或调整原成绩。

```json
{"video_id":"实际视频ID","issues":[{"idx":150,"category":"polarity_conflict","status":"suspected","reviewer":"实际执行者","note":"英中否定不一致，待听原音裁定。","evidence":[]}]}
```

idx 为该视频参考的零基索引，不是本片字幕序号。类别可用 polarity_conflict / omission / entity_conflict / timing / style_difference / other；confirmed 须给真实证据定位，声明不构成脚本对证据真实性的认证。不要把字段示例的 idx 当成所有视频的同一问题。

```text
python <skill>/scripts/dovebench.py reference-issues --run eval-v01 --annotations reference-annotations.v01.json --output reference-diagnostics.v01.json
```

该命令要求已有评分结果，并校验冻结候选和参考版本与评分记录一致；诊断仅另存文件，不重打分、不改字幕、不改参考。同义名称/电竞热键与全称的区别应单列风格差异，不一律判为术语错误，更不能为提高 BLEU 把参考习惯写回本轮译稿。

迭代效果需要同一未见参考样本、同一输入路径和固定配置的对照，或多个未见参考的新样本；两个不同视频的分数涨跌不是可归因的技能增益。实际音频复核、独立语义评审及样本范围分别报告，不用同会话多角色冒充独立评分。

多轮维护后的整组复测，按video_id做成对统计并核对相同参考/数据hash及评分配置，同时列每片变化、宏平均、改善/退步片数和AS/t的P/R/F1。样本数小或反复见过参考时不把平均增分宣称为泛化；如提供bootstrap区间，以整部视频为重采样单位并记录种子、次数和探索性限制。已确认的媒体准备缺陷保留在实际流程主结果，可另列剔除该片的敏感性分析；错误素材另行归档并用正确素材重做，不按分数选择排除。汇总后产生的新技能版本标为“已实现和工程验证、尚未整组重测”，不能把前一版26部成绩冠给新版本。

回归报告分开列制作质量、相对参考的文本/语义指标及原音证据状态。SubER 下降但 BLEURT/BERTScore 或全片拼接分数下降时，明确写出结果分化；检查 SubER 的词/断行编辑统计及 BERT P/R，不能把净分数改善称为全面进步。字数减少仅为精简程度，不能当作信息保留率；即使术语零告警、ID 全覆盖，也要检查阶段、限定、反应和分句顺序。

跨版本逐行比较须验证相同参考 hash、同一评分配置，按共同参考行关联分数，同时展示两版完整字幕与 AS/t 计算文本。先排查重分段将内容放到邻行、中文分句倒序、空候选和术语别名，再判断实际表达损失；AS/t 大幅反向变化应特别标注。拼接指标也退步时不能把全部下降归于错轴。可以用 revision_audit.py 对真实来源相同的两版做语义组差异检查，详见 [保真复核](semantic-preservation.md)，但该工具不读取参考、不自动判译错。

用户要求整片重跑时如实执行并记录变化因素；仅修时轴/排版的任务保留已确认的译文，避免全片重写带入额外变量。有必要单独验证某项改动时，另建冻结版本并明确属于控制变量诊断；不能把几项同时变化的已知参考回归称作独立泛化收益，也不自动为维护任务启动昂贵的全套重跑。

缓存缺失或损坏时明确报错，先恢复本地完整版本；仅在用户要求安装/更新时联网获取新缓存。不要退回每次打开 Hugging Face 首页，也不要默默把某个视频的局部提取当成完整数据集。

## 文本变体与参考可靠性

用户只要求对照文件时，可以读取已冻结候选和本地参考生成文本对比，不必启动服务器或重算神经指标。以共同参考行按时间重叠关联完整候选，明确制作 ID、SRT 序号、参考行号；参考与候选数量不同不能 zip。重叠会重复展示同一候选或带入相邻分句，不等于实际字幕重复或遗漏。

每列分别记录候选 SHA256、是否评分及评分所属版本。未评分变体显示 not_run，不能继承父稿分数；相同文件名或条数不足以认证。若完整候选未变而 AS 分数变化，检查邻文长度引起的评分切片漂移；若整句信息或出现顺序也变了，另记真实编辑问题，不全归因于对齐。

参考来源分开记录：官方数据集、用户确认的人类译稿、AI 初稿经人工校对、未知。后两类仍可诊断，但不与独立人类参考混成统一“准确率”结论；人工译稿也可能有错。已知参考训练/维护案例只提炼错误机制，不能把逐句答案回填提示词。多个相关指标改善的数量不是独立实验次数。

## BLEURT 与 BERTScore（可选）

`scripts/benchmark_neural.py` 将冻结后的配对、评分和对比流程分开，不负责安装或 SSH。原始 ASR、模型密钥和媒体不随评估配对上传。可在用户已授权的服务器运行 score，再将结果取回；未经配置的环境不能声称已运行。首次依赖缺失时根据环境单独部署，不自动替换 ASR 服务依赖。

```text
python <skill>/scripts/benchmark_neural.py prepare --run eval-v01 --deps <本地DoveBench缓存>/deps --output neural-pairs.v01.json
python <skill>/scripts/benchmark_neural.py score --pairs neural-pairs.v01.json --expected-pairs-sha256 <prepare返回的散列> --bleurt-checkpoint <缓存的BLEURT-20目录> --device cuda --output neural-metrics.v01.json
python <skill>/scripts/benchmark_neural.py compare --run eval-v01 --pairs neural-pairs.v01.json --metrics neural-metrics.v01.json --output comparison.v01.csv
```

prepare 必须在本地基础评分完成后运行；先验证冻结候选、参考及基础评分的身份与数量，再使用 SubER 官方中文可逆分词和 AS/t 重分段。不能把不同长度 SRT 按序号 zip。输出完整参考覆盖、空候选数及重分段前后非空白字符数；AS 丢字即失败，t 的缺失必须报告。空候选参与均值，不移除低分行。

t 字符数少于完整候选时，先区分交付文件漏字与对齐规则排除。SubER 0.4 的 t 对齐会将评分插值时刻落在参考时间窗之外的词元排除；这不是原音词时间，也不能据此直接判漏译。需要解释差异时，用本地已锁定实现复现排除项，记录实现散列、受影响 cue 和字符数，核对与配对覆盖差值一致；不修改官方规则补分。空候选的 BLEURT 仍可能为正，须同时展示空白状态，分数不能替代内容存在检查。

score 默认使用已缓存的完整 BLEURT-20 与 bert-base-chinese（第 8 层、IDF=false、无基线重缩放、慢分词器），不自动下载或降级。BLEURT 使用官方 LengthBatchingBleurtScorer，先与标准评分路径做数值一致性校验；用公开 tokenizer/max_seq_length 接口逐条检查最大输入长度，禁止用可能不存在的私有属性静默跳过检查。BERTScore 同样检查长度。记录实际依赖版本、上游 hash、配对文件 hash 和输入覆盖。完整 BLEURT-20 可使用 CPU，BERTScore 按指定设备运行。

报告同时列 AS/t-BLEURT 与 AS/t-BERTScore 的 P/R/F1，不把其中一个隐藏选择为最优结果。基础 metrics.json 的 not_run 只表示基础命令当时的执行范围；最终汇总合并已实际完成的神经结果，不改写历史基础记录。模型分数不等于准确率；同义游戏行话不一定被通用语义模型识别。

compare 生成 UTF-8 BOM CSV，包含参考行号、时间重叠关联的实际候选序号/完整字幕、AS/t 重分段文本及两类神经分数，并校验结果确实属于该候选。实际字幕列可能重复覆盖相邻参考行，AS/t 可能切断汉字词组；低分不自动标为“译错”。HTML 审阅界面可从同一 CSV 生成，不能另做一套不透明配对。

诊断若需回读含 source 的制作 JSON，用交付清单记录的 input_sha256 定位实际导出版本，并验证该导出 SRT 与冻结候选散列一致；不能硬编码 edited.delivery.json、按文件修改时间猜测，或因旧稿条数相近就拿来解释新成绩。未找到匹配源稿时报告缺失，仍可阅读冻结 SRT 与评分文本，不把旧源句冒充本次候选。制作 ID、候选序号与参考行号分别标明。

低分抽查依次比较：完整字幕与原声含义、重分段是否误分邻句、术语别名、语气/笑点压缩、参考注释或创作性扩写。分别记录 alignment_artifact、term_alias、expression_loss、reference_expansion、audio_uncertain 等诊断说明；这些分类是诊断，不删行也不回改分数。后评测新增疑点与冻结前队列分开统计。依赖和缓存可复用；每次输入、配置或语义分段变化必须使用新结果文件。

抽查极短参考行时记录实际持续时间及相邻完整 cue。AS/t 可能只分到单个汉字甚至截断专名；这类评分文本不能直接当成真实播放字幕。参考存在而候选为空时，区分省略、错配和疑似 VAD/ASR 漏掉的惊呼；没有实际听原音时只标待核实，不能凭参考补进候选。维护清单分别列出“已证实需改”“有待音画裁定”和“对齐/风格差异”，避免把低分排序直接用作自动改译列表。

每片诊断除低分与开放疑点外，补查全部 AS/t 空候选记录，防止只看最低若干行漏掉短语缺失。另列极短参考、异常英文和参考创作性扩写；这些只是诊断标签，仍全部计入原指标。AS 有文本而 t 为空时检查真实显示间隙；完整字幕有内容也要检查句内信息是否被提前或延后，不能一概称对齐误差。相反，高分也不能解除未听校的否定、词形笑点或音画冲突。维护使用可迁移的错误机制，不把参考答案或它的习惯用词写进下一版术语库。
