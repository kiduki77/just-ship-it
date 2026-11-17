"""Replay finished projects against the analysis-run limits, read-only: what each spent and when, and what the same
timeline costs under `services/rounds.py`, the plan's build ask and the per-round card batch.

    uv run python scripts/analysis_replay.py --env-file <repo>/.env 1 2 3

It is a cost model over the measured timeline, not a rerun: a task the round's envelope or its task cap leaves out is
taken as not run (its spend and its model time saved), the development ask moves to when the round-1 product tasks
finished (the build's spec inputs), and the cards a round raised are counted as one drop at its end. The session is
read-only (`default_transaction_read_only`), so it is safe against the live database.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from justshipit.db.models import AgentRequest, Artifact, Evidence, LLMCallRecord, Project, Task
from justshipit.services import budget, evidence, rounds
from justshipit.settings import Settings, limits, settings

DEV_PURPOSES = budget.DEV_PURPOSES


def _minutes(a: datetime | None, b: datetime | None) -> float | None:
    return round((b - a).total_seconds() / 60) if a and b else None


def replay(s: Session, p: Project, registry: Path) -> dict:
    ts = list(s.scalars(select(Task).where(Task.project_id == p.id).order_by(Task.started_at.nulls_last(), Task.id)))
    calls = list(s.scalars(select(LLMCallRecord).where(LLMCallRecord.project_id == p.id)))
    reqs = list(s.scalars(select(AgentRequest).where(AgentRequest.project_id == p.id).order_by(AgentRequest.id)))
    start = min([p.created_at] + [t.started_at for t in ts if t.started_at])
    dev_spent = sum(c.cost_usd for c in calls if c.purpose in DEV_PURPOSES or c.dev_job_id)
    asks = [r for r in reqs if r.type == "DEVELOPMENT" and r.action_kind == "dev.implementation_start"]
    first_ask = min((r.created_at for r in asks), default=None)
    builds = bool(asks)

    def spent_before(at: datetime | None) -> float | None:
        return round(sum(c.cost_usd for c in calls if c.created_at < at), 2) if at else None

    # Round envelope and task cap, applied in the order tasks started.
    analysis = max(0.0, p.budget_usd - (budget.dev_need_usd() if builds else 0.0))
    limit = float(limits()["round_budget_share"]) * analysis
    cap = rounds.max_tasks()
    kept, cut = [], []
    by_round: dict[int, dict] = {}
    # Which planned tasks the CEO would drop to fit the cap is its call; the model keeps the product tasks (the
    # build's spec inputs, which `rounds.admit` also runs first) and drops the latest-started others.
    for t in sorted(ts, key=lambda t: (t.owner != "product", t.started_at or datetime.max.replace(tzinfo=start.tzinfo),
                                       t.id)):
        r = by_round.setdefault(t.plan_round, {"spent": 0.0, "planned": 0})
        planned = t.origin in rounds.PLANNED
        over_cap = planned and r["planned"] >= cap
        over_env = t.origin != rounds.OWNER and r["spent"] >= limit   # one task at a time: Envelope.room(0)
        if over_cap or over_env:
            cut.append((t, "cap" if over_cap else "envelope"))
            continue
        kept.append(t)
        r["spent"] += t.spent_usd
        r["planned"] += planned
    saved_usd = sum(t.spent_usd for t, _ in cut)
    saved_min = sum(_minutes(t.started_at, t.finished_at) or 0 for t, _ in cut)

    # The build ask once round 1's product work is done: the spec's inputs.
    r1_product = [t for t in ts if t.plan_round == 1 and t.origin == "ceo_plan" and t.owner == "product"
                  and t.finished_at]
    new_ask = max((t.finished_at for t in r1_product), default=None) if builds else None

    # Owner drops: the moments task cards reached the owner. After: a blocking card still goes up at once, every
    # other card with its round's batch. The cards themselves are counted as they were (the cut is the CEO's call).
    owner_cards = [r for r in reqs if r.task_id is not None]
    drops_before = len({r.created_at.replace(second=0, microsecond=0) for r in owner_cards})
    task_round = {t.id: t.plan_round for t in ts}
    drops_after = len({("blocking", r.id) if r.blocking else ("round", task_round.get(r.task_id))
                       for r in owner_cards})

    report = s.get(Artifact, (p.context or {}).get("report_artifact_id") or 0)
    body = (registry / report.path).read_text(encoding="utf-8") if report and (registry / report.path).exists() else ""
    head, _, ledger = body.partition("\n---\n## 부록: 근거 원장")
    mix = evidence.mix(s, p.id)
    return {
        "project": f"P-{p.id} {p.name}", "budget": p.budget_usd, "spent": round(p.spent_usd, 2),
        "analysis_spent": round(p.spent_usd - dev_spent, 2), "run_min": _minutes(start, max(
            [t.finished_at for t in ts if t.finished_at], default=None)),
        "tasks": len(ts), "first_dev_ask_min": _minutes(start, first_ask), "spent_before_dev_ask": spent_before(first_ask),
        "after_tasks": len(kept), "after_cut": [(f"T-{t.id}", why, round(t.spent_usd, 2)) for t, why in cut],
        "after_saved_usd": round(saved_usd, 2), "after_saved_task_min": round(saved_min),
        "after_dev_ask_min": _minutes(start, new_ask), "after_spent_before_dev_ask": spent_before(new_ask),
        "after_analysis_max_usd": round(min(p.spent_usd - dev_spent - saved_usd, analysis), 2),
        "after_dev_kept_usd": budget.dev_need_usd() if builds else 0.0,
        "owner_cards": len(owner_cards), "owner_drops": drops_before, "after_owner_drops": drops_after,
        "report_bytes": len(body.encode()), "report_ledger_share": round(len(ledger) / len(body), 2) if body else None,
        "after_report_bytes": len(head.encode()),
        "evidence": mix.line(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", required=True)
    ap.add_argument("projects", nargs="+", type=int)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    url = Settings(_env_file=args.env_file).database_url
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _read_only(conn, _):
        with conn.cursor() as cur:
            cur.execute("SET default_transaction_read_only = on")

    registry = Path(args.env_file).resolve().parent / "registry"
    with Session(engine) as s:
        for pid in args.projects:
            for k, v in replay(s, s.get(Project, pid), registry if registry.exists() else settings().registry_dir).items():
                print(f"{k:28} {v}")
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
