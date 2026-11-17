"""A note on one planned screen sends the spec back, and the next card shows where each note landed (Product
principle 4: the owner's words are binding and visibly change the plan). The landing is checked against what really
changed between the two drafts, never taken from the spec writer's word."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from justshipit.contracts.handoff import OwnerTraceOut
from justshipit.contracts.requests import HumanDecision, HumanResponse
from justshipit.db.models import ActivityEvent, AgentRequest, Job, Project, Spec
from justshipit.engineering import owner_trace, specs, status, wireflow
from justshipit.i18n import msg
from justshipit.services import app_settings, matters, queue, registry, requests
from test_one_card import asked, dev
from test_spec_kit import HOSTING, _answer, _questions
from test_spec_kit import _draft as _kit_draft
from test_wireflow import STATES, _brief, _draft, _plan, _repo, _state, _write
from ask_kit import by_project_manager

pytestmark = pytest.mark.usefixtures("own_jobs_settled")

EXPORT_FIRST = "Put the export button first."
NO_JARGON = "Say this without the word CSV."
TOO_SLOW = "This should not take a whole screen."
SAME_AS_REPORT = "Use the same buttons as the report."
ONE_STEP = "Pick the columns for me."


@pytest.fixture(autouse=True)
def _english_owner(monkeypatch):
    monkeypatch.setattr(app_settings, "language", lambda: "en")


@pytest.fixture
def api(api_token):
    from justshipit.api.app import app
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def _card(s, spec: Spec) -> AgentRequest:
    return s.get(AgentRequest, spec.approval_request_id)


def _send_back(s, card: AgentRequest, notes: dict[str, str], note: str | None = None) -> None:
    requests.respond(s, card.id, HumanResponse(
        decision=HumanDecision.REJECT, note=note,
        screen_notes=[{"screen": screen, "note": text} for screen, text in notes.items()]))
    s.commit()


def _redraft_jobs(s, spec: Spec) -> list[Job]:
    return list(s.scalars(select(Job).where(Job.kind == "redraft_spec", Job.payload["spec_id"].as_integer() == spec.id)
                          .order_by(Job.id)))


def _screens(api, card_id: int) -> dict:
    return api.get(f"/api/requests/{card_id}").json()["screens"]


def test_a_decline_with_notes_on_screens_sends_the_spec_back_under_the_spec_lock(s, project, monkeypatch):
    spec = _write(s, project, monkeypatch, _draft())
    card = _card(s, spec)

    _send_back(s, card, {"report": EXPORT_FIRST, "login": "Where do people log in?"}, note="Close, not there yet.")

    job, = _redraft_jobs(s, spec)
    assert job.lock_key == queue._lock_key("write_spec", {"project_id": project.id}), \
        "a redraft never runs beside a draft of the same product"
    assert job.payload == {"project_id": project.id, "spec_id": spec.id, "request_id": card.id}
    assert card.status == "REJECTED" and card.response["screen_notes"] == [{"screen": "report", "note": EXPORT_FIRST}]
    assert card.response["note"] == "Close, not there yet.\nWhere do people log in?", \
        "a note on a screen the draft does not plan is kept as a general note"
    s.refresh(spec)
    assert wireflow.sent_back(spec) and matters.live_spec(s, project.id).id == spec.id, \
        "the spec stays the product's while it is written again, so no new ask drafts a rival"
    assert status.spec_state(s, spec) == "DRAFT"
    note, = [x for x in owner_trace.owner_sources(s, s.get(Project, project.id), "en") if x.screen]
    assert (note.ref, note.text, note.drawn_at) == (f"REQ-{card.id}@report", EXPORT_FIRST, spec.draft_commit_sha)
    assert requests.reopen_refusal(s, card) is not None, "the redraft followed from the decline"


def test_notes_on_screens_go_with_a_decline_only(s, project, monkeypatch, api):
    spec = _write(s, project, monkeypatch, _draft())
    card = _card(s, spec)
    notes = [{"screen": "report", "note": EXPORT_FIRST}]

    for decision in (HumanDecision.APPROVE, HumanDecision.DEFER):
        with pytest.raises(requests.ResponseRejected):
            requests.respond(s, card.id, HumanResponse(decision=decision, revision=requests.revision(card),
                                                       screen_notes=notes))
    revision = requests.revision(card)
    s.rollback()  # the refused answers held the card's row
    refused = api.post(f"/api/requests/{card.id}/respond",
                       json={"decision": "approve", "revision": revision, "screen_notes": notes})

    assert refused.status_code == 409 and refused.json()["detail"] == msg("request.screen_notes_decline", "en")
    s.refresh(card)
    assert card.status == "PENDING" and _redraft_jobs(s, spec) == []


def test_a_decline_with_only_a_general_note_redrafts_nothing(s, project, monkeypatch):
    spec = _write(s, project, monkeypatch, _draft())
    card = _card(s, spec)

    requests.respond(s, card.id, HumanResponse(decision=HumanDecision.REJECT, note="Not this."))
    s.commit()

    s.refresh(spec)
    assert _redraft_jobs(s, spec) == [] and spec.status == "IN_REVIEW" and spec.approval_request_id == card.id


def _redrawn(card_id: int) -> tuple:
    """The second draft: the report redrawn, the loading screen gone, a new screen added, the rest as it was."""
    report = _state("report", "한 장 리포트", "success", "'오늘 출고 312건 중 확인할 예외 5건'. 버튼: '내보내기', '새 파일'.",
                    ("actions", "", ["내보내기", "새 파일"]), ("title", "오늘 출고 312건 중 확인할 예외 5건", []),
                    ("chips", "", ["주소 빈칸 2건", "중복 주문 2건", "반품 1건"]))
    states = [report if x["key"] == "report" else x for x in STATES if x["key"] != "mapping-loading"]
    states.append(_state("export-done", "내보내기", "success", "'내보냈어요.'", ("notice", "내보냈어요.", [])))

    def says(screen: str, disposition: str, *screens: str, reason: str = "") -> OwnerTraceOut:
        return OwnerTraceOut(source=f"REQ-{card_id}@{screen}", says="s", disposition=disposition, requirement_ids=[],
                             reason=reason, screens=list(screens))
    trace = [says("report", "included", "report"), says("upload-error", "included", "upload-error"),
             says("report-empty", "included", "report"),
             says("mapping-warning", "dropped", reason="The column step stays as it is in this version.")]
    return states, trace


def test_a_redraft_keeps_the_spec_and_shows_where_each_note_landed(s, project, monkeypatch, api):
    spec = _write(s, project, monkeypatch, _draft())
    old, first, key = _card(s, spec), spec.draft_commit_sha, spec.spec_key
    _send_back(s, old, {"report": EXPORT_FIRST, "upload-error": NO_JARGON, "mapping-loading": TOO_SLOW,
                        "report-empty": SAME_AS_REPORT, "mapping-warning": ONE_STEP})
    states, trace = _redrawn(old.id)
    prompts = []
    monkeypatch.setattr(specs, "run_agent", lambda agent, purpose, prompt, model, **kw: prompts.append(prompt) or _draft(
        design_brief=_brief(states), owner_trace=trace))

    specs.redraft_spec(spec.id, old.id)
    specs.redraft_spec(spec.id, old.id)

    s.expire_all()
    spec, old = s.get(Spec, spec.id), s.get(AgentRequest, old.id)
    new = _card(s, spec)
    assert len(prompts) == 1, "run again, it finds the spec written again and does nothing"
    assert f"[REQ-{old.id}@report] on `report`: {EXPORT_FIRST}" in prompts[0] and "mapping-loading:" in prompts[0], \
        "the writer reads each note against the key of its screen, and the last draft's screens"
    assert spec.spec_key == key and spec.draft_commit_sha != first and spec.status == "IN_REVIEW"
    assert new.id != old.id and new.status == "PENDING" and new.action_params["commit_sha"] == spec.draft_commit_sha
    assert new.action_params["redraft_of"] == {"request": old.id, "commit": first}
    assert old.status == "REJECTED" and old.response["superseded_by"] == requests.display_id(new.id)

    got = _screens(api, new.id)
    frames = got["screens"]
    assert {i: x["change"] for i, x in frames.items() if x["change"]} == {"report": "changed", "export-done": "new"}
    assert [x["id"] for x in got["removed"]] == ["mapping-loading"]

    def landed(screen: str) -> list[tuple]:
        return [(x["screen"], x["outcome"]) for x in frames[screen]["landed"]]
    assert landed("report") == [("report", "changed"), ("report-empty", "moved")]
    assert frames["report"]["landed"][0]["note"] == EXPORT_FIRST, "the owner's own words, not the writer's summary"
    assert landed("report-empty") == [("report-empty", "moved")] and frames["report-empty"]["landed"][0]["on"] == ["report"]
    assert landed("upload-error") == [("upload-error", "missed")], \
        "the writer said it changed the screen, and the screen is as it was"
    assert frames["upload-error"]["landed"][0]["reason"] == msg("build.trace.screen_unchanged", "en")
    assert landed("mapping-warning") == [("mapping-warning", "dropped")]
    assert frames["mapping-warning"]["landed"][0]["reason"] == "The column step stays as it is in this version."
    assert [(x["screen"], x["name"], x["note"], x["outcome"]) for x in got["unplaced"]] == [
        ("mapping-loading", "열 고르기", TOO_SLOW, "missed")], "its screen is gone and it landed nowhere"
    by_note = {r["source"]: r["disposition"] for r in new.action_params["owner_trace"]}
    assert by_note == {f"REQ-{old.id}@report": "included", f"REQ-{old.id}@upload-error": "missed",
                       f"REQ-{old.id}@mapping-loading": "missed", f"REQ-{old.id}@report-empty": "included",
                       f"REQ-{old.id}@mapping-warning": "dropped"}

    sent = _screens(api, old.id)
    assert sent["redraft"] == {"state": "done", "card": new.id, "retry_job": None}
    assert sent["screens"]["report"]["note"] == EXPORT_FIRST and "export-done" not in sent["screens"], \
        "the card the owner sent back keeps the draft and the notes it was sent back with"


def test_a_redraft_takes_out_the_files_the_new_draft_no_longer_has(s, project, monkeypatch):
    spec = _write(s, project, monkeypatch, _draft())
    old, first = _card(s, spec), spec.draft_commit_sha
    _send_back(s, old, {"report": "No screen at all: mail me the report."})
    monkeypatch.setattr(specs, "run_agent", lambda *a, **kw: _draft(design_brief=None))

    specs.redraft_spec(spec.id, old.id)

    s.expire_all()
    spec = s.get(Spec, spec.id)

    def names(commit: str) -> set[str]:
        return {f.removeprefix(f"{spec.registry_path}/") for f in registry.tree_at(spec.registry_path, commit)}
    assert names(first) - names(spec.draft_commit_sha) == {wireflow.FILE, "design-brief.md"}
    assert _card(s, spec).action_params["planned"] == 0


def test_a_redraft_that_failed_is_read_from_the_spec_and_its_jobs_and_can_run_again(s, project, monkeypatch, api):
    spec = _write(s, project, monkeypatch, _draft())
    card = _card(s, spec)
    _send_back(s, card, {"report": EXPORT_FIRST})
    job, = _redraft_jobs(s, spec)
    assert _screens(api, card.id)["redraft"] == {"state": "writing", "card": None, "retry_job": None}

    job.status, job.last_error = "failed", "AgentFailed: product/spec: timeout"
    s.commit()

    sent = _screens(api, card.id)
    assert sent["redraft"] == {"state": "failed", "card": None, "retry_job": job.id}
    assert sent["screens"]["report"]["note"] == EXPORT_FIRST, "the notes are kept"
    stage, = [st for st in status.board(s, project.id, None, lang="en") if st["key"] == f"spec-{spec.id}"]
    assert stage["tone"] == "bad" and stage["retry_job"] == job.id
    assert stage["headline"] == msg("build.stage.spec_redraft_failed", "en")

    again = api.post(f"/api/jobs/{job.id}/retry").json()["job_id"]

    assert again != job.id and _screens(api, card.id)["redraft"]["state"] == "writing"


def test_a_frame_reworded_under_the_same_blocks_is_not_changed():
    def redrawn(**by_key) -> wireflow.Wireflow:
        return _plan([{**x, **by_key.get(x["key"], {})} for x in STATES])
    before = _plan()

    reworded = redrawn(report={"shows": "Reworded, the same blocks."},
                       **{"mapping-loading": {"shows": "'열을 읽어요'"}})
    moved = redrawn(report={"sketch": list(reversed(next(x for x in STATES if x["key"] == "report")["sketch"]))})

    assert wireflow.changed(before, reworded) == {"mapping-loading": "changed"}, \
        "a frame draws its blocks, so new words under them change nothing the owner sees; one with none draws its words"
    assert wireflow.changed(before, moved) == {"report": "changed"}


def test_a_screen_the_writer_cites_in_its_own_spelling_still_counts(s, project, monkeypatch):
    spec = _write(s, project, monkeypatch, _draft())
    old = _card(s, spec)
    _send_back(s, old, {"report-empty": SAME_AS_REPORT})
    states = [_state("report-empty", "한 장 리포트", "empty", "'예외가 없어요'", ("title", "예외가 없어요", []),
                     ("actions", "", ["내보내기", "새 파일"])) if x["key"] == "report-empty" else x for x in STATES]
    cited = OwnerTraceOut(source=f"REQ-{old.id}@report-empty", says="s", disposition="included", requirement_ids=[],
                          reason="", screens=["Report_Empty"])
    monkeypatch.setattr(specs, "run_agent", lambda *a, **kw: _draft(design_brief=_brief(states), owner_trace=[cited]))

    specs.redraft_spec(spec.id, old.id)

    s.expire_all()
    row, = [r for r in _card(s, s.get(Spec, spec.id)).action_params["owner_trace"]
            if r["source"] == f"REQ-{old.id}@report-empty"]
    assert row["disposition"] == "included", "the writer's key is read the way its screen's id was made"


def test_a_cancelled_redraft_can_run_again(s, project, monkeypatch, api):
    spec = _write(s, project, monkeypatch, _draft())
    card = _card(s, spec)
    _send_back(s, card, {"report": EXPORT_FIRST})
    job, = _redraft_jobs(s, spec)
    job.status = "cancelled"
    s.commit()

    assert _screens(api, card.id)["redraft"] == {"state": "stopped", "card": None, "retry_job": job.id}
    stage, = [st for st in status.board(s, project.id, None, lang="en") if st["key"] == f"spec-{spec.id}"]
    assert stage["retry_job"] == job.id, "a spec sent back never waits on a job no one will run"
    assert stage["headline"] == msg("build.stage.spec_redraft_stopped", "en") and stage["tone"] != "bad",         "the owner stopped it: that is no failure"
    assert stage["detail"] == msg("build.stage.spec_redraft_failed_next", "en")

    api.post(f"/api/jobs/{job.id}/retry").raise_for_status()

    assert _screens(api, card.id)["redraft"]["state"] == "writing"


def _product(s, project, monkeypatch) -> tuple[AgentRequest, Spec]:
    """A product's first request, filed before plans existed, and the spec written from it, so a later ask finds it."""
    first = asked(s, project, "Exception report")
    s.commit()
    monkeypatch.setattr(specs, "run_agent", lambda *a, **kw: _draft())
    spec = s.get(Spec, specs.write_spec(project.id, first.id))
    s.refresh(spec)
    return first, spec


def _ask(s, project, title: str) -> AgentRequest:
    got = requests.create(s, dev(title), from_agent="project_manager", project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()
    return got


def _candidates(card: AgentRequest) -> list[str]:
    return [n["title"] for n in (card.action_params or {}).get("extra_needs", [])]


def _kept(s, text: str) -> bool:
    return s.scalar(select(ActivityEvent.id).where(ActivityEvent.verb == "request_folded",
                                                   ActivityEvent.summary.contains(text)).limit(1)) is not None


def test_an_ask_during_the_first_draft_goes_onto_the_first_spec_card(s, project, monkeypatch):
    first = asked(s, project, "Exception report")
    s.commit()

    later = _ask(s, project, "Print labels for the kitchen")

    assert later.id == first.id and matters.live_spec(s, project.id) is None
    assert _kept(s, msg("matter.waits_on_draft", "en")), "the activity note says it waits for the card"
    monkeypatch.setattr(specs, "run_agent", lambda *a, **kw: _draft())
    spec = s.get(Spec, specs.write_spec(project.id, first.id))

    s.expire_all()
    card = _card(s, spec)
    assert _candidates(card) == ["Print labels for the kitchen"]
    assert "[For the next version, not in this spec · Project Manager] Print labels for the kitchen" in card.rationale
    assert requests.WAITING_NEEDS not in s.get(AgentRequest, first.id).action_params, "carried, not copied"


def test_an_ask_during_a_first_draft_that_asks_questions_reaches_the_card_after_the_answers(s, project, monkeypatch):
    first = asked(s, project, "Banchan orders")
    s.commit()
    _ask(s, project, "Print labels for the kitchen")
    monkeypatch.setattr(specs, "run_agent", lambda *a, **kw: _kit_draft(_repo(), clarifications=[HOSTING]))
    spec = s.get(Spec, specs.write_spec(project.id, first.id))
    s.refresh(spec)
    assert spec.approval_request_id is None, "the card waits for the owner's answers"

    [hosting] = _questions(s, spec)
    _answer(s, hosting, decision=HumanDecision.ANSWER, chosen_option="Cloudflare Pages")

    assert _candidates(_card(s, s.get(Spec, spec.id))) == ["Print labels for the kitchen"]


def test_an_ask_while_a_sent_back_spec_is_rewritten_goes_onto_the_new_card_once(s, project, monkeypatch):
    first, spec = _product(s, project, monkeypatch)
    old = _card(s, spec)
    _send_back(s, old, {"report": EXPORT_FIRST})

    later = _ask(s, project, "Print labels for the kitchen")

    assert later.id == first.id, "no second card and no second spec"
    assert _kept(s, msg("matter.waits_on_redraft", "en"))
    monkeypatch.setattr(specs, "run_agent", lambda *a, **kw: _draft())
    specs.redraft_spec(spec.id, old.id)
    s.expire_all()
    spec = s.get(Spec, spec.id)
    card = _card(s, spec)
    assert _candidates(card) == ["Print labels for the kitchen"] and card.id != old.id
    assert card.rationale.count("· Project Manager] Print labels") == 1
    assert requests.WAITING_NEEDS not in s.get(AgentRequest, first.id).action_params

    _send_back(s, card, {"report": NO_JARGON})
    specs.redraft_spec(spec.id, card.id)

    s.expire_all()
    again = _card(s, s.get(Spec, spec.id))
    assert again.id not in (old.id, card.id)
    assert _candidates(again) == ["Print labels for the kitchen"], "a later card never takes it a second time"
    assert again.rationale.count("· Project Manager] Print labels") == 1


def test_an_ask_while_a_sent_back_spec_has_no_request_to_wait_on_is_only_noted(s, project):
    spec = Spec(spec_key="ORPHAN-0001", project_id=project.id, title="t", registry_path="specs/orphan", status="DRAFT",
                draft_commit_sha="abc", handoff={})
    s.add(spec)
    head = asked(s, project, "Exception report")
    s.commit()

    placed = matters.place(s, matters.Matter(f"dev:{matters.product_scope(s, project.id)[0]}", adds_up=True),
                           dev("More"), project.id, has_connector=False)

    assert placed.route == matters.Route.NOTE and placed.target is None and not placed.waits
    assert head.id == placed.head.id


def _drop(api, card_id: int):
    return api.post(f"/api/requests/{card_id}/drop-notes")


def _sent_back_with_a_waiting_ask(s, project, monkeypatch):
    first, spec = _product(s, project, monkeypatch)
    card = _card(s, spec)
    _send_back(s, card, {"report": EXPORT_FIRST}, note="Close, not there yet.")
    _ask(s, project, "Print labels for the kitchen")
    job, = _redraft_jobs(s, spec)
    job.status = "cancelled"
    s.commit()
    return first, spec, card


def test_dropping_the_notes_declines_the_spec_and_the_next_ask_drafts_a_new_one(s, project, monkeypatch, api):
    first, spec, card = _sent_back_with_a_waiting_ask(s, project, monkeypatch)

    got = _drop(api, card.id)

    assert got.status_code == 200, got.text
    s.expire_all()
    spec, card = s.get(Spec, spec.id), s.get(AgentRequest, card.id)
    assert (spec.status, spec.approval_request_id) == ("IN_REVIEW", card.id) and not wireflow.sent_back(spec)
    assert card.status == "REJECTED" and card.response["notes_dropped"] is True
    assert card.response["screen_notes"] == [{"screen": "report", "note": EXPORT_FIRST}], "what it was sent back with"
    assert status.spec_state(s, spec) == "REJECTED"
    assert matters.live_spec(s, project.id) is None
    assert _candidates(card) == ["Print labels for the kitchen"], "the ask that waited goes with the decline"
    assert requests.WAITING_NEEDS not in s.get(AgentRequest, first.id).action_params
    verbs = [e.verb for e in s.scalars(select(ActivityEvent).where(ActivityEvent.entity_id == card.id))]
    assert verbs.count("spec_notes_dropped") == 1
    assert _screens(api, card.id)["redraft"] == {"state": "dropped", "card": None, "retry_job": None}

    nxt = asked(s, project, "Weekly totals")  # a new ask now starts a plan (test_plans.py); this is its spec being written
    s.commit()
    assert nxt.id != first.id and s.scalar(select(Job.id).where(Job.idempotency_key == f"write_spec:{nxt.id}"))
    prompts = []
    monkeypatch.setattr(specs, "run_agent",
                        lambda agent, purpose, prompt, model, **kw: prompts.append(prompt) or _draft())
    specs.write_spec(project.id, nxt.id)
    assert "Print labels for the kitchen" in prompts[0], "the next version reads what waited for it"
    assert EXPORT_FIRST not in prompts[0], "dropped notes are no longer asked of the next draft"


def test_dropping_the_notes_again_changes_nothing(s, project, monkeypatch, api):
    _, _, card = _sent_back_with_a_waiting_ask(s, project, monkeypatch)
    first = _drop(api, card.id)
    s.expire_all()
    before = (s.get(AgentRequest, card.id).response, s.get(AgentRequest, card.id).action_params)

    again = _drop(api, card.id)

    assert again.status_code == 200 and again.json() == first.json()
    s.expire_all()
    assert (s.get(AgentRequest, card.id).response, s.get(AgentRequest, card.id).action_params) == before
    assert s.scalar(select(func.count(ActivityEvent.id)).where(
        ActivityEvent.verb == "spec_notes_dropped", ActivityEvent.entity_id == card.id)) == 1


def test_the_notes_stay_while_a_redraft_from_them_is_in_flight(s, project, monkeypatch, api):
    _, spec = _product(s, project, monkeypatch)
    card = _card(s, spec)
    _send_back(s, card, {"report": EXPORT_FIRST})

    refused = _drop(api, card.id)

    assert refused.status_code == 409 and refused.json()["detail"] == msg("build.notes.refuse_writing", "en")
    s.expire_all()
    assert wireflow.sent_back(s.get(Spec, spec.id)) and "notes_dropped" not in s.get(AgentRequest, card.id).response


def test_the_notes_of_a_card_that_was_not_sent_back_or_was_written_again_cannot_be_dropped(s, project, monkeypatch,
                                                                                             api):
    _, spec = _product(s, project, monkeypatch)
    card = _card(s, spec)
    assert _drop(api, card.id).json()["detail"] == msg("build.notes.refuse_not_sent_back", "en"), "still open"
    _send_back(s, card, {"report": EXPORT_FIRST})
    monkeypatch.setattr(specs, "run_agent", lambda *a, **kw: _draft())
    specs.redraft_spec(spec.id, card.id)
    s.expire_all()

    moved = _drop(api, card.id)

    assert moved.status_code == 409 and moved.json()["detail"] == msg("build.notes.refuse_moved_on", "en")
    assert _drop(api, 999999999).status_code == 404


def test_a_redraft_that_runs_after_the_notes_were_dropped_does_nothing(s, project, monkeypatch, api):
    _, spec, card = _sent_back_with_a_waiting_ask(s, project, monkeypatch)
    _drop(api, card.id).raise_for_status()
    prompts = []
    monkeypatch.setattr(specs, "run_agent", lambda *a, **kw: prompts.append(a) or _draft())

    specs.redraft_spec(spec.id, card.id)

    s.expire_all()
    spec = s.get(Spec, spec.id)
    assert prompts == [] and (spec.status, spec.approval_request_id) == ("IN_REVIEW", card.id)


def test_an_ask_during_a_later_versions_draft_waits_for_that_card(s, project, monkeypatch, api):
    _, spec, card = _sent_back_with_a_waiting_ask(s, project, monkeypatch)
    _drop(api, card.id).raise_for_status()
    s.expire_all()
    nxt = asked(s, project, "Weekly totals")  # a new ask now starts a plan (test_plans.py); this is its spec being written
    s.commit()

    later = _ask(s, project, "Delivery map")

    assert later.id == nxt.id, "no second card while the next spec is being written"
    assert _kept(s, msg("matter.waits_on_draft", "en"))
    monkeypatch.setattr(specs, "run_agent", lambda *a, **kw: _draft())
    new = s.get(Spec, specs.write_spec(project.id, nxt.id))
    s.expire_all()
    assert new.id != spec.id and _candidates(_card(s, s.get(Spec, new.id))) == ["Delivery map"]


def test_after_the_notes_are_dropped_a_failed_redraft_offers_no_retry(s, project, monkeypatch, api):
    _, spec = _product(s, project, monkeypatch)
    card = _card(s, spec)
    _send_back(s, card, {"report": EXPORT_FIRST})
    job, = _redraft_jobs(s, spec)
    job.status = "failed"
    s.commit()

    _drop(api, card.id).raise_for_status()

    s.expire_all()
    assert s.get(Job, job.id).alert_dismissed_at is not None, "Home no longer asks the owner about it"
    refused = api.post(f"/api/jobs/{job.id}/retry")
    assert refused.status_code == 409 and refused.json()["detail"] == msg("console.redraft_moved_on", "en")


def test_an_ask_a_card_already_took_is_not_carried_onto_it_again(s, project, monkeypatch):
    first, spec = _product(s, project, monkeypatch)
    old = _card(s, spec)
    need = {"title": "Print labels for the kitchen", "problem": "Labels", "by": "project_manager"}
    old.action_params = {**old.action_params, "extra_needs": [need]}
    first.action_params = {**first.action_params, requests.WAITING_NEEDS: [need]}  # copied, as before needs moved
    s.commit()
    _send_back(s, old, {"report": EXPORT_FIRST})
    monkeypatch.setattr(specs, "run_agent", lambda *a, **kw: _draft())

    specs.redraft_spec(spec.id, old.id)

    s.expire_all()
    assert _candidates(_card(s, s.get(Spec, spec.id))) == ["Print labels for the kitchen"]
    assert requests.WAITING_NEEDS not in s.get(AgentRequest, first.id).action_params
