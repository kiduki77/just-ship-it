"""The merge card's walkthrough, as the server words it (`engineering/walkthrough.py`, `engineering/worker.py`): one
acceptance criterion per must-have user journey, and the evidence line of each step that did not show what the
spec says the person should see. `{n}` is the step's number in its journey, `{do}` the spec's words for it, and
`{need}` what the sandbox lacked, which can end in a Latin letter (a key's name, a host)."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    "walkthrough.criterion": {"en": "{title}, as {persona} would", "ko": "{persona} 입장에서 따라 하기: {title}"},
    "walkthrough.evidence.passed": {"en": "Every step ran on this build and showed what you should see.",
                                    "ko": "모든 단계를 이 개발 작업으로 돌렸고, 보여야 할 것이 그대로 나왔어요."},
    "walkthrough.evidence.product": {"en": "Step {n} ({do}) didn't show what you should see.",
                                     "ko": "{n}단계({do})에서 보여야 할 것이 나오지 않았어요."},
    "walkthrough.evidence.scenario": {"en": "Step {n} ({do}): the product agent thinks the step itself is wrong.",
                                      "ko": "{n}단계({do})는 제품기획 에이전트가 단계 설명이 틀렸다고 봐요."},
    "walkthrough.evidence.not_judged": {"en": "Step {n} ({do}) ran, but the product agent didn't say whether it "
                                              "matches.",
                                        "ko": "{n}단계({do})는 돌렸지만, 제품기획 에이전트가 맞는지 말하지 않았어요."},
    "walkthrough.evidence.not_run.needs": {"en": "Nobody ran step {n} ({do}): it needs {need}, which the agent's "
                                                 "sandbox never gets.",
                                           "ko": "{n}단계({do})는 아무도 돌려 보지 못했어요. 필요한 것({need})이 "
                                                 "에이전트 샌드박스에는 없어요."},
    "walkthrough.evidence.not_run.unbound": {"en": "Step {n} ({do}) didn't run: the build doesn't say how to do it "
                                                   "yet.",
                                             "ko": "{n}단계({do})를 돌리지 못했어요. 개발 결과에 이 단계를 하는 "
                                                   "방법이 아직 없어요."},
    "walkthrough.evidence.not_run.no_docker": {"en": "Step {n} ({do}) didn't run: the sandbox couldn't start.",
                                               "ko": "{n}단계({do})를 돌리지 못했어요. 샌드박스를 시작하지 못했어요."},
    "walkthrough.evidence.not_run.setup_failed": {"en": "Step {n} ({do}) didn't run: the setup stopped first.",
                                                  "ko": "{n}단계({do})를 돌리지 못했어요. 준비 단계가 먼저 멈췄어요."},
    "walkthrough.evidence.not_run.earlier_failed": {"en": "Step {n} ({do}) didn't run: an earlier step didn't show "
                                                          "what it should.",
                                                    "ko": "{n}단계({do})를 돌리지 못했어요. 앞 단계가 보여야 할 "
                                                          "대로 나오지 않았어요."},
    "walkthrough.evidence.not_run.out_of_time": {"en": "Step {n} ({do}) didn't run: the check ran out of time.",
                                                 "ko": "{n}단계({do})를 돌리지 못했어요. 확인 시간이 다 됐어요."},
    "walkthrough.evidence.not_run.unreachable": {"en": "Step {n} ({do}) didn't run: the build's preview didn't "
                                                       "answer.",
                                                 "ko": "{n}단계({do})를 돌리지 못했어요. 미리보기가 응답하지 "
                                                       "않았어요."},
}
