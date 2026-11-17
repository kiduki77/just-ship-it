"""Team chat (`services/chat.py`, `services/chat_reply.py`): the lines the server writes in a conversation, the marker
that stands where a secret was removed, and why a message was refused. The agents' own replies are theirs."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    "chat.scrub.marker": {"en": "[removed: {what}]", "ko": "[지움: {what}]"},
    "chat.secret.private_key": {"en": "private key", "ko": "개인 키"},
    "chat.secret.github_token": {"en": "GitHub token", "ko": "GitHub 토큰"},
    "chat.secret.slack_token": {"en": "Slack token", "ko": "Slack 토큰"},
    "chat.secret.api_key": {"en": "API key", "ko": "API 키"},
    "chat.secret.aws_key": {"en": "AWS key", "ko": "AWS 키"},
    "chat.secret.google_key": {"en": "Google key", "ko": "Google 키"},
    "chat.secret.bearer": {"en": "access token", "ko": "접근 토큰"},
    "chat.secret.env_secret": {"en": "secret value", "ko": "비밀 값"},

    "chat.status.merge_set_aside": {
        "en": "Merge approval {ref} is set aside while this is fixed. A new one comes after the check.",
        "ko": "고치는 동안 머지 승인 {ref} 카드는 내려 둬요. 확인이 끝나면 새 카드가 와요."},
    "chat.status.card_set_aside": {
        "en": "{ref} is set aside while this is fixed. The check after the fix decides what comes next.",
        "ko": "고치는 동안 {ref} 카드는 내려 둬요. 고친 다음 확인 결과를 보고 다음 단계를 정해요."},
    "chat.status.round_busy": {
        "en": "Something on this build is already under way: a check, a merge, or a fix that hasn't started yet. "
              "Once it's done, ask me again and I'll take this on.",
        "ko": "이 개발 작업에 이미 진행 중인 일이 있어요. 확인이나 머지, 아직 시작하지 않은 수정이에요. 끝나면 다시 "
              "말씀해 주세요. 그때 바로 맡을게요."},
    "chat.status.round_queued": {"en": "The fix is next in line and starts as soon as a slot is free.",
                                 "ko": "고칠 일을 줄에 올렸어요. 자리가 나면 바로 시작해요."},
    "chat.status.round_budget": {
        "en": "The fix needs about ${need} and the project has ${left} left. It starts once the budget covers it.",
        "ko": "고치는 데 {need}달러쯤 드는데 프로젝트 예산이 {left}달러 남았어요. 예산이 되면 시작해요."},
    "chat.status.round_running": {"en": "Started on the fix.", "ko": "고치기 시작했어요."},
    "chat.status.round_delivered": {"en": "The fix is in. Product is checking it now.",
                                    "ko": "다 고쳤어요. 지금 제품기획이 확인하고 있어요."},
    "chat.status.round_done": {"en": "Checked. A new merge approval is waiting for you: {ref}.",
                               "ko": "확인을 마쳤어요. 새 머지 승인 카드가 왔어요: {ref}"},
    "chat.status.round_failed_check": {"en": "The check found problems, so it goes back for another fix before any merge.",
                                       "ko": "확인에서 문제가 나와서 머지 전에 한 번 더 고쳐요."},
    "chat.status.round_stopped": {"en": "The fix didn't finish: {cause} The branch is back where it was.",
                                  "ko": "고치기를 끝내지 못했어요: {cause} 브랜치는 원래대로 돌려 놨어요."},
    "chat.status.round_cancelled": {
        "en": "You cancelled the fix before it started. You can ask for the merge approval again on Engineering.",
        "ko": "시작하기 전에 고치기를 취소했어요. 개발 화면에서 머지 승인을 다시 요청할 수 있어요."},
    "chat.status.not_delivered": {"en": "This build can take changes once it's delivered.",
                                  "ko": "이 개발 작업은 전달된 다음에 고칠 수 있어요."},
    "chat.status.merged": {"en": "This build is merged already, so a change goes through a new spec.",
                           "ko": "이미 머지한 개발 작업이라 바꾸려면 새 명세로 가야 해요."},
    "chat.status.no_round": {"en": "The fix couldn't start right now. Engineering shows why.",
                             "ko": "지금은 고치기를 시작하지 못했어요. 이유는 개발 화면에 있어요."},
    "chat.status.reply_failed": {"en": "No answer came this time. Send your message again to ask once more.",
                                 "ko": "이번에는 답이 오지 않았어요. 메시지를 다시 보내면 다시 물어볼게요."},
    "chat.status.decision_filed": {"en": "Filed {ref} for you to decide in Decisions.",
                                   "ko": "결정할 일로 올렸어요: {ref}"},
    "chat.line.escalation": {"en": "{from} → {to}: {why}", "ko": "{from} → {to}: {why}"},
    "chat.line.handoff": {"en": "{from} → {to}: {why}", "ko": "{from} → {to}: {why}"},
    "chat.note.set_aside": {"en": "Set aside: you asked for a change in team chat. A new merge approval comes after the "
                                  "check.",
                            "ko": "팀 대화에서 수정을 요청해서 내려 뒀어요. 확인이 끝나면 새 머지 승인 카드가 와요."},

    "chat.refuse.empty": {"en": "Write a message first.", "ko": "메시지를 먼저 써 주세요."},
    "chat.refuse.too_long": {"en": "A message can be up to {n} characters.", "ko": "메시지는 {n}자까지 쓸 수 있어요."},
    "chat.refuse.bad_agent": {"en": "There's no team member called {agent}.", "ko": "그런 팀원이 없어요: {agent}"},
    "chat.refuse.no_thread": {"en": "That conversation no longer exists.", "ko": "그 대화를 찾을 수 없어요."},
    "chat.refuse.no_subject": {"en": "What this screen is about can't be found anymore.",
                               "ko": "이 화면이 다루던 일을 더는 찾을 수 없어요."},
    "chat.refuse.about_subject": {"en": "A step can only be attached to a build's conversation.",
                                  "ko": "단계는 개발 작업의 대화에만 붙일 수 있어요."},
    "chat.refuse.about_step": {"en": "That step isn't on this build's card, so it can't be attached.",
                               "ko": "이 개발 작업 카드에 없는 단계라서 붙일 수 없어요."},
    "chat.refuse.recall_subject": {"en": "Only a task or a build that a team worked on can be asked about.",
                                   "ko": "팀이 맡아 일한 업무나 개발 작업만 물어볼 수 있어요."},
    "chat.refuse.recall_role": {"en": "That role has no record of work on this job, so there's nothing to ask it about.",
                                "ko": "이 일에 그 역할이 일한 기록이 없어서 물어볼 수 없어요."},
    "chat.refuse.recall_to": {"en": "A question about past work goes to the role that did it.",
                              "ko": "지난 일에 대한 질문은 그 일을 한 역할에게 가요."},
    "chat.recall.no_record": {"en": "No record. What you asked about isn't in the records kept for this job.",
                              "ko": "기록이 없어요. 물어보신 내용은 이 일에 남은 기록에 없어요."},
    "chat.refuse.no_task": {"en": "That message didn't start any work.", "ko": "그 메시지로 시작한 일이 없어요."},
    "chat.refuse.started": {"en": "The work has already started, so it can't be cancelled here. Pause the engineer to "
                                  "stop it.",
                            "ko": "이미 시작해서 여기서는 취소할 수 없어요. 멈추려면 엔지니어를 멈춤으로 바꿔 주세요."},
}
