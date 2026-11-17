"""`ship` command line: run the company, work the Approval Center, inspect results."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from logging.handlers import RotatingFileHandler

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from sqlalchemy import func, select

from justshipit.contracts.requests import HumanDecision, HumanResponse
from justshipit.db.models import AgentRequest, Artifact, BudgetMonth, Decision, DevJob, LLMCallRecord, Project, Spec, Task
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.services import projects as project_service
from justshipit.services import queue, registry, requests
from justshipit.settings import settings

app = typer.Typer(no_args_is_help=True, help="Just Ship It")
project_app = typer.Typer(no_args_is_help=True, help="Projects (business goals)")
app.add_typer(project_app, name="project")
con = Console()

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


@app.command()
def init() -> None:
    """Set a random Postgres password, apply DB migrations, initialize the Spec Registry and LangGraph checkpoint
    tables."""
    from justshipit.services import credentials
    for line in credentials.ensure(settings().root / ".env"):
        con.print(line, soft_wrap=True)
    _recreate_compose_services()
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=settings().root, check=True)
    registry.ensure_repo()
    settings().products_dir.mkdir(exist_ok=True)
    from justshipit.orchestration.runner import graph
    graph()
    con.print("[green]initialized[/] database, registry/, products/, checkpointer")
    _print_cli_logins()


def _recreate_compose_services() -> None:
    """Recreating the services from .env starts them on the password init wrote, and builds the sandbox egress proxy
    and networks. Compose reads the password from .env itself."""
    try:
        done = subprocess.run(["docker", "compose", "up", "-d", "--wait"], cwd=settings().root, capture_output=True,
                              text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        con.print(f"[yellow]docker compose up -d failed ({type(exc).__name__}). Run it yourself.[/]")
        return
    if done.returncode:
        con.print("[yellow]docker compose up -d failed. Run it yourself.[/]")
        con.print(done.stderr[-800:], soft_wrap=True)
    else:
        con.print("docker compose: recreated the services with the password in .env")


_LOGIN_CHECK_TIMEOUT_S = 30


def _cli_logins() -> list[tuple[bool, str]]:
    """Whether claude is installed and logged in to a subscription, asked without a model call and
    without echoing what the CLI prints, since its status output can include part of a key."""
    import json

    from justshipit.runtime.base import LLMError, run_process
    from justshipit.runtime.claude_cli import ClaudeRunner

    install = ("claude: not installed. Install Claude Code (npm i -g @anthropic-ai/claude-code). "
               "If it lives somewhere else, set SHIP_CLAUDE_BIN in .env.")
    args = ["auth", "status"]

    def ask() -> tuple[int, str] | str:
        """Exit code and output, or the line saying why the CLI could not be asked."""
        try:
            binary = ClaudeRunner().bin
        except LLMError:
            return install
        try:
            proc = run_process([binary, *args], "", None, _LOGIN_CHECK_TIMEOUT_S)
        except LLMError:
            return (f"claude: check timed out. `claude {' '.join(args)}` didn't finish within "
                    f"{_LOGIN_CHECK_TIMEOUT_S} seconds. Try running it in a terminal.")
        except PermissionError:
            return f"claude: not executable. Check that `{binary}` has execute permission."
        except OSError:
            return install
        return proc.returncode, (proc.stdout + proc.stderr).decode("utf-8", errors="replace")

    ok, subscription_only = "installed, subscription login confirmed", "This system uses subscription login only."
    out = []
    claude = ask()
    if isinstance(claude, str):
        out.append((False, claude))
    else:
        try:
            info = json.loads(claude[1])
        except ValueError:
            info = {}
        if not info.get("loggedIn"):
            out.append((False, "claude: not logged in. Run `claude` in a terminal and log in with your subscription account."))
        elif "api" in str(info.get("authMethod", "")).lower():
            out.append((False, (f"claude: authenticated with an API key. {subscription_only} Remove the API key "
                                "setting and log in to your subscription account with `claude`.")))
        else:
            out.append((True, f"claude: {ok}"))
    return out


def _print_cli_logins() -> None:
    for ok, line in _cli_logins():
        con.print(("[green]✓[/] " if ok else "[red]✗[/] ") + line, highlight=False, soft_wrap=True)


@project_app.command("new")
def project_new(goal: str, name: str = typer.Option(None), slug: str = typer.Option(None),
                budget: float = typer.Option(None, help="USD-equivalent budget"),
                start: bool = typer.Option(True, help="Queue the run for the worker")) -> None:
    """Give the company a business goal."""
    with session_scope() as s:
        p = project_service.create(s, goal, name=name, slug=slug, budget_usd=budget, start=start, by_owner=True)
        con.print(f"[green]P-{p.id}[/] {p.name} ({p.slug}) budget {p.budget_usd} — "
                  + ("queued; run `ship supervise`" if start else "not started"))


@project_app.command("list")
def project_list() -> None:
    with session_scope() as s:
        t = Table("ID", "Name", "Status", "Phase", "Round", "Spent/Budget", "Open REQ")
        for p in s.scalars(select(Project).order_by(Project.id.desc())):
            n = len(requests.pending(s, p.id))
            t.add_row(f"P-{p.id}", p.name, p.status, p.phase or "", str(p.plan_round),
                      f"{p.spent_usd:.2f}/{p.budget_usd:.0f}", str(n))
        con.print(t)


@project_app.command("archive")
def project_archive(project_id: int) -> None:
    """Take a project off the books (pending requests cancelled, schedules stopped; data kept)."""
    from justshipit.services import projects
    try:
        with session_scope() as s:
            done = projects.archive(s, project_id)
    except ValueError as exc:
        con.print(str(exc), soft_wrap=True)
        raise typer.Exit(1) from None
    con.print(f"P-{project_id} archived: cancelled REQ {done['cancelled_requests']}, "
              f"disabled schedules {done['disabled_schedules']}")


business_app = typer.Typer(no_args_is_help=True, help="Businesses")
app.add_typer(business_app, name="business")
requests_app = typer.Typer(no_args_is_help=True, help="Owner requests (decision cards)")
app.add_typer(requests_app, name="requests")
DELEGATED = "claude (오너 위임)"


@app.command()
def stop(agent: str = typer.Option(None, help="Stop only this agent (its key, e.g. research)"),
         reason: str = typer.Option(None, help="Why, for the activity log"),
         by: str = typer.Option("owner", help=f"Who stops it, e.g. '{DELEGATED}'")) -> None:
    """Emergency stop: nothing new starts but housekeeping, and running work stops at its next check and waits in the
    queue. Nothing is cancelled. With --agent, only that agent stops. `ship resume` carries on."""
    from justshipit.services import activity, stops
    try:
        with activity.as_actor(by), session_scope() as s:
            done = (stops.stop_agent(s, agent, reason, by=by) if agent else stops.stop_company(s, reason, by=by))
    except stops.UnknownAgent:
        con.print(f"no agent '{agent}'")
        raise typer.Exit(1) from None
    con.print(f"{agent or 'company'} stopped since {done['stopped']['at'].astimezone():%Y-%m-%d %H:%M %z}")


@app.command()
def resume(agent: str = typer.Option(None, help="Resume only this agent"),
           by: str = typer.Option("owner", help=f"Who resumes it, e.g. '{DELEGATED}'")) -> None:
    """Lift `ship stop`: queued work runs again. Resuming what is not stopped changes nothing."""
    from justshipit.services import activity, stops
    try:
        with activity.as_actor(by), session_scope() as s:
            stops.resume_agent(s, agent, by=by) if agent else stops.resume_company(s, by=by)
    except stops.UnknownAgent:
        con.print(f"no agent '{agent}'")
        raise typer.Exit(1) from None
    con.print(f"{agent or 'company'} running")


@business_app.command("archive")
def business_archive(business_id: int,
                     by: str = typer.Option("owner", help=f"Who archives it, e.g. '{DELEGATED}'")) -> None:
    """Take a business off the books: its projects archived, pending requests cancelled, schedules and routines
    stopped. Nothing is deleted; `business unarchive` brings it back."""
    from justshipit.services import activity, app_settings
    lang = app_settings.language()
    try:
        with activity.as_actor(by), session_scope() as s:  # the commit's flush logs the actor too
            done = project_service.archive_business(s, business_id, lang=lang, by=by)
    except ValueError as exc:
        con.print(str(exc), soft_wrap=True)
        raise typer.Exit(1) from None
    con.print(f"B-{business_id} archived: projects {done['projects']}, cancelled REQ {done['cancelled_requests']}, "
              f"disabled schedules {done['disabled_schedules']}")


@business_app.command("unarchive")
def business_unarchive(business_id: int) -> None:
    """Bring an archived business back with the projects archived with it. Cancelled requests stay cancelled."""
    from justshipit.services import app_settings
    lang = app_settings.language()
    try:
        with session_scope() as s:
            done = project_service.unarchive_business(s, business_id, lang=lang)
    except ValueError as exc:
        con.print(str(exc), soft_wrap=True)
        raise typer.Exit(1) from None
    con.print(f"B-{business_id} active again: projects {done['projects']}, enabled schedules {done['enabled_schedules']}")


profile_app = typer.Typer(no_args_is_help=True, help="The customer description (who buys, how they are reached)")
app.add_typer(profile_app, name="profile")


@profile_app.command("backfill")
def profile_backfill(business: int = typer.Option(None, "--business", help="Only this business id"),
                     dry_run: bool = typer.Option(False, "--dry-run", help="Only list the businesses it would queue")
                     ) -> None:
    """Queue a reading of the approved plan, as guesses, for each active business that has no customer description.
    The jobs run under the usage caps. Prints business ids and names only."""
    from justshipit.services.profile import infer
    with session_scope() as s:
        found = infer.backfill(s, business_id=business, dry_run=dry_run)
    for bid, name in found:
        con.print(f"B-{bid} {name}", soft_wrap=True, markup=False)
    con.print(f"{'would queue' if dry_run else 'queued'} {len(found)}")


@profile_app.command("audit")
def profile_audit(compare: bool = typer.Option(False, "--compare",
                                               help="Accepted from before the switch-over; the word count it compared"
                                                    " with is gone, so the report is the same without it"),
                  business: int = typer.Option(None, "--business", help="Only this business id")) -> None:
    """Read-only. Per business, the description and the channels and places it picks now."""
    from justshipit.services.profile import audit
    with session_scope() as s:
        con.print(audit.render(audit.compare(s, [business] if business is not None else None)), soft_wrap=True,
                  markup=False)


cycle_app = typer.Typer(no_args_is_help=True, help="A business's cycle of owner-started stages")
app.add_typer(cycle_app, name="cycle")


@cycle_app.command("board")
def cycle_board_show(business_id: int) -> None:
    """The dashboard's cycle board for a business, as JSON: its state, what waits for the owner, the stages."""
    from justshipit.services import app_settings, cycle_board
    with session_scope() as s:
        con.print_json(cycle_board.board(s, business_id, lang=app_settings.language()).model_dump_json())


@cycle_app.command("decide")
def cycle_decide(cycle_id: int, decision: str = typer.Argument(..., help='JSON, e.g. \'{"kind": "start_cycle", '
                                                                          '"version": 1}\' or \'{"kind": "start_next", '
                                                                          '"after_stage_id": 4, "version": 2}\' (the '
                                                                          'version is the proposal version the card '
                                                                          'showed; see `ship cycle board`)'),
                 by: str = typer.Option("owner", help=f"Who decides, e.g. '{DELEGATED}'")) -> None:
    """The owner's decision on a cycle card (start, revise, start next, stop here, ...), the same one the console makes."""
    from pydantic import TypeAdapter, ValidationError
    from justshipit.services import activity, app_settings, cycle
    from justshipit.services.control import ControlError
    lang = app_settings.language()
    try:
        d = TypeAdapter(cycle.Decision).validate_json(decision)
        with activity.as_actor(by), session_scope() as s:
            cycle.decide(s, cycle_id, d, by=by, lang=lang)
    except (ValidationError, ControlError) as exc:
        con.print(str(exc), soft_wrap=True, markup=False)
        raise typer.Exit(1) from None
    con.print(f"cycle #{cycle_id}: {d.kind} done")


def _pair(text: str) -> tuple[int, int]:
    old, _, new = text.partition(":")
    try:
        return requests.parse_id(old), requests.parse_id(new)
    except ValueError:
        raise typer.BadParameter(f"{text!r}: expected OLD:NEW, as in 22:28") from None


@requests_app.command("close-duplicates")
def requests_close_duplicates(
        business: int = typer.Option(..., help="Business id (B-n)"),
        dry_run: bool = typer.Option(False, "--dry-run", help="List what would close, change nothing"),
        replaced: list[str] | None = typer.Option(None, "--replaced",  # noqa: B008
                                                  help="OLD:NEW, a card a later one replaced (repeatable)"),
        by: str = typer.Option("owner", help=f"Who closes them, e.g. '{DELEGATED}'")) -> None:
    """Close the business's duplicate cards as superseded into the card that decides each. Never approves anything."""
    from justshipit.services import activity, app_settings, matters
    pairs = [_pair(p) for p in replaced or []]
    lang = app_settings.language()
    try:
        with activity.as_actor(by), session_scope() as s:  # the commit's flush logs the actor too
            found = matters.duplicates(s, business, lang=lang) + matters.replaced(s, business, pairs, lang=lang)
            for d in found:
                con.print(f"{requests.display_id(d.card.id)} ({d.card.status}) -> {requests.display_id(d.into.id)}: "
                          f"{d.why}", soft_wrap=True)
            if dry_run:
                s.rollback()
                con.print(f"dry run: {len(found)} would close")
                return
            closed = requests.close_duplicates(s, found, by=by)
    except ValueError as exc:
        con.print(str(exc), soft_wrap=True)
        raise typer.Exit(1) from None
    con.print(f"closed {len(closed)}: {', '.join(map(requests.display_id, closed)) or '-'}")


work_path_app = typer.Typer(no_args_is_help=True, help="What each decision card records of where it came from")
app.add_typer(work_path_app, name="work-path")


@work_path_app.command("audit")
def work_path_audit() -> None:
    """For each field of the work path, how many cards have it recorded, backfilled, matched or unknown."""
    from justshipit.services import work_path_audit as audit
    with session_scope() as s:
        counts = audit.audit(s)
    t = Table("Field", *audit.LEVELS)
    for field, levels in counts.items():
        t.add_row(field, *(str(levels[level]) for level in audit.LEVELS))
    con.print(t)


@work_path_app.command("backfill")
def work_path_backfill(
        from_calls: bool = typer.Option(False, "--from-calls", help="Link a Project Manager card to the one model call whose "
                                        "output holds a draft with the card's own title"),
        dry_run: bool = typer.Option(False, "--dry-run", help="List what would be linked, change nothing")) -> None:
    """Add what the migration could not read from the cards themselves. Opt-in: name the source."""
    from justshipit.services import work_path_audit as audit
    if not from_calls:
        con.print("Name a source to backfill from: --from-calls")
        raise typer.Exit(1)
    with session_scope() as s:
        found = audit.backfill_from_calls(s, dry_run=dry_run)
        for m in found:
            how = (f"matched call {m.call_ids[0]}" if m.linked else
                   f"{len(m.call_ids)} calls match, left unknown" if m.call_ids else "no call matches, left unknown")
            con.print(f"{requests.display_id(m.request_id)}: {how}", soft_wrap=True)
        linked = sum(m.linked for m in found)
        con.print(f"{'dry run: ' if dry_run else ''}{linked} of {len(found)} cards "
                  f"{'would be linked' if dry_run else 'linked'}")


@project_app.command("show")
def project_show(project_id: int) -> None:
    with session_scope() as s:
        p = s.get(Project, project_id)
        con.print(f"[bold]P-{p.id} {p.name}[/] [{p.status}/{p.phase}] spent {p.spent_usd:.2f}/{p.budget_usd}")
        con.print(f"Goal: {p.goal}")
        if p.error:
            con.print(f"[red]Error:[/] {p.error}")
        t = Table("Task", "Owner", "Status", "It", "Contested", "Title", "Review")
        for task in s.scalars(select(Task).where(Task.project_id == p.id).order_by(Task.id)):
            t.add_row(f"T-{task.id}", task.owner, task.status, str(task.iteration), "⚑" if task.contested else "",
                      task.title[:60], (task.review or {}).get("verdict", ""))
        con.print(t)
        decs = list(s.scalars(select(Decision).where(Decision.project_id == p.id)))
        for d in decs:
            con.print(f"DEC-{d.id} [{d.decision}/{d.status}] {d.title}")
        for a in s.scalars(select(Artifact).where(Artifact.project_id == p.id, Artifact.kind.in_(
                ["plan", "report", "minutes", "prd"])).order_by(Artifact.id)):
            con.print(f"  {a.kind:8} registry/{a.path}")


@project_app.command("run")
def project_run(project_id: int) -> None:
    """Run in the foreground (for debugging) instead of via the worker."""
    _logging()
    from justshipit.orchestration import runner
    from justshipit.services import gate, stops
    with session_scope() as s:
        company_stopped = stops.read(s).company is not None
    if company_stopped:
        con.print("The company is stopped, so this run would spend usage. Run `ship resume` first.")
        raise typer.Exit(1)
    with gate.asked("cli:project_run"):
        runner.start(project_id)
    project_show(project_id)


@project_app.command("retry")
def project_retry(project_id: int) -> None:
    """Continue a failed run from its last LangGraph checkpoint."""
    with session_scope() as s:
        queue.enqueue(s, "retry_project", {"project_id": project_id}, max_attempts=1, by_owner=True)
    con.print("queued retry")


@project_app.command("report")
def project_report(project_id: int) -> None:
    with session_scope() as s:
        p = s.get(Project, project_id)
        art = s.get(Artifact, (p.context or {}).get("report_artifact_id") or 0)
        if not art:
            con.print("no report yet")
            return
        con.print(Markdown(registry.read(art.path)))


@app.command()
def inbox() -> None:
    """Approval Center: pending agent requests."""
    with session_scope() as s:
        t = Table("REQ", "Type", "Policy", "Blocking", "From", "Project", "Title")
        for r in requests.pending(s):
            t.add_row(requests.display_id(r.id), r.type, r.policy_mode, "●" if r.blocking else "", r.from_agent,
                      f"P-{r.project_id}" if r.project_id else "", r.title[:70])
        con.print(t)


@app.command()
def show(request_id: str) -> None:
    with session_scope() as s:
        r = s.get(AgentRequest, requests.parse_id(request_id))
        con.print(f"[bold]{requests.display_id(r.id)} {r.type}[/] {r.title}")
        con.print(f"from {r.from_agent} · policy {r.policy_mode} · risk {r.risk_level} · blocking {r.blocking} · "
                  f"status {r.status} · action {r.action_kind or '-'} · revision {requests.revision(r)}")
        con.print(Markdown(f"{r.summary}\n\n**Why**: {r.rationale}"))
        if r.options:
            con.print("options: " + " / ".join(r.options) + (f" (recommended: {r.recommended_option})" if r.recommended_option else ""))


def _respond(request_id: str, decision: HumanDecision, note: str | None, answer: str | None = None,
             confirm: str | None = None, option: str | None = None, max_amount: float | None = None,
             revision: int | None = None) -> None:
    with session_scope() as s:
        try:
            r = requests.respond(s, requests.parse_id(request_id), HumanResponse(
                decision=decision, note=note, answer=answer, confirm_text=confirm, chosen_option=option,
                max_amount_usd=max_amount, revision=revision))
        except requests.ResponseRejected as exc:
            con.print(f"[red]{exc}[/]")
            raise typer.Exit(1)
        con.print(f"{requests.display_id(r.id)} → {r.status}")


@app.command()
def approve(request_id: str, note: str = typer.Option(None), option: str = typer.Option(None),
            confirm: str = typer.Option(None, help="strong_approve: repeat the request id"),
            max_amount: float = typer.Option(None, help="Narrow the grant amount"),
            revision: int = typer.Option(None, help="The revision `show` printed: refused if the card changed since")) -> None:
    _respond(request_id, HumanDecision.APPROVE, note, confirm=confirm, option=option, max_amount=max_amount,
             revision=revision)


@app.command()
def answer(request_id: str, text: str, note: str = typer.Option(None),
           revision: int = typer.Option(None, help="The revision `show` printed: refused if the card changed since")) -> None:
    _respond(request_id, HumanDecision.ANSWER, note, answer=text, revision=revision)


@app.command()
def reject(request_id: str, note: str = typer.Option(None)) -> None:
    _respond(request_id, HumanDecision.REJECT, note)


@app.command()
def defer(request_id: str, note: str = typer.Option(None)) -> None:
    _respond(request_id, HumanDecision.DEFER, note)


@app.command()
def usage() -> None:
    """Budget and model usage (USD = list-price equivalent; subscriptions are not billed per call)."""
    with session_scope() as s:
        for m in s.scalars(select(BudgetMonth).order_by(BudgetMonth.month.desc()).limit(3)):
            con.print(f"{m.month}: spent {m.spent_usd:.2f} reserved {m.reserved_usd:.2f} / limit {m.limit_usd}")
        t = Table("Agent", "Provider", "Calls", "Errors", "In tok", "Out tok", "USD-eq")
        rows = s.execute(select(LLMCallRecord.agent, LLMCallRecord.provider, func.count(),
                                func.count().filter(LLMCallRecord.status != "ok"),
                                func.sum(LLMCallRecord.input_tokens), func.sum(LLMCallRecord.output_tokens),
                                func.sum(LLMCallRecord.cost_usd))
                         .group_by(LLMCallRecord.agent, LLMCallRecord.provider))
        for a, prov, n, err, i, o, c in rows:
            t.add_row(a, prov, str(n), str(err or 0), f"{i or 0:,}", f"{o or 0:,}", f"{c or 0:.2f}")
        con.print(t)


@app.command()
def dev(retry: str = typer.Option(None, help="Spec key to retry with a new attempt")) -> None:
    """Engineering team: specs and dev jobs."""
    if retry:
        from justshipit.engineering.specs import retry_dev
        from justshipit.services import app_settings
        con.print(f"queued J-{retry_dev(retry, app_settings.language())}")
    with session_scope() as s:
        t = Table("Spec", "Status", "Repo", "Job", "Job status", "Branch / PR")
        for sp in s.scalars(select(Spec).order_by(Spec.id.desc())):
            job = s.scalar(select(DevJob).where(DevJob.spec_id == sp.id).order_by(DevJob.id.desc()))
            t.add_row(sp.spec_key, sp.status, sp.target_repository or "", f"J-{job.id}" if job else "",
                      job.status if job else "", (job.pr_url or job.branch or "") if job else "")
        con.print(t)


@app.command()
def skills(factory: bool = typer.Option(False, "--factory", help="Queue a Skill Factory cycle")) -> None:
    """Capability team: skills and reported capability gaps."""
    from justshipit.capability.factory import gaps
    from justshipit.db.models import Skill
    with session_scope() as s:
        if factory:
            queue.enqueue(s, "skill_factory", {}, max_attempts=1, by_owner=True)
            con.print("queued skill factory")
        t = Table("Skill", "Ver", "Kind", "Status", "Targets", "Eval")
        for k in s.scalars(select(Skill).order_by(Skill.id.desc())):
            ev = (k.eval_results or {}).get("summary") or {}
            t.add_row(k.name, str(k.version), k.kind, k.status, ",".join(k.target_agents),
                      f"pass {ev.get('baseline_pass')}→{ev.get('candidate_pass')}" if ev else "")
        con.print(t)
    g = Table("Agent", "Count", "Capability gap")
    for x in gaps()[:20]:
        g.add_row(x["agent"], str(x["count"]), x["gap"][:100])
    con.print(g)


sandbox_app = typer.Typer(no_args_is_help=True, help="Container sandboxes (engineer, analysis)")
app.add_typer(sandbox_app, name="sandbox")


def _sandbox_images() -> dict[str, tuple[str, str]]:
    from justshipit.settings import company_config
    cc = company_config()
    return {"engineer": ("docker/engineer.Dockerfile", cc["engineering"]["sandbox"]["image"]),
            "analysis": ("docker/analysis.Dockerfile", cc["analysis"]["image"])}


@sandbox_app.command("build")
def sandbox_build(which: str = typer.Argument("all", help="engineer | analysis | all")) -> None:
    """Build the sandbox images."""
    import subprocess
    root = settings().root
    for name, (dockerfile, tag) in _sandbox_images().items():
        if which in ("all", name):
            con.print(f"building {tag} …")
            r = subprocess.run(["docker", "build", "-f", str(root / dockerfile), "-t", tag, str(root / "docker")])
            if r.returncode != 0:
                raise typer.Exit(r.returncode)


@sandbox_app.command("check")
def sandbox_check() -> None:
    """Show whether each sandbox is ready."""
    from justshipit.engineering import worker
    from justshipit.i18n.said import words
    from justshipit.runtime import host_auth
    from justshipit.runtime.base import LLMError
    from justshipit.runtime.claude_cli import engineering_isolation
    from justshipit.sandbox import docker
    from justshipit.services import app_settings
    lang = app_settings.language()
    _print_cli_logins()
    ok = docker.docker_ok()
    con.print(f"docker: {'ok' if ok else 'not running. Start Docker.'}", soft_wrap=True)
    for name, (_, tag) in _sandbox_images().items():
        present = ok and docker.image_present(tag)
        con.print(f"{name} image {tag}: {'present' if present else f'missing. Build it with `ship sandbox build {name}`.'}", soft_wrap=True)
    try:
        iso = engineering_isolation()
    except LLMError as exc:
        con.print(words(exc, lang), soft_wrap=True)
        return
    con.print(f"engineer isolation: {iso}")
    if iso == "container":
        con.print(f"egress proxy {docker.EGRESS_PROXY}: "
                  + ("running" if ok and docker.egress_running() else "off. Start it with `docker compose up -d egress`."),
                  soft_wrap=True)
        con.print("engineer auth: " + host_auth.status(lang), soft_wrap=True)
        con.print(worker.host_git_refusal(lang) or f"git: ok ({worker._git_version().removeprefix('git version ')}, passed the engineer clone fetch safety check)",
                  soft_wrap=True)


kpi_app = typer.Typer(no_args_is_help=True, help="KPIs")
app.add_typer(kpi_app, name="kpi")


@kpi_app.command("list")
def kpi_list() -> None:
    from justshipit.db.models import Kpi, KpiValue
    with session_scope() as s:
        t = Table("KPI", "Project", "Name", "Target", "Latest")
        for k in s.scalars(select(Kpi).order_by(Kpi.id)):
            last = s.scalar(select(KpiValue).where(KpiValue.kpi_id == k.id).order_by(KpiValue.at.desc()))
            t.add_row(f"K-{k.id}", f"P-{k.project_id}", k.name, f"{'≥' if k.direction == 'up' else '≤'}{k.target:g} {k.unit or ''}",
                      f"{last.value:g}" if last else "-")
        con.print(t)


@kpi_app.command("add")
def kpi_add(project_id: int, name: str, target: float, unit: str = typer.Option(None),
            direction: str = typer.Option("up")) -> None:
    from justshipit.db.models import Kpi
    with session_scope() as s:
        k = Kpi(project_id=project_id, name=name, target=target, unit=unit, direction=direction)
        s.add(k)
        s.flush()
        con.print(f"K-{k.id} created")


@kpi_app.command("record")
def kpi_record(kpi_id: int, value: float, note: str = typer.Option(None)) -> None:
    from justshipit.db.models import KpiValue
    with session_scope() as s:
        s.add(KpiValue(kpi_id=kpi_id, value=value, note=note))
    con.print("recorded")


@app.command()
def schedules(run: int = typer.Option(None, help="Run schedule id now"),
              toggle: int = typer.Option(None, help="Enable/disable schedule id")) -> None:
    """Recurring operations (change watch, KPI review, portfolio review, skill factory)."""
    from justshipit.db.models import Schedule
    from justshipit.operations import scheduler
    with session_scope() as s:
        scheduler.sync(s)
        if run:
            scheduler.run_now(s, run)
        if toggle:
            sc = s.get(Schedule, toggle)
            sc.enabled = not sc.enabled
        t = Table("ID", "Name", "Kind", "Every(h)", "Enabled", "Next", "Last")
        for sc in s.scalars(select(Schedule).order_by(Schedule.id)):
            t.add_row(str(sc.id), sc.name, sc.kind, f"{sc.interval_hours:g}", str(sc.enabled),
                      f"{sc.next_run_at:%m-%d %H:%M}", f"{sc.last_run_at:%m-%d %H:%M}" if sc.last_run_at else "-")
        con.print(t)


@app.command()
def consent(contact_id: int, granted: bool = typer.Argument(True)) -> None:
    """Record (or revoke) a CRM contact's consent. Only humans can do this; live email requires it."""
    from justshipit.db.models import CrmContact
    with session_scope() as s:
        s.get(CrmContact, contact_id).consent = granted
    con.print(f"C-{contact_id} consent={granted}")


@app.command()
def backup(keep: int = typer.Option(None, min=1, help="Backups to keep (default operations.backups_kept)")) -> None:
    """Copy the app database into var/backups/ (pg_dump in the compose postgres container). The worker does it daily."""
    from justshipit.operations import backup as backups
    try:
        path = backups.run(keep)
    except backups.BackupFailed as exc:
        con.print(f"[red]✗[/] {exc}", soft_wrap=True)
        raise typer.Exit(1) from exc
    con.print(f"[green]✓[/] {path}")


def _print_problems() -> None:
    """Run the PC checks and put one block in the terminal for those that fail, one warning each in the log. Run by
    `supervise` and `api`, so a stopped proxy is told here, where the owner just started the console."""
    from justshipit.services import system_health
    lang = system_health.owner_language()
    found = system_health.startup_problems(lang)
    if not found:
        return
    con.print(msg("system.cli.header", lang), style="bold yellow", markup=False)
    for d in found:
        con.print(f"  {d['problem']}", markup=False, soft_wrap=True)
        con.print(f"    {msg('system.cli.fix', lang)} {d['fix']}", markup=False, soft_wrap=True)


@app.command()
def doctor() -> None:
    """Check this PC for what the company needs, and say how to fix what is missing. Exit code 1 if anything is."""
    from rich.markup import escape

    from justshipit.services import system_health
    lang = system_health.owner_language()
    checks = system_health.report(lang, results=system_health.snapshot(fresh=True))
    titles = {c["id"]: c["title"] for c in checks}
    for c in checks:
        if c["state"] == "ok":
            con.print(f"[green]✓[/] {escape(c['title'])}", highlight=False)
        elif c["state"] == "skipped":
            con.print(f"[dim]-[/] {escape(c['title'])}: " + escape(msg("system.cli.skipped", lang, needs=titles[c["needs"]])),
                      highlight=False, soft_wrap=True)
        else:
            con.print(f"[red]✗[/] {escape(c['title'])}: {escape(c['problem'])}", highlight=False, soft_wrap=True)
            con.print(f"    {escape(msg('system.cli.fix', lang))} {escape(c['fix'])}", highlight=False, soft_wrap=True)
    bad = [c for c in checks if c["state"] == "problem"]
    if bad:
        raise typer.Exit(1)
    con.print(msg("system.cli.all_ok", lang), markup=False)


@app.command()
def supervise(concurrency: int = 4, port: int = 8000,
              install: bool = typer.Option(False, "--install", help="Run this at every Windows logon, then exit"),
              uninstall: bool = typer.Option(False, "--uninstall", help="Remove that logon task, then exit")) -> None:
    """Run the worker and the console, and start each again when it exits. Ctrl+C stops both."""
    from justshipit.workers import supervisor
    if install or uninstall:
        out = supervisor.install(concurrency, port, remove=uninstall)
        if out.returncode != 0:
            con.print(f"[red]✗[/] Couldn't register with Task Scheduler: {out.stderr.strip()[-500:]}", soft_wrap=True)
            raise typer.Exit(1)
        con.print(f"Logon task '{supervisor.TASK_NAME}' " + ("removed." if uninstall else
                  "registered. From the next logon the worker and the console start on their own. To start them now, "
                  "run this command without --install."), markup=False, soft_wrap=True)
        return
    _logging("supervisor")
    _print_problems()
    os.environ["SHIP_SUPERVISED"] = "1"  # the console it starts logs the same problems but leaves the terminal to this
    stop = threading.Event()
    supervisor.stop_on_signals(stop)
    con.print(f"supervising the worker (concurrency {concurrency}) and the console (port {port}) — Ctrl+C to stop")
    supervisor.run(supervisor.managed(concurrency, port), stop)


@app.command()
def worker(concurrency: int = 3) -> None:
    """Run the job worker (company runs, resumes, spec writing, dev jobs)."""
    _logging("worker")
    from justshipit.workers.runner import serve
    con.print(f"worker started (concurrency {concurrency}) — Ctrl+C to stop")
    serve(concurrency=concurrency)


@app.command()
def api(host: str = "127.0.0.1", port: int = 8000,
        login: bool = typer.Option(False, "--login", help="Print a fresh login link for a running console and exit")
        ) -> None:
    """Serve the Company Control Plane API."""
    from justshipit.api import access
    from justshipit.api.app import ConsoleServer, server_config
    from justshipit.api.app import app as console
    try:
        rotated = access.retire_cookie_token(settings().var_dir)
        access.ensure_token(settings().var_dir)
        code = access.mint_login_code(settings().var_dir)
    except access.TokenFileError as exc:
        con.print(f"[red]✗[/] {exc}", soft_wrap=True)
        raise typer.Exit(1) from exc
    if rotated:
        con.print("Security update: earlier versions put the API token itself in the console login cookie, so other "
                  "ports on this PC (a dev server, say) could receive it. The API token was made anew once for that. "
                  "Log in to the console again with the login address below, and point scripts that used "
                  "var/api_token at the new token.", markup=False, soft_wrap=True)
    # The console answers only as 127.0.0.1 on this PC (localhost is the product preview's name), so a server
    # started on `localhost` prints the address that will not be redirected.
    shown = ("<this PC's address>" if host in ("0.0.0.0", "::") else "127.0.0.1" if host.lower() == "localhost"
             else f"[{host}]" if ":" in host else host)
    con.print(f"Console login address (opens once, within {access.LOGIN_TTL_S // 60} minutes): "
              f"http://{shown}:{port}/?{access.LOGIN_PARAM}={code}", markup=False, soft_wrap=True)
    con.print("If the address has expired, get a new one with `uv run ship api --login`. "
              "Scripts send the token in var/api_token as an `Authorization: Bearer` header.", markup=False, soft_wrap=True)
    if login:
        return
    console.state.loopback_only = access.is_loopback(host)
    if not console.state.loopback_only:
        con.print(f"[yellow]Warning[/] The console also opens outside this PC ({host}). http isn't encrypted, so on a "
                  "network many people share, like a cafe or an office, anyone on it can see the login address and "
                  "cookie and take over the console. To use it from another device, open it only on a private network "
                  "address such as Tailscale, or keep the server on its default (127.0.0.1) and connect from the other "
                  f"device through an SSH tunnel (ssh -L {port}:127.0.0.1:{port} <this PC>).",
                  soft_wrap=True)
    # On Windows the default Proactor loop can die with InvalidStateError when a browser drops a
    # connection mid-read (CPython asyncio bug), taking the whole console down; the selector loop
    # does not have that failure mode and is plenty for a local console.
    loop = "asyncio:SelectorEventLoop" if sys.platform == "win32" else "auto"
    _logging("api")
    if os.environ.get("SHIP_SUPERVISED") == "1":
        from justshipit.services import system_health
        system_health.startup_problems(system_health.owner_language())
    else:
        _print_problems()
    from justshipit.operations import notify
    threading.Thread(target=notify.watch_worker, name="worker-watch", daemon=True).start()
    log_config = access.log_config()
    # Server lines go where the app's go (the console and var/logs/api.log). Access lines stay on the console: the
    # console polls all day, and in the file they would push the errors out within hours.
    log_config["loggers"]["uvicorn"] = {"level": "INFO", "propagate": True}
    ConsoleServer(server_config(host=host, port=port, loop=loop, log_config=log_config)).run()


api_token_app = typer.Typer(no_args_is_help=True, help="The console API token (var/api_token)")
app.add_typer(api_token_app, name="api-token")


@api_token_app.command("rotate")
def api_token_rotate() -> None:
    """Replace the API token. Every console login, Bearer use and unused login link stops working."""
    from justshipit.api import access
    try:
        access.rotate_token(settings().var_dir)
    except access.TokenFileError as exc:
        con.print(f"[red]✗[/] {exc}", soft_wrap=True)
        raise typer.Exit(1) from exc
    con.print("Made a new API token. Every console login so far, every script on the old token and every unused login "
              "address is now refused. Get a new login address for the console with `uv run ship api --login`.",
              markup=False, soft_wrap=True)


def _logging(log_file: str | None = None) -> None:
    """Log to the console and, for a long-running process, to var/logs/<log_file>.log: what it did while no one
    watched. The file rotates at 10 MB and five are kept."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        path = settings().var_dir / "logs" / f"{log_file}.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(path, maxBytes=10_000_000, backupCount=5, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=handlers)


if __name__ == "__main__":
    app()
