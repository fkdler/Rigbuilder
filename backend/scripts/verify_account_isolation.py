"""End-to-end check of the account boundary over real HTTP (Plan_V4.5 §11.4).

Runs against a *running* API process, so it verifies the deployed path -- routing,
dependency injection, SQL and status mapping -- rather than a test client. It is the
HTTP half of the acceptance list in §11.4.1: "A and B must be refused on
conversations, messages, constraints, jobs, cancel, SSE, trace and any direct-ID
path"; in-process unit tests cover the other half.

Usage (the port must be one the process under test actually listens on)::

    python backend/scripts/verify_account_isolation.py --base-url http://127.0.0.1:8099

Every account it creates is deleted again at the end, which also removes the
conversations it created (the ownership foreign keys cascade). It never submits an
inference job, so it costs no GPU time and does not touch device B.
"""

from __future__ import annotations

import argparse
import sys
from uuid import uuid4

import httpx
from sqlalchemy import text

from app.db.session import SessionLocal

PASS = "PASS"
FAIL = "FAIL"


class Report:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        self.results.append((name, bool(condition), detail))
        marker = PASS if condition else FAIL
        line = f"[{marker}] {name}"
        if detail:
            line += f" -- {detail}"
        print(line)

    def ok(self) -> bool:
        return all(item[1] for item in self.results)

    def summary(self) -> str:
        failed = [item[0] for item in self.results if not item[1]]
        if not failed:
            return f"{len(self.results)} checks, all passed"
        return f"{len(self.results)} checks, {len(failed)} failed: {', '.join(failed)}"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def register(client: httpx.Client, username: str, password: str) -> dict:
    response = client.post("/api/auth/register", json={"username": username, "password": password})
    response.raise_for_status()
    return response.json()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8099")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    report = Report()
    suffix = uuid4().hex[:10]
    user_a, user_b, user_c = f"vfy_a_{suffix}", f"vfy_b_{suffix}", f"vfy_c_{suffix}"
    created_ids: list[str] = []

    with httpx.Client(base_url=args.base_url, timeout=args.timeout) as client:
        report.check("health", client.get("/health").status_code == 200)

        # ── anonymous ────────────────────────────────────────────────────────
        anonymous = [
            ("GET", "/api/conversations"),
            ("POST", "/api/conversations"),
            ("GET", f"/api/conversations/{uuid4()}"),
            ("DELETE", f"/api/conversations/{uuid4()}"),
            ("POST", "/api/query/jobs"),
            ("GET", f"/api/query/jobs/{uuid4()}"),
            ("GET", f"/api/query/jobs/{uuid4()}/events"),
            ("GET", f"/api/query/jobs/{uuid4()}/trace"),
            ("POST", f"/api/query/jobs/{uuid4()}/cancel"),
            ("GET", "/api/auth/me"),
            ("GET", "/api/admin/summary"),
        ]
        for method, path in anonymous:
            response = client.request(method, path, json={"message": "x"} if method == "POST" else None)
            report.check(f"anonymous {method} {path} -> 401", response.status_code == 401,
                         f"got {response.status_code}")

        # ── two accounts ─────────────────────────────────────────────────────
        session_a = register(client, user_a, "pw-a")
        session_b = register(client, user_b, "pw-b")
        created_ids += [session_a["user"]["id"], session_b["user"]["id"]]
        token_a, token_b = session_a["token"], session_b["token"]

        report.check("self-registration is always an ordinary user", session_a["user"]["role"] == "user",
                     session_a["user"]["role"])
        report.check("second account is an ordinary user", session_b["user"]["role"] == "user",
                     session_b["user"]["role"])
        report.check("token is returned once with an expiry", bool(session_a["token"]) and bool(session_a["expires_at"]))
        report.check("no password material in the response", "pbkdf2" not in str(session_a))

        # The create route takes no parameters at all, so no request body can name an
        # owner; the only owner it can write is the authenticated identity.
        conversation_a = client.post("/api/conversations", headers=auth(token_a)).json()["id"]

        listed_by_a = client.get("/api/conversations", headers=auth(token_a)).json()
        report.check("A sees its own conversation",
                     [row["id"] for row in listed_by_a] == [conversation_a],
                     str([row["id"] for row in listed_by_a]))

        listed_by_b = client.get("/api/conversations", headers=auth(token_b)).json()
        report.check("B's sidebar is empty", listed_by_b == [], str(listed_by_b))

        # ── cross-account refusals: 404, never 403 (no existence leak) ───────
        cross = [
            ("GET", f"/api/conversations/{conversation_a}"),
            ("DELETE", f"/api/conversations/{conversation_a}"),
        ]
        for method, path in cross:
            response = client.request(method, path, headers=auth(token_b))
            report.check(f"B {method} A's conversation -> 404", response.status_code == 404,
                         f"got {response.status_code}")

        job_into_a = client.post("/api/query/jobs", headers=auth(token_b),
                                 json={"message": "推荐一块显卡", "conversation_id": conversation_a,
                                       "mode": "chat"})
        report.check("B cannot queue a job inside A's conversation -> 404",
                     job_into_a.status_code == 404, f"got {job_into_a.status_code}")

        chat_into_a = client.post("/api/chat", headers=auth(token_b),
                                  json={"message": "继续", "conversation_id": conversation_a})
        report.check("B cannot append a chat turn to A's conversation -> 404",
                     chat_into_a.status_code == 404, f"got {chat_into_a.status_code}")

        unknown_job = uuid4()
        for method, path in [("GET", f"/api/query/jobs/{unknown_job}"),
                             ("GET", f"/api/query/jobs/{unknown_job}/trace"),
                             ("GET", f"/api/query/jobs/{unknown_job}/events"),
                             ("POST", f"/api/query/jobs/{unknown_job}/cancel")]:
            response = client.request(method, path, headers=auth(token_a))
            report.check(f"A {method} unknown job -> 404", response.status_code == 404,
                         f"got {response.status_code}")

        # ── admin boundary ───────────────────────────────────────────────────
        report.check("ordinary user is refused the operator console",
                     client.get("/api/admin/summary", headers=auth(token_b)).status_code == 403)
        report.check("first registered user is also refused the operator console",
                     client.get("/api/admin/summary", headers=auth(token_a)).status_code == 403)
        report.check("the endpoint probe only needs an account",
                     client.get("/api/admin/endpoints", headers=auth(token_b)).status_code == 200)

        # ── credentials ──────────────────────────────────────────────────────
        renamed_name = f"{user_a}_renamed"
        report.check("rename succeeds with the authenticated session",
                     client.patch("/api/auth/me", headers=auth(token_a),
                                  json={"username": renamed_name}).status_code == 200)
        report.check("the new username logs in",
                     client.post("/api/auth/login",
                                 json={"username": renamed_name.upper(), "password": "pw-a"}).status_code == 200)

        extra = client.post("/api/auth/login", json={"username": renamed_name, "password": "pw-a"}).json()["token"]
        password_change = client.post("/api/auth/password", headers=auth(token_a),
                                      json={"current_password": "pw-a", "new_password": "pw-a2"})
        report.check("password change is accepted", password_change.status_code == 204)
        report.check("this session survives its own password change",
                     client.get("/api/auth/me", headers=auth(token_a)).status_code == 200)
        report.check("the other session is revoked immediately",
                     client.get("/api/auth/me", headers=auth(extra)).status_code == 401)
        report.check("the old password no longer works",
                     client.post("/api/auth/login",
                                 json={"username": renamed_name, "password": "pw-a"}).status_code == 401)
        report.check("the new password works",
                     client.post("/api/auth/login",
                                 json={"username": renamed_name, "password": "pw-a2"}).status_code == 200)

        report.check("duplicate username is refused case-insensitively",
                     client.post("/api/auth/register",
                                 json={"username": renamed_name.upper(), "password": "x"}).status_code == 409)

        third = register(client, user_c, "pw-c")
        created_ids.append(third["user"]["id"])
        report.check("logout revokes only the calling session",
                     client.post("/api/auth/logout", headers=auth(third["token"])).status_code == 204
                     and client.get("/api/auth/me", headers=auth(third["token"])).status_code == 401)

    # ── cleanup: deleting the accounts cascades to their conversations ─────
    with SessionLocal() as session:
        for user_id in created_ids:
            session.execute(text("DELETE FROM app_user WHERE id = :id"), {"id": user_id})
        session.commit()
        remaining = session.execute(
            text("SELECT count(*) FROM conversation WHERE owner_id = :id"), {"id": created_ids[0]},
        ).scalar()

    print("")
    print(f"cleanup: deleted {len(created_ids)} verification accounts; "
          f"A's conversations remaining = {remaining}")
    print(report.summary())
    return 0 if report.ok() else 1


if __name__ == "__main__":
    sys.exit(main())
