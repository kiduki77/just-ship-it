"""What a plan or a request may not hold: work an owner directive ruled out, and work the company already did.

Owner directives used to reach agents only as prose in a prompt. The CEO raised a KOSIS download request after
"시장규모 집계 필요 없음", and 원두재고 re-verified one table four times, the last time after "같은 표를 다시 검증하는
업무는 더 만들지 마세요". So each directive carries rules: the CEO reads it once (`orchestration.realign`) and names
the work it rules out as a few keywords, and from then on the server checks every new task, request and scheduled
task against them, in every project of the business. A rule matches a title that holds all of its keywords; titles
only, because an objective or a rationale often mentions the excluded work to say it is excluded.

A keyword matches by its script. One in Latin letters is a whole word, as `i18n/match` reads English, with a plural
-s: "stop running ads" gave "ads", which as a substring withdrew "Approve outreach to the 12 webinar leads". One in
Hangul matches inside a word, since a Korean particle or compound sits right against it ("시장규모" in "국내시장규모"),
except a rule's only keyword when it is SHORT, which matches only where it starts a word ("시장" is in "재래시장").

A task that repeats a finished one is the other half: the planner may re-plan finished work once, and only when it
names the new evidence that calls for it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from justshipit.contracts.requests import RequestStatus
from justshipit.contracts.work import TaskDraft, TaskStatus
from justshipit.db.models import AgentRequest, Project, Task
from justshipit.i18n import Lang, msg

SAME_WORK = 0.7      # share of a title's character pairs another title must hold to be the same work
REDO_LIMIT = 1       # how many times finished work may be done again
MIN_KEYWORD = 2      # letters a Korean keyword needs
MIN_LATIN = 3        # letters a keyword in Latin letters needs: "ad" or "QA" names no one piece of work
SHORT = 3            # a rule's only Korean keyword, when this short, starts a word
TOO_BROAD = 0.3      # share of the business's task and waiting decision titles a rule may match
BROAD_MIN = 3        # titles a rule must match before it is judged too broad, so a business with few tasks can keep one
_SPACE = re.compile(r"[\s\W_]+")
_GAP = r"[\s\W_]*"   # what `_norm` drops, which may sit between a keyword's letters in a title
_HANGUL = re.compile(r"[\uac00-\ud7a3]")


@dataclass(frozen=True)
class Rule:
    n: int               # the directive's number on its project
    project_id: int      # the project the directive was given on
    label: str           # what the owner ruled out, in words
    keywords: tuple[str, ...]

    @property
    def ref(self) -> str:
        """The directive the rule came from, as the owner sees it after the word for an instruction: "#2 (P-1)"."""
        return f"#{self.n} (P-{self.project_id})"


def _norm(text: str) -> str:
    return _SPACE.sub("", text.lower())


def _projects(s: Session, p: Project) -> list[Project]:
    """The projects whose directives bind this one: every unarchived project of the business, or just this one."""
    if p.business_id is None:
        return [p]
    rows = list(s.scalars(select(Project).where(Project.business_id == p.business_id, Project.status != "ARCHIVED")))
    return rows if p in rows else [*rows, p]


def directives(s: Session, p: Project) -> list[tuple[Project, dict]]:
    """Every owner directive that binds this project, oldest first, with the project it was given on."""
    return [(q, d) for q in _projects(s, p) for d in (q.context or {}).get("directives", [])]


def rules(s: Session, p: Project) -> list[Rule]:
    """The rules in force: a stored rule from before rules were screened that `_too_short` refuses applies no more."""
    return [Rule(d["n"], q.id, r["label"], _words(r))
            for q, d in directives(s, p) for r in d.get("rules", []) if _too_short(r) is None]


def _words(r: dict) -> tuple[str, ...]:
    return tuple(k for k in r["keywords"] if _norm(k))


def _too_short(r: dict, lang: Lang = "en") -> str | None:
    """Why a rule's words cannot rule out anything precise, or None. A one-letter word sits in most titles ("표" in
    목표, 발표, 지표), and dropping it from the rule would make the rest match more than the owner said."""
    words = _words(r)
    if not words:
        return msg("work.rule.no_words", lang)
    if short := [k for k in words if _HANGUL.search(k) and len(_norm(k)) < MIN_KEYWORD]:
        return msg("work.rule.one_letter", lang, words=", ".join(short))
    if short := [k for k in words if not _HANGUL.search(k) and len(_norm(k)) < MIN_LATIN]:
        return msg("work.rule.short_latin", lang, words=", ".join(short))
    return None


def _too_broad(r: dict, titles: list[str], lang: Lang) -> str | None:
    rule = Rule(0, 0, r["label"], _words(r))
    hit = sum(1 for t in titles if broken([rule], t))
    if hit >= BROAD_MIN and hit > TOO_BROAD * len(titles):
        return msg("work.rule.too_broad", lang, n=len(titles), hit=hit)
    return None


def screen_rules(s: Session, p: Project, drafted: list[dict], *, lang: Lang) -> tuple[list[dict], list[dict]]:
    """The rules a directive keeps, and the ones refused with why. A rule with a word too short to name one piece of
    work, or that matches more than TOO_BROAD of the titles a rule can take away (the business's tasks and its waiting
    decisions), would cancel work the owner did not name, so it is refused and shown to the owner instead of applied.
    A business with a few tasks often has as many decisions waiting, so counting them too judges it on more titles."""
    ids = [q.id for q in _projects(s, p)]
    titles = [*s.scalars(select(Task.title).where(Task.project_id.in_(ids))),
              *s.scalars(select(AgentRequest.title).where(AgentRequest.project_id.in_(ids),
                                                          AgentRequest.status == RequestStatus.PENDING.value))]
    kept, refused = [], []
    for r in drafted:
        if why := _too_short(r, lang) or _too_broad(r, titles, lang):
            refused.append({**r, "why": why})
        else:
            kept.append(r)
    return kept, refused


@cache
def _pattern(keyword: str, word_start: bool) -> re.Pattern[str]:
    """How a title holds this keyword (see the module docstring). Letters may have spaces or marks between them, as
    the titles' own spacing varies ("시장 규모", "market-size")."""
    if _HANGUL.search(keyword):
        body = _GAP.join(map(re.escape, _norm(keyword)))
        return re.compile((r"(?<![\uac00-\ud7a3])" if word_start else "") + body)
    body = _GAP.join(re.escape(w) for w in _SPACE.split(keyword.lower()) if w)
    return re.compile(rf"(?<![a-z-]){body}(?:e?s)?(?![a-z-])")


def _holds(rule: Rule, title: str) -> bool:
    text = title.lower()
    word_start = len(rule.keywords) == 1 and len(_norm(rule.keywords[0])) <= SHORT
    return all(_pattern(k, word_start).search(text) for k in rule.keywords)


def broken(active: list[Rule], title: str) -> Rule | None:
    """The first rule this title breaks: it holds every keyword of the rule."""
    return next((r for r in active if _holds(r, title)), None)


def _pairs(text: str) -> set[str]:
    t = _norm(text)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def same_work(a: str, b: str) -> bool:
    x, y = _pairs(a), _pairs(b)
    return bool(x and y) and len(x & y) / min(len(x), len(y)) >= SAME_WORK


def done_like(s: Session, p: Project, title: str) -> list[Task]:
    """The finished tasks of the business that did the work this title names."""
    ids = [q.id for q in _projects(s, p)]
    return [t for t in s.scalars(select(Task).where(Task.project_id.in_(ids), Task.status == TaskStatus.DONE.value))
            if same_work(t.title, title)]


def task_problem(s: Session, p: Project, draft: TaskDraft, active: list[Rule], *, lang: Lang) -> str | None:
    """Why this drafted task is not created, in the owner's language, or None."""
    if rule := broken(active, draft.title):
        return msg("work.task.ruled_out", lang, ref=rule.ref, label=rule.label)
    done = done_like(s, p, draft.title)
    if not done:
        return None
    refs = ", ".join(f"T-{t.id}" for t in done)
    if len(done) > REDO_LIMIT:
        return msg("work.task.redone_too_often", lang, n=len(done), refs=refs)
    if not (draft.new_evidence or "").strip():
        return msg("work.task.redo_needs_evidence", lang, refs=refs)
    return None
