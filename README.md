# 海上时空知识图谱自动更新

基于动态本体的海上知识图谱更新原型，以船舶 V-017 在敏感海域 A-03 的活动为例，融合 AIS/雷达轨迹与文本证据，完成行为识别、事件聚合、威胁评估和本地告警，提供命令行与网页两种使用方式。

更新流程：**语义感知 → 本体演化与 HermiT 校验 → 规则规划 → 计划校验 → 局部事务写入 → 反馈复核**。系统保留历史状态、证据来源和操作审计，所有研判结果均为待人工复核的候选结论。

## 快速开始

环境要求：Python 3.10+、JDK 17、现代浏览器。Python 仅使用标准库，前端无需 Node.js；Maven 仅用于构建 Java 校验器。

```powershell
cd D:\HZAU\KG\code\code0
$env:JAVA_HOME = 'C:\Program Files\Java\jdk-17'  # 按实际安装位置调整
$env:JAVA = Join-Path $env:JAVA_HOME 'bin\java.exe'
$env:PATH = (Join-Path $env:JAVA_HOME 'bin') + ';' + $env:PATH
.\hermit\build.ps1  # 已有完整 JAR 且未修改 Java 源码时可跳过
.\web\start_demo.ps1
```

打开 [本地演示页面](http://127.0.0.1:8765)，选择场景并点击“运行闭环”，通过下一步或自动播放查看结果。首次构建需联网下载依赖，生成的 `hermit/target/hermit-validator.jar` 可离线运行。设置 `MARITIME_DEMO_PORT` 可更换端口，终端按 `Ctrl+C` 停止服务。

## 场景与运行

在项目根目录执行：

| 场景 | 命令 | 结果 |
| --- | --- | --- |
| 轨迹基线 | `python run_demo.py` | 疑似非法捕捞事件 |
| 轨迹与文本融合 | `python run_demo.py --texts data/texts_R001.json` | 疑似捕捞、身份矛盾、通信异常 |
| 伪装渔船综合研判 | `python run_demo.py --scenario disguised` | 5 类子事件、high / 0.90 事件簇及本地告警 |
| 证据不足反例 | `python run_demo.py --scenario insufficient` | 疑似捕捞与身份矛盾，low / 0.90，不触发高威胁告警 |

网页支持场景切换、流程回放、轨迹示意、原文证据展开、事件复核和本体专家复审。事件簇按 `hasSubEvent` 关联展示子事件卡片，包含置信度、复核状态和可展开的来源证据。

通过 `--mode` 可运行异常分支，网页也提供相应入口：

- `ontology_conflict`：本体冲突，拒绝发布及图谱写入。
- `plan_rejected`：首版计划被拦截，修复并复核后提交。
- `execution_rollback`：工具执行失败，回滚图谱变更。

例如：`python run_demo.py --scenario disguised --mode execution_rollback`。

其他参数：`--input` 指定观测 JSON，`--texts` 指定文本 JSON，`--output-dir` 指定输出目录，`--ontology-dir` 继承已发布本体历史。`--scenario` 与 `--input` 不能同时使用；`--ontology-dir` 不恢复历史图谱快照。

## 架构与规则

| 组件 | 职责 |
| --- | --- |
| 语义感知智能体 | 聚合轨迹，按船舶和时间窗口关联文本，抽取可追溯事实 |
| 本体演化智能体 | 提议语义扩展，调用 HermiT，发布版本并校验更新计划 |
| 决策规划智能体 | 匹配 R1–R9，生成局部更新计划并按反馈修复 |
| 动作执行智能体 | 事务写入、回滚、证据关联和人工复核留痕 |
| Harness | 编排闭环，汇总报告、审计和指标 |

R1–R3 识别低速徘徊及敏感区持续小范围活动；R4 识别申报与检查的身份矛盾；R5 识别多次呼叫无应答与频繁加密通信；R6 用举报佐证已有事件；R7 根据轨迹行为标注生成异常轨迹；R8 组合多类异常进行威胁升级和告警；R9 关联通信静默、呼叫无应答及高频加密外发，生成高风险行为。

`ThreatAssessment` 聚合疑似非法捕捞、身份矛盾、通信异常、异常轨迹和高风险行为。簇置信度取子事件及所用证据的最小值；敏感区内同时存在身份、通信、轨迹异常且置信度至少 0.80 时，升级为 high 并生成 `ThreatAlert`。告警仅保存于本地图谱。

HermiT 负责 OWL 一致性校验，业务匹配和事件创建由 Python 规划器执行，计划校验检查类型、关系、时序及证据约束。图谱写入失败整体回滚，此前发布的本体版本保留。专家复审单独记录，默认 pending；驳回不自动撤回已发布版本或图谱。

## 数据与输出

`data/` 包含初始图谱、轨迹、文本以及综合研判和反例输入；`ontology/00/` 为初始本体。文本按同一船舶和 AIS 时间窗口筛选，默认前后容差 120 秒，事实保留原文、来源及 Unicode 字符区间 `[start,end)`。自定义输入设置 `enableThreatAssessment: true` 启用事件簇研判。

命令行报告写入 `reports/`，可用 `--output-dir` 调整；重复使用同一目录会覆盖同名产物。

| 产物 | 内容 |
| --- | --- |
| `round_R-001.json` | 感知事实、本体提案、计划校验、威胁摘要、执行审计及反馈 |
| `graph_after_R-001.json` | 更新后图谱与证据快照 |
| `metrics.csv` | 耗时及逻辑写入指标 |
| `ontology/versions.json` | 已发布本体版本索引 |
| `ontology/<版本>/` | 本体 JSON、OWL Turtle 和校验报告 |

业务拒绝或回滚仍会生成报告，CLI 退出码为 0，应检查报告 `status`。写入指标属于逻辑项计数，不是数据库性能基准。网页使用共享内存会话，每次运行独立计算，回放不改变后端事务，也不会自动导出 CLI 报告。

## HTTP 接口

| 接口 | 用途及主要参数 |
| --- | --- |
| `GET /api/health` | 检查服务及 JAR 存在性 |
| `POST /api/run` | 运行闭环：`mode`、`includeText`、`scenario`（narrative / disguised / insufficient） |
| `POST /api/review` | 事件复核：`eventId`、`status`（confirmed / rejected）、`reason` |
| `POST /api/ontology-review` | 本体复审：`version`、`status`（approved / rejected）、`reviewer`、`reason` |

参数错误返回 400，未运行即复核返回 409，服务异常返回 500。事件反馈保留历史，本体复审记录于当前版本的校验报告。

## 目录与测试

```text
code0/
├─ data/          # 初始图谱和场景输入
├─ mvp/           # 感知、规划、本体、图谱事务、威胁研判及 HTTP 服务
├─ ontology/      # 初始本体与版本定义
├─ hermit/        # HermiT Java 校验器与构建配置
├─ rules/         # 轨迹规则与 SWRL 登记文本
├─ web/           # 网页演示与启动脚本
├─ tests/         # 自动化测试
├─ reports/       # 运行产物
└─ run_demo.py    # 命令行入口
```

配置 JDK 17 和完整 JAR 后运行 `python -m unittest discover -s tests -v`。测试覆盖闭环、真实 HermiT、计划修复、回滚、证据追溯、事件簇、威胁边界、版本导出与接口。

JAR 缺失时执行 `hermit/build.ps1`；Java 启动失败时检查 `JAVA` 和 `JAVA_HOME`。网页应通过 HTTP 地址访问，后端代码修改后需重启服务。

## 实现范围

项目使用本地 Python 智能体、内存 JSON 图谱和 MCP 风格工具接口，未运行 MCP 网络服务。文本由规则抽取，LLM 仅预留适配接口；光电标注作为文本接入。异常轨迹消费上游 circling / zigzag / stationary 标注，地图为示意，未从坐标自动识别行为。

当前适用于固定案例与受控输入，尚未接入真实数据流、生产图数据库或模型服务。通用语义映射演化、多轮图谱连续更新、发布前审批及外部告警分发仍需开发；人工反馈用于留痕，不自动训练模型或调整阈值。
