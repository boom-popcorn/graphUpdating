# 海上时空知识图谱更新 MVP

这是框架图的第一阶段可执行基线，当前包含：

- 三个统一数据结构：`ObservationEvent`、`CandidateFact`、`GraphChangeSet`。
- 海上时空知识图谱本体 v0.1（OWL/Turtle）。
- 面向观测、候选事实和版本记录的 SHACL 一致性约束。
- AIS 与雷达双模态测试数据。
- “持续低速观测 -> 候选悬停事实 -> 受控图谱变更”的最小闭环。

## 运行

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m maritime_kg.sample_data
.\.venv\Scripts\python.exe -m maritime_kg.demo
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

演示输出位于 `output/demo/`：

- `demo_graph.ttl`：提交更新后的数据图。
- `demo_change_set.json`：结构化变更集。
- `demo_summary.json`：处理数量、版本与耗时摘要。

## 当前边界

当前版本使用确定性规则模拟“语义感知”和“决策规划”，不调用大模型。后续智能体必须通过这三个数据结构交换信息，并只能提交结构化变更集，不能直接修改图谱。

