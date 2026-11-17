"""What this PC needs (`services/system_health.py`): each check's name, the sentence for each way it can fail and the
way out, and the lines `ship doctor` and the startup report print. A problem's `<code>.problem` and `<code>.fix` are
a pair; `{what}` in either is the check's name."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    "system.docker.title": {"en": "Docker", "ko": "Docker"},
    "system.image.title": {"en": "Sandbox image", "ko": "샌드박스 이미지"},
    "system.egress.title": {"en": "Sandbox proxy", "ko": "샌드박스 프록시"},
    "system.claude.title": {"en": "Claude Code", "ko": "Claude Code"},
    "system.database.title": {"en": "Database", "ko": "데이터베이스"},
    "system.worker.title": {"en": "Background process", "ko": "백그라운드 프로세스"},

    "system.docker.down.problem": {
        "en": "Docker isn't running, so builds, tests and reading the product's code can't start.",
        "ko": "Docker가 꺼져 있어서 개발, 테스트, 제품 코드 읽기를 시작할 수 없어요."},
    "system.docker.down.fix": {
        "en": "Open Docker Desktop and wait until it says it is running.",
        "ko": "Docker Desktop을 열고 실행 중이라고 뜰 때까지 기다려 주세요."},
    "system.image.missing.problem": {
        "en": "The sandbox image {image} is missing, so the engineer can't start.",
        "ko": "샌드박스 이미지({image})가 없어서 엔지니어가 시작할 수 없어요."},
    "system.image.missing.fix": {
        "en": "Run `uv run ship sandbox build engineer`.",
        "ko": "`uv run ship sandbox build engineer`를 실행해 주세요."},
    "system.egress.down.problem": {
        "en": "The {proxy} proxy, the sandbox's only way out, is off, so reading the product's code and building stop "
              "within seconds.",
        "ko": "샌드박스가 밖으로 나가는 유일한 길인 {proxy} 프록시가 꺼져 있어서 제품 코드 읽기와 개발이 바로 멈춰요."},
    "system.egress.down.fix": {
        "en": "Run `docker compose up -d egress`.",
        "ko": "`docker compose up -d egress`를 실행해 주세요."},
    "system.claude.missing.problem": {
        "en": "Claude Code isn't installed on this PC, or it isn't on the PATH, so no agent can run.",
        "ko": "이 PC에 Claude Code가 없거나 PATH에 없어서 에이전트가 하나도 일할 수 없어요."},
    "system.claude.missing.fix": {
        "en": "Run `npm i -g @anthropic-ai/claude-code`. If it lives somewhere else, set SHIP_CLAUDE_BIN in .env.",
        "ko": "`npm i -g @anthropic-ai/claude-code`를 실행해 주세요. 다른 곳에 있으면 .env의 SHIP_CLAUDE_BIN에 "
              "적어 주세요."},
    "system.claude.login.problem": {
        "en": "This PC isn't signed in to Claude ({path} is missing), so the engineer sandbox has no login to use.",
        "ko": "이 PC에서 Claude에 로그인돼 있지 않아서({path} 없음) 엔지니어 샌드박스가 쓸 로그인이 없어요."},
    "system.claude.login.fix": {
        "en": "Run `claude` in a terminal and sign in with your subscription account.",
        "ko": "터미널에서 `claude`를 실행해 구독 계정으로 로그인해 주세요."},
    "system.database.down.problem": {
        "en": "The database doesn't answer, so nothing can be saved or loaded.",
        "ko": "데이터베이스가 응답하지 않아서 저장도 불러오기도 할 수 없어요."},
    "system.database.down.fix": {
        "en": "Run `docker compose up -d`, then check again with `uv run ship doctor`.",
        "ko": "`docker compose up -d`를 실행한 뒤 `uv run ship doctor`로 다시 확인해 주세요."},
    "system.database.behind.problem": {
        "en": "The database is not at this code's version (database {db}, code {code}), so parts of the app can fail.",
        "ko": "데이터베이스가 이 코드의 버전과 달라서(데이터베이스 {db}, 코드 {code}) 일부 기능이 실패할 수 있어요."},
    "system.database.behind.fix": {
        "en": "Stop the background process, then run `uv run alembic upgrade head`.",
        "ko": "백그라운드 프로세스를 멈춘 뒤 `uv run alembic upgrade head`를 실행해 주세요."},
    "system.worker.note.waiting": {
        "en": {"one": "It reports in, which shows only that it is running. {n} job is waiting to start, for {minutes} min.",
               "other": "It reports in, which shows only that it is running. {n} jobs are waiting to start, the oldest "
                        "for {minutes} min."},
        "ko": "신호를 보내고 있어요. 켜져 있다는 뜻일 뿐이에요. 시작을 기다리는 작업이 {n}개 있고, 가장 오래된 것은 "
              "{minutes}분째예요."},
    "system.worker.note.idle": {
        "en": "It reports in, which shows only that it is running. No job is waiting to start.",
        "ko": "신호를 보내고 있어요. 켜져 있다는 뜻일 뿐이에요. 시작을 기다리는 작업은 없어요."},
    "system.worker.note.last": {
        "en": "The last job finished {minutes} min ago.", "ko": "마지막 작업은 {minutes}분 전에 끝났어요."},
    "system.worker.note.never": {
        "en": "No job has finished yet.", "ko": "아직 끝난 작업이 없어요."},
    "system.worker.down.problem": {
        "en": "The background process isn't running, so nothing can start.",
        "ko": "백그라운드 프로세스가 꺼져 있어서 아무 일도 시작할 수 없어요."},
    "system.worker.down.fix": {
        "en": "Run `uv run ship supervise` in a terminal. It runs the background process and the console together.",
        "ko": "터미널에서 `uv run ship supervise`를 실행해 주세요. 백그라운드 프로세스와 콘솔을 함께 켜 둬요."},

    "system.slow.problem": {
        "en": "{what} didn't answer within {s} seconds.",
        "ko": "{what} 점검이 {s}초 안에 끝나지 않았어요."},
    "system.slow.fix": {
        "en": "Check that it is running and responding, then run `uv run ship doctor` again.",
        "ko": "켜져 있고 응답하는지 확인한 뒤 `uv run ship doctor`를 다시 실행해 주세요."},
    "system.error.problem": {
        "en": "Couldn't check {what} ({error}).",
        "ko": "{what} 점검을 하지 못했어요({error})."},
    "system.error.fix": {
        "en": "Run `uv run ship doctor` and read the newest lines of var/logs/api.log.",
        "ko": "`uv run ship doctor`를 실행하고 var/logs/api.log의 마지막 줄을 읽어 주세요."},

    "system.cli.header": {"en": "Problems on this PC:", "ko": "이 PC에서 고칠 것이 있어요:"},
    "system.cli.fix": {"en": "Fix:", "ko": "해결:"},
    "system.cli.skipped": {"en": "not checked, {needs} comes first", "ko": "확인하지 않았어요. 먼저 {needs} 문제를 해결해 주세요."},
    "system.cli.all_ok": {"en": "Everything the company needs on this PC is ready.",
                          "ko": "이 PC에서 회사가 일할 준비가 다 됐어요."},
}
