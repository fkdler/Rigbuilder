"""Read-only remote cutover preflight with a machine-readable report."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

LEGACY_TABLES = ("hardware", "ai_model", "model_variant", "evidence", "benchmark")
EXPECTED_REVISION = "20260831_0004"


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--report", type=Path); args = parser.parse_args()
    report = {"ok": False, "checks": {}, "errors": []}
    url = os.environ.get("DATABASE_URL")
    if not url:
        try:
            from app.core.config import get_settings
            url = get_settings().database_url
        except Exception: report["errors"].append("DATABASE_URL is not configured")
    if url:
        try:
            from sqlalchemy import create_engine, text
            with create_engine(url).connect() as connection:
                version = connection.execute(text("SHOW server_version_num")).scalar_one()
                report["checks"]["postgres_version_num"] = int(version)
                if int(version) < 140000: report["errors"].append("PostgreSQL 14 or newer is required")
                revision = connection.execute(text("SELECT version_num FROM public.alembic_version")).scalar_one()
                report["checks"]["alembic_revision"] = revision
                if revision != EXPECTED_REVISION: report["errors"].append(f"expected Alembic {EXPECTED_REVISION}, got {revision}")
                schemas = connection.execute(text("SELECT nspname FROM pg_namespace WHERE nspname IN ('truth','agent_catalog')")).scalars().all()
                report["checks"]["schema_conflicts"] = schemas
                if schemas: report["errors"].append(f"V3 schemas already exist: {schemas}")
                counts = {}
                for table in LEGACY_TABLES:
                    exists = connection.execute(text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"public.{table}"}).scalar_one()
                    if not exists: report["errors"].append(f"legacy table missing: public.{table}")
                    else: counts[table] = connection.execute(text(f'SELECT count(*) FROM public."{table}"')).scalar_one()
                report["checks"]["legacy_counts"] = counts
                can_create_schema = connection.execute(text("SELECT has_database_privilege(current_user,current_database(),'CREATE')")).scalar_one()
                can_create_role = connection.execute(text("SELECT rolcreaterole OR rolsuper FROM pg_roles WHERE rolname=current_user")).scalar_one()
                owned = connection.execute(text("""SELECT c.relname, pg_has_role(current_user,c.relowner,'MEMBER')
                    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='public' AND c.relname IN ('hardware','ai_model','model_variant','evidence','benchmark')""")).all()
                report["checks"]["can_create_schema"] = can_create_schema
                report["checks"]["can_create_role"] = can_create_role
                report["checks"]["owns_legacy_tables"] = {row[0]: row[1] for row in owned}
                if not can_create_schema: report["errors"].append("migration account cannot CREATE schemas")
                if not can_create_role: report["errors"].append("migration account lacks CREATEROLE for agent_readonly")
                if any(not row[1] for row in owned): report["errors"].append("migration account does not own every legacy table")
        except Exception as exc: report["errors"].append(str(exc))
    report["ok"] = not report["errors"]
    rendered = json.dumps(report, ensure_ascii=False, indent=2, default=str); print(rendered)
    if args.report: args.report.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__": raise SystemExit(main())
