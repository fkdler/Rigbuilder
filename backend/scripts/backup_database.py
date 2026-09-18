"""数据库备份：pg_dump 到 .local-logs/backups/。写操作前的强制步骤。"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from app.core.config import get_settings

CANDIDATES = [
    Path(r"D:\PostgreSQL\18\bin\pg_dump.exe"),
    Path(r"C:\Program Files\PostgreSQL\18\bin\pg_dump.exe"),
    Path(r"C:\Program Files\PostgreSQL\17\bin\pg_dump.exe"),
    Path(r"C:\Program Files\PostgreSQL\16\bin\pg_dump.exe"),
]


def main() -> int:
    url = get_settings().database_url
    parsed = urlparse(url)
    dump = next((p for p in CANDIDATES if p.exists()), None)
    if dump is None:
        print("找不到 pg_dump.exe，已尝试：")
        for p in CANDIDATES:
            print("   ", p)
        return 2

    target_dir = Path(r"D:\RigBuilder\.local-logs\backups")
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = target_dir / f"rigbuilder-{stamp}.dump"

    env = dict(os.environ)
    env["PGPASSWORD"] = unquote(parsed.password or "")
    env["PGCLIENTENCODING"] = "UTF8"

    cmd = [
        str(dump), "--format=custom", "--no-owner", "--no-privileges",
        "--host", parsed.hostname or "127.0.0.1",
        "--port", str(parsed.port or 5432),
        "--username", unquote(parsed.username or ""),
        "--dbname", (parsed.path or "/").lstrip("/"),
        "--file", str(target),
    ]
    print("执行备份 ->", target)
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        print("备份失败：", proc.returncode)
        print(proc.stderr[:1500])
        return 1
    size = target.stat().st_size
    print(f"备份完成：{size/1024/1024:.2f} MB")
    print("还原命令示例：")
    print(f'  "{dump.parent / "pg_restore.exe"}" --clean --if-exists -d <db> "{target}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
