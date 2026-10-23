# DeepSeek 中文润色建议后端

状态：**公开分发版默认关闭；提示词来源于维护版本，实际使用前需取得覆盖本次项目的授权**。S11.3 修复响应留存与离线诊断，保留提示词、目的地与采纳规则。它在已有 draft 或 target 上提出建议，GPT 对照源文决定是否采纳，再重走受影响的校对/编辑、revision_audit 和导出。原稿与基准评分不被覆盖。

2026-09-22 查阅[官方更新日志](https://api-docs.deepseek.com/zh-cn/updates/)，最新 Flash 的调用名为 `deepseek-flash`，当时对应 DeepSeek-V4.1-Flash。使用[Chat Completions](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/)与 [JSON Output](https://api-docs.deepseek.com/guides/json_mode/)，端点 `https://api.deepseek.com/chat/completions`。配置显式关闭思考模式、设置输出预算；不靠声称低温度保证保真。别名可能继续更新，每次保存服务返回 model、system_fingerprint 与 usage；无法确定快照时明确说明。

## 审批和凭据

- 提示词为 [deepseek-polish-prompt-v1-user-reviewed.md](deepseek-polish-prompt-v1-user-reviewed.md)，全文是发送的 system 内容。公开包保留该提示词版本；摘要见 [未授权说明模板](deepseek-prompt-approval-v1.json)。此文件不代表新用户的批准。按本项目真实授权生成批次记录，已有充分授权可复用；实质变更需重新审核。
- `assets/deepseek-polish.json` 保持 `enabled: false`，状态为 `requires_project_approval`。为已授权任务使用 `enabled: true`、`status: approved` 的项目配置副本，不把全局默认改为自动发送。已有批准按真实范围复用；维护技能本身不触发新的批次调用。
- 每个准备好的请求另存 approval JSON：`status: approved`、`approved_by: user`、`user_message`、`approved_at`、`prompt_sha256`、`packet_digest`、`model: deepseek-flash`、`endpoint`。只能依据已覆盖本次素材和目的的真实授权创建，不把测试样例、历史服务器上传授权或模型建议当批准。批次可引用同一条充分覆盖范围的用户授权，无须重复询问。
- 运行前验证启用状态、提示词、配置、源稿与批次摘要。提示词变更须重新审核，源稿变更须重建批次。此记录是本机流程闸门，并不是密码学意义的用户签名。
- 密钥优先读 `DEEPSEEK_API_KEY`，否则读取 `%USERPROFILE%/.codex/credentials/long-video-subtitles/deepseek.dpapi`，复用 CurrentUser DPAPI。不要把密钥写入技能、项目、审批 JSON、命令行参数或日志。

## 离线准备

项目 `localization-profile.json` 必须提供这些非空字符串字段：

```json
{
  "target_language": "zh-Hans",
  "locale": "zh-CN",
  "domain": "anime-review",
  "register": "自然、清楚的口头评论，保留必要的影视分析术语",
  "audience": "普通动画观众",
  "term_policy": "遵循当前冻结 term_context，不改专名",
  "unit_policy": "保留原数值和单位；需要换算则只提出审校建议",
  "culture_policy": "保留事实与画面对象；简述文化作用，不硬塞梗",
  "line_policy": "single"
}
```

`line_policy` 支持 single（单行）或 preserve（保留每条现有行数）；其他目标语言走常规翻译流程。

```text
python <skill>/scripts/chinese_polish.py prepare --source edited.vNN.json --field target --ids c0001,c0002 --profile localization-profile.json --output polish-packet.v01.json
```

每次选择连续的 1–40 条，附左右各最多两条只读上下文。读取 ID/source/现译/时间/term_context/source_checks/issues，排除整个项目 meta 和参考字幕。term_context 必须已按常规流程验证并冻结，操作代理仍须检查实际包内是否含非必要资料或参考泄漏。上传内容仅为该文本包与提示词，不上传媒体。源路径和本机文件摘要只留在本地审计包，不发送给服务。

## 获批后运行及复核

```text
python <skill>/scripts/chinese_polish.py --config project-polish-config.json run --packet polish-packet.v01.json --approval polish-approval.v01.json --output polish-suggestions.v01.json
```

工具只写独立建议 JSON，校验 ID、条目数、输出协议、行数约束和开放 source_checks。未知字段/缺项/截断/空响应拒收；不能自动确认语义、术语正确或自然。keep/unclear 必须保持原译，开放 source_checks 只能 unclear。新疑点回写新版本 source_checks，不能只留在润色输出里。

请求前先独占创建 started 记录；已有同名输出拒绝再次提交。失败或断网保留 failed_or_uncertain，不自动重试、不覆盖原稿。恢复时确认是否已发出、是否已有结果；人工决定重试才用新输出文件并记录前一次路径及原因，不能删旧记录来重跑。

S11.3 在成功 HTTP 响应返回后、解析和严格校验前，将原始响应字节写入 `<output>.response.json`，状态记录其路径与 SHA-256。只保存响应体，不保存请求头或凭据；HTTP 错误响应体不写入。响应已收到但格式拒收仍为 failed_or_uncertain，response_received 与 failure_phase 用于区分排障；不会把拒收改为通过，也不会自动重新请求。

已有响应可离线诊断：

```text
python <skill>/scripts/chinese_polish.py diagnose --packet polish-packet.v01.json --response polish-suggestions.v01.json.response.json --output polish-diagnostic.v01.json
```

也支持以往单独保存的 API JSON。诊断检查原批次摘要，区分整体协议失败、单条结构问题、漏项、重复 ID 和只读上下文越界；不修复 JSON、不自动挑选冲突候选、不调用 API。schema_valid_requires_gpt_review 仅代表单条结构符合要求，绝不是采纳或审定；整体失败状态仍保留。无效批次中值得参考的文字只能作为未受信建议，由 GPT 回到源文重新编辑并明确记录来源，不把人工整理文件冒充原批次通过。

GPT 编辑逐条检查 `protected_meaning` 与实际原文，核对术语和上下文，再接受或驳回；模型自己的解释不是正确性证明。保存新稿和采纳记录后，用现有批次协议重做受影响审校，不直接复制建议并继承旧的 reviewed/editing_digest。确定拒绝、跳过及疑点均留痕，正常通过 QA 才导出单语和审查版。

## 实验解释

获批后建议先挑几个不同语体的片段做 A/B，保留未经润色版本；先比较保真、自然度、简洁度与梗的作用，再看参考指标。已有测试集接触过参考，只能称回归。没有本轮实际输出和冻结评分时，不能宣称 DeepSeek 提升了分数或翻译质量。
