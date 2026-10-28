# 版本更新历史

本页记录 GitHub 公开版本。S14—S16 及 S17 早期候选属于本地迭代，不列为已经公开发布的版本。

## S17 Team r4.3 — 测试版（2026-10-29）

**S17 当前为测试版本，尚非稳定版。** 新版采用组长与三位专家协作：源稿与语境、翻译、独立审校；审校直接修改，制作沿用现有岗位，避免重复全片验算。

- 按目标语言地区核对术语；自然口语表达，识别梗与双关。简中默认不用断句逗号和句号。
- 选手 ID 与网名默认保留原文，官方译名和片内名字梗按证据处理。
- 持久领域词库自动加载和积累，正式接入 S15 的 SC2 330 条起始库，避免每次手动找旧项目。
- 保留本地 WhisperX、远程 MAI-Transcribe-2 和自定义 ASR，独立维护下载偏好；沿用已有授权和配置。
- r4.3 补强数字所修饰的量、指代与评价对象，区分已核实事实与上游暂定解释；短闪需要实际观看依据。
- 机械检查利用已有词范围发现缺口、重复和越界；导出回读生成编号映射，VTT 保留 cue ID。
- 加入 [DoveBench 本地结果](docs/benchmarks.md)：记录已有三版的七项指标和限制，不宣称 r4.3 已有新基准成绩。
- 为 S13 保留 `archive/s13` 分支、固定提交下载和旧版页面；在首页与 README 提供回退入口。
- 项目正式采用 [MIT 开源协议](LICENSE)。

验证：31 项脚本测试、真实冻结稿的词范围与导出回读检查、一次六条跨领域独立小样审校通过。尚未对 r4.3 重跑整片基准；既有视频字幕和评测记录不修改。

## S13 — 历史公开版（2026-10-24）

增加本地素材入口和首次下载偏好选择，支持 yt-dlp / SnapAny；保留岗位子代理、时间证据和断点交接。

[查看源码](https://github.com/beginsc/long-video-subtitles/tree/cde22e07a0302afb5342de297a948b3a9dffc9ee) · [下载快照](https://github.com/beginsc/long-video-subtitles/archive/cde22e07a0302afb5342de297a948b3a9dffc9ee.zip) · [旧版介绍页](https://beginsc.github.io/long-video-subtitles/s13/) · [回退说明](README.md#回退到-s13)

## S12.1 — 历史公开版（2026-10-24）

增加岗位子代理协作和字符时间证据。

[历史提交](https://github.com/beginsc/long-video-subtitles/commit/63cfb077234b4029673e035d0ae28d8a4d6b1dae)

## 首次公开发布（2026-10-23）

发布 Skill、工具与中文介绍页。

[历史提交](https://github.com/beginsc/long-video-subtitles/commit/d5a612277665371be2be73cbdadd08207db856be)
