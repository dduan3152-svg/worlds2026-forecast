# 第三方来源与许可说明

## 比赛数据

- 发布者：ChainCC（chaincc.lol）。
- 来源：https://chaincc.lol/free/data/
- 队伍快照：https://chaincc.lol/data/chaincc-teams-2026.csv.gz
- 选手快照：https://chaincc.lol/data/chaincc-players-2026.csv.gz
- 原项目来源记录注明许可为 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。
- 本仓库保留原始压缩快照；衍生表经过筛选、去重、队伍代码统一、系列赛归组和阵容匹配，处理细节见 `evidence/history_sources.md` 与 `evidence/history_lineup_notes.md`。
- 数据来源和许可记录不代表发布者或 Riot 对模型结论的认可。

自动更新模块仅下载上述队伍 CSV，在内存中筛选本届世界赛已完成比赛、聚合系列赛并生成条件预测。保留来源 URL、快照哈希和检查时间；不将新增抓取功能用于 Riot 认证接口。

## 字体

图表与中文 PDF 使用 Noto 字体，字体文件随对应许可一同提供：

- `figures/assets/NotoSansCJK-LICENSE.txt`
- `figures/assets/NotoSansSC-LICENSE.txt`

## 官方资料与赛事标识

赛程快照、赛制来源和引用链接保存在 `evidence/` 与 `data/rules.json`。这些材料的权利归各自权利人，不自动适用 ChainCC 的数据许可。League of Legends、Riot Games 及战队名称、标识属于各自权利人。本项目为独立个人分析项目。

## 原创代码与报告

仓库目前未对原创代码、文字和报告授予统一开源许可证。第三方材料按各自许可和来源说明处理。后续如增加代码许可证，应明确范围，不替代第三方许可。
