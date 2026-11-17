"""Business scope: with a business selected, every owner list shows that business only.

A row belongs to the business of its project. A row with no project belongs to the business it names, and a row that
names neither is the company's own (the worker, the monthly budget): it shows under every business and says so.
Without `business_id` a list spans every business but the archived ones, which the console asks for only on screens
that say 회사 전체. With no business at all, the console's business screens fetch nothing and say how to start one.
"""
import re
from pathlib import Path

import pytest
from justshipit import staffing
from justshipit.api.app import app
from justshipit.db.models import AgentRequest, DevJob, LLMCallRecord, OwnerTask, Spec, Task
from justshipit.services import control, projects, roadmap
from fastapi.testclient import TestClient
from sqlalchemy import func, update


@pytest.fixture(autouse=True)
def _quiet(s):
    s.execute(update(AgentRequest).where(AgentRequest.status == "PENDING").values(status="CANCELLED"))
    s.execute(update(OwnerTask).where(OwnerTask.status == "OPEN").values(status="SKIPPED", done_at=func.now()))
    s.commit()


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def _task(pid: int, key: str, status: str) -> Task:
    return Task(project_id=pid, key=key, title=f"업무 {key}", owner="research", objective="o", acceptance_criteria=["c"],
                status=status)


def _request(pid: int | None, title: str) -> AgentRequest:
    return AgentRequest(project_id=pid, from_agent="project_manager", type="APPROVAL", title=title, summary="s", rationale="r",
                        status="PENDING", policy_mode="approve")


def _spec(pid: int, key: str) -> Spec:
    return Spec(spec_key=key, project_id=pid, title=f"명세 {key}", registry_path=f"registry/specs/{key}.md")


class World:
    """Two businesses with the same kinds of rows, and the company's own rows that belong to neither."""

    def __init__(self, s):
        self.a = roadmap.create_business(s, name="가 사업", one_liner="가")
        self.b = roadmap.create_business(s, name="나 사업", one_liner="나")
        self.pa = projects.create(s, "가 목표", name="가 프로젝트", start=False, business=self.a)
        self.pb = projects.create(s, "나 목표", name="나 프로젝트", start=False, business=self.b)
        self.ta, self.tb = _task(self.pa.id, "ga", "FAILED"), _task(self.pb.id, "na", "FAILED")
        self.ra, self.rb, self.loose = _request(self.pa.id, "가 결정"), _request(self.pb.id, "나 결정"), _request(None, "전사 결정")
        uid = f"{self.a.id}-{self.b.id}"
        self.sa, self.sb = _spec(self.pa.id, f"SCOPE-A-{uid}"), _spec(self.pb.id, f"SCOPE-B-{uid}")
        s.add_all([self.ta, self.tb, self.ra, self.rb, self.loose, self.sa, self.sb])
        s.flush()
        self.ja, self.jb = DevJob(spec_id=self.sa.id, status="FAILED"), DevJob(spec_id=self.sb.id, status="FAILED")
        self.oa = OwnerTask(business_id=self.a.id, kind="other", title="가 할 일")
        self.ob = OwnerTask(business_id=self.b.id, kind="other", title="나 할 일")
        self.oloose = OwnerTask(business_id=None, kind="other", title="전사 할 일")
        s.add_all([self.ja, self.jb, self.oa, self.ob, self.oloose])
        s.commit()


@pytest.fixture
def world(s) -> World:
    """The company-wide request and to-do sit on every business's list, so they are settled after the test: a later file's
    today list (test_owner_time) would otherwise count them."""
    w = World(s)
    yield w
    s.rollback()
    s.execute(update(OwnerTask).where(OwnerTask.id == w.oloose.id, OwnerTask.status == "OPEN")
              .values(status="SKIPPED", done_at=func.now()))
    s.execute(update(AgentRequest).where(AgentRequest.id == w.loose.id, AgentRequest.status == "PENDING")
              .values(status="CANCELLED"))
    s.commit()


def _get(api, path: str, business_id: int | None = None):
    sep = "&" if "?" in path else "?"
    res = api.get(f"/api{path}{'' if business_id is None else f'{sep}business_id={business_id}'}")
    assert res.status_code == 200, res.text
    return res.json()


def test_the_work_board_shows_the_selected_business_only(world, api):
    ids = {t["id"] for t in _get(api, "/tasks", world.a.id)["tasks"]}
    assert world.ta.id in ids and world.tb.id not in ids


def test_the_project_list_shows_the_selected_business_only(world, api):
    ids = {p["id"] for p in _get(api, "/projects?include_archived=true", world.a.id)}
    assert world.pa.id in ids and world.pb.id not in ids


def test_an_archived_business_keeps_its_projects_to_itself(world, s, api):
    """With every business archived on 2026-11-04, the projects page listed their three archived projects while the
    rest of the console said there was no business."""
    projects.archive_business(s, world.b.id, lang="en")
    s.commit()
    for path in ("/projects", "/projects?include_archived=true"):
        assert world.pb.id not in {p["id"] for p in _get(api, path)}, path
    assert world.pa.id in {p["id"] for p in _get(api, "/projects")}
    named = {p["id"] for p in _get(api, "/projects?include_archived=true", world.b.id)}
    assert world.pb.id in named, "its own archived tab still lists it"


def test_a_project_brought_back_under_an_archived_business_stays_out_of_the_company_lists(world, s, api):
    projects.archive_business(s, world.b.id, lang="en")
    control.unarchive(s, world.pb.id, lang="en")
    s.commit()
    assert world.pb.id not in {p["id"] for p in _get(api, "/projects")}
    assert world.tb.id not in {t["id"] for t in _get(api, "/tasks?include_test=true")["tasks"]}
    assert world.pb.id in {p["id"] for p in _get(api, "/projects", world.b.id)}
    assert world.tb.id in {t["id"] for t in _get(api, "/tasks", world.b.id)["tasks"]}


def test_the_dev_page_shows_the_selected_business_only(world, api):
    data = _get(api, "/engineering", world.a.id)
    assert {x["id"] for x in data["specs"]} & {world.sa.id, world.sb.id} == {world.sa.id}
    assert {x["id"] for x in data["jobs"]} & {world.ja.id, world.jb.id} == {world.ja.id}


def test_the_dev_page_leaves_out_an_archived_business_and_project(world, s, api):
    """A test business archived on 2026-11-04 kept its stopped dev jobs, with 전달만 다시, on the 개발 page."""
    projects.archive_business(s, world.b.id, lang="en")
    s.commit()
    for business_id in (None, world.b.id):
        data = _get(api, "/engineering", business_id)
        assert world.sb.id not in {x["id"] for x in data["specs"]}
        assert world.jb.id not in {x["id"] for x in data["jobs"]}
        assert not [st for st in data["board"] if st["project_id"] == world.pb.id]
    own_page = _get(api, f"/engineering?project_id={world.pb.id}")
    assert {x["id"] for x in own_page["jobs"]} == {world.jb.id}, "the archived project's own page still shows it"


def test_the_activity_feed_shows_the_selected_business_and_the_company_only(world, api):
    events = _get(api, "/activity?limit=500", world.a.id)["events"]
    assert {e["business_id"] for e in events} <= {world.a.id, None}
    assert any(e["entity"] == "task" and e["entity_id"] == world.ta.id for e in events)
    assert not any(e["entity"] == "task" and e["entity_id"] == world.tb.id for e in events)


def test_alerts_show_the_selected_business_and_label_the_company_wide_ones(world, api):
    alerts = _get(api, "/alerts", world.a.id)
    failed = {a["task_id"] for a in alerts if a["kind"] == "task_failed"}
    assert world.ta.id in failed and world.tb.id not in failed
    assert {a["dev_job_id"] for a in alerts if a["kind"] == "dev_failed"} & {world.ja.id, world.jb.id} == {world.ja.id}
    assert all(a["company_wide"] == (a["project_id"] is None) for a in alerts)
    assert all(a["project_id"] in (None, world.pa.id) for a in alerts)


def test_the_home_overview_shows_the_selected_business_only(world, api):
    ov = _get(api, "/overview?tz=Asia/Seoul", world.a.id)
    assert world.pa.id in {p["id"] for p in ov["projects"]} and world.pb.id not in {p["id"] for p in ov["projects"]}
    recent = {t["id"] for t in ov["recent_tasks"]}
    assert world.tb.id not in recent
    assert {e["business_id"] for e in ov["activity"]} <= {world.a.id, None}
    assert all(a["project_id"] in (None, world.pa.id) for a in ov["alerts"])


def _dev_events(events, job_ids) -> set[int]:
    return {e["entity_id"] for e in events if e["entity"] == "dev_job" and e["entity_id"] in job_ids}


def test_a_dev_jobs_activity_belongs_to_its_specs_business(world, api):
    """J-1/J-2/J-3 showed in every business's 최근 활동: a dev job names no project, only its spec does."""
    jobs = {world.ja.id, world.jb.id}
    feed = _get(api, "/activity?limit=500", world.a.id)["events"]
    home = _get(api, "/overview?tz=Asia/Seoul", world.a.id)["activity"]
    project_tab = _get(api, f"/activity?project_id={world.pa.id}&limit=500")["events"]
    assert _dev_events(feed, jobs) == _dev_events(project_tab, jobs) == {world.ja.id}
    assert _dev_events(home, jobs) <= {world.ja.id}
    assert {e["project_id"] for e in feed if e["entity"] == "dev_job" and e["entity_id"] in jobs} == {world.pa.id}


def test_a_dev_job_event_stored_without_its_project_still_shows_under_its_business_only(world, s, api):
    """Events written before the fix have neither a project nor a business; they are placed at read time."""
    from justshipit.db.models import ActivityEvent
    s.add_all([ActivityEvent(actor="engineer", verb="status", entity="dev_job", entity_id=j.id, project_id=None,
                             business_id=None, summary="old", data={"ref": f"J-{j.id}", "to": "FAILED"})
               for j in (world.ja, world.jb)])
    s.commit()
    old = lambda events: {e["entity_id"] for e in events if e["summary"] == "old"}  # noqa: E731

    assert old(_get(api, "/activity?limit=500", world.a.id)["events"]) == {world.ja.id}
    assert old(_get(api, f"/activity?project_id={world.pb.id}&limit=500")["events"]) == {world.jb.id}


def test_a_bets_failed_job_is_its_business_alert_not_the_companys(world, s, api):
    """'회사 전체 · 도전 회고 작업 #239: 실패' was BET-3 of 동네산책: a bet_retro job names only its bet."""
    from justshipit.db.models import Bet, Job
    bet = Bet(business_id=world.b.id, title="나 도전", pitch="p", status="REVIEW")
    s.add(bet)
    s.flush()
    job = Job(gate="direct", kind="bet_retro", payload={"bet_id": bet.id}, status="failed", last_error="BudgetExceeded: x",
              idempotency_key=f"bet_retro:{bet.id}:scope")
    s.add(job)
    s.commit()

    mine = [a for a in _get(api, "/alerts", world.b.id) if a.get("job_id") == job.id]
    other = [a for a in _get(api, "/alerts", world.a.id) if a.get("job_id") == job.id]

    assert [(a["company_wide"], a["business_id"]) for a in mine] == [(False, world.b.id)] and other == []


def test_a_failed_job_naming_a_project_that_is_gone_still_lists_its_alert(world, s, api):
    """A job's payload outlives the row it names (a test's or an import's project): the alert list still answers."""
    from justshipit.db.models import Job
    job = Job(gate="direct", kind="write_spec", payload={"project_id": 2_000_000_000}, status="failed", last_error="x",
              idempotency_key="write_spec:gone")
    s.add(job)
    s.commit()

    alerts = [a for a in _get(api, "/alerts") if a.get("job_id") == job.id]

    assert [(a["project_id"], a["business_id"]) for a in alerts] == [(2_000_000_000, None)]


def test_the_sidebar_agents_show_work_of_the_selected_business_only(world, s, api):
    s.execute(update(Task).where(Task.id.in_([world.ta.id, world.tb.id])).values(status="RUNNING"))
    s.commit()
    running = [w for a in _get(api, "/agents", world.a.id) for w in a["running"]]
    assert world.ta.id in {w.get("task_id") for w in running} and world.tb.id not in {w.get("task_id") for w in running}


def test_an_agents_page_lists_the_selected_business_work_only(world, api):
    """사업전략's page, with 견적봇 selected, listed P-1 and P-3 tasks."""
    mine = {t["id"] for t in _get(api, "/agents/research", world.a.id)["tasks"]}
    every = {t["id"] for t in _get(api, "/agents/research")["tasks"]}
    assert world.ta.id in mine and world.tb.id not in mine and {world.ta.id, world.tb.id} <= every


def test_an_agents_month_and_all_time_cost_count_the_same_calls(world, s, api):
    """The CEO's page read "$24.88 this month ($4.44 all time)": the month counted every business, all time only the
    selected one."""
    uid = f"{world.a.id}-{world.b.id}"
    s.add_all([LLMCallRecord(call_id=f"cost-a-{uid}", project_id=world.pa.id, agent="project_manager", purpose="work",
                             provider="claude", status="ok", cost_usd=1.0),
               LLMCallRecord(call_id=f"cost-b-{uid}", project_id=world.pb.id, agent="project_manager", purpose="work",
                             provider="claude", status="ok", cost_usd=10.0)])
    s.commit()
    a, b = _get(api, "/agents/project_manager", world.a.id), _get(api, "/agents/project_manager", world.b.id)
    for page in (a, b):
        assert page["cost_month"] == pytest.approx(sum(p["cost_usd"] for p in page["by_purpose"]))
    assert b["cost_month"] - a["cost_month"] == pytest.approx(9.0)
    listed = {x["id"]: x for x in _get(api, "/agents", world.a.id)}["project_manager"]
    assert listed["cost_month"] == pytest.approx(listed["cost_total"]) == pytest.approx(a["cost_month"])


def test_without_a_business_the_list_spans_every_business(world, api):
    ids = {t["id"] for t in _get(api, "/tasks")["tasks"]}
    assert {world.ta.id, world.tb.id} <= ids


def test_every_count_of_decisions_and_owner_work_comes_out_the_same(world, api):
    today = _get(api, f"/businesses/{world.a.id}/today?all=true")
    decisions = [i for i in today["items"] if i["kind"] == "decision"]
    inbox = _get(api, "/inbox", world.a.id)
    overview = _get(api, "/overview", world.a.id)
    assert {i["ref"] for i in decisions} == {f"REQ-{r['id']}" for r in inbox} == {f"REQ-{world.ra.id}", f"REQ-{world.loose.id}"}
    assert overview["pending"]["total"] == len(inbox)
    # 내 할 일 shows the business's own tasks, then the company-wide ones from the same list under 사업 밖 할 일
    mine = {i["ref"] for i in today["items"] if i["kind"] == "other"}
    listed = {t["ref"] for t in _get(api, "/owner-tasks?status=OPEN", world.a.id)["items"]}
    assert mine == {f"OT-{world.oa.id}", f"OT-{world.oloose.id}"} and listed == {f"OT-{world.oa.id}"}


def test_an_item_opened_by_its_address_names_its_business(world, api):
    assert _get(api, f"/requests/{world.rb.id}")["business_id"] == world.b.id
    assert _get(api, f"/requests/{world.loose.id}")["business_id"] is None
    assert _get(api, f"/tasks/{world.tb.id}")["project"]["business_id"] == world.b.id
    assert _get(api, f"/owner-tasks/{world.ob.id}")["business_id"] == world.b.id
    assert _get(api, f"/projects/{world.pb.id}")["project"]["business_id"] == world.b.id


# ------------------------------------------------------------------ the console reads business lists one way

WEB = Path(__file__).resolve().parents[1] / "web" / "src"
SCOPED = ("overview", "alerts", "activity", "tasks", "projects", "engineering", "agents", "runs", "inbox", "owner-tasks")
# a list path written straight into a fetch: "/tasks", `/tasks?…`, "/inbox${q}"; a detail path (/tasks/12) is fine
DIRECT = re.compile(r"""\b(?:useLive|api)(?:<[^>]*>)?\(\s*[`"']/(""" + "|".join(SCOPED) + r""")(?=[?`"'$])""")


def test_the_console_reads_business_lists_only_through_the_business_scope():
    """A screen that fetched a list straight would show every business again. The ways in are in business.tsx:
    `useBusinessLive` (or `useScoped` for a one-off fetch) adds the selected business, and `useCompanyLive` is the
    explicit cross-business read for a screen that says 회사 전체."""
    found = [f"{p.relative_to(WEB)}:{n}: {line.strip()}" for p in WEB.rglob("*.ts*") if p.name != "business.tsx"
             for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if DIRECT.search(line)]
    assert found == []


def test_every_business_screen_says_how_to_start_one_when_there_is_none():
    """With no business a business list never loads (`useScoped` in business.tsx), so a screen that only waited for it
    would spin. Each one checks `useNoBusiness()` or `current` first, or sits on an item's page (`ItemScope`)."""
    waits = [p.name for p in (WEB / "pages").glob("*.tsx") if "useBusinessLive" in (src := p.read_text(encoding="utf-8"))
             and not any(x in src for x in ("useNoBusiness()", "if (!current)", "<ItemScope"))]
    assert waits == []


def test_the_company_wide_cost_table_names_each_projects_business(world, s, api):
    uid = f"{world.a.id}-{world.b.id}"
    s.add(LLMCallRecord(call_id=f"costs-row-{uid}", project_id=world.pb.id, agent="project_manager", purpose="work",
                        provider="claude", status="ok", cost_usd=2.0))
    s.commit()
    row = next(r for r in api.get("/api/costs").json()["by_project"] if r["project_id"] == world.pb.id)
    assert (row["business_id"], row["business"]) == (world.b.id, world.b.name)


def _call(world, s, key: str, *, project=None, business=None, agent="analyst", status="ok", usd=0.5,
          task=None) -> None:
    s.add(LLMCallRecord(call_id=f"p15-{key}-{world.a.id}-{world.b.id}", project_id=project.id if project else None,
                        business_id=business.id if business else None, task_id=task.id if task else None,
                        agent=agent, purpose=f"p15-{key}", provider="claude", status=status, cost_usd=usd))
    s.commit()


def test_an_agents_state_and_tasks_count_only_the_selected_business(world, s, api):
    _call(world, s, "b-failed", project=world.pb, business=world.b, status="error")
    b = {x["id"]: x for x in _get(api, "/agents", world.b.id)}["analyst"]
    a = {x["id"]: x for x in _get(api, "/agents", world.a.id)}["analyst"]
    assert b["last_status"] == "error" and a["last_run_at"] != b["last_run_at"]
    assert _get(api, "/agents/analyst", world.a.id)["last_run_at"] == a["last_run_at"]
    assert {x["id"]: x for x in _get(api, "/overview", world.a.id)["agents"]}["analyst"]["last_run_at"] == a["last_run_at"]
    every = {x["id"]: x for x in _get(api, "/agents")}["research"]["tasks_total"]
    mine = {x["id"]: x for x in _get(api, "/agents", world.a.id)}["research"]["tasks_total"]
    assert mine == 1 and every >= 2


def test_model_calls_name_their_business_and_a_project_less_call_shows_only_in_its_own(world, s, api):
    _call(world, s, "b-kickoff", business=world.b)
    b = _get(api, "/runs?purpose=p15-b-kickoff", world.b.id)
    a = _get(api, "/runs?purpose=p15-b-kickoff", world.a.id)
    assert [r["business_id"] for r in b["runs"]] == [world.b.id] and a["runs"] == []
    assert "p15-b-kickoff" in b["purposes"] and "p15-b-kickoff" not in a["purposes"]


def test_crews_and_usage_count_the_selected_business(world, s, api):
    staffing.ensure(s, world.tb.id, staffing.task_facts(s, world.tb), staffing.load_limits())
    _call(world, s, "b-usage", project=world.pb, business=world.b, agent="finance", usd=7.0, task=world.tb)
    usage = {(r["agent"]): r for r in _get(api, "/usage", world.b.id)["by_agent"]}
    other = {(r["agent"]): r for r in _get(api, "/usage", world.a.id)["by_agent"]}
    assert usage["finance"]["cost_usd"] - other.get("finance", {"cost_usd": 0.0})["cost_usd"] == pytest.approx(7.0)
    crews_b = {(c["subject"]["kind"], c["subject"]["id"]): c for c in _get(api, "/crews", world.b.id)["crews"]}
    crews_a = {(c["subject"]["kind"], c["subject"]["id"]): c for c in _get(api, "/crews", world.a.id)["crews"]}
    finance = [(m["role"], m["cost_usd"]) for m in crews_b[("task", world.tb.id)]["members"]]
    assert finance == [("finance", pytest.approx(7.0))]
    assert ("task", world.tb.id) not in crews_a and ("task", world.ta.id) in crews_a


def test_schedules_and_jobs_list_the_selected_business_and_the_companys_own(world, s, api):
    from datetime import UTC, datetime, timedelta

    from justshipit.db.models import Job, Schedule
    sc = Schedule(name=f"p15 {world.b.id}", kind="find_people", payload={"business_id": world.b.id}, interval_hours=24,
                  enabled=True, next_run_at=datetime.now(UTC) + timedelta(days=1))
    job = Job(gate="direct", kind="find_people", payload={"business_id": world.b.id, "p15": True}, status="done",
              business_id=world.b.id)
    s.add_all([sc, job])
    s.commit()
    try:
        b_rows = {r["id"]: r for r in _get(api, "/schedules", world.b.id)}
        assert b_rows[sc.id]["business_id"] == world.b.id
        assert sc.id not in {r["id"] for r in _get(api, "/schedules", world.a.id)}
        assert sc.id in {r["id"] for r in _get(api, "/schedules")}
        assert job.id in {j["id"] for j in _get(api, "/jobs?limit=200", world.b.id)}
        assert job.id not in {j["id"] for j in _get(api, "/jobs?limit=200", world.a.id)}
    finally:
        s.delete(sc)
        s.commit()
