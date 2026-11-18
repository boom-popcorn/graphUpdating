# 海上时空知识图谱自动更新 MVP

本项目以船舶 V-017 在敏感区域 A-03 的活动为案例，实现“轨迹与文本感知 → 本体演化 → 一致性校验 → 更新规划 → 计划校验 → 局部事务写入 → 反馈”的知识更新闭环，提供命令行报告和本地网页演示。

系统支持纯轨迹基线与轨迹＋文本融合两种输入。融合案例结合 AIS/雷达、船舶申报、光电标注文本、VHF 转录、通信监测摘要和举报，生成疑似非法捕捞、身份矛盾与通信异常候选事件，并保留证据与历史状态。候选事件均待人工复核，不代表已确认违法。

## 快速开始

建议使用 Python 3.10+、JDK 17 和现代浏览器。Python 仅使用标准库，前端无需 Node.js。Maven 只用于首次构建或修改 Java 校验器后的重新构建；首次需联网下载依赖，完整 JAR 可离线运行。

```powershell
cd D:\HZAU\KG\code\code0
# 根据本机安装位置调整。JAVA 显式指定实际使用的 Java。
$env:JAVA_HOME = 'C:\Program Files\Java\jdk-17'
$env:JAVA = Join-Path $env:JAVA_HOME 'bin\java.exe'
$env:PATH = (Join-Path $env:JAVA_HOME 'bin') + ';' + $env:PATH
# 已有完整 JAR 且未修改 Java 源码时可跳过构建。
.\hermit\build.ps1
.\web\start_demo.ps1
```

打开 <http://127.0.0.1:8765>，点击“运行闭环”。不要直接打开 HTML。服务仅监听本机，终端按 `Ctrl+C` 停止；可设置 `$env:MARITIME_DEMO_PORT=8766` 更换端口，也可用 `python -m mvp.web_server` 启动。

校验器为 `hermit/target/hermit-validator.jar`，Maven 缓存位于 `.m2repo/`，HermiT 版本为 `1.4.5.519`。启动脚本可寻找本地 JDK 17，但不会覆盖已有 `JAVA`；若其指向旧版 Java，请按上例重新设置。

## 架构与校验边界

| 组件 | 职责 |
| --- | --- |
| 语义感知智能体 | 聚合轨迹，按主体和时间筛选文本，抽取带原文位置的语义事实 |
| 本体演化智能体 | 提议扩展、调用 HermiT、发布版本；校验更新计划并返回约束反馈 |
| 决策规划智能体 | 匹配 R1–R6，生成局部更新计划，按反馈修复计划 |
| 动作执行智能体 | 调用本地图工具，暂存/提交/回滚变更，记录审计和人工复核 |
| Harness | 编排闭环，限制修复次数，汇总计划历史、状态轨迹、反馈与指标 |

四个智能体是本地 Python 类。图工具采用 MCP 风格调用接口，当前不运行 MCP 网络或 stdio 服务，图谱使用内存 JSON 模型。

HermiT 经 Java/OWLAPI 对候选本体及相关实例断言做 OWL 一致性校验；本体演化智能体的 Python 校验器检查计划中的类型、属性范围、时序、证据与规则约束。业务规则由 Python 规划器执行。SWRL 文本用于规则登记与表达，不表示 HermiT 执行了全部业务动作；新事件个体由规划器创建。

本体校验失败不发布版本；计划失败最多修复一次并复核，逐次结果见 `planHistory`。工具失败撤销图谱暂存变更，但此前发布的本体版本保留。本体发布与图谱提交不是跨资源的统一事务。

## 输入与证据

| 文件 | 内容 |
| --- | --- |
| `data/initial_graph.json` | 船舶、敏感区域、旧状态、设备和时间节点 |
| `data/observations_R001.json` | R-001 的 6 条 AIS/雷达观测 |
| `data/texts_R001.json` | 7 条文本，5 条满足本轮筛选条件 |
| `ontology/00/ontology.json`、`ontology.ttl` | 初始本体的运行描述与 OWL Turtle 表达 |

AIS 时刻为 2026-11-11 10:06、10:12、10:19（UTC+08:00），航速 0.8、0.6、0.7 节，持续 780 秒，分段距离合计 0.20 海里。距离累加输入 `segmentDistanceNm`，未由坐标计算。新状态为 S-017-1，旧状态 S-017-0 保留。

文本顶层包含 `roundId/toleranceSeconds/texts`；记录含 `id/source/subject/timestamp/confidence/rawText/span`。置信度须为有限的 0–1 数值，时间戳须带时区。`span` 表示 Python Unicode 字符左闭右开区间 `[start,end)`，不是 UTF-8 字节位置。缺字段、重复或冲突 ID、非法 span/置信度等会报错。

文本按同一船舶和首末 AIS 时间加减容差筛选，默认容差 120 秒，即闭区间 10:04–10:21。纳入 AIS 申报、光电标注、VHF、通信监测、举报 5 条；较早的值班日志和船舶登记被排除，原因见 `excludedTexts`。

`mvp/text.py` 按来源白名单用规则/正则抽取申报船型与渔具、检查结果、呼叫与应答、加密短报文数、通信地址和举报等字段。每个事实保留主体、谓词、值、来源、置信度、原文 ID、字符位置和引用片段。当前支持受控表达与基础否定保护，尚非开放域语言理解；光电标注为文本输入，没有执行图像/视频识别。

`LLMTextExtractor` 是抽象适配接口，默认未连接模型。注入后的模型候选保存在 `textCandidates`，不直接驱动 R4–R6；规则事实进入 `textFacts`。原文始终作为数据处理。

## 本体与规则

初始本体含船舶、状态、区域、时间、设备、事件及图像/视频等基础类，只登记 R1。轨迹更新增加 `SuspectedIllegalFishingActivity`、`hasPotentialIllegalActivity` 和 R2–R3；文本融合再增加：

- 8 个类：`TextEvidence`、`DeclarationStatement`、`VHFCallEvent`、`CommunicationEvent`、`InspectionObservation`、`IdentityConflict`、`CommunicationAnomaly`、`CommunicationEndpoint`。
- 6 个对象属性：`hasTextEvidence`、`declaredAs`、`calledBy`、`sentTo`、`contradicts`、`reportedBy`。
- 8 个数据属性：`callSign`、`declaredVesselType`、`declaredGearType`、`callAttempts`、`responseStatus`、`encryptedMessageCount`、`textSource`、`rawTextSpan`。
- 规则 R4–R6。

| 规则 | 条件与结果 |
| --- | --- |
| R1 | 航速小于 5 节，将新状态归为 LoiteringState |
| R2 | 徘徊状态且区域敏感，建立潜在非法活动关系 |
| R3 | 在 R2 条件上持续超过 600 秒、移动小于 0.5 海里，创建疑似非法捕捞事件 E-017 |
| R4 | 申报“捕捞船/渔船”，检查文本显示“无渔具”或“有定向天线”，创建身份矛盾事件并连接矛盾来源 |
| R5 | 同一 VHF 记录呼叫至少 3 次且无应答，无已应答事实；窗口内加密短报文至少 20 条，创建通信异常事件 |
| R6 | 有举报、R1 命中、区域敏感且已生成主事件，置信度增加 0.05、最高 0.99，威胁等级设为 elevated |

融合案例命中 R1–R6，E-017 置信度从 0.90 提升至 0.95。R4/R5 可在没有主事件时独立生成异常候选；R6 不自动发布告警。

候选本体通过 HermiT 后发布到递增数字版本目录，更新 `versions.json`。从 00 直接运行融合案例发布 01；在纯轨迹 01 上接入文本发布 02。读取与导出按索引处理。

## 命令行与输出

```powershell
# 纯轨迹基线与融合案例
python run_demo.py
python run_demo.py --texts .\data\texts_R001.json
# 异常分支，也可组合 --texts
python run_demo.py --mode ontology_conflict
python run_demo.py --mode plan_rejected
python run_demo.py --mode execution_rollback
# 自定义输入和输出
python run_demo.py --input .\data\observations_R001.json --output-dir .\reports\R-001-manual
# 已发布本体的连续版本演化
python run_demo.py --output-dir .\reports\R-001-version-base
python run_demo.py --texts .\data\texts_R001.json --ontology-dir .\reports\R-001-version-base\ontology --output-dir .\reports\R-001-version-02
```

`--ontology-dir` 仅继承本体历史，图谱仍从 `data/initial_graph.json` 开始，不是多轮图谱快照恢复。`--input` 仅替换观测；状态、事件、幂等 ID 仍有案例约定，当前不是通用海事数据导入器。

标准模式默认输出 `reports/R-001-run/`，文本模式为 `reports/R-001-run-text/`；异常目录为 `R-001-<mode>`，文本模式追加 `-text`。CLI 在隔离临时本体目录运行，不覆盖项目根目录的初始本体。

| 输出 | 用途 |
| --- | --- |
| `round_R-001.json` | 语义增量、本体校验、计划历史、执行审计、反馈、状态及耗时 |
| `graph_after_R-001.json` | 更新后图谱快照 |
| `metrics.csv` | 各阶段耗时及逻辑写入指标 |
| `ontology/versions.json` | 已发布版本索引 |
| `ontology/<版本>/ontology.json`、`ontology.ttl` | 各版本运行描述与 OWL Turtle 本体 |
| `ontology/<版本>/validation_report.json` | 有校验产物版本的校验报告 |

重复使用同一输出目录会覆盖同名文件。导出器保留全部已发布版本，拒绝不完整版本，清理不在索引内的数字版本目录中的自身本体/校验文件，保留其他用户文件。文件采用临时文件替换实现单文件原子写入，不是整目录原子事务。

预期本体拒绝或事务回滚仍生成报告，CLI 退出码为 0；应查看报告 `status` 判断业务结果。Java/JAR 等实际运行错误会中止执行。

## 更新前后文件与结果

路径相对于项目根目录，更新后使用标准融合案例产物：

| 对象 | 更新前 | 更新后 |
| --- | --- | --- |
| OWL 本体 | `ontology/00/ontology.ttl` | `reports/R-001-run-text/ontology/01/ontology.ttl` |
| 本体 JSON | `ontology/00/ontology.json` | `reports/R-001-run-text/ontology/01/ontology.json` |
| 知识图谱 | `data/initial_graph.json` | `reports/R-001-run-text/graph_after_R-001.json` |

纯轨迹结果位于 `reports/R-001-run/`，不能与融合结果混用。知识图谱快照为 JSON，当前没有导出 RDF 图谱文件。

| 指标 | 纯轨迹 | 轨迹＋文本 |
| --- | ---: | ---: |
| 新增节点 | 2 | 10 |
| 新增关系 | 5 | 23 |
| 新增类型断言 | 2 | 12 |
| 登记证据记录 | 6 | 11 |
| 事件证据挂接次数 | 6 | 17 |
| 关闭旧状态 | 1 | 1 |
| 增量逻辑写入 | 22 | 74 |
| 全量重建逻辑写入估算 | 36 | 88 |

E-017 引用 11 条证据（6 轨迹＋5 文本）。17 次总挂接还包含身份矛盾 2 次、通信异常 2 次、VHF/通信源事件各 1 次；同一证据可多次引用。74 项写入为节点、关系、类型、证据登记、证据挂接与状态关闭之和。全量估算比增量多 14 项，属于逻辑项比较，不是数据库性能基准。

旧状态 S-017-0 的 validTo 设为本轮首个 AIS 时刻，历史节点不删除；事件及 provenance 保留来源、时间、原文和事实位置。

## 网页与接口

网页默认运行文本融合；每次从初始本体和图谱独立计算。后端完整执行后返回报告，前端分步回放。“下一步/上一步”不触发或撤销后端事务。标准流程 9 步，本体拒绝 5 步，计划修复 11 步，回滚 9 步。自动播放遇到错误步骤暂停，可继续推进重规划演示。

| 模式 | 行为 |
| --- | --- |
| standard | 校验、提交与反馈 |
| ontology_conflict | 注入错误值域触发冲突，拒绝本体，图谱不写入 |
| plan_rejected | 首版含未声明类型，拒绝后生成第二版，通过复核再提交 |
| execution_rollback | 工具故障，撤销图谱暂存变更，已发布本体保留 |

| 接口 | 请求与用途 |
| --- | --- |
| `GET /api/health` | 服务及 JAR 存在性检查，不保证 Java 可运行 |
| `POST /api/run` | `{"mode":"standard","includeText":true}`；false 使用轨迹基线 |
| `POST /api/review` | `{"eventId":"E-017","status":"confirmed","reason":"人工核查"}`；也支持 rejected |

参数错误返回 400，运行前复核返回 409，服务异常返回 500。复核保留事件与反馈历史。服务只有一个共享内存会话，不按浏览器用户隔离；再次运行替换会话，失败运行保留原会话。网页不自动写入 CLI 报告目录。`mvp/playback.py` 提供独立后端分步执行类，当前网页未使用该类。

## 验证与排错

准备 JDK 17 与完整 JAR 后运行：

```powershell
python -m unittest discover -s tests -v
```

测试集包含 52 项，覆盖闭环、真实 HermiT、计划修复、事务回滚、证据约束、版本导出、文件写入及文本融合。

| 现象 | 处理 |
| --- | --- |
| JAR 缺失 | 执行 hermit/build.ps1，确认构建成功 |
| Java 版本或启动错误 | 检查 JAVA 与 JAVA_HOME，使用 JDK 17；health 成功不能代替运行校验 |
| 点击运行返回 500 | 查看网页错误详情和终端，核对 Java/JAR 与输入；修改源码后重启后端 |
| 浏览器无法调用 API | 使用 HTTP 服务地址，不直接打开本地 HTML |
| 网页结果未进入 reports | 网页只保存会话，另行执行 CLI 生成文件 |

## 目录与适用范围

```text
code0/
├─ data/               # 初始图谱、轨迹与文本
├─ mvp/
│  ├─ agents.py        # 感知、规则匹配、规划
│  ├─ text.py          # 文本事实、候选接口、本体扩展
│  ├─ ontology.py      # 版本发布、HermiT、计划校验
│  ├─ graph.py         # 图谱、事务工具、执行与复核
│  ├─ harness.py       # 闭环、审计和指标
│  ├─ playback.py      # 独立后端分步执行
│  ├─ web_server.py    # HTTP API
│  └─ io.py            # 文件读写
├─ ontology/00/        # 初始本体
├─ hermit/             # Java 校验器与构建配置
├─ rules/              # 轨迹规则 JSON 和 SWRL 登记文本
├─ reports/            # 报告、本体版本、图谱快照
├─ tests/              # 自动化测试
├─ web/                # 演示界面与启动脚本
└─ run_demo.py         # CLI 入口
```

项目验证了本体增量扩展、约束校验、有限重规划、局部事务更新和证据回溯。目前依赖固定案例和受控文本，未接入真实数据流、生产图数据库或模型服务；页面关系图为示意。通用映射与单位转换、真实视觉/通信接入、多轮图谱连续更新、自动威胁告警和持续反馈学习仍需开发。人工反馈当前用于留痕，不会自动训练模型或优化阈值。
