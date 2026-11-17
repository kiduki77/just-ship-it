"""The work path's server words (docs/design/work-path.md): why a card is asked (`path.reason.<AskReason>`), and why the
server did not file a request (`path.refused.<key>`, services/request_contract.py). The refusals reach the agent that
wrote the request as feedback, and the owner in the activity log."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    "path.reason.GOAL_UNCLEAR": {"en": "The goal can be read more than one way.",
                                 "ko": "목표를 여러 가지로 읽을 수 있어요."},
    "path.reason.INPUT_MISSING": {"en": "The work needs something only you can give.",
                                  "ko": "일을 하려면 오너님만 줄 수 있는 것이 필요해요."},
    "path.reason.STEP_NOT_RUN": {"en": "A step this needs was planned but never run.",
                                 "ko": "이 결정에 필요한 단계를 계획만 하고 아직 하지 않았어요."},
    "path.reason.RESULT_NEEDS_CALL": {"en": "A result is in, and the next step is your call.",
                                      "ko": "결과가 나왔고, 다음 단계는 오너님이 정해요."},
    "path.reason.EVIDENCE_CONFLICTS": {"en": "The evidence points both ways.",
                                       "ko": "근거가 서로 다른 쪽을 가리켜요."},
    "path.reason.NEEDS_PERMISSION": {"en": "An action needs your yes.",
                                     "ko": "오너님이 허락해야 할 수 있는 일이에요."},
    "path.reason.SPEND": {"en": "It spends money.", "ko": "돈이 드는 일이에요."},
    "path.reason.BUILD_NEEDED": {"en": "Software has to be built.", "ko": "소프트웨어를 만들어야 해요."},
    "path.reason.SPEC_UNCLEAR": {"en": "The spec can be read more than one way.",
                                 "ko": "명세를 여러 가지로 읽을 수 있어요."},
    "path.reason.FOLLOWS_DECISION": {"en": "It follows from a decision already made.",
                                     "ko": "이미 내린 결정에서 이어진 일이에요."},

    "path.refused.context_missing": {
        "en": "It doesn't say why it is asked, what it holds up or what happens if nobody answers.",
        "ko": "묻는 이유, 막고 있는 일, 답이 없을 때의 규칙이 없어요."},
    "path.refused.run_not_goal_unclear": {
        "en": "Only a question about an unclear goal may hold up the whole project.",
        "ko": "목표가 분명하지 않아 묻는 질문만 프로젝트 전체를 막을 수 있어요."},
    "path.refused.refs_empty": {
        "en": "A question needs at least one record that shows why it is asked, unless the goal itself is unclear.",
        "ko": "목표가 분명하지 않은 게 아니라면, 질문에는 묻는 이유를 보여 주는 기록이 하나 이상 있어야 해요."},
    "path.refused.ref_missing": {"en": "It points at a record this project doesn't have: {ref}",
                                 "ko": "이 프로젝트에 없는 기록을 가리켜요: {ref}"},
    "path.refused.hold_not_allowed": {"en": "Whoever sent it, or its kind, may not hold up that work.",
                                      "ko": "보낸 쪽이나 요청 종류로는 그 일을 막을 수 없어요."},
    "path.refused.hold_target_is_ancestor": {"en": "It would hold up a task its own task waits on: {task}",
                                             "ko": "자기 업무가 기다리는 앞 업무를 막으려고 해요: {task}"},
    "path.refused.hold_target_terminal": {"en": "It would hold up a task that has already ended: {task}",
                                          "ko": "이미 끝난 업무를 막으려고 해요: {task}"},
    "path.refused.hold_target_unknown": {"en": "It would hold up a task this project doesn't have: {task}",
                                         "ko": "이 프로젝트에 없는 업무를 막으려고 해요: {task}"},
    "path.refused.answer_target_unknown": {
        "en": "What answering does names an option or a task this request doesn't have: {what}",
        "ko": "답하면 일어나는 일이 이 요청에 없는 선택지나 업무를 가리켜요: {what}"},
    "path.refused.expiry_not_allowed": {"en": "This kind of request can't use that rule for when nobody answers.",
                                        "ko": "이 종류의 요청에는 답이 없을 때 그 규칙을 쓸 수 없어요."},
    "path.refused.expiry_cannot_approve": {
        "en": "An approval, a spend or anything sent outside closes if nobody answers. It never goes ahead on its own.",
        "ko": "승인, 돈이 드는 일, 밖으로 나가는 일은 답이 없으면 닫혀요. 저절로 진행되지 않아요."},
    "path.refused.default_not_allowed": {"en": "Only a question may go ahead with a default when nobody answers.",
                                         "ko": "답이 없을 때 기본값으로 진행하는 건 질문만 할 수 있어요."},
    "path.refused.default_not_recommended": {"en": "The default has to be the recommended option.",
                                             "ko": "기본값은 추천안이어야 해요."},
    "path.refused.default_declines": {
        "en": "The default can't be an option that declines or puts the decision off. Close it instead.",
        "ko": "거절하거나 미루는 선택지는 기본값이 될 수 없어요. 대신 닫히게 하세요."},
    "path.refused.development_holds_server_owned": {
        "en": "A build request holds up no work itself. The server decides what waits for the build.",
        "ko": "개발 요청은 스스로 일을 막지 않아요. 무엇이 개발을 기다릴지는 서버가 정해요."},
    "path.refused.owner_work_missing": {
        "en": "It asks the owner to do something by hand without saying why only the owner can, how long it takes and "
              "what is lost if it's skipped. A request for work a program could do is never put to the owner.",
        "ko": "오너가 손으로 할 일을 요청하면서, 왜 오너만 할 수 있는지, 얼마나 걸리는지, 하지 않으면 무엇을 잃는지 "
              "쓰지 않았어요. 프로그램이 할 수 있는 일은 오너에게 요청하지 않아요."},
    "path.not_filed": {"en": "Not put to you: {title}. {why}", "ko": "오너에게 올리지 않은 결정: {title}. {why}"},
}
