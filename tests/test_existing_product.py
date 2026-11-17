"""A product the owner already has: the import's refusals and secret scan, a clone that can never reach the owner's
folder, the pin from the start, the CPO's current-state report, the kickoff that starts from it, the stages that
read it, and the owner-facing words in both languages."""
import os
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from justshipit.api.app import app
from justshipit.contracts.product import CurrentStateOut, ExistingProduct
from justshipit.db.models import Business, DevJob, Job, KickoffSession, Project, Spec
from justshipit.db.session import session_scope
from justshipit.engineering import current_state, worker
from justshipit.i18n import msg
from justshipit.i18n.said import Refusal
from justshipit.orchestration import context
from justshipit import runtime
from justshipit.runtime import claude_cli
from justshipit.sandbox import docker
from justshipit.services import existing_product, kickoff, matters, queue, roadmap
from justshipit.workers import runner
from justshipit.settings import settings
from fastapi.testclient import TestClient
from sqlalchemy import select
from story_kit import JOURNEYS
from story_kit import P1 as P1_STORIES

pytestmark = pytest.mark.usefixtures("strengths_answered")

HANGUL = re.compile(r"[가-힣]")
FAKE_KEY = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0000\n-----END RSA PRIVATE KEY-----\n"
FAKE_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit(repo: Path, msg: str = "work") -> None:
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "-c", "user.name=o", "-c", "user.email=o@x", "commit", "-qm", msg)


def owner_repo(root: Path, name: str = "my-app", files: dict[str, str] | None = None) -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    sh(repo, "git", "init", "-q", "-b", "main")
    for rel, text in {"README.md": "# My app\nIt books rooms.\n", ".gitignore": ".env\nvar/\n",
                      "app.py": "print('hi')\n", **(files or {})}.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    commit(repo, "init")
    return repo


def refusal(fn, *args) -> Refusal:
    with pytest.raises(Refusal) as exc:
        fn(*args)
    return exc.value


def report(**over) -> CurrentStateOut:
    base = {
        "summary": "A room-booking web app that works end to end but has no sign-up yet.",
        "for_owner": "You can show it to people now. Sign-up is the one thing missing before strangers can use it.",
        "what_it_does": "People book a meeting room and see who has it.",
        "features": [{"name": "Book a room", "state": "works", "evidence": "app.py"}],
        "stack": ["Python"], "tests": {"state": "few", "summary": "One smoke test.", "evidence": "tests/"},
        "risks": [{"text": "No backups of the bookings.", "evidence": "app.py"}],
        "unfinished": [{"text": "Sign-up is a stub.", "evidence": "app.py"}],
        "readme_vs_reality": [{"claim": "Books rooms", "verdict": "holds", "reality": "It does.", "evidence": "app.py"}],
        "files_read": ["README.md", "app.py"]}
    return CurrentStateOut.model_validate({**base, **over})


@pytest.fixture
def products(tmp_path, monkeypatch):
    path = tmp_path / "products"
    monkeypatch.setattr(settings(), "products_dir", path)
    return path


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000", headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


# ------------------------------------------------------------------ refusals

def test_refuses_what_is_not_a_repository_top_with_what_to_give_instead(tmp_path, products):
    repo = owner_repo(tmp_path / "owner")
    (repo / "src").mkdir()
    plain = tmp_path / "plain"
    plain.mkdir()
    empty = tmp_path / "empty"
    empty.mkdir()
    sh(empty, "git", "init", "-q")
    cases = {"": "need_source", "https://github.com/acme/app": "github_not_live", "git@github.com:acme/app.git":
             "github_not_live", "relative/app": "not_absolute", str(tmp_path / "nowhere"): "not_found",
             str(plain): "not_git", str(repo / "src"): "not_top", str(empty): "no_commits"}
    for given, key in cases.items():
        assert refusal(existing_product.parse_source, given, products).what.key == f"product.import.{key}", given
    assert existing_product.parse_source(f'"{repo}"', products).repo == "my-app"


def test_refuses_a_folder_inside_products(tmp_path, products):
    inner = owner_repo(products, "already-here")
    assert refusal(existing_product.parse_source, str(inner), products).what.key == "product.import.inside_products"


# ------------------------------------------------------------------ the secret scan

@pytest.mark.parametrize("rel, text, kind", [
    (".env", "DB_PASSWORD=hunter2\n", "env_file"),
    ("config/.env.production", "X=1\n", "env_file"),
    ("deploy/id_rsa", FAKE_KEY, "private_key"),
    ("src/settings.py", f"TOKEN = '{FAKE_TOKEN}'\n", "github_token"),
])
def test_a_secret_in_a_tracked_file_refuses_the_import_naming_the_file_never_the_secret(tmp_path, products, rel,
                                                                                        text, kind):
    repo = owner_repo(tmp_path / "owner", files={rel: text})
    if rel == ".env":
        sh(repo, "git", "add", "-f", ".env")
        commit(repo, "oops")
    exc = refusal(existing_product.clone, existing_product.parse_source(str(repo), products), products)
    assert exc.what.vars["kind"].key == f"product.secret.{kind}"
    for lang in ("en", "ko"):
        words = exc.words(lang)
        assert rel in words
        assert "hunter2" not in words and FAKE_TOKEN not in words and "MIIE" not in words
    assert not (products / "my-app").exists()
    assert [p.name for p in products.iterdir()] == []  # the staging copy is gone too


def test_test_fixtures_and_env_templates_are_not_secrets(tmp_path):
    repo = owner_repo(tmp_path, files={"tests/fixtures/server.key": FAKE_KEY, "src/test_auth.py": f"T = '{FAKE_TOKEN}'\n",
                                       ".env.example": "DB_PASSWORD=\n", "web/login.spec.ts": FAKE_TOKEN})
    assert existing_product.scan_secrets(repo) == []


def test_the_refusal_names_the_line_and_counts_the_rest(tmp_path, products):
    repo = owner_repo(tmp_path / "owner", files={"a.py": f"x = 1\nT = '{FAKE_TOKEN}'\n", "b.py": FAKE_KEY})
    exc = refusal(existing_product.clone, existing_product.parse_source(str(repo), products), products)
    assert "a.py, line 2" in exc.words("en") and "(and 1 more place)" in exc.words("en")
    assert "a.py 2번째 줄" in exc.words("ko") and "외 1곳" in exc.words("ko")


# ------------------------------------------------------------------ the clone

def test_the_clone_copies_tracked_history_only_and_cannot_reach_the_owner_folder(tmp_path, products):
    repo = owner_repo(tmp_path / "owner")
    (repo / ".env").write_text("SECRET=1\n")            # ignored
    (repo / "var").mkdir()
    (repo / "var" / "data.db").write_text("rows")       # ignored
    (repo / "notes.txt").write_text("draft")            # untracked
    (repo / "app.py").write_text("print('changed')\n")  # uncommitted change
    before = (sh(repo, "git", "status", "--porcelain"), sh(repo, "git", "branch", "-a"), sh(repo, "git", "rev-parse", "HEAD"))

    product = existing_product.clone(existing_product.parse_source(str(repo), products), products)

    clone = products / "my-app"
    assert product == ExistingProduct(repo="my-app", source=str(repo), base_branch="main",
                                      commit=sh(repo, "git", "rev-parse", "--short", "HEAD"))
    assert not (clone / ".env").exists() and not (clone / "var").exists() and not (clone / "notes.txt").exists()
    assert (clone / "app.py").read_text() == "print('hi')\n"
    assert sh(clone, "git", "remote") == ""  # no origin: nothing the company pushes or merges can land in the owner's repo
    assert sh(clone, "git", "config", "--get", existing_product.SOURCE_KEY) == str(repo.resolve())
    objects = [p for p in (clone / ".git" / "objects").rglob("*") if p.is_file()]
    assert objects and all(os.stat(p).st_nlink == 1 for p in objects)  # --no-hardlinks
    assert (sh(repo, "git", "status", "--porcelain"), sh(repo, "git", "branch", "-a"),
            sh(repo, "git", "rev-parse", "HEAD")) == before


def test_importing_the_same_folder_again_reuses_the_clone_and_catches_up(tmp_path, products):
    repo = owner_repo(tmp_path / "owner")
    src = existing_product.parse_source(str(repo), products)
    first = existing_product.clone(src, products)
    (repo / "more.py").write_text("x = 2\n")
    commit(repo, "more")
    again = existing_product.clone(src, products)
    assert again.commit != first.commit and (products / "my-app" / "more.py").exists()


def test_a_same_named_folder_from_elsewhere_is_never_overwritten(tmp_path, products):
    existing_product.clone(existing_product.parse_source(str(owner_repo(tmp_path / "a")), products), products)
    other = existing_product.parse_source(str(owner_repo(tmp_path / "b")), products)
    assert refusal(existing_product.clone, other, products).what.key == "product.import.taken"


# ------------------------------------------------------------------ pinning and the kickoff

def imported(api, repo: Path, **body) -> dict:
    r = api.post("/api/businesses/import", json={"source": str(repo), **body})
    assert r.status_code == 200, r.text
    return r.json()


def test_import_pins_the_repo_from_the_start_and_waits_for_the_report(tmp_path, products, api):
    repo = owner_repo(tmp_path / f"owner-{uuid.uuid4().hex[:6]}", name=f"booker-{uuid.uuid4().hex[:6]}")
    v = imported(api, repo, name="Booker")
    bid = v["session"]["business_id"]
    assert v["product"]["folder"] == f"products/{repo.name}" and v["product"]["report_state"] == "reading"
    background = next(sec for sec in v["brief"]["sections"] if sec["key"] == "background")["lines"]
    assert background[0]["provenance"] == "said" and f"products/{repo.name}" in background[0]["text"]
    with session_scope() as s:
        b = s.get(Business, bid)
        assert existing_product.existing(b).repo == repo.name
        kinds = [j.kind for j in s.scalars(select(Job).where(Job.payload["business_id"].astext == str(bid)))]
        assert kinds == ["product_report"]  # no idea panel, and the first turn waits for the report
        assert not s.scalars(select(Job).where(Job.kind == "kickoff_turn",
                                               Job.payload["session_id"].astext == str(v["session"]["id"]))).all()
        p = Project(name="Booker", slug=f"booker-{bid}", goal="launch", budget_usd=5, business_id=bid)
        s.add(p)
        s.flush()
        assert matters.product_repo(s, p.id) == repo.name

    r = api.post("/api/businesses/import", json={"source": str(repo)})
    assert r.status_code == 409 and f"B-{bid}" in r.json()["detail"]


def test_an_import_refusal_reaches_the_owner_in_their_words_and_creates_nothing(tmp_path, products, api,
                                                                                korean_owner):
    repo = owner_repo(tmp_path / "owner", name=f"leaky-{uuid.uuid4().hex[:6]}", files={"key.pem": FAKE_KEY})
    with session_scope() as s:
        before = s.scalar(select(Business.id).order_by(Business.id.desc()).limit(1))
    r = api.post("/api/businesses/import", json={"source": str(repo)})
    assert r.status_code == 409
    assert "key.pem" in r.json()["detail"] and HANGUL.search(r.json()["detail"]) and "MIIE" not in r.json()["detail"]
    with session_scope() as s:
        assert s.scalar(select(Business.id).order_by(Business.id.desc()).limit(1)) == before


# ------------------------------------------------------------------ the report

@pytest.fixture(autouse=True)
def no_report_jobs_from_other_tests():
    """A worker claims the oldest queued job of its kind, so the ones earlier tests left would be claimed first."""
    with session_scope() as s:
        for job in s.scalars(select(Job).where(Job.kind == "product_report", Job.status == "queued")):
            job.status = "done"


@pytest.fixture
def business(tmp_path, products):
    repo = owner_repo(tmp_path / "owner", name=f"rooms-{uuid.uuid4().hex[:6]}")
    product = existing_product.clone(existing_product.parse_source(str(repo), products), products)
    with session_scope() as s:
        v = kickoff.open_session(s, name="Rooms", one_liner="", product=product, lang="en")
    return v["session"]["business_id"], v["session"]["id"]


def test_the_report_is_read_from_a_read_only_export_then_the_kickoff_starts(business, monkeypatch):
    bid, sid = business
    seen = {}

    def cpo(agent, purpose, prompt, model, *, project_id, business_id, cwd, tools, max_turns, system_override):
        seen.update(agent=agent, purpose=purpose, business_id=business_id, tools=tools,
                    files=sorted(p.name for p in cwd.iterdir()),
                    prompt=prompt, system=system_override)
        return report()
    monkeypatch.setattr(current_state, "run_agent", cpo)
    current_state.run(bid)

    assert (seen["agent"], seen["purpose"], seen["tools"]) == ("product", "product_report", ["read_workspace"])
    assert seen["business_id"] == bid, "the report's cost counts toward the business"
    assert ".git" not in seen["files"] and "app.py" in seen["files"]
    assert "Repository map" in seen["prompt"] and "It books rooms." in seen["prompt"]
    assert "between 8 and 15 files" in seen["system"]
    with session_scope() as s:
        product = existing_product.existing(s.get(Business, bid))
        assert product.report_state == "ready" and product.report.summary.startswith("A room-booking")
        assert s.get(Business, bid).one_liner == "People book a meeting room and see who has it."
        background = roadmap.lines_by_key(roadmap.draft_brief(s, bid).sections)["background"]
        assert [x["provenance"] for x in background] == ["said", "guess"]
        assert s.scalar(select(Job).where(Job.kind == "kickoff_turn", Job.payload["session_id"].astext == str(sid)))
    current_state.run(bid)  # a rerun after the report is in does nothing


def test_a_report_that_fails_twice_still_starts_the_kickoff(business, monkeypatch):
    bid, sid = business
    monkeypatch.setattr(current_state, "run_agent", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("cli died")))
    with pytest.raises(RuntimeError):
        current_state.run(bid, attempt=1, max_attempts=2)
    with session_scope() as s:
        assert existing_product.existing(s.get(Business, bid)).report_state == "reading"
    with pytest.raises(RuntimeError):
        current_state.run(bid, attempt=2, max_attempts=2)
    with session_scope() as s:
        assert existing_product.existing(s.get(Business, bid)).report_state == "failed"
        assert s.scalar(select(Job).where(Job.kind == "kickoff_turn", Job.payload["session_id"].astext == str(sid)))


def work_the_report_job(bid: int) -> int:
    """A worker takes the product_report job and runs it as the real handler does. Returns the job's id."""
    with session_scope() as s:
        claimed = queue.claim(s, "worker-test", ["product_report"])
        assert claimed is not None and claimed.payload["business_id"] == bid
        jid, payload, token = claimed.id, dict(claimed.payload), claimed.lease_token
    runner._execute(jid, "product_report", payload, runner._handlers(), runner.Runs(),
                    queue.Lease(job_id=jid, token=token))
    return jid


@pytest.fixture
def sandbox_down(monkeypatch):
    """The sandbox refuses the CPO's call the way docker.run does when the egress proxy is not running."""
    def refuse(spec, **kw):
        raise docker.SandboxUnavailable("build.sandbox.egress_down", proxy=docker.EGRESS_PROXY)
    claude = claude_cli.ClaudeRunner.__new__(claude_cli.ClaudeRunner)
    claude.bin = "claude"
    monkeypatch.setattr(runtime, "runner_for", lambda provider: claude)
    monkeypatch.setattr(claude_cli.docker, "run", refuse)
    monkeypatch.setattr(claude_cli.host_auth, "access_token", lambda **kw: "token")


def test_a_sandbox_that_is_down_holds_the_report_job_instead_of_failing_the_reading(business, sandbox_down):
    bid, sid = business
    jid = work_the_report_job(bid)
    with session_scope() as s:
        job = s.get(Job, jid)
        assert (job.status, job.attempts) == ("queued", 0), "the attempt is returned: nothing failed"
        assert "docker compose up -d egress" in job.last_error
        assert job.run_after > datetime.now(timezone.utc)
        assert existing_product.existing(s.get(Business, bid)).report_state == "reading"
        assert not s.scalars(select(Job).where(Job.kind == "kickoff_turn",
                                               Job.payload["session_id"].astext == str(sid))).all()


def test_a_held_report_is_written_when_the_sandbox_is_back(business, sandbox_down, monkeypatch):
    bid, sid = business
    jid = work_the_report_job(bid)
    monkeypatch.setattr(current_state, "run_agent", lambda *a, **k: report())  # the sandbox is back
    with session_scope() as s:
        s.get(Job, jid).run_after = datetime.now(timezone.utc)
    assert work_the_report_job(bid) == jid
    with session_scope() as s:
        assert (s.get(Job, jid).status, s.get(Job, jid).attempts) == ("done", 1)
        assert existing_product.existing(s.get(Business, bid)).report_state == "ready"
        assert s.scalar(select(Job).where(Job.kind == "kickoff_turn", Job.payload["session_id"].astext == str(sid)))


def test_a_reading_held_up_by_the_sandbox_says_why_in_the_owners_language(business, monkeypatch):
    bid, sid = business
    down = docker.SandboxUnavailable("build.sandbox.egress_down", proxy=docker.EGRESS_PROXY)
    monkeypatch.setattr(docker, "unavailable", lambda *a, **kw: down)

    def waiting(lang):
        with session_scope() as s:
            return kickoff.view(s, sid, lang=lang)["product"]["waiting"]
    assert waiting("en") == down.words("en") and "docker compose up -d egress" in waiting("en")
    assert HANGUL.search(waiting("ko"))
    monkeypatch.setattr(docker, "unavailable", lambda *a, **kw: None)
    assert waiting("en") is None, "a sandbox that is there holds nothing up"


@pytest.mark.parametrize("state", ["ready", "failed"])
def test_only_a_reading_waits_on_the_sandbox(business, monkeypatch, state):
    bid, sid = business
    monkeypatch.setattr(docker, "unavailable",
                        lambda *a, **kw: docker.SandboxUnavailable("build.sandbox.docker_down"))
    monkeypatch.setattr(current_state, "run_agent", lambda *a, **k: report())
    if state == "ready":
        current_state.run(bid)
    else:
        fail_the_reading(bid)
    with session_scope() as s:
        product = kickoff.view(s, sid, lang="en")["product"]
    assert product["report_state"] == state and product["waiting"] is None


def test_the_project_manager_kickoff_reads_the_report_as_the_cpos_not_the_owners(business, monkeypatch):
    bid, sid = business
    monkeypatch.setattr(current_state, "run_agent", lambda *a, **k: report())
    current_state.run(bid)
    with session_scope() as s:
        sess = s.get(KickoffSession, sid)
        b = s.get(Business, bid)
        prompt = kickoff._prompt(sess, b, roadmap.draft_brief(s, bid).sections, [], 1, False, "en",
                                 product=existing_product.existing(b))
        words = kickoff.owner_words(sess, b, [])
    assert "## The product as it is today" in prompt and "Sign-up is a stub." in prompt
    assert "Sign-up is a stub." not in words


# ------------------------------------------------------------------ the rest of the company

def test_every_agent_and_the_merge_card_know_the_product_exists(business, monkeypatch):
    bid, _ = business
    monkeypatch.setattr(current_state, "run_agent", lambda *a, **k: report())
    current_state.run(bid)
    with session_scope() as s:
        p = Project(name="Rooms", slug=f"rooms-p{bid}", goal="launch globally", budget_usd=5, business_id=bid)
        s.add(p)
        s.flush()
        brief = context.project_brief(s, p)
        assert "The product as it is today" in brief and "never a first version" in brief
        repo = existing_product.existing(s.get(Business, bid)).repo
        spec = Spec(spec_key=f"SPEC-R{bid}", project_id=p.id, title="Sign-up", registry_path="x", target_repository=repo)
        s.add(spec)
        s.flush()
        job = DevJob(spec_id=spec.id, branch="ai/spec-r-a1", result={"acceptance": {"accepted": True}})
        s.add(job)
        s.flush()
        card = worker._request_merge(s, job, spec, "Approve the merge", "detail", "en").summary
    assert f"This changes the company's copy in products/{repo}. Your original folder isn't touched" in card
    assert f"products/{repo}에 있는 회사 사본을 바꿔요" in msg("build.merge_card.copy", "ko", repo=repo)


def test_an_idea_business_reads_no_existing_product(s, project):
    assert existing_product.of_project(s, project.id) is None
    assert "The product as it is today" not in context.project_brief(s, project)


# ------------------------------------------------------------------ the owner's words

def test_every_import_refusal_reads_in_each_language():
    from justshipit.i18n import catalog
    keys = [k for k in catalog() if k.startswith(("product.import.", "product.secret.", "product.brief."))]
    assert len(keys) > 20
    for key in keys:
        en, ko = catalog()[key]["en"], catalog()[key]["ko"]
        en = en if isinstance(en, str) else en["other"]
        assert not HANGUL.search(en), key
        assert HANGUL.search(ko), key


def test_the_repository_map_never_follows_a_symlinked_readme(tmp_path):
    secret = tmp_path / "outside.txt"
    secret.write_text("PRIVATE NOTES OUTSIDE THE REPO\n")
    repo = owner_repo(tmp_path, name="linked", files={"README.md": "x"})
    (repo / "README.md").unlink()
    (repo / "README.md").symlink_to(secret)
    commit(repo, "link the readme")
    assert "PRIVATE NOTES" not in current_state.repo_map(repo)


def test_the_repository_map_dates_the_first_commit(tmp_path):
    repo = owner_repo(tmp_path)
    sh(repo, "git", "-c", "user.name=o", "-c", "user.email=o@x", "commit", "-q", "--allow-empty", "-m", "later",
       "--date=2030-01-01T00:00:00")
    first = sh(repo, "git", "log", "-1", "--date=short", "--format=%ad", sh(repo, "git", "rev-list", "--max-parents=0", "HEAD"))
    assert f"2 commits since {first}." in current_state.repo_map(repo)


def test_a_report_item_that_cites_a_file_the_cpo_never_opened_is_dropped():
    r = report(files_read=["app.py"],
               features=[{"name": "Book a room", "state": "works", "evidence": "app.py: book()"},
                         {"name": "Billing", "state": "partial", "evidence": "billing.py"}],
               risks=[{"text": "No backups.", "evidence": "db.py"}],
               readme_vs_reality=[{"claim": "Books rooms", "verdict": "holds", "reality": "It does.",
                                   "evidence": "README.md; app.py"},
                                  {"claim": "Sends invoices", "verdict": "partly", "reality": "Unread.",
                                   "evidence": "billing.py"}])
    assert [f.name for f in r.features] == ["Book a room"]
    assert r.risks == [] and [c.claim for c in r.readme_vs_reality] == ["Books rooms"]


def test_a_one_letter_file_name_grounds_nothing():
    r = report(files_read=["a"], features=[{"name": "Book a room", "state": "works", "evidence": "app.py"}])
    assert r.features == []


def test_the_repository_map_stays_bounded_for_a_long_readme(tmp_path):
    repo = owner_repo(tmp_path, files={"README.md": "\n".join("x" * 400 for _ in range(200))})
    assert len(current_state.repo_map(repo)) < current_state.README_CHARS + 4000


# ------------------------------------------------------------------ what a clone may carry, and a job that dies

def test_a_repository_off_any_branch_is_refused_so_delivery_has_a_branch_to_land_on(tmp_path, products):
    repo = owner_repo(tmp_path / "owner")
    sh(repo, "git", "checkout", "-q", "--detach")
    assert refusal(existing_product.parse_source, str(repo), products).what.key == "product.import.detached"


def test_a_secret_taken_out_of_head_but_left_in_history_still_refuses_naming_the_commit(tmp_path, products):
    repo = owner_repo(tmp_path / "owner", files={"conf.py": f"T = '{FAKE_TOKEN}'\n"})
    leaked = sh(repo, "git", "rev-parse", "--short", "HEAD")
    (repo / "conf.py").write_text("T = ''\n")
    commit(repo, "take the token out")
    exc = refusal(existing_product.clone, existing_product.parse_source(str(repo), products), products)
    assert exc.what.vars["kind"].key == "product.secret.github_token"
    assert f"conf.py, in commit {leaked}" in exc.words("en") and FAKE_TOKEN not in exc.words("en")
    assert [p.name for p in products.iterdir()] == []


def test_a_deleted_env_file_in_history_refuses(tmp_path, products):
    repo = owner_repo(tmp_path / "owner", files={".env": "DB_PASSWORD=hunter2\n"})
    sh(repo, "git", "add", "-f", ".env")
    commit(repo, "oops")
    sh(repo, "git", "rm", "-q", "--cached", ".env")
    commit(repo, "untrack")
    exc = refusal(existing_product.clone, existing_product.parse_source(str(repo), products), products)
    assert exc.what.vars["kind"].key == "product.secret.env_file" and "hunter2" not in exc.words("en")


def test_layered_env_templates_pass_and_pgp_keys_do_not(tmp_path):
    pgp = "-----BEGIN PGP PRIVATE KEY BLOCK-----\nlQOYBF0000\n-----END PGP PRIVATE KEY BLOCK-----\n"
    repo = owner_repo(tmp_path, files={".env.local.example": "X=\n", "deploy/signing.asc": pgp})
    assert [(h.path, h.kind) for h in existing_product.scan_secrets(repo)] == [("deploy/signing.asc", "private_key")]


def test_a_scan_that_cannot_run_refuses_instead_of_passing(tmp_path):
    repo = owner_repo(tmp_path)
    assert refusal(existing_product.scan_secrets, repo, "no-such-rev").what.key == "product.import.scan_failed"


def test_catching_up_refuses_a_new_secret_and_keeps_the_clone_where_it_was(tmp_path, products):
    repo = owner_repo(tmp_path / "owner")
    src = existing_product.parse_source(str(repo), products)
    before = existing_product.clone(src, products).commit
    (repo / "conf.py").write_text(f"T = '{FAKE_TOKEN}'\n")
    commit(repo, "leak")
    assert refusal(existing_product.clone, src, products).what.key == "product.import.secret"
    assert sh(products / "my-app", "git", "rev-parse", "--short", "HEAD") == before
    assert not (products / "my-app" / "conf.py").exists()


def test_a_report_job_the_queue_gives_up_on_still_starts_the_kickoff(business):
    from justshipit.services import queue
    bid, sid = business

    def report_job(s):
        return s.scalar(select(Job).where(Job.kind == "product_report", Job.payload["business_id"].astext == str(bid)))

    def turns(s):
        return s.scalars(select(Job).where(Job.kind == "kickoff_turn", Job.payload["session_id"].astext == str(sid))).all()
    with session_scope() as s:
        queue.fail_subject(s, report_job(s))
    with session_scope() as s:
        assert existing_product.existing(s.get(Business, bid)).report_state == "failed"
        assert len(turns(s)) == 1
        queue.fail_subject(s, report_job(s))  # once only: a second give-up starts no second turn
    with session_scope() as s:
        assert len(turns(s)) == 1


def fail_the_reading(bid: int) -> None:
    """The report job runs out of attempts, as the queue records it."""
    with session_scope() as s:
        job = queue.claim(s, "worker-test", ["product_report"])
        assert job is not None and job.payload["business_id"] == bid
        queue.finish(s, job.id, job.lease_token, "the CPO could not write it", final=True)


def turn_jobs(s, sid: int) -> list[Job]:
    return list(s.scalars(select(Job).where(Job.kind == "kickoff_turn", Job.payload["session_id"].astext == str(sid))))


def report_jobs(s, bid: int) -> list[Job]:
    return list(s.scalars(select(Job).where(Job.kind == "product_report",
                                            Job.payload["business_id"].astext == str(bid)).order_by(Job.id)))


def test_agents_of_a_product_nobody_could_read_are_not_sent_to_read_it(business):
    bid, _ = business
    fail_the_reading(bid)
    with session_scope() as s:
        text = existing_product.render(existing_product.existing(s.get(Business, bid)))
    for told in ("no agent can open the code until it is written", "fetch it from GitHub", "make it public",
                 "read again from the kickoff screen", "ask the owner briefly what it does"):
        assert told.lower() in text.lower(), told


def test_the_owner_can_have_a_failed_reading_started_again_once(business, api):
    bid, sid = business
    assert api.post(f"/api/kickoff/{sid}/read-again").status_code == 409, "still reading"
    fail_the_reading(bid)
    first = api.post(f"/api/kickoff/{sid}/read-again")
    assert first.status_code == 200 and first.json()["product"]["report_state"] == "reading"
    assert api.post(f"/api/kickoff/{sid}/read-again").status_code == 409, "a second click starts nothing"
    with session_scope() as s:
        assert [(j.status, j.idempotency_key) for j in report_jobs(s, bid)] == [
            ("failed", f"product_report:{bid}"), ("queued", f"product_report:{bid}:1")]


def test_a_reading_that_came_in_cannot_be_read_again(business, api, monkeypatch):
    bid, sid = business
    monkeypatch.setattr(current_state, "run_agent", lambda *a, **k: report())
    current_state.run(bid)
    assert api.post(f"/api/kickoff/{sid}/read-again").status_code == 409


@pytest.mark.parametrize("chat", ["thinking", "waiting"])
def test_a_report_that_lands_after_the_chat_started_updates_the_plan_and_starts_nothing(business, api, monkeypatch, chat):
    bid, sid = business
    fail_the_reading(bid)  # the chat starts without a report
    if chat == "waiting":
        with session_scope() as s:
            sess = s.get(KickoffSession, sid)
            sess.status, sess.turns_used = "WAITING_OWNER", 1
    assert api.post(f"/api/kickoff/{sid}/read-again").status_code == 200
    monkeypatch.setattr(current_state, "run_agent", lambda *a, **k: report())
    with session_scope() as s:
        before = len(turn_jobs(s, sid))
    assert before == 1
    current_state.run(bid)
    with session_scope() as s:
        assert existing_product.existing(s.get(Business, bid)).report_state == "ready"
        assert len(turn_jobs(s, sid)) == 1, "the chat is started once"
        background = roadmap.lines_by_key(roadmap.draft_brief(s, bid).sections)["background"]
        assert [x["provenance"] for x in background] == ["said", "guess"], "the plan says where the product stands"
        assert s.get(KickoffSession, sid).status == ("OPEN" if chat == "thinking" else "WAITING_OWNER")


def test_the_old_job_giving_up_does_not_fail_the_reading_the_owner_asked_for(business, api):
    bid, sid = business
    with session_scope() as s:
        [old] = report_jobs(s, bid)
        old_id = old.id
    fail_the_reading(bid)
    assert api.post(f"/api/kickoff/{sid}/read-again").status_code == 200
    with session_scope() as s:
        queue.fail_subject(s, s.get(Job, old_id))
    with session_scope() as s:
        assert existing_product.existing(s.get(Business, bid)).report_state == "reading"


# ------------------------------------------------------------------ an imported product with the owner's files

def test_an_imported_product_with_a_plan_file_reads_both_but_only_the_file_is_the_owners_word(tmp_path, products,
                                                                                               api, monkeypatch):
    from test_documents import PLAN_PAGE_2, uploaded
    from test_kickoff import ScriptedProjectManager, lines, out, view
    repo = owner_repo(tmp_path / f"owner-{uuid.uuid4().hex[:6]}", name=f"planned-{uuid.uuid4().hex[:6]}")
    plan = uploaded(api, "plan.pdf")
    v = imported(api, repo, name="Planned", documents=[plan["id"]])
    bid, sid = v["session"]["business_id"], v["session"]["id"]
    assert [(d["name"], d["after_turn"]) for d in v["documents"]] == [("plan.pdf", 0)]
    monkeypatch.setattr(current_state, "run_agent", lambda *a, **k: report())
    current_state.run(bid)

    project_manager = ScriptedProjectManager().says(out(section="path", patch=[
        ("goal_deadline", "6주 안에 유료 고객 10명", "said", PLAN_PAGE_2),
        ("background", "가입 기능은 아직 비어 있어요", "said", "Sign-up is a stub.")]))
    monkeypatch.setattr(kickoff, "run_agent", project_manager)
    kickoff.run_turn(sid, 1)

    [prompt] = project_manager.prompts
    assert "## The product as it is today" in prompt and "Sign-up is a stub." in prompt
    assert "### plan.pdf (PDF, 2 pages, attached with the first words)" in prompt and PLAN_PAGE_2 in prompt
    after = view(api, sid)
    [goal] = lines(after, "goal_deadline")
    assert (goal["provenance"], goal["source"]) == ("said", f"doc:{plan['id']}:p2")
    assert [x["provenance"] for x in lines(after, "background") if x["text"] == "가입 기능은 아직 비어 있어요"] == ["guess"]
    with session_scope() as s:
        p = Project(name="Planned", slug=f"planned-p{bid}", goal="launch", budget_usd=5, business_id=bid)
        s.add(p)
        s.flush()
        brief = context.project_brief(s, p)
    assert "### The owner's files" in brief and PLAN_PAGE_2 in brief
    assert "The product as it is today" in brief and "Sign-up is a stub." in brief


# ------------------------------------------------------------------ the spec for an imported product

def _spec_out():
    from justshipit.contracts.handoff import RequirementOut, SpecDraftOut
    return SpecDraftOut(
        title="English copy", prd_markdown="Add English to src/copy.ts.", design_notes_markdown="d",
        requirements=[RequirementOut(id="REQ-1", statement="English copy", priority="must", rationale="r")],
        acceptance_criteria=["Given English, Then every string reads in English"], owner_trace=[], design_brief=None,
        suggested_target_repository="ignored", suggested_test_command="npm test", suggested_test_image="node:22",
        user_stories=P1_STORIES, journeys=JOURNEYS)


def _build_request(s, project_id: int) -> int:
    from justshipit.db.models import AgentRequest
    req = AgentRequest(project_id=project_id, from_agent="product", type="DEVELOPMENT", title="English copy",
                       summary="s", rationale="r", status="APPROVED", policy_mode="auto",
                       action_kind="dev.implementation_start", action_params={"need": {"title": "English copy"}})
    s.add(req)
    s.flush()
    return req.id


def test_the_spec_writer_for_an_imported_product_reads_its_code_in_a_read_only_export(business, monkeypatch):
    """[평가3] Alex: the spec writer had web tools only ("The spec author could not read the live source files") and
    invented src/i18n.ts beside the product's own src/copy.ts."""
    from justshipit.contracts.handoff import Handoff
    from justshipit.engineering import specs
    bid, _ = business
    monkeypatch.setattr(current_state, "run_agent", lambda *a, **k: report())
    current_state.run(bid)
    with session_scope() as s:
        product = existing_product.existing(s.get(Business, bid))
        repo = product.repo
        p = Project(name="Rooms", slug=f"rooms-spec-{bid}", goal="launch", budget_usd=5, business_id=bid)
        s.add(p)
        s.flush()
        pid, rid = p.id, _build_request(s, p.id)
    clone = settings().products_dir / repo
    assert sh(clone, "git", "symbolic-ref", "--short", "HEAD") == product.base_branch
    (clone / "env").symlink_to("/proc/self/environ")  # a committed link out of the export, to the call's own token
    sh(clone, "git", "add", "env")
    sh(clone, "git", "-c", "user.name=o", "-c", "user.email=o@x", "commit", "-qm", "link")
    seen = {}

    def cpo(agent, purpose, prompt, model, **kw):
        seen.update(purpose=purpose, prompt=prompt, tools=kw.get("tools"),
                    links=[x for x in kw["cwd"].rglob("*") if x.is_symlink()] if kw.get("cwd") else None,
                    files=sorted(x.name for x in kw["cwd"].iterdir()) if kw.get("cwd") else None,
                    app=(kw["cwd"] / "app.py").read_text() if kw.get("cwd") else None)
        return _spec_out()
    monkeypatch.setattr(specs, "run_agent", cpo)
    spec_id = specs.write_spec(pid, rid)

    assert seen["purpose"] == "spec" and seen["tools"] == ["read_workspace"], "code to read, and no web egress beside it"
    assert seen["files"] is not None and "app.py" in seen["files"] and ".git" not in seen["files"]
    assert seen["links"] == [], "no link takes a Read out of the export"
    assert seen["app"] == "print('hi')\n"
    assert "## This product's code" in seen["prompt"] and "- ./ (" in seen["prompt"], "the repository map leads"
    with session_scope() as s:
        h = Handoff.model_validate(s.get(Spec, spec_id).handoff)
    assert h.target.repository == repo and h.workflow.existing_code is True


def test_the_first_spec_for_an_imported_product_keeps_its_look_stack_and_scope(business, monkeypatch):
    """[평가3] Alex: the imported product grew beyond what he asked. Its constitution says what every version keeps,
    and the engineer, the reviewer and the acceptance check read it beside the spec."""
    from justshipit.engineering import constitution, specs
    from justshipit.services import registry
    bid, _ = business
    monkeypatch.setattr(current_state, "run_agent", lambda *a, **k: report())
    current_state.run(bid)
    with session_scope() as s:
        repo = existing_product.existing(s.get(Business, bid)).repo
        p = Project(name="Rooms", slug=f"rooms-rules-{bid}", goal="launch", budget_usd=5, business_id=bid)
        s.add(p)
        s.flush()
        pid, rid = p.id, _build_request(s, p.id)
    monkeypatch.setattr(specs, "run_agent", lambda *a, **kw: _spec_out())
    spec_id = specs.write_spec(pid, rid)

    with session_scope() as s:
        spec = s.get(Spec, spec_id)
        proposed = constitution.in_spec(spec.registry_path, spec.draft_commit_sha)
        written = registry.read_at(f"{spec.registry_path}/{constitution.FILE}", spec.draft_commit_sha)
    rules = [r["rule"] for r in proposed["principles"]]
    assert rules[0].startswith("Keep the product's current look") and rules[1].startswith("Keep its stack")         and rules[2].startswith("Change only what a spec asks for")
    assert "## C3. Change only what a spec asks for" in written
    assert constitution.read(repo) is None, "they bind once the owner approves the spec"


def test_the_spec_writer_for_a_new_product_keeps_its_web_tools_and_reads_no_code(s, project, monkeypatch):
    from justshipit.contracts.handoff import Handoff
    from justshipit.engineering import specs
    seen = {}
    monkeypatch.setattr(specs, "run_agent", lambda agent, purpose, prompt, model, **kw: seen.update(kw) or _spec_out())
    rid = _build_request(s, project.id)
    s.commit()
    spec_id = specs.write_spec(project.id, rid)
    assert seen.get("tools") is None and seen.get("cwd") is None
    assert Handoff.model_validate(s.get(Spec, spec_id).handoff).workflow.existing_code is False


@pytest.mark.parametrize("branch", ["gone", "--index-output=planted"])
def test_a_base_branch_the_clone_lacks_drafts_without_code_and_is_never_read_as_a_git_option(business, monkeypatch,
                                                                                            branch):
    from justshipit.engineering import specs
    bid, _ = business
    with session_scope() as s:
        b = s.get(Business, bid)
        b.product = {**b.product, "base_branch": branch}
        repo = settings().products_dir / existing_product.existing(b).repo
        p = Project(name="Rooms", slug=f"rooms-gone-{uuid.uuid4().hex[:6]}", goal="launch", budget_usd=5,
                    business_id=bid)
        s.add(p)
        s.flush()
        pid, rid = p.id, _build_request(s, p.id)
    seen = {}
    monkeypatch.setattr(specs, "run_agent", lambda agent, purpose, prompt, model, **kw: seen.update(kw) or _spec_out())
    if branch.startswith("-"):
        # The handoff contract refuses the name, so no spec carries it to the engineer's git commands.
        with pytest.raises(ValueError, match="not a branch name git accepts"):
            specs.write_spec(pid, rid)
    else:
        specs.write_spec(pid, rid)
    assert seen.get("tools") is None and seen.get("cwd") is None
    assert not (repo / "planted").exists() and not (Path.cwd() / "planted").exists()
