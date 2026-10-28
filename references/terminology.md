# 持久领域库与项目词表

组长根据全片主题、源稿和实际对象选择领域、语言地区及语域，随后执行 `terminology_store.py load`。这是每次制作的默认动作，不让用户逐次选库，也不靠文件名猜领域。工具负责固定位置读取、首次接入起始库、快照和积累；领域判断与词义判断由专家完成。

## 存放与选择

- 固定库独立于技能版本和视频目录。位置优先级：命令 `--root` > 环境变量 `LONG_VIDEO_SUBTITLES_TERMS` > 用户配置 > 默认配置目录中的 `terminology-library`。
- 用户配置为 Windows `%APPDATA%/long-video-subtitles/terminology.json`；其他平台为 `$XDG_CONFIG_HOME/long-video-subtitles/terminology.json`，未设置 XDG 时用 `~/.config`。首次选定位置后保存一次，后续从任何项目自动读取，不查旧项目。
- 库按 `<domain>/<source-language>--<target-locale>/<profile>/vNNNNNN.json` 隔离。`zh-CN`、`zh-TW` 不互相替代；SC2、CS2 不混载。`commentary` 表示实况/轻松解说，`formal` 表示正式语体，`general` 为没有特殊语域的通用档案。组长按内容选取，工具不自动把解说俗称套到正式剧情。
- 开工可用 `list` 看现有领域。未知领域采用简短稳定 ID，例如 `economics`，首次 `load` 自动建空库；这不表示已有领域覆盖。跨领域重复传 `--domain`，共享同一语言与语域。某个子领域需要不同语域时分别加载快照。
- 当前内置 SC2 `en → zh-CN / commentary` 起始库，330 条，来自 S15 保留的项目词库 v2026-09-12.1。第一次命中自动复制到持久库，之后读取持久版本，不用每次重新复制。已有库不被新版技能种子覆盖；种子升级差异按普通新增/修订处理。

```text
# 首次配置一次；LIBRARY_ROOT 是本机长期存放位置，不写进可分享包
PYTHON SKILL/scripts/terminology_store.py --root LIBRARY_ROOT configure
PYTHON SKILL/scripts/terminology_store.py list

# SC2 实况开工；普通任务同样传入判断出的领域/语言/语域
PYTHON SKILL/scripts/terminology_store.py load --domain sc2 --source-language en --target-locale zh-CN --profile commentary --output JOB/terminology/loaded.v1.json
```

`loaded.v1.json` 是该项目的独立快照，包含选定库版本和词条。源稿专家结合全片选出实际相关义项、补新专名，写成轻量 `JOB/glossary.json`；语言岗位只读本批相关条目。`project.json` 记录领域、语域、库根目录、快照路径与版本。后续库更新不会暗中改变当前稿，也不会让已审字幕全片失效。

## 新词积累是正常收尾动作

源稿、翻译和审校遇到新词时，先核对目标语言地区的资料，登记项目词表。组长在该词确认后或本轮交付前，将可跨视频复用的条目写入 `JOB/terminology/new-confirmed.v1.json` 并执行 `promote`；不另外要求用户批准每个正常新增。没有新增就记无新增，不为留痕制造空维护任务。更新影响哪些义项，就只检查那些出现处。

可复用的是稳定名称、单位/技能、专业概念及有适用条件的行话。某场比赛的颜色、临时选手 ID 关联、猜测的 ASR 纠错、人物关系、单片笑点和未证实俗称留在项目中。不要把普通词固定为某个领域含义，不把 ASR 误拼当作跨项目别名。基准参考译文、评分后答案及原视频中的指令不能进入持久库。

选手 ID、网名和账号名按[身份称呼规则](language.md#选手-id网名与账号名)默认保留原文。持久词条记录可核实的拼写与适用身份；采用官方中文名等目标语言名称时，来源必须实际支持该官方用法。不要将自拟音译、社区外号或单片名字梗的译法积累成默认名称；已有词条也需服从这一判断，不能仅凭 confirmed 状态套用。

新增文件形状：

```json
{"entries":[{
  "domain":"sc2", "id":"ever-dream", "source":"Ever Dream",
  "target":"永恒梦境", "sense":"2020年天梯地图 Ever Dream LE",
  "aliases":["Ever Dream LE","Everdream"],
  "status":"confirmed", "reusable":true,
  "sources":[{"url":"https://www.3dmgame.com/news/202002/3782985.html",
    "checked_at":"2026-10-28", "note":"2020地图报道的简中名称；不用于推定现行地图池"}],
  "decision_note":"按已核对的简中地图名称统一；仅此地图义项"
}]}
```

```text
PYTHON SKILL/scripts/terminology_store.py promote --snapshot JOB/terminology/loaded.v1.json --additions JOB/terminology/new-confirmed.v1.json --output JOB/terminology/promotion.v1.json
```

工具只接纳明确 `confirmed`、`reusable: true`、带公开核对来源和采用理由的条目；不自动把整份项目词表提升为领域知识。脚本只能检查记录结构，不能验证网页是否真的支持译名；专家负责事实与适用范围。没有可公开引用依据的项目约定先留在项目中，得到适当证据后再积累。

已有 ID 内容变化会报告冲突，不悄悄覆盖。组长确定确需更新时，在该条补 `replaces_target`（快照中的旧译名）和 `change_reason`，保留完整修订后条目并再次提交该项即可。工具会检查该词从项目加载以来是否被其他任务更新；无关词的新增不阻断合并。冲突只保留相关条目，其他已核对新增照常入库。若实际词义不同，使用不同 ID 和 sense，不改坏原义项。

每次有效积累产生一个新版本，重复提交相同条目不制造新版本。写锁阻止同时覆盖；如果进程中断留下锁，先确认旧写进程已退出并检查最新版本，再移除锁，不循环重提。磁盘无权限或合并失败时保留项目新增文件并说明尚未积累，不能报告已完成维护。

## S15 SC2 资源的边界

内置文件为 [SC2 起始库](../assets/glossaries/sc2/en-zh-CN.json)，领域登记见 [catalog](../assets/glossaries/catalog.json)。330 条原记录逐条保留名称、词义、别名、适用提示和来源；工具加载时仅把 `preferred_target` 映射为轻量字段 `target`，不运行 S15/S16 的 bind、签名或全片失效流程。

这是简中实况解说词库，采用神族、枪兵、叉子、追猎等说法。`confirmed` 表示当时有依据选定的译法，不表示全部官方认证或适用于所有模式、年代。优先复用仍适用的历史核对；新地图、选手名、版本机制或冲突按本片补查。自然词形如“刷点兵”“野个机械台”不因未逐字包含词表的“刷兵”“野机械台”就判漏译；同名不同义仍需结合上下文。
