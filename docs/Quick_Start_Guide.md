# RigBuilder 快速启动指南：设备 A（服务）+ 设备 B（推理）

> 2026-09-16 V4.4 更新：办公整机现在显示逐项配置草案，未核验的核显/配件会明确标注；这不是全套兼容性已验证的采购单。更新代码后重启设备 A 后端并刷新前端，设备 B 两个 llama-server 不需要因本次代码变更重启；本次没有数据库迁移或 Truth 数据导入。重构方案见 `docs/Plan_documents/Plan_V4.4.md`。可在 backend 目录运行 `python scripts/eval_office_refactor.py --live` 复现两轮测试（会新增一个测试会话）。

> 2026-09-15。两台机器，角色固定，**A 不是 B**：

| 设备 | 地址 | 职责 | 配置文件 |
|---|---|---|---|
| A | `<DEVICE_A_IP>` | 前端 Vite :5173、后端 FastAPI :8000、PostgreSQL :5432 | `backend/.env`、`frontend/.env`（由 `.env.example` 复制） |
| B | `<DEVICE_B_IP>` | 两个 llama-server：:8081 `agent-a`、:8082 `agent-b` | **不需要任何配置文件**，也不需要本仓库或数据库 |

浏览器访问 A；A 通过网络调用 B；B 的推理端口只放行 A。文中命令都在对应设备上执行。架构与固定双 Agent 的依据见 [Plan_V4.3](Plan_documents/Plan_V4.3.md)。

**日常使用：B 的两个推理进程已启动、A 的配置已填好时，直接看第 4 节。只需在 A 打开两个 PowerShell 终端，分别运行后端和前端。第 1–3 节主要用于首次部署或换设备。**

## 1. 设备 B：启动推理

两个终端各跑一个实例并保持打开（`Ctrl+C` 停止）。参数只有权重文件名、端口、别名不同。

**终端 1 —— `agent-a`，Qwen3-8B：**

```powershell
$llamaExe  = 'D:\code\Ground-Turth System\llama-b10630-bin-win-cuda-12.4-x64\llama-server.exe'
$modelPath = 'D:\code\Ground-Turth System\models\Qwen3-8B-Q4_K_M.gguf'
& $llamaExe --host 0.0.0.0 --port 8081 --alias agent-a --model $modelPath `
    --n-gpu-layers all --ctx-size 16384 --cache-type-k q8_0 --cache-type-v q8_0 `
    --flash-attn on --jinja --parallel 1
```

**终端 2 —— `agent-b`，Qwen3.5-4B：** 与终端 1 只差三处（权重文件名、端口、别名）。

```powershell
$llamaExe  = 'D:\code\Ground-Turth System\llama-b10630-bin-win-cuda-12.4-x64\llama-server.exe'
$modelPath = 'D:\code\Ground-Turth System\models\Qwen3.5-4B-UD-Q6_K_XL.gguf'
& $llamaExe --host 0.0.0.0 --port 8082 --alias agent-b --model $modelPath `
    --n-gpu-layers all --ctx-size 16384 --cache-type-k q8_0 --cache-type-v q8_0 `
    --flash-attn on --jinja --parallel 1
```

要点：

- **必须 `--host 0.0.0.0`**，否则 A 连不上（只监听回环时只有 B 自己能访问）。
- `--port` 与 `--alias` 必须与设备 A 的 `.env` 一致；`--ctx-size` 必须等于 A 的 `AGENT_DEFAULT_CONTEXT_SIZE`。
- 两个实例各占权重与 KV cache，合计约 9–12 GB 显存（RTX 3090 Ti 24 GB 有余量）；启动日志要确认 GPU 实际 offload，别把 CPU 回退当成网络慢。

**在 B 上自检（回环即 B 自身）：**

```powershell
foreach ($port in @(8081, 8082)) {
    (Invoke-RestMethod "http://127.0.0.1:$port/v1/models").data | Select-Object id
}
```

期望分别返回 `agent-a`、`agent-b`。端口 LISTENING 不代表权重加载完成。

## 2. 设备 B：只放行设备 A

在 B 上用**管理员 PowerShell**执行（WLAN 是 Public 网络类别，规则必须 `-Profile Any`；来源由 `-RemoteAddress` 限定为 A，不是靠网络类别）：

```powershell
New-NetFirewallRule -DisplayName 'RigBuilder inference from A' `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8081,8082 `
    -RemoteAddress <DEVICE_A_IP> -Profile Any
```

没有这条规则时，A 会连接失败，而 B 自己的自检仍然正常。

## 3. 设备 A：首次配置（已有 `.env` 就跳过）

本机 A 的 `backend/.env` 和 `frontend/.env` 已于 2026-09-15 按当前模板配置，数据库账号口令已保留。**日常启动不需要重新复制模板，否则可能覆盖已填好的配置。** 换机器、没有 `.env` 时才执行：

```powershell
Set-Location D:\RigBuilder\backend
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
Set-Location ..\frontend
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

`.env.example` 里推理部分已按设备 B 填好，只需补 `[填设备 A]` 的数据库账号口令。逐键来源：

| A 要填的键 | 取值 | 由设备 B 的什么决定 |
|---|---|---|
| `LLM_BASE_URL` | `http://<DEVICE_B_IP>:8081/v1` | `agent-a` 的 `--port`；路径固定 `/v1` |
| `LLM_MODEL` | `agent-a` | `agent-a` 的 `--alias`，逐字符一致，否则 chat 报「模型不存在」 |
| `AGENT_A_BASE_URL` / `AGENT_A_MODEL` | `...:8081/v1` / `agent-a` | `agent-a` 的端口与别名 |
| `AGENT_B_BASE_URL` / `AGENT_B_MODEL` | `...:8082/v1` / `agent-b` | `agent-b` 的端口与别名 |
| `LLM_TEST_MODEL_IDS` | `agent-a,agent-b` | 顺序与 A/B profile 一致；恰好两个、互不重复 |
| `NARRATOR_PROFILE_ID` | `agent-b` | 复用 4B 实例，不新增常驻模型 |
| `AGENT_DEFAULT_CONTEXT_SIZE` | `16384` | 必须等于 B 两个实例的 `--ctx-size` |
| `NO_PROXY`（启动后端的终端） | `127.0.0.1,localhost,<DEVICE_B_IP>` | 让本地请求和发往 B 的请求绕过代理，避免代理导致连接失败 |

`backend/.env`、`frontend/.env` 不进 Git，需在 A 上手工复制。

## 4. 设备 A：日常手动启动

### 第一步：确认数据库已运行

设备 B 的两个推理终端保持打开。设备 A 还需要本机 PostgreSQL 服务；本机服务名为 `postgresql-x64-18`，2026-09-15 本次检查时为 Running。重启电脑后不确定是否已启动，可在 PowerShell 查看：

```powershell
Get-Service -Name postgresql-x64-18
```

显示 `Running` 就继续，不需要再启动数据库。若为 `Stopped`，用**管理员 PowerShell**执行下面一行，然后关闭这个管理员窗口即可：

```powershell
Start-Service -Name postgresql-x64-18
```

换机器后服务名可能不同，可用 `Get-Service -Name '*postgres*'` 查看实际名称。本机 Python 虚拟环境和前端依赖已存在，日常不需要重新安装，也不需要手动激活虚拟环境。

### 第二步：终端 1 启动后端

在 A 打开一个普通 **PowerShell** 终端，复制运行整组命令：

```powershell
Set-Location D:\RigBuilder\backend
$env:NO_PROXY = '127.0.0.1,localhost,<DEVICE_B_IP>'
& '..\.venv\Scripts\python.exe' -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

看到 `Application startup complete` 和 `Uvicorn running on http://0.0.0.0:8000`，说明后端已启动。**让这个终端保持运行，不要关闭，也不要按 Ctrl+C。** 若出现报错而没有启动完成，先看第 6 节。

`NO_PROXY` 只对设置它的终端及其后续子进程生效，**每次新开后端终端都要执行第二行**；只把它写进 `backend/.env` 不会自动设置 HTTP 客户端的代理环境。`--workers 1` 保持不变，任务注册表与推理锁都在单进程内。

### 第三步：终端 2 启动前端

再打开一个普通 **PowerShell** 终端，复制运行：

```powershell
Set-Location D:\RigBuilder\frontend
npm.cmd run dev -- --host 0.0.0.0 --port 5173 --strictPort
```

看到 Vite 的 `ready` 和 `Local` / `Network` 地址，说明前端已启动。**这个终端也保持打开。**

### 第四步：打开页面

在设备 A 自己的浏览器打开 **http://localhost:5173/**。其他能访问 A 的设备使用 **http://<DEVICE_A_IP>:5173/**。先发送“你是谁”确认普通问答，再试硬件推荐；如果连接失败，按第 5 节检查。

正常使用时：A 的两个终端保持运行，B 的两个推理终端也保持运行。浏览器关闭不会停止这些服务。

### 停止与重启

- **停止 A 的服务**：先等当前回答结束，在前端终端按 `Ctrl+C`，再在后端终端按 `Ctrl+C`。如果提示“终止批处理操作吗”，输入 `Y` 并回车。
- **再次启动**：重复第二步、第三步的整组命令。不要在旧进程仍运行时重复启动同一个端口。
- **改了 `backend/.env` 或后端代码**：在后端终端按 `Ctrl+C`，重新执行第二步。
- **改了 `frontend/.env`**：在前端终端按 `Ctrl+C`，重新执行第三步；仅刷新网页不会重新加载 Vite 环境配置。
- A 的前后端停止或重启时，B 的推理进程可以继续保持运行；日常结束无需停止 PostgreSQL Windows 服务。

## 5. 验收（在设备 A 上）

```powershell
$deviceB = '<DEVICE_B_IP>'
$env:NO_PROXY = "127.0.0.1,localhost,$deviceB"
Test-NetConnection $deviceB -Port 8081
Test-NetConnection $deviceB -Port 8082
Invoke-RestMethod http://127.0.0.1:8000/health/database
Invoke-RestMethod http://127.0.0.1:8000/api/admin/endpoints | ConvertTo-Json -Depth 6
```

期望：两个端口通；`configuration_issues` 为空；两个 profile 指向 `<DEVICE_B_IP>:8081/8082`，`configured_model_id` 为 `agent-a` / `agent-b`，`reachable=true`。

工具协议冒烟（首次部署或更换模型时按需执行，**不是每次启动必做**；会真实调用 B 的模型；退出码 0 = 全部 READY，1 = 工具协议不兼容，2 = 有不可达）：

```powershell
Set-Location D:\RigBuilder\backend
$env:NO_PROXY = '127.0.0.1,localhost,<DEVICE_B_IP>'
& '..\.venv\Scripts\python.exe' scripts\capability_smoke.py
```

前端依次试三条：

1. `你是谁` → FAST 直接回答。
2. `推荐一个入门级显卡，不要查询数据库，用你自己的知识回答。` → 应解析为 `chat`，不查库。
3. `请推荐一款适合本地运行 7B 模型、显存优先且功耗不过高的显卡` → 走 VERIFIED，核对显存/功耗的证据与理由。

判据：`status=completed` 不等于可信。`kind=fusion` 表示核验融合路径，还要检查候选非空、实际证据和约束结果；`chat` 是通用知识；`chat_fallback` 是核验未完成后的通用建议，**不能当作数据库已核验**。用 PowerShell 提交中文请求体时必须转 UTF-8 字节，否则中文变 `?` 会导致路由错误。

## 6. 常见问题

| 现象 | 检查顺序 |
|---|---|
| A 连不上 B:8081/8082 | B 是否 `--host 0.0.0.0`；§2 的防火墙规则是否已建且来源为 A 的地址；B 的 `/v1/models` 是否已有模型 |
| B 自检正常但 A 调用失败 | A 的 `LLM_BASE_URL`、`AGENT_A_BASE_URL`、`AGENT_B_BASE_URL` 是否都写 B 的地址；启动后端的终端是否设了含 B 地址的 `NO_PROXY` |
| 页面能开、API 代理失败 | A 的 FastAPI 是否监听 8000；`VITE_API_PROXY_TARGET` 是否为 `http://127.0.0.1:8000` |
| 后端启动时报数据库连接被拒绝 | 用第 4 节命令确认 PostgreSQL 为 Running；再检查 `backend/.env` 的本机数据库连接 |
| 8000/5173 端口已占用、`WinError 10048` 或 `Port 5173 is already in use` | 检查之前开的终端；旧服务仍在运行就直接使用，需要重启则先在原终端按 Ctrl+C |
| 改 `.env` 后没变化 | 终端里是否有同名环境变量覆盖；后端进程是否真的重启过 |
| 中文「不查询数据库」仍走 VERIFIED | 检查实际请求正文是否为 UTF-8 |
| VERIFIED 很慢 | 看 B 的 GPU 卸载/显存/CPU 回退，以及每轮 prompt/completion 耗时 |

## 7. 边界

- 设备 B 不需要本仓库、数据库或任何 `.env`；它的启动参数就是本文 §1、放行规则就是 §2。
- 手动停止优先在对应终端按 `Ctrl+C`，设备 B 不需要为停止推理另行复制本仓库。A 上的 `backend/scripts/stop_local_stack.ps1 -BackendOnly` 仅作为已有脚本的备用方式，日常手动启动不依赖它。
- 仓库里原有的**单设备**脚本（`start_local_stack.ps1`、`stack_status.ps1` 及其机器本地配置模板）会启动本机后端/前端并把推理绑到回环，与双机拓扑冲突，已删除，不要指望它们。
- B 换地址或端口时，同步检查 A 的三个推理 URL 和 `NO_PROXY`；A 换地址时，同步修改 B 防火墙规则的来源地址。


## 7. 看后端计时，定位推荐慢在哪一步

更新本轮代码后，按第 4 节重启 **A 的后端** 即可；B 的两个推理进程可以保持运行。普通启动命令就会输出 `[timing]`，无需打开调试模式。

日志形状如下（数值仅为格式示例，不是性能承诺）：

```text
INFO: [timing] request=<同一次请求的UUID> stage=llm_result ms=1800.0 model=agent-b mode=native_tools prompt_tokens=900 completion_tokens=70 prefill_ms=400 decode_ms=1300
INFO: [timing] request=<同一次请求的UUID> stage=tool ms=5.0 event=tool_completed model=agent-b status=completed
INFO: [timing] request=<同一次请求的UUID> stage=agent ms=6500.0 event=agent_completed model=agent-b status=completed
INFO: [timing] request=<同一次请求的UUID> stage=narrator_validation ms=0.0 status=accepted mode=compact
INFO: [timing] request=<同一次请求的UUID> stage=fusion_total ms=8000.0 status=completed
```

| 看到的字段 / 阶段 | 含义与排查方向 |
|---|---|
| `request` | 用同一个 ID 串起两 Agent 的日志，不要混算不同提问 |
| `queue` | 等待推理调度锁；不是模型生成时间 |
| `context_prepare` | 读取、整理会话上下文 |
| `tokenize` / `context_preflight` / `selection_preflight` | 请求前检查上下文预算；tokenize 已包含在 preflight 内 |
| `llm_http` / `llm_result` | 同一次模型调用的 HTTP 耗时和结果统计，不能把两者相加 |
| `prefill_ms` / `decode_ms` | B 返回的提示词处理 / token 生成耗时；缺字段表示 B 未提供，不能视作零耗时 |
| `tool` | SQL 执行、返回实体 ID 补齐、证据绑定及结果整理的工具总耗时 |
| `verification` | Truth DB 校验和当前请求类别检查 |
| `db_persist` / `event_persist` | 执行记录提交 / 事件回调持久化开销 |
| `fusion` / `build_assembly` | 确定性融合 / 核心配置组装 |
| `narrator` | 面向用户的理由生成；`narrator_validation` 的 `accepted` / `template_fallback` 是结果状态，后者说明文字校验失败后使用安全模板 |
| `agent` / `fusion_total` | 一个 Agent / 整个 FusionService 请求总耗时；并行 Agent 不能简单相加 |

先看总耗时，再看最慢 Agent 调了几次 `llm_result`，最后区分 `prefill_ms` 和 `decode_ms`。很多次短调用也会累积成很长等待；不能只盯着显存占用率。计时日志不输出 Prompt、SQL 正文或密钥。

回归时在**同一会话**依次输入：

1. `推荐一套合适打大型游戏的配置`：核心配置应包含显卡和 CPU；缺件必须明确写“部分配置”。
2. `显卡呢？显卡咋不给我推荐？`：返回显卡，不应重复 CPU；证据必须对应显卡。

当前实现、历史速度测量口径、适用范围与未完成项见 [Plan V4.5](Plan_documents/Plan_V4.5.md)。


## 8. 验证连续追问与一行工作进度

本次代码更新涉及设备 A 后端。如果你没有开启自动重载，在没有正在运行的任务时，用原后端窗口按 `Ctrl+C`，然后按本文前面的命令重新启动；前端刷新页面即可。设备 B 的两个 llama-server 不需要重启。不需要为本次更新做数据库迁移。

在同一会话、AUTO 模式中依次输入：

1. `推荐一套适合打3A游戏的配置`。
2. `能否进一步解释一下为什么这样推荐？`：应保留上一轮选中的 CPU/显卡，显示“进一步解释上次配置”，重新查询并核验证据，并补充功耗、平台匹配等理由。不能突然改成讨论本地 7B 模型或另一个显卡。
3. `能再详细说明推荐理由和证据的局限吗？`：仍应围绕同一配置。规格不能被说成游戏帧率实测。
4. `不要查询数据库，再解释一下这套配置适合什么用途`：应走 FAST 并标注未经本轮数据库核验。

执行时仅显示一行 `思考中：正在……` ，光照从文字上掠过，文字随真实阶段变化。FAST 不应显示“正在查询数据库”；完成后提示消失。系统启用减少动画时，文字保持静态可读属于正常行为。

终端中的 `narrator_validation ... template_fallback` 表示模型解释未通过内容校验、使用了可信事实模板；不等于数据库证据失败。证据解释路径不重新启动两个推荐 Agent，因此原来的融合评分不能当成本次新评分。账号与多用户会话隔离仍是 [Plan_V4.3](Plan_documents/Plan_V4.3.md) 中的后续工作。


## 9. 模型能力推荐、数据与弃用功能（2026-09-15）

- “推荐一下适合本地部署的视觉模型”会查找有明确图像输入能力证据的模型。许可证或名称不能代替视觉证据；页面标为“数据库能力参考 · 部署条件未核验”。候选不存在时解释数据缺口，并提示去 Hugging Face / ModelScope 核对官方模型卡。
- 简单能力咨询采用确定性数据库查证，不需要先让两 Agent 搜索和融合；普通硬件推荐仍保持两 Agent。具体机器的显存、运行格式、视觉投影文件、框架兼容性需要进一步确认。
- 已导入 `rigbuilder-v3-4-model-capabilities-2026-09-15`：44 个新模型、1 个已有模型的视觉证据补全；不是把全部 pending 草稿批量转成 accepted。
- 模型对比与决策台 V2 已永久弃用，前后端专用代码及旧直接 Agent 接口已移除。当前界面保留会话终端和运行统计，使用 `/api/query/jobs`。
- 本次已清空旧会话与上下文。清理前压缩备份在 `.local-logs/backups/conversation-history-20260915.json.gz`，不会被后端拿来当对话上下文。刷新页面时，已不存在的旧会话 ID 会自动回到新会话。
- 应用代码更新后，请重启设备 A 后端并刷新前端；设备 B 不需要为本次更改重启。历史 API 文档仅作历史参考。
