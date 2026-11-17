"""Why a job was staffed the way it was (`justshipit.staffing`): one sentence for each rule, keyed
`staffing.why.<rule id>`, and one for each reason a slot is added later (`staffing.AMENDMENT_REASONS`). A role is
named by its glossary name, passed as `{owner}` or `{checker}`. `staffing.crew.<standing>` says why the crews screen
shows a job with no team (`staffing.Standing`)."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    "staffing.why.check.feeds_build": {
        "en": "The build spec reads this result, so a second role checks it.",
        "ko": "개발 명세가 이 결과를 읽어서, 다른 역할이 한 번 더 확인해요."},
    "staffing.why.check.analysed": {
        "en": "Data analysis worked this out in code, so a second role checks the figures.",
        "ko": "데이터 분석이 코드로 계산한 결과라서, 다른 역할이 숫자를 한 번 더 확인해요."},
    "staffing.why.check.numbers": {
        "en": "It is finance work, and a second role always checks numbers.",
        "ko": "재무 업무라서, 숫자는 늘 다른 역할이 한 번 더 확인해요."},
    "staffing.why.check.reviewer_role": {
        "en": "{owner} did the work, so {checker} checks it. No role checks its own work.",
        "ko": "{owner|이} 한 일이라서 {checker|이} 확인해요. 역할이 자기 일을 확인하지는 않아요."},
    "staffing.why.check.resumed": {
        "en": "It had already been written or sent back for rework, so a second role checks it.",
        "ko": "이미 결과가 나왔거나 다시 하던 업무라서, 다른 역할이 확인해요."},
    "staffing.why.check.decision": {
        "en": "A meeting decision created it, so a second role checks it.",
        "ko": "회의 결정에서 나온 업무라서, 다른 역할이 확인해요."},
    "staffing.why.check.owner_added": {
        "en": "You added it, so a second role checks it.",
        "ko": "오너님이 직접 추가한 업무라서, 다른 역할이 확인해요."},
    "staffing.why.check.has_dependents": {
        "en": "Other tasks wait on it, so a second role checks it.",
        "ko": "이 결과를 기다리는 업무가 있어서, 다른 역할이 확인해요."},
    "staffing.why.check.criteria": {
        "en": "It has {criteria} done criteria, more than the {solo_max_criteria} one role may take alone, so a second role checks it.",
        "ko": "완료 기준이 {criteria}개라서, 혼자 맡는 한도 {solo_max_criteria}개를 넘어요. 다른 역할이 확인해요."},
    "staffing.why.check.config": {
        "en": "Settings ask for every task to be checked.",
        "ko": "설정에서 모든 업무를 확인하게 해 뒀어요."},
    "staffing.why.check.owner_card": {
        "en": "The result asks you something, and nothing reaches you before a second role checks it.",
        "ko": "결과에 오너님께 묻는 게 있어요. 다른 역할이 확인한 뒤에야 오너님께 보내요."},
    "staffing.why.check.calculation": {
        "en": "The result has figures the finance engine computed, so a second role checks them.",
        "ko": "결과에 재무 엔진이 계산한 숫자가 있어서, 다른 역할이 숫자를 한 번 더 확인해요."},
    "staffing.why.solo.small_leaf": {
        "en": "Nothing waits on it and nothing forces a check (done criteria: {criteria}), so one role does it alone.",
        "ko": "기다리는 업무가 없고 확인할 이유도 없어요(완료 기준 {criteria}개). 한 역할이 혼자 해요."},
    "staffing.why.history.solo": {
        "en": "Shadow checks of {samples} earlier tasks of this kind done alone found unmet criteria in {unmet} "
              "({unmet_pct}%), under the {limit_pct}% limit, so one role does it alone.",
        "ko": "혼자 한 같은 종류의 업무 {samples}건을 따로 확인해 보니 기준을 못 채운 건 {unmet}건({unmet_pct}%)으로 "
              "한도 {limit_pct}%보다 적어요. 한 역할이 혼자 해요."},
    "staffing.why.history.checked": {
        "en": "Shadow checks of {samples} earlier tasks of this kind done alone found unmet criteria in {unmet} "
              "({unmet_pct}%), at or above the {limit_pct}% limit, so a second role checks it.",
        "ko": "혼자 한 같은 종류의 업무 {samples}건을 따로 확인해 보니 {unmet}건({unmet_pct}%)이 기준을 못 채워서 "
              "한도 {limit_pct}% 이상이에요. 다른 역할이 확인해요."},
    "staffing.why.history.model": {
        "en": "On this kind of task {model} passed its first check in {met_pct}% of {samples}, within "
              "{tolerance_pct} points of the best ({best_pct}%), so it runs on {model}, which uses less of the "
              "subscription than {configured}.",
        "ko": "같은 종류의 업무 {samples}건에서 {model} 모델은 첫 확인을 {met_pct}% 통과했어요. 가장 좋은 결과"
              "({best_pct}%)와 {tolerance_pct}%p 안이라서, {configured} 모델보다 구독을 덜 쓰는 {model} 모델로 해요."},
    "staffing.why.history.explore": {
        "en": "{model} has {samples} of the {min_samples} results needed to compare it with {configured} on this "
              "kind of task, so this task, picked at random ({explore_pct}% of tasks), runs on {model} to add one.",
        "ko": "같은 종류의 업무에서 {model} 모델 결과가 {samples}건이라, {configured} 모델과 비교할 {min_samples}건에 "
              "못 미쳐요. 무작위로 고른 업무({explore_pct}%)라서 {model} 모델로 한 건 더 해 봐요."},
    "staffing.why.fixed.build": {
        "en": "A build always runs in the same order: engineer, code review, product check.",
        "ko": "개발 작업은 늘 엔지니어, 코드 리뷰, 제품 확인 순서로 해요."},
    "staffing.crew.waiting": {
        "en": "Not started yet, so no team has been placed.",
        "ko": "아직 시작하지 않아서 팀을 꾸리지 않았어요."},
    "staffing.crew.cancelled_before_start": {
        "en": "Cancelled before it started, so no team was placed.",
        "ko": "시작하기 전에 취소돼서 팀을 꾸리지 않았어요."},
    "staffing.crew.no_record": {
        "en": "No record of a team for this job.",
        "ko": "이 일을 맡은 팀 기록이 없어요."},
}
