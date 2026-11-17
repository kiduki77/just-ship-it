"""Decisions (`services/requests.py`): what a decision card says approving does, the owner's part, the refusals the
server gives a decision it cannot take, and the notes it keeps on a card and in the activity log."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    # what the owner decides, as a word inside a sentence
    "request.verdict.approve": {"en": "approve", "ko": "승인"},
    "request.verdict.answer": {"en": "answer", "ko": "답"},
    "request.verdict.reject": {"en": "decline", "ko": "반려"},
    "request.verdict.defer": {"en": "hold", "ko": "보류"},

    # refusals of a decision (409)
    "request.not_found": {"en": "{ref} not found.", "ko": "{ref|을} 찾지 못했어요."},
    "request.already_decided": {"en": "{ref} was already decided ({status}).",
                                "ko": "{ref|은} 이미 정한 결정이에요({status})."},
    "request.reopen.not_closed": {"en": "{ref} isn't on hold or declined, so there's nothing to reopen.",
                                  "ko": "{ref|은} 보류하거나 반려한 결정이 아니라서 다시 열 게 없어요."},
    "request.reopen.build": {
        "en": "This decision belongs to a build, which already acted on your answer. Ask again from the build on the "
              "Engineering screen.",
        "ko": "개발 작업에 딸린 결정이라 작업이 이미 이 답에 맞춰 움직였어요. 개발 화면에서 그 개발 작업을 열고 다시 요청해 주세요."},
    "request.reopen.flow": {
        "en": "The company already acted on the decline, so this can't be reopened. If it's still wanted, an agent can "
              "ask again.",
        "ko": "회사가 이미 반려에 맞춰 움직여서 다시 열 수 없어요. 아직 필요하면 에이전트가 다시 물을 수 있어요."},
    "request.reopen.archived_project": {
        "en": "{ref} is archived, so this decision can't be reopened. Unarchive the project first.",
        "ko": "{ref|은} 보관한 프로젝트라 이 결정을 다시 열 수 없어요. 먼저 보관을 해제해 주세요."},
    "request.reopen.archived_business": {
        "en": "{ref} is archived, so this decision can't be reopened. Unarchive the business first.",
        "ko": "{ref|은} 보관한 사업이라 이 결정을 다시 열 수 없어요. 먼저 사업 보관을 해제해 주세요."},
    "request.reopen.expired":{"en": "{ref} has expired, so it can't be reopened. If it's still needed, an agent asks "
                                     "again.",
                               "ko": "{ref|은} 기한이 지나서 다시 열 수 없어요. 아직 필요하면 에이전트가 다시 물어요."},
    "request.reopen.followed": {
        "en": "This can't be reopened: work has moved on since you declined. The Project Manager re-planned around your note, or "
              "the project carried on without it.",
        "ko": "반려한 뒤로 일이 이미 진행돼서 다시 열 수 없어요. 프로젝트 매니저가 메모에 맞춰 계획을 고쳤거나, 프로젝트가 이 "
              "결정 없이 계속했어요."},
    "request.stale": {"en": "The card changed. Read it again before you decide.",
                      "ko": "내용이 바뀌었어요. 다시 읽고 결정해 주세요."},
    "request.expired": {"en": "{ref} has expired, so it can't be approved. Decline it or put it on hold instead.",
                        "ko": "{ref|은} 만료돼서 승인할 수 없어요. 반려하거나 보류해 주세요."},
    "request.typed_confirm": {"en": "This approval is hard to take back, so it needs one more check. Type this decision's "
                                    "number, {ref}, in the confirmation box.",
                              "ko": "되돌리기 어려운 승인이라 한 번 더 확인해야 해요. 확인란에 이 결정의 번호 {ref|을} 적어 주세요."},
    "request.typed_confirm.unknown_spend": {
        "en": "This spends {cost} on an action the company has no rule for, so it needs one more check. Type this "
              "decision's number, {ref}, in the confirmation box.",
        "ko": "회사 규칙에 없는 지출({cost})이라 한 번 더 확인해야 해요. 확인란에 이 결정의 번호 {ref|을} 적어 주세요."},
    "request.plan_locked": {"en": "'{label}' changes your plan, and the plan can't change right now. {why} Or answer "
                                  "in your own words instead.",
                            "ko": "'{label}' 선택지는 계획을 바꾸는데, 지금은 계획을 바꿀 수 없어요. {why} 아니면 직접 답을 "
                                  "적어 주세요."},
    "request.plan_line_gone": {"en": "'{label}' changes a line that is no longer in your approved plan. Pick another "
                                     "option or answer in your own words.",
                               "ko": "'{label}' 선택지가 바꾸려던 줄이 승인한 계획에 없어요. 다른 선택지를 고르거나 "
                                     "직접 답을 적어 주세요."},
    "request.needs_answer": {"en": "Write an answer or pick an option.", "ko": "답을 적거나 선택지를 골라 주세요."},
    "request.screen_notes_decline": {
        "en": "Notes on a screen go with sending the spec back. Send it back with them, or clear them first.",
        "ko": "화면 메모는 명세를 돌려보낼 때 함께 가요. 메모와 함께 돌려보내거나, 메모를 먼저 지워 주세요."},
    "request.verdict_mismatch": {"en": "'{label}' means {verdict}, which isn't the decision you chose.",
                                 "ko": "'{label}' 선택지는 {verdict} 쪽이에요. 고른 결정과 달라요."},
    "request.declining_option": {
        "en": "'{label}' says not to do it, but approving would run it. Decline it or put it on hold instead.",
        "ko": "'{label}' 선택지는 하지 않겠다는 뜻이에요. 승인하면 실행되니 반려나 보류로 정해 주세요."},
    "request.not_an_option": {"en": "'{label}' isn't an option on this card. Pick one of the card's options.",
                              "ko": "'{label}' 선택지는 이 카드에 없어요. 카드에 있는 선택지에서 골라 주세요."},
    "request.no_draft": {"en": "This decision has no draft post to rewrite.", "ko": "고쳐 쓸 글 초안이 없는 결정이에요."},
    "request.empty_body": {"en": "The rewritten post has no body.", "ko": "고친 글의 본문이 비어 있어요."},
    "request.not_external": {"en": "This isn't an action you can ask for again.",
                             "ko": "다시 요청할 수 있는 실행이 아니에요."},
    "request.already_ran": {"en": "It already ran (A-{id}).", "ko": "이미 실행을 끝냈어요(A-{id})."},
    "request.still_running": {"en": "It's still running: {why}", "ko": "아직 실행 중이에요: {why}"},
    "request.screen_old": {"en": "This screen is out of date. Refresh it, read the card again, then decide.",
                           "ko": "화면 내용이 오래됐어요. 새로고침하고 카드를 다시 읽은 뒤 결정해 주세요."},

    # what approving does (the card's "If you approve")
    "request.effect.dev_resume": {"en": "Build resumes with your answer", "ko": "답을 넘겨 멈춘 개발을 이어 가요"},
    "request.effect.dev_fix": {"en": "The team fixes it on the same branch, then asks about the merge again", "ko": "개발팀이 같은 브랜치에서 고친 뒤 머지를 다시 물어요"},
    "request.effect.dev_merge": {"en": "Merges the build branch into the default branch",
                                 "ko": "개발 브랜치를 기본 브랜치에 머지해요"},
    "request.effect.dev_merge.undo": {"en": "You'd revert it in the repository yourself", "ko": "저장소에서 직접 되돌려야 해요"},
    "request.effect.spec_question": {"en": "Your answer goes into the spec", "ko": "답을 명세에 넣어요"},
    "request.effect.spec_approved": {"en": "Development starts from this spec", "ko": "이 명세로 개발을 시작해요"},
    "request.effect.spec_approved.undo": {"en": "Usage spent on the build doesn't come back",
                                          "ko": "개발에 쓴 사용량은 돌아오지 않아요"},
    "request.effect.write_spec": {"en": "The product agent writes a spec", "ko": "제품기획 에이전트가 명세를 써요"},
    "request.effect.plan_approved": {"en": "The product agent writes the spec from this plan",
                                     "ko": "제품기획 에이전트가 이 기획안으로 명세를 써요"},
    "request.effect.execute": {"en": "Runs once and {sends}", "ko": "한 번 실행하고, {sends}"},
    "request.effect.sends.live": {"en": "goes out right away", "ko": "바로 밖으로 나가요"},
    "request.effect.sends.outbox": {"en": "stays in the outbox", "ko": "보관함에만 쌓여요"},
    "request.effect.sends.off": {"en": "nothing goes out, the connector is off", "ko": "커넥터가 꺼져 있어 기록만 남아요"},
    "request.effect.sends.none": {"en": "is recorded", "ko": "기록을 남겨요"},
    "request.effect.budget_raise": {"en": "Adds ${amount} to this project's budget", "ko": "이 프로젝트 예산을 ${amount} 늘려요"},
    "request.effect.budget_raise.undo": {"en": "Lower it again in the project's settings", "ko": "프로젝트 설정에서 다시 낮출 수 있어요"},
    "request.effect.reassess": {"en": "Reassesses what's left of the project's plan",
                                "ko": "프로젝트의 남은 계획을 다시 평가해요"},
    "request.effect.skill": {"en": "Turns the skill on", "ko": "스킬을 켜요"},
    "request.effect.routine": {"en": "Creates a routine and runs it once now", "ko": "정기 업무를 만들고 바로 한 번 실행해요"},
    "request.effect.routine.undo": {"en": "You can stop it any time", "ko": "언제든 멈출 수 있어요"},
    "request.effect.direction_change": {"en": "Adds it as a candidate for the next experiment", "ko": "다음 실험 후보로 올려요"},
    "request.effect.answered": {"en": "Your answer is kept and agents read it", "ko": "답을 남겨요. 에이전트가 읽어요"},
    "request.effect.answered.undo": {"en": "Correct it from the project's Overview", "ko": "프로젝트 개요에서 바로잡을 수 있어요"},
    "request.effect.owner_task": {"en": "Adds a to-do that you do yourself. The company doesn't do it",
                                  "ko": "내 할 일이 하나 생기고, 오너님이 직접 해요. 회사는 하지 않아요"},
    "request.effect.owner_task.post": {
        "en": "Adds a to-do with the post ready to copy. You post it yourself. The company never posts for you",
        "ko": "글이 준비된 내 할 일이 하나 생겨요. 오너님이 직접 올려요. 회사가 대신 올리지는 않아요"},
    "request.effect.owner_task.post_outline": {
        "en": "Adds a to-do with the facts and a checklist. You write the post and post it yourself. The company never "
              "posts for you",
        "ko": "사실과 확인 목록이 담긴 내 할 일이 하나 생겨요. 글은 오너님이 직접 쓰고 올려요. 회사가 대신 올리지는 않아요"},
    "request.effect.owner_task.publish": {
        "en": "Adds a to-do for you to make it public yourself. The company never publishes for you",
        "ko": "직접 공개하는 내 할 일이 하나 생겨요. 회사가 대신 공개하지는 않아요"},
    "request.effect.owner_task.spend": {
        "en": "Adds a to-do for you to pay for it yourself. The company never spends your money. "
              "It doesn't change any project's budget",
        "ko": "직접 결제하는 내 할 일이 하나 생겨요. 회사가 오너님 돈을 대신 쓰지는 않아요. 프로젝트 예산은 바뀌지 않아요"},
    "request.effect.owner_task.budget": {
        "en": "Adds a to-do for you to pay for it yourself. It doesn't raise the project's budget. "
              "To let a stopped project run again, raise its budget in the project's settings",
        "ko": "직접 결제하는 내 할 일이 하나 생겨요. 프로젝트 예산은 올라가지 않아요. "
              "예산 때문에 멈춘 프로젝트를 다시 돌리려면 프로젝트 설정에서 예산을 올려 주세요"},
    "request.effect.owner_task.check": {"en": "Adds a to-do for you to check it yourself. The company waits for your answer", "ko": "직접 확인하는 내 할 일이 하나 생겨요. 회사는 오너님 답을 기다려요"},
    "request.effect.owner_task.revises": {"en": "Your open to-do '{title}' changes to this version", "ko": "열려 있는 내 할 일 '{title}' 내용이 이 내용으로 바뀌어요"},
    "request.effect.owner_task.joins": {"en": "The message goes on your to-do '{title}', ready to copy. The company doesn't send it", "ko": "메시지가 내 할 일 '{title}'에 들어가요. 복사해 직접 보내요. 회사는 보내지 않아요"},
    "request.effect.owner_task.undo": {"en": "You can skip it before you do it", "ko": "하기 전에는 건너뛸 수 있어요"},
    "request.effect.owner_pays": {"en": "Its cost, about {cost}, is yours to pay. The company never spends your money",
                                  "ko": "드는 돈 약 {cost}도 오너님이 직접 내요. 회사가 오너님 돈을 대신 쓰지는 않아요"},
    "request.effect.record_only": {"en": "Records the approval only", "ko": "승인만 기록해요"},
    "request.effect.by_hand": {"en": "Records the approval only", "ko": "승인만 기록해요"},
    "request.effect.team_follows": {"en": "The team works as you approved", "ko": "팀이 승인대로 일해요"},
    "request.effect.record": {"en": "Records the decision", "ko": "결정을 기록해요"},
    "request.effect.open_checks": {"en": "Not yet checked by you: {titles}. That part is built on a guess", "ko": "직접 확인할 일이 남았어요({titles}). 그 부분은 짐작으로 만들어요"},
    "request.effect.resumes": {"en": "The paused project starts again", "ko": "멈춘 프로젝트를 다시 시작해요"},
    "request.effect.still_waits": {"en": "Starts again once the other blocking decisions are settled", "ko": "일을 막는 다른 결정이 정해지면 다시 시작해요"},
    "request.closed.withdrawn_ceo": {"en": "The Project Manager withdrew this. {why}", "ko": "프로젝트 매니저가 이 결정을 거뒀어요. {why}"},
    "request.closed.withdrawn": {"en": "The company withdrew this. {why}", "ko": "회사가 이 결정을 거뒀어요. {why}"},
    "request.closed.expired": {"en": "Nobody answered by the deadline, so this closed.",
                               "ko": "기한까지 답이 없어 만료되고 닫혔어요."},
    "request.closed.expired_default": {
        "en": "Nobody answered by the deadline, so the team went with “{option}”. It's recorded as the default, not as "
              "your answer.",
        "ko": "기한까지 답이 없어 만료됐고, 기본값인 “{option}” 쪽으로 진행했어요. 오너님의 답이 아니라 기본값으로 "
              "기록했어요."},
    "request.closed.auto": {"en": "Your rules let this go ahead without asking you.",
                            "ko": "정해 둔 규칙대로 오너님께 묻지 않고 진행했어요."},
    "request.closed.auto.write_spec": {"en": "Writing a spec needs no approval. You approve the spec before anything is built.",
                                       "ko": "명세를 쓰는 데는 승인이 필요 없어요. 만들기 전에 명세를 오너님이 승인해요."},
    "request.undo.easy": {"en": "Easy to undo", "ko": "되돌리기 쉬워요"},
    "request.undo.hard": {"en": "Takes work to undo", "ko": "되돌리기 번거로워요"},
    "request.undo.none": {"en": "Can't be undone", "ko": "되돌릴 수 없어요"},

    # the owner's part, when the card names none
    "request.owner_does.post": {"en": "Approve, then post it yourself", "ko": "승인 후 직접 게시"},
    "request.owner_does.publish": {"en": "Approve, then publish it yourself", "ko": "승인 후 직접 공개"},
    "request.owner_does.spend": {"en": "Approve, then pay for it yourself", "ko": "승인 후 직접 결제"},
    "request.owner_does.other": {"en": "Approve, then do it yourself", "ko": "승인 후 직접 처리"},
    "request.owner_does.check": {"en": "Approve, then check it yourself", "ko": "승인 후 직접 확인"},
    "request.owner_does.decide": {"en": "Approval needed", "ko": "승인 필요"},
    "request.owner_does.decide_only": {"en": "Approval needed", "ko": "승인 필요"},
    "request.owner_does.pick": {"en": "Pick an answer", "ko": "답 고르기"},
    "request.owner_does.write": {"en": "Write an answer", "ko": "답 적기"},
    "request.option": {"en": "Option {n}", "ko": "선택지 {n}"},
    "request.revises": {"en": "The card you already approved: {title}", "ko": "이미 승인한 카드: {title}"},

    # notes the server keeps on a card and in the activity log
    "request.note.asked": {"en": "{agent}'s '{title}'", "ko": "{agent}의 '{title}'"},
    "request.note.same_ask": {"en": "[{agent} asked the same]", "ko": "[{agent}도 같은 요청]"},
    "request.note.revised": {"en": "Revised {ref} (version {n}): {asked}", "ko": "{ref} 카드를 고쳤어요({n}번째 안): {asked}"},
    "request.note.next_version": {"en": "For the next version, not in this spec", "ko": "다음 버전 후보, 이 명세에는 없어요"},
    "request.note.also": {"en": "Also proposed", "ko": "추가 후보"},
    "request.note.folded": {"en": "Not raised as a new card: {asked}. {why}", "ko": "새 카드로 올리지 않았어요: {asked}. {why}"},
    "request.note.task_updated": {"en": "Added the revision to {ot}: {asked}", "ko": "{ot}에 고친 내용을 붙였어요: {asked}"},
    "request.note.rework": {"en": "Replaced by the cards from T-{task}'s rework",
                            "ko": "T-{task} 업무를 다시 하면서 새 카드로 바뀌었어요"},
    "request.note.merged": {"en": "[{ref} merged · {agent}]", "ko": "[{ref} 합침 · {agent}]"},
    "request.note.directive": {"en": "Not raised: your instruction {source} ruled this out ({rule})",
                               "ko": "오너님이 지시 {source}에서 하지 말라고 한 일이라 올리지 않았어요({rule})"},
    "request.note.ceo_decided": {"en": "The Project Manager approved this: a decision rule leaves it to the Project Manager ({action}).",
                                 "ko": "프로젝트 매니저에게 맡긴 일이라 프로젝트 매니저가 승인했어요({action})."},
    "request.note.ceo_decided_event": {"en": "{ref} {title}: decided by the Project Manager ({action} rule)",
                                       "ko": "{ref} {title}: 프로젝트 매니저가 정했어요({action} 규칙)"},
    "request.note.rules_ignored": {
        "en": {"one": "{ref}: {n} decision rule didn't apply ({why})", "other": "{ref}: {n} decision rules didn't apply ({why})"},
        "ko": "{ref}: 결정 권한 규칙 {n}건은 적용하지 않았어요({why})"},
    "request.note.post_refused": {"en": "{ref} {title}: the post card was refused. {refusal}",
                                  "ko": "{ref} {title}: 올릴 글 카드를 받지 않았어요. {refusal}"},
    "request.note.post_stripped": {
        "en": "{ref}: this channel takes only your own words, so the agent's draft ({fields}) was removed",
        "ko": "{ref}: 직접 쓴 글만 받는 채널이라 에이전트가 쓴 초안({fields})을 지웠어요"},
    "request.field.title": {"en": "title", "ko": "제목"},
    "request.field.body": {"en": "body", "ko": "본문"},
    "request.note.again_title": {"en": "Asked again: {title}", "ko": "다시 요청: {title}"},
    "request.note.again_why": {"en": "{ref} failed to run, so you asked again.",
                               "ko": "{ref} 실행이 실패해서 오너님이 다시 요청했어요."},
}
