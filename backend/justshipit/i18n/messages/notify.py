"""Slack notices for the owner (`contracts/notify.py`, `operations/notify.py`): each notice's words, the daily
briefing's lines, and what the sender says when Slack refuses. A notice is put into words when it is sent, so it reads
in the owner's language at that moment."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    "notify.label.decision_needed": {"en": "Decision needed", "ko": "결정이 필요해요"},
    "notify.label.failed": {"en": "Failed", "ko": "실패했어요"},
    "notify.label.usage_wait": {"en": "Waiting on usage", "ko": "사용량 대기"},
    "notify.label.owner_task": {"en": "To-do", "ko": "내 할 일"},
    "notify.label.daily_briefing": {"en": "Daily briefing", "ko": "오늘 브리핑"},
    "notify.view": {"en": "Open in the console", "ko": "콘솔에서 보기"},

    "notify.run_failed": {"en": "{name}: the run failed", "ko": "{name} 실행이 실패했어요"},
    "notify.dev_failed": {"en": "{spec}: the build failed", "ko": "{spec} 개발이 실패했어요"},
    "notify.merge_failed": {"en": "{spec}: the merge failed", "ko": "{spec} 머지가 실패했어요"},
    "notify.usage_wait.title": {"en": "Work is waiting: the company hit the {reason}",
                                "ko": "{reason}에 닿아서 일이 기다려요"},
    "notify.when": {"en": "{month} {day}, {time} ({zone})", "ko": "{month}월 {day}일 {time} ({zone})"},
    **{f"notify.month.{n}": {"en": name, "ko": str(n)} for n, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), start=1)},
    "notify.usage_wait.body": {"en": "It picks up again at {until}. {detail}", "ko": "{until}에 이어서 해요. {detail}"},
    "notify.worker_down.title": {"en": "The background process stopped", "ko": "백그라운드 프로세스가 멈췄어요"},
    "notify.worker_down.body": {
        "en": "The background process hasn't answered since {seen}, so company work has stopped. Start it again on this PC with "
              "`uv run ship supervise`. The log is in var/logs/worker.log.",
        "ko": "{seen} 이후로 백그라운드 프로세스가 응답하지 않아서 회사 일이 멈췄어요. 이 PC에서 `uv run ship supervise`로 "
              "다시 켜 주세요. 로그는 var/logs/worker.log에 있어요."},
    "notify.owner_task.body": {"en": "Only you can do this ({kind}).", "ko": "오너님이 직접 해야 하는 일이에요({kind})."},
    "notify.briefing.title": {"en": "What to know today", "ko": "오늘 알려 드릴 일"},
    "notify.stage_gate": {"en": "Stage done: {title}", "ko": "단계가 끝났어요: {title}"},
    "notify.stage_gate.body": {"en": "The company stopped and waits for you. Read the summary and choose the next step.",
                               "ko": "회사가 멈추고 오너님을 기다려요. 요약을 읽고 다음 단계를 정해 주세요."},
    "notify.expiring.title": {"en": "Expires soon: {title}", "ko": "곧 만료돼요: {title}"},
    "notify.expiring.default": {
        "en": "If you don't answer by {when}, the team goes with “{option}” and records it as the default, not as your "
              "answer.",
        "ko": "{when}까지 답이 없으면 기본값인 “{option}” 쪽으로 진행해요. 오너님의 답이 아니라 기본값으로 기록해요."},
    "notify.expiring.close": {
        "en": "If you don't answer by {when}, this decision closes and the Project Manager re-plans without it.",
        "ko": "{when}까지 답이 없으면 이 결정은 닫히고 프로젝트 매니저가 이 결정 없이 다시 계획해요."},
    "notify.cycle_proposal": {"en": "The Project Manager proposed a cycle", "ko": "프로젝트 매니저가 사이클을 제안했어요"},
    "notify.cycle_proposal.body": {"en": "Read the stages and start the first one, or say what to change.",
                                   "ko": "단계를 읽고 첫 단계를 시작하거나 바꿀 점을 말해 주세요."},

    "notify.briefing.waiting": {"en": {"one": "{n} decision is waiting", "other": "{n} decisions are waiting"},
                                "ko": "기다리는 결정 {n}건"},
    "notify.briefing.stuck": {"en": ", {n} of them holding up work", "ko": "(그중 {n}건이 일을 막고 있어요)"},
    "notify.briefing.failed": {"en": "Failed: {projects} in projects, {builds} in builds",
                               "ko": "실패한 일: 프로젝트 {projects}건, 개발 {builds}건"},
    "notify.briefing.todos": {"en": {"one": "{n} to-do is open", "other": "{n} to-dos are open"},
                              "ko": "남은 내 할 일 {n}개"},
    "notify.briefing.usage_waits": {"en": {"one": "{n} job is waiting on usage", "other": "{n} jobs are waiting on usage"},
                                    "ko": "사용량 때문에 기다리는 작업 {n}개"},
    "notify.briefing.people_today": {"en": {"one": "{n} person to reach out to today",
                                            "other": "{n} people to reach out to today"},
                                     "ko": "오늘 연락할 사람 {n}명"},
    "notify.briefing.weekly":{"en": "Weekly roundup: this includes what has stayed open a while",
                               "ko": "주간 정리: 오래 열려 있는 일도 함께 알려요"},
    "notify.briefing.usage": {"en": "Usage at list price: {today}, {week}", "ko": "사용량(정가 환산): {today}, {week}"},
    "notify.briefing.usage_today": {"en": "today ${used} of ${cap} ({pct}%)", "ko": "오늘 ${used}/${cap}({pct}%)"},
    "notify.briefing.usage_week": {"en": "this week ${used} of ${cap} ({pct}%)", "ko": "이번 주 ${used}/${cap}({pct}%)"},
    "notify.briefing.usage_today_uncapped": {"en": "today ${used} (no cap)", "ko": "오늘 ${used}(상한 없음)"},
    "notify.briefing.usage_week_uncapped": {"en": "this week ${used} (no cap)", "ko": "이번 주 ${used}(상한 없음)"},

    "notify.slack.auth": {
        "en": "Slack refused the bot token: it's wrong or was revoked. Enter it again in Settings > Connections.",
        "ko": "Slack이 봇 토큰을 받지 않아요. 토큰이 틀렸거나 취소됐어요. 설정 > 연결에서 토큰을 다시 넣어 주세요."},
    "notify.slack.channel_not_found": {
        "en": "Slack couldn't find the channel. Check its name or ID in Settings > Notifications.",
        "ko": "Slack 채널을 찾지 못했어요. 설정 > 알림에서 채널 이름이나 ID를 확인해 주세요."},
    "notify.slack.not_in_channel": {"en": "The bot isn't in this channel. Invite it to the channel in Slack.",
                                    "ko": "봇이 이 채널에 없어요. Slack에서 채널에 봇을 초대해 주세요."},
    "notify.slack.is_archived": {"en": "The notification channel is archived. Pick another channel.",
                                 "ko": "알림 채널이 보관됐어요. 다른 채널을 골라 주세요."},
    "notify.slack.missing_scope": {
        "en": "The bot doesn't have the chat:write scope. Add chat:write to the Slack app's scopes and reinstall it.",
        "ko": "봇에 chat:write 권한이 없어요. Slack 앱 권한에 chat:write를 넣고 다시 설치해 주세요."},
    "notify.slack.no_permission": {"en": "The bot can't post in this channel. Check the channel's settings.",
                                   "ko": "봇이 이 채널에 글을 쓸 수 없어요. 채널 설정을 확인해 주세요."},
    "notify.slack.server_error": {
        "en": "Slack returned an error ({status}). It's unknown whether the message reached the channel.",
        "ko": "Slack 서버에서 오류({status})가 났어요. 메시지가 채널에 갔는지 알 수 없어요."},
    "notify.slack.unreadable": {
        "en": "Couldn't read Slack's answer. It's unknown whether the message reached the channel.",
        "ko": "Slack의 응답을 읽지 못했어요. 메시지가 채널에 갔는지 알 수 없어요."},
    "notify.slack.check_unanswered": {
        "en": "Slack couldn't check the token right now. Nothing was posted. Try the test again in a moment.",
        "ko": "Slack이 지금은 토큰을 확인하지 못했어요. 아무것도 올리지 않았어요. 잠시 뒤 다시 시험해 주세요."},
    "notify.slack.refused": {"en": "Slack didn't accept the message (error code: {code}).",
                             "ko": "Slack이 메시지를 받지 않았어요(오류 코드: {code})."},
    "notify.slack.unknown_code": {"en": "unknown", "ko": "알 수 없음"},
    "notify.slack.no_channel": {"en": "No Slack channel is set for notices. Add one in Settings > Notifications.",
                                "ko": "알림을 보낼 Slack 채널이 없어요. 설정 > 알림에서 채널을 넣어 주세요."},
    "notify.slack.no_token": {"en": "There's no Slack bot token. Add it in Settings > Connections.",
                              "ko": "Slack 봇 토큰이 없어요. 설정 > 연결에 넣어 주세요."},
    "notify.slack.bad_token": {
        "en": "The saved Slack bot token holds characters that can't be sent. Enter it again in Settings > "
              "Connections; it starts with xoxb-.",
        "ko": "저장한 Slack 봇 토큰에 보낼 수 없는 문자가 있어요. 설정 > 연결에서 xoxb-로 시작하는 토큰을 다시 넣어 "
              "주세요."},
    "notify.slack.cant_connect": {"en": "Couldn't reach Slack ({error}). Check the network. It tries again soon.",
                                  "ko": "Slack에 연결하지 못했어요({error}). 네트워크를 확인해 주세요. 곧 다시 시도해요."},
    "notify.slack.no_answer": {
        "en": "Got no answer from Slack ({error}). It's unknown whether the message reached the channel.",
        "ko": "Slack에서 응답을 받지 못했어요({error}). 메시지가 채널에 갔는지 알 수 없어요."},
    "notify.slack.busy_test": {"en": "Slack says there are too many requests. Try the test again in a moment.",
                               "ko": "Slack이 요청이 너무 많다고 해요. 잠시 뒤 다시 시험해 주세요."},
    "notify.slack.busy": {
        "en": "Slack says there are too many requests and asked to wait {s} seconds. It tries again later.",
        "ko": "Slack이 요청이 너무 많다며 {s}초 뒤에 다시 보내 달라고 했어요. 나중에 다시 보내요."},
    "notify.slack.gave_up": {
        "en": "{error} It couldn't confirm twice, so it stopped rather than send the same message again. Check "
              "whether it's already in the Slack channel.",
        "ko": "{error} 두 번 다 확인하지 못해서, 같은 메시지를 또 보내지 않고 멈췄어요. Slack 채널에 이미 와 있는지 "
              "확인해 주세요."},
    "notify.quiet": {"en": "Holding notices until quiet hours end", "ko": "조용한 시간이 끝나면 알림을 보내요"},

    "notify.test.off": {
        "en": "Slack notices are Off, so nothing was tested. Choose Outbox or Send right away for Slack notices in "
              "Settings > Sending out.",
        "ko": "Slack 알림이 꺼져 있어서 시험하지 않았어요. 설정 > 밖으로 보내기에서 Slack 알림을 '보관함'이나 "
              "'바로 보내기'로 바꿔 주세요."},
    "notify.test.title": {"en": "Connection test", "ko": "연결 시험"},
    "notify.test.body": {"en": "A test message from Just Ship It. If you can see it, notices will come to this channel.",
                         "ko": "Just Ship It이 보낸 시험 메시지예요. 이 메시지가 보이면 알림이 이 채널로 와요."},
    "notify.test.outbox": {
        "en": "Slack accepted the token. Nothing was posted: Slack notices are set to Outbox, so the test message was "
              "kept in the outbox. To send for real, choose Send right away for Slack notices in Sending out. Whether "
              "the app can post in the channel shows when the first real message goes out.",
        "ko": "Slack이 토큰을 받았어요. Slack 알림이 보관함으로 설정돼 있어서 아무것도 올리지 않고 시험 메시지를 "
              "보관함에 남겼어요. 실제로 보내려면 밖으로 보내기에서 Slack 알림을 '바로 보내기'로 바꿔 주세요. 앱이 그 "
              "채널에 글을 올릴 수 있는지는 실제 메시지가 처음 나갈 때 알 수 있어요."},
    "notify.test.sent": {"en": "Sent a test message to {channel}. Check Slack.",
                         "ko": "{channel} 채널에 시험 메시지를 보냈어요. Slack에서 확인해 주세요."},
}
