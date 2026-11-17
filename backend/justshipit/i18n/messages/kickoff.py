"""The kickoff chat (`services/kickoff.py`, `api/kickoff.py`): refusals the composer shows, the notes on a paused chat,
the lines and sentences the server writes into the plan and the closing turn, and the activity summaries."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    "kickoff.why_not.open": {"en": "The Project Manager is still writing the next question. Try again in a moment.",
                             "ko": "프로젝트 매니저가 다음 질문을 준비하고 있어요. 잠시 뒤에 다시 해 주세요."},
    "kickoff.why_not.waiting_owner": {"en": "The Project Manager is waiting for your answer.",
                                      "ko": "프로젝트 매니저가 오너님의 답을 기다리고 있어요."},
    "kickoff.why_not.paused": {"en": "This chat is paused. Press “Continue” to pick it up.",
                               "ko": "멈춘 대화예요. '이어 하기'를 눌러 주세요."},
    "kickoff.why_not.done": {"en": "This chat has already ended.", "ko": "이미 끝난 대화예요."},
    "kickoff.why_not.abandoned": {"en": "You ended this chat. Press “Reopen” to pick it back up.",
                                  "ko": "끝낸 대화예요. 다시 하려면 '다시 열기'를 눌러 주세요."},

    "kickoff.not_found": {"en": "Couldn't find kickoff chat #{id}.", "ko": "시작 대화 #{id|를} 찾지 못했어요."},
    "kickoff.none_for_business": {"en": "B-{id} has no kickoff chat.", "ko": "B-{id}에는 시작 대화가 없어요."},
    "kickoff.no_draft": {"en": "There's no draft of the plan left to edit.", "ko": "고칠 수 있는 계획 초안이 없어요."},
    "kickoff.already_live": {"en": "This business already has a kickoff chat in progress (#{live}).",
                             "ko": "이 사업에는 이미 진행 중인 시작 대화가 있어요(#{live})."},

    "kickoff.need_idea": {"en": "Write your idea in one line.", "ko": "아이디어를 한 줄로 적어 주세요."},
    "kickoff.material_too_long": {"en": "The pasted notes are too long. Keep them to {max} characters.",
                                  "ko": "붙여 넣은 자료가 너무 길어요. {max}자까지 넣을 수 있어요."},
    "kickoff.answer.empty": {
        "en": "Type an answer or pick one. If you're not sure, press \"I don't know\". To come back to it later, "
              "press \"Later\".",
        "ko": "답을 적거나 골라 주세요. 모르면 '모르겠어요', 나중에 하려면 '나중에 할게요'를 눌러 주세요."},
    "kickoff.profile.question": {
        "en": "One last question. Who is your first customer, and how will you meet them?",
        "ko": "마지막으로 하나만 물어볼게요. 첫 고객은 누구이고, 어떻게 만날 생각이세요?"},
    "kickoff.customer.not_asked": {"en": "The Project Manager isn't asking about your customers right now.",
                                   "ko": "지금은 고객을 묻는 때가 아니에요."},
    "kickoff.customer.answered": {"en": "You already answered this question.", "ko": "이미 답한 질문이에요."},
    "kickoff.strengths.question": {
        "en": "What are you good at? Where you're strong, I'll ask deeper questions and leave that work to you. "
              "Where you're new, the company takes on more and I'll explain things plainly. Pick all that fit; you "
              "can change this later in Settings.",
        "ko": "어떤 일을 잘하세요? 잘하는 분야는 더 깊이 묻고 오너님께 맡길게요. 처음인 분야는 회사가 더 맡고 "
              "쉽게 설명할게요. 맞는 걸 모두 골라 주세요. 나중에 설정에서 바꿀 수 있어요."},
    "kickoff.strengths.empty": {
        "en": "Pick what you're good at, or \"None of these yet\". To skip it, press \"Skip for now\".",
        "ko": "잘하는 분야를 고르거나 '아직 없어요'를 골라 주세요. 건너뛰려면 '지금은 건너뛰기'를 눌러 주세요."},
    "kickoff.answer.bad_action": {"en": "The chat doesn't take that reply ({action}).",
                                  "ko": "받을 수 없는 응답이에요({action})."},
    "kickoff.resume.not_paused": {"en": "Only a paused chat can be resumed.", "ko": "멈춘 대화만 이어 할 수 있어요."},
    "kickoff.reopen.not_abandoned": {"en": "Only a chat you ended can be reopened.",
                                     "ko": "끝낸 대화만 다시 열 수 있어요."},

    "kickoff.continue.not_done": {"en": "Only a finished chat can be continued. {why}",
                                  "ko": "끝난 대화만 이어서 정할 수 있어요. {why}"},
    "kickoff.continue.not_latest": {"en": "Only the business's latest chat can be continued.",
                                    "ko": "이 사업의 가장 최근 대화만 이어서 정할 수 있어요."},
    "kickoff.continue.approved": {"en": "The plan is already approved, so there's nothing left to settle here.",
                                  "ko": "이미 승인한 계획이라 이어서 정할 칸이 없어요."},
    "kickoff.continue.settled": {"en": "Everything the approval needs is settled, so there's nothing to continue.",
                                 "ko": "승인에 필요한 칸을 다 정해서 더 정할 게 없어요."},
    "kickoff.continue.cap": {"en": "The chat has reached its {cap}-question limit. Edit the plan's lines yourself instead.",
                             "ko": "질문이 최대 {cap}번까지 와서 더 이어 갈 수 없어요. 계획의 줄을 직접 고쳐 주세요."},

    "kickoff.note.reopened": {"en": "You reopened this chat. Press “Continue” to carry on.",
                              "ko": "다시 연 대화예요. '이어 하기'를 누르면 이어서 해요."},
    "kickoff.note.stalled": {"en": "The Project Manager couldn't write the next question. Press “Continue” to try again.",
                             "ko": "프로젝트 매니저가 다음 질문을 만들지 못했어요. '이어 하기'를 누르면 다시 해 볼게요."},
    "kickoff.note.cancelled": {"en": "You cancelled the Project Manager's next question before it was written. Press “Continue” to ask again.",
                               "ko": "프로젝트 매니저가 다음 질문을 쓰기 전에 취소했어요. '이어 하기'를 누르면 다시 물어봐요."},
    "kickoff.note.usage_wait": {"en": "The {reason} was reached, so the Project Manager writes the next question at {when}.",
                                "ko": "{reason}에 닿아서 {when}에 프로젝트 매니저가 다음 질문을 써요."},

    "kickoff.unknown.line": {"en": "Not known yet. The first research will look into it.",
                             "ko": "아직 몰라요. 첫 조사에서 알아볼게요."},
    "kickoff.unknown.assumption": {"en": "{title}: not known yet, so it needs research",
                                   "ko": "{title}: 아직 몰라요. 알아봐야 해요"},
    "kickoff.unknown.no_evidence": {"en": "None", "ko": "없음"},

    "kickoff.closing.question": {"en": "Which part is most likely wrong?", "ko": "제일 틀렸을 것 같은 곳이 어디예요?"},
    "kickoff.closing.answer": {"en": "[Final check] {text}", "ko": "[마지막 확인] {text}"},
    "kickoff.closing.empty_optional": {
        "en": "Optional sections left empty: {sections}. You can start without them and fill them in any time.",
        "ko": "비워 둔 선택 칸: {sections}. 안 채워도 시작할 수 있고, 나중에 채워도 돼요."},

    "kickoff.activity.started": {"en": "B-{id} {name}: kickoff chat started", "ko": "B-{id} {name}: 시작 대화를 열었어요"},
    "kickoff.activity.done": {"en": {"one": "B-{id}: kickoff chat ended after {n} question",
                                     "other": "B-{id}: kickoff chat ended after {n} questions"},
                              "ko": "B-{id}: 시작 대화를 마쳤어요(질문 {n}번)"},
    "kickoff.activity.continued": {"en": "B-{id}: kickoff chat picked back up after question {n}",
                                   "ko": "B-{id}: 질문 {n}번 뒤부터 시작 대화를 이어 가요"},

    "kickoff.field.name": {"en": "business name", "ko": "사업 이름"},
    "kickoff.field.one_liner": {"en": "one-line idea", "ko": "아이디어 한 줄"},
    "kickoff.field.material": {"en": "pasted notes", "ko": "붙여 넣은 자료"},
    "kickoff.field.source": {"en": "product folder", "ko": "제품 폴더"},
    "kickoff.field.documents": {"en": "attached files", "ko": "첨부 파일"},
    "kickoff.field.text": {"en": "answer", "ko": "답"},
    "kickoff.field.choice": {"en": "picked answer", "ko": "고른 답"},
    "kickoff.field.action": {"en": "reply type", "ko": "응답 종류"},
    "kickoff.field.strengths": {"en": "what you're good at", "ko": "잘하는 분야"},
    "kickoff.field.request": {"en": "request", "ko": "요청"},
    "kickoff.invalid.too_long": {"en": "The {field} is too long. Keep it to {max} characters.",
                                 "ko": "{field|이} 너무 길어요. {max}자까지 쓸 수 있어요."},
    "kickoff.invalid.missing": {"en": "Please fill in the {field}.", "ko": "{field|을} 적어 주세요."},
    "kickoff.invalid.action": {"en": "The reply type must be answer, unknown or later.",
                               "ko": "응답 종류는 answer, unknown, later 중 하나여야 해요."},
    "kickoff.invalid.format": {"en": "The {field} isn't in a form the server can read.",
                               "ko": "{field} 형식이 맞지 않아요."},
}
