"""The product plan the owner approves before a spec is written (`engineering/plans.py`): plan.md's headings, its
card and what each choice on it does."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    "plan.doc.legend": {"en": "Each line says what it rests on: {firm}, {sourced} or {assumed}.",
                        "ko": "줄마다 끝에 근거를 적었어요. {firm}, {sourced}, {assumed} 중 하나예요."},
    "plan.doc.goal": {"en": "Goal and the problem to solve", "ko": "목표와 풀 문제"},
    "plan.doc.users": {"en": "Who it is for", "ko": "누가 쓰나요"},
    "plan.doc.scope": {"en": "What the first version does", "ko": "첫 버전에서 할 일"},
    "plan.doc.not_doing": {"en": "What we will not do", "ko": "하지 않을 일"},
    "plan.doc.success": {"en": "Success criteria and how to check them", "ko": "성공 기준과 확인 방법"},
    "plan.doc.evidence": {"en": "Evidence", "ko": "근거"},
    "plan.doc.risks": {"en": "Risks and open questions", "ko": "위험과 남은 질문"},
    "plan.doc.check": {"en": "How to check: {how}", "ko": "확인 방법: {how}"},
    "plan.doc.none": {"en": "Nothing here.", "ko": "없어요."},
    "plan.card.title": {"en": "Approve the product plan: {title}", "ko": "기획안 승인: {title}"},
    "plan.card.summary": {"en": {"one": "The product plan has {n} line. What it rests on: {mix}.",
                                 "other": "The product plan has {n} lines. What they rest on: {mix}."},
                          "ko": "기획안은 {n}줄이에요. 근거: {mix}."},
    "plan.card.part": {"en": "{grade} {count}", "ko": "{grade} {count}줄"},
    "plan.card.headline": {"en": "Approve this product plan?", "ko": "이 기획안을 승인할까요?"},
    "plan.card.owner_does": {"en": "Read the plan, then approve it or say what to change.",
                             "ko": "기획안을 읽고 승인하거나, 고칠 점을 적어 주세요."},
    "plan.card.recommendation": {"en": "The spec is written only from a plan you approved.",
                                 "ko": "명세는 오너님이 승인한 기획안으로만 써요."},
    "plan.option.approve": {"en": "Approve the plan", "ko": "기획안 승인"},
    "plan.option.approve.says": {"en": "The product agent writes the spec from this plan. You approve the spec before "
                                       "anything is built.",
                                 "ko": "제품기획 에이전트가 이 기획안으로 명세를 써요. 명세도 승인해야 개발을 시작해요."},
    "plan.option.changes": {"en": "Request changes", "ko": "수정 요청"},
    "plan.option.changes.says": {"en": "Write what to change in the note. The product agent rewrites the plan and asks "
                                       "you again.",
                                 "ko": "고칠 점을 메모에 적어 주세요. 제품기획 에이전트가 기획안을 다시 쓰고 다시 "
                                       "물어요."},
    "plan.option.hold": {"en": "Hold", "ko": "보류"},
    "plan.option.hold.says": {"en": "Nothing is written for now. You can reopen it later.",
                              "ko": "지금은 아무것도 쓰지 않아요. 나중에 다시 열 수 있어요."},
    "plan.option.decline": {"en": "Decline", "ko": "반려"},
    "plan.option.decline.says": {"en": "No spec is written for this build.", "ko": "이 개발은 명세를 쓰지 않아요."},
    "plan.changes.no_note": {"en": "Write what to change in the note, then send it.",
                             "ko": "고칠 점을 메모에 적어서 보내 주세요."},
    "plan.not_found": {"en": "That product plan doesn't exist.", "ko": "없는 기획안이에요."},
    "plan.revise.not_approved": {"en": "Only an approved plan can be revised. A plan still waiting on its card is "
                                       "changed on that card.",
                                 "ko": "승인한 기획안만 고칠 수 있어요. 기다리는 기획안은 카드에서 고쳐 주세요."},
    "plan.revise.frozen": {"en": "Its spec is approved, so this plan can't change. Ask for a new build to change "
                                 "direction.",
                           "ko": "이 기획안으로 쓴 명세를 승인해서 기획안을 바꿀 수 없어요. 방향을 바꾸려면 새 개발을 "
                                 "요청해 주세요."},
    "plan.revise.withdrawn": {"en": "The product plan is being revised. The spec is written again from the new plan.",
                              "ko": "기획안을 고치는 중이에요. 새 기획안으로 명세를 다시 써요."},
}
