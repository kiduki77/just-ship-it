"""Whether a delivered build may be offered to the owner for merge. A build whose setup or core daily flow failed, whose
P1 scenarios did not pass, whose engineering review asked for fixes, or that meets fewer than half of its acceptance
criteria failed its check: the owner is never asked to approve its merge ([재평가] Sam: REQ-97 asked to approve a build
whose setup stopped on "python: not found", 35 of 35 criteria unmet). It goes back to the engineering team for a fix
round, or, when the fix budget cannot cover one, to the owner as fix or drop (engineering/revise.py). A build that
passed or partly passed gets the merge card.

`failures` returns the reasons as catalog keys with their values, which the job keeps (`result["failed"]`) so every
later reading (the merge-again refusal, the card in another language) says the same."""
from __future__ import annotations

from justshipit.contracts.handoff import AcceptanceOut
from justshipit.engineering import stories, walkthrough
from justshipit.i18n import Lang, msg

SETUP_FAILED = "[ci-setup failed]"  # the mark the worker's scripts print when scripts/ci-setup.sh fails
NO_FLOW = "[no scripts/core-flow.sh]"


def failures(out: AcceptanceOut, flow: dict, told: list[dict], review_verdict: str | None,
             run: dict | None = None) -> list[dict]:
    """Why the build failed its check, worst first; an empty list when it passed or partly passed. A core flow that
    could not start for a reason of the host's (Docker down) is no failure of the build: the card says it did not run.
    A must-have user journey of the walkthrough `run` fails it only where a fix round can repair it
    (walkthrough.holds_up)."""
    found: list[dict] = []
    log = flow.get("log") or ""
    if flow.get("ran") and not flow.get("ok"):
        found.append({"key": "build.failed.setup", "vars": {"line": _last_line(log.split(SETUP_FAILED, 1)[1])}}
                     if SETUP_FAILED in log else {"key": "build.failed.run"})
    elif not flow.get("ran") and log.startswith(NO_FLOW):
        found.append({"key": "build.failed.no_flow"})
    if flow.get("ran"):
        said = flow.get("scenarios") or {}
        scenarios = stories.p1_scenarios(told)
        unpassed = [sc for sc in scenarios if said.get(sc["id"]) != "pass"]
        if unpassed:
            found.append({"key": "build.failed.scenarios", "vars": {"n": len(unpassed), "total": len(scenarios)}})
    must = walkthrough.p1(run)
    if broken := [j for j in must if not walkthrough.holds_up(j)]:
        found.append({"key": "build.failed.journeys", "vars": {"n": len(broken), "total": len(must)}})
    if review_verdict not in (None, "pass"):
        found.append({"key": "build.failed.review"})
    met, n = sum(1 for c in out.criteria_results if c.met), len(out.criteria_results)
    if n and met * 2 < n:
        found.append({"key": "build.failed.criteria", "vars": {"met": met, "n": n}})
    return found


def lines(failed: list[dict], lang: Lang) -> list[str]:
    return [msg(x["key"], lang, **x.get("vars", {})) for x in failed]


def _last_line(log: str) -> str:
    """The setup's own last word, which is where a shell names what stopped it."""
    said = [x.strip() for x in log.splitlines() if x.strip() and not x.strip().startswith("(exit")]
    return (said[-1] if said else "")[:200]
