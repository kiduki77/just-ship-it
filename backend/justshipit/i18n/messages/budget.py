"""Budget stops (`services/budget.py`): what a budget refused, and the owner's way on."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    "budget.stopped": {"en": "Stopped: not enough budget.", "ko": "예산이 모자라 멈췄어요."},
    "budget.room_now": {"en": "It stopped for lack of budget, but there's room now, so you can try it again.",
                        "ko": "예산이 모자라 멈췄었어요. 지금은 예산이 남아서 다시 시도할 수 있어요."},
    "budget.month": {"en": "{lead} This month's company budget is used up. Raise the monthly budget or wait for next month.",
                     "ko": "{lead} 이번 달 월 한도를 다 썼어요. 월 한도를 올리거나 다음 달까지 기다려 주세요."},
    "budget.project": {"en": "{lead} {used} of the project's {budget} budget is used. Raise the budget to try again.",
                       "ko": "{lead} 프로젝트 예산 {budget} 중 {used} 썼어요. 예산을 올리면 다시 시도할 수 있어요."},
}
