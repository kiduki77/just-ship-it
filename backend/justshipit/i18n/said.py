"""A catalog message held as facts and put into words later, in the language the reader has then.

Text the server keeps for later (a queued notice) or hands up to a boundary (a refusal raised deep in a service) is a
`Said`: a key and its values. A value may itself be a `Said`, as a usage reason inside a notice title, or a list of
them, worded one by one and joined with "; ", or a name kept in every language (a channel's, from channels.yaml).
`say` words it with `msg`; a plain string is text someone wrote and stays as written.
"""
from __future__ import annotations

from functools import partial

from pydantic import BaseModel, ConfigDict

from justshipit.i18n import DEFAULT, Lang, msg


class Said(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    vars: dict[str, str | Said | list[Said] | dict[Lang, str]] = {}


Text = str | Said


def said(key: str, **values: object) -> Said:
    """A `Said` for `key`, each value kept as text unless it is a message itself."""
    return Said(key=key, vars={k: v if isinstance(v, Said | list | dict) else str(v) for k, v in values.items()})


def say(text: Text | list[Said] | dict[Lang, str], lang: Lang) -> str:
    if isinstance(text, str):
        return text
    if isinstance(text, dict):
        return text[lang]
    if isinstance(text, list):
        return "; ".join(say(part, lang) for part in text)
    return msg(text.key, lang, **{name: say(value, lang) for name, value in text.vars.items()})


class Refusal(ValueError):
    """A refusal the owner reads. Raised with a `Said`, it is facts the boundary that catches it words in the owner's
    language (`words(lang)`); raised with a plain string, it is a sentence someone wrote, shown as written. `str()` is
    the default language, for logs and the CLI."""

    def __init__(self, what: Text) -> None:
        self.what = what
        super().__init__(self.words(DEFAULT))

    def words(self, lang: Lang) -> str:
        return say(self.what, lang)


class SaidError(RuntimeError):
    """An error the owner reads, raised far from where the owner's language is known: a sandbox or runtime check deep
    under a job raises it with a catalog key and its values. `str()` is the English text, for logs and tracebacks.
    Where the job records the error for the owner, `words(exc, lang)` puts it in the owner's language."""

    def __init__(self, key: str, /, **vars: object) -> None:
        self.said = said(key, **vars)
        super().__init__(self.words(DEFAULT))

    def __reduce__(self):
        # The default rebuilds from args, the English text, which is no catalog key.
        return partial(type(self), self.said.key, **self.said.vars), ()

    def words(self, lang: Lang) -> str:
        return say(self.said, lang)


def words(exc: BaseException, lang: Lang) -> str:
    """The owner's words for `exc`: the first `SaidError` or `Refusal` on its chain of causes (an AgentFailed around a
    runtime error), else what `exc` says."""
    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, SaidError | Refusal):
            return seen.words(lang)
        seen = seen.__cause__ or seen.__context__
    return str(exc)
