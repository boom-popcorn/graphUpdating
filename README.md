# 海上时空知识图谱更新 MVP

这是框架图的第一阶段可执行基线，当前包含：

- 三个统一数据结构：`ObservationEvent`、`CandidateFact`、`GraphChangeSet`。
- 海上时空知识图谱本体 v0.1（OWL/Turtle）。
- 面向观测、候选事实和版本记录的 SHACL 一致性约束。
- AIS 与雷达双模态测试数据。
- 五个独立角色：语义感知、本体演化、决策规划、一致性校验、行动执行。
- Harness 协同编排，以及“校验失败 -> 反馈 -> 重规划”的有限闭环。
- 图谱中写入轨迹、状态快照、空间区域和海上事件；支持事实失效与回滚标记。
- 人工批准本体演化建议后，可从 v0.1 生成 v0.2 本体文件。
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
- `workflow_report.json`：智能体工作流、校验历史和本体演化建议。

## 当前边界

当前版本使用可测试的确定性 Agent，不调用大模型。Agent 只能通过结构化契约交换信息，只有行动执行 Agent 能在一致性校验通过后提交图谱。本体演化 Agent 只生成需要人工审核的建议，不会自动修改正式本体。

