# Truth DB V3 远端重建操作手册

本手册是远端设备的唯一操作入口。它只替换旧 Truth 五表，保留 `public` 中的会话、约束、Agent 日志与 `alembic_version`。失败恢复依赖完整 `pg_dump`，不要执行 Alembic downgrade。

## 1. 前置条件

代码 Commit、V3 Release 和待部署应用必须来自同一次交付。把 `backend/.env.example` 复制为 `backend/.env`，填写迁移账号 `DATABASE_URL` 和独立只读账号 `AGENT_READONLY_DATABASE_URL`。迁移账号须能创建 Schema/View、删除旧五表和创建/配置只读角色。

在仓库根目录执行：

```powershell
Copy-Item backend\.env.example backend\.env
# 编辑 backend\.env 后，先备份；示例路径必须替换
pg_dump --format=custom --dbname "postgresql://迁移账号:密码@主机:5432/rigbuilder" --file D:\backups\rigbuilder-before-v3.dump
$BackupHash = (Get-FileHash D:\backups\rigbuilder-before-v3.dump -Algorithm SHA256).Hash.ToLowerInvariant()
$BackupHash
```

把备份文件路径、哈希、Git Commit 和 Release Manifest 路径记录到变更单。建议先在临时数据库验证该 dump 能恢复。

## 2. 一键切换

```powershell
powershell -ExecutionPolicy Bypass -File backend\scripts\remote_cutover_v3.ps1 `
  -Release D:\releases\rigbuilder-v3-2026-09-02\manifest.json `
  -BackupFile D:\backups\rigbuilder-before-v3.dump `
  -BackupSha256 $BackupHash `
  -ReportDirectory D:\reports\rigbuilder-v3-cutover
```

入口会依次执行 Release 离线校验、只读预检、带确认参数的原子 Alembic 升级、Importer dry-run、apply、只读角色配置、严格验收和第二次 dry-run。任一步非零即停止。严格验收要求至少一个带 accepted Evidence 的可推荐实体；第二次 dry-run 的业务实体应为 `skipped`，不会删除 Manifest 未列出的实体。

## 3. 手工等价命令

在 `backend` 目录：

```powershell
..\.venv\Scripts\python.exe -m scripts.import_v3 --release D:\release\manifest.json --validate-only --report release-validation.json
..\.venv\Scripts\python.exe -m scripts.preflight_v3 --report preflight.json
..\.venv\Scripts\alembic.exe -x allow_truth_rebuild=true upgrade head
..\.venv\Scripts\python.exe -m scripts.import_v3 --release D:\release\manifest.json --dry-run --report dry-run.json
..\.venv\Scripts\python.exe -m scripts.import_v3 --release D:\release\manifest.json --apply --report apply.json
..\.venv\Scripts\python.exe -m scripts.create_agent_readonly --apply
..\.venv\Scripts\python.exe -m scripts.check_v3 --strict --report check.json
```

迁移后若需要清空 V3 数据但保留 Schema/View/Alembic 历史：

```powershell
..\.venv\Scripts\python.exe -m scripts.reset_truth_data --dry-run
..\.venv\Scripts\python.exe -m scripts.reset_truth_data --apply --confirm RESET_TRUTH_V3
```

## 4. 失败与恢复

Alembic Revision 在 PostgreSQL 单事务中执行，迁移过程中失败会整体回滚。若迁移提交后导入或验收失败，停止应用，保留报告，并从已经验证的 dump 恢复整个数据库。不要用 `downgrade` 重建旧数据，也不要单独执行 DROP/TRUNCATE。

本机无数据库时仍可运行 Schema 生成、旧数据转换和正式 Release 离线校验：

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe backend\scripts\generate_v3_schema.py
.\.venv\Scripts\python.exe backend\scripts\convert_v2_to_v3.py
.\.venv\Scripts\python.exe backend\scripts\import_v3.py --release D:\release\manifest.json --validate-only
```
