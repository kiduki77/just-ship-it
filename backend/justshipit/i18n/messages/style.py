"""The style packs a business's new products are built from (`engineering/style_packs.py`, `api/looks.py`): each
pack's name and one-line pitch, and the refusals of a look the owner sets."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    "style.pack.linear.name": {"en": "Linear style", "ko": "Linear 스타일"},
    "style.pack.linear.pitch": {"en": "A dense, quiet screen for clearing a backlog fast from the keyboard.",
                                "ko": "쌓인 일을 키보드로 빠르게 처리하는 빽빽하고 조용한 화면."},
    "style.pack.toss.name": {"en": "Toss style", "ko": "토스 스타일"},
    "style.pack.toss.pitch": {"en": "One decision per screen, with big numbers and plain words.",
                              "ko": "한 화면에 결정 하나, 큰 숫자와 쉬운 말."},
    "style.pack.stripe.name": {"en": "Stripe style", "ko": "Stripe 스타일"},
    "style.pack.stripe.pitch": {"en": "Reports and dashboards where the numbers matter, in precise tables.",
                                "ko": "숫자가 중요한 리포트와 대시보드, 정확한 표."},
    "style.field.pack": {"en": "style", "ko": "스타일"},
    "style.field.main": {"en": "main color", "ko": "메인 색"},
    "style.not_found": {"en": "There's no business B-{id}.", "ko": "B-{id} 사업이 없어요."},
}
