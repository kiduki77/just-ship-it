"""Recurring operations (`operations/`): the reports they file, the decisions they raise, what they sent and
why they didn't, and the names of the routines."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    "operations.none": {"en": "none", "ko": "없음"},
    "operations.source_lost": {"en": "Source recheck failed: {note}", "ko": "출처를 다시 확인하지 못했어요: {note}"},
    "operations.watch.title": {"en": "Change watch", "ko": "변화 감시"},
    "operations.watch.heading": {"en": "# Change watch: {name} ({date})", "ko": "# 변화 감시: {name} ({date})"},
    "operations.watch.lost": {"en": "Sources that failed the recheck: {ids}", "ko": "다시 확인하지 못한 출처: {ids}"},
    "operations.watch.changes": {"en": "## Material changes", "ko": "## 중요한 변화"},
    "operations.reassess.title": {"en": "Reassess P-{id}'s conclusion", "ko": "P-{id} 결론 다시 평가하기"},
    "operations.reassess.rationale": {"en": "Change watch report: registry/{path}", "ko": "변화 감시 보고서: registry/{path}"},
    "operations.reassess.start": {"en": "Approve the reassessment project", "ko": "다시 평가 프로젝트 시작"},
    "operations.reassess.name": {"en": "{name}: reassessment", "ko": "{name}: 다시 평가"},
    "operations.option.hold": {"en": "Hold", "ko": "보류"},
    "operations.option.reject": {"en": "Reject", "ko": "반려"},
    "operations.kpi.title": {"en": "KPI review", "ko": "KPI 리뷰"},
    "operations.kpi.heading": {"en": "# KPI review ({state})", "ko": "# KPI 리뷰 ({state})"},
    "operations.kpi.on_track": {"en": "on track", "ko": "목표대로"},
    "operations.kpi.watch": {"en": "needs attention", "ko": "주의"},
    "operations.kpi.causes": {"en": "## Possible causes", "ko": "## 짐작되는 원인"},
    "operations.portfolio.title": {"en": "Weekly review of all businesses", "ko": "사업 전체 주간 점검"},
    "operations.portfolio.heading": {"en": "# Weekly review of all businesses ({date})", "ko": "# 사업 전체 주간 점검 ({date})"},
    "operations.portfolio.priorities": {"en": "## Priorities", "ko": "## 우선순위"},
    "operations.portfolio.stalled": {"en": "## Stuck work", "ko": "## 막힌 일"},

    "operations.connector.email": {"en": "email", "ko": "메일"},
    "operations.connector.calendar": {"en": "calendar", "ko": "캘린더"},
    "operations.connector.survey": {"en": "survey", "ko": "설문"},
    "operations.unread_settings": {
        "en": "Couldn't read the settings, so nothing was sent ({error}). Try the job again in a moment.",
        "ko": "설정을 읽지 못해서 보내지 않았어요({error}). 잠시 뒤에 작업을 다시 시도해 주세요."},
    "operations.connector_off": {
        "en": "The {connector} connector is off in Settings, so nothing was sent. Turn it on in Settings, then try the job again.",
        "ko": "{connector} 커넥터가 설정에서 꺼져 있어서 보내지 않았어요. 설정에서 켠 뒤 작업을 다시 시도해 주세요."},
    "operations.connector_opened": {
        "en": "The {connector} connector's sending mode was opened up after the approval, so nothing was sent. That isn't what you approved, so ask again for a new approval.",
        "ko": "승인한 뒤 {connector} 커넥터의 보내기 방식이 더 열린 쪽으로 바뀌어서 보내지 않았어요. 승인할 때와 조건이 달라서, 다시 요청해서 새로 승인받아 주세요."},
    "operations.maybe_sent": {
        "en": "{ref}: an earlier attempt (A-{id}, {status}) may already have gone out, so it isn't sent again. Check the result, and if it's still needed, get a new approval with 'Ask again' on the alert.",
        "ko": "{ref}: 이전 시도(A-{id}, {status})가 이미 밖으로 나갔을 수 있어서 다시 보내지 않아요. 결과를 확인하고, 다시 필요하면 알림의 '다시 요청'으로 새로 승인받아 주세요."},
    "operations.sent.outbox_email": {"en": "Saved to the outbox (not sent): {path}", "ko": "보관함에 저장했어요(보내지 않음): {path}"},
    "operations.sent.email": {"en": {"one": "Sent to {n} person", "other": "Sent to {n} people"}, "ko": "{n}명에게 보냈어요"},
    "operations.sent.calendar": {"en": "Made a calendar file: {path} (import it into your calendar)",
                                 "ko": "일정 파일을 만들었어요: {path} (캘린더로 가져오기)"},
    "operations.sent.survey": {"en": "Made a survey package: {path} (you send it out yourself)",
                               "ko": "설문 배포 파일을 만들었어요: {path} (배포는 직접 해 주세요)"},
    "operations.sent.contact": {"en": "Saved contact C-{id}", "ko": "연락처 C-{id|을} 저장했어요"},
    "operations.sent.interaction": {"en": "Logged conversation I-{id}", "ko": "대화 기록 I-{id|을} 남겼어요"},
    "operations.taken_back": {"en": "The job was taken back just before sending, so nothing was sent",
                              "ko": "보내기 직전에 작업이 회수돼서 보내지 않았어요"},
    "operations.check_failed": {"en": "Checking the job just before sending failed, so nothing was sent ({error})",
                                "ko": "보내기 직전에 작업을 확인하지 못해서 보내지 않았어요({error})"},

    "operations.backup_failed": {
        "en": "Couldn't make the database backup ({error}). Check that Docker Desktop and the postgres container are running.",
        "ko": "데이터베이스 백업을 만들지 못했어요({error}). Docker Desktop과 postgres 컨테이너가 켜져 있는지 확인해 주세요."},
    "operations.backup_exit": {"en": "Couldn't make the database backup (pg_dump exited with {code}): {why}",
                               "ko": "데이터베이스 백업을 만들지 못했어요(pg_dump 종료 코드 {code}): {why}"},
    "operations.retention": {"en": {"one": "Removed {n} person from B-{id} after their retention period ended",
                                    "other": "Removed {n} people from B-{id} after their retention period ended"},
                             "ko": "B-{id}에서 보관 기간이 지난 사람 {n}명을 지웠어요"},
}
