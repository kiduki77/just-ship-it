"""Defects the live bilingual check of the console found, each pinned where code can be tested without a browser."""
import re
import types
import typing

from justshipit.services import app_settings, owner_quote
from i18n_kit import WEB
from pydantic import BaseModel


def _setting_field_table() -> set[str]:
    """The paths `SETTING_FIELD` in web/src/components/activity.tsx names."""
    text = (WEB / "components" / "activity.tsx").read_text(encoding="utf-8")
    block = text.split("const SETTING_FIELD", 1)[1].split("};", 1)[0]
    return set(re.findall(r'"([a-z_]+/[a-z_/]+)":', block))


def _model(annotation) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    if isinstance(annotation, types.UnionType) or typing.get_origin(annotation) is typing.Union:
        return next((m for a in typing.get_args(annotation) if (m := _model(a))), None)
    return None


def _leaves(model: type[BaseModel], path: str) -> list[str]:
    """Every path a settings change can store (`app_settings.save`), a field holding a table counted once."""
    out = []
    for name, field in model.model_fields.items():
        sub = _model(field.annotation)
        out += _leaves(sub, f"{path}/{name}") if sub else [f"{path}/{name}"]
    return out


def test_every_settings_field_has_a_name_in_the_activity_feed():
    """A settings change reads "Changed Usage: Daily cap", never "general/language"."""
    table = _setting_field_table()
    leaves = [leaf for section, model in app_settings.SECTIONS.items() for leaf in _leaves(model, section)]
    assert "general/language" in leaves and "connectors/slack/mode" in leaves
    unnamed = [leaf for leaf in leaves
               if not any("/".join(leaf.split("/")[:n]) in table for n in range(2, leaf.count("/") + 2))]
    assert unnamed == [], f"add these settings fields to SETTING_FIELD in web/src/components/activity.tsx: {unnamed}"
    stale = sorted(k for k in table if not any(leaf == k or leaf.startswith(k + "/") for leaf in leaves))
    assert stale == [], f"SETTING_FIELD names fields that no longer exist: {stale}"


def test_every_settings_section_has_a_name_in_the_activity_feed():
    """Saving About you reads "Changed About you: What you're good at", never "Changed owner: owner/strengths"."""
    feed = (WEB / "components" / "activity.tsx").read_text(encoding="utf-8")
    renamed = dict(re.findall(r'(\w+): "(\w+)"', feed.split("const SECTION_KEY", 1)[1].split("};", 1)[0]))
    names = (WEB / "i18n" / "areas" / "settings.ts").read_text(encoding="utf-8")
    unnamed = [s for s in app_settings.SECTIONS if f'"settings.section.{renamed.get(s, s)}"' not in names]
    assert unnamed == [], f"name these sections in SECTION_KEY or settings.section.*: {unnamed}"


# ------------------------------------------------------------------ a quote backs only what it says

PRICE_QUOTE = "앱은 1회 2만 원이 넘어서 매일 쓰기엔 부담된다고 했어요"
PRICE_GLOSS = "The app costs over 20,000 won a time, so she said using it every day is a burden"


def _problem(line: str, quote: str = PRICE_QUOTE, gloss: str = PRICE_GLOSS):
    q = owner_quote.norm(quote)
    return owner_quote.problem(q, line, gate=False, said=q, gloss=gloss)


def test_a_quote_of_the_price_does_not_back_a_reason_to_switch():
    """The live kickoff marked "You said it" on a line whose second sentence was the CEO's own reading."""
    line = ("Walking apps cost over 20,000 won a walk, which she said is too much to pay every day. A cheaper neighbor "
            "walker for regular weekday walks is the reason to switch.")
    assert _problem(line).key == "evidence.quote.adds_reason"
    assert _problem("Walking apps cost over 20,000 won a walk, which she said is too much to pay every day.") is None


def test_a_reason_the_owner_gave_still_backs_a_line_that_states_it():
    quote = "산책 앱이 비싸서 안 써요. 가격 때문에 이웃한테 맡기고 싶어요"
    gloss = "She doesn't use walking apps because they're expensive. Because of the price she'd rather ask a neighbor"
    assert _problem("Because walking apps are expensive, she would rather ask a neighbor", quote, gloss) is None
    assert _problem("가격 때문에 이웃에게 산책을 맡기고 싶어 해요", quote, "") is None
    assert _problem("앱이 1회 2만 원이 넘어서, 싼 이웃이 바꿀 이유가 돼요", PRICE_QUOTE, "").key == "evidence.quote.adds_reason"


def test_a_reason_is_backed_by_what_it_says_not_by_its_joining_word():
    """The owner gives a reason with "so" or "-라서" and the line with "because" or "때문에"."""
    assert _problem("You don't want to run things by hand because it doesn't scale",
                    "I don't want to do things by hand, that's not scalable", "") is None
    assert _problem("직장인이기 때문에 근무 시간에는 통화가 어려워요", "직장인이라서 근무 시간엔 통화가 안 돼요", "") is None
    assert _problem("You don't want to run things by hand because it costs too much",
                    "I don't want to do things by hand, that's not scalable", "").key == "evidence.quote.adds_reason"
    assert _problem("마감이 급하기 때문에 근무 시간에는 통화가 어려워요", "직장인이라서 근무 시간엔 통화가 안 돼요",
                    "").key == "evidence.quote.adds_reason"
