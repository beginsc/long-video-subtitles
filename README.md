# 长视频字幕组 · Long Video Subtitles

将长视频、音频和已有字幕，按「转写 → 术语核验 → 翻译 → 校对 → 编辑 → 时间轴 → 交付」流程制作成可追溯的字幕。

这是供 Codex 等支持技能文件的智能体使用的 **Skill 与 Python 工具集**。翻译和语义审校由当前会话模型执行，脚本处理数据、证据、时间轴与格式检查。

[公开介绍页](https://beginsc.github.io/long-video-subtitles/) · [技能入口](SKILL.md) · [工作流程](references/workflow.md) · [脚本用法](references/data-and-tools.md)

## 能做什么

- **长片分批与续做**：保存版本、稳定 ID、输入摘要及批次状态，中断后核对工件继续。
- **音画证据结合**：抽帧、原音疑点队列和证据关联；明确区分实际核验、跳过与不可用。
- **领域术语一致性**：发现、核验、绑定、审计全片用词；附 SC2 / CS2 初始词库及来源。
- **分阶段审校**：初译、校对建议、编辑定稿分开留存，未决问题不会被自动标为通过。
- **自然的中文字幕**：按受众与表达方式选择语体，保留事实、限定、否定、因果和信息顺序。
- **字幕制作**：支持单语或双语 SRT / VTT / ASS，保留原时间轴或依据真实音频重建。

适用于课程、访谈、纪录片、演讲、游戏实况和赛事解说。默认目标为简体中文，主交付为单语 SRT 与独立审校 CSV；双语字幕可作为审查版。

## 安装

需要 Git，以及能够读取技能、执行 Python、按需查看图像和搜索网页的智能体环境。Python 工具在 3.12 环境验证。

在 Codex 对话中可以使用内置安装器：

```text
使用 $skill-installer 安装 https://github.com/beginsc/long-video-subtitles 根目录中的技能。
```

也可手动克隆到个人技能目录。当前 Codex 的个人技能目录是 `~/.agents/skills`；已有安装同名技能时，先核对旧版本，避免重复安装。目录说明以 [OpenAI 官方文档](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills) 为准。

**Windows PowerShell**

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE/.agents/skills" | Out-Null
git clone https://github.com/beginsc/long-video-subtitles.git "$env:USERPROFILE/.agents/skills/long-video-subtitles"
```

**macOS / Linux**

```bash
mkdir -p ~/.agents/skills
git clone https://github.com/beginsc/long-video-subtitles.git ~/.agents/skills/long-video-subtitles
```

若技能没有出现，重启 Codex。其他智能体需按其技能目录约定安装；本仓库未验证所有客户端的兼容性。

## 开始使用

```text
使用 $long-video-subtitles，给这个视频制作简体中文字幕。
领域是星际争霸 2，面向 B 站观众，输出单语 SRT，另附双语审查版。
素材路径：<你的文件路径>
```

```text
使用 $long-video-subtitles，只校对这个 SRT，保留原时间轴。
检查术语、遗漏、否定和因果，另存修改版并给出审校记录。
```

```text
使用 $long-video-subtitles，继续 <项目目录> 中上次未完成的字幕任务。
先核对素材、版本与批次状态，再从首个未完成阶段继续。
```

项目素材和输出保存在独立工作目录。不要放进技能安装目录或提交到本仓库。

## 依赖与后端

| 使用场景 | 需要准备 |
| --- | --- |
| 已有 SRT 的翻译 / 校对 | 当前会话模型与 Python；无音频时保留时间轴 |
| 视频抽帧、音频处理 | FFmpeg / ffprobe；有图像输入能力的会话 |
| 本机 WhisperX | 独立部署的运行环境、模型和兼容运行器，见[说明](references/whisperx-local.md) |
| AssemblyAI | 自行配置 API 凭据与实际任务授权，见[说明](references/assemblyai.md) |
| WhisperX 服务器 | 自有兼容服务、SSH 与 API 凭据，见[说明](references/whisperx.md) |
| DeepSeek 润色（可选） | 默认关闭；核对提示词、模型可用性、项目授权与数据去向，见[说明](references/deepseek-polish.md) |
| DoveBench 评测（可选） | 另行部署数据缓存和评测环境，见[说明](references/dovebench-local.md) |

公开包中的服务器、密钥路径和本机运行目录均为占位示例。克隆仓库不会自动安装 ASR 环境或下载模型；`whisperx_local.py` 当前只对接约定布局的 Windows 独立运行环境。云端服务的费用与访问条件以服务商实际要求为准。

## 仓库结构

```text
SKILL.md               技能入口与验收要求
agents/openai.yaml      技能显示信息
scripts/                Python 工具与现有单元测试
references/             分阶段工作规范与后端说明
assets/                 配置模板、语体配置、领域词库
docs/                   GitHub Pages 静态介绍页
```

本地运行现有测试（不会启动完整翻译任务）：

```bash
python -B -m unittest discover -s scripts -p "test_*.py"
```

## 质量边界

技术检查不能证明翻译含义正确。只有实际读取图像或音频，才可以记为完成对应核验；未解决的问题应保持 `UNCLEAR` / `needs_review`。同一个模型承担不同审校阶段不等同于人工审校或独立模型评审。公开包不包含私人素材、模型权重、凭据或维护者的历史授权。

流程参考 ViDove、VideoLingo 等项目，设计映射与来源见 [project-sources.md](references/project-sources.md)。仓库公开不改变第三方工具、模型和数据各自的许可证；本仓库暂未指定开源许可证。
