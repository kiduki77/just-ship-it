"""The owner's surfaces carry no internal codes or machine text, in either language. The third persona round read, on
English screens: 'Review: 리스크·감사 → unmet (contested=False)', 'Owner: marketing · Status: DONE · Attempt: 2',
'(policy dev.merge = strong_approve)', 'Branch ai/3danie-0001-a1 · PR none (local)' as a merge card's why,
'3MAYAA-0001' in card titles, 'l-eacdba' and 'REQ-1, REQ-12' beside spec lines, 'The paused project (P-6) starts
again', 'Auto-approved · System closed it' on a build the owner had not approved, and red 'failed' on work a later task
redid.

Each builder is fed code-shaped input and held to `owner_text.leaks`, the contract's own definition of a leak."""
import re
from datetime import datetime, timezone

import pytest
from justshipit.api import console
from justshipit.contracts import owner_text
from justshipit.contracts.handoff import AcceptanceOut, CriterionResultOut
from justshipit.contracts.work import TaskDraft, TaskStatus
from justshipit.db.models import AgentRequest, DevJob, Evidence, OwnerTask, Spec, Task
from justshipit.engineering import owner_trace, specs, worker
from justshipit.i18n import LANGS, catalog, msg
from justshipit.i18n.glossary import ROLES
from justshipit.orchestration import company_graph
from justshipit.services import requests, tasks

HANGUL = re.compile(r"[가-힣]")

# Catalog messages that name a code on purpose, and why the reader needs it.
NAMES_A_CODE = {
    "build.sandbox.login_missing": "names the environment variable the owner may have set",
    "system.claude.missing.fix": "names the environment variable that points at a Claude Code kept somewhere else",
    "operations.backup_exit": "names the backup tool whose exit code it reports",
    "post.refuse.card_missing": "goes back to the agent that asked, which fills the field by that name",
    # A request's own text names the file the owner opens; `owner_text.body` keeps a registry path for that.
    "factory.activate.summary": "a request body: the skill's file",
    "operations.reassess.rationale": "a request body: the report's file",
}


def _filled(text: str, lang: str) -> str:
    return re.sub(r"\{(\w+)(\|[^}]*)?\}", "Alpha" if lang == "en" else "알파", text)


def test_no_catalog_message_carries_a_code():
    """'(policy dev.merge = strong_approve)' and '(contested={contested})' were catalog messages."""
    found = {}
    for key, entry in catalog().items():
        for lang in LANGS:
            forms = entry[lang].values() if isinstance(entry[lang], dict) else [entry[lang]]
            if hits := [h for text in forms for h in owner_text.leaks(_filled(text, lang))]:
                found[key] = hits
    assert sorted(set(found) - set(NAMES_A_CODE)) == []
    assert sorted(set(NAMES_A_CODE) - set(found)) == [], "an exception whose message no longer names a code goes"


@pytest.mark.parametrize("seen", [
    "Review: 리스크·감사 → unmet (contested=False)", "Every merge needs your approval (policy dev.merge = strong_approve).",
    "The spec says it's in. l-eacdba", "Approve the merge: 3MAYAA-0001 Weekly client status report",
    "The paused project (P-6) starts again", "Not met: the claim at [E3]", "four changes EV-1371",
    "It runs with the stub AI (AI_PROVIDER=stub)", "Guide pages show the T-104/T-105 text", "from the first_test answers",
])
def test_the_contract_finds_each_code_the_owner_saw(seen):
    assert owner_text.leaks(seen, line=True)


# ------------------------------------------------------------------ the task's result document

@pytest.mark.parametrize("lang", LANGS)
def test_a_task_document_names_its_people_and_verdict_in_the_owners_words(s, project, monkeypatch, lang):
    written = {}
    monkeypatch.setattr(company_graph.registry, "write_files", lambda files, _m: written.update(files) or "abc1234")
    t = Task(project_id=project.id, key="walk_in_kit", title="Walk-in kit", owner="marketing", objective="o",
             acceptance_criteria=["c"], status=TaskStatus.FAILED.value, iteration=2, contested=False)
    s.add(t)
    s.flush()
    ev = Evidence(project_id=project.id, task_id=t.id, agent="marketing", claim="Cafés reorder on Fridays",
                  kind="sourced_claim", verification_status="unverifiable", source_url="https://example.com/a")
    s.add(ev)
    s.flush()
    t.result = {"summary": "The kit is ready.", "findings_markdown": "Body [E1].", "evidence_ids": [ev.id]}
    t.review = {"reviewer": "risk", "verdict": "unmet", "criteria_unmet": ["The opener is 79 words, not 72"],
                "counter_arguments": []}

    company_graph._write_task_artifact(s, project, t, lang)

    doc = next(iter(written.values()))
    head = doc.split("\n## ")[0].split("\n")[1:]
    assert [line for line in head if owner_text.leaks(line, line=True)] == []
    told = "\n".join(head)
    assert ROLES["marketing"][lang] in head[0] and ROLES["risk"][lang] in told
    assert msg("work.doc.task.reworked", lang, n=1) in told and msg("work.doc.task.verdict.unmet", lang) in told
    assert not any(w in doc for w in ("contested", "Attempt", "FAILED", "sourced_claim", "unverifiable"))
    assert f"[EV-{ev.id}] ({msg('evidence.grade.sourced', lang)}) Cafés reorder on Fridays" in doc
    if lang == "en":
        assert not HANGUL.search("\n".join(head))


def test_a_task_document_cites_its_evidence_and_no_plan_line_ids(s, project, monkeypatch):
    """P-11's ledger form read '(l-53627c)' and '(I-08514f)' beside the plan lines it followed, and the Kakao scripts
    cited '[E1]' as plain text: the console marks evidence it can point at (EV-ids), and the document carried only
    the number the agent used. The heading 'T-113 <title>' read as the title twice once the console named the code."""
    written = {}
    monkeypatch.setattr(company_graph.registry, "write_files", lambda files, _m: written.update(files) or "abc1234")
    t = Task(project_id=project.id, key="kakao", title="Kakao scripts", owner="marketing", objective="o",
             acceptance_criteria=[], status=TaskStatus.DONE.value, iteration=1)
    s.add(t)
    s.flush()
    ev = Evidence(project_id=project.id, task_id=t.id, agent="marketing", claim="Regulars answer by evening",
                  kind="sourced_claim", verification_status="supported")
    s.add(ev)
    s.flush()
    t.result = {"summary": "Ready.", "evidence_ids": [ev.id],
                "findings_markdown": "Fits the plan's line (l-53627c) and \"one line a day\" (l-08514f).\n"
                                     "Use it as is [E1]. Names are made up [E2].",
                "risks": ["Over 30 minutes, the signal (l-e49e80) applies"]}

    company_graph._write_task_artifact(s, project, t, "en")

    doc = next(iter(written.values()))
    assert doc.split("\n")[0] == "# Kakao scripts"
    assert not re.search(r"l-[0-9a-f]{6}|\[E\d", doc), doc
    assert f"Use it as is [EV-{ev.id}]. Names are made up." in doc
    assert "Fits the plan's line and \"one line a day\".\n" in doc


def test_a_task_document_written_before_reads_the_same_way(s, project, monkeypatch):
    """Documents already written keep '# T-113 …', '[E1]' and plan line ids in their file; the console reads them
    the way a new one is written."""
    from justshipit.api import app as api_app
    from justshipit.db.models import Artifact
    t = Task(project_id=project.id, key="k", title="Kakao scripts", owner="marketing", objective="o",
             acceptance_criteria=[], status=TaskStatus.DONE.value)
    a = Artifact(project_id=project.id, kind="task_output", title="Kakao scripts", path="projects/x/work/T-1-k.md")
    s.add_all([t, a])
    s.flush()
    ev = Evidence(project_id=project.id, task_id=t.id, agent="marketing", claim="c", kind="sourced_claim")
    s.add(ev)
    s.flush()
    t.artifact_id, t.result = a.id, {"evidence_ids": [ev.id]}
    s.commit()
    old = f"# T-{t.id} Kakao scripts\n- Marketing · Done\n\nAs planned (l-53627c), use it [E1][E3].\n- [E1] (Sourced) c"
    monkeypatch.setattr(api_app.registry, "read", lambda _p: old)

    got = api_app.get_artifact(a.id)["content"]

    assert got == f"# Kakao scripts\n- Marketing · Done\n\nAs planned, use it [EV-{ev.id}].\n- [EV-{ev.id}] (Sourced) c"


def test_a_task_document_says_when_reviewers_disagree(s, project, monkeypatch):
    written = {}
    monkeypatch.setattr(company_graph.registry, "write_files", lambda files, _m: written.update(files) or "abc1234")
    t = Task(project_id=project.id, key="k", title="T", owner="research", objective="o", acceptance_criteria=[],
             status=TaskStatus.DONE.value, iteration=1, contested=True, result={"summary": "s"},
             review={"reviewer": "strategy", "verdict": "pass"})
    s.add(t)
    s.flush()

    company_graph._write_task_artifact(s, project, t, "en")

    doc = next(iter(written.values()))
    assert msg("work.doc.task.contested", "en") in doc and "Redone" not in doc


# ------------------------------------------------------------------ the merge card

@pytest.mark.parametrize("lang", LANGS)
def test_a_merge_card_leads_with_the_verdict_and_carries_no_codes(s, project, lang):
    spec = Spec(spec_key=f"3M{project.id}A-0001", project_id=project.id, title="Weekly client status report",
                registry_path="x", target_repository="weekly-report")
    s.add(spec)
    s.flush()
    job = DevJob(spec_id=spec.id, branch="ai/3mayaa-0001-a1", result={"acceptance": {"accepted": False}})
    s.add(job)
    s.flush()
    out = AcceptanceOut(accepted=False, notes="The plan screen is missing.",
                        criteria_results=[CriterionResultOut(criterion="Sign in works", met=True, evidence="e")])

    req = worker._ask_merge(s, job, spec, out, "build.merge_card.ask", lang)
    seen = requests.read_model(s, req, lang, None)

    first = [seen["title"], seen["report"]["headline"], seen["report"]["why"], seen["report"]["on_approve"],
             req.rationale]
    assert [x for x in first if owner_text.leaks(x, line=True)] == []
    assert spec.spec_key not in req.title and "none (local)" not in req.summary and "없음(로컬)" not in req.summary
    assert seen["report"]["why"].startswith(msg("build.merge_card.unmet", lang, notes="").split(".")[0])


# ------------------------------------------------------------------ what approving does

@pytest.mark.parametrize("lang", LANGS)
def test_approving_a_change_says_what_happens_next_without_a_project_code(s, project, lang):
    project.status = "WAITING_HUMAN"
    req = AgentRequest(project_id=project.id, from_agent="project_manager", type="APPROVAL", title="Go ahead with four changes",
                       summary="s", rationale="r", options=["Approve", "Decline"], action_kind="project.direction_change",
                       hold_scope="run", policy_mode="ask", status="PENDING")
    s.add(req)
    s.flush()

    says = requests.outcome(s, req, lang).says

    assert says == (msg("request.effect.team_follows", lang) + ". " + msg("request.effect.resumes", lang) + ".")
    assert not owner_text.leaks(says, line=True)


@pytest.mark.parametrize("lang", LANGS)
def test_a_build_the_rules_started_says_why_no_one_asked(s, project, lang):
    req = AgentRequest(project_id=project.id, from_agent="product", type="DEVELOPMENT", title="Build: weekly report",
                       summary="s", rationale="r", options=[], action_kind="dev.implementation_start",
                       policy_mode="auto", status="AUTO_APPROVED", response={"responded_by": "policy"})
    s.add(req)
    s.flush()

    assert requests.report(s, req, lang)["closed"] == msg("request.closed.auto.write_spec", lang)
    req.status = "PENDING"
    assert requests.report(s, req, lang)["closed"] is None


# ------------------------------------------------------------------ the spec card's trace

@pytest.mark.parametrize("lang", LANGS)
def test_a_trace_row_says_where_it_came_from_in_words(lang):
    sources = [owner_trace.OwnerSource("l-eacdba", "the plan", "Run-out is a guess", msg("build.trace.from_plan", lang)),
               owner_trace.OwnerSource("answer REQ-53", "answer", "Build: changes → APPROVED\nWith limits",
                                       msg("build.trace.from_answer", lang), "Build: changes: With limits")]

    rows = owner_trace.settle(sources, [], set(), lang)

    assert [r["from"] for r in rows] == [msg("build.trace.from_plan", lang), msg("build.trace.from_answer", lang)]
    assert rows[1]["says"] == "Build: changes: With limits"
    assert [r for r in rows if owner_text.leaks(r["says"] + " " + r["from"], line=True)] == []


# ------------------------------------------------------------------ work a later task redid

FAILED_AT = datetime(2026, 11, 4, 14, 0, tzinfo=timezone.utc)


def _failed(s, project, key: str, status=TaskStatus.FAILED) -> Task:
    t = Task(project_id=project.id, key=key, title=key, owner="product", objective="o", acceptance_criteria=[],
             status=status.value, plan_round=1, error="Didn't pass review: x", finished_at=FAILED_AT)
    s.add(t)
    s.flush()
    return t


def test_a_failed_task_a_follow_up_redoes_reads_as_redone(s, project):
    old = _failed(s, project, "first_version_scope")
    done = _failed(s, project, "risk_review", TaskStatus.DONE)
    drafts = [TaskDraft(key="scope_v2", title="Revise the first-version scope", owner="product", objective="o",
                        acceptance_criteria=["c"], depends_on=[], redoes=f"T-{old.id}"),
              TaskDraft(key="risk_v2", title="Argue against the revised plan", owner="risk", objective="o",
                        acceptance_criteria=["c"], depends_on=[], redoes="risk_review")]

    created, _ = tasks.create_from_drafts(s, project, drafts, origin="follow_up", plan_round=2, lang="en")

    assert (old.status, old.result, old.finished_at) == (TaskStatus.REPLACED.value, None, FAILED_AT)
    assert done.status == TaskStatus.DONE.value
    assert old.id not in {a.get("task_id") for a in _alerts(s)}


def test_a_failed_task_nothing_redoes_still_alerts(s, project):
    old = _failed(s, project, "toronto_route")

    assert old.id in {a.get("task_id") for a in _alerts(s)}


def _alerts(s) -> list[dict]:
    return console._alerts(s, set(), lang="en")


@pytest.mark.parametrize("raw, shown", [
    ("Approve the merge: 3MAYAA-0001 Weekly report", "Approve the merge: Weekly report"),
    ("Target 5G-2030 rollout", "Target 5G-2030 rollout"), ("4K-2160 screens", "4K-2160 screens"),
])
def test_a_title_drops_a_digit_led_spec_key_but_keeps_a_word_with_a_year(raw, shown):
    assert owner_text.title(raw) == shown


def test_approving_a_card_with_no_options_promises_nothing_it_cannot_keep(s, project):
    """Only a chosen option or a note reaches the project's record (`respond`), so a bare approval says it records."""
    req = AgentRequest(project_id=project.id, from_agent="project_manager", type="APPROVAL", title="t", summary="s",
                       rationale="r", options=[], action_kind="project.direction_change", policy_mode="ask",
                       status="PENDING")
    s.add(req)
    s.flush()

    assert requests.outcome(s, req, "en").says == msg("request.effect.by_hand", "en") + "."


# ------------------------------------------------------------------ the fourth round's codes

def test_a_line_read_first_drops_a_setting_with_its_value():
    """'It runs with the stub AI (AI_PROVIDER=stub)' read as '(=stub)' once the name went and its value stayed."""
    said = "It runs with the stub AI (AI_PROVIDER=stub), PREVIEW_FIXTURES=1 and made-up data."

    assert owner_text.one_line(said, 200) == "It runs with the stub AI and made-up data."


@pytest.mark.parametrize("lang", LANGS)
def test_a_merge_card_drops_the_codes_the_cpo_wrote_in_its_lines(s, project, lang):
    """REQ-97/98 listed '✘ Owner page shows … from the first_test answers', '… show the T-104/T-105 text' and 'The
    screens show sample output, not a real run: … (AI_PROVIDER=stub), PREVIEW_FIXTURES=1 …'."""
    spec = Spec(spec_key=f"SAM{project.id}-0001", project_id=project.id, title="Monday support digest",
                registry_path="x", target_repository="digest")
    s.add(spec)
    s.flush()
    job = DevJob(spec_id=spec.id, branch="ai/sammon-0001-a1", result={"acceptance": {
        "accepted": False, "unseen": ["The first_test owner page"],
        "sample_output": "It runs with the stub AI (AI_PROVIDER=stub), PREVIEW_FIXTURES=1 and made-up shop data."}})
    s.add(job)
    s.flush()
    out = AcceptanceOut(accepted=False, notes="Set AI_PROVIDER=stub to see T-104 offline.",
                        criteria_results=[
                            CriterionResultOut(criterion="Owner page shows 'Gate passed' from the first_test answers",
                                               met=False, evidence="e"),
                            CriterionResultOut(criterion="Guide pages show the T-104/T-105 text", met=False,
                                               evidence="e")],
                        design_findings=["The upload_form button reads REQ-12"])

    req = worker._ask_merge(s, job, spec, out, "build.merge_card.ask", lang)

    said = req.summary.rsplit("\n\n", 1)[0]  # the branch line after it is the owner's to copy
    assert [line for line in said.splitlines() if owner_text.leaks(line, line=True)] == []
    assert "made-up shop data" in said and "- ✘ Guide pages show the text" in said
    assert "Owner page shows 'Gate passed' from the answers" in said


def test_a_spec_card_shows_the_spec_without_its_key_or_the_engineers_file_paths(s, project, monkeypatch):
    """REQ-92's spec read 'SAMMON-0001 Monday support digest…' and 'the format in sources/T-103.md': the key is the
    engineers' name for it, and a task's result is the task, which the console links by its title."""
    spec = Spec(spec_key=f"SAM{project.id}-0001", project_id=project.id, title="Monday support digest",
                registry_path="x", target_repository="digest")
    s.add(spec)
    s.flush()
    req = AgentRequest(project_id=project.id, from_agent="product", type="APPROVAL", title="Approve the spec",
                       summary="s", rationale="r", options=[], action_kind="dev.spec_approve", policy_mode="ask",
                       status="PENDING", action_params={"spec_id": spec.id, "commit_sha": "abc1234"})
    s.add(req)
    s.flush()
    prd = (f"# {spec.spec_key} Monday support digest\n\nOne-page digest in the format in sources/T-103.md.\n"
           "The guide (from `.ship/spec/sources/T-104.md`) and the data note (from sources/T-105.md).\n")
    monkeypatch.setattr(specs.registry, "read_at", lambda path, sha: prd)

    text = specs.card_spec(s, req)["text"]

    assert text.startswith("# Monday support digest\n")
    assert spec.spec_key not in text and "sources/" not in text and "`" not in text
    assert "in the format in T-103." in text and "(from T-104)" in text and "(from T-105)" in text


def test_a_code_in_an_agents_text_reads_as_what_it_points_at(s, project):
    """The CEO's decision log linked '(EV-1926)… (EV-1913, EV-2004)' and a spec 'T-103' by their codes. The console
    asks what each points at and links it by that: a task, a decision or a to-do by its title, evidence by its
    claim."""
    t = Task(project_id=project.id, key="digest_format", title="Design the digest format", owner="product",
             objective="o", acceptance_criteria=[], status="DONE")
    ev = Evidence(project_id=project.id, agent="research", claim="Small stores get about 50 emails a week [E2]",
                  kind="sourced_claim")
    req = AgentRequest(project_id=project.id, from_agent="product", type="APPROVAL",
                       title="Approve the merge: 3MAYAA-0001 Weekly report", summary="s", rationale="r", options=[],
                       action_kind="dev.merge", policy_mode="ask", status="APPROVED")
    todo = OwnerTask(project_id=project.id, kind="check", title="Talk to Priya (REQ-94)",
                     status="DONE", done_at=FAILED_AT)  # settled: an open one sits on other files' lists
    s.add_all([t, ev, req, todo])
    s.commit()
    codes = [f"T-{t.id}", f"EV-{ev.id}", f"REQ-{req.id}", f"OT-{todo.id}"]

    got = console.refs(",".join([*codes, "T-999999999", "X-1", "drop table"]))

    assert got == {codes[0]: {"title": "Design the digest format"},
                   codes[1]: {"title": "Small stores get about 50 emails a week", "project_id": project.id},
                   codes[2]: {"title": "Approve the merge: Weekly report"}, codes[3]: {"title": "Talk to Priya"}}


def test_a_project_decision_or_build_code_reads_as_its_name(s, project):
    """The breadcrumb said only 'P-11', the CEO's run log '#912 · P-11', the project page 'DEC-10' and the dev board
    'J-9 BUSINE10-0001': the console asks what each points at and shows that."""
    from justshipit.db.models import Decision
    spec = Spec(spec_key=f"BUS{project.id}-0001", project_id=project.id, title="Weekly order sheet",
                registry_path="products/x/spec.md", target_repository="orders")
    dec = Decision(project_id=project.id, title="Keep the paper ledger", decision="go", rationale="r")
    s.add_all([spec, dec])
    s.flush()
    job = DevJob(spec_id=spec.id, branch="ai/bus-0001-a1")
    s.add(job)
    s.commit()
    codes = [f"P-{project.id}", f"DEC-{dec.id}", f"J-{job.id}"]

    got = console.refs(",".join([*codes, "J-999999999", "DEC-999999999"]))

    assert got == {codes[0]: {"title": owner_text.title(project.name, 40)},
                   codes[1]: {"title": "Keep the paper ledger"}, codes[2]: {"title": "Weekly order sheet"}}


def test_an_activity_line_names_records_by_their_titles(s, project):
    """The dashboard's feed read 'REQ-104를 승인해서 내 할 일이 생겼어요: OT-48 예전 회사 물류팀 동료에게…' and
    '승인한 명세 BUSINE10-0001로…': a line the console shows as written carries no record codes."""
    from justshipit.db.models import ActivityEvent
    e = ActivityEvent(actor="system", verb="todo", entity="request", entity_id=1, project_id=project.id,
                      summary="REQ-104를 승인해서 내 할 일이 생겼어요: OT-48 예전 회사 동료에게 부탁 메시지", data={})
    s.add(e)
    s.commit()

    got = next(x for x in console.activity_feed(project_id=project.id, include_test=True)["events"] if x["id"] == e.id)

    assert got["summary"] == "승인해서 내 할 일이 생겼어요: 예전 회사 동료에게 부탁 메시지"


def test_a_tasks_findings_cite_evidence_the_console_can_mark(s, project):
    """The task panel's findings showed '[E1]' as text: the result carries the agent's place numbers."""
    t = Task(project_id=project.id, key="k", title="Kakao scripts", owner="marketing", objective="o",
             acceptance_criteria=[], status=TaskStatus.DONE.value)
    s.add(t)
    s.flush()
    ev = Evidence(project_id=project.id, task_id=t.id, agent="marketing", claim="c", kind="sourced_claim")
    s.add(ev)
    s.flush()
    t.result = {"summary": "Ready [E1].", "findings_markdown": "As planned (l-53627c), use it [E1].",
                "evidence_ids": [ev.id]}
    s.commit()

    got = console.task_detail(t.id)["task"]["result"]

    assert got["findings_markdown"] == f"As planned, use it [EV-{ev.id}]."
    assert got["summary"] == f"Ready [EV-{ev.id}]."


def test_a_tasks_brief_carries_no_plan_line_ids(s, project):
    """T-113's panel read 'l-bbc0a0' in what the CEO asked of it."""
    t = Task(project_id=project.id, key="k", title="Kakao scripts", owner="marketing",
             objective="Write the Sunday message (l-bbc0a0).", acceptance_criteria=["Three lines (l-08514f)"],
             status=TaskStatus.DONE.value)
    s.add(t)
    s.commit()

    got = console.task_detail(t.id)["task"]

    assert got["objective"] == "Write the Sunday message." and got["acceptance_criteria"] == ["Three lines"]


SPEC_NOTES = {
    "matter.spec_in_progress": ("제품기획 에이전트가 쓰고 있는 명세가 있어요", "The product agent is writing a spec"),
    "matter.waits_on_questions": ("명세 카드에 다음 버전 후보로 올려 둘게요. 명세 질문에 답하면 카드가 올라와요",
                                  "Kept for the spec's card as a candidate for the next version. The card goes up once "
                                  "you answer the spec's questions"),
    "matter.waits_on_draft": (
        "다음 버전 후보로 올려 둘게요. 제품기획 에이전트가 명세를 쓰면 그 카드에 함께 올라와요",
        "Kept as a candidate for the next version. It goes onto the spec's card once the product agent has written "
        "the spec"),
    "matter.waits_on_redraft": (
        "다음 버전 후보로 올려 둘게요. 메모대로 명세를 다시 쓰면 그 카드에 함께 올라와요",
        "Kept as a candidate for the next version. It goes onto the spec's card once the product agent has rewritten "
        "the spec from your notes"),
    "matter.next_version": ("명세 카드에 다음 버전 후보로 적어 뒀어요", "Noted on the spec's card as a candidate for the next version"),
    "matter.reask_deferred": ("보류한 명세 카드를 다시 올렸어요", "Raised the held spec card again"),
    "matter.reask_withdrawn": ("철회한 명세 카드를 다시 올렸어요", "Raised the withdrawn spec card again"),
    "matter.building": ("승인한 명세로 개발하고 있어요", "Building from the approved spec"),
    "matter.built": ("승인한 명세의 개발이 끝나서 머지 결정을 기다려요",
                     "The build of the approved spec is done and waits for the merge decision"),
    "matter.approved_idle": ("승인한 명세가 있어요. 지금 돌고 있는 개발 작업은 없어요", "There's an approved spec, with no build running now"),
}


@pytest.mark.parametrize("key", SPEC_NOTES)
def test_a_note_about_the_spec_reads_whole_on_the_feed(key):
    """Round 6: the feed read '승인한 명세 개발하고 있어요' once the console dropped 'BUSINE10-0001로' from it. The note
    never names the spec's key, so nothing the owner reads is cut out of the middle of it."""
    for lang, want in zip(("ko", "en"), SPEC_NOTES[key]):
        assert owner_text.title(msg(key, lang, spec="BUSINE10-0001"), 400) == want


@pytest.mark.parametrize("lang", LANGS)
def test_a_draft_folded_into_the_same_card_reads_whole_on_the_feed(lang):
    """Junho round 7: the feed read '…승인한 명세로 개발하고 있어요 (같은 일:)' once the console dropped the card's code.
    The row links to that card already, so the note names no code; one stored before says nothing empty."""
    new = msg("request.note.folded", lang, asked="product's 'Order fix'", why="Building from the approved spec")
    stored = {"ko": "새 카드로 올리지 않았어요: 제품기획의 '주문 수정'. 승인한 명세로 개발하고 있어요 (같은 일: REQ-115)",
              "en": "Not raised as a new card: product's 'Order fix'. Building from the approved spec (same as REQ-115)"}
    for line in (new, stored[lang]):
        shown = owner_text.title(line, 400)
        assert "REQ-115" not in shown and "()" not in shown and not shown.endswith(":)"), shown
        assert shown.endswith(("개발하고 있어요", "approved spec")), shown


def test_an_item_opened_from_another_business_is_named_by_its_title_not_its_code():
    """Junho round 7: on first entry a toast read '방금 연 항목(P-10)'. The item's code only keys the switch."""
    from pathlib import Path
    web = Path(__file__).resolve().parents[1] / "web" / "src"
    calls = [m.group(0) for f in web.rglob("*.tsx") for m in re.finditer(r"useFollowBusiness\([^;]*\);", f.read_text("utf-8"))]
    assert calls, "the console follows an item to its business somewhere"
    coded = [c for c in calls if re.search(r"`[^`]*-\$\{[^`]*`", c)]
    assert coded == [], f"name the item by its title, not a record code: {coded}"


def test_a_story_code_goes_but_a_name_with_a_year_stays():
    """A spec's story or scenario code ("US3.3") means nothing to the owner; a name like "US2024 plan" is a name."""
    assert owner_text.title("core-flow(US3.3) passed, as US1 asked", 200) == "core-flow passed, as asked"
    assert owner_text.title("US2024 plan", 200) == "US2024 plan"
