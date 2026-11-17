"""The prefix a build proposal's title starts with, per language (`engineering/status.py` strips it).

The company graph titles the DEVELOPMENT request it makes with `work.ask.proposal.title` ("Build proposal: <need>").
The Engineering page shows the need alone, so it strips the prefix in whichever language the title was stored. The
prefixes are read from that message, so a reworded title can never leave the page showing it.
"""
from justshipit.i18n import LANGS, Lang, msg

PROPOSAL: dict[Lang, str] = {lang: msg("work.ask.proposal.title", lang, title="") for lang in LANGS}
