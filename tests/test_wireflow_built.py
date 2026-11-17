"""The merge card shows each planned screen beside the capture of it, because the owner cannot judge a build from text
(owner, 2026-11-11). The data is BUSINE10-0001's again: the screens of test_wireflow.STATES and the captures of its J-10
build."""
from types import SimpleNamespace

import pytest
from test_wireflow import STATES, _draft, _plan, _write

from justshipit.api import app
from justshipit.contracts.handoff import AcceptanceOut, ScreenCheckOut
from justshipit.db.models import AgentRequest, DevJob
from justshipit.engineering import wireflow, worker

# what J-10's capture left: one 1440 shot per screen the engineer named, the first open at two widths, and a state of its
# own that the plan does not have
FILES = ["first-open-1280.png", "first-open-1440.png", "mapping-warning-1440.png", "report-1440.png",
         "error-xlsx-1440.png"]
JOINED = {"first-open": "first-open-1440.png", "mapping-warning": "mapping-warning-1440.png",
          "report": "report-1440.png"}
NOT_SHOT = ["upload-error", "report-empty", "mapping-loading"]


def _check(screen_id: str, built: str = "yes", differs: str = "") -> dict:
    return ScreenCheckOut(screen_id=screen_id, built=built, differs=differs).model_dump()


def _job(files: list[str], *checks: dict, acceptance: bool = True) -> SimpleNamespace:
    result = {"screens": {"files": files, "log": ""}}
    if acceptance:
        result["acceptance"] = AcceptanceOut(accepted=True, criteria_results=[], notes="n", screens=checks) \
            .model_dump(mode="json")
    return SimpleNamespace(id=10, result=result)


def test_a_capture_joins_its_planned_screen_by_id_and_the_second_width_joins_none():
    w = _plan()

    assert wireflow.shot_of(w, FILES) == JOINED
    assert wireflow.missing(w, FILES) == NOT_SHOT, "in plan order, and the 1280 copy of the first open is no capture"
    assert wireflow.shot_of(w, ["first-open-1280.png", "report.png", "report-1440.jpg", "x-1440.png"]) == {}


def test_the_card_pairs_each_planned_screen_with_its_capture_and_the_cpos_word_on_it():
    w = _plan()
    job = _job(FILES, _check("first-open"), _check("mapping-warning", "partly", "The address has no warning."),
               _check("report", "no", "A table of raw rows."), _check("upload-error"))

    got = wireflow.built(w, job)

    assert got["mode"] == "built"
    assert got["order"] == ["first-open", "upload-error", "mapping-warning", "mapping-loading", "report", "report-empty"]
    assert set(got["order"]) == set(got["screens"]) == set(w.screens)
    assert got["screens"]["report"]["built"] == {
        "shot": {"name": "report-1440", "url": "/api/dev-jobs/10/screens/report-1440.png"},
        "verdict": "no", "differs": "A table of raw rows."}
    assert got["screens"]["first-open"]["built"]["verdict"] == "yes"
    assert got["screens"]["report"]["blocks"] == [b.model_dump(mode="json") for b in w.screens["report"].blocks]
    assert "asks" not in got["screens"]["report"], "the sketch is drawn; the spec card's questions stay on that card"


def test_a_planned_screen_with_no_capture_says_not_captured_and_no_verdict_is_taken_for_it():
    job = _job(FILES, _check("report-empty", "yes"))

    got = wireflow.built(_plan(), job)

    assert [i for i, x in got["screens"].items() if x["built"]["shot"] is None] == NOT_SHOT
    assert got["screens"]["report-empty"]["built"] == {"shot": None, "verdict": None, "differs": ""}


def test_a_verdict_for_a_screen_the_plan_lacks_is_dropped_and_the_first_for_one_screen_stands():
    job = _job(FILES, _check("invented", "no", "x"), _check("report", "yes"), _check("report", "no", "second"))

    got = wireflow.built(_plan(), job)

    assert "invented" not in got["screens"]
    assert [x["built"]["verdict"] for x in got["screens"].values()].count("yes") == 1
    assert got["screens"]["report"]["built"]["verdict"] == "yes" and got["screens"]["report"]["built"]["differs"] == ""


def test_a_capture_no_planned_screen_claims_is_extra_and_the_first_opens_other_width_is_not():
    got = wireflow.built(_plan(), _job(FILES))

    assert got["extra"] == [{"name": "error-xlsx-1440", "url": "/api/dev-jobs/10/screens/error-xlsx-1440.png"}]
    left_alone = wireflow.built(_plan([x for x in STATES if x["key"] != "first-open"]), _job(FILES))
    assert [x["name"] for x in left_alone["extra"]] == ["first-open-1440", "error-xlsx-1440"], \
        "a spec that leaves the first screen alone has its capture shown with the others"


def test_a_job_the_check_has_not_judged_still_shows_its_captures():
    got = wireflow.built(_plan(), _job(FILES, acceptance=False))

    assert got["screens"]["report"]["built"]["shot"]["name"] == "report-1440"
    assert got["screens"]["report"]["built"]["verdict"] is None
    assert wireflow.built(_plan(), SimpleNamespace(id=1, result=None))["extra"] == []


def test_the_cpos_sentence_is_read_by_the_owner_without_the_engineers_names():
    got = wireflow.built(_plan(), _job(FILES, _check("report", "partly", "Missing in src/Report.tsx (T-104).")))

    said = got["screens"]["report"]["built"]["differs"]
    assert "T-104" not in said and "Missing" in said


def test_the_acceptance_prompt_lists_the_planned_ids_with_their_files_and_what_is_missing():
    w = _plan()

    asked = worker._screens_ask({"files": FILES, "log": ""}, w)

    assert "## Planned screens" in asked and ".ship/spec/screens.yaml" in asked
    assert "- `report`: `.ship/screens/report-1440.png`" in asked
    assert "- `first-open`: `.ship/screens/first-open-1440.png`" in asked
    assert "- `upload-error`: not captured" in asked and "- `mapping-loading`: not captured" in asked
    assert "error-xlsx" not in asked.split("## Planned screens")[1], "only planned screens are listed"
    assert "its missing capture alone does not make the build unaccepted" in asked
    assert "## Planned screens" not in worker._screens_ask({"files": FILES, "log": ""}), "no plan, no such section"
    assert "## Planned screens" not in worker._screens_ask({"files": [], "log": "x"}, w), "nothing captured"


def test_the_capture_script_names_files_by_the_suffix_the_join_reads():
    script = worker._capture("en")

    assert "shoot first-open-1440 1440,900 /" in script and "shoot first-open-1280 1280,800 /" in script
    assert '"$name-1440"' in script


def test_an_old_verdict_has_no_screens_and_a_verdict_is_one_of_four_words():
    old = AcceptanceOut.model_validate({"accepted": True, "criteria_results": [], "notes": "n"})
    assert old.screens == []
    with pytest.raises(ValueError):
        ScreenCheckOut(screen_id="report", built="maybe", differs="")


def _merge_card(s, project, spec, files: list[str], *checks: dict) -> AgentRequest:
    spec.approved_commit_sha = spec.draft_commit_sha
    job = DevJob(spec_id=spec.id, status="DELIVERED", branch="main", result=_job(files, *checks).result)
    s.add(job)
    s.flush()
    card = AgentRequest(project_id=project.id, from_agent="product", type="APPROVAL", title="merge", summary="s",
                        rationale="r", status="PENDING", policy_mode="approve", action_kind="dev.merge",
                        action_params={"dev_job_id": job.id})
    s.add(card)
    s.commit()
    return card


def test_the_merge_card_of_a_spec_with_screens_carries_them_built(s, project, monkeypatch):
    spec = _write(s, project, monkeypatch, _draft())
    card = _merge_card(s, project, spec, FILES, _check("report", "partly", "One more column."))

    got = wireflow.for_card(s, card)

    assert got["mode"] == "built" and list(got["screens"]) == list(_plan().screens)
    assert got["screens"]["report"]["built"]["verdict"] == "partly"
    assert app.get_request(card.id)["screens"] == got, "the detail fetch serves it beside the try-it card"


def test_the_merge_card_of_a_spec_from_before_planned_screens_is_the_card_it_always_was(s, project, monkeypatch):
    old = _write(s, project, monkeypatch, _draft(design_brief=None))

    card = _merge_card(s, project, old, FILES)

    assert wireflow.for_card(s, card) is None and app.get_request(card.id)["screens"] is None
    assert wireflow.for_card(s, AgentRequest(action_kind="dev.merge", action_params={})) is None
