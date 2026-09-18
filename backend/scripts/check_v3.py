"""Post-cutover structural, data, view and readonly-role checks."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.models.truth_v3 import TRUTH_TABLES
from app.tools.tables import ALLOWED_TABLES

BACKEND_DIR = Path(__file__).resolve().parents[1]


def expected_revisions() -> set[str]:
    """Alembic head revision(s) the database must be at.

    Structural acceptance follows the migration chain instead of hard-coding a
    revision, so adding a migration cannot silently make this check fail.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return set(ScriptDirectory.from_config(config).get_heads())


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--strict", action="store_true"); parser.add_argument("--report", type=Path); args = parser.parse_args()
    report = {"ok": False, "checks": {}, "errors": [], "warnings": []}; url = os.environ.get("DATABASE_URL"); settings = None
    try:
        from app.core.config import get_settings
        settings = get_settings()
    except Exception:
        pass
    if not url:
        if settings: url = settings.database_url
        else: report["errors"].append("DATABASE_URL is not configured")
    if url:
        try:
            from sqlalchemy import create_engine, text
            with create_engine(url).connect() as c:
                revision = c.execute(text("SELECT version_num FROM public.alembic_version")).scalar_one(); report["checks"]["revision"] = revision
                heads = expected_revisions(); report["checks"]["expected_revisions"] = sorted(heads)
                if revision not in heads: report["errors"].append(f"database revision {revision} is not at head {sorted(heads)}")
                tables = set(c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='truth'")).scalars())
                expected_tables = {t.name for t in TRUTH_TABLES}; report["checks"]["truth_tables"] = sorted(tables)
                if tables != expected_tables: report["errors"].append(f"Truth table mismatch missing={sorted(expected_tables-tables)} extra={sorted(tables-expected_tables)}")
                views = set(c.execute(text("SELECT viewname FROM pg_views WHERE schemaname='agent_catalog'")).scalars()); report["checks"]["agent_views"] = sorted(views)
                if views != set(ALLOWED_TABLES): report["errors"].append(f"Agent view mismatch missing={sorted(set(ALLOWED_TABLES)-views)} extra={sorted(views-set(ALLOWED_TABLES))}")
                legacy = [name for name in ("hardware","ai_model","model_variant","evidence","benchmark") if c.execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": f"public.{name}"}).scalar_one()]
                if legacy: report["errors"].append(f"legacy tables remain: {legacy}")
                protected = ("conversation", "user_constraint", "conversation_message", "conversation_context_snapshot", "agent_run", "agent_message", "tool_call", "query_job", "query_event", "alembic_version")
                missing_protected = [name for name in protected if not c.execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": f"public.{name}"}).scalar_one()]
                if missing_protected: report["errors"].append(f"protected application/audit tables missing: {missing_protected}")
                invalid_constraints = c.execute(text("""SELECT conname FROM pg_constraint co JOIN pg_namespace n ON n.oid=co.connamespace
                    WHERE n.nspname='truth' AND NOT co.convalidated""")).scalars().all()
                if invalid_constraints: report["errors"].append(f"unvalidated Truth constraints: {invalid_constraints}")
                for view in sorted(ALLOWED_TABLES):
                    c.execute(text(f'SELECT * FROM agent_catalog."{view}" LIMIT 0'))
                releases = c.execute(text("SELECT count(*) FROM truth.dataset_release")).scalar_one(); report["checks"]["release_count"] = releases
                if releases == 0: (report["errors"] if args.strict else report["warnings"]).append("no accepted dataset release imported")
                orphan_sql = """SELECT count(*) FROM truth.evidence_claim e LEFT JOIN truth.catalog_entity c ON c.id=e.entity_id LEFT JOIN truth.source_document s ON s.id=e.source_id WHERE c.id IS NULL OR s.id IS NULL"""
                if c.execute(text(orphan_sql)).scalar_one(): report["errors"].append("orphan Evidence claims detected")
                uncovered = c.execute(text("""SELECT count(*) FROM truth.catalog_entity e WHERE e.recommendable
                    AND NOT EXISTS (SELECT 1 FROM truth.evidence_claim c WHERE c.entity_id=e.id AND c.review_status='accepted')""")).scalar_one()
                recommendable_count = c.execute(text("SELECT count(*) FROM truth.catalog_entity WHERE recommendable")).scalar_one()
                report["checks"]["recommendable_entity_count"] = recommendable_count
                if recommendable_count == 0: (report["errors"] if args.strict else report["warnings"]).append("no recommendable V3.2 entity was imported")
                report["checks"]["recommendable_entities_without_evidence"] = uncovered
                if uncovered: (report["errors"] if args.strict else report["warnings"]).append(f"{uncovered} recommendable entities have no accepted Evidence")
                from sqlalchemy.engine import make_url
                readonly_url = settings.agent_readonly_database_url if settings else os.environ.get("AGENT_READONLY_DATABASE_URL")
                role_name = (make_url(readonly_url).username if readonly_url else None) or "agent_readonly"
                role_exists = c.execute(text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=:role)"), {"role": role_name}).scalar_one(); report["checks"]["agent_readonly_exists"] = role_exists
                if not role_exists: (report["errors"] if args.strict else report["warnings"]).append("agent_readonly role does not exist")
                else:
                    memberships = c.execute(text("""SELECT parent.rolname FROM pg_auth_members m
                        JOIN pg_roles child ON child.oid=m.member JOIN pg_roles parent ON parent.oid=m.roleid
                        WHERE child.rolname=:role"""), {"role": role_name}).scalars().all()
                    if memberships: report["errors"].append(f"agent readonly role has forbidden memberships: {memberships}")
                    if c.execute(text("SELECT has_schema_privilege(:role,'truth','USAGE')"), {"role": role_name}).scalar_one(): report["errors"].append("agent_readonly has forbidden truth schema USAGE")
                    truth_privileges = c.execute(text("""SELECT tablename FROM pg_tables WHERE schemaname='truth' AND (
                        has_table_privilege(:role,format('%I.%I',schemaname,tablename),'SELECT') OR
                        has_table_privilege(:role,format('%I.%I',schemaname,tablename),'INSERT') OR
                        has_table_privilege(:role,format('%I.%I',schemaname,tablename),'UPDATE') OR
                        has_table_privilege(:role,format('%I.%I',schemaname,tablename),'DELETE') OR
                        has_table_privilege(:role,format('%I.%I',schemaname,tablename),'TRUNCATE'))"""), {"role": role_name}).scalars().all()
                    if truth_privileges: report["errors"].append(f"agent_readonly has direct Truth table privileges: {truth_privileges}")
                    public_select = c.execute(text("""SELECT tablename FROM pg_tables WHERE schemaname='public'
                        AND has_table_privilege(:role,format('%I.%I',schemaname,tablename),'SELECT')"""), {"role": role_name}).scalars().all()
                    if public_select: report["errors"].append(f"agent_readonly can SELECT public tables: {public_select}")
                    missing_view_grants = c.execute(text("""SELECT viewname FROM pg_views WHERE schemaname='agent_catalog'
                        AND NOT has_table_privilege(:role,format('%I.%I',schemaname,viewname),'SELECT')"""), {"role": role_name}).scalars().all()
                    if missing_view_grants: report["errors"].append(f"agent_readonly lacks view grants: {missing_view_grants}")
                    writable_views = c.execute(text("""SELECT viewname FROM pg_views WHERE schemaname='agent_catalog' AND (
                        has_table_privilege(:role,format('%I.%I',schemaname,viewname),'INSERT') OR
                        has_table_privilege(:role,format('%I.%I',schemaname,viewname),'UPDATE') OR
                        has_table_privilege(:role,format('%I.%I',schemaname,viewname),'DELETE'))"""), {"role": role_name}).scalars().all()
                    if writable_views: report["errors"].append(f"agent_readonly has write privileges on views: {writable_views}")
                if not readonly_url:
                    (report["errors"] if args.strict else report["warnings"]).append("AGENT_READONLY_DATABASE_URL is not configured for login smoke test")
                else:
                    with create_engine(readonly_url, connect_args={"options": "-c search_path=agent_catalog,public -c default_transaction_read_only=on"}).connect() as readonly:
                        report["checks"]["readonly_transaction"] = readonly.execute(text("SHOW transaction_read_only")).scalar_one()
                        report["checks"]["readonly_search_path"] = readonly.execute(text("SHOW search_path")).scalar_one()
                        readonly.execute(text("SELECT entity_key,name FROM gpu_catalog ORDER BY entity_key LIMIT 1")).all()
                        readonly.execute(text("SELECT entity_key,name FROM model_catalog ORDER BY entity_key LIMIT 1")).all()
        except Exception as exc: report["errors"].append(str(exc))
    report["ok"] = not report["errors"]
    rendered = json.dumps(report, ensure_ascii=False, indent=2, default=str); print(rendered)
    if args.report: args.report.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__": raise SystemExit(main())
