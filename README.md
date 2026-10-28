# 长视频字幕组 · Long Video Subtitles

**当前版本：S17 Team r4.3 · 测试版 · 2026-10-29。** S17 正在验证流程和字幕质量，尚未作为稳定版发布。

[公开介绍页](https://beginsc.github.io/long-video-subtitles/) · [版本更新历史](CHANGELOG.md) · [回退到 S13](#回退到-s13) · [MIT 许可证](LICENSE)

主 agent 担任字幕组组长，源稿与语境专家、译者、独立审校专家协作，将电影、剧集、YouTube 视频、音频或已有字幕制作成自然准确的单语或双语字幕。各岗位同时承担目标语言本地化职责，脚本负责可确定的文件、覆盖和时间检查。

## S17 的工作方式

- 按完整场景、话题和论证分工，维护章节、人物与前后文，按具体疑点听音、看画面、查资料。
- 译者交成稿，另一位专家审校并直接修订；默认一次独立审校，仅针对实际问题返工。
- 简中按简体中文资料核对通行术语，口语自然；句内短停顿用空格，显示字幕默认不用断句逗号和句号。
- 选手 ID、网名默认保留原文；有核实的官方译名或原片实际玩名字梗时再局部处理。
- 根据领域、语言地区和语域自动复用持久词库，新核对的可复用词条继续积累。内置 S15 保留的 SC2 简中实况起始库，共 330 条；其他领域按需建立和复用。
- 支持保留原轴或依据真实时间证据拆合；已有词范围可检查缺口、重复和越界，导出回读可生成制作 ID 与成品编号映射。
- 默认简中单语 SRT 与简短制作说明；双语、VTT、ASS 和审校 CSV 按需求生成。保存实际进度，支持中断续做。

[技能入口](SKILL.md) · [岗位分工](references/team.md) · [语言与本地化](references/language.md) · [持久领域词库](references/terminology.md) · [时间轴与质检](references/production.md) · [文件和工具](references/files.md)

## 安装测试版

需要能读取 Skill、执行 Python 并调度真实子 agent 的环境。脚本使用 Python 标准库，在 Python 3.12 环境验证；处理媒体另需 FFmpeg/ffprobe，下载器、ASR、模型和服务凭据按所选方式准备。

在支持技能安装器的 Codex 对话中：

```text
使用 $skill-installer 安装 https://github.com/beginsc/long-video-subtitles 根目录中的技能。
这是 S17 r4.3 测试版。已有同名技能时先保留旧版，再按当前宿主的技能目录安装。
沿用我已有的视频下载和 ASR 偏好，缺少配置时再分别选择。
```

手动方式：将仓库克隆到当前宿主的技能目录。已有同名安装时先备份，不把两个版本叠在同一个目录，以免留下旧脚本。

```bash
git clone https://github.com/beginsc/long-video-subtitles.git long-video-subtitles
```

下载支持本地 yt-dlp、远程 SnapAny 或自定义命令适配器。ASR 支持本地 WhisperX、远程 MAI-Transcribe-2 或自定义服务；两项偏好独立保存。不会替新用户默认选择收费服务，也不随技能升级重置配置。

```bash
python scripts/media_intake.py configure
python scripts/media_intake.py check
python scripts/asr_workflow.py configure
python scripts/asr_workflow.py check
```

本地素材不要求先配置下载，已有字幕不要求先配置 ASR。命令参数、MAI 区域与环境变量、旧配置复用见[媒体与 ASR](references/media.md)。`check` 仅检查本机配置和依赖，不代表云端鉴权或推理已经成功。

## 开始使用

```text
使用 $long-video-subtitles，给这个视频制作简体中文字幕。
面向普通中文观众，输出单语 SRT。素材：<文件路径或视频链接>
```

```text
使用 $long-video-subtitles，只校对这个 SRT，保留原编号和时间轴。
检查意思、术语、否定和指代，保存修订稿并说明实质修改。
```

```text
使用 $long-video-subtitles，继续 <项目目录> 中未完成的任务。
读取项目状态和当前稿，只补未完成工作。
```

素材、项目输出、个人词库和凭据放在技能目录之外。S17 的工作文件结构与 S13 不同；旧项目继续使用对应版本，新项目再使用 S17，不自动迁移或覆盖旧工件。

## 回退到 S13

更新前 GitHub 上的 **S13 原版**已完整保留，提交为 `cde22e07a0302afb5342de297a948b3a9dffc9ee`。`archive/s13` 分支指向同一提交；下面的快照链接固定到提交，不受后续 main 更新影响。

- [S13 旧版介绍页](https://beginsc.github.io/long-video-subtitles/s13/)
- [S13 完整源码快照](https://github.com/beginsc/long-video-subtitles/tree/cde22e07a0302afb5342de297a948b3a9dffc9ee) / [下载 S13 ZIP](https://github.com/beginsc/long-video-subtitles/archive/cde22e07a0302afb5342de297a948b3a9dffc9ee.zip)
- [S13 README 与原版配置说明](https://github.com/beginsc/long-video-subtitles/blob/cde22e07a0302afb5342de297a948b3a9dffc9ee/README.md)

可在新的空目录获取旧版，然后将该目录安装到宿主技能位置：

```bash
git clone --branch archive/s13 --single-branch https://github.com/beginsc/long-video-subtitles.git long-video-subtitles-S13
git -C long-video-subtitles-S13 rev-parse HEAD
```

最后一条输出应为上述 S13 提交。替换当前安装前保留它及进行中的项目；使用 S13 自己的配置说明。该回退入口保留历史行为，不表示 S13 获得了 S17 的修正。

## 验证与已知限制

S17 r4.3 维护时通过 31 项脚本测试，以及六条跨领域素材的独立小样审校。它们验证工具和特定行为，不能证明整体翻译质量已提升。技术检查不等于意思正确，静态渲染不等于动态观看；实际听看范围和未决问题随交付说明。

[DoveBench 本地结果与问题复盘](docs/benchmarks.md)公开 S09、S17 r4.2＋WhisperX、S17 r4.2＋MAI 的七项已完成指标。SC2/29_30 的 SubER 分别为 **60.807 / 64.708 / 67.362**，越低越好。本次 MAI 分数较差，合理异译与参考错误也会影响评分。**r4.3 尚未重新运行整片基准**，不能把 r4.2 的分数标为 r4.3 成绩。

```bash
python -B -m unittest discover -s scripts -p "test_*.py"
```

## 设计来源与许可证

字幕制作流程参考 ViDove、VideoLingo 等项目；[历史来源记录](https://github.com/beginsc/long-video-subtitles/blob/cde22e07a0302afb5342de297a948b3a9dffc9ee/references/project-sources.md)保留固定提交和当时的设计说明。本仓库不捆绑该项目、第三方模型或基准数据。SC2 起始词库保留每条记录的来源与适用范围。

本项目采用 **[MIT 开源协议](LICENSE)**，版权署名为 `2026 beginsc and contributors`。第三方工具、模型、视频和评测数据仍适用各自的许可证。MIT 标准文本见 [Open Source Initiative](https://opensource.org/license/mit)。
