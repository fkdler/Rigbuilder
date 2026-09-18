r"""Create the agent_readonly PostgreSQL role (Plan V2.2 §3.5).

Usage (from the backend/ directory):

    ..\.venv\Scripts\python.exe -m scripts.create_agent_readonly --dry-run
    ..\.venv\Scripts\python.exe -m scripts.create_agent_readonly --apply

The script prints the DDL by default. Pass --apply to execute it against the
configured DATABASE_URL (the main account must have CREATEROLE privilege).
"""

from __future__ import annotations

import argparse
import re
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import get_settings
SQL_QUOTE = chr(39)


def _quote(value: str | None) -> str:
    if value is None:
        return ""
    return value.replace(SQL_QUOTE, SQL_QUOTE * 2)


def build_statements(settings) -> list[str]:
    readonly_url = settings.agent_readonly_database_url
    if not readonly_url:
        raise SystemExit(
            "Set AGENT_READONLY_DATABASE_URL in backend/.env before creating the role."
        )
    url = make_url(readonly_url)
    role_name = url.username or "agent_readonly"
    password = url.password or "change_me"
    database = url.database or "rigbuilder"
    identifier = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    if not identifier.fullmatch(role_name) or not identifier.fullmatch(database):
        raise SystemExit("Readonly role and database names must be plain PostgreSQL identifiers.")
    migration_role = make_url(settings.database_url).username
    if not migration_role or not identifier.fullmatch(migration_role):
        raise SystemExit("Migration role must be a plain PostgreSQL identifier.")
    if role_name in {"postgres", migration_role}:
        raise SystemExit("Readonly role must be distinct from postgres and the migration/application role.")

    return [
        "DO $do$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
        f"{SQL_QUOTE}{_quote(role_name)}{SQL_QUOTE}) THEN CREATE ROLE {role_name} LOGIN; END IF; END $do$;",
        "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
        f"{SQL_QUOTE}{_quote(role_name)}{SQL_QUOTE} AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)) "
        f"THEN RAISE EXCEPTION {SQL_QUOTE}readonly role has privileged attributes{SQL_QUOTE}; END IF; END $do$;",
        # PostgreSQL 18 requires the caller to possess each privileged
        # attribute even when setting its NO* form. The guard above verifies
        # the safe defaults, so only ordinary login properties are altered.
        f"ALTER ROLE {role_name} LOGIN NOINHERIT PASSWORD {SQL_QUOTE}{_quote(password)}{SQL_QUOTE};",
        # PostgreSQL 16+ grants a creator membership with ADMIN OPTION. It is
        # unnecessary after provisioning and weakens the role separation.
        f"REVOKE {role_name} FROM {migration_role};",
        f"GRANT CONNECT ON DATABASE {database} TO {role_name};",
        f"REVOKE ALL ON SCHEMA truth FROM {role_name};",
        f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA truth FROM {role_name};",
        f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {role_name};",
        f"GRANT USAGE ON SCHEMA agent_catalog TO {role_name};",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA agent_catalog TO {role_name};",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {migration_role} IN SCHEMA agent_catalog GRANT SELECT ON TABLES TO {role_name};",
        f"ALTER ROLE {role_name} SET search_path = agent_catalog, public;",
        f"ALTER ROLE {role_name} SET default_transaction_read_only = on;",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the agent_readonly PostgreSQL role.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true", help="execute the DDL")
    group.add_argument("--dry-run", action="store_true", help="print the DDL without executing (default)")
    parser.add_argument("--refresh-grants", action="store_true", help="refresh catalogue SELECT grants without changing credentials")
    args = parser.parse_args(argv)

    settings = get_settings()
    statements = build_statements(settings)
    if args.refresh_grants:
        statements = [statement for statement in statements if statement.startswith(("GRANT USAGE ON SCHEMA agent_catalog", "GRANT SELECT ON ALL TABLES IN SCHEMA agent_catalog", "ALTER DEFAULT PRIVILEGES"))]

    if not args.apply:
        print("-- Dry run: run again with --apply to execute --")
        for statement in statements:
            print(re.sub(r"PASSWORD\s+'(?:''|[^'])*'", "PASSWORD '<redacted>'", statement, flags=re.IGNORECASE))
        return 0

    engine = create_engine(settings.database_url)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            for statement in statements:
                connection.execute(text(statement))
    except Exception as exc:  # noqa: BLE001 - operator-facing diagnostic
        print(f"Role creation failed: {exc}", file=sys.stderr)
        print("Verify that the DATABASE_URL account has CREATEROLE and the role does not conflict.", file=sys.stderr)
        return 1

    print("agent_readonly is restricted to SELECT on agent_catalog views.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
