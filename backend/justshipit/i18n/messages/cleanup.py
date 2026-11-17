"""Closing duplicate cards and archiving a business (`services/matters.py`, `services/projects.py`, the CLI's
`requests close-duplicates` and `business archive`). A closed card keeps its reason as the response's note."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    "cleanup.why.newest_spec": {"en": "This product's spec is decided on one card, the newest spec {key}.",
                                "ko": "같은 제품의 명세는 최신 명세 {key} 카드 하나에서 결정해요."},
    "cleanup.why.on_spec": {"en": "Decided together on the card for spec {key}.",
                            "ko": "명세 {key} 카드에서 함께 결정해요."},
    "cleanup.why.built": {"en": "The approved spec {key} already covers this development.",
                          "ko": "승인한 명세 {key}에서 이미 다루는 개발이에요."},
    "cleanup.why.newest_ask": {"en": "This product's development asks are gathered on the newest card.",
                               "ko": "같은 제품의 개발 제안은 최신 카드 하나에 모았어요."},
    "cleanup.why.replaced": {"en": "{new} replaced this card.", "ko": "이 카드는 {new} 카드로 바뀌었어요."},
    "cleanup.refuse.other_business": {"en": "{old}, {new}: both cards must belong to business {business}.",
                                      "ko": "{old}, {new}: 둘 다 {business} 사업의 카드여야 해요."},
    "cleanup.refuse.not_later": {"en": "{new}: it must be newer than {old} and still open.",
                                 "ko": "{new}: {old}보다 나중에 올라왔고 아직 열려 있는 카드여야 해요."},
    "cleanup.refuse.not_closable": {
        "en": "{old}: only a waiting, on-hold or expired card that no server flow owns can be closed.",
        "ko": "{old}: 대기·보류·만료 상태이고 서버 흐름에 묶이지 않은 카드만 닫을 수 있어요."},
    "cleanup.note.business_archived": {"en": "Business archived", "ko": "사업 보관"},
    "cleanup.note.gated_closed": {"en": "You archived the business, so this work, which was waiting for a stage, won't run.",
                                  "ko": "사업을 보관해서, 단계를 기다리던 이 작업은 실행하지 않아요."},
    "cleanup.note.queued_closed": {"en": "You archived the business, so this work, which hadn't started, won't run.",
                                   "ko": "사업을 보관해서, 아직 시작하지 않은 이 작업은 실행하지 않아요."},
    "cleanup.refuse.no_business": {"en": "There is no business {business}.", "ko": "{business} 사업이 없어요."},
    "cleanup.refuse.busy": {
        "en": "{business}: job #{job} ({kind}) is queued or running, so it can't be archived yet. Archive it once the "
              "job ends.",
        "ko": "{business}: 대기 중이거나 실행 중인 작업(#{job} {kind})이 있어서 아직 보관할 수 없어요. 작업이 끝나면 "
              "보관해 주세요."},
    "cleanup.refuse.not_archived": {"en": "{business} is not archived.", "ko": "{business}: 보관한 사업이 아니에요."},
}
