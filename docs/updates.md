# 自动赛果更新

私有 GitHub Actions 每小时 UTC 第 17、47 分钟计划检查 ChainCC 的 CC BY 4.0 公开队伍 CSV，不需要数据源账号或 API 密钥。实际运行受任务排队和源数据发布延迟影响。2026-11-15 UTC 结束后停止定时抓取，仍可手动触发。

有新可采纳系列赛或已确认签位时，锁定已知结果，以冻结的单局胜率矩阵重跑 100,000 次剩余赛事模拟。新赛果不参与实力重拟合，冻结报告中的 Bootstrap 区间不直接套用到赛中结果。

- [最新结果](../live/latest.json)：当前概率、数据截止和检查状态。
- [历史版本](../live/history/)：追加保存预测、采纳赛果、来源哈希和前一版本哈希。
- [更新设置](../live/settings.json)：模拟次数、已公布完整对阵和八强半区签位。
- Actions 下载包 `private-forecast-results` 只包含上述 JSON 文件，保留 7 天。

首轮对阵已知，可以逐场更新。后续瑞士轮没有完整官方对阵时等待全轮结束；八强开始前需人工确认四组签位，前两组同半区、后两组另一半区。填写签位时同时提供 `draw_source_url` 和带时区的 `draw_as_of`，再手动运行工作流。不得从比赛时间猜测半区。

来源存在缺局、重复、未知队伍或撤回/修正既有赛果时，暂停更新并保留最后有效版本，待人工核查。哈希链用于发现修改，不是第三方时间戳认证。合成测试数据不会进入真实预测。

```bash
python src/live_forecast.py --fetch
python src/check_live.py
```

离线检查使用 `--input 本地快照.csv.gz`；`--export-only` 仅从已有历史记录生成 `live/latest.json`。

数据来源：[ChainCC](https://chaincc.lol/free/data/)，[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。更新器筛选逐局记录、核对双方赛果与局序、聚合系列赛，并保留来源哈希。原始训练数据、模型结果和报告保持冻结。
