"""The CPO's current-state report on a product the owner brought (the `product_report` job).

The CPO reads an export of the clone's HEAD (no `.git`) with Read, Glob and Grep only, in the same read-only sandbox
the acceptance check uses. Its cost is bounded by what it is handed: a repository map the server builds from git
(folders, manifests, tests, recent commits, the top of the README), 8 to 15 files to open, and a turn limit.
When the report is written, the kickoff's first turn starts with it.
"""
from __future__ import annotations

import tempfile
from collections import Counter
from pathlib import Path, PurePosixPath

from justshipit.agents import executor
from justshipit.agents.executor import MustWait, run_agent
from justshipit.contracts.owner_text import one_line
from justshipit.contracts.product import CurrentStateOut
from justshipit.db.models import Business
from justshipit.db.session import session_scope
from justshipit.engineering.worker import _export_branch
from justshipit.i18n import Lang, msg
from justshipit.sandbox import hostgit
from justshipit.sandbox.docker import SandboxUnavailable
from justshipit.services import activity, app_settings, existing_product, kickoff, queue
from justshipit.services.fence import fenced
from justshipit.settings import settings

MAX_TURNS = 40
SANDBOX_RECHECK_S = 60  # how long a report waits for a sandbox that is not there before it looks again
README_LINES = 80
README_CHARS = 8000
_MANIFESTS = {"pyproject.toml", "package.json", "requirements.txt", "setup.py", "cargo.toml", "go.mod", "gemfile",
              "pom.xml", "build.gradle", "composer.json", "dockerfile", "docker-compose.yml", "compose.yaml",
              "makefile", "alembic.ini", "vite.config.ts", "tsconfig.json"}


def _git(repo: Path, *args: str) -> str:
    p = hostgit.run(repo, *args, attr_source=False)
    return p.stdout if p.returncode == 0 else ""


def repo_map(repo: Path) -> str:
    """What the CPO reads before opening any file, built from git so it costs no model turns."""
    files = [f for f in _git(repo, "ls-files", "-z").split("\0") if f]
    dirs = Counter(str(PurePosixPath(f).parent) if "/" in f else "." for f in files)
    top = Counter(f.split("/", 1)[0] if "/" in f else "." for f in files)
    exts = Counter(PurePosixPath(f).suffix.lower() or "(none)" for f in files)
    manifests = [f for f in files if PurePosixPath(f).name.lower() in _MANIFESTS or f.startswith(".github/workflows/")
                 or (PurePosixPath(f).name.lower().startswith("dockerfile"))]
    tests = [f for f in files if existing_product._in_tests(f)]
    readme = next((f for f in files if "/" not in f and f.lower().startswith("readme")), None)
    readme_head = ""
    if readme:
        # The committed blob, never the file: a README that is a symlink would otherwise put its target in the prompt.
        lines = _git(repo, "show", f"HEAD:{readme}").splitlines()
        readme_head = "\n".join(lines[:README_LINES]) + (f"\n… ({len(lines) - README_LINES} more lines)"
                                                         if len(lines) > README_LINES else "")
    log = _git(repo, "log", "-15", "--date=short", "--format=%h %ad %s").strip()
    commits = _git(repo, "rev-list", "--count", "HEAD").strip()
    root = (_git(repo, "rev-list", "--max-parents=0", "HEAD").split() or ["HEAD"])[-1]
    first = _git(repo, "log", "-1", "--date=short", "--format=%ad", root).strip()
    todo = _git(repo, "grep", "-I", "-c", "-E", "TODO|FIXME|XXX", "HEAD", "--").splitlines()
    todos = sum(int(x.rsplit(":", 1)[1]) for x in todo if x.rsplit(":", 1)[-1].isdigit())
    parts = [
        f"{len(files)} tracked files, {commits} commits since {first}.",
        "### Top-level folders (files)\n" + "\n".join(f"- {d}/ ({n})" for d, n in sorted(top.items())),
        "### Busiest folders\n" + "\n".join(f"- {d}/ ({n})" for d, n in dirs.most_common(25)),
        "### File types\n" + ", ".join(f"{e} {n}" for e, n in exts.most_common(12)),
        "### Manifests, containers and CI\n" + ("\n".join(f"- {f}" for f in manifests[:40]) or "none")
        + (f"\n- … {len(manifests) - 40} more" if len(manifests) > 40 else ""),
        f"### Test files ({len(tests)})\n" + ("\n".join(f"- {f}" for f in tests[:60]) or "none")
        + (f"\n- … {len(tests) - 60} more" if len(tests) > 60 else ""),
        f"### TODO, FIXME and XXX markers: {todos}",
        "### Recent commits\n" + (log or "none"),
        f"### {readme or 'README'} (first {README_LINES} lines)\n" + (readme_head[:README_CHARS] or "no README"),
    ]
    return "\n\n".join(parts)


def prompt(repo: Path) -> str:
    return ("## Repository map\n" + fenced("Repository map", repo_map(repo))
            + "\n\nRead the code as the system prompt says and write the report.")


def write(repo: Path, business_id: int) -> tuple[CurrentStateOut, float]:
    """The CPO's report on the clone at products/<repo>, and what the call cost. The export it reads is deleted after."""
    with tempfile.TemporaryDirectory(prefix="ship-state-") as tmp:
        export = Path(tmp) / "repo"
        export.mkdir()
        _export_branch(repo, "HEAD", export)
        system = (settings().prompts_dir / "current_state.md").read_text(encoding="utf-8")
        out = run_agent("product", "product_report", prompt(repo), CurrentStateOut, project_id=None,
                        business_id=business_id, cwd=export, tools=["read_workspace"], max_turns=MAX_TURNS,
                        system_override=system)
    return out, executor.last_cost()


def _sandbox_down(exc: BaseException) -> SandboxUnavailable | None:
    """The missing sandbox behind `exc`: run_agent wraps it in an AgentFailed around the LLMError the runner raised."""
    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, SandboxUnavailable):
            return seen
        seen = seen.__cause__
    return None


def run(business_id: int, attempt: int = 1, max_attempts: int = 1) -> None:
    """The job: write the report once, then let the kickoff start. A last failed attempt still starts the kickoff,
    where the CEO asks briefly what the product does. A sandbox that is not there (Docker off, the egress proxy not
    started) is the PC's state, not the code's: the job waits and looks again, and the reading never fails for it."""
    lang = app_settings.language()
    with session_scope() as s:
        product = existing_product.existing(s.get(Business, business_id))
    if product is None or product.report is not None:
        return
    try:
        report, cost = write(settings().products_dir / product.repo, business_id)
    except MustWait:
        raise
    except Exception as exc:
        if (down := _sandbox_down(exc)) is not None:
            raise queue.Hold(SANDBOX_RECHECK_S, down.words(lang)) from exc
        if attempt >= max_attempts:
            _finish(business_id, None, None, lang)
        raise
    _finish(business_id, report, cost, lang)


def _finish(business_id: int, report: CurrentStateOut | None, cost: float | None, lang: Lang) -> None:
    with session_scope() as s:
        settle(s, business_id, report, cost, lang)


def settle(s, business_id: int, report: CurrentStateOut | None, cost: float | None, lang: Lang) -> None:
    """Keep the report, or mark it failed when there is none, then start the kickoff. Once only: a report that is
    already in, or already failed, stays as it is."""
    b = s.get(Business, business_id, with_for_update=True)
    product = existing_product.existing(b)
    if product is None or product.report_state != "reading":
        return
    b.product = product.model_copy(update={"report": report, "report_cost_usd": cost,
                                           "report_failed": report is None}).model_dump(mode="json")
    state_line = None
    if report is not None:
        if not b.one_liner.strip():
            b.one_liner = one_line(report.what_it_does, 200)
        state_line = msg("product.brief.state", lang, summary=report.summary)
        activity.record(s, verb="product_reported", entity="business", entity_id=business_id, project_id=None,
                        business_id=business_id, summary=msg("product.activity.reported", lang, repo=product.repo))
    kickoff.report_ready(s, business_id, state_line=state_line)
