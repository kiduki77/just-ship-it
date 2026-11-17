"""The experiment loop's clock and its routines (`services/bet_loop.py`): why an experiment ended, the status lines
the sweep writes, the refusals the console shows, and the activity lines."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    "bet.cause.run_done": {"en": "The run finished", "ko": "실행이 끝났어요"},
    "bet.cause.run_failed": {"en": "The run failed", "ko": "실행이 실패했어요"},
    "bet.cause.time_limit": {"en": "It reached its time limit", "ko": "정한 기간이 끝났어요"},
    "bet.cause.budget_limit": {"en": "It used up its budget", "ko": "예산을 다 썼어요"},
    "bet.cause.owner": {"en": "You ended it", "ko": "오너님이 끝냈어요"},

    "bet.reason.review": {"en": "{cause}. Waiting for the review.", "ko": "{cause}. 회고를 기다려요."},
    "bet.reason.listening": {"en": "The run finished. The company collects responses until the window closes, then "
                                   "writes the review.",
                             "ko": "실행이 끝났어요. 기간이 끝날 때까지 반응을 모은 뒤 회고해요."},
    "bet.reason.stuck": {"en": "Asked for the review {n} times and it never finished.",
                         "ko": "회고를 {n}번 맡겼지만 끝나지 않았어요."},
    "bet.reason.stuck_ask": {"en": "{stuck} Press “Retry review” to ask again.", "ko": "{stuck} '회고 다시 맡기기'를 눌러 주세요."},
    "bet.reason.retry": {"en": "The run is being retried, so the experiment goes on.",
                         "ko": "실행을 다시 시도해서 실험을 이어 가요."},
    "bet.reason.card_approved": {"en": "You approved \"{title}\".", "ko": "오너님이 '{title}' 결정을 승인했어요."},
    "bet.reason.card_rejected": {"en": "You declined \"{title}\".", "ko": "오너님이 '{title}' 결정을 반려했어요."},

    "bet.refuse.not_waiting": {"en": "BET-{id} isn't waiting for a review.", "ko": "BET-{id}: 회고를 기다리는 실험이 아니에요."},
    "bet.refuse.retro_running": {"en": "BET-{id}'s review is already being written.", "ko": "BET-{id}: 회고를 이미 쓰고 있어요."},
    "bet.refuse.no_routine": {"en": "BET-{id} has no routine.", "ko": "BET-{id}: 정기 업무가 없어요."},

    "bet.activity.requeued": {"en": "BET-{id}: no review yet, so the company asked again (round {n}).",
                              "ko": "BET-{id}: 회고가 없어서 {n}번째로 다시 맡겼어요."},
    "bet.activity.redo": {"en": "BET-{id}: you asked for the review again.", "ko": "BET-{id}: 오너님이 회고를 다시 맡겼어요."},
    "bet.activity.routine_refused": {
        "en": {"one": "BET-{id}: not started, because the business already has {n} routine.",
               "other": "BET-{id}: not started, because the business already has {n} routines."},
        "ko": "BET-{id}: 정기 업무가 이미 {n}개라 시작하지 않았어요."},
    "bet.activity.routine_started": {
        "en": {"one": "BET-{id}: now runs as a routine every {n} day.",
               "other": "BET-{id}: now runs as a routine every {n} days."},
        "ko": "BET-{id}: {n}일마다 정기 업무로 돌려요."},
    "bet.activity.routine_stopped": {"en": "BET-{id}: you stopped the routine.", "ko": "BET-{id}: 오너님이 정기 업무를 멈췄어요."},

    "bet.routine.paused": {"en": "Routine stopped", "ko": "정기 업무를 멈췄어요"},
    "bet.routine.project": {"en": "[Routine] {title}", "ko": "[정기] {title}"},
    "bet.routine.why": {"en": "BET-{id} showed this work pays off, so it now runs as a routine. The original experiment: "
                              "{pitch}",
                        "ko": "BET-{id}에서 효과를 확인해서 정기 업무가 된 일이에요. 원래 실험: {pitch}"},
}
