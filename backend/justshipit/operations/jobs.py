"""Recurring and follow-up operations jobs: change watch, KPI review, portfolio review, reassessment."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from justshipit.agents.executor import last_call_id, run_agent
from justshipit.contracts.operations import KpiReviewOut, PortfolioReviewOut, WatchOut
from justshipit.contracts.origins import OriginKind, ServerOrigin
from justshipit.contracts.requests import AgentRequestDraft, RequestStatus, RequestType, RiskLevel
from justshipit.db.models import AgentRequest, Artifact, Decision, Evidence, Kpi, KpiValue, Project, Task
from justshipit.db.session import session_scope
from justshipit.i18n import Lang, msg
from justshipit.operations import backup, gateway, growth, notify, retention
from justshipit.orchestration import context as ctx
from justshipit.services import (
    app_settings, evidence, ideas, memory, owner_tasks, people, registry, request_contract, requests,
)
from justshipit.services import projects as project_service


def _artifact(s, project_id: int | None, kind: str, title: str, rel: str, content: str) -> Artifact:
    sha = registry.write_files({rel: content}, f"[ops] {kind}: {title}")
    a = Artifact(project_id=project_id, kind=kind, title=title, path=rel, commit_sha=sha)
    s.add(a)
    s.flush()
    return a


def _ids(ids: list[int], lang: Lang) -> str:
    return ", ".join(f"EV-{i}" for i in ids) or msg("operations.none", lang)


def evidence_watch(project_id: int) -> None:
    """Re-verify the sources behind a finished project's conclusions and look for material changes."""
    lang = app_settings.language()
    with session_scope() as s:
        rows = list(s.scalars(select(Evidence).where(
            Evidence.project_id == project_id, Evidence.kind.in_(["verified_fact", "sourced_claim"]),
            Evidence.source_url.is_not(None))))
        targets = [(e.id, e.source_url, e.quote, e.verification_status) for e in rows]
    lost = []
    for eid, url, quote, before in targets:
        status, note = evidence._check(url, quote)
        if before in ("url_ok", "quote_found", "supported") and status == "url_unreachable":
            lost.append(eid)
            with session_scope() as s:
                e = s.get(Evidence, eid)
                finding = msg("operations.source_lost", lang, note=note)
                if finding not in (e.verification_note or ""):  # a rerun after a wait must not note it twice
                    mark = f"[watch {datetime.now(timezone.utc):%Y-%m-%d}] {finding}"
                    e.verification_note = f"{e.verification_note or ''}\n{mark}".strip()
    with session_scope() as s:
        p = s.get(Project, project_id)
        decisions = "\n".join(f"- DEC-{d.id} [{d.decision}] {d.title}: {ctx.clip(d.rationale, 300)}"
                              for d in s.scalars(select(Decision).where(Decision.project_id == project_id)))
        finished = (p.context or {}).get("finished_at", "")
        prompt = "\n\n".join([
            ctx.project_brief(s, p, reader="research"), "## Key evidence\n" + evidence.digest(s, project_id, limit=60),
            f"## Decisions\n{decisions}",
            f"## Sources that failed the recheck\n{', '.join(f'EV-{i}' for i in lost) or 'none'}",
            f"Search the web for changes since the project finished ({finished[:10] or 'date unknown'}) that could "
            "undo the premises of these decisions: new or amended laws and notices, competitor launches and price "
            "changes, updated public statistics, policy announcements. If nothing changed, say so.",
        ])
    out = run_agent("research", "scheduled", prompt, WatchOut, project_id=project_id)
    call_id = last_call_id()
    with session_scope() as s:
        p = s.get(Project, project_id)
        new_ids = evidence.record(s, project_id, None, "research", out.new_evidence)
        body = [msg("operations.watch.heading", lang, name=p.name, date=f"{datetime.now(timezone.utc):%Y-%m-%d}"),
                out.summary, "\n" + msg("operations.watch.lost", lang, ids=_ids(lost, lang)),
                "\n" + msg("operations.watch.changes", lang)] + [
            f"- [{c.severity}] {c.change} → {c.impact} ({c.related_evidence_id or '-'})" for c in out.material_changes]
        art = _artifact(s, project_id, "watch", msg("operations.watch.title", lang),
                        f"projects/{p.slug}/watch/{datetime.now(timezone.utc):%Y%m%d}.md", "\n".join(body))
        high = any(c.severity == "high" for c in out.material_changes)
        if out.recommend_reassessment or high or len(lost) >= 3:
            start = msg("operations.reassess.start", lang)
            requests.create(s, AgentRequestDraft(
                type=RequestType.PROPOSAL, title=msg("operations.reassess.title", lang, id=p.id),
                summary=out.summary + "\n\n" + "\n".join(f"- {c.change}" for c in out.material_changes[:8]),
                rationale=msg("operations.reassess.rationale", lang, path=art.path),
                options=[start, msg("operations.option.hold", lang), msg("operations.option.reject", lang)],
                recommended_option=start, action_kind="project.reassess", estimated_cost_usd=None,
                risk_level=RiskLevel.MEDIUM,
            ), from_agent="research", project_id=project_id, system=True,
                origin=ServerOrigin(OriginKind.REASSESS, request_contract.call_row(s, call_id)))
    evidence.verify_sources(new_ids)


def reassess_project(project_id: int, request_id: int) -> None:
    with session_scope() as s:
        p = s.get(Project, project_id)
        req = s.get(AgentRequest, request_id)
        if req.status != RequestStatus.APPROVED.value:
            raise PermissionError("reassessment not approved")
        requests.consume_grant(s, "project.reassess", project_id, request_id=req.id)
        project_service.create(
            s, f"{p.goal}\n\n[Reassessment] Review the conclusions of the earlier project P-{p.id} in the light of the "
               "changes below. Don't research again the evidence already in company memory; research only what "
               f"changed.\n{req.summary}",
            name=msg("operations.reassess.name", app_settings.language(), name=p.name), budget_usd=p.budget_usd / 2,
            parent_id=p.id)


def kpi_review(project_id: int) -> None:
    with session_scope() as s:
        p = s.get(Project, project_id)
        kpis = list(s.scalars(select(Kpi).where(Kpi.project_id == project_id)))
        if not kpis:
            return
        series = []
        for k in kpis:
            vals = list(s.scalars(select(KpiValue).where(KpiValue.kpi_id == k.id).order_by(KpiValue.at)))
            pts = ", ".join(f"{v.at:%m-%d}={v.value:g}" for v in vals[-20:]) or "no records"
            series.append(f"- {k.name} (target {k.target:g}{k.unit or ''}, {'↑' if k.direction == 'up' else '↓'}): {pts}")
        prompt = "\n\n".join([ctx.project_brief(s, p), "## KPI trend\n" + "\n".join(series),
                              "Assess where each KPI stands against its target. Offer causes only as hypotheses. If "
                              "the strategy or priorities need to change, propose it as a PROPOSAL request. If the "
                              "data is too thin, say so and propose a better way to measure."])
    out = run_agent("project_manager", "scheduled", prompt, KpiReviewOut, project_id=project_id)
    call_id = last_call_id()
    lang = app_settings.language()
    with session_scope() as s:
        p = s.get(Project, project_id)
        requests.from_agent_drafts(s, out.requests, agent="project_manager", project_id=project_id,
                                   origin=ServerOrigin(OriginKind.KPI_REVIEW, request_contract.call_row(s, call_id)))
        state = msg("operations.kpi.on_track" if out.on_track else "operations.kpi.watch", lang)
        _artifact(s, project_id, "kpi_review", msg("operations.kpi.title", lang),
                  f"projects/{p.slug}/kpi/{datetime.now(timezone.utc):%Y%m%d}.md",
                  msg("operations.kpi.heading", lang, state=state) + f"\n\n{out.assessment}\n\n"
                  + msg("operations.kpi.causes", lang) + "\n" + "\n".join(f"- {x}" for x in out.diagnosis))


def _strip_title(md: str) -> str:
    """Models often repeat the document title; we add our own dated heading."""
    lines = md.lstrip().splitlines()
    return "\n".join(lines[1:]).lstrip() if lines and lines[0].startswith("# ") else md


def portfolio_review() -> None:
    with session_scope() as s:
        lines = []
        for p in s.scalars(select(Project).where(Project.status != "ARCHIVED").order_by(Project.id)):
            open_tasks = s.scalar(select(Task.id).where(Task.project_id == p.id, Task.status.notin_(
                ["DONE", "FAILED", "CANCELLED", "REJECTED", "REPLACED"])).limit(1))
            lines.append(f"- P-{p.id} {p.name} [{p.status}/{p.phase}] cost {p.spent_usd:.2f}/{p.budget_usd:.0f}"
                         f"{' · tasks in progress' if open_tasks else ''}")
        pend = "\n".join(f"- REQ-{r.id} [{r.type}] {r.title} (P-{r.project_id}, {r.created_at:%m-%d})"
                         for r in requests.pending(s))
        mem = memory.recall_block(s, "company strategy, priorities and decisions", k=10)
        owner_work = owner_tasks.portfolio_section(s)
        idea_box = ideas.portfolio_section(s)
    out = run_agent("project_manager", "scheduled", f"## Projects\n{chr(10).join(lines)}\n\n## Waiting for approval\n{pend or 'none'}\n\n"
                                    f"{owner_work + chr(10) * 2 if owner_work else ''}"
                                    f"{idea_box + chr(10) * 2 if idea_box else ''}{mem}\n\n"
                                    "Write the weekly company review: the company's priorities, the work that is stuck, "
                                    "and what the owner needs to decide this week.",
                    PortfolioReviewOut, project_id=None)
    call_id = last_call_id()
    lang = app_settings.language()
    with session_scope() as s:
        requests.from_agent_drafts(s, out.requests, agent="project_manager", project_id=None,
                                   origin=ServerOrigin(OriginKind.PORTFOLIO, request_contract.call_row(s, call_id)))
        today = f"{datetime.now(timezone.utc):%Y-%m-%d}"
        _artifact(s, None, "portfolio", msg("operations.portfolio.title", lang),
                  f"company/weekly/{datetime.now(timezone.utc):%Y%m%d}.md",
                  msg("operations.portfolio.heading", lang, date=today) + f"\n\n{_strip_title(out.summary_markdown)}\n\n"
                  + msg("operations.portfolio.priorities", lang) + "\n"
                  + "\n".join(f"{i + 1}. {x}" for i, x in enumerate(out.priorities))
                  + "\n\n" + msg("operations.portfolio.stalled", lang) + "\n" + "\n".join(f"- {x}" for x in out.stalled_items))


notify.add_briefing_line(ideas.pending_discard_line)
notify.add_briefing_line(people.today_line)

HANDLERS = {
    **growth.HANDLERS,
    "execute_action": lambda p: gateway.execute_action(p["request_id"]),
    "evidence_watch": lambda p: evidence_watch(p["project_id"]),
    "reassess_project": lambda p: reassess_project(p["project_id"], p["request_id"]),
    "kpi_review": lambda p: kpi_review(p["project_id"]),
    "portfolio_review": lambda p: portfolio_review(),
    "retention": lambda p: retention.run(),
    "backup": lambda p: backup.run(),
    "notify": lambda p: notify.deliver(p, p.get("attempt", 1)),
    "daily_briefing": lambda p: notify.send_briefing(p["date"], p.get("attempt", 1)),
}
