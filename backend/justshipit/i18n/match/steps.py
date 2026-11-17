"""The words that open a run of steps written inside one paragraph ("Steps: 1) … 2) …", "순서: 1) … 2) …"), read by
`services/todo_steps.numbered`. Without one, "1) … 2) …" inside a sentence is a list of options, not steps."""
from justshipit.i18n import Lang

LEAD: dict[Lang, tuple[str, ...]] = {
    "en": (r"(?<![A-Za-z])(?:steps?|how to do it)\s*:",),
    "ko": (r"(?:순서|단계|방법|하는 법)\s*:",),
}
