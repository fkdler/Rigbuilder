# RigBuilder Backend

> Truth DB V3 migration is a confirmed remote cutover, not a normal startup step. It requires a verified full backup and an accepted Release. See `docs/Data_documents/Truth_DB_V3_Remote_Cutover.md`; plain `alembic upgrade head` is deliberately refused by revision 0005.

FastAPI Application Server，负责数据库、LLM Gateway、Agent 调度、Truth Verification 与 `fusion-v1` 确定性融合。

## 本地开发

从仓库根目录安装依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

复制并填写配置：

```powershell
Copy-Item backend\.env.example backend\.env
```

在本目录运行：

```powershell
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

配置模板中的 `TODO(CONFIG)` 是部署前必须确认的部分；代码中的 `TODO(V1.1-IMPLEMENT)` 是已经预留边界、尚未实现的阶段功能。

当前 GPU 串行锁位于单个 FastAPI 进程内。在模型管理器或跨进程锁实现前，开发环境只能启动一个 Uvicorn worker，否则多个 worker 仍可能同时请求同一张 GPU。

运行后端测试：

```powershell
..\.venv\Scripts\python.exe -m pytest tests -q
```

远端 V3 切换并导入正式 Release 后，可设置 `RUN_POSTGRES_INTEGRATION=1` 运行 Truth Repository 的 PostgreSQL 冒烟测试。
