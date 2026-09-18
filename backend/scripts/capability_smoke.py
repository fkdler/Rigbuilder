"""Standalone capability smoke test for the two resident endpoints.

Run from the backend directory with the project virtualenv, e.g.:

    ..\\.venv\\Scripts\\python.exe scripts\\capability_smoke.py

It probes agent-a/b endpoints and runs Tests 1-4 against every reachable one.
Exit code:
  0  all reachable profiles are READY
  1  at least one profile is TOOL_PROTOCOL_UNSUPPORTED
  2  all profiles unreachable / no weights installed (explicit "unverified")
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


async def main() -> int:
    from app.agents.capabilities import smoke_profile
    from app.core.config import get_settings
    from app.inference.profiles import build_profiles

    settings = get_settings()
    profiles = build_profiles(settings)
    if not profiles:
        print(json.dumps({"error": "no profiles configured"}, ensure_ascii=False))
        return 2
    results = await asyncio.gather(*(smoke_profile(profile, settings) for profile in profiles))
    report = {}
    for profile, (state, detail) in zip(profiles, results):
        report[profile.id] = {"state": state, "endpoint": profile.endpoint_url, "detail": detail}
        print(json.dumps(report[profile.id], ensure_ascii=False))
    states = [state for _, (state, _) in zip(profiles, results)]
    if any(state == "tool_protocol_unsupported" for state in states):
        return 1
    if any(state == "skipped" for state in states) or not all(state == "ready" for state in states):
        print("capability smoke: endpoints unreachable or weights not installed - unverified", flush=True)
        return 2
    print("capability smoke: all profiles READY", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
