# RigBuilder

> 面向个人计算机硬件与本地 AI 部署的多模型**真值推荐系统**（课程项目：基于大语言模型的真值推荐系统）。

推荐结果不是让模型自由发挥，而是被拆成可核验的几层：模型只能通过只读查询面取得候选 → 两个 Agent 产出候选与断言 → `TruthVerifier` 逐条比对同实体的 `accepted` Evidence → 确定性融合与 CPU/GPU 场景门禁 → 配置草案与理由。核心原则是**没查到就不写、未知就标未知**。

> **📌 当前执行基线：** [Plan V4.5](docs/Plan_documents/Plan_V4.5.md)（架构边界、数据真实性闭环、性能与搭配政策、用户体系路线图、阶段门禁）；其后的完成情况见 [Plan V5](docs/Plan_documents/Plan_V5.md)。两者都显式区分「已实现 / 已验证 / 待端到端验证 / 产品政策」。

## 文档入口

| 文档                                                                     | 用途                                                                                                                                                                                      |
| ------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [Plan V4.5](docs/Plan_documents/Plan_V4.5.md)                             | **当前基线**：三层数据边界、采集时间规则、性能与 `balance_rule` 政策、下一阶段门禁                                                                                                |
| [Plan V5](docs/Plan_documents/Plan_V5.md)                                 | 2026-09-17 至 09-18 的完成情况与**仍未完成项**                                                                                                                                      |
| [快速启动指南](docs/Quick_Start_Guide.md)                                 | 双设备部署、日常启动/停止、验收清单、`[timing]` 日志解读                                                                                                                                |
| [优化记录](docs/Optimization/README.md)                                   | 链路与输出优化报告索引（含[实时 token 用量](docs/Optimization/live-token-usage.md)、[请求路由](docs/Optimization/request-routing.md)、[管理员工作区](docs/Optimization/Admin_Workspace.md)） |
| [API 文档 V3.1](docs/API_documents/API_Documentation_V3.1.md)             | 现行接口约定                                                                                                                                                                              |
| [数据采集规范 V3](docs/Data_documents/Data_Collection_Spec_V3.md)         | release bundle 与字段口径                                                                                                                                                                 |
| [Truth DB 契约 V3.2](docs/Data_documents/Truth_DB_Contract_V3.2.md)       | `truth` / `agent_catalog` / `public` 三层契约                                                                                                                                       |
| [Agent Loop 契约 V3.3](docs/Data_documents/Agent_Loop_Contract_V3.3.md)   | Agent 循环、终止条件与工具协议                                                                                                                                                            |
| [Truth DB V3 远程切换](docs/Data_documents/Truth_DB_V3_Remote_Cutover.md) | 重建/切换真值库的唯一合法路径                                                                                                                                                             |
| [前端设计](docs/frontend_design/frontend_V2.md)                           | 界面与交互基线                                                                                                                                                                            |
| [题目要求](docs/Target.md)                                                | 课程原始要求                                                                                                                                                                              |

历史文档（Plan V1–V4.4、API V1/V2.2/V3、Data Spec V1/V2/V3.1/V3.2）保留在各自目录，仅作演进记录，不代表当前实现。**`/api/agent/run`、模型对比、决策台 V2 及旧直接 Agent 接口已被移除**，相关旧文档不要再按现役接口使用。

## 1. 系统拓扑（双设备、固定双 Agent）

| 设备 | 地址              | 运行内容                                                                                                    |
| ---- | ----------------- | ----------------------------------------------------------------------------------------------------------- |
| A    | `<DEVICE_A_IP>` | Vite`5173`、FastAPI `8000`、PostgreSQL `5432`                                                         |
| B    | `<DEVICE_B_IP>` | `llama-server` `8081` = `agent-a`（Qwen3-8B-Q4_K_M）、`8082` = `agent-b`（Qwen3.5-4B-UD-Q6_K_XL） |

- 固定两个 Agent 实例，**不恢复三 Agent 架构**；两个实例共用一张 GPU，按请求串行。
- 设备 B **不需要本仓库、数据库或任何配置文件**，启动参数就是全部契约（端口与 `--alias` 必须与 A 的 `.env` 逐字符一致）。
- 浏览器只访问 A；A 通过网络调用 B，B 的推理端口只放行 A。

主链路：

```text
用户问题
  → agent-b 生成短执行计划（≤320 tokens）
  → FAST / VERIFIED 路由
  → 两个 Agent 只读 SQL（agent_catalog 视图）
  → 证据绑定与 decision_context
  → TruthVerifier 逐条核验
  → 确定性融合（fusion-v1）→ CPU/GPU 场景门禁
  → 配置草案 + 理由（Narrator，失败即确定性模板）
  → Query Job / SSE / 前端展示
```

旁路：`/api/chat` 为 FAST 通用问答；硬件日报（`/api/hardware-daily/latest`）是**独立阅读模块**，内容与真值库严格隔离，不参与检索、核验或推荐。

## 2. 快速开始

完整步骤（含首次部署、防火墙规则、`.env` 逐键来源、验收与排障）见 [快速启动指南](docs/Quick_Start_Guide.md)。日常启动如下。

**设备 B**：两个终端各起一个实例并保持打开（详见指南 §1）。

**设备 A 终端 1 —— 后端：**

```powershell
Set-Location D:\RigBuilder\backend
$env:NO_PROXY = '127.0.0.1,localhost,<DEVICE_B_IP>'
& '..\.venv\Scripts\python.exe' -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

**设备 A 终端 2 —— 前端：**

```powershell
Set-Location D:\RigBuilder\frontend
npm.cmd run dev -- --host 0.0.0.0 --port 5173 --strictPort
```

本机访问 `http://localhost:5173/`，其他设备访问 `http://<DEVICE_A_IP>:5173/`。

首次部署才需要建虚拟环境并复制配置模板（已有 `.env` 就跳过，否则会覆盖已填好的配置）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
Copy-Item backend\.env.example backend\.env
Copy-Item frontend\.env.example frontend\.env
```

`backend/.env` 与 `frontend/.env` **不进 Git**，需手工创建；模板中需填写处标为 `[填设备 A]`。后端仍兼容读取根目录 `.env`（当前不存在），`backend/.env` 优先。

接口自检：

- `http://127.0.0.1:8000/health` —— 进程健康（不需要数据库）
- `http://127.0.0.1:8000/health/database` —— 数据库连通性、当前 release、`alembic_version`、视图/表计数
- `http://127.0.0.1:8000/docs` —— OpenAPI

> **单 worker 是硬约束。** 任务注册表与推理调度锁都在进程内，`--workers 1` 不要改。推理请求物理串行；多模型由推理机侧按需加载。

> **8000 端口上的旧进程不会自动热加载新路由。** 改了后端代码或跑完迁移后，必须重启后端并核验 OpenAPI，否则会出现「源码已更新、接口仍 404」的假象。

## 3. 数据库与迁移

迁移位于 `backend/alembic/versions/`，当前 head 为 **`0012_hardware_daily_cache`**：

```text
0001 create_truth_db_tables → 0002 conversation_context → 0003 snapshot_message_ondelete_set_null
→ 0004 agent_log_tables → 0005 rebuild_truth_v3 → 0006 freeze_truth_v3_1 → 0007 create_query_jobs
→ 0008 cpu_catalog_integrated_gpu → 0009 performance_surface → 0010 user_accounts
→ 0011 admin_workspace → 0012 hardware_daily_cache
```

日常升级（在 `backend/` 目录执行）：

```powershell
& '..\.venv\Scripts\python.exe' -m scripts.preflight_v3
& '..\.venv\Scripts\alembic.exe' upgrade head
```

> ⚠️ **Truth 重建有硬门禁。** `0005_rebuild_truth_v3` 在缺少远程切换预检、已验证备份、已审阅 Release 与 `-x allow_truth_rebuild=true` 确认时会**直接拒绝执行**；重建必须走[远程切换指南](docs/Data_documents/Truth_DB_V3_Remote_Cutover.md)，**不要先独立执行 DROP/TRUNCATE**。日常增量变更走下面的发布包流程，不走重建。

数据现状（2026-09-16 本机实测，属历史时点，改动后须重新核对）：`truth` 35 表 + `agent_catalog` 16 视图 + `public` 10 表；实体 652、Evidence 2,132（`collected_at` 无空值）；发布包 9 个（`v3-2` … `v3-10`）；`performance_anchor` 19 条锚点、`balance_rule` 9 条规则。逐字段快照可用 `backend/scripts/export_schema_reference.py` 按当前数据库重新生成。

## 4. 目录与模块边界

```text
RigBuilder/
├── frontend/             Vue 3 + TypeScript + Vite + Pinia
│   ├── src/api/          Axios 封装（http/token/auth/conversations/queryJobs/admin/hardwareDaily）
│   ├── src/stores/       Pinia：auth（登录态唯一来源）、conversations
│   ├── src/components/   terminal/（推荐块、事实表、进度、约束表单）、admin/（看板、用户、目录）、auth/、hardware-daily/
│   ├── src/views/        ConversationTerminal.vue、AdminWorkspace.vue、AdminDashboard.vue
│   └── e2e/              Playwright：admin-dashboard / conversation-terminal / hardware-daily + 4 个 live 冒烟
├── backend/              FastAPI + SQLAlchemy + Alembic
│   ├── app/api/          路由：auth / chat / conversations / query / admin(+admin_workspace) / hardware_daily
│   ├── app/services/     用例编排：routing、request_analysis、request_scope、fusion、jobs、narrator、build_assembler…
│   ├── app/agents/       Agent 循环、提示词、工具选择与能力探测
│   ├── app/verification/ TruthVerifier 与断言核验
│   ├── app/fusion/       确定性融合引擎（纯函数）
│   ├── app/tools/        只读查询面白名单（视图注册表 + 字段↔Evidence 绑定）
│   ├── app/models/       Truth DB / public schema 的 ORM 模型
│   ├── app/data_contracts/  V3 bundle 契约与枚举
│   ├── app/admin/        管理员工作区：统计、内省、目录编辑
│   ├── alembic/versions/ 迁移
│   └── scripts/          发布、备份、回滚、验收、评估脚本
├── data/
│   ├── v3/releases/      版本化发布包（v3-2 … v3-10，每包可单独回退）
│   ├── v3/schemas/       由契约生成的 JSON Schema
│   ├── v3/drafts/        待审核草稿（legacy 与 pending）
│   ├── v3/dictionaries/  字段定义等
│   └── raw/              原始抓取产物
└── docs/                 计划、API、数据契约、优化记录、前端设计
```

前端**没有 `/login` 路由**：未登录时 `App.vue` 承载不可关闭的登录弹窗；管理员工作区在 `/admin`。

## 5. 主要 HTTP 接口

| 方法             | 路径                                                                                                   | 说明                                 |
| ---------------- | ------------------------------------------------------------------------------------------------------ | ------------------------------------ |
| POST             | `/api/auth/register` `/login` `/logout`、`GET/PATCH /api/auth/me`、`POST /api/auth/password` | 本地账号（PBKDF2）、令牌、改名改密   |
| POST             | `/api/query/jobs`                                                                                    | 提交推荐/咨询任务，返回 job（202）   |
| GET              | `/api/query/jobs/{id}` `/trace` `/events`                                                        | 任务状态、执行轨迹、SSE 事件流       |
| POST             | `/api/query/jobs/{id}/cancel`                                                                        | 取消任务                             |
| GET/POST/DELETE  | `/api/conversations`、`/api/conversations/{id}`                                                    | 会话列表、详情、创建、删除           |
| POST             | `/api/chat`                                                                                          | FAST 通用问答（不经真值核验）        |
| GET              | `/api/hardware-daily/latest`                                                                         | 硬件日报最新一期（需登录；独立缓存） |
| GET              | `/api/admin/summary` `/stats/agents` `/stats/jobs` `/database` `/endpoints`                  | 管理员看板与推理端点探测             |
| GET/PATCH/DELETE | `/api/admin/catalog`、`/api/admin/catalog/{id}`、`/api/admin/users/{id}`                         | 目录查询与白名单字段编辑、用户删除   |

鉴权口径：SSE 用 `?token=`（EventSource 不能设请求头）；他人资源一律返回 **404 而非 403**（403 会确认 UUID 存在）；`/api/admin/*` 需登录，其中看板与目录编辑需管理员。

## 6. 测试与验收

```powershell
# 后端全量（在 backend/ 目录；--basetemp 避开沙箱对系统临时目录的限制）
& 'D:\RigBuilder\.venv\Scripts\python.exe' -m pytest -q --basetemp=D:\RigBuilder\.pytest_tmp

# 针对真实运行进程的账号隔离验收（39 项；不要占用 8000）
& 'D:\RigBuilder\.venv\Scripts\python.exe' scripts\verify_account_isolation.py --base-url http://127.0.0.1:<port>

# 工具协议冒烟（会真实调用设备 B 的模型）
& '..\.venv\Scripts\python.exe' scripts\capability_smoke.py

# 前端
npm.cmd run test:unit      # vitest
npm.cmd run build          # vue-tsc --noEmit && vite build
npm.cmd run test:e2e       # playwright
npm.cmd run test:live      # 真实后端 + 真实模型
```

最近一次实测（2026-09-18，分支 `dev`，本机）：后端全量 `pytest` **603 passed / 5 skipped / 0 failed**，前端 unit **168 passed / 1 skipped**（22 个测试文件）。真实 HTTP 权限验收 **39/39**、e2e 管理员 7/7、live 浏览器冒烟 2/2 —— 这三项记录于 2026-09-16，**未随本次改动重跑**。

更早的 2026-09-16 记录是后端 425 passed、前端 151 passed：数字增长来自此后新增的测试用例，**两套数字不是同一集合**，也不代表新功能已被端到端验收。

> 测试通过数属于**当时提交时点与当时测试集合**，不能当成「全站永久全绿」。全量跨场景 E2E 仍存在与「确定性展示」改版漂移的历史契约失败项（其断言的文案已在源码中不存在），尚未收敛，且未被删除掩盖。改动后必须重跑受影响的测试集。

## 7. 当前状态（分层口径）

**已实现且已验证**

- 真值链：`TruthVerifier` 逐条核验 + 同实体 `accepted` Evidence 硬约束 + `no_supported_truth_claim` 门禁；发布包 v3-2…v3-10 全部经 `validate-only → dry-run → apply` 导入。
- 数据完整性：`collected_at` 全库可追溯；CPU 核显字段已注册并配齐证据；103/106 模型有变体；空表降至 3 张。
- 推荐质量：性能三层（来源指标 → 人工锚定 `index_100` → 排行视图）、9 条 `balance_rule`、融合后 CPU/GPU 配对门禁、`office-fit-v1` 办公排序、解释路径锁定上一轮选型。
- 用户体系：本地账号、按账号隔离会话/消息/任务/SSE/trace、登录弹窗与账号面板、首个账号自动成为管理员、登录节流。
- 产品面：管理员工作区三区块（数据看板 / 用户管理 / 数据管理一期）、硬件日报、类 CLI 的工作态与**实测**输出 token 展示、单件/整机/介绍/模型推荐分路径输出。

**已实现、待端到端验证**

- 在当前 HTTP 服务 + 真实 LLM + 前端界面上以**固定题集**做端到端验收（事实安全、场景正确、语义诚实、输出完整性、会话、性能六类指标）。**端到端从未跑过固定题集，「推荐变好」目前零证据。**
- 当前版本的 p50/p95、失败率、冷/热态与并发表现——历史单样本耗时不能代替。

**仍未完成**

- 实时价格与总价优化、具体内存/存储/主板/机箱/散热 SKU、完整物理兼容性（`compatibility_edge` 仍为空）；输出只能称为「已核验部件 + 规格方向的方案」，不是完整可采购 BOM。
- 复杂自然语言约束（通用否定、跨轮多条件修改、长会话）；现有规则是覆盖明确场景，不是约束求解器。
- 管理员编辑闭环（一期只放开安全白名单字段）、刷新令牌轮换、注册/任务限流、持久审计与运维演练。
- 性能硬件来源仍是 PassMark 单一 tier-C 来源；`balance_rule` 为未评测的产品政策 v1。

## 8. 不可削弱的约束

- **`TruthVerifier` 不得以「提升成功率」为由放宽**；`no_supported_truth_claim` 门禁与同实体 accepted Evidence 约束必须保留。
- **NULL = 未知**，不等于「无核显 / 不兼容 / 不支持」。原始来源、规范化、估算、实测与推荐政策分层，估算不得覆盖实测。
- 计算分不是实测性能；覆盖率不是成功概率；`index_100` 是人工锚定的**产品政策**，不得改名为「客观百分位」或第三方实测。
- 改查询视图时必须同步字段定义、`EVIDENCE_FIELD_BY_VIEW_COLUMN` 绑定、Prompt、DecisionContext 与 JSON Schema；**只增视图列时注意 PostgreSQL 按位置映射**（新列只能加在末尾）。
- 数据导入顺序固定：审核 → 校验 → `--validate-only` → `--dry-run` → 授权后 `--apply`；append-only 表出错用 `rollback_release.py` 按 `release_id` 精确回滚，不要直接重跑。
- **入库文件禁止出现真实机器地址**，一律用 `<DEVICE_A_IP>` / `<DEVICE_B_IP>`（`127.0.0.1`、`0.0.0.0`、`localhost` 是回环与通配，不替换）。真实地址只存在于未追踪的 `backend/.env`、`frontend/.env`。
- 报告与界面不得把 completed、字段覆盖率、模型自信或人工指数偷换成推荐质量、性能实测或兼容性证明。
- 破坏性 Git 操作（`git reset --hard`、粗范围 restore、`git clean -fd`）一律先列清单等确认：`backend/tests/` 下存在**未被 Git 跟踪且被 ignore 的测试文件**，`git clean -fd` 会静默删除且不可恢复。

## 9. 数据发布流程

1. 备份：`python backend/scripts/backup_database.py`（`pg_dump` 在 `D:\PostgreSQL\18\bin\`）。
2. 组包：内容写入 `data/v3/releases/<key>/`（**不能含 `drafts/` 路径**），再用 `build_v3_manifest.py --release-dir <dir> --release-key <key> --all` 生成 manifest。
3. 导入：`import_v3.py --release <dir>/manifest.json` → `--validate-only` → `--dry-run` → 人工审阅 → 授权后 `--apply`。
4. 导入后**重跑 `apply_field_and_timestamp.py`**（幂等），否则新证据的 `collected_at` 为空。
5. 出错回退：`rollback_release.py --release-key <key> --apply`。

细节与踩坑（最短路径：最小化 payload 会把既有字段写成 NULL、`publisher_key` 解析失败会外键报错、改契约后需重跑 `generate_v3_schema.py`、视图加列必须同步绑定）见 [Plan V4.5](docs/Plan_documents/Plan_V4.5.md) 与各发布包 manifest。

## 10. 演进地图

| 文档                                                                                                                           | 状态                          |
| ------------------------------------------------------------------------------------------------------------------------------ | ----------------------------- |
| `docs/Plan_documents/Plan_V4.5.md`、`Plan_V5.md`                                                                           | **现行基线**            |
| `docs/Optimization/*.md`                                                                                                     | 现行优化记录（09-17 / 09-18） |
| `docs/Quick_Start_Guide.md`、`docs/Target.md`、`docs/frontend_design/`、`docs/API_documents/API_Documentation_V3.1.md` | 现行参考                      |
| `docs/Plan_documents/Plan_V1…V4.4`、`Plan_V2.x`、`Plan_V3.x`                                                            | 历史方案演进                  |
| `docs/API_documents/API_Documentation_V1.md`、`V2.2.md`、`V3.md`                                                         | 历史接口（部分接口已移除）    |
| `docs/Data_documents/Data_Collection_Spec_V1/V2/V3.1/V3.2.md`、`Truth_DB_Contract_V3.1.md`                                 | 历史数据契约                  |

已从仓库移除、仅存在于 Git 历史的文档：`Application_Server_Guide_V1.md`、`Inference_Server_Guide_V1.md`、`Client_Server_Deployment_Guide_V1.md`、`Local_Test_Setup.md`、`docs/CHANGES.md`、`V2.2.md`（旧根路径）。双机部署的现行依据是[快速启动指南](docs/Quick_Start_Guide.md)与 [Plan V4.5](docs/Plan_documents/Plan_V4.5.md) §3。
