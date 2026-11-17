"""Run one real meeting on a [TEST] copy of a project and print a quality report.

The source project is only read: its plan, finished task results and evidence are copied into a new
[TEST] project (EV-ids remapped), and the meeting runs there. plan_round is set to the limit so the
meeting's decision cannot spawn follow-up tasks. Uses subscription calls (~9 model calls).

    uv run python scripts/meeting_quality.py --source 1 --agenda "..." --participants strategy,risk,finance,research
"""
from __future__ import annotations

import argparse
import re
from typing import Any

from justshipit.db.models import Decision, Evidence, Meeting, Project, Task
from justshipit.db.session import session_scope
from justshipit.orchestration import company_graph
from justshipit.services import evidence as ev
from justshipit.services import projects
from justshipit.settings import limits
from sqlalchemy import select

_EV = re.compile(r"EV-?(\d+)", re.IGNORECASE)


def remap(obj: Any, ids: dict[int, int], key: str = "") -> Any:
    if isinstance(obj, str):
        return _EV.sub(lambda m: f"EV-{ids.get(int(m.group(1)), int(m.group(1)))}", obj)
    if isinstance(obj, list):
        if key == "evidence_ids":
            return [ids.get(x, x) if isinstance(x, int) else remap(x, ids) for x in obj]
        return [remap(x, ids, key) for x in obj]
    if isinstance(obj, dict):
        return {k: remap(v, ids, k) for k, v in obj.items()}
    return obj


def clone(source_id: int) -> int:
    with session_scope() as s:
        src = s.get(Project, source_id)
        p = projects.create(s, src.goal, name=f"[TEST] 회의 품질 검증 (P-{src.id} 사본)", budget_usd=20.0,
                            start=False)
        p.plan = src.plan
        p.context = {"owner_answers": (src.context or {}).get("owner_answers", [])}
        p.plan_round = int(limits()["max_plan_rounds"])
        p.status, p.phase = "DONE", "meeting_test"
        s.flush()
        ids: dict[int, int] = {}
        tids: dict[int, int] = {}
        olds = list(s.scalars(select(Evidence).where(Evidence.project_id == src.id).order_by(Evidence.id)))
        for e in olds:
            n = Evidence(project_id=p.id, agent=e.agent, claim=e.claim, kind=e.kind, source_url=e.source_url,
                         source_title=e.source_title, quote=e.quote, published_at=e.published_at,
                         confidence=e.confidence, verification_status=e.verification_status,
                         verification_note=e.verification_note)
            s.add(n)
            s.flush()
            ids[e.id] = n.id
        for t in s.scalars(select(Task).where(Task.project_id == src.id, Task.result.isnot(None)).order_by(Task.id)):
            n = Task(project_id=p.id, key=t.key, title=t.title, owner=t.owner, objective=t.objective,
                     acceptance_criteria=t.acceptance_criteria, status=t.status, origin=t.origin,
                     plan_round=t.plan_round, iteration=t.iteration, max_iterations=t.max_iterations,
                     result=remap(t.result, ids), review=remap(t.review, ids), contested=t.contested)
            s.add(n)
            s.flush()
            tids[t.id] = n.id
        for e in olds:
            n = s.get(Evidence, ids[e.id])
            n.claim = remap(e.claim, ids)
            n.task_id = tids.get(e.task_id) if e.task_id else None
            n.superseded_by = ids.get(e.superseded_by) if e.superseded_by else None
        for old, new in tids.items():
            t = s.get(Task, new)
            t.depends_on = [tids.get(x, x) for x in (s.get(Task, old).depends_on or [])]
        return p.id


def report(pid: int) -> None:
    with session_scope() as s:
        m = s.scalars(select(Meeting).where(Meeting.project_id == pid).order_by(Meeting.id.desc())).first()
        print(f"\n===== M-{m.id} participants {m.participants}\nAGENDA: {m.agenda}")
        first = {x["agent"]: x["stance"] for x in m.transcript if x["round"] == 0}
        cited, bad = 0, []
        for x in m.transcript:
            tags = []
            for i in x.get("evidence_ids", []):
                n = ev.parse_ev(i)
                e = s.get(Evidence, n) if n else None
                cited += 1
                if e is None or e.project_id != pid:
                    bad.append(f"{x['agent']}:{i}")
                    tags.append(f"{i}:INVALID")
                else:
                    tags.append(f"{i}:{e.kind}/{e.verification_status}")
            others = [a for a in m.participants if a != x["agent"] and a in x["statement"]]
            moved = f" (r0 {first.get(x['agent'])} -> {x['stance']})" if x["round"] and first.get(x["agent"]) != x["stance"] else ""
            print(f"\n-- r{x['round']} {x['agent']} [{x['stance']}]{moved} | mentions {others}\n{x['statement']}")
            print("   EV:", tags)
            print("   concerns:", x.get("concerns", []))
        d = s.get(Decision, m.decision_id)
        print(f"\n## DECISION D-{d.id} {d.decision}: {d.title}\n{d.rationale}\n"
              f"supporting={d.supporting_evidence_ids} dissenting={d.dissenting_evidence_ids}\n"
              f"dissent: {d.dissent_summary}\nopen questions: {d.open_questions}")
        print(f"\ncited {cited}, invalid {len(bad)} {bad}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=int, required=True)
    ap.add_argument("--agenda", required=True)
    ap.add_argument("--participants", default="strategy,risk,finance,research")
    ap.add_argument("--report-only", type=int, help="print the report for an existing [TEST] project id")
    a = ap.parse_args()
    if a.report_only:
        return report(a.report_only)
    pid = clone(a.source)
    with session_scope() as s:
        p = s.get(Project, pid)
        assert p.name.startswith("[TEST]")
        p.context = {**p.context, "pending_meeting": {"agenda": a.agenda, "participants": a.participants.split(",")}}
    print(f"cloned P-{a.source} -> [TEST] P-{pid}; running meeting", flush=True)
    company_graph.meeting({"project_id": pid})
    report(pid)


if __name__ == "__main__":
    main()
