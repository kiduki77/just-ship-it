"""The server sentences say the glossary's word too. The Engineering board and the alerts read "워커 worker-pc:21024:eca3b8에서
600초 넘게 응답이 없어" beside a console that says 백그라운드 프로세스, so the owner met two names for one program
(glossary.py `background_process`). Every server area now uses the glossary word."""
import re

import pytest
from justshipit.i18n import catalog

DRIFT = {"en": re.compile(r"\b[Ww]orkers?\b"), "ko": re.compile(r"워커")}
# A value's name (`{worker}`), a file path (`var/logs/worker.log`) and a quoted command are not words the owner
# reads as a name.
NOT_PROSE = re.compile(r"\{[^{}]*\}|\S*/\S*|`[^`]*`")


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_the_server_says_background_process_where_it_said_worker(lang):
    hits = []
    for key, entry in catalog().items():
        message = entry[lang]
        for text in message.values() if isinstance(message, dict) else [message]:
            if DRIFT[lang].search(NOT_PROSE.sub("", text)):
                hits.append(key)
    assert hits == []
