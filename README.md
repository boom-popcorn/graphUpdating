# 海上时空知识图谱自动更新系统

基于动态本体的海上知识图谱更新原型。系统以船舶 `V-017` 在敏感海域 `A-03` 的活动为例，融合 AIS / 雷达轨迹与文本证据，完成行为识别、本体演化、事件聚合、威胁评估、图谱更新和人工复核，提供网页演示、命令行和数据库查询入口。

系统采用 Python 规则驱动的智能体协作流程，由 Harness 统一调度；使用 HermiT 校验 OWL 本体一致性，通过 Apache Jena TDB2 保存图谱，借助 Fuseki 提供 SPARQL 查询和图谱可视化。研判结果属于待人工复核的候选结论。

## 功能与处理流程

```text
轨迹观测 + 文本证据
        ↓
语义感知：识别行为、提取证据、对齐实体与时间窗口
        ↓
本体演化：生成候选类与属性 → HermiT 一致性校验 → 发布版本
        ↓
规则规划：匹配业务规则 → 生成更新计划 → 校验与修复
        ↓
事务执行：登记证据、关闭旧状态、创建新状态和事件、关联来源
        ↓
反馈复核：查看审计、确认或驳回候选事件、记录本体专家意见
```

- **多源证据融合**：关联船舶身份、轨迹、通信和报告信息，保留文本来源及原文字符区间。
- **动态本体管理**：保存初始本体和演化版本，展示类、属性、约束及版本差异。
- **局部图谱更新**：保留历史状态，以事务提交业务图谱、证据、提交审计和幂等记录。
- **持久化与工具调用**：支持内存演示、嵌入式 TDB2，以及 Fuseki 共享数据库；持久化执行通过 MCP stdio 工具完成。
- **可视化与复核**：展示更新过程、知识图谱、示意地图、证据链和本体演化，支持事件复核与本体专家评审。

HermiT 负责本体一致性校验；业务规则匹配由 Python 实现，规则文件中的 SWRL 表达用于描述规则语义。

## 环境与构建

| 组件 | 要求与用途 |
| --- | --- |
| Python | 建议 3.10+；Python 服务仅使用标准库 |
| Java | JDK 17；运行 HermiT、Jena 与 Fuseki |
| Maven | 构建 Java 桥接程序并下载依赖 |
| 浏览器 | 现代浏览器；查看演示、图谱与本体 |
| PowerShell | 执行项目提供的启动和构建脚本 |

前端无需 Node.js 构建。图谱和本体视图通过 `unpkg.com` 加载 vis-network，使用这些页面时需能够访问该 CDN。首次 Maven 构建需要联网，依赖缓存在项目的 `.m2repo/` 中。

在项目根目录执行，按实际安装位置调整 JDK 路径：

```powershell
cd D:\HZAU\KG\code\code0
$env:JAVA_HOME = 'C:\Program Files\Java\jdk-17'
$env:JAVA = Join-Path $env:JAVA_HOME 'bin\java.exe'
$env:PATH = (Join-Path $env:JAVA_HOME 'bin') + ';' + $env:PATH
.\hermit\build.ps1
.\jena\build.ps1
```

构建产物为 `hermit/target/hermit-validator.jar` 和 `jena/target/jena-store.jar`。Jena / Fuseki 使用 5.1.0。已有与源码匹配的完整 JAR 时可跳过构建；修改 Java 源码后需重新构建并重启相关服务。

## 快速开始：数据库与网页演示

### 1. 启动 Fuseki

在已配置 Java 的终端中执行：

```powershell
cd D:\HZAU\KG\code\code0
.\jena\start_fuseki.ps1 -DatasetDir .\storage\fuseki-demo -Port 3030
```

首次启动会导入 `data/initial_graph.json`，并将缺失的初始本体文件复制到数据目录的 `ontology/00/`。已有数据和本体文件会保留，因此无需先运行知识更新就能查看初始图谱和初始本体。

### 2. 启动演示服务

另开一个 PowerShell 终端执行。环境变量按终端独立生效：

```powershell
cd D:\HZAU\KG\code\code0
$env:JAVA_HOME = 'C:\Program Files\Java\jdk-17'
$env:JAVA = Join-Path $env:JAVA_HOME 'bin\java.exe'
$env:MARITIME_FUSEKI_URL = 'http://localhost:3030'
$env:MARITIME_DATASET_DIR = 'D:\HZAU\KG\code\code0\storage\fuseki-demo'
.\web\start_demo.ps1
```

应用配置的数据目录必须与 Fuseki 挂载目录一致。

### 3. 查看并运行案例

| 页面 | 地址 | 用途 |
| --- | --- | --- |
| 更新演示 | [http://127.0.0.1:8765/](http://127.0.0.1:8765/) | 选择场景、运行闭环、分步播放、查看证据和复核结果 |
| 图谱与查询 | [http://localhost:3030/](http://localhost:3030/) | 左侧查看图谱，右侧执行 SPARQL |
| 本体演化全景图 | [http://localhost:3030/ontology_viewer.html](http://localhost:3030/ontology_viewer.html) | 查看初始版本及已发布的演化版本 |

在更新演示中选择“伪装渔船 · 多证据融合”，点击“运行闭环”，再通过“下一步”或自动播放查看处理过程。**运行闭环时后端已执行更新，分步播放用于解释结果。**

更新后，在图谱页面点击“重新加载全图”，或执行以下查询查看业务命名图中的全部三元组。页面查询预设可能包含 `LIMIT 200`，完整展示时可移除该限制。

```sparql
SELECT ?s ?p ?o
WHERE {
  GRAPH <urn:maritime:data> { ?s ?p ?o }
}
```

本体页面从初始版本 `00` 开始显示。版本下拉框根据数据目录 `ontology/` 下实际存在、包含 `ontology.json` 的数字版本目录生成；点击“加载本体”刷新版本列表。版本差异按实际前一版本计算，允许版本编号不连续。

图谱页面需要手动重新加载。两个服务分别在各自终端按 `Ctrl+C` 停止。

> 不同案例复用船舶和轮次标识。切换场景或异常演示时，请为新案例选择独立的数据目录，并同步修改 Fuseki 启动参数和演示服务环境变量；需要重现首次运行时也应使用新的目录，以保留已有数据。

## 命令行使用

以下命令均在项目根目录、已配置 Java 的终端执行。

### 内存演示

不指定 `--dataset-dir` 时使用内存图谱，每次运行从初始数据开始，无需启动 Fuseki。

```powershell
python run_demo.py
python run_demo.py --texts data/texts_R001.json
python run_demo.py --scenario disguised
python run_demo.py --scenario insufficient
```

| 案例 | 内容 |
| --- | --- |
| 默认观测 | 识别船舶在敏感区内的持续小范围活动 |
| 默认观测 + 文本 | 融合轨迹与文本来源证据 |
| `disguised` | 聚合五类子事件，形成高等级威胁评估与本地告警 |
| `insufficient` | 证据不足的对照案例，形成低等级评估，不触发高等级告警 |

### 持久化运行与查询

连接已启动的 Fuseki：

```powershell
$env:MARITIME_FUSEKI_URL = 'http://localhost:3030'
python run_demo.py --scenario disguised --dataset-dir storage\fuseki-demo --output-dir reports\fuseki-disguised
python -m mvp.query_graph --dataset-dir storage\fuseki-demo --vessel V-017
python -m mvp.query_graph --dataset-dir storage\fuseki-demo --subgraph E-017
```

上述案例成功后，状态查询应返回 `{"vesselId":"V-017","states":["S-017-1"]}`。如网页已运行其他案例，请改用独立数据目录。

| 参数 | 说明 |
| --- | --- |
| `--input` | 自定义观测 JSON；不能与 `--scenario` 同时使用 |
| `--texts` | 文本证据 JSON |
| `--scenario` | 内置扩展案例：`disguised`、`insufficient` |
| `--mode` | 正常执行或异常路径演示，见下表 |
| `--dataset-dir` | 使用持久化图谱和 MCP 执行工具 |
| `--ontology-dir` | 指定本体版本来源；单独使用时不恢复历史图谱 |
| `--output-dir` | 指定报告输出目录 |

| 运行模式 | 预期结果 |
| --- | --- |
| `standard` | 正常校验并提交，状态为 `COMMITTED` |
| `ontology_conflict` | 本体校验失败，状态为 `REJECTED`，不发布候选本体、不写入图谱 |
| `plan_rejected` | 无效计划被拒绝，经修复后提交 |
| `execution_rollback` | 执行中故障，状态为 `EXECUTION_FAILED`，图谱事务回滚 |

例如：`python run_demo.py --mode execution_rollback`。业务流程失败可能仍返回进程退出码 0，应读取报告状态判断结果；基础设施故障或计划内容冲突会抛出异常。

## 存储与事务

### 后端选择

| 模式 | 配置 | 适用场景 |
| --- | --- | --- |
| 内存 | CLI 不传 `--dataset-dir`；网页不设置 `MARITIME_DATASET_DIR` | 独立案例演示与场景切换 |
| 嵌入式 TDB2 | 设置数据目录，不设置 `MARITIME_FUSEKI_URL` | 本地持久化执行与顺序查询 |
| Fuseki + TDB2 | 同时设置数据目录和 `MARITIME_FUSEKI_URL` | 网页与命令行共享同一图数据库 |

恢复内存网页模式前，在启动终端清除配置并重启演示服务：

```powershell
Remove-Item Env:MARITIME_DATASET_DIR -ErrorAction SilentlyContinue
Remove-Item Env:MARITIME_FUSEKI_URL -ErrorAction SilentlyContinue
.\web\start_demo.ps1
```

嵌入式示例：清除 `MARITIME_FUSEKI_URL` 后，运行 `python run_demo.py --scenario disguised --dataset-dir storage\embedded-disguised`。

**TDB2 文件只能由一个 Java 进程持有。** Fuseki 模式由服务器持有数据库，应用通过 HTTP 访问；不能同时让嵌入式进程打开同一目录。嵌入式运行与查询应顺序执行。

### RDF 组织与一致性

业务命名空间为 `https://example.org/maritime#`。

| 命名图 | 内容 |
| --- | --- |
| `urn:maritime:data` | 实体类型、业务属性、关系、时间及 `prov:used` 证据链接 |
| `urn:maritime:commits` | `prov:Activity` 提交审计和已执行计划记录 |
| `urn:maritime:metadata` | 无损 JSON 快照，保留列表顺序、空值和原文字符区间 |

业务三元组通过 Jena Model API 差量写入，与快照、审计和幂等记录在同一原生事务中提交；失败则整体回滚。嵌入式模式在事务内读取、检查并写入。Fuseki 模式在提交时比较预期快照，过期写入返回 HTTP 409，调用方需要重新读取并规划。

计划编号由船舶与轮次生成，并校验写入内容摘要。完全相同的图谱计划重放返回 `idempotentReplay: true`、复用提交编号且不增加写入；同编号对应不同写入内容会被拒绝。幂等检查针对图谱执行，本体处理阶段仍可能发布版本。

本体发布与图谱写入是独立阶段：图谱回滚不会撤销已经发布的本体。持久化目录的 `ontology/` 保存本体历史，备份时应同时保存数据库和本体文件。

### 环境变量

| 变量 | 用途 |
| --- | --- |
| `JAVA_HOME` / `JAVA` | JDK 目录 / Java 可执行文件路径 |
| `MARITIME_FUSEKI_URL` | Fuseki 服务地址，例如 `http://localhost:3030` |
| `MARITIME_DATASET_DIR` | 网页服务使用的持久化目录；CLI 使用 `--dataset-dir` |
| `MARITIME_DEMO_PORT` | 演示服务端口，默认 `8765` |

Fuseki 端口通过 `-Port` 设置，默认 `3030`；默认数据目录为 `storage/fuseki-demo`。

## 规则、证据与人工复核

业务规则覆盖以下内容：

| 规则 | 识别或聚合内容 |
| --- | --- |
| R1–R3 | 敏感区内持续小范围活动与徘徊行为 |
| R4 | 船舶身份信息矛盾 |
| R5 | 无 VHF 响应与频繁加密通信 |
| R6 | 报告来源提供的支持证据 |
| R7 | 上游提供的轨迹行为标注 |
| R8 | 多类子事件聚合为威胁评估及本地告警 |
| R9 | 静默、无响应和加密外联组合形成的高风险事件 |

`ThreatAssessment` 通过 `hasSubEvent` 关联子事件。评估置信度取相关子事件与证据置信度的最小值；满足敏感区条件，具备身份、通信和轨迹等必要证据且置信度不低于 `0.80` 时，可形成高等级评估。

文本证据按船舶和 AIS 时间窗口对齐，默认时间容差为 ±120 秒；原文区间采用 Unicode 字符索引 `[start, end)`。报告与子图保留证据和来源，支持回查结论依据。

事件复核状态为 `pending`、`confirmed`、`rejected`，复核记录追加保存，持久化模式通过 MCP 写入。人工复核不会自动训练模型或调整规则。

本体专家评审状态为 `pending`、`approved`、`rejected`，与 HermiT 校验独立。专家驳回用于记录意见，不会自动删除已发布的本体或已有事件。

## 数据与报告

| 路径 | 内容 |
| --- | --- |
| `data/initial_graph.json` | 初始知识图谱 |
| `ontology/00/` | 初始本体 JSON 与 OWL / Turtle |
| `data/observations_R001.json` | 默认轨迹观测 |
| `data/texts_R001.json` | 默认文本证据 |
| `data/observations_disguised.json` | 多证据融合案例 |
| `data/observations_insufficient.json` | 证据不足对照案例 |

自定义案例可参考上述 JSON；需要运行威胁聚合时设置 `enableThreatAssessment: true`。

CLI 在报告目录输出：

- `round_<roundId>.json`：处理结果、计划、执行审计及反馈。
- `graph_after_<roundId>.json`：更新后的图谱快照。
- `metrics.csv`：实体、关系和操作等逻辑计数，用于观察流程结果，不代表性能基准。
- `ontology/versions.json` 与版本目录：本体版本索引、`ontology.json`、`ontology.ttl`，以及演化版本的校验报告。

持久化运行还会保存数据目录中的本体历史。CLI 报告的存储信息包含后端、传输方式、数据目录及 Fuseki 地址等字段。网页服务维护共享的当前会话，不会自动生成上述 CLI 报告文件；需要归档报告时使用命令行。

## 接口与工具

### 演示 HTTP API

服务默认地址为 `http://127.0.0.1:8765`。

| 方法与路径 | 作用及主要参数 |
| --- | --- |
| `GET /api/health` | 检查服务及 JAR 文件存在情况，不等同于完整运行验证 |
| `POST /api/run` | 执行流程：`mode`、`includeText`、`scenario` |
| `POST /api/review` | 事件复核：`eventId`、`status`、`reason` |
| `POST /api/ontology-review` | 本体评审：`version`、`status`、`reviewer`、`reason` |

网页场景标识为 `narrative`、`disguised`、`insufficient`。参数错误返回 400，尚无可复核运行结果时返回 409，基础设施异常返回 500。所有浏览器客户端共享服务的当前会话。

### Fuseki HTTP API

服务默认地址为 `http://localhost:3030`，提供项目配套的图谱与本体页面。

| 方法与路径 | 用途 |
| --- | --- |
| `POST /maritime/query` | SPARQL 查询 |
| `GET /api/snapshot` | 获取图谱快照与数据目录信息 |
| `POST /api/snapshot` | 使用 `expected` / `document` 提交快照，执行事务内比较并写入 |
| `GET /api/ontology/` | 获取实际本体版本列表 |
| `GET /api/ontology/<version>` | 获取指定版本的本体 JSON |

应用通过原子快照接口写入，以保持业务 RDF、审计与快照一致；标准 SPARQL 端点用于查询。服务默认面向本机使用。

### MCP stdio 服务

```powershell
python -m mvp.mcp_server --dataset-dir storage\fuseki-demo
```

从项目根目录启动，继承 Java 配置；访问 Fuseki 时还需继承 `MARITIME_FUSEKI_URL`。内置客户端会自动管理该进程，独立启动主要用于外部工具集成。

服务实现 MCP `2024-11-05`，使用 UTF-8 换行分隔的 JSON-RPC 2.0，支持 `initialize`、`notifications/initialized`、`ping`、`tools/list` 和 `tools/call`。标准输出用于协议消息，诊断写入标准错误；该服务使用 stdio 传输。

| 工具 | 主要参数 | 用途 |
| --- | --- | --- |
| `executePlan` | `plan`，可选 `failAfterOperations` | 执行计划并返回执行结果与审计；故障注入参数仅用于验证回滚 |
| `queryState` | `vesselId` | 查询船舶当前状态 |
| `getSubgraph` | `entityId` | 查询一跳业务子图及关联证据和来源 |
| `recordReview` | `eventId`、`status`、`reason`、`ontology` | 保存事件复核及所需本体信息 |

### 独立本体页面

不启动 Fuseki 时，也可在项目根目录运行：

```powershell
python -m http.server 8080 --bind 127.0.0.1
```

打开 [http://localhost:8080/ontology_viewer.html](http://localhost:8080/ontology_viewer.html)。此方式读取固定的 `storage/fuseki-demo/ontology/`，该目录不可用时回退到项目初始本体。查看其他数据目录的版本时，使用挂载对应目录的 Fuseki 页面。

## 项目结构

```text
code0/
├── run_demo.py             # 命令行入口
├── ontology_viewer.html    # 本体演化可视化
├── mvp/
│   ├── harness.py          # 闭环流程调度
│   ├── agents.py           # 感知、规划、执行等智能体
│   ├── ontology.py         # 本体版本、演化与校验
│   ├── graph.py            # 图谱操作、计划执行与事务语义
│   ├── jena.py             # Jena / Fuseki 存储适配
│   ├── mcp_client.py       # MCP 客户端
│   ├── mcp_server.py       # MCP 工具服务
│   ├── text.py             # 文本提取与证据对齐
│   ├── threat.py           # 威胁评估与事件聚合
│   ├── query_graph.py      # 持久化查询入口
│   └── web_server.py       # 网页演示 API
├── data/                   # 初始图谱与案例输入
├── ontology/00/            # 初始本体
├── rules/                  # 规则配置与语义表达
├── hermit/                 # OWL 一致性校验器与构建脚本
├── jena/                   # TDB2、Fuseki、数据库页面与启动脚本
├── web/                    # 更新演示前端与启动脚本
├── tests/                  # 单元与集成测试
├── storage/                # 持久化数据及本体历史
└── reports/                # CLI 报告输出
```

## 验证与故障排查

完成构建后，在独立终端配置 Java，清除演示服务配置，再执行测试：

```powershell
Remove-Item Env:MARITIME_DATASET_DIR -ErrorAction SilentlyContinue
Remove-Item Env:MARITIME_FUSEKI_URL -ErrorAction SilentlyContinue
python -m unittest discover -s tests -v
```

测试覆盖规则与证据对齐、本体一致性与版本管理、计划拒绝和修复、事务回滚、幂等执行、人工复核，以及真实 HermiT、TDB2、MCP 和 Fuseki 集成。Fuseki 测试使用临时数据目录及独立端口；缺少 Jena JAR 时相关集成测试会跳过，应检查测试输出中的跳过项。

| 现象 | 检查方式 |
| --- | --- |
| Java 或 JAR 不可用 | 检查 `JAVA_HOME` / `JAVA`，执行对应构建脚本 |
| TDB2 目录被锁定 | 检查是否有另一个 Fuseki 或嵌入式进程持有同一目录 |
| 应用连接错误或目录不匹配 | 核对 Fuseki 地址、端口及两端数据目录 |
| 同一计划编号内容冲突 | 为不同场景或模式选择独立的新数据目录 |
| 更新后页面仍显示旧图谱或本体列表 | 点击“重新加载全图”或“加载本体” |
| 图谱或本体网络视图空白 | 检查 vis-network CDN 是否可访问及浏览器控制台错误 |
| 本体已发布但图谱没有提交 | 查看报告状态；本体发布与图谱事务分别处理 |

## 实现范围

本项目用于受控案例下的知识更新研究与本地演示。文本提取和推理以规则为主，LLM 适配为扩展入口；光电识别结果以文本标注输入，盘旋、折返和静止等轨迹行为由上游提供，不直接从原始坐标训练或识别。

当前执行会在内存中处理完整快照与候选 RDF，案例使用固定实体与轮次标识。生产规模的增量存储、通用多轮业务标识、并发重试调度、鉴权、HTTP MCP、实时数据接入、模型训练、发布前专家审批和外部告警投递属于后续扩展范围。本地告警仅作为图谱内的候选研判结果。
