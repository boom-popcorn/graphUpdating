# 海上时空知识图谱自动更新 MVP

本项目参考 `plan.txt`，以“海上时空知识图谱自动更新叙事案例”验证项目第四部分“知识更新”的最小可行闭环：聚合观测、生成语义增量、演化本体、规划更新、校验计划、事务执行与反馈。汇报界面思路参考 `webPlan.txt`。

默认案例将 2026 年 11 月 11 日 10:06、10:12、10:19 的 AIS/雷达观测聚合为 V-017 的新状态，使本体从 `00` 演进到 `01`，匹配 R1–R3 后增量更新相关子图。

**职责调整：本体一致性校验和更新计划校验均由本体演化智能体承担；决策规划智能体负责生成计划及根据反馈重规划。**

## 运行与汇报界面

建议使用 Python 3.10 或更高版本、JDK 17 和现代浏览器。Python 端仅使用标准库，无须安装 pip 依赖；前端无需 Node.js 或打包工具。Maven 仅用于首次构建或修改 Java 校验器后的重新构建。首次构建会联网下载 OWLAPI/HermiT，生成完整 JAR 后可离线运行。

```powershell
cd D:\HZAU\KG\code\code0

# 如 JDK 安装位置不同，请修改此路径。
$env:JAVA_HOME = 'C:\Program Files\Java\jdk-17'
$env:PATH = (Join-Path $env:JAVA_HOME 'bin') + ';' + $env:PATH

# 仅首次运行、JAR 缺失或修改 Java 校验器后需要构建。
.\hermit\build.ps1

# 启动网页汇报服务。
.\web\start_demo.ps1
```

构建产物为 `hermit/target/hermit-validator.jar`，Maven 依赖缓存位于项目内 `.m2repo/`。`pom.xml` 中固定 HermiT 依赖版本为 `1.4.5.519`。已有完整 JAR 时，日常运行不需要 Maven。

启动后打开 <http://127.0.0.1:8765>。服务仅监听本机 `127.0.0.1`；保留启动终端，按 `Ctrl+C` 停止服务。不要直接双击 `web/index.html`，因为页面需要调用本地 API。完成 Java 环境配置后，也可直接使用 `python -m mvp.web_server` 启动。

如果 8765 已被占用，可在启动前切换端口：`$env:MARITIME_DEMO_PORT=8766; .\web\start_demo.ps1`，然后打开对应地址。

### 网页分步演示

1. 页面初次打开停在“案例起点”：本体版本 `00`、旧状态 S-017-0、证据 `0 / 6`，不会自动运行或展开最终结果。
2. 点击“运行闭环”，等待后端完成本轮计算；页面停在 `0 / 9`，由讲解者推进。
3. 点击“下一步”逐步展示观测、校验、规划和写入；“上一步”恢复前一阶段的展示内容。“自动播放”每约 2.2 秒推进一次，可点击暂停；播放结束后可再次自动播放。
4. 候选事件出现后，可点击“确认”或“标为误报”。两种操作都保留事件并记录复核结果，不删除历史事件。

| 正常回放步骤 | 展示内容 |
| --- | --- |
| 1：10:06 第一组观测 | 显示轨迹起点及 2 条 AIS/雷达证据 |
| 2：10:12 第二组观测 | 显示后续观测，证据增至 4 条 |
| 3：语义感知完成 | 第三组观测及 6 条证据；最新航速 0.7 节、移动 0.2 海里、AIS 时间窗口持续 780 秒 |
| 4：本体演化提案 | 新增概念、关系与规则，候选版本 01 等待校验 |
| 5：HermiT 本体校验 | 逻辑一致性通过，发布本体版本 01 |
| 6：规则匹配与计划 | 命中 R1–R3，展示候选事件与局部更新计划 |
| 7：计划一致性校验 | 由本体演化智能体检查类型、范围、时序、证据和规则等约束 |
| 8：增量写入 | 展示新状态、事件和证据关系，旧状态结束但历史保留 |
| 9：提交与反馈 | 展示提交编号和本轮增量指标 |

**当前网页采用“后端完成真实计算，前端分步回放”的方式。** `/api/run` 一次性调用 `Harness.run_round()` 完整执行本轮，前端再逐步揭示结果。“下一步”“上一步”不逐次触发或撤销后端事务；网页中讲解“提交”的步骤不等同于真实后端提交时刻。

### 异常场景

每次点击异常演示按钮都会从初始本体和初始图谱独立运行所选场景，完成后停在 `0/N`，再由讲解者推进。

| 按钮 / API 模式 | 回放步数 | 过程与结果 |
| --- | --- | --- |
| 错误本体被拒 / `ontology_conflict` | 5 | 将候选关系 `hasPotentialIllegalActivity` 的值域改为 `State`，触发区域类型约束冲突；HermiT 拒绝提案，本体保持 00、图谱无写入 |
| 错误计划被拦截 / `plan_rejected` | 11 | 首版计划注入未声明类型 `UndeclaredEventType`，本体演化智能体拦截后返回反馈；规划器生成第 2 次方案，经复核后正常执行 |
| 写入失败回滚 / `execution_rollback` | 9 | 执行工具注入故障，事务暂存变更整体撤销，已提交图谱写入为 0；此前发布的本体版本 01 保留 |

自动播放遇到错误步骤会暂停，便于讲解；首版计划被拒后，可继续点击“下一步”或“自动播放”展示重规划。网页运行及人工复核保存在当前服务会话的内存图谱中；重新运行场景会替换会话并重置复核结果，不自动写入 CLI 报告目录。服务只有一个共享当前会话，不按浏览器用户隔离。

## 命令行运行与输出

完成上述 Java 配置与 JAR 构建后，在项目根目录运行：

```powershell
python run_demo.py

# 三种真实异常分支，各自保存到独立的默认报告目录。
python run_demo.py --mode ontology_conflict
python run_demo.py --mode plan_rejected
python run_demo.py --mode execution_rollback

# 自定义观测输入和输出目录。
python run_demo.py --input .\data\observations_R001.json --output-dir .\reports\R-001-manual
python run_demo.py --help
```

默认输入为 `data/observations_R001.json`，初始图谱固定读取 `data/initial_graph.json`，初始本体来自 `ontology/00/`。输出位于 `reports/R-001-run/`：

| 输出 | 内容 |
| --- | --- |
| `round_R-001.json` | 语义增量、本体提案与校验结果、更新计划与校验结果、逐次计划历史 `planHistory`、执行审计、反馈、耗时和状态轨迹 |
| `graph_after_R-001.json` | 更新后的图谱快照 |
| `metrics.csv` | 各阶段及端到端耗时、增量写入量与全量重建估算 |
| `ontology/00/`、`ontology/01/` | 初始与已发布的演化后 JSON/Turtle 本体；拒绝本体提案时仅导出 00，01 发布后包含校验报告 |
| `ontology/versions.json` | 本轮版本索引 |

CLI 使用隔离临时本体工作区，结果复制到报告目录，不覆盖项目根目录的 `ontology/`。正常模式默认输出 `reports/R-001-run/`；三种异常模式分别输出 `reports/R-001-ontology_conflict/`、`reports/R-001-plan_rejected/` 和 `reports/R-001-execution_rollback/`，也可通过 `--output-dir` 指定目录。重复使用同一输出目录会覆盖同名文件；若本次本体被拒，会清理上次导出的 01 本体文件并将版本索引恢复为 00，避免残留文件被误认为本轮已发布。网页运行与 CLI 报告相互独立。

`--input` 仅替换观测，不替换初始图谱；状态、事件等 ID 仍有本案例约定，当前脚本不是任意海事案例的通用导入器。`--mode` 支持 `standard` 及上述三种异常模式。预期的本体拒绝和事务回滚属于演示结果，CLI 正常生成报告并以退出码 0 结束；应读取报告中的 `status` 判断业务结果。缺少 JAR、Java 调用失败等运行错误仍会中止。

## 智能体职责

- 语义感知智能体：聚合轨迹、计算观测窗口/移动距离，输出结构化 DeltaSemantics；不写图谱或本体。
- 本体演化智能体：生成提案、检查候选本体并发布版本；同时独占更新计划的类型、domain/range、互斥、时间、证据、规则引用和影响子图校验。
- 决策规划智能体：只执行 R1-R3 匹配和计划生成；计划被拒绝时根据校验反馈重规划，不负责校验。
- 行动执行智能体：只执行已通过校验的计划；MCPToolbox 是本地 MCP 风格工具适配器，支持事务暂存、幂等、回滚和操作审计。

`DecisionPlanningAgent.replan()` 在被拒计划的深拷贝上读取 `validation_report["suggestions"]`，按反馈修补而不重新调用 `plan()`：事件类型未声明时，使用当前本体 R3 的 `then.eventType` 恢复事件声明及类型断言；证据缺失、来源不匹配或证据未登记时，从当前语义增量中选取本船的真实证据，修复挂接及登记。保留其他计划内容，记录 `replanFeedback`、`replanRepairs` 和递增的 `replanAttempt`。Harness 的 `planHistory` 同时保留被拒版本及复核结果，最多重规划一次，修复后仍由本体演化智能体重新校验。未识别的反馈不会自动放行，没有可用证据时不伪造来源；这是针对当前校验反馈的确定性修复机制。

人工复核使用执行智能体持有的当前 `OntologyModel.inferred_types()`，经 `ancestors()` 递归读取类继承关系；新增事件子类无需修改复核代码。Harness 和后端八步回放会在本体发布后更新执行智能体的本体引用。单独调用 `ActionExecutionAgent` 进行复核时需传入 `ontology` 模型。

## 校验边界

`OntologyConsistencyChecker` 保留原类名，但内部已通过 Java/OWLAPI 调用真实 HermiT 推理器。Python 先做提案版本、类与属性声明、规则引用等结构检查；通过后，对候选 OWL TBox 和相关图谱断言组成的 ABox 进行实际推理。结构检查提前拒绝的提案可能尚未调用 HermiT。

报告包含 `engine`、`hermitExecuted`、`hermitResultUsed`、`cacheHit`、`durationMs`、`cachedDurationMs`、`reason` 和 `violatedConstraints`。缺少构建产物、Java 调用失败或推理超时会中止，不降级成近似检查。结果在同一 Python 进程内按候选本体/ABox 序列化摘要缓存；命中时使用已有的真实 HermiT 结果，`hermitExecuted=false`、`hermitResultUsed=true`、`durationMs=0`，原推理耗时放在 `cachedDurationMs` 中。

更新计划约束由 `OntologyAgent.validate_plan()` 在 Python 中校验，不是 HermiT 对整份更新计划的自动证明。它检查对象类型、domain/range、互斥、时序、证据、规则与影响子图；该方法归属本体演化智能体，而非决策规划智能体。

Java 选择优先级为：构造参数 `java_command` → 环境变量 `JAVA` → `JAVA_HOME/bin/java.exe` → PATH 中的 `java`。如设置过 `JAVA`，应确认其路径有效；仅修改 `JAVA_HOME` 不会覆盖 `JAVA`。

规则文件中的 `swrl`/`swrlPattern` 用于登记规则语义；运行时由 Python 规则匹配器执行可运行条件。R3 的事件创建是规划器的动作，因为标准 SWRL 不能在规则头部生成新的匿名个体；本例由规划器生成确定性 ID `E-017`，再交给行动执行智能体落图。

| 规则 | 当前运行条件 |
| --- | --- |
| R1 | 最新 AIS 航速严格小于 5 节，将新状态规划为 `LoiteringState` |
| R2 | 低速状态成立且区域敏感，规划船舶到区域的潜在风险关系 |
| R3 | R2 成立、持续时间严格大于 600 秒、累计移动严格小于 0.5 海里，规划创建候选事件 |

`rules/r1_r3.json`、`rules/r1_r3.swrl` 是规则语义登记文件；规划器运行时读取的是本体模型中的规则定义，不直接加载上述文件作为 SWRL 引擎输入。调整阈值时需同步核对 `ontology/00/ontology.json`、`OntologyAgent.propose()` 中的新增规则和相关测试。

默认正常案例新增 2 个节点、5 条关系、2 条类型断言，挂接 6 条证据，关闭旧状态 S-017-0 并保留其历史。**叙事案例中的“7 条类型/关系断言” = `createdRelations=5` + `createdTypeAssertions=2`，不能表述为“7 条关系”。** 其中 5 条关系为船舶→新状态、船舶→风险区域、事件→主体、事件→客体、事件→时刻；2 条类型断言分别给新状态和事件赋型，证据挂接另行统计。

增量 22 项的组成是：2 个节点 + 5 条关系 + 2 条类型断言 + 6 条证据记录 + 6 条新增来源记录 + 1 次旧状态关闭；全量估算 36 项，估算避免 14 项写入。候选事件初始 `reviewStatus=pending`，需人工复核，不能直接视为已确认的违法事实。

## 本地 HTTP 接口与回放实现

| 接口 | 说明 |
| --- | --- |
| `GET /`、`GET /index.html` | 返回汇报页面 |
| `GET /api/health` | 检查校验器 JAR 是否存在；不启动 Java，也不验证 Java 版本 |
| `POST /api/run` | 请求体 `{"mode":"standard"}`，或上述三种异常模式；返回 `report`、`graph`、`initialGraph`、`mode` |
| `POST /api/review` | 请求体包含 `eventId`、`status`、`reason`；`status` 为 `confirmed` 或 `rejected`，修改当前内存图谱 |

请求体必须是 JSON 对象，最大 1 MiB。非法 JSON、无效模式、错误复核状态或未知事件返回 HTTP 400；尚未运行场景就请求复核返回 409；后端运行异常返回 500。本轮运行失败时清理新建临时工作区，保留此前可用会话。

`mvp/playback.py` 中另有 `PlaybackSession`，可通过每次 `advance()` 执行一个真实后端阶段，总计 8 个阶段。**当前 `mvp/web_server.py` 未接入这个类，也没有逐步执行 API**；网页使用 `web/index.html` 的前端回放状态机。两者的步数与执行语义不同。

## 测试与排查

```powershell
cd D:\HZAU\KG\code\code0
# 需先配置 Java 并构建 hermit/target/hermit-validator.jar。
python -m unittest -v
# 也可使用 python -m unittest discover -s tests -v
```

当前共 32 项测试：`tests/test_mvp.py` 的 10 项核心测试，及 `tests/test_engineering.py` 的 22 项工程测试。覆盖正常闭环、真实 HermiT 校验、规则阈值、本体拒绝、反馈驱动修复、计划历史、动态事件继承、幂等和回滚；实际启动临时 HTTP 服务验证 `/api/run`、`/api/review`、`/api/health`；检查八步回放仅在第 7 步写入、异常中止与完成后不重复提交；验证四种 CLI 模式导出和同目录残留清理，以及原子写入的编码、失败保全、临时文件清理和并发覆盖。测试使用临时工作区，不覆盖已有 CLI 报告；不包含网页按钮及布局的自动化验证。

`mvp/io.py` 的 JSON/文本写入使用目标同目录的唯一临时文件，写完后 flush/fsync，再通过 `os.replace` 替换；同进程的最终替换加锁以避免 Windows 并发替换冲突。序列化、刷新或替换失败时保留原文件并清理临时文件。这是单文件原子替换，不承诺整份报告目录的跨文件事务。

| 问题 | 排查方法 |
| --- | --- |
| `HermiT bridge not found` | 运行 `.\hermit\build.ps1`，确认目标 JAR 已生成 |
| Java 调用失败 | 检查 `java -version`、`JAVA_HOME` 与 `JAVA`；`JAVA` 优先于 `JAVA_HOME` |
| `mvn` 找不到 | 构建需要 Maven 加入 PATH；已有完整 JAR 时日常运行不需要 Maven |
| 端口被占用 | 设置 `MARITIME_DEMO_PORT`，访问与终端显示一致的新地址 |
| 页面运行失败但显示服务就绪 | `/api/health` 仅检查 JAR 存在；检查启动终端中的 Java/后端错误 |
| 页面停在 `0/N` | 等待讲解者点击“下一步”或“自动播放”，不是运行失败 |
| 计划被拒后播放暂停 | 错误步骤会自动暂停，可继续推进重规划 |
| 网页结果未保存到 `reports/` | 网页为临时会话；持久化 CLI 报告需另行执行 `python run_demo.py` |

## MVP 适用范围

本地图谱适配器使用内存 JSON 图模型，验证增量操作、审计、幂等及失败回滚逻辑，不是生产级图数据库，也不启动 MCP 网络/stdio 服务。四个智能体由本地 Python 类实现，未连接大模型；AIS/雷达观测来自固定案例文件。移动距离按输入 `segmentDistanceNm` 累加，不做经纬度计算；页面态势关系图为示意图，不代表真实海图。

增量与全量数字是当前 fixture 的逻辑项统计，不是数据库性能基准；实际耗时受运行环境及推理缓存影响。材料统一使用“新增 5 条关系、2 条类型断言，合计 7 条类型/关系断言”。原始叙事 Word 与申请书 PDF 保持不变。

### 与申请书知识更新目标的关系

本轮按 `MVP收尾与工程化.txt` 完成闭环收尾。对应申请书第四部分，已验证本体增量扩展、约束校验、规划反馈修复、局部事务更新与审计的单案例链路。申请书中的数据/本体/规则/动作/语义映射协同演化算子、字段重定向与单位转换、多模态身份/装备/通信证据融合、威胁等级与告警，以及持续多轮反馈优化仍属于后续研究与开发范围；本轮收尾不代表申请书全部知识更新任务已完成。

## 目录

```text
code0/
├─ data/                 # 叙事案例输入与初始知识图谱
├─ mvp/                  # 四智能体、计划校验、事务工具和 Harness
│  ├─ agents.py          # 语义感知与决策规划
│  ├─ ontology.py        # 本体、HermiT 调用与计划约束校验
│  ├─ graph.py           # 图谱、事务工具、执行智能体和人工反馈
│  ├─ harness.py         # 完整闭环编排和异常注入
│  ├─ playback.py        # 后端逐步执行类，当前未接入网页
│  ├─ web_server.py      # HTTP API
│  └─ io.py              # JSON/文本读写
├─ ontology/00/          # 初始 JSON/OWL Turtle 本体
├─ hermit/               # HermiT Java/OWLAPI 桥与 Maven 构建脚本
├─ .m2repo/              # 构建使用的 Maven 依赖缓存
├─ rules/                # R1-R3 JSON 与 SWRL 登记文本
├─ reports/R-001-run/    # 本轮报告、图谱快照、指标和隔离的 00/01 本体版本
├─ tests/                # 32 项核心闭环、接口、回放、CLI 与原子写入测试
├─ web/                  # 本地汇报界面与启动脚本
├─ plan.txt              # 用户提供的实现思路，保留原文件
├─ webPlan.txt           # 用户提供的汇报界面思路
├─ run_demo.py           # 命令行入口
└─ README.md
```

