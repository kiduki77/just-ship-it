"""Finding people the owner may meet, from what they said in public.

Two background jobs call the research agent once each. `find_people` looks for people who wrote that they have the
problem, and `find_threads` picks public questions the owner can answer. The agent only proposes. Everything it
returns is untrusted, and the server keeps a candidate only when it opened the link itself and found the quote on the
page with the author's handle near it. What it cannot confirm is dropped and counted.

Web text is data. The agent is told so, and the only web-derived text this module puts into a prompt (the people and
threads already known) sits inside a fence the agent is told never to obey.

Where it looks follows the customer description and the business's market (`people.where`,
`audience.market_of_business`), and what it writes for the owner follows the owner's language (`lang`, read by the job
handler).
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from justshipit.agents.executor import run_agent
from justshipit.contracts.people import CandidateOut, FindPeopleOut, FindThreadsOut, ThreadOut
from justshipit.db.models import Business, BusinessBrief, OwnerTask, Person
from justshipit.db.session import session_scope
from justshipit.i18n import NAMES, Lang, msg
from justshipit.services import (
    activity,
    audience,
    budget,
    channels,
    owner_tasks,
    people,
    people_tasks,
    profile,
    queue,
    roadmap,
)
from justshipit.services.evidence import fetch_json, fetch_page
from justshipit.services.owner_tasks import CardRefused
from justshipit.services.profile import Profile
from justshipit.settings import company_config, settings

log = logging.getLogger(__name__)

AGENT, PURPOSE = "research", "people"
MAX_CANDIDATES, MAX_THREADS = 10, 5
CONTACT_BATCH = 3
MAX_UNWORKED = 20
MIN_QUOTE = 20
NEAR = 200
KNOWN_SHOWN = 60
FENCE_OPEN, FENCE_CLOSE = "<<<WEB-DATA", "WEB-DATA>>>"
BUILDER_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
BRIEF_KEYS = ("one_liner", "customer_problem", "alternative_value", "path", "reverse_100", "goal_deadline")
# How sure a plan line is, as the agent reads it (`roadmap.PROVENANCE_KO` has the owner's words).
SURE = {"said": "the owner said so", "confirmed": "confirmed", "guess": "a guess", "unknown": "unknown"}

NOTHING_TO_SEARCH = "no known place to search for this customer description, so the finder did not search"

Fetch = Callable[[str], tuple[str, str, str | None]]
KINDS = ("find_people", "find_threads")


def _no_api(url: str) -> tuple[str, str, str | None]:
    return "url_unreachable", "no API", None


def _default_api(fetch: Fetch) -> Fetch:
    """The platform APIs are read only when pages are read for real, so a test that supplies its own pages never
    reaches the network."""
    return fetch_json if fetch is fetch_page else _no_api


# ------------------------------------------------------------------ the prompt

_FENCE_WORDS = re.compile(f"{re.escape(FENCE_OPEN)}|{re.escape(FENCE_CLOSE)}", re.IGNORECASE)


def fenced(label: str, lines: list[str]) -> str:
    """Text that came from the web, as a block the agent is told is data. Each line is one line, and the fence words
    cannot appear inside it in any case, so a line cannot close the fence and write instructions after it."""
    body = "\n".join(_FENCE_WORDS.sub("[fence]", " ".join(line.split())) for line in lines)
    return f"{label}\n{FENCE_OPEN}\n{body or '(none)'}\n{FENCE_CLOSE}"


@lru_cache
def _system(name: str) -> str:
    return (settings().prompts_dir / f"{name}.md").read_text(encoding="utf-8")


def brief_text(brief: BusinessBrief) -> str:
    """The lines of the sections that say who the customer is and where to look, with how sure each line is. An older
    brief's sections are read under the registry's keys."""
    out = []
    for sec in roadmap.normalize_sections(brief.sections):
        lines = [x for x in sec["lines"] if x["provenance"] != "blank"] if sec["key"] in BRIEF_KEYS else []
        if lines:
            bullets = "\n".join(f"- ({SURE[x['provenance']]}) {x['text']}" for x in lines)
            out.append(f"### {sec['title']}\n{bullets}")
    return "\n\n".join(out) or "(the plan has no lines here)"


@dataclass(frozen=True)
class Look:
    """Who the business's customers are and where to look for them: the description, the market, and the one list of
    places `people.where` gives for them, which the prompt tells the agent about and the server then holds it to."""
    prof: Profile
    market: Lang
    searched: tuple[str, ...]
    by_hand: tuple[str, ...]


def where_to_look(look: Look) -> str:
    """The sites the agent may search for this description and market, each with the `platform` key to return, and the
    places it is told it cannot open. The server drops a find on any other site. Each place is named the way its market
    names it, since that is what the agent searches for."""
    market = look.market
    sites = []
    for key in look.searched:
        place, hosts = people.PLACES[key], _hosts(key)
        sites.append(f"- `{key}` {place.names[market]} ({', '.join(hosts[:3]) or 'the hosts in the config'}): {place.hint}")
    blind = ", ".join(people.PLACES[k].names[market] for k in look.by_hand)
    return "\n".join([
        "## Where to search",
        f"The business's customers write in {NAMES[market]}, so search in {NAMES[market]}.",
        ("Search only the sites below. The server drops anything found on another site. Put the key before each site in "
         "`platform`."),
        *sites,
        *(["", "## Where not to search",
           (f"{blind}: their posts need a login or have no web address, so do not search them and do not report finds "
            "there. The owner looks there in person.")] if blind else [])])


PLACE_KEYS = ("one_liner", "background", "path")


def where_it_is(brief: BusinessBrief, prof: Profile) -> str:
    """For a business whose customers are reached in person nearby, the plan's lines that say where it is, and the rule
    that keeps the finds there: a 망원동 shop is no help to someone asking near 건대. Empty for any other business."""
    if not prof.has("reach", "local"):
        return ""
    lines = [x["text"] for sec in roadmap.normalize_sections(brief.sections) if sec["key"] in PLACE_KEYS
             for x in sec["lines"] if x["provenance"] != "blank"]
    return "\n".join([
        "## Where the business is",
        "Its customers come in person, so only people nearby can become customers. What the plan says about where it is:",
        *(f"- {x}" for x in lines),
        ("Pick only posts and people in that neighborhood or the one next to it. A post that names another "
         "neighborhood, district, station or campus area is not for this owner, however close its topic. When a post "
         "names no place, keep it only if the site shows it was written nearby, such as a Daangn post's "
         "neighborhood.")])


def people_prompt(brief: BusinessBrief, known: list[Person], look: Look) -> str:
    shown = [f"{p.platform} {p.handle} {p.where_found_url}" for p in known[:KNOWN_SHOWN]]
    return "\n\n".join(x for x in [f"Today: {date.today().isoformat()}", "## The plan\n" + brief_text(brief),
                        profile.prompt_block(look.prof, "en"),
                        where_it_is(brief, look.prof), where_to_look(look),
                        fenced("## Already on the list (do not return them again)", shown),
                        (f"Find up to {MAX_CANDIDATES} people in this business's target customers who described "
                         "this problem in a public post.")] if x)


def threads_prompt(brief: BusinessBrief, seen: list[str], look: Look) -> str:
    return "\n\n".join(x for x in [f"Today: {date.today().isoformat()}", "## The plan\n" + brief_text(brief),
                        profile.prompt_block(look.prof, "en"),
                        where_it_is(brief, look.prof), where_to_look(look),
                        fenced("## Posts already picked (do not return them again)", seen[:KNOWN_SHOWN]),
                        f"Pick 3 to {MAX_THREADS} public questions the owner can help with this week."] if x)


# ------------------------------------------------------------------ confirming a link, a quote and an author

def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _words(text: str) -> str:
    """Letters and digits only, one space between runs, so markup, punctuation and line breaks cannot hide a match."""
    return re.sub(r"[\W_]+", " ", _fold(text)).strip()


def _indexed(text: str) -> tuple[str, list[int], str]:
    """`_words` of the text, where each of its characters sits in the folded text, and the folded text."""
    light, out, pos, gap = _fold(text), [], [], True
    for i, ch in enumerate(light):
        if ch.isalnum():
            out.append(ch)
            pos.append(i)
            gap = False
        elif not gap:
            out.append(" ")
            pos.append(i)
            gap = True
    return "".join(out), pos, light


def _hosts(platform: str) -> tuple[str, ...]:
    if platform == "forum":
        return tuple(h.lower() for h in company_config().get("people", {}).get("forum_hosts", ()))
    place = people.PLACES.get(platform)
    return place.hosts if place else ()


def _host_problem(platform: str, url: str) -> str | None:
    """`wrong_site` unless the link is on one of the platform's hosts, exactly. A forum is only a host the config lists."""
    parts = urlparse(url)
    try:
        port = parts.port
    except ValueError:
        return "wrong_site"
    host = (parts.hostname or "").rstrip(".").lower()
    return None if host in _hosts(platform) and port in (None, 80, 443) else "wrong_site"


_BYLINE = re.compile(r"\b(?:commented|opened|wrote|replied|posted|answered|asked|submitted|reviewed|said)\b")
_BADGE = re.compile(r"\b(?:owner|member|collaborator)\b")
_GITHUB_ITEM = re.compile(r"^/(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?:issues|pull)/(?P<n>\d+)/?$")


def _handle_pattern(handle: str) -> re.Pattern[str]:
    """The handle as a whole name: not part of a longer word, not an `@mention`, not part of a path or a domain."""
    return re.compile(rf"(?:(?<![\w@./-])|(?<=\bu/)){re.escape(_fold(handle))}(?![\w-])")


def _github_item(url: str, quote: str, api: Fetch) -> dict | None:
    """The issue or comment of the public REST API whose text holds the quote, which names its author and says whether
    that author builds the project. None when the link is no issue, the API is out of reach, or no item holds it."""
    m = _GITHUB_ITEM.match(urlparse(url).path)
    if not m:
        return None
    base = f"https://api.github.com/repos/{m['owner']}/{m['repo']}/issues/{m['n']}"
    for api_url in (base, f"{base}/comments?per_page=100"):
        status, _, body = api(api_url)
        if status != "url_ok" or not body:
            continue
        try:
            data = json.loads(body)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and quote in _words(str(item.get("body") or "")):
                return item
    return None


def _author_problem(platform: str, url: str, handle: str, quote: str, light: str, start: int, api: Fetch) -> str | None:
    """Why the quote is not the handle's, or that the handle builds what the owner would sell, or None. The handle
    must stand within NEAR characters before the quote, outside it, as the nearest byline: a second byline between
    them means the quote is another author's."""
    h = _fold(handle)
    owner = urlparse(url).path.split("/")[1:2] if platform == "github" else []
    if owner and owner[0].lower() == h:
        return "builder"
    if platform == "github" and (item := _github_item(url, quote, api)) is not None:
        if str((item.get("user") or {}).get("login", "")).lower() != h:
            return "author_not_found"
        return "builder" if item.get("author_association") in BUILDER_ASSOCIATIONS else None
    window = light[max(0, start - NEAR):start]
    found = list(_handle_pattern(handle).finditer(window))
    if not found:
        return "author_not_found"
    gap = window[found[-1].end():]
    if len(_BYLINE.findall(gap)) > 1:
        return "author_not_found"
    if platform == "github" and _BADGE.search(gap):
        return "builder"
    if platform == "hn" and "show hn" in light and re.search(rf"\bby {re.escape(h)}\b", light):
        return "builder"
    return None


def confirm(platform: str, url: str, quote: str, handle: str, fetch: Fetch, api: Fetch = _no_api) -> str | None:
    """None when the page at `url` holds `quote` and `handle` is its author, otherwise the reason. Only the handle
    counts, never a display name. A page the site blocks cannot be confirmed, so it is dropped, not trusted."""
    status, _, page = fetch(url)
    if status == "unverifiable":
        return "blocked"
    if status != "url_ok":
        return "unreachable"
    if page is None:
        return "no_text"
    q = _words(quote)
    if len(q) < MIN_QUOTE:
        return "quote_short"
    words, pos, light = _indexed(page)
    at = words.find(q)
    if at < 0:
        return "quote_not_found"
    return _author_problem(platform, url, handle, q, light, pos[at], api)


# ------------------------------------------------------------------ one run

@dataclass
class Report:
    asked: int = 0
    verified: int = 0
    added: int = 0
    duplicate: int = 0
    dropped: Counter[str] = field(default_factory=Counter)
    tasks: int = 0
    notes: list[str] = field(default_factory=list)


def _precheck(platform: str, url: str, texts: list[str], searched: tuple[str, ...]) -> str | None:
    """Why a candidate is dropped before the server opens its link, or None. Only the places searched for this
    business count: a GitHub find for a dog-walking business is dropped unopened."""
    if platform not in searched:
        return "platform"
    if channels.link_problem(None, url):
        return "invalid"
    if reason := _host_problem(platform, url):
        return reason
    if any(people.contact_info_problem(t) for t in texts):
        return "contact_info"
    return None


def _content_problem(scanned: list[str], prose: list[str]) -> str | None:
    """Why text from the candidate may not reach a card. `scanned` is every text that will be shown, the person's own
    words included, and gets the forbidden-ask scan; `prose` is what the model wrote and must hold no link, command or
    instruction. A quote may hold a link, since people paste them."""
    if channels.forbidden_reason(scanned):
        return "forbidden_ask"
    if any(people_tasks.hostile_problem(t) for t in prose):
        return "hostile_text"
    return None


def _screen_candidate(c: CandidateOut, fetch: Fetch, api: Fetch, searched: tuple[str, ...], lang: Lang,
                      ) -> tuple[people.NewPerson | None, str | None]:
    """The person to add, or the reason the candidate is dropped."""
    if reason := _precheck(c.platform, c.where_found_url, [c.handle, c.display_name, c.quote, c.why,
                                                          *c.questions, *c.talking_points], searched):
        return None, reason
    if reason := _content_problem([c.handle, c.display_name, c.quote, c.why], [c.display_name, c.why]):
        return None, reason
    if reason := confirm(c.platform, c.where_found_url, c.quote, c.handle, fetch, api):
        return None, reason
    questions, _ = people_tasks.clean_points(c.questions, people_tasks.POINTS_MAX)
    points, _ = people_tasks.clean_points(c.talking_points, people_tasks.POINTS_MAX)
    new = people.NewPerson(c.platform, c.handle, c.where_found_url, "similar_tool" if c.from_alternative else "public_problem",
                           c.display_name, why=" ".join(c.why.split())[:people.MAX_WHY], quote=" ".join(c.quote.split()),
                           prep={"questions": questions, "talking_points": points})
    try:
        return people.validate(new, lang=lang), None
    except people.PeopleError:
        return None, "invalid"


def _each(items: list, work: Callable) -> list:
    with ThreadPoolExecutor(max_workers=5) as pool:
        return list(pool.map(work, items))


def _look(s: Session, business_id: int) -> Look:
    prof, market = profile.of(s, business_id), audience.market_of_business(s, business_id)
    return Look(prof, market, *people.where(prof, market))


def find_people(business_id: int, *, lang: Lang, fetch: Fetch = fetch_page, api: Fetch | None = None) -> Report:
    """Look for people who said in public that they have the problem. The warm list is imported first and needs no
    model call. Nothing is added when the call must wait: the job runs again later. `lang` is the owner's language,
    for what the run writes on the list and the cards."""
    with session_scope() as s:
        brief = roadmap.approved_brief(s, business_id)
        if brief is None or not _active(s, business_id):
            log.info("find_people B-%s: no approved brief, or the business is not active", business_id)
            return Report()
        people_tasks.import_warm(s, business_id, lang=lang)
        unworked = s.scalar(select(func.count()).select_from(Person).where(
            Person.business_id == business_id, Person.stage == "found", Person.archived_at.is_(None)))
        known = list(s.scalars(select(Person).where(Person.business_id == business_id, Person.archived_at.is_(None))
                               .order_by(Person.id.desc()).limit(KNOWN_SHOWN)))
        look = _look(s, business_id)
        prompt = people_prompt(brief, known, look)
    if not look.searched:
        return Report(notes=[NOTHING_TO_SEARCH])
    if unworked >= MAX_UNWORKED:
        log.info("find_people B-%s: %s people wait for the owner already", business_id, unworked)
        return Report(notes=[f"{unworked} people are waiting for the owner, so the finder did not search"])
    budget.gate_step("people_find")
    out = run_agent(AGENT, PURPOSE, prompt, FindPeopleOut, project_id=None, business_id=business_id, system_override=_system("find_people"), max_turns=50)
    report = Report(asked=len(out.candidates))
    candidates = out.candidates[:MAX_CANDIDATES]
    if len(out.candidates) > len(candidates):
        report.dropped["over_limit"] = len(out.candidates) - len(candidates)
    screened = _each(candidates, lambda c: _screen_candidate(c, fetch, api or _default_api(fetch), look.searched, lang))
    with session_scope() as s:
        for new, reason in screened:
            if new is None:
                report.dropped[reason] += 1
                continue
            report.verified += 1
            person, outcome = people.insert(s, business_id, new, lang=lang)
            if outcome == "created":
                report.added += 1
                if (report.added <= CONTACT_BATCH
                        and people_tasks.create_task(s, person.id, "contact", lang=lang, actor="system") is not None):
                    report.tasks += 1
            elif outcome == "duplicate":
                report.duplicate += 1
            else:
                report.dropped["archived"] += 1
        _record(s, business_id, "people_find_ran", "people", report, lang)
    return report


def _thread_of(t: ThreadOut, fetch: Fetch, api: Fetch, searched: tuple[str, ...],
               lang: Lang) -> tuple[people_tasks.Thread | None, str | None]:
    """The thread as a card will carry it, or why it is dropped."""
    texts = [t.asker_handle, t.quote, t.asks, *t.talking_points]
    if reason := _precheck(t.platform, t.where_found_url, texts, searched):
        return None, reason
    if reason := _content_problem([t.asker_handle, t.quote, t.asks, *t.talking_points], [t.asks]):
        return None, reason
    if reason := confirm(t.platform, t.where_found_url, t.quote, t.asker_handle, fetch, api):
        return None, reason
    points, _ = people_tasks.clean_points(t.talking_points, people_tasks.POINTS_MAX)
    asks = " ".join(t.asks.split())
    thread = people_tasks.Thread(t.platform, t.where_found_url, people.normalize_handle(t.platform, t.asker_handle),
                                 " ".join(t.quote.split()), asks, points)
    try:
        people_tasks.thread_card(thread, lang=lang)
    except CardRefused as exc:
        return None, exc.rule
    return thread, None


def find_threads(business_id: int, *, lang: Lang, fetch: Fetch = fetch_page, api: Fetch | None = None) -> Report:
    """Pick the week's public questions the owner can answer, as `participate` tasks."""
    with session_scope() as s:
        brief = roadmap.approved_brief(s, business_id)
        if brief is None or not _active(s, business_id):
            return Report()
        if (waiting := people_tasks.open_participate(s, business_id)) >= people_tasks.MAX_OPEN_PARTICIPATE:
            return Report(notes=[f"{waiting} posts to answer are open, so the finder did not search"])
        room = people_tasks.MAX_OPEN_PARTICIPATE - waiting
        seen = [f"{u} {a}" for u, a in s.execute(select(OwnerTask.card["thread_url"].astext, OwnerTask.card["asks"].astext)
                                                 .where(OwnerTask.business_id == business_id,
                                                        OwnerTask.kind == "participate").order_by(OwnerTask.id.desc())
                                                 .limit(KNOWN_SHOWN))]
        look = _look(s, business_id)
        prompt = threads_prompt(brief, seen, look)
    if not look.searched:
        return Report(notes=[NOTHING_TO_SEARCH])
    budget.gate_step("people_find")
    out = run_agent(AGENT, PURPOSE, prompt, FindThreadsOut, project_id=None, business_id=business_id, system_override=_system("find_threads"), max_turns=50)
    report = Report(asked=len(out.threads))
    threads = out.threads[:MAX_THREADS]
    if len(out.threads) > len(threads):
        report.dropped["over_limit"] = len(out.threads) - len(threads)
    screened = _each(threads, lambda t: _thread_of(t, fetch, api or _default_api(fetch), look.searched, lang))
    with session_scope() as s:
        for thread, reason in screened:
            if thread is None:
                report.dropped[reason] += 1
                continue
            if people.is_forgotten(s, business_id, thread.platform, thread.asker):
                report.dropped["forgotten"] += 1
                continue
            report.verified += 1
            if report.added >= room:
                report.dropped["cap"] += 1
            elif not owner_tasks.allowed(s, business_id, "participate"):
                report.dropped["no_stage"] += 1
            elif people_tasks.create_participate(s, business_id, thread, lang=lang) is None:
                report.duplicate += 1
            else:
                report.added += 1
                report.tasks += 1
        _record(s, business_id, "threads_find_ran", "threads", report, lang)
    return report


def daily_key(kind: str, business_id: int) -> str:
    """One run per kind per business per day, whoever asks: the owner's "find now" and the weekly schedule share it."""
    return f"{kind}:{business_id}:{date.today().isoformat()}"


def _active(s: Session, business_id: int) -> bool:
    business = s.get(Business, business_id)
    return business is not None and business.status == "ACTIVE"


def request(s: Session, business_id: int, *, lang: Lang) -> dict:
    """The owner asks to look now. Once a day per kind: asking again the same day queues nothing new."""
    people._business(s, business_id, lang=lang)
    if roadmap.approved_brief(s, business_id) is None:
        raise people.PeopleError(msg("people.find.no_plan", lang))
    queued = {kind: queue.enqueue(s, kind, {"business_id": business_id}, idempotency_key=daily_key(kind, business_id),
                                  max_attempts=2, by_owner=True) is not None for kind in KINDS}
    return {"queued": queued}


def _record(s: Session, business_id: int, verb: str, run: str, report: Report, lang: Lang) -> None:
    dropped = ", ".join(f"{k} {v}" for k, v in sorted(report.dropped.items())) or msg("people.find.none_dropped", lang)
    activity.record(s, verb=verb, entity="business", entity_id=business_id, project_id=None, business_id=business_id,
                    actor="research",
                    summary=msg("people.find.summary", lang, id=business_id, label=msg(f"people.find.{run}", lang),
                                asked=report.asked, verified=report.verified, added=report.added,
                                duplicate=report.duplicate, dropped=dropped),
                    data={"asked": report.asked, "verified": report.verified, "added": report.added, "duplicate": report.duplicate,
                          "dropped": dict(report.dropped), "tasks": report.tasks})
