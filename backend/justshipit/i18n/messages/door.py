"""Decision rules: who decides which kind of action (`services/policy.py`, `services/door_rules.py`). Why a rule cannot
be stored or did not apply, and the notes a rule taken from the plan carries."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    "door.problem.malformed": {"en": "A rule needs an action kind (action) and who decides: you or the Project "
                                     "Manager.",
                               "ko": "규칙에는 행동 종류(action)와 정하는 쪽(오너님 또는 프로젝트 매니저)이 "
                                     "있어야 해요."},
    "door.problem.unknown_kind": {"en": "'{action}' isn't a known action kind.", "ko": "모르는 행동 종류예요: {action}"},
    "door.problem.not_ceo": {
        "en": "The Project Manager can't decide '{action}', so you do. The Project Manager can decide only {kinds}.",
        "ko": "'{action}' 일은 프로젝트 매니저가 정할 수 없어서 오너님이 정해요. 프로젝트 매니저가 정할 수 있는 일은 {kinds}뿐이에요."},
    "door.ignored.malformed": {"en": "It isn't a well-formed rule", "ko": "형식이 맞지 않아요"},
    "door.ignored.renamed": {"en": "The Project Manager can't decide a request the server renamed",
                             "ko": "서버가 이름을 바꾼 요청은 프로젝트 매니저가 정할 수 없어요"},
    "door.ignored.not_ceo": {"en": "The Project Manager can't decide this kind of work", "ko": "프로젝트 매니저가 정할 수 없는 일이에요"},

    "door.note.default": {
        "en": "The plan doesn't say who decides this, so the Project Manager decides this reversible work and tells you.",
        "ko": "계획에 누가 정할지 적혀 있지 않아서, 되돌릴 수 있는 이 일은 프로젝트 매니저가 정하고 알려 드려요."},
    "door.note.from_plan": {"en": "{reason} (plan: \"{line}\")", "ko": "{reason} (계획: \"{line}\")"},
    "door.note.owner_decides": {"en": "{note} The Project Manager can't decide this, so you do.",
                                "ko": "{note} 프로젝트 매니저가 정할 수 없는 일이라 오너님이 정해요."},
    "door.dropped.unknown": {"en": "'{action}' isn't a known action kind, so it was dropped",
                             "ko": "모르는 행동 종류라서 버렸어요: {action}"},
    "door.dropped.red_line": {"en": "'{action}': a red line leaves nothing to the Project Manager, so it became your rule",
                              "ko": "'{action}': 금지선은 프로젝트 매니저에게 아무것도 맡기지 않아서 오너님이 정하는 규칙으로 바꿨어요"},
    "door.dropped.unknown_line": {"en": "'{action}' cited a line that isn't in the plan ({line}), so it was dropped",
                                  "ko": "'{action}' 규칙은 계획에 없는 줄({line})을 근거로 들어서 버렸어요"},
    "door.dropped.too_many": {"en": "'{action}': one line can leave at most {n} rules to the Project Manager, so it was dropped",
                              "ko": "'{action}': 한 줄로 프로젝트 매니저에게 맡길 수 있는 규칙은 {n}개까지라서 버렸어요"},
}
