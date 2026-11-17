"""The Activity page's run and job logs and the Routines page name the records their rows are about, by title, the way
the feed does since round 6 (`/api/refs` through `RefLink`). The codes2 report read 'P-11', 'J-9', 'REQ-104', 'A-3' and
'B-21' in those rows."""
import re
from pathlib import Path

PAGES = Path(__file__).resolve().parents[1] / "web" / "src" / "pages"
CODE = r"(?:P|J|T|REQ|OT|DEC|A|B)-"
# A code shown as it is: JSX text such as `>P-{id}` or a template literal such as `· REQ-${id}`.
SHOWN = re.compile(rf">\s*{CODE}\{{|`[^`]*{CODE}\$\{{")
# Where a code is only a key the console turns into a title.
NAMED = re.compile(r"RefLink|useRefs|refTitle")


def shown_codes(page: str) -> list[str]:
    text = (PAGES / page).read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if SHOWN.search(line) and not NAMED.search(line)]


def test_the_activity_logs_name_records_by_title():
    assert shown_codes("Audit.tsx") == []


def test_the_routines_page_names_records_by_title():
    assert shown_codes("Operations.tsx") == []


WEB = PAGES.parent
# A code the owner reads anywhere in the console: JSX text such as `EV-{id}` or `{r.kind}-{r.id}`, or a title,
# label or aria-label made of one, such as `title={name ?? `B-${id}`}` (round 7: B- on the business and kickoff
# pages, REQ- on the decisions history, EV- on the evidence tab and panels, kind-id in search results), in a page or
# in a catalog string.
VISIBLE = re.compile(rf"\b(?:{CODE[3:-2]}|EV)-\{{|\{{[\w.]+\}}-\{{|(?:title|aria-label|label)=\{{[^`}}]*`[^`]*\b(?:{CODE[3:-2]}|EV)-\$\{{")


def test_no_console_screen_shows_a_record_code():
    shown = []
    for path in sorted([*WEB.rglob("*.tsx"), *(WEB / "i18n").rglob("*.ts")]):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if VISIBLE.search(line) and not NAMED.search(line) and not line.strip().startswith(("//", "*", "/*")):
                shown.append(f"{path.relative_to(WEB).as_posix()}:{n}: {line.strip()[:120]}")
    assert shown == []
