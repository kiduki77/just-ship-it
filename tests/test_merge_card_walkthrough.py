"""The merge card's read model for the walkthrough (roadmap 0.2b, design 2.4 and 2.5). REQ-7 showed 27 rows under
"Scenarios", most not scenarios, each cut to 90 characters: the card now carries the run of its own head, what each
step printed as it printed it, and every other check folded by what it protects. An old card keeps reading."""
import pytest
from justshipit.contracts.handoff import (
    AcceptanceOut,
    CoreFlowOut,
    CriterionResultOut,
    ProductNeedOut,
    StepVerdictOut,
)
from justshipit.db.models import DevJob
from justshipit.engineering import merge_review, stories, walkthrough
from justshipit.i18n import msg

LONG = ("a feature list of six features where one of them is a whole paragraph that runs on well past ninety "
        "characters, the way founders write them")
STORIES = [
    {"id": "US1", "priority": "P1", "title": "Specs per screen", "story": "Mina gets one spec per screen she plans.",
     "why_priority": "w", "independent_test": "t", "scenarios": [
         {"id": "US1.1", "given": LONG, "when": "she runs generate", "then": "six spec files appear",
          "guards": "no feature is lost on the way", "empty": False},
         {"id": "US1.2", "given": "the same list", "when": "she runs generate twice into two folders",
          "then": "both folders hold the same specs", "guards": "the same list gives the same spec every time",
          "empty": False}]},
    {"id": "US2", "priority": "P2", "title": "A check of an edited spec", "story": "Mina checks a spec she edited.",
     "why_priority": "w", "independent_test": "t", "scenarios": [
         {"id": "US2.1", "given": "an edited spec", "when": "she runs check", "then": "it lists missing states",
          "guards": "", "empty": False}]},
]
DOC = {"about": {"what": "uxscope turns your app's feature list into a flow spec for each screen",
                 "who": "a solo founder planning an app who is not a designer",
                 "problem": "before building, they don't know which screens they need"},
       "journeys": [{"id": "JN1", "priority": "P1", "title": "Turn a feature list into screen specs",
                     "persona": "Mina, planning a habit-tracker app alone",
                     "why": "she wants to know which screens to build before she pays a developer",
                     "brings": "6 features in a text file", "success": "one spec per screen",
                     "steps": [{"id": "JN1.1", "do": "Start a project for her app", "why": "so uxscope has a folder",
                                "see": "a folder habit-app with a features file"},
                               {"id": "JN1.2", "do": "Turn her features into specs", "why": "to see the screens",
                                "see": "one spec per screen in habit-app/out"},
                               {"id": "JN1.3", "do": "Ask the AI to review a spec", "why": "to find states she missed",
                                "see": "a list of states she hadn't thought of"},
                               {"id": "JN1.4", "do": "Open the spec page", "why": "to read it as a page",
                                "see": "the Today screen's spec"}]}]}
PRINTED = "Wrote 6 specs to ./habit-app/out\n  spec.json\n  spec.md\n  prompt.md\n\x1b-free --out kept"


def _capture(exit_code, text=PRINTED, shot=None):
    return {"text": text, "lines": text.count("\n") + 1, "cut_after": None, "exit": exit_code, "ms": 1200,
            "shot": shot, "file": {"path": "habit-app/out/spec.md", "text": "# Today\n- empty state", "lines": 2,
                                   "volatile": [], "binary": False, "size": 22} if text == PRINTED else None,
            "volatile": [[10, 14]], "scrubbed": []}


def _ran(sid, capture=None, not_run=None, kind="run", action=None):
    return {"id": sid, "kind": kind, "action": action or f"npx uxscope {sid}", "expect": "ok", "capture": capture,
            "not_run": not_run, "signals": [], "seed": [{"path": "habit-app/features.md", "text": "- Today\n- Streaks"}]}


WALK = {"setup": [_ran("setup.1", _capture(0, "added 41 packages in 9s"), action="sh scripts/ci-setup.sh")],
        "steps": {"JN1.1": _ran("JN1.1", _capture(0, "Created habit-app/features.md")),
                  "JN1.2": _ran("JN1.2", _capture(0)),
                  "JN1.3": _ran("JN1.3", _capture(1, "claude: not logged in")),
                  "JN1.4": _ran("JN1.4", _capture(None, "", shot="JN1.4.png"), kind="open", action="/today")},
        "setup_ms": 61000, "ms": 9400, "lint": []}
VERDICTS = [StepVerdictOut(step_id="JN1.1", verdict="passed", looks_like="a features file appears"),
            StepVerdictOut(step_id="JN1.2", verdict="passed", looks_like="six spec files listed",
                           hint="Your times will differ"),
            StepVerdictOut(step_id="JN1.3", verdict="differs", fault="environment", need="Claude CLI login",
                           looks_like="it asks for a Claude login"),
            StepVerdictOut(step_id="JN1.4", verdict="passed", looks_like="the Today spec as a page")]
NEEDS = [{"name": "Claude CLI login", "what": "the AI review", "required": True, "without": "review stops"}]
FLOW = {"ran": True, "ok": True, "log": "ok", "scenarios": {"US1.1": "pass", "US1.2": "fail"}}


def _run(head: str) -> dict:
    return walkthrough.held(WALK, DOC, STORIES, VERDICTS, NEEDS, head=head, spec_commit="5" * 40,
                            ran_at="2026-11-13T09:00:00+00:00")


def _rules_and_asks() -> list[CriterionResultOut]:
    return [CriterionResultOut(criterion="It writes one spec per screen", met=True, evidence="src/generate.ts"),
            CriterionResultOut(criterion="Never send the feature list anywhere", met=True, evidence="no network",
                               about="rule"),
            CriterionResultOut(criterion="Name the output folder in the summary", met=False, evidence="it does not",
                               about="asked")]


def _new_job(heads: tuple[str, ...] = ("a" * 40,)) -> DevJob:
    """A build accepted after 0.2b: its acceptance criteria as the worker holds them, and a run per head."""
    from justshipit.engineering import worker

    runs: list = []
    for head in heads:
        runs = walkthrough.store({"walkthroughs": runs}, _run(head))
    cpo = AcceptanceOut(accepted=True, notes="ok", criteria_results=_rules_and_asks(),
                        core_flow=CoreFlowOut(realistic=True, quiet_day=True, evidence="fixtures/core-flow/ is there"))
    out = worker.held_to_core_flow(cpo, FLOW, "en", STORIES, runs[-1])
    return DevJob(id=7, branch="ai/spec-uxscope-a1",
                  result={"acceptance": out.model_dump(mode="json"), "walkthroughs": runs, "head": heads[-1]})


def _legacy(about: bool = True) -> DevJob:
    """REQ-7's shape: 27 criteria, the core flow's, P1 scenarios in their 90-character form, the owner's rules, what
    the owner asked this round, and the agent's own, and no walkthrough."""
    told = [{"id": f"US{i}", "priority": "P1", "title": f"Story {i}", "story": "s", "why_priority": "w",
             "independent_test": "t",
             "scenarios": [{"id": f"US{i}.{k}", "given": LONG, "when": f"you run generate {k} with --out out{k}",
                            "then": "three files appear, " + "and nothing else is touched " * 4, "empty": False}
                           for k in (1, 2)]} for i in range(1, 8)]
    flow = {"ran": True, "ok": True, "log": "ok", "scenarios": {f"US{i}.{k}": "pass" for i in range(1, 8)
                                                                  for k in (1, 2)}}
    held = [{"criterion": msg("build.core_flow.criterion", "ko"), "met": True, "evidence": "e"},
            *stories.criteria(told, flow, "ko")]
    rules = [{"criterion": f"Rule {i}", "met": True, "evidence": "e", **({"about": "rule"} if about else {})}
             for i in range(4)]
    asks = [{"criterion": f"Ask {i}", "met": i != 0, "evidence": "e", **({"about": "asked"} if about else {})}
            for i in range(3)]
    own = [{"criterion": f"Criterion {i}", "met": True, "evidence": "e"} for i in range(5)]
    return DevJob(id=4, branch="ai/spec-uxscope-a1",
                  result={"acceptance": {"criteria_results": [*own, *held, *rules, *asks]}, "head": "f" * 40})


# ------------------------------------------------------------------ an old card keeps reading

def test_a_req7_shaped_job_reads_no_walkthrough_and_folds_its_27_checks_into_five_groups():
    review = merge_review.card(_legacy(), "uxscope", "f" * 40)

    assert review["walkthrough"] is None
    checked = review["checked"]
    assert [g["kind"] for g in checked["groups"]] == ["core_flow", "scenarios", "rule", "asked", "spec"]
    assert [g["total"] for g in checked["groups"]] == [1, 14, 4, 3, 5]
    assert (checked["total"], checked["met"]) == (27, 26)
    scenario = checked["groups"][1]["items"][0]
    assert scenario["scenario"]["when"].startswith("you run generate 1") and scenario["guards"] == ""
    assert len(review["criteria"]) == 27, "the list Before you decide reads stays as it was"


def test_a_legacy_job_whose_criteria_never_said_what_they_are_about_folds_them_with_the_spec():
    groups = merge_review.card(_legacy(about=False), "uxscope")["checked"]["groups"]

    assert [(g["kind"], g["total"]) for g in groups] == [("core_flow", 1), ("scenarios", 14), ("spec", 12)]


def test_a_script_that_never_ran_is_one_check_on_a_legacy_job_and_each_scenario_unmet_on_a_new_one():
    lost = {"ran": False, "ok": False, "log": "[no scripts/core-flow.sh]"}
    [all_of_them] = stories.criteria(STORIES, lost, "ko")
    legacy = DevJob(id=4, branch="b", result={"acceptance": {"criteria_results": [all_of_them]}})
    assert [(g["kind"], g["total"], g["met"]) for g in merge_review.card(legacy, "x")["checked"]["groups"]] == [
        ("scenarios", 1, 0)]

    job = _new_job()
    job.result["acceptance"]["criteria_results"] = [all_of_them]
    [story] = merge_review.card(job, "x", "a" * 40)["checked"]["groups"]
    assert (story["kind"], story["title"], story["total"], story["met"]) == ("story", "Specs per screen", 2, 0)
    assert [x["evidence"] for x in story["items"]] == [all_of_them["evidence"]] * 2


# ------------------------------------------------------------------ a new card

def test_a_new_job_gives_its_walkthrough_and_its_checks_by_what_they_protect():
    review = merge_review.card(_new_job(), "uxscope", "a" * 40)

    wt = review["walkthrough"]
    assert {k: wt[k] for k in ("head", "spec_commit", "ran_at", "ms", "setup_ms", "later")} == {
        "head": "a" * 40, "spec_commit": "5" * 40, "ran_at": "2026-11-13T09:00:00+00:00", "ms": 9400,
        "setup_ms": 61000, "later": False}
    assert wt["about"] == DOC["about"]
    assert [(x["id"], x["state"], x["action"]) for x in wt["setup"]] == [("setup.1", "passed",
                                                                         "sh scripts/ci-setup.sh")]
    [journey] = wt["journeys"]
    assert {k: journey[k] for k in ("id", "priority", "title", "persona", "why", "brings", "success")} == {
        k: DOC["journeys"][0][k] for k in ("id", "priority", "title", "persona", "why", "brings", "success")}
    steps = journey["steps"]
    assert [(x["state"], x["not_run"], x["need"]) for x in steps] == [
        ("passed", None, None), ("passed", None, None), ("not_run", "needs", "Claude CLI login"),
        ("passed", None, None)]
    two = steps[1]
    assert (two["do"], two["why"], two["see"]) == ("Turn her features into specs", "to see the screens",
                                                   "one spec per screen in habit-app/out")
    assert (two["looks_like"], two["hint"], two["judged"]) == ("six spec files listed", "Your times will differ", True)
    assert two["capture"]["text"] == PRINTED, "what the step printed passes as stored: its lines and code names"
    assert two["capture"]["file"]["text"] == "# Today\n- empty state" and two["capture"]["shot"] is None
    assert two["seed"] == [{"path": "habit-app/features.md", "text": "- Today\n- Streaks"}]
    assert steps[3]["capture"]["shot"] == f"/api/dev-jobs/7/walk/{'a' * 12}/JN1.4.png"

    checked = review["checked"]
    assert [(g["kind"], g["title"], g["total"], g["met"]) for g in checked["groups"]] == [
        ("core_flow", "", 1, 1), ("story", "Specs per screen", 2, 1), ("rule", "", 1, 1), ("asked", "", 1, 0),
        ("spec", "", 1, 1)]
    assert (checked["total"], checked["met"]) == (6, 4)
    story = checked["groups"][1]
    assert story["purpose"] == "Mina gets one spec per screen she plans."
    first, second = story["items"]
    assert first["scenario"] == {"given": LONG, "when": "she runs generate", "then": "six spec files appear"}
    assert len(first["scenario"]["given"]) > 90 and first["guards"] == "no feature is lost on the way"
    assert (second["met"], second["guards"]) == (False, "the same list gives the same spec every time")
    journey_check = msg("walkthrough.criterion", "en", title="Turn a feature list into screen specs",
                        persona="Mina, planning a habit-tracker app alone")
    assert journey_check in [c["criterion"] for c in review["criteria"]], "Before you decide still reads it"
    assert journey_check not in [x["criterion"] for g in checked["groups"] for x in g["items"]]


def test_a_card_reads_its_own_heads_run_and_one_with_no_run_reads_the_newest_marked_later():
    job = _new_job(("a" * 40, "b" * 40, "c" * 40))

    assert merge_review.card(job, "x", "a" * 40)["walkthrough"]["head"] == "a" * 40
    assert merge_review.card(job, "x", "a" * 40)["walkthrough"]["later"] is False
    newest = merge_review.card(job, "x", "9" * 40)["walkthrough"]
    assert (newest["head"], newest["later"]) == ("c" * 40, True)
    assert newest["journeys"][0]["steps"][3]["capture"]["shot"].startswith(f"/api/dev-jobs/7/walk/{'c' * 12}/")
    assert merge_review.card(job, "x")["walkthrough"]["later"] is False, "a card with no head claims no version"


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_the_merge_card_reads_the_walkthrough_of_the_head_its_acceptance_filed_it_for(s, project, monkeypatch, lang):
    from justshipit.db.models import AgentRequest
    from justshipit.engineering import journeys, worker
    from justshipit.services import app_settings, requests
    from test_usable import USE, _delivered, _sh

    monkeypatch.setattr(app_settings, "language", lambda: lang)
    job, _, repo = _delivered(s, project, f"wt-card-{lang}-{project.id}")
    head = _sh(repo, "git", "rev-parse", job.branch)
    s.get(DevJob, job.id).result = {"head": head}
    s.commit()
    monkeypatch.setattr(worker.registry, "read_at", lambda *a: "- criteria")
    monkeypatch.setattr(worker, "_write_spec", lambda wt, h, given: None)
    monkeypatch.setattr(worker, "run_core_flow", lambda export: {"ran": True, "ok": True, "log": "ok"})
    monkeypatch.setattr(journeys, "at", lambda path, commit: DOC)
    monkeypatch.setattr(walkthrough, "walk", lambda repo, ref, doc, **kw: WALK)
    login = ProductNeedOut(name="Claude CLI login", what="the AI review", required=True, without="review stops")
    monkeypatch.setattr(worker, "run_agent", lambda *a, **kw: AcceptanceOut(
        accepted=True, notes="ok", use=USE.model_copy(update={"needs": [login]}), journeys=VERDICTS,
        criteria_results=_rules_and_asks(), core_flow=CoreFlowOut(realistic=True, quiet_day=True, evidence="e")))

    worker.acceptance(job.id)

    s.expire_all()
    card = s.query(AgentRequest).filter_by(project_id=project.id, action_kind="dev.merge").one()
    assert card.action_params["head"] == head
    done = s.get(DevJob, job.id)
    done.result = {**done.result, "walkthroughs": [*done.result["walkthroughs"], _run("e" * 40)]}
    s.commit()
    review = requests.read_model(s, card, lang, None)["review"]
    assert (review["walkthrough"]["head"], review["walkthrough"]["later"]) == (head, False), \
        "its own head's run, not the newer one"
    three = review["walkthrough"]["journeys"][0]["steps"][2]
    assert (three["state"], three["need"]) == ("not_run", "Claude CLI login")
    assert [g["kind"] for g in review["checked"]["groups"]] == ["core_flow", "rule", "asked", "spec"]
