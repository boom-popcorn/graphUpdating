# 海上时空知识图谱更新 MVP

这是框架图的第一阶段可执行基线，当前包含：

- 三个统一数据结构：`ObservationEvent`、`CandidateFact`、`GraphChangeSet`。
- 海上时空知识图谱本体 v0.1（OWL/Turtle）。
- 面向观测、候选事实和版本记录的 SHACL 一致性约束。
- AIS 与雷达双模态测试数据。
- 五个独立角色：语义感知、本体演化、决策规划、一致性校验、行动执行。
- Harness 协同编排，以及“校验失败 -> 反馈 -> 重规划”的有限闭环。
- 工作台采用两阶段更新：候选变更先完成副本校验，工作人员批准后才原子提交正式图谱。
- 图谱中写入轨迹、状态快照、空间区域和海上事件；支持事实失效与回滚标记。
- `IncrementalUpdateService` 按批次接收观测、去重并交给更新链路；仓库保存版本快照，可恢复到已保存版本。
- 人工批准本体演化建议后，可从 v0.1 生成 v0.2 本体文件。
- “持续低速观测 -> 候选悬停事实 -> 受控图谱变更”的最小闭环。

## 运行

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m maritime_kg.sample_data
.\.venv\Scripts\python.exe -m maritime_kg.demo
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
\.venv\Scripts\python.exe web\server.py
```

演示输出位于 `output/demo/`：

- `demo_graph.ttl`：提交更新后的数据图。
- `demo_change_set.json`：结构化变更集。
- `demo_summary.json`：处理数量、版本与耗时摘要。
- `workflow_report.json`：智能体工作流、校验历史和本体演化建议。

## 当前边界

当前版本使用可测试的确定性 Agent，不调用大模型。Agent 只能通过结构化契约交换信息，只有行动执行 Agent 能在一致性校验通过后提交图谱。本体演化 Agent 只生成需要人工审核的建议，不会自动修改正式本体。

## 可视化学习页面

启动本地页面：

```powershell
\.venv\Scripts\python.exe web\server.py
```

浏览器打开 `http://127.0.0.1:8876`。左侧六个工作区均可操作：

- `态势总览`：查看目标、处理链路和待办事项。
- `目标与轨迹`：选择目标，查看 AIS/雷达融合轨迹和证据记录。
- `更新审核`：批准或拒绝候选事实、本体演化建议；只有批准的事实会写入正式图谱。
- `版本记录`：查看图谱快照、操作日志并恢复历史版本。
- `数据接入`：逐批接收数据，查看来源、标准化观测和去重结果。
- `规则与本体`：查看四层本体、规则库，并审批本体演化建议。

## 增量批次接入

```python
from maritime_kg.harness import UpdateHarness
from maritime_kg.incremental import IncrementalUpdateService

service = IncrementalUpdateService(UpdateHarness(repository))
result = service.consume(new_observation_batch)
print(result.accepted_event_ids)
print(result.duplicate_event_ids)
```

如果需要恢复当前进程中保存的历史版本：

```python
repository.rollback_to(0)
```

当前版本快照保存在内存中；正式部署时还需要把快照或操作日志持久化到数据库。

如果需要将快照保存到磁盘：

```python
repository = MaritimeGraphRepository(
    ontology_path,
    shapes_path,
    snapshot_dir=Path("output/snapshots"),
)
```

快照会保存为 `graph-v0000.ttl`、`graph-v0001.ttl` 等文件，程序重新启动时可以读取最近版本。

