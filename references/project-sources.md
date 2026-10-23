# 参考来源

ViDove 源码核对日期：2026-09-06，参考提交 `88470bcb3d34f0f6da396ece0030b5374b6dbf47`。本技能为原创流程和脚本，没有复制或捆绑上游实现；研究时没有运行 ViDove 或安装其依赖。README 和代码有差异时以固定提交源码为准。

- [ViDove 主流程](https://github.com/pigeonai-org/ViDove/blob/88470bcb3d34f0f6da396ece0030b5374b6dbf47/src/task.py)：分段→视觉→转写→原文预处理→翻译→后处理→校对→编辑→输出；纯 SRT 跳过音画。校对/编辑失败可保留初译。本技能保留草稿，但不会把失败阶段标成审定完成。
- [视觉实现](https://github.com/pigeonai-org/ViDove/blob/88470bcb3d34f0f6da396ece0030b5374b6dbf47/src/vision/gpt_vision_agent.py)：对片段采样多帧并生成视觉描述。本技能采用视觉子代理在其会话中实际看帧，并增加原创 FFmpeg 抽帧、帧打包和证据写回脚本；未复制其独立 Vision API 客户端。
- [翻译器](https://github.com/pigeonai-org/ViDove/blob/88470bcb3d34f0f6da396ece0030b5374b6dbf47/src/translators/translator.py)：翻译提示可结合近期译文、领域知识、网络资料和检索的视觉描述。本技能使用相关文件和时间索引，不声称已安装向量 RAG。
- [校对器](https://github.com/pigeonai-org/ViDove/blob/88470bcb3d34f0f6da396ece0030b5374b6dbf47/src/editorial/proofreader.py)：主要输出建议；源文存疑标 UNCLEAR，不直接重写译文。本技能增加结构化 PASS/REVISE/UNCLEAR 与建议记录。
- [编辑器](https://github.com/pigeonai-org/ViDove/blob/88470bcb3d34f0f6da396ece0030b5374b6dbf47/src/editorial/editor.py)：综合建议、音画线索、前后译文和领域记忆决定修订。本技能另保存采纳理由和未决问题。
- [VideoLingo README](https://github.com/Huanshere/VideoLingo)：参考词级识别、术语准备、翻译后反思润色、切分和续做。配音不在默认范围。
- [翻译批次](https://github.com/Huanshere/VideoLingo/blob/main/core/_4_2_translate.py)：参考相邻上下文与分批处理，本技能采用稳定 ID 和输入摘要匹配。
- [字幕切分](https://github.com/Huanshere/VideoLingo/blob/main/core/_5_split_sub.py)：参考源译配对切分，另保留可追溯原始 ID。
- [配置](https://github.com/Huanshere/VideoLingo/blob/main/config.yaml)：后端/字幕参数可配置，本技能不硬编码其模型、价格或默认服务。

主流程优先参考上述 ViDove 固定版本。上游质量宣传不作为本技能承诺，不把单行作为所有情境固定规范。AssemblyAI 接口仍为本技能已有实现，不声称它能接收 ViDove 的图像提示。

## 领域术语扩展

2026-09-06 读取下列固定版本源码，参考设计后以原创代码实现；未运行三个项目，也未复制其术语数据库：

- [ViDove 领域词典说明](https://github.com/pigeonai-org/ViDove/blob/88470bcb3d34f0f6da396ece0030b5374b6dbf47/domain_dict/README_zh.md) 和 [SC2 对照表](https://github.com/pigeonai-org/ViDove/blob/88470bcb3d34f0f6da396ece0030b5374b6dbf47/domain_dict/SC2/Translations_Table.csv)：领域划分、多源语言及别名，说明中特别指出人族/神族 VS 需要消歧。本技能按领域、地区、词义和每次出现位置隔离，不按拼写直接覆盖。
- [ViDove 旧词典工具](https://github.com/pigeonai-org/ViDove/blob/88470bcb3d34f0f6da396ece0030b5374b6dbf47/src/dict_util.py)：get 实现随机选择多个候选译名中的一个；这是该旧工具的行为，不能推断所有当前翻译路径均如此。本技能固定一个首选译名，不采用随机选择。
- [VideoLingo 术语准备](https://github.com/Huanshere/VideoLingo/blob/814f84eeb98db987510e3558feeb595de2ac328a/core/_4_1_summarize.py)：GPT 概要/术语、custom_terms.xlsx 和句内相关词注入。本技能补充全片发现覆盖，因为所读版本 combine_chunks 仅取 summary_length 字符；另增加冲突检测，不简单追加同名自定义条目。
- [LinguaGacha 术语匹配与检查](https://github.com/neavo/LinguaGacha/blob/c30941257b5eb68e98d901160ded67a1eb209494/src/shared/quality/glossary.ts)：稳定 entry_id、源文字段命中和目标译词应用诊断。本技能加入字幕 ID、领域/地区隔离及有效性摘要，不将目标包含固定词视为语义校对完成。
- [LinguaGacha 候选提取提示](https://github.com/neavo/LinguaGacha/blob/c30941257b5eb68e98d901160ded67a1eb209494/builtin/analysis_prompt/template/zh/base.txt) 与 [术语规则](https://github.com/neavo/LinguaGacha/blob/c30941257b5eb68e98d901160ded67a1eb209494/builtin/agent/skill/glossary-rules/SKILL.md)：参考原文字面证据、实体边界、上下文消歧和长尾检查。其专用 workspace API 不属于本技能，未移植该工具链。

初始少量名称另以 Blizzard/Valve 官方页面核验；每条来源、日期和译名选择理由保存在库自身。SC2 实况解说库现已根据用户提供的 AzureF1ow 四篇社区术语文章扩充，来源及修正说明见 [sc2-terminology.md](sc2-terminology.md)。它不是 ViDove 全词典的复刻，CS2 库也不宣称覆盖全部武器、地图和点位。运行时联网流程见 [terminology.md](terminology.md)，查询由术语岗位实际可用的会话工具执行，Python 脚本没有网络或付费模型调用。
