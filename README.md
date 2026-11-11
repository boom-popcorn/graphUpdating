# 海上时空知识图谱自动更新 MVP

这是按 `plan.txt` 和“海上时空知识图谱自动更新叙事案例”实现的一轮可运行闭环。示例把 10:06、10:12、10:19 的 AIS/雷达观测聚合为状态增量，演化本体 `00 → 01`，匹配 R1-R3，经本体演化智能体校验计划后，以事务方式增量更新 V-017 子图。

## 运行与汇报界面

需要 Python 3.10 或更高版本、JDK 17 和 Maven。首次构建会从 Maven 仓库下载 OWLAPI/HermiT，打包为项目内的校验器；之后运行不需要网络。

```powershell
cd D:\HZAU\KG\code\code0
.\hermit\build.ps1
python run_demo.py
python -m unittest discover -s tests -v
.\web\start_demo.ps1
```

启动界面后打开 `http://127.0.0.1:8765`。页面会通过本地 Harness 运行实际案例；支持逐步/自动播放，也支持本体冲突拒绝、计划驳回重规划和执行失败回滚三种真实异常路径。人工确认/误报操作会保留事件并记录复核状态。

如果 8765 已被占用，可在启动前切换端口：`$env:MARITIME_DEMO_PORT=8766; .\web\start_demo.ps1`，然后打开对应地址。

命令行演示报告写入 `reports/R-001-run/round_R-001.json`，更新后的图谱快照写入同目录下的 `graph_after_R-001.json`，耗时和增量/全量逻辑写入量对比写入 `metrics.csv`。本次运行使用隔离的临时本体工作区，产出的 OWL/Turtle 与 JSON 版本记录复制到 `reports/R-001-run/ontology/`，不会覆盖 `ontology/` 中已发布的本体版本。可用 `--input` 和 `--output-dir` 指定其他输入与输出目录。

## 智能体职责

- 语义感知智能体：聚合轨迹、计算观测窗口/移动距离，输出结构化 DeltaSemantics；不写图谱或本体。
- 本体演化智能体：生成提案、检查候选本体并发布版本；同时独占更新计划的类型、domain/range、互斥、时间、证据、规则引用和影响子图校验。
- 决策规划智能体：只执行 R1-R3 匹配和计划生成；计划被拒绝时根据校验反馈重规划，不负责校验。
- 行动执行智能体：只执行已通过校验的计划；MCPToolbox 是本地 MCP 风格工具适配器，支持事务暂存、幂等、回滚和操作审计。

## 校验边界

`OntologyConsistencyChecker` 通过 OWLAPI 调用 HermiT 1.4.5，对候选 OWL TBox 和相关图谱断言组成的 ABox 进行实际推理。报告写明推理器名称、本轮是否启动 HermiT（`hermitExecuted`）、是否使用 HermiT 推理结果（`hermitResultUsed`）、缓存状态、冲突原因和耗时；缺少构建产物或 Java 调用失败时本轮会停止，不降级成近似检查。HermiT 结果按候选本体/ABox 序列化摘要缓存，命中时使用已缓存的真实 HermiT 结果并将本轮推理耗时记为 0。

规则文件中的 `swrl`/`swrlPattern` 用于登记规则语义；运行时由 Python 规则匹配器执行可运行条件。R3 的事件创建是规划器的动作，因为标准 SWRL 不能在规则头部生成新的匿名个体；本例由规划器生成确定性 ID `E-017`，再交给行动执行智能体落图。

本地图谱适配器使用内存中的 JSON 图模型，事务提交后由演示脚本输出快照；它验证增量操作、审计及失败回滚逻辑，不是生产级图数据库，也不启动 MCP 网络/stdio 服务。界面中的态势关系图是示意图，因为案例未提供地理坐标，不代表真实海图。演示数据固定回放，不连接真实 AIS、雷达或大模型。增量与全量对比统一按节点、关系、类型、证据来源记录和状态关闭计数；本案例为 22 项增量逻辑操作，全量重建按更新后图快照估算 36 项。这是当前 fixture 的逻辑写入量估算，不是数据库性能基准，也不应与叙事稿中的 7 条关系混淆：当前可运行计划显式创建的是 5 条关系。

## 目录

```text
code0/
├─ data/                 # 叙事案例输入与初始知识图谱
├─ mvp/                  # 四智能体、计划校验、事务工具和 Harness
├─ ontology/00/          # 初始 JSON/OWL Turtle 本体
├─ hermit/               # HermiT Java/OWLAPI 桥与 Maven 构建脚本
├─ rules/                # R1-R3 JSON 与 SWRL 登记文本
├─ reports/R-001-run/    # 本轮报告、图谱快照、指标和隔离的 00/01 本体版本
├─ tests/                # T1-T10 相关核心闭环/边界测试
├─ web/                  # 本地汇报界面与启动脚本
├─ plan.txt              # 用户提供的实现思路，保留原文件
└─ run_demo.py
```

