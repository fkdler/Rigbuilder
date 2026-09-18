"""Replay policy checks and optionally run a real two-turn recommendation job.

Run from backend: python scripts/eval_office_refactor.py [--live]
Live mode writes a new test conversation; it does not mutate Truth data.
"""
import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.requirements import office_utility
from app.services.routing import classify_request, requested_component_roles

QUESTION = "给我推荐一套适合普通办公的电脑吧"
FOLLOWUP = "不对啊，你这个推荐不是完整配置啊"


async def live():
    from app.services.jobs import QueryJobManager
    from app.schemas.query import QueryJobRequest
    manager = QueryJobManager()
    conversation, records = None, []
    for message in (QUESTION, FOLLOWUP):
        start = time.perf_counter()
        job = manager.submit(QueryJobRequest(message=message, conversation_id=conversation))
        await asyncio.wait_for(manager._tasks[job.id], timeout=120)
        result = manager.get(job.id)
        conversation = result.conversation_id
        payload = result.result or {}
        build = (payload.get("presentation") or {}).get("core_build") or {}
        roles = {c["role"] for c in build.get("core", [])} | {c["role"] for c in build.get("supporting", [])}
        records.append({"question": message, "elapsed_seconds": round(time.perf_counter()-start, 3),
            "completed": result.status == "completed", "kind": payload.get("kind"),
            "component_slot_coverage": len(roles & {"cpu", "display", "platform", "memory", "storage", "psu", "cooler", "case"}) / 8,
            "purchase_readiness_verified": False,
            "job": result.model_dump(mode="json")})
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = {"policy_version": "office-fit-v1", "scope": classify_request(QUESTION),
        "roles": sorted(requested_component_roles(QUESTION)),
        "modest_cpu_score": office_utility({"cpu.base_power_w":65,"cpu.cores_total":6}),
        "high_power_cpu_score": office_utility({"cpu.base_power_w":125,"cpu.cores_total":24}),
        "note": "Policy utility is not measured performance. Slot coverage is not verified build compatibility."}
    if args.live:
        result["live"] = asyncio.run(live())
    output = args.output or ROOT / ".local-logs" / ("office-eval-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(output)
    if args.live and any(not r["completed"] or r["component_slot_coverage"] != 1 for r in result["live"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
