# TravelMind 行程稳定性评测

评测矩阵使用与正式接口相同的 DeepSeek Agent、高德工具、生成策略和确定性质量检查。

## 运行方式

默认只运行一个广州 smoke 案例，避免意外产生整套 API 费用：

```powershell
rtk .\.venv\Scripts\python.exe -m evals.run_itinerary_eval `
  --live --suite smoke `
  --output evals/results/smoke.json
```

显式运行完整 8 案例矩阵：

```powershell
rtk .\.venv\Scripts\python.exe -m evals.run_itinerary_eval `
  --live --suite matrix `
  --output evals/results/matrix.json
```

只运行一个指定案例：

```powershell
rtk .\.venv\Scripts\python.exe -m evals.run_itinerary_eval --live `
  --case xian_history_intensive_max_days
```

## 稳定性门槛

- 案例完成率不低于 80%。
- 同时通过硬校验和质量信号的案例不低于 80%。
- 被后端拒绝的工具调用比例不高于 20%。

所有案例都会独立执行。单案例失败会进入报告并继续下一例；整批结束后，如果汇总指标没有通过门槛，命令以非零状态退出。

报告同时记录每个案例的耗时、模型轮次、输入/输出 Token、工具成功/拒绝/失败次数、质量信号和最终行程。
