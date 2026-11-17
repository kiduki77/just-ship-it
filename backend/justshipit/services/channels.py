"""The places the owner posts by hand, and what the company may and may not write for them.

The registry is `config/channels.yaml`. The company never posts: it prepares a card and the owner posts. `mode` says
how much it may write. A channel that is one unit of a bigger site (a subreddit, a group) says so in `unit`, and the
weekly promotion reads `fits`, `weekly`, `repost`, `requires`, `same_day_exclusive` and `region` to decide
what may be posted and when. `outline` gives facts, expected questions and a checklist, never a sentence to post (Hacker
News bans generated and AI-edited text). `draft` gives a title and a body the owner edits.

Some asks are forbidden in every channel, so no card may carry one: begging for votes, stars or reviews, or paying for
them. The check is a best-effort pass over normalized text in Korean and English, so every card also carries a fixed
checklist line that makes the owner the last check. A negation lets an ask through only when it governs the ask verb
("don't ask for", "부탁하지 않").

A place is `checked` when its rules were read at its own page (`basis: verified`, with `checked_on`) and `unchecked`
otherwise (`state`). The company writes only for a checked place (`writable`).
"""
from __future__ import annotations

import posixpath
import re
import unicodedata
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Any, Literal
from urllib.parse import parse_qsl, unquote, urlparse

import yaml
from pydantic import BaseModel, ConfigDict

from justshipit.i18n import LANGS, Lang
from justshipit.i18n.match import forbidden_asks as asks
from justshipit.i18n.said import Said, said
from justshipit.services.profile import vocab
from justshipit.services.profile.vocab import Line
from justshipit.settings import settings

Mode = Literal["outline", "draft"]
Basis = Literal["verified", "secondary", "none"]
# What a post card's own link to the owner's place is here: tagged (the address carries the channel's tag), plain (the
# address as it is) or none (no link; the card gives a line to say instead).
Link = Literal["tagged", "plain", "none"]
LINKS: tuple[Link, ...] = ("tagged", "plain", "none")
BASES = ("verified", "secondary", "none")
# Whether a place's rules were read at its own page (`state`): only `basis: verified` is checked. An unchecked place says
# why: not_read (a source exists and could not be read), per_unit (the rules belong to each server, list, newsletter or
# subreddit) or no_source (no rule page was found).
State = Literal["checked", "unchecked"]
Unchecked = Literal["not_read", "per_unit", "no_source"]
UNCHECKED_WHY: tuple[Unchecked, ...] = ("not_read", "per_unit", "no_source")
_TAG = re.compile(r"[a-z]{2,4}")
TRACKING_KEYS = ("tag", "link", "link_basis")
# global: an English-language place open to every market. en, ko: a place for a business whose market is that language.
Region = Literal["global", "en", "ko"]
REGIONS: dict[Lang, tuple[Region, ...]] = {"en": ("global", "en"), "ko": ("global", "ko")}


class Repost(BaseModel):
    """When the same place may be posted to again. `min_interval_days` is the company's own floor between two posts
    here; `needs_new_material` means a post must carry something no earlier post here carried. `basis` says whether the
    rule text behind them was read at `source_url` (verified), known from a summary (secondary) or not found (none)."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    min_interval_days: int
    needs_new_material: bool
    source_url: str | None
    basis: Basis


class Channel(BaseModel):
    """A place and its rules. `names`, `rules` and `aliases` hold every language; the owner reads the one they chose."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    key: str
    names: dict[Lang, str]
    mode: Mode
    rules: dict[Lang, str]
    basis: Basis
    source_url: str | None
    domains: tuple[str, ...]
    unit: str | None
    repost: Repost
    requires: tuple[str, ...]
    same_day_exclusive: bool
    region: Region
    self_written: bool
    generic: bool
    # what the place is, in the customer description's codes per line (`services/profile/core.fit`); a line left out
    # means any, and `{}` names no particular place
    fits: dict[Line, tuple[str, ...]]
    weekly: bool
    aliases: dict[Lang, tuple[str, ...]]
    # the shape a post's own address has here (channels.yaml `post_link`), and the example the owner reads
    post_link: tuple[str, ...] = ()
    post_example: str | None = None
    # the owner's own link on a card here (`services/tracking.py`): `tag` only makes a tag readable, `link_basis` says
    # whether `link` was read from the place's rules or is the company's own choice, as `basis` does for the rules
    tag: str = ""
    link: Link = "plain"
    link_basis: Basis = "none"
    # when the rules were read at `source_url` (set exactly when `basis` is verified), and why they were not (set exactly
    # when it is not). `background_url` is a page that was read but is not this place's rule text.
    checked_on: date | None = None
    unchecked_why: Unchecked | None = None
    background_url: str | None = None


def state(channel: Channel) -> State:
    """checked when the place's rules were read at their source, unchecked otherwise. Derived from `basis`, never stored."""
    return "checked" if channel.basis == "verified" else "unchecked"


def writable(channel: Channel) -> bool:
    """Whether the company may write a post card for the place: only where its rules were read."""
    return state(channel) == "checked"


def unchecked_said(channel: Channel) -> Said | None:
    """Why the place's rules were not read, as the owner reads it, or None for a checked place."""
    return said(f"post.rules.{channel.unchecked_why}") if channel.unchecked_why else None


def fits_problem(fits: Mapping[str, Sequence[str]]) -> str | None:
    """Why a `fits` mapping cannot be read, or None: a line the description lacks, a declared line with no code, or a
    code that is not live on its line. A retired code no longer steers anything, so a tag may not name it."""
    for line, codes in fits.items():
        if line not in vocab.LINES:
            return f"names {line!r}, which is not a line of the description"
        if not codes:
            return f"declares {line} with no code, so nothing could fit it: leave the line out to mean any"
        if bad := [c for c in codes if c not in vocab.live_codes(line)]:
            return f"names {', '.join(bad)} on {line}, which are not live codes of that line"
    return None


def state_problem(body: Mapping[str, Any]) -> str | None:
    """Why an entry's reading of its rules cannot stand, or None: a place is checked (verified, with the day it was read)
    or unchecked (with why), never both and never neither, and `source_url` is set exactly when a rule text was read."""
    basis, read = body["basis"], body["basis"] == "verified"
    if basis not in BASES:
        return f"basis {basis!r} is unknown"
    if (body["source_url"] is None) != (basis == "none"):
        return "needs a source_url exactly when its basis is not none"
    if read != isinstance(body.get("checked_on"), date):
        return "needs checked_on, a date, exactly when its basis is verified"
    if read != (body.get("unchecked_why") is None):
        return "needs unchecked_why exactly when its basis is not verified"
    if not read and body["unchecked_why"] not in UNCHECKED_WHY:
        return f"unchecked_why {body['unchecked_why']!r} must be one of {', '.join(UNCHECKED_WHY)}"
    if read and body.get("background_url"):
        return "has a background_url, which only a place whose rules were not read has"
    return None


@lru_cache
def registry() -> dict[str, Channel]:
    with open(settings().config_dir / "channels.yaml", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)["channels"]
    for key, body in raw.items():
        if problem := fits_problem(body["fits"]):
            raise ValueError(f"channels.yaml: {key} fits {problem}")
        if problem := state_problem(body):
            raise ValueError(f"channels.yaml: {key} {problem}")
        if lacking := [k for k in TRACKING_KEYS if k not in body]:
            raise ValueError(f"channels.yaml: {key} lacks {', '.join(lacking)}")
        if not _TAG.fullmatch(str(body["tag"])):
            raise ValueError(f"channels.yaml: {key} tag {body['tag']!r} must be 2 to 4 lowercase letters")
        if body["link"] not in LINKS or body["link_basis"] not in BASES:
            raise ValueError(f"channels.yaml: {key} link {body['link']!r} or link_basis {body['link_basis']!r} is unknown")
    by_tag: dict[str, list[str]] = {}
    for key, body in raw.items():
        by_tag.setdefault(body["tag"], []).append(key)
    if dupes := [f"{tag} ({', '.join(keys)})" for tag, keys in by_tag.items() if len(keys) > 1]:
        raise ValueError(f"channels.yaml: a tag is used by two channels: {'; '.join(dupes)}")
    out ={key: Channel(key=key, names={lang: body["name"][lang] for lang in LANGS}, mode=body["mode"],
                        rules={lang: " ".join(body["rules"][lang].split()) for lang in LANGS},
                        basis=body["basis"], source_url=body["source_url"], domains=tuple(body["domains"]),
                        unit=body["unit"], repost=Repost(**body["repost"]), requires=tuple(body["requires"]),
                        same_day_exclusive=body["same_day_exclusive"], region=body["region"],
                        self_written=body["self_written"], generic=body.get("generic", False),
                        fits={line: tuple(codes) for line, codes in body["fits"].items()},
                        weekly=body.get("weekly", True),
                        aliases={lang: tuple((body.get("aliases") or {}).get(lang) or ()) for lang in LANGS},
                        post_link=tuple((body.get("post_link") or {}).get("patterns") or ()),
                        post_example=(body.get("post_link") or {}).get("example"),
                        tag=body["tag"], link=body["link"], link_basis=body["link_basis"],
                        checked_on=body.get("checked_on"), unchecked_why=body.get("unchecked_why"),
                        background_url=body.get("background_url"))
           for key, body in raw.items()}
    if "other" not in out:
        raise ValueError("channels.yaml needs an 'other' channel")
    if unnamed := [k for k, c in out.items() if c.post_link and not c.post_example]:
        raise ValueError(f"channels.yaml: post_link needs an example: {', '.join(unnamed)}")
    return out


def named(text: str, url: str | None = None) -> Channel | None:
    """The one place a card filed under a generic channel names: by its link's host when one place owns that host,
    else by the longest registry name or alias, in any language, the text holds ("당근알바" before "당근"). None when it
    names none."""
    places = [c for c in registry().values() if not c.generic]
    if url and not _host(url)[1]:
        host = _host(url)[0]
        on = [c for c in places if _on(host, c.domains)]
        if len(on) == 1:
            return on[0]
        places = on or places
    names = sorted(((n, c) for c in places for lang in LANGS for n in (c.names[lang], *c.aliases[lang])),
                   key=lambda x: -len(x[0]))
    return next((c for n, c in names if n in (text or "")), None)


@lru_cache
def _name_patterns() -> tuple[tuple[re.Pattern[str], Channel], ...]:
    """Every name and alias of every place but `other`, generic ones included, longest first, each as a case-blind
    pattern. A Latin word starts at a word boundary and, when short, ends at one."""
    found = []
    for c in registry().values():
        if c.key == "other":
            continue
        for name in {n for lang in LANGS for n in (c.names[lang], *c.aliases[lang]) if n}:
            head = r"(?<![A-Za-z0-9])" if name[0].isascii() and name[0].isalnum() else ""
            tail = r"(?![A-Za-z0-9])" if len(name) <= 4 and name[-1].isascii() and name[-1].isalnum() else ""
            found.append((len(name), re.compile(head + re.escape(name) + tail, re.IGNORECASE), c))
    return tuple((p, c) for _, p, c in sorted(found, key=lambda x: -x[0]))


def place_in(text: str) -> Channel | None:
    """The place a title names, by any registry name or alias in any language, generic places such as Reddit included.
    The longest one wins. None when it names none. A card filed under `other` may not name one."""
    return next((c for p, c in _name_patterns() if p.search(text or "")), None)


def for_market(market: Lang) -> list[Channel]:
    """The channels a business whose market is `market` may post to, the global ones first."""
    regions = REGIONS[market]
    return sorted((c for c in registry().values() if c.region in regions), key=lambda c: c.region != "global")


def keys() -> list[str]:
    return list(registry())


def get(key: str) -> Channel | None:
    return registry().get(key)


_PREFILL_PARAMS = frozenset({"text", "t", "title", "body", "message", "content", "comment", "selftext", "description",
                             "summary"})
_URL_BAD = re.compile(r"[\s\x00-\x1f\\]")
_AUTHORITY_BAD = re.compile(r"[@\\%]")
MAX_URL = 500


def _host(url: str) -> tuple[str, Said | None]:
    """The lower-case ASCII host of an http(s) link, and why the link cannot be used if it cannot. The authority
    may carry no `@`, backslash or percent escape: a browser reads `https://a.com\\@b.com` and `news%2eycombinator.com`
    as another host than a parser that stops at the first delimiter."""
    try:
        parts = urlparse(url)
        host = (parts.hostname or "").rstrip(".")
    except ValueError:
        return "", said("post.url.malformed")
    if parts.scheme not in ("http", "https") or not host:
        return "", said("post.url.scheme")
    if _URL_BAD.search(url) or _AUTHORITY_BAD.search(parts.netloc):
        return "", said("post.url.characters")
    try:
        return host.encode("idna").decode("ascii").lower(), None
    except UnicodeError:
        return "", said("post.url.domain")


def _on(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith(f".{d}") for d in domains)


def host_of(url: str) -> str | None:
    """The lower-case host of an http(s) link, or None when the link has none this code can read."""
    host, problem = _host(url.strip())
    return None if problem else host


# Hosts that serve a site's own pages and nobody else's (`pages.dev` is where the Cloudflare connector deploys): an
# address under one is the owner's own, whoever the subdomain names.
STATIC_HOSTS = ("pages.dev", "vercel.app", "netlify.app", "github.io", "workers.dev")


def _bare(host: str) -> str:
    return host.removeprefix("www.")


def _segments(url: str) -> list[str]:
    """The path of an address as a browser resolves it: percent escapes decoded, `.` and `..` applied."""
    path = posixpath.normpath("/" + unquote(urlparse(url).path))
    return [p for p in path.split("/") if p]


def under_places(url: str, places: Collection[str]) -> bool:
    """Whether an http(s) address is under one of the owner's saved places: the same host (`www.` and the bare domain
    are one, no other subdomain is) and the saved place's path is a prefix of the address's path on a segment boundary.
    A place saved at the site root covers the whole host."""
    host, problem = _host(url.strip())
    if problem:
        return False
    mine = _segments(url.strip())
    for place in places:
        base, bad = _host(place.strip())
        if bad or _bare(base) != _bare(host):
            continue
        saved = _segments(place.strip())
        if mine[:len(saved)] == saved:
            return True
    return False


def on_static_host(url: str) -> bool:
    """Whether an http(s) address is under a static-hosting domain (`STATIC_HOSTS`), a subdomain of one."""
    host, problem = _host(url.strip())
    return problem is None and any(host.endswith(f".{d}") for d in STATIC_HOSTS)


def link_problem(channel: Channel | None, url: str) -> Said | None:
    """Why a post link cannot be kept for this channel, or None. A channel with domains takes only those.
    A channel with none (`other`) takes any http(s) link except one on another channel's domain, so a post cannot be
    filed under the wrong channel's rules. No channel at all takes any http(s) link."""
    host, problem = _host(url.strip())
    if problem:
        return problem
    if channel is None:
        return None
    if channel.domains:
        if _on(host, channel.domains):
            return None
        return said("post.url.other_site", channel=channel.names, domains=", ".join(channel.domains))
    if others := [c for c in registry().values() if c.key != channel.key and _on(host, c.domains)]:
        return said("post.url.wrong_channel", channel=others[0].names)
    return None


def post_link_problem(channel: Channel | None, url: str) -> Said | None:
    """`link_problem`, and for a channel that knows the shape of its posts' address, a link that is not a post's own
    page: a submit page, a list or a profile. Reactions are read only from the post itself."""
    if problem := link_problem(channel, url):
        return problem
    if channel is None or not channel.post_link:
        return None
    parts = urlparse(url.strip())
    address = _host(url.strip())[0] + parts.path + (f"?{parts.query}" if parts.query else "")
    if any(re.fullmatch(p, address, re.IGNORECASE) for p in channel.post_link):
        return None
    return said("post.url.not_post", channel=channel.names, example=channel.post_example)


def where_url_problem(channel: Channel, url: str) -> Said | None:
    """The same check for the address a card says to post at, plus two more. It may not be long, and it may not open
    a submit page with the title or text already filled in, which would carry a ready-made post past the channel's
    mode."""
    if len(url) > MAX_URL:
        return said("post.url.too_long", n=MAX_URL)
    if problem := link_problem(channel, url):
        return problem
    filled = sorted({k.lower() for k, _ in parse_qsl(urlparse(url).query, keep_blank_values=True)} & _PREFILL_PARAMS)
    if filled:
        return said("post.url.prefilled", params=", ".join(f"{p}=" for p in filled))
    return None


@dataclass(frozen=True)
class ForbiddenAsk:
    """`id` names the ask in the catalog (`post.ask.<id>`)."""
    id: str
    patterns: tuple[re.Pattern[str], ...]


def _p(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


_WORDS = r"\s*(?:[^\s,.;!?]+\s+){0,3}?"
_RANK = r"(?:#\s?1\b|number\s+one|no\s?1\b|front\s+page|top\s+(?:\d+|of)\b)"


def _either(words: dict[Lang, tuple[str, ...]]) -> str:
    return "(?:" + "|".join(w for lang in LANGS for w in words[lang]) + ")"


def _ask(id_: str) -> ForbiddenAsk:
    """One ask's patterns in every language, and the rewards ask built from what is asked for and what is offered."""
    if id_ == "rewards":
        thing, reward = _either(asks.THING), _either(asks.REWARD)
        return ForbiddenAsk(id_, _p(rf"{thing}[^\n.!?]{{0,40}}{reward}", rf"{reward}[^\n.!?]{{0,40}}{thing}"))
    return ForbiddenAsk(id_, _p(*(p.format(words=_WORDS, rank=_RANK) for lang in LANGS for p in asks.ASKS[lang][id_])))


FORBIDDEN_ASKS: tuple[ForbiddenAsk, ...] = tuple(_ask(id_) for id_ in ("votes", "stars", "reviews", "rewards"))

_NEG_EN = re.compile(r"\b(?:don'?t|do\s+not|doesn'?t|does\s+not|never|not|avoid(?:ing)?|won'?t|will\s+not|must\s+not|"
                     r"should\s+not|shouldn'?t|cannot|can'?t|without|stop)\b")
_ASK_VERB_EN = re.compile(r"\s+(?:(?!forget|hesitate|fail|miss)\w+\s+)?(?:ask|request|solicit|beg|offer|pay|promis|reward|"
                          r"incentiv|encourag|nudg|trad|buy|bribe)\w*\b(?P<gap>[^.,;:!?]*)$")
_CONTRAST = re.compile(_either(asks.CONTRAST))
_TAILS = tuple(re.compile(asks.NEGATED_TAIL[lang]) for lang in LANGS)
_CLAUSE = re.compile(r"[.!?。\n;]+")
_SPACED = re.compile(r"(?<![A-Za-z])(?:[A-Za-z][\s.\-_*]){3,}[A-Za-z](?![A-Za-z])")
_MIXED_TOKEN = re.compile(r"(?<![a-z0-9])(?=[a-z0-9]*[a-z])(?=[a-z0-9]*\d)[a-z0-9]+(?![a-z0-9])")
_LEET = str.maketrans("013", "oie")
_LOOKALIKES = str.maketrans("аеорсухіѕјοναιυτкмнтв", "aeopcyxisjovaiutkmhtb")


def _clean(text: str) -> str:
    """Width and compatibility forms folded (NFKC), invisible format and control characters dropped, and letters
    spelled out one by one joined, so none of them hides a word from the patterns."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Cf" and (c in "\n\t" or unicodedata.category(c) != "Cc"))
    return _SPACED.sub(lambda m: re.sub(r"[\s.\-_*]", "", m.group()), text)


def _fold(clause: str) -> str:
    """Lower case, look-alike letters mapped to Latin, and the leetspeak digits (0, 1, 3) of a word that mixes letters
    and digits mapped back to letters. Plain numbers stay numbers."""
    return _MIXED_TOKEN.sub(lambda m: m.group().translate(_LEET), clause.lower().translate(_LOOKALIKES))


def _negated(clause: str, start: int, end: int) -> bool:
    """Whether a negation governs the ask verb of the match: "don't ask for upvotes" before it, "부탁하지 않기" after
    it. A bare "don't", "never" or "avoid" elsewhere in the clause does not count ("don't forget to upvote")."""
    if any(tail.match(clause[end:]) for tail in _TAILS):
        return True
    negs = list(_NEG_EN.finditer(clause[:start]))
    if not negs:
        return False
    m = _ASK_VERB_EN.match(clause[negs[-1].end():start])
    return m is not None and not _CONTRAST.search(m.group("gap"))


def forbidden_hits(text: str) -> list[tuple[ForbiddenAsk, str]]:
    """Every forbidden ask in the text with the clause that holds it."""
    hits = []
    for clause in _CLAUSE.split(_clean(text)):
        if not clause.strip():
            continue
        folded = _fold(clause)
        for ask in FORBIDDEN_ASKS:
            if any(not _negated(folded, m.start(), m.end()) for p in ask.patterns for m in p.finditer(folded)):
                hits.append((ask, clause.strip()[:80]))
    return hits


def forbidden_reason(texts: list[str]) -> Said | None:
    """Why a card is refused, or None when no text asks for votes, stars or reviews: each ask found, with the clause
    that holds it."""
    seen: dict[str, str] = {}
    for text in texts:
        for ask, clause in forbidden_hits(text):
            seen.setdefault(ask.id, clause)
    if not seen:
        return None
    found = [said("post.ask.found", ask=said(f"post.ask.{id_}"), clause=clause) for id_, clause in seen.items()]
    return said("post.ask.refused", found=found)
