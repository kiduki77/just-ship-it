"""Evidence ledger: separates sourced claims from hypotheses and verifies sources server-side.

Rules enforced here (not by prompts):
- Agents can never write VERIFIED_FACT; it is only reached through independent review plus a
  reachable source.
- A claim without a source URL is a hypothesis, regardless of what the agent labelled it.
- Repetition by several agents never promotes a claim.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from html import unescape
from urllib.parse import urljoin

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from justshipit.contracts.work import ClaimCheckOut, EvidenceKind, EvidenceOut
from justshipit.db.models import Evidence
from justshipit.db.session import session_scope
from justshipit.i18n import Lang, msg
from justshipit.services import netguard

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"


def ev_id(i: int) -> str:
    return f"EV-{i}"


def parse_ev(value: str) -> int | None:
    m = re.fullmatch(r"\s*EV-?(\d+)\s*", str(value), flags=re.I)
    return int(m.group(1)) if m else None


_CITED = re.compile(r"(?<![\w-])EV-(\d+)(?!\d)")


def cited(text: str | None) -> list[str]:
    """EV ids a piece of agent prose cites, in first-mention order."""
    seen: dict[str, None] = {}
    for n in _CITED.findall(text or ""):
        seen.setdefault(f"EV-{n}", None)
    return list(seen)


def normalize_kind(e: EvidenceOut) -> EvidenceKind:
    kind = e.kind
    if kind in (EvidenceKind.VERIFIED_FACT, EvidenceKind.CALCULATION):
        kind = EvidenceKind.SOURCED_CLAIM
    if kind == EvidenceKind.SOURCED_CLAIM and not (e.source_url or "").startswith("http"):
        kind = EvidenceKind.HYPOTHESIS
    return kind


def record(s: Session, project_id: int, task_id: int | None, agent: str, items: list[EvidenceOut]) -> list[int]:
    ids = []
    for e in items:
        kind = normalize_kind(e)
        row = Evidence(
            project_id=project_id, task_id=task_id, agent=agent, claim=e.claim, kind=kind.value,
            source_url=e.source_url,
            source_title=e.source_title, quote=e.quote, published_at=e.published_at,
            confidence=max(0.0, min(1.0, e.confidence)),
        )
        s.add(row)
        s.flush()
        ids.append(row.id)
    return ids


def record_calculation(s: Session, project_id: int, task_id: int | None, claims: list[str]) -> list[int]:
    ids = []
    for claim in claims:
        row = Evidence(project_id=project_id, task_id=task_id, agent="finance_engine", claim=claim,
                       kind=EvidenceKind.CALCULATION.value, confidence=1.0,
                       verification_status="deterministic")
        s.add(row)
        s.flush()
        ids.append(row.id)
    return ids


_RAW_OPEN = re.compile(r"<(script|style)\b", re.I)
_RAW_CLOSE = {"script": re.compile(r"</script\b", re.I), "style": re.compile(r"</style\b", re.I)}


def _text(html: str) -> str:
    """The visible text of a page, lower-cased. One pass that only moves forward, so its time grows with the page's
    length whatever the page holds: a script or style element goes with its content (to the end of the page when it
    is never closed), and a `<` with no `>` anywhere after it is text."""
    out, pos = [], 0
    while (start := html.find("<", pos)) >= 0:
        out.append(html[pos:start])
        pos = len(html)
        tag_end = start + 1
        if raw := _RAW_OPEN.match(html, start):
            close = _RAW_CLOSE[raw.group(1).lower()].search(html, raw.end())
            if close is None:
                break
            tag_end = close.end()
        end = html.find(">", tag_end)
        if end < 0:
            out.append("" if raw else html[start:])
            break
        out.append(" ")
        pos = end + 1
    out.append(html[pos:])
    return re.sub(r"\s+", " ", unescape("".join(out))).lower()


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


MAX_HOPS = 5
MAX_BYTES = 2_000_000  # of decoded body; a longer page is checked on its first 2 MB
TOTAL_S = 30.0         # for the whole visit, redirects included
MAX_QUOTE = 4000       # characters of a quote searched for in pieces; an agent's quote has no length limit
_BODY_HEADERS = ("content-encoding", "content-length", "transfer-encoding")


def _get(url: str) -> httpx.Response:
    """GET with redirects followed by hand. Every hop is resolved and must be a global address, and the connection
    goes to the address that was checked, so a redirect or a second lookup cannot reach a private network. The page
    is one an agent chose, so the body is streamed and cut at MAX_BYTES, and the visit ends at TOTAL_S however slowly
    the server sends."""
    deadline = time.monotonic() + TOTAL_S
    current = url
    for _ in range(MAX_HOPS + 1):
        u = httpx.URL(current)
        if u.scheme not in ("http", "https") or not u.host:
            raise ValueError("unsupported URL")
        ip = netguard.public_address(u.host, u.port or (443 if u.scheme == "https" else 80))
        host = (f"[{u.host}]" if ":" in u.host else u.host) + (f":{u.port}" if u.port else "")
        if (left := deadline - time.monotonic()) <= 0:
            raise httpx.ReadTimeout("over the total time limit")
        with httpx.Client(timeout=min(15.0, left), follow_redirects=False, trust_env=False,
                          headers={"User-Agent": _UA, "Accept-Encoding": netguard.ACCEPT_ENCODING},
                          transport=netguard.transport(u.scheme, u.host)) as c, \
                c.stream("GET", u.copy_with(host=ip), headers={"Host": host},
                         extensions={"sni_hostname": u.host}) as r:
            if r.is_redirect and r.headers.get("location"):
                current = urljoin(current, r.headers["location"])
                continue
            body = netguard.read_capped(r, MAX_BYTES, deadline) if r.status_code < 400 else b""
            headers = [(k, v) for k, v in r.headers.multi_items() if k.lower() not in _BODY_HEADERS]
            return httpx.Response(r.status_code, headers=headers, content=body)
    raise ValueError("too many redirects")


def _visit(url: str) -> tuple[str, str, httpx.Response | None]:
    """Open a link: the status (`url_ok`, `url_unreachable`, `unverifiable` when the site blocks an automated visit), a
    note, and the response when the page opened. A link that resolves to a private, loopback, link-local or shared
    address, at any redirect hop, is unreachable."""
    try:
        r = _get(url)
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        return "url_unreachable", f"{type(exc).__name__}", None
    if r.status_code in (401, 403, 429):
        return "unverifiable", f"HTTP {r.status_code} (access blocked for automated check)", None
    if r.status_code >= 400:
        return "url_unreachable", f"HTTP {r.status_code}", None
    return "url_ok", f"HTTP {r.status_code}", r


def fetch_page(url: str) -> tuple[str, str, str | None]:
    """Open a link as a visitor would: the status, a note, and the lower-cased visible text when the page is HTML."""
    status, note, r = _visit(url)
    return status, note, _text(r.text) if r is not None and "html" in r.headers.get("content-type", "") else None


def fetch_json(url: str) -> tuple[str, str, str | None]:
    """The same visit for a JSON API: the status, a note, and the body as it came when the response is JSON."""
    status, note, r = _visit(url)
    return status, note, r.text if r is not None and "json" in r.headers.get("content-type", "") else None


def _check(url: str, quote: str | None) -> tuple[str, str]:
    status, note, page = fetch_page(url)
    if status != "url_ok" or not quote or page is None:
        return status, note
    q = _norm(quote)
    # Accept a match on a sizeable contiguous fragment of the quote.
    fragments = [q] + [q[i:i + 40] for i in range(0, min(max(1, len(q) - 40), MAX_QUOTE), 20)]
    if any(f and f in page for f in fragments if len(f) >= min(25, len(q))):
        return "quote_found", "quote located in source page"
    return "url_ok", "page reachable, quote not located"


def verify_sources(evidence_ids: list[int], workers: int = 6) -> None:
    with session_scope() as s:
        rows = [(e.id, e.source_url, e.quote) for e in s.scalars(
            select(Evidence).where(Evidence.id.in_(evidence_ids), Evidence.source_url.is_not(None),
                                   Evidence.verification_status == "unchecked"))]
    if not rows:
        return
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda r: (r[0], *_check(r[1], r[2])), rows))
    with session_scope() as s:
        for eid, status, note in results:
            e = s.get(Evidence, eid)
            e.verification_status = status
            e.verification_note = note


def apply_review(s: Session, evidence_ids: list[int], checks: list[ClaimCheckOut], reviewer: str) -> None:
    """Fold an independent reviewer's claim checks into the ledger; promote only when the source
    was reachable AND the reviewer (a separate reviewer role) found it supported."""
    for c in checks:
        if not 0 <= c.evidence_index < len(evidence_ids):
            continue
        e = s.get(Evidence, evidence_ids[c.evidence_index])
        # transcribed: computed from numbers the author copied from a page; the reviewer is told to check those
        # inputs against the source before supporting — the same bar as any other sourced claim.
        source_ok = e.verification_status in ("url_ok", "quote_found", "reproduced", "transcribed")
        note = f"[{reviewer}] {c.status}: {c.note}"
        if c.counter_source_url:
            note += f" (counter: {c.counter_source_url})"
        e.verification_note = f"{e.verification_note or ''}\n{note}".strip()
        if c.status == "supported" and source_ok and e.kind == EvidenceKind.SOURCED_CLAIM.value:
            e.kind = EvidenceKind.VERIFIED_FACT.value
            e.verification_status = "supported"
        elif c.status in ("contradicted", "weak"):
            e.verification_status = c.status
            if e.kind == EvidenceKind.SOURCED_CLAIM.value and c.status == "contradicted":
                e.kind = EvidenceKind.HYPOTHESIS.value


def digest(s: Session, project_id: int, *, limit: int = 120, task_ids: list[int] | None = None) -> str:
    q = select(Evidence).where(Evidence.project_id == project_id, Evidence.superseded_by.is_(None))
    if task_ids is not None:
        q = q.where(Evidence.task_id.in_(task_ids))
    rows = list(s.scalars(q.order_by(Evidence.id.desc()).limit(limit)))
    lines = []
    for e in reversed(rows):
        src = f" — {e.source_title or ''} <{e.source_url}>" if e.source_url else ""
        when = f" ({e.published_at})" if e.published_at else ""
        lines.append(f"- {ev_id(e.id)} [{e.kind}|{e.verification_status}|{e.agent}] {e.claim}{when}{src}")
    return "\n".join(lines) or msg("evidence.none", "en")


class Grade(StrEnum):
    """How far a claim was checked, as the owner reads it. Only the server or an independent review makes a claim
    firm; what an agent inferred or assumed stays assumed however often it is repeated."""
    FIRM = "firm"          # independently verified, computed by the finance engine, or reproduced from a file
    SOURCED = "sourced"    # cites a source no reviewer confirmed
    ASSUMED = "assumed"    # a hypothesis, an assumption, or a claim a reviewer contradicted


def grade_label(g: Grade, lang: Lang) -> str:
    return msg(f"evidence.grade.{g.value}", lang)


def grade(e: Evidence) -> Grade:
    if e.verification_status == "contradicted":
        return Grade.ASSUMED
    if e.kind in (EvidenceKind.VERIFIED_FACT.value, EvidenceKind.CALCULATION.value):
        return Grade.FIRM
    if e.kind == EvidenceKind.SOURCED_CLAIM.value:
        return Grade.FIRM if e.verification_status in ("reproduced", "computed") else Grade.SOURCED
    return Grade.ASSUMED


@dataclass(frozen=True)
class Mix:
    """A ledger counted by grade."""
    counts: dict[Grade, int]

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def share(self, g: Grade) -> int:
        return round(100 * self.counts[g] / self.total) if self.total else 0

    def line(self, lang: Lang) -> str:
        if not self.total:
            return msg("evidence.mix.empty", lang)
        parts = ", ".join(msg("evidence.mix.part", lang, grade=grade_label(g, lang), count=self.counts[g],
                              share=self.share(g)) for g in Grade)
        return msg("evidence.mix.line", lang, n=self.total, parts=parts)


def mix(s: Session, project_id: int, task_id: int | None = None) -> Mix:
    q = select(Evidence).where(Evidence.project_id == project_id, Evidence.superseded_by.is_(None))
    if task_id is not None:
        q = q.where(Evidence.task_id == task_id)
    counts = dict.fromkeys(Grade, 0)
    for e in s.scalars(q):
        counts[grade(e)] += 1
    return Mix(counts)


def support(s: Session, project_id: int, refs: list[str]) -> Grade:
    """What a conclusion rests on: the best grade among the project's current evidence it cites (one checked fact is
    enough to say it is not only assumed). Citing nothing, or only ids the ledger does not hold, is resting on
    assumption."""
    ids = [i for i in map(parse_ev, refs) if i is not None]
    rows = s.scalars(select(Evidence).where(Evidence.project_id == project_id, Evidence.id.in_(ids),
                                            Evidence.superseded_by.is_(None))) if ids else []
    grades = {grade(e) for e in rows}
    return next((g for g in Grade if g in grades), Grade.ASSUMED)
