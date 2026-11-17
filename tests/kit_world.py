"""Builders for the First 10 kit tests: a business with a plan, a customer description, a place and a few people, and a
marketing agent that writes a valid text for every line the prompt asks for unless a test tells it to write another."""
import re

from justshipit.contracts.first_ten_kit import KitDraft, KitOut
from justshipit.db.models import KitText
from justshipit.services import tracking
from people_kit import add, make_business
from sqlalchemy import select

LINK = "https://logtool.example.com/start"
SITE = [{"kind": "site", "url": LINK, "param": "ref"}]
NONE = object()   # a text the model leaves out

GOOD = {
    ("message", "p"): "Hi, I read your note about losing hours in server logs. I am building a command-line tool for "
                      "exactly that. Could we talk for ten minutes about how you handle it today?",
    ("profile", "line"): "A command-line tool that finds why a server crashed.",
    ("profile", "short"): "I build a command-line tool that reads long server logs and points to the likely cause of a crash.",
    ("profile", "about"): "A command-line tool for small backend teams. It reads long server logs and points to the likely "
                          "cause of a crash. I am learning how teams debug today. {link}",
    ("invite", "known"): "I built a tool that finds why a server crashed. Would you try it and tell me what is confusing? {link}",
    ("invite", "problem"): "You wrote about losing hours to server logs. I built a tool for that and would like your honest "
                           "reaction. {link}",
}


def good(part: str, slot: str, link: str | None = LINK) -> str:
    return GOOD[(part, slot)].replace("{link}", link or "").strip()


def business(s, *, place: bool = True, **kw):
    """A business whose plan is approved and whose owner confirmed who buys. `place` gives it a site to link to."""
    b = make_business(s, **kw)
    if place:
        tracking.set_doors(s, b.id, SITE)
        s.commit()
    return b


def person(s, b, handle="alice", *, source="warm", stage="found", quote="I lose hours grepping through 2GB of server logs", **kw):
    p = add(s, b, handle, source=source, platform="direct" if source in ("warm", "inbound") else "github", stage=stage,
            quote=quote, url=f"owner:{handle}" if source in ("warm", "inbound") else None, **kw)
    s.commit()
    return p


def asked_keys(prompt: str) -> list[str]:
    """The lines the prompt asks for: the ones listed again in a rewrite round, else the whole list."""
    section = prompt.split("## Write these again")[1] if "## Write these again" in prompt else prompt.split("## The texts to write")[1]
    return re.findall(r"^### ((?:message p PPL-\d+)|(?:profile \w+)|(?:invite \w+))$", section, re.MULTILINE)


def link_in(prompt: str) -> str | None:
    found = re.search(r"The only link you may write: (\S+)", prompt)
    return found.group(1) if found else None


class Marketing:
    """The marketing agent. `texts` maps a line (`profile line`, `message p PPL-3`) to what it writes: a string, a list with one
    string for each round, or NONE to leave the line out. Any other line gets a valid text. Every prompt is recorded."""

    def __init__(self, texts: dict | None = None):
        self.prompts: list[str] = []
        self.texts = texts or {}

    def __call__(self, agent, purpose, prompt, output_model, *, project_id, business_id=None, system_override=None, **kw):
        assert (agent, purpose, project_id, output_model) == ("marketing", "kit", None, KitOut)
        assert system_override and "First 10 kit" in system_override
        self.prompts.append(prompt)
        out = []
        for key in asked_keys(prompt):
            part, slot, *ref = key.split()
            want = self.texts.get(key, good(part, slot, link_in(prompt)))
            if isinstance(want, list):
                want = want[min(len(self.prompts), len(want)) - 1]
            if want is not NONE:
                out.append(KitDraft(part=part, slot=slot, person=ref[0] if ref else None, body=want))
        return KitOut(texts=out)

    @property
    def calls(self) -> int:
        return len(self.prompts)


def live(s, b, part: str | None = None) -> dict[tuple, KitText]:
    s.expire_all()
    q = select(KitText).where(KitText.business_id == b.id, KitText.replaced_at.is_(None))
    if part:
        q = q.where(KitText.part == part)
    return {(r.part, r.slot, r.person_id): r for r in s.scalars(q)}
