# Architecture — Just Ship It v2 (구현 기준)

원 설계(Architecture Proposal v1.0 + v2)를 따르되, **구독형 CLI(Claude Max의 `claude -p`)만 사용**하는 제약과
Windows 환경, pstack의 실제 동작을 검증한 결과에 맞춰 조정했다. 아래는 조정 사항과 그 근거다.

## 1. 조정된 결정

### 1.1 LLM 런타임 = Claude 구독 CLI 어댑터 (API 대신)
LangGraph 노드는 LLM API 대신 `runtime/` 어댑터로 `claude -p`를 하위 프로세스로 호출한다. 회사 전체가 Claude 구독 하나로
돈다. 다른 공급자로 대체 실행하지 않는다.

| | Claude (`claude -p`) |
|---|---|
| 구조화 출력 | `--json-schema` → `structured_output` |
| 격리 | `--system-prompt-file --setting-sources "" --strict-mcp-config --disable-slash-commands --tools <허용목록>` |
| 웹 | `WebSearch`, `WebFetch` |
| 사용량 | `usage`, `total_cost_usd`, `modelUsage`(실제로 답한 모델) |

**측정치**: 격리 플래그 없이 `claude -p` 1회 = 122k 토큰(사용자 MCP/스킬 정의 포함) → 격리 후 1~2k 토큰.
`contracts/schema.py`가 Pydantic 스키마를 엄격한 JSON Schema로 변환한다
(모든 속성 required, `additionalProperties:false`, `$ref` 인라인, 자유형 dict 금지).

사용량 한도에 닿으면 `runtime/quota.py`가 Claude를 초기화 시각까지 사용불가로 표시하고(`var/state/` 파일로 워커 간 공유) 호출은 그
시각까지 `AgentFailed`로 끝난다. 다른 공급자로 넘기지 않는다.

### 1.2 교차 검증 = 별도 검토자
검토는 작성자와 다른 역할이 새 컨텍스트에서 따로 호출하는 것이다. 모델이 같은지는 독립성의 조건이 아니다. 구독이나 모델을
하나만 쓰는 사용자도 같은 규칙으로 쓸 수 있어야 하기 때문이다. `agents/independence.py`의
`assess(author_role, author_served, reviewer_role, reviewer_served)`가 검토자 역할이 작성자 역할과 다르면 독립으로 본다.
독립이면 서버 출처확인(`apply_review`의 URL·인용 검사)을 통과한 `supported` 판정을 `verified_fact`로 승격한다.

승격을 막는 경우는 실제 별도 검토가 없을 때뿐이다. 검토 호출이 실패하면 업무는 `unreviewed`로 끝나고 아무것도 승격되지 않는다.
검토자 역할이 작성자 역할과 같으면 `independence: not independent: ...`로 기록하고 승격하지 않는다.

실제로 호출을 처리한 모델은 정보로만 남긴다. `executor.last_served()`가 마지막 호출의 `Served(provider, model)`을 돌려주고
(`model`은 CLI가 보고한 `modelUsage` 중 비용이 가장 큰 모델), 검토 기록의 `independence`는
`review by risk (claude/opus) of research (claude/sonnet)` 형식이다. 엔지니어링 리뷰도 같은 형식으로 단계에 남긴다.

배치는 `staffing` 패키지가 정한다([설계](design/staffing-structure.md)). 업무마다 규칙(`staffing.place`)이 다른 역할이
확인할지 정하고, 그 이유를 `crews`에 남긴다. 확인하는 역할은 `staffing.verifier_role`이 고른다. Risk가 모두를
검토하고 Risk의 업무는 strategy가 검토한다.
기본 배치는 품질을 위한 것이다. 작성자(research/strategy/marketing/product/finance/analyst/skill_builder) = sonnet,
Risk = opus, 엔지니어 = opus, 엔지니어링 리뷰어 = sonnet. 같은 모델이 자기 오류를 반복 승인하는 상관 오류를 줄이는 효과는
모델이 다를 때 더 크지만, 그것을 승격의 조건으로 삼지 않는다.

**실측 사례**: 웹 검색을 끈 모델이 기억으로 출처 URL을 생성함 → 서버 측 URL 검증을 도입(1.4).

### 1.3 작업 큐 = PostgreSQL 트랜잭셔널 아웃박스
원안의 Redis Queue 대신 `jobs` 테이블(`FOR UPDATE SKIP LOCKED`). 승인 처리와 후속 작업 등록이 **같은 트랜잭션**에서
일어나므로 "승인됐는데 재개가 안 됨 / 두 번 재개됨"이 구조적으로 불가능하다. 멱등 키와 재시도를 포함한다. 워커는
실행 중인 작업의 리스(`locked_at`)를 heartbeat로 갱신하고, 리스가 `operations.job_lease_s` 넘게 끊긴 작업만 회수한다.
리스는 claim마다 새로 받는 `lease_token`으로 식별하고 시각은 DB 시계로 기록한다. 리스가 끊겼다고 해서 그 워커가
죽은 것은 아니다(호스트 절전, DB 장애 뒤에도 핸들러는 계속 일한다). 그래서 리스를 잃은 워커는 다음 부작용 경계에서
멈춘다. 모든 모델 호출(`run_agent`), 그래프 노드 시작, 개발 단계(`_step`/`_fail`) 앞에서 리스를 확인하고, 잃었으면
`LeaseLost`로 되감아 finish도 FAILED 표시도 하지 않는다(그 행들은 새 소유자의 것이다). push·머지·외부 실행 직전에는
플래그가 아니라 claim 잠금 아래에서 행 자체를 다시 읽는다(`confirm_lease`). 이 읽기는 행 잠금을 쥐지 않은 채로 한다. 머지와 외부 실행은 Grant 행을 잠그기 전에 읽고, Grant를 소모한 뒤 다시 확인한다. 행이 우리 것임을 마지막으로 확인한 시각
(`Lease.confirmed_at`: heartbeat 갱신 또는 confirm)이 `job_lease_s`보다 오래됐으면(호스트 절전으로 heartbeat 스레드도
잠든 경우) 일반 확인도 플래그를 믿지 않고 행을 다시 읽는다. 이 읽기는 claim 잠금을 잡지 않는다. 호출자가 회수가 기다리는 행 잠금을 쥐고 있을 수 있기 때문이다. 잠금 순서는 하나다(`queue.take_claim_lock`): claim 잠금이 항상 Project·DevJob·Spec·BudgetMonth 행 잠금보다 먼저다(BudgetMonth는 고아 예약 해제가 claim 잠금 아래에서 잠근다). 그런 행을 쥔 코드는 다른 연결에서도 claim 잠금을 잡지 않는다. 두 연결에 걸친 기다림은 PostgreSQL이 교착으로 보지 못해 영원히 멈춘다. 리스를 잃은 뒤의 쓰기는 커밋되지 않는다.
`session_scope`가 커밋 직전에 리스를 확인하고, 레지스트리 git 커밋도 같다. 예외는 예산 정산(`budget.settle`), 모델
호출 기록, Tool Gateway의 결과 기록(`done`·`failed`·`not_sent`)이다. 모두 일어난 사실이다. 회수된 작업은 행이 아니라 `lock_key`가 `held_until`(DB 시계, 회수 시각 +
`queue.rerun_delay_s`)까지 잡혀 있다. 그동안 같은 키의 다른 작업(명세 작성, 재평가 등), 오너의 다시 실행(`retry_project`),
새 개발 시도(`retry_dev`)는 모두 그 시각을 이름 붙여 거부되고, claim도 그 키를 건너뛴다. 워커가 없으면 오너의 다시 실행이 회수를 하는데, 그 회수는 자기 트랜잭션으로 먼저 커밋하므로(`queue.settle_lapsed`) 거부가 되돌리지 않고 몇 번을 눌러도 같은 시각을 말한다. `rerun_delay_s`는 리스 확인
없이 지나가는 가장 긴 구간이다: 모델 호출 한 번(재시도 포함), 테스트 실행 한 번, 분석 컨테이너 실행 한 번 중 큰 쪽.
그 밖의 구간(다운로드는 청크마다, 공급자 슬롯 대기는 틱마다, 그래프 노드와 개발 단계는 시작마다)은 리스를 확인한다.
pid가 사라진 것만으로는 기다린다. SIGKILL·OOM으로 죽은 워커가 시작한 `claude -p`와 테스트 컨테이너는
계속 돌 수 있기 때문이다. 기다리는 이유와 시간은 작업의 `last_error`에 남는다. 예외는 워커 틱이 증명한 경우 하나다
(`runner._gone_holders`, 컨테이너 격리일 때만): 하트비트 두 번 동안 보고가 없고, 같은 호스트에서 pid가 없고(Windows는
`OpenProcess`로 확인), 그 워커 라벨의 컨테이너를 모두 지운 뒤 다시 조회해 하나도 없으면, 남을 수 있는 것은 읽기만 하는
`claude -p`뿐이므로 리스 만료를 기다리지 않고 바로 회수하고 hold 없이 다시 실행한다(`queue.reclaim`의 `gone`). 배포가
워커 프로세스 트리를 죽여도 개발 작업은 몇 분 안에 이어진다.
회수된 개발 작업(`dev_job`, `dev_resume`)과 수정 라운드(`dev_fix`)는 실패로 끝내지 않고 시도 3번까지 큐로 돌아가, 멈춘
단계부터 이어 간다(`worker._resume_at`, `revise.run`): 구현은 작업 트리와 `[task k/M]` 커밋에서 다음 작업부터(수정 라운드는
`-r<n>` 트리에 남은 커밋 위에서 `.ship/fix.md`의 남은 수정부터), 테스트·리뷰는 다시, 전달은 리뷰한 커밋으로. 같은 라운드를
이어 갈 뿐 새 라운드가 아니므로 자동 수정 횟수를 쓰지 않고, 비용은 예전처럼 호출마다 프로젝트 예산이 막는다. 호스트 격리에서는 죽은 워커의 엔지니어가
같은 트리에 계속 쓸 수 있으므로 예전처럼 멈춘다(빌드는 실패, 수정 라운드는 전달된 상태로 "다시 수정").
작업 중간에 사용 한도(`MustWait`: 구독 한도, 하루·주간 상한)에 걸리면 실패로 끝내지 않는다. 작업은 그 단계에 머물며
`build.step.usage_paused` 줄을 남기고(`worker.pause`), 큐는 다른 작업처럼 풀리는 시각까지 미룬다(`queue.defer`). 다시 돌면
멈춘 단계부터 이어 간다. CLI 호출이 이미 답했으므로 그 실행에서 아직 도는 것이 없어, 호스트 격리에서도 이어 간다
(`worker.carries_on`: 마지막 줄이 사용 한도 멈춤이면). 보드는 원문 대신 카탈로그 문장("사용 한도에 걸려서 {시각}에 하던 곳부터
이어서 만들어요")을 보여 준다. 미룸 횟수는 따로 세지 않는다: 작업 중간의 미룸도 시작 전 미룸과 같이 그 큐 작업의
`deferrals`로 세고 `max_deferrals`(48)를 넘으면 실패한다. 이 상한은 끝나지 않는 대기(잘못 읽은 리셋 시각, 30초마다 다시 미룸)만
막기 위한 것이라 실제 한도 대기(몇 시간)로는 닿지 않는다. 미룸은 시도를 돌려주므로 재시작 시도 3번과 미룸은 서로를 쓰지 않는다.
엔지니어가 CPO에게 물어보는 중에 멈춘 빌드는 오너가 답한 질문이 없으면 다시 물어본다(`worker._owner_answered`).
같은 `lock_key`(프로젝트 실행, 개발 작업, 외부 실행은 요청마다 `action:<요청 id>`)를 가진 작업은 claim 단계에서 워커 프로세스를 넘어 한 번에 하나만 돈다.
"처리 중"의 정의는 하나(`queue.in_flight`)다. 대기 중이거나 running인 작업은 리스가 끊겼어도 처리 중이며, 회수된 뒤에도
`held_until`까지는 처리 중이다. claim 잠금 아래의 회수가 queued나 failed로 바꾸고 그 시각이 지난 뒤에야 멈춤 경고와
다시 실행이 열린다. 예산 예약 해제와 컨테이너 정리는 `held_until`을 기다리지 않는다. 회수가 옛 소유자를 지우는 순간 그
워커는 살아 있는 리스의 소유자가 아니므로 바로 열린다. 이것이 맞다. 옛 워커가 남긴 컨테이너는 다시 실행 전에 치우는 편이
낫고, 늦게 깨어난 옛 소유자의 정산은 예약을 0 아래로 내리지 않고 쓴 금액만 기록한다. 기다리는 동안 홈 화면은 `job_waiting` 경고로 왜 기다리는지와 언제까지인지
(끝나는 시각)를 보여 주고, 그 시각이 지나면 실시간 피드가 경고를 거둔다. 워커는
tick마다 먼저 heartbeat를 보내고, 끊긴 리스를 회수한 뒤(슬롯이 모두 차 있어도), running 작업이 소유하지 않은 예산 예약과
엔지니어·테스트 컨테이너(`ship.owner` 라벨)를 정리한다. 테스트 컨테이너(`ship-test-*`)도 엔지니어 컨테이너와 같은
소유자·배포 라벨을 달고, 테스트가 시간을 넘기면 docker 클라이언트만이 아니라 컨테이너도 지운다. CLI 호출(`claude -p`)은
자기 프로세스 그룹(POSIX는 새 세션)에서 돈다. 콘솔의 Ctrl+C는 워커에게 실행 중인 작업을 마치라는 뜻이라
호출에는 가지 않고, 시간 초과는 CLI가 시작한 하위 프로세스까지 멈춘다(Windows는 `taskkill /T`). 워커가 강제로 죽으면
호출은 스스로 끝날 때까지 남는다. 호스트 프로세스 정리기는 없고, 그동안 같은 작업이 다시 돌지 않게 하는 것은 회수 뒤의
대기(`rerun_delay_s`)다. 컨테이너의 `ship.deployment` 라벨은 DB 자신의 식별자
(`pg_control_system()`+DB 이름)에서 오므로 같은 DB를 다른 주소로 적어도 한 배포다. DB가 답하지 않는 동안(접속 제한 2초, psycopg가 받는 가장 짧은 값)
에는 id가 없고, 추측한 라벨로 컨테이너를 시작하는 대신 그 호출이 실패하고 다음에 다시 묻는다. 개발 작업 다시 시도(`retry_dev`)는
먼저 끊긴 리스를 회수하고, 아직 running이거나 `held_until`이 지나지 않은 작업이 있으면 그 시각과 함께 409로 거부한다.
오너의 예산 인상 뒤 재개(`set_budget(resume_run=True)`)도 회수와 같은 순서(claim 잠금, 그다음 프로젝트 행)로 잠근다. 인상은 실행 작업이 무엇을 하든 남는다. 대기 중인 실행(`run_project`)이나 다시 실행(`retry_project`) 작업이 있으면 그 작업이 새 예산으로 이어 가고(대기 중인 `resume_project`는 실패한 프로젝트를 건너뛰므로 그 뒤에 다시 실행을 예약한다), 회수된 작업이 기다리는 중이면 그 끝 시각에 다시 실행을 예약하고, 워커가 아직 처리 중이면 예약하지 않는다. 어느 쪽인지는 `budget_changes`의 `resume`에 남고 콘솔이 그대로 보여 준다.
SIGTERM/SIGINT를 받으면 새 작업을 받지 않고, 실행 중인 작업의 CLI 호출(프로세스 트리)과 컨테이너를 끝낸다. 각 핸들러는
다음 리스 확인에서 `Stopping`(`LeaseLost`의 하위 형식이라 커넥터는 Grant를 돌려준다)으로 되감고, 워커가 그 작업을 대기열로
바로 돌려놓는다(`queue.release`: held_until 없이, 실행은 retry_project로 체크포인트부터). 회수와 달리 이 보유자는 끝났음을 안다.
핸들러가 돌아온 뒤에만 돌려놓고, 핸들러가 시작한 하위 작업은 모두 끝날 때까지 기다리기 때문이다. 마지막 리스 확인을 지난 작업은
그대로 마친다. 두 번째 신호는 즉시 끝내고 작업을 리스에 맡긴다. 예산 예약은 행(`budget_reservations`)마다 그 예약을 잡은 작업
리스를 기록하고, 정리기는 리스가 사라진 예약만 푼다(다른 작업이 돌고 있어도). 작업 밖에서 잡은 예약은 살아 있는 리스가 하나도
없을 때 푼다.
같은 heartbeat가 `workers.seen_at`을 갱신하므로, `job_lease_s` 동안 보고한 워커가 없으면 홈 경고에 "워커 응답 없음"이
뜬다. 실행·개발 작업이 재시도를 다 쓰고 실패하면(회수 포함) 큐가 해당 프로젝트나 개발 작업을 FAILED로 바꿔 콘솔의
다시 실행 버튼이 동작하게 한다. 그 밖의 실패한 작업은 오너가 다시 시도하거나 닫을(`alert_dismissed_at`) 때까지 경고에
남는다. 외부 실행(`execute_action`)과 머지(`dev_merge`)는 새 오너 결정이 필요해 다시 시도를 거부한다. 머지 작업이
실패하면 개발 작업도 FAILED로 바꿔, 오너가 새 시도를 열거나 직접 머지할 수 있게 한다.
슬롯용 DB 연결이 안 되거나 할당량 표시 파일을 쓸 수 없으면 프로세스별 상태로 대체되고, 전환과 복구가 한 번씩 로그에
남는다. 실시간 이벤트(웹 콘솔)와 다중 프로세스 세마포어도 Postgres가 맡는다(NOTIFY/LISTEN, advisory lock).
Redis Queue를 쓰지 않은 데는 Windows에서 RQ가 fork 의존으로 동작하지 않는 것도 이유다.

### 1.4 근거 원장 (evidence ledger)
- 에이전트 출력의 `verified_fact`/`calculation`은 서버가 `sourced_claim`으로 강등, URL 없는 `sourced_claim`은 `hypothesis`로 강등.
- 서버가 모든 출처 URL에 직접 접속: `url_ok` / `quote_found`(인용문 실재) / `url_unreachable` / `unverifiable`(봇 차단).
- 독립 검토자의 claim check(supported/weak/contradicted)와 서버 확인이 **모두** 통과해야 `verified_fact`로 승격.
- 여러 에이전트가 반복해도 승격되지 않는다. 재무 엔진 결과만 `calculation`.

### 1.5 pstack 연결
pstack은 Lauren Tan의 스킬 묶음이며, Claude Code 플러그인 `pstack@pstack-claude`(michael-denyer/pstack-claude)로
설치한다. 스킬 이름은 `pstack:<이름>` 이름공간이다. `no-session/pstack`(gstack 포크, `pstack-*` 이름)은 pstack이 아니다.
- 엔지니어 이미지는 플러그인 저장소를 고정 커밋(`docker/engineer.Dockerfile`의 `PSTACK_REF`, 0.9.114)으로 clone한 뒤
  로컬 마켓플레이스로 설치한다. `marketplace add owner/repo#ref`는 브랜치·태그만 받고 0.9.114에는 태그가 없다.
  설치가 agent 사용자 설정에 `enabledPlugins`를 쓰고, 엔지니어링 호출은 그 설정을 읽는다.
- 개발 워커 단계: 계획 검토는 `pstack:architect`, 자체 리뷰는 `pstack:thermo-nuclear-review`, 독립 리뷰는 별도 역할인 `eng_reviewer`(기본 sonnet). 처리한 모델은 `review by eng_reviewer (...) of engineer (...)` 단계로 기록한다.
- 개발 워커는 `claude -p --permission-mode bypassPermissions`이지만 deny 규칙으로 `git push/remote/merge/rebase`,
  `gh`, `pstack:babysit`, `pstack:fix-ci`, `pstack:setup-pstack`을 차단 (deny는 bypass보다 우선 — 실측 확인).
  Skill deny는 Skill 도구 호출만 막는다. 프롬프트가 `/pstack:babysit`로 시작하면 스킬 본문이 펼쳐지므로,
  실제 방어선은 `gh`·`git push` deny와 자격증명 없는 컨테이너다. 워커 프롬프트는 `/`로 시작하지 않는다.
  push·PR 생성은 워커 코드가 승인 상태와 명세 SHA를 **실행 직전 재검증**한 뒤 수행. 머지는 strong_approve.
- 비대화형 규칙: 스킬이 질문하면 권장안 선택 + `decisions`에 기록.
- 화면이 있는 제품: 명세의 `design_brief`가 있으면 `handoff.workflow.screens`가 켜진다. 워커가 회사의 화면 기준
  (`prompts/engineering/product-ui.md`)을 `.ship/ui-standard.md`로 명세 옆에 두고, 계획·구현 단계가 DESIGN.md 토큰,
  모든 상태, `scripts/preview.sh`·`scripts/ui-states.txt`, chromium 캡처로 직접 보기를 요구한다. 수용 검토는 배달된
  브랜치를 엔지니어 이미지에서 빌드해 찍고(`worker.capture_screens`), CPO가 읽기 전용 작업 공간에서 캡처를 열어 판정한다.

### 1.6 승인 게이트와 사이클 단계 (2026-11-06)

오너가 승인하고, 승인한 것은 스스로 돈다. 사용량을 쓰거나 오너의 할 일을 만드는 것은 그 승인 없이는 돌지 않는다.
설계는 `docs/design/cycle-stages/` (synthesis.md가 우선)에 있다.

- **목록 하나.** `services/gate.py`의 `POLICY`가 작업 종류마다 왜 돌아도 되는지 적는다. 행이 없는 종류는 거부된다:
  `queue.enqueue`가 `UndeclaredJobKind`를 던지고, 워커는 시작하지 않고(`gate.check_handlers`), claim은 아무것도 고르지
  않는다. `STAGE_WORK`는 돌고 있는 단계 안에서만, `STAGE_RECURRING`은 그 단계가 `allows_kinds`에 적었을 때만, `ASKED`와
  `by_owner=True`는 오너가 누른 것, `CYCLE`은 관문을 여는 CEO 작업, `FREE`는 쓰지 않는 일이다.
- **보장은 claim.** 모든 작업 행은 `enqueue`가 `jobs.gate`(`none`/`direct`/`stage`, 기본값 없음)와 단계를 찍어 만든다.
  `stage` 작업은 그 단계가 `running`일 때만 claim된다. 단계가 없으면 `gated`로 기다렸다가 오너가 시작한 다음 단계에
  합류한다(`gated`도 `in_flight`다). 돌던 작업은 단계가 멈추면 다음 리스 확인에서 멈춘다(`queue._check_stage`).
- **쓰는 지점의 마지막 선.** `executor.run_agent`는 FREE 작업의 호출과 작업 밖의 호출을 거부한다(CLI·테스트는
  `gate.asked`). 할 일은 `owner_tasks._insert` 한 곳에서만 만들고, 오너의 클릭이 아니면 돌고 있는 단계가
  `allows_owner_work`에 그 종류를 적었을 때만 만든다.
- **단계 기계.** `services/cycle.py`만 `cycles`·`cycle_stages`·`cycle_proposals`를 쓴다. 오너의 쓰기는 `cycle.decide`
  하나, 시스템의 움직임은 스케줄러가 매 틱 부르는 `cycle.tick` 하나다. 대시보드는 `services/cycle_board.py`
  (`GET /businesses/{id}/cycle-board`)가, "누가 무엇을 하는지"는 `services/work_now.py`가 Overview와 함께 읽는다.

## 2. 계층

```
CLI / API (FastAPI)  ── Approval Center, 프로젝트, 근거, 개발 현황
      │
Job Worker (outbox)  ── run/resume/retry_project, write_spec, spec_approved, dev_job, acceptance, dev_merge
      │
LangGraph Company Graph (PostgresSaver, thread = project-<id>)
  plan → plan_gate → schedule ⇄ work(fan-out) → ceo_review → meeting | follow-up | decide → final_gate → report
      │
Agent Executor ── 프롬프트 합성(공통 원칙 + 역할) · 예산 선예약/정산 · 호출 원장 · 스키마 검증
      │
Runtime ── claude -p (isolated | engineering)
      │
Services ── policy · requests/grants · budget · tasks(DAG) · evidence · finance · registry(git) · queue
      │
PostgreSQL (업무·결정·근거·승인·원장·체크포인트)   Git registry (문서·명세 원본)   products/* (코드)
```

체크포인트는 실행 위치만 기록한다(그래프 상태는 project_id, batch 정도). 업무·결정·근거는 정규화 테이블에 있다.
실패한 실행은 `ship project retry`가 마지막 체크포인트부터 이어간다(완료된 노드는 재실행하지 않음).

## 3. 기술 계약 (교체 가능성의 경계)

| 계약 | 위치 | 핵심 |
|---|---|---|
| Agent Request Protocol | `contracts/requests.py` | 8개 유형, blocking, action_kind, 정책 모드는 서버가 결정, 응답은 범위 축소만 가능 |
| Work/Evidence | `contracts/work.py` | TaskStatus 상태머신, AgentResult, Review(claim checks, contested) |
| Spec→Code Handoff | `contracts/handoff.py` | handoff.yaml 스키마, commit_sha 고정, DevJob 상태 |
| Skill Lifecycle | `contracts/skills.py` | 3종(knowledge/workflow/executable), 상태, 활성화 action 매핑 |

## 4. 데이터 모델

projects · tasks · llm_calls(task_runs) · evidence(pgvector 384) · artifacts · meetings · decisions · agent_requests ·
grants · agent_messages · specs · dev_jobs · skills · jobs · budget_months (+ LangGraph checkpoint 테이블)

## 5. Phase B·C 구성 요소

| 구성 | 위치 | 핵심 설계 |
|---|---|---|
| 실시간 이벤트 | `services/events.py`, `/api/events` | SQLAlchemy after_flush 훅이 변경 행을 모으고, 최상위 트랜잭션이 커밋되면 `pg_notify('ship_events', …)`로 보냄 → `/api/events`가 `LISTEN`해 SSE로 전달. NOTIFY는 DB별이라 테스트 DB의 이벤트는 오너 콘솔에 닿지 않음. 8000바이트를 넘는 이벤트는 `data`·`summary`·`title`을 빼고 보냄. 코드 곳곳에 이벤트 호출 불필요 |
| 웹 콘솔 | `web/` | React+Vite, 마크다운은 DOMPurify로 정화(웹에서 온 텍스트 포함) |
| Skill Factory | `capability/factory.py` | 공백 집계 → 제안 → 패키지 → 별도 역할(risk)의 안전 검토 → 원장 재실행 A/B → 정책 승인 → 주입. 스킬은 권한을 바꿀 수 없고 스크립트는 실행되지 않음 |
| 회사 기억 | `services/memory.py`, `memory_items` | 프로젝트 간 재조사 방지. 한 프로젝트의 에이전트가 쓴 글이 이후 모든 프로젝트의 프롬프트에 들어가는 길이므로 두 가지만 기억하고 꺼낸다: 서버의 출처 확인을 통과한 뒤 별도 역할의 검토자가 지지한 근거(`verified_fact` + `supported`, `evidence.apply_review`만 만든다)와 오너가 카드에서 승인한 결정(`approval_request_id`의 요청이 `APPROVED`). 링크만 열리는 주장(`sourced_claim` + `url_ok`·`quote_found`), 작성자 스크립트만 재현된 값(`reproduced`), 에이전트의 가정으로 낸 재무 계산(`calculation`), 승인이 필요 없었거나 규칙이 자동 승인한 결정은 기억하지 않는다. 꺼낼 때 근거·결정 행의 지금 상태를 다시 보므로, 예전 규칙으로 저장된 행이나 뒤에 검토·답변이 뒤집은 행은 지우지 않고 꺼내지만 않는다. 꺼낸 줄은 600자로 잘라 데이터 울타리 안에 넣는다. 최종 보고서 때 한 번 색인하므로 그 뒤에 승인된 결정은 기억되지 않는다 |
| 동시성 | `runtime/slots.py` | 슬롯 하나가 전용 연결의 세션 advisory lock(`pg_try_advisory_lock`, 기다리지 않음, 트랜잭션 밖). 프로세스가 죽으면 연결과 함께 풀림. DB에 닿지 못하면 프로세스 내 세마포어 |
| 설정 | `services/app_settings.py`, `services/secrets.py` | 오너가 설정 화면에서 넣는 값(구독 사용량, 커넥터 모드, 승인 규칙 덮어쓰기, 알림)은 `app_settings` 표에 섹션별 한 행으로 저장되고, YAML이 기본값, DB 행이 필드 단위로 덮어씀. `effective()`가 호출마다 읽어 재시작 없이 반영. 커넥터는 구현된 모드만, `deny` 행동은 `strong_approve`까지만 풀 수 있고 `strong_approve`는 `auto`가 될 수 없음. 연결 정보는 `var/secrets/<이름>`에 오너 전용 파일로 두고 API는 설정 여부와 끝 4자리만 돌려줌 |
| 사업 문서 | `services/doc_extract.py`, `services/documents.py`, `api/documents.py`, `documents` | 오너가 넣은 기획서·발표 자료(PDF, DOCX, PPTX, 마크다운, 텍스트). 형식은 확장자와 파일 앞 바이트가 함께 맞아야 받고, DOCX·PPTX는 압축 목록의 개수와 풀린 크기를 먼저 본다. 글자는 올릴 때 한 번 읽어 `documents.parts`(쪽마다 한 칸)에 두고, 원본은 `var/documents/<sha256>`에 오너 전용으로 둔다. 같은 바이트는 다시 읽지 않는다. 시작 대화의 CEO, 아이디어 평가단, 이후 프로젝트가 데이터 울타리 안에서 읽고, 파일에서 따온 인용은 붙여 넣은 메모처럼 오너의 말(`doc:<id>:p<쪽>`)로 친다. 글자 층이 없는 스캔본은 받지 않는다 |
| 관측성 | `services/tracing.py` | OTel GenAI span, Langfuse OTLP 호환, 기본 비활성 |
| 스케줄러 | `operations/scheduler.py` | 회사 상태에서 일정 파생(sync) → tick이 멱등 키로 잡 등록 |
| Tool Gateway | `operations/gateway.py` | 작업 lease 확인 → 승인 상태 재확인, Grant 소모, 멱등 원장 `started`를 짧은 한 트랜잭션으로 → lease 재확인 → 커넥터 → 결과 기록. 외부 I/O 전에 멈춘 실패(`ConnectorError`: 페이로드·동의·설정 검사), Grant를 소모하는 동안 작업이 회수된 경우, 소모 뒤 lease 재확인이 DB 오류로 실패한 경우만 Grant를 돌려주고(`not_sent`), 실패 알림의 '다시 시도'가 같은 승인으로 한 번 더 실행함(오너가 '다시 요청'한 뒤나, 쓸 수 있는 Grant가 남지 않았으면(만료·사용 횟수 소진) 불가하고 알림이 '다시 요청'을 안내). 그 밖의 실패(SMTP·소켓·OS 오류, 중단)는 보냈을 수 있으므로 원장이 같은 요청의 다음 호출을 막음(최대 한 번). 오너는 실패 알림의 '다시 요청'으로 같은 동작·페이로드의 새 요청을 만들고(회수된 작업의 옛 소유자가 아직 보내는 중일 수 있으면 `held_until`까지 그 시각과 함께 거부), 새 요청은 일반 정책대로 다시 승인받음(옛 Grant는 회수). 기본 outbox(아무것도 외부로 나가지 않음). live 이메일은 사람이 기록한 CRM 동의가 있는 수신자만 |
| KPI | `kpis`, `kpi_values`, `operations/jobs.kpi_review` | 보고서가 KPI 제안 → 등록 → 값 기록 → CEO 리뷰 → PROPOSAL |
| 개발 격리 | `engineering/worker.run_tests` | 테스트는 워커가 직접, 테스트 이미지 컨테이너(worktree만 마운트, 4GB/2CPU)에서. 의존성은 저장소의 `scripts/ci-setup.sh` |
| 엔지니어 컨테이너 | `runtime/claude_cli._run_in_container`, `sandbox/docker.py`, `docker/engineer.Dockerfile` | 엔지니어 호출마다 새 컨테이너(Claude Code + pstack 플러그인 고정 커밋, 비root, cap-drop ALL, no-new-privileges, 6GB/4CPU). 마운트는 그 작업의 독립 clone(`/work`)과 시스템 프롬프트(읽기 전용)뿐: 제품 저장소·호스트 홈·SSH 키·git 자격증명·docker 소켓 없음. 인증은 호스트 Claude Code 구독 로그인의 단기 접근 토큰만 환경변수 *이름*으로 전달(갱신 토큰은 호스트에만, 만료 임박 시 호스트 CLI가 갱신; 명령줄·로그에 값 없음, `runtime/host_auth.py`). 브랜치는 워커가 호스트에서 clone→제품 저장소로 fetch한 뒤 push/PR/머지. 금지 명령 규칙은 이중 방어로 유지 |
| 데이터 분석 | `analysis/runner.py`, `docker/analysis.Dockerfile` | analyst가 입력 URL과 스크립트를 제안 → 서버가 직접 다운로드(공개 IP의 http/https만, 리다이렉트마다 재검사, 크기 제한, sha256 기록) → `--network none`·읽기 전용 루트·입력 읽기 전용 컨테이너에서 실행 → 결과를 다음 단계에 제공(최대 6단계) → 최종 스크립트를 새 컨테이너에서 재실행해 결과 일치 시 근거 `reproduced`. `reproduced` + 별도 검토자의 지지일 때만 `verified_fact`. 스크립트·결과·입력 목록은 registry에 커밋 |

**알려진 한계: 웹을 여는 에이전트가 가진 것.** `web_fetch`를 가진 역할(`config/agents.yaml`의 `tools`가 정한다. 지금은 strategy, research, marketing, product, analyst, skill_builder)은 스스로 고른 주소를 연다. 읽은 페이지에 속아 넘어가면 프롬프트에 있는 글을 주소에 실어 밖으로 보낼 수 있다. 그래서 `context.project_brief(reader=…)`는 그런 역할에게 팀 채팅을 주지 않는다(`context.reads_the_web`, 규칙은 이 한 곳). 조사에 필요해서 그대로 주는 것: 승인된 계획, 오너가 올린 파일(8,000자까지), 고객 설명, 오너의 답변과 지시, 결정, 내 할 일 목록(사람에 관한 할 일의 이름 포함), 그리고 오너가 만날 사람들의 이름. 마지막은 marketing이 사람마다 메시지 요청을 올릴 때 `to`에 적어야 해서 남겼다. 회사 기억과 근거 목록도 그대로 읽는다. 계획된 해결은 서버가 대신 가져오는 것(서버 측 fetch)이고, 이 단계에서는 하지 않았다.

## 6. 운영 중 발견한 문제와 조치 (2026-10-29 첫 실전 실행)

| 관찰 | 조치 |
|---|---|
| 재작업 회차마다 같은 승인 요청이 중복 생성 | 같은 프로젝트의 대기 요청과 유형·action·제목 유사도로 병합 |
| Maya: 승인한 "내 Slack에서 15분 복붙 테스트"(REQ-58)가 내 할 일이 되지 않고, 결과를 적을 칸도, 결과가 다음 단계로 갈 길도 없었다. 36초 전에 승인한 명세 카드는 테스트가 남았다는 말이 없었다 (2026-11-05) | 오너가 직접 확인하고 결과를 알려 주는 일은 `owner.check` → 내 할 일 `check`. 결과가 필요한 종류는 표(`owner_tasks.RESULT_RULES`: 게시는 링크, 확인은 결과 메모 2만 자까지)가 정하고, 확인 결과는 명세 작성자에게 `OT-n`으로 자르지 않고 가며(`owner_trace.owner_sources`), 프롬프트에서는 데이터 울타리 안에 있고, 완료하면 계획을 다시 맞춘다(`realign_project`). 명세 카드는 아직 열린 확인을 읽는 시점에 찾아 한 문장으로 알려 준다 |
| 원두재고: 오너가 승인한 결정(DEC-3)의 '주문했어요'가 명세에서 말없이 빠지고, 만든 앱은 꾸밈없는 표 하나 (2026-11-04 비판 라운드) | `engineering/owner_trace.py`: 지시서 줄·지시·답변·결정을 자르지 않고 명세 작성자에게 주고(전에는 결정을 400자에서 잘랐다), 명세가 하나하나 넣음·뺌(이유)·제품 밖을 적게 한다. 적지 않은 것은 `missed`. 뺀 것과 빠진 것이 명세 승인 카드 맨 위에 보인다. 화면 기준과 캡처 검토는 1.5절 |
| 명세 카드가 오너 답변 REQ-53을 '다루지 않았어요'로 표시(명세는 REQ-53으로 적었는데 서버는 '답변 REQ-53'만 알았음). 명세를 쓴 31초 뒤 CEO가 정한 DEC-4의 조건 4개가 어느 명세에도 없는데 명세 카드는 '모두 넣었어요', 승인 뒤에도 DEC-4는 PENDING_APPROVAL, 최종 보고서는 네 가지를 모두 명세에 넣었다고 씀 (2026-11-05 L3) | `engineering/owner_trace.py`: 출처 id는 언어와 무관(`l-…`, `D-1`, `REQ-53`, `DEC-4`)하고 카드에 보이는 말은 `label`에만 둔다. 모르는 출처를 적은 항목은 경고 로그. 검토 중인 명세를 다시 쓰는 길은 없으므로(카드에 접힌 요청은 다음 버전 몫), 명세 카드가 오너를 기다리는 동안 결정·답변·지시가 새로 생기면 `restate()`가 그것을 '명세를 쓴 뒤에 정해져서 들어가지 않았어요'로 카드에 더하고 첫 줄을 다시 센다. 결정 행 상태는 카드를 따른다(`requests.follow_decision`). 최종 보고서 프롬프트는 명세의 추적표를 읽고 넣음으로 표시된 것만 '명세에 넣었다'고 쓴다 |
| 한 MVP에 승인 13건·명세 8개, 전단 하나에 카드 5장 (2026-11-04 비판 라운드) | `services/matters.py`: 요청마다 사안 키(`dev:b<사업>`, `post:…`, 에이전트가 적은 `replaces`)를 두고, 같은 사안의 새 요청은 열린 카드를 고치거나(같은 REQ), 승인으로 생긴 오너 할 일에 붙거나, 활동 기록이 된다. 보류·만료된 카드만 새 카드로 바뀐다. 개발 요청은 오너 카드 없이 명세 작성을 바로 시작하고(`dev.implementation_start: auto`), 오너는 명세 카드 하나로 개발을 정한다. 사업의 제품 하나에 진행 중 명세 하나, 저장소 이름은 첫 명세의 것을 유지. 재작업이 같은 action의 카드를 이전 회차의 열린 카드 수만큼 다시 올리면 이전 것을 닫는다. 오너가 할 일이 없는 요청(`owner_action: false`)은 활동 기록. `replaces`는 같은 작성자·업무의 카드만, 서버가 명세·머지·도전·결정에 묶은 카드는 못 바꾼다. 사안 배치는 사업마다 advisory 잠금 하나(`matters.lock`, 트랜잭션당 하나라 순서 교착 없음) 아래에서 사안의 최신 요청 행을 잠그고 읽는다. 오너의 응답(`respond`)은 요청 행만 잠그고 이 advisory 잠금을 잡지 않는다 |
| 승인한 글 하나에 열린 할 일 2개(Maya REQ-55·REQ-69), 오너가 아직 답하지 않은 질문을 CEO가 다시 묻고(Maya REQ-68, Daniel REQ-64) 결정 카드에 병합하지 못함 (2026-11-05) | 승인한 내용을 바꾸는 요청(다른 문구, 더 큰 비용)은 `Route.SUCCEEDS`: 새 카드가 `action_params["revises"]`에 고치는 승인을 적고 카드에 "고치는 카드"로 보인다. 그 카드를 승인하면 원래 승인의 열린 할 일이 새 요청으로 옮겨 가고(`owner_tasks.create_for_request`), 원래 요청은 `superseded_by`를 남긴다. 할 일이 이미 끝났으면 새 할 일이 생긴다. 반려된 고친 안은 사안의 최신 요청이 되지 않아 원래 승인이 그대로 기준이다. 대기 중인 카드는 `context.project_brief`에 제목·REQ 번호로 들어가 모든 CEO 호출이 읽고(다시 묻지 말고 대신 정하지 말 것, 고치려면 `replaces`), 승인함 정리 목록에는 결정 자신의 카드도 들어간다 |
| 검토자가 도구로 해결 불가능한 미충족(파일 다운로드·집계)에 계속 rework 지시 → 모든 업무 3회차 | 검토 규칙에 "(도구 한계)" 분류 추가 + 미충족 수가 줄지 않으면 재작업 중단(결정적 규칙) |
| Codex 검토 1회 입력 57만 토큰(근거마다 재검색) | 웹 재검색을 핵심 주장 5개로 제한 |
| Codex가 웹 검색 없이 기억으로 출처 URL 생성 | 서버측 URL·인용 검증, 검증 상태를 검토자에게 제공 |
| Alembic autogenerate가 LangGraph 체크포인트 테이블 삭제 마이그레이션 생성 → 실행 중 런 실패 | `include_object`로 모델 밖 테이블 제외. 체크포인트 유실 시 업무 DB로 재구성하는 경로 추가(설계 원칙 검증됨) |
| 빈 테스트 컨테이너에 의존성 없음 | `scripts/ci-setup.sh` 규약 (2차 시도에서 컨테이너 테스트 83개 통과) |
| **ChatGPT 구독 사용량 한도 소진**(약 3시간 운영 후). 검토 실패가 DONE으로 처리되어 미검토 결과가 통과처럼 보임 | `runtime/quota.py`: 한도 오류를 별도 분류(재시도 낭비 제거), 초기화 시각 파싱, provider를 Redis에 사용불가로 표시 → 다른 provider로 대체 실행. 대체로 같은 계열이 검토하면 `independence: degraded`로 표시하고 근거 승격 금지. 검토 실패는 `unreviewed`로 명시. 회의 참석자 실패는 회의 전체를 중단시키지 않음 |
| 에이전트가 정책에 없는 action 이름을 지어냄(`external.interview_recruit` 등) → 병합 실패, 커넥터 없는 승인 | 유효 action 목록을 프롬프트에 제공, 미등록 이름은 `external.other`(strong_approve)로 정규화하고 원래 이름 보존. 커넥터가 있는 action만 Gateway 실행, 나머지 승인은 사람이 수행할 결정 기록 |
| 승인함 과잉(요청 약 30건) | 최종 게이트 직전 CEO가 승인함 정리: 같은 결정을 묻는 요청 병합, 결정으로 불필요해진 요청 철회 (P-1: 17건 병합·4건 철회 → 9건) |
| Skill Factory가 미충족 기준을 줄였지만 근거 지지율이 0.78→0.35로 떨어진 스킬을 승격 후보로 올림(근거를 약하게 대며 기준을 "충족") | 승격 조건에 검토자 지지율 하락 상한(`MAX_SUPPORT_DROP` 0.15) 추가. 기존 대기 후보 재평가 → `competitor-feature-overlap-matrix` 탈락, REQ-37 시스템 철회 |
| 머지 후에도 개발 작업이 DELIVERED로 남고 비용이 0으로 기록됨 | `MERGED` 상태 추가(작업·명세), 단계마다 호출 원장에서 개발 작업 비용 합산 |
| 머지하면 프로젝트가 "끝났어요"인데 아무도 쓸 수 없음. 최종 보고서는 머지 전에 써서 코드와 다른 말을 하고, 머지 카드는 못 본 화면과 예시 데이터 화면을 말하지 않음 (페르소나 3라운드) | `engineering/usable.py`: 머지는 쓸 수 있음이 아니다. 완료 기준 확인이 브랜치 자체 파일에서 실행 방법·필요한 키·호스팅 후보(`AcceptanceOut.use`)와 실제로 만든 것(`built`)을 적고, 머지 뒤 `dev_merged` 작업이 결정 하나(어떻게 쓸지, `dev.deploy_production` 질문)를 올린다. 답은 단계가 적힌 할 일이 되고 회사는 배포하지 않는다. `state`(decide·todo·usable·later)는 결정과 할 일 상태에서 파생하고 프로젝트 상태 줄이 그것을 말한다. 보고서에는 머지한 코드에서 확인한 부분이 요약 바로 뒤에 들어가고, CEO는 머지 전 개발 결과를 말하지 않는다. 머지 카드 첫 줄은 못 본 핵심 화면과 예시 데이터 화면 |
| 워커를 호출 도중 재시작하면 예산 예약(4.4 USD-eq)이 영구히 남아 한도를 잠식 | 워커 시작 시 실행 중 작업이 없으면 남은 예약을 해제 |
| 분석가가 다운로드 가능한 원자료를 못 찾자 HTML 표의 수치를 스크립트에 직접 적어 계산 → 입력 없이 "재현됨"으로 기록 (첫 실전) | 서버가 받지 않은 수치에서 나온 값은 `transcribed`로 표시, 검토자에게 입력 대조 의무 부여, 기록에 "입력 파일 없음" 경고. HTML 표 페이지도 서버가 받아 `pandas.read_html`로 읽도록 안내하고 수치 직접 기입 금지. 검토자가 스크립트를 보도록 본문에 포함 |
| 검토 프롬프트 수정 중 문자열 뒤 괄호식이 함수 호출로 해석되는 버그(실행 시 TypeError) | 실전 실행 전 경고로 발견해 수정. 업무 노드를 모델 호출 없이 끝까지 돌리는 테스트 추가(`tests/test_analysis_flow.py`) |
| Daniel이 목표 질문(REQ-61)에 '150–250명으로'라고 답했는데 대시보드는 계속 '500 signups'. Alex의 '카페 5곳 … 매주 다시 셈'은 단어 목록에 'cafés'가 없어 회사 기본값(외부 사용자 10명)으로 바뀜 (2026-11-04 비판 라운드) | 목표의 출처를 승인된 계획의 목표 줄 하나로 둔다. 줄에 숫자를 타입으로 단다(`contracts/goal.py` `GoalTarget`: 숫자·단위·명단 단계, `BriefLine.target`). 킥오프 CEO가 주 숫자 줄에 적고, 서버는 줄 글에 있는 숫자일 때만 받는다(`roadmap.checked_target`). `completion.goals`는 target을 먼저 읽고, 없으면 글을 읽고, 그래도 숫자가 없으면 그 줄을 오너가 확인할 목표로 보인다. 기본값은 승인된 목표 줄이 없을 때만 쓴다. 계획 줄을 바꾸는 카드는 `amends`(줄 id)와 선택지별 `line`을 적고(서버는 승인된 계획의 오너 줄이 아니면 버린다), 오너가 고르면 `roadmap.amend_line`이 다음 승인 버전을 쓴다(출처 `request:<id>`, 같은 답은 버전을 더 만들지 않음). 새 버전이 승인되면 이전 계획을 복사해 둔 프로젝트 목표도 새 계획으로 다시 쓴다 |
| Maya의 '어디에 호스팅할지'가 명세의 열린 질문으로만 남고 결정이 되지 않음. Alex의 가져온 제품에서 범위가 커지고, 보고서와 코드가 달랐음 (2026-11-05 평가3) | GitHub Spec Kit(MIT)의 형식을 명세 파이프라인에 넣었다. 명세는 우선순위 사용자 스토리(P1부터, 따로 시험 가능)와 Given/When/Then, 아무 일 없는 날 시나리오를 `stories.yaml`(서버 번호 US1, US1.2)에 둔다. 작성자는 모르는 것을 짐작해 Assumptions에 적고, 꼭 물어야 할 것만 `[NEEDS CLARIFICATION Qn]` 최대 3개로 표시한다. 표시마다 결정 카드 하나(`engineering/questions.py`, 질문·추천 답·한 줄 이유)가 명세 카드보다 먼저 가고, 명세는 `CLARIFICATION_REQUIRED`로 기다린다. 카드가 모두 닫히면 `specs.on_questions_answered`가 답을 표시 자리와 Clarifications에 적고 그 커밋으로 명세 카드를 올린다(보류한 질문은 추천 답, 가정으로 표시). 제품마다 헌법(`engineering/constitution.py`, 레지스트리 `constitutions/<repo>.yaml`, 명세마다 `constitution.md` 사본): 가져온 제품은 모습·기술·범위 유지, 오너가 말한 제약은 출처(오너의 말만: 승인 전 결정은 안 됨)와 함께. 첫 명세가 제안하고 이후 명세는 결정 카드로 바꿀 것을 묻는다(양쪽만 적고 추천은 없음). 어느 쪽이든 명세 카드에 규칙 목록으로 보이고, 오너가 그 명세를 승인할 때만 정해진다. P1 스토리(시나리오 포함)가 없는 초안은 한 번 다시 쓰고, 그래도 없으면 실패로 보인다. 완료 확인은 P1 시나리오마다 `scripts/core-flow.sh`의 `[scenario US1.2] pass` 줄로 판정하고, 머지 카드는 스토리·만든 것·확인 결과가 어긋난 곳을 시나리오의 말로 맨 앞에 적는다(스크립트가 안 돌았으면 그 한 줄만) |
| 페르소나 3라운드: Alex(개발자)는 브랜치를 직접 받아 돌리고 기술 내용을 원했고, 박정숙(가게 사장)은 'CPO'·'재사용'을 몰라 게시 링크도 못 가져왔고, Maya(전 PM)는 마케팅 문구를 직접 고쳐 썼다. 한 화면이 아무에게도 맞지 않았다 (2026-11-05) | 오너가 잘하는 분야를 한 번 고른다(`app_settings.owner.strengths`: software·marketing·design·operations, 여러 개, `None`은 아직 안 물음, `[]`는 '아직 없어요'). 첫 새 사업 화면에서 묻고(가져온 제품이면 개발이 미리 골라짐) 설정 › 내 강점에서 고친다. `services/strengths.py` 표 하나가 두 곳에 쓰인다. 모든 에이전트 호출은 `executor.with_owner_rules`가 언어 규칙 바로 앞에 붙이는 한 절로 읽는다: 잘하는 분야는 더 깊이 묻고, 실제 내용을 보이고, 선택지를 장단점과 함께 열어 두고, 그 일을 오너에게 맡긴다. 아닌 분야는 기본값을 추천하며 덜 묻고, 결론과 다음 할 일을 쉬운 말로, 추천 하나와 한 줄 이유, 단계별 할 일로 쓴다. 결정 카드는 action·작성 에이전트로 분야를 정해 `fit`(strong·weak)을 받고, 콘솔은 잘하는 분야의 자세히·명세·코드(브랜치, PR, 바뀐 파일: 완료 기준 확인이 `changes`로 저장)·게시 위치를 펼치고, 아닌 분야는 실행 명령을 접는다. 묻지 않았으면 전과 같다 |
| [재평가] Sam: 설정이 'python: not found'로 멈춰 아무것도 돌지 않았고 기준 35개를 모두 못 채운 개발에 머지 승인 카드(REQ-97)가 왔다. 개발 페이지는 44분 동안 '개발 시작'만 보였고, 머지 뒤 결정은 가게들에 '업로드 링크'를 약속한 초안이 있는데 '내 컴퓨터에서 실행'을 추천했고, 쓸 수 없는데 프로젝트는 '완료'였다 (2026-11-05) | `engineering/gate.py`: 설정·핵심 흐름 실패, P1 시나리오 미통과, 코드 검토의 수정 요구, 기준 절반 미만 충족이면 확인 실패다. 실패한 개발은 머지 카드를 받지 않고 `revise.failed_check`가 수정 회차를 스스로 시작하거나(`engineering.auto_fix_rounds`, 수정 예산 안에서), 실패를 맨 앞에 적은 카드로 고치기·버리기만 묻는다(머지 없음). 버린 개발은 새 시도를 기다리지 않고 프로젝트는 `DEV_DROPPED`(완료, 개발 결과 버림)로 끝나며, PR을 못 닫아도 버림은 남고 그 사실을 단계에 적는다. 핵심 흐름과 화면 캡처는 엔지니어 이미지에서 `ci-setup.sh`를 돌리는데 테스트 이미지와 달리 `python`이 없었다: 이미지 4가 `python-is-python3`를 넣는다. 긴 개발은 엔지니어 clone의 reflog를 평범한 파일로만 읽어(호스트는 그 안에서 git을 돌리지 않음) `[task k/M]` 커밋으로 '계획한 작업 M개 중 k개'를 단계 줄에 보인다(`worker.note_progress`, 단계 기록은 늘리지 않음). 완료 기준 확인은 회사가 준비한 메시지를 읽고 `use.reach`(오너 말고 누가 열어야 하는지)를 적고, 그러면 로컬 실행을 추천하지 않으며 골라도 충돌을 한 줄로 말한다. 프로젝트 상태는 `status.project_stage` 하나다(머지했지만 쓸 수 없으면 MERGED). 열 화면이 없는 제품은 카드 없이 'tool' 상태로 쓰는 법(명령·README)을 보인다 |

## 7. 알려진 한계
- 엔지니어·검토·테스트 컨테이너는 인터넷과 호스트로 가는 경로가 없는 내부 네트워크(`ship-sandbox`)에서 돈다. 밖으로 나가는 길은 출구 프록시(`ship-egress`, tinyproxy) 하나이고, 프록시는 기본 거부에 `docker/egress/allowlist`의 네 호스트(`api.anthropic.com`, `registry.npmjs.org`, `pypi.org`, `files.pythonhosted.org`)만 이름으로 허용하며 HTTPS 터널은 443만 연다. 호스트 파일은 마운트되지 않는다. 남은 한계: 허용된 호스트 안에서 오가는 내용은 검사하지 않으므로, 엔지니어가 가진 단기 접근 토큰으로 모델 API에 보내는 내용까지 막지는 못한다.
- 분석 입력 다운로드의 주소 검사는 DNS 조회 시점 기준이다(DNS 재바인딩까지 막지는 않음). 다운로드는 호스트에서 GET만 하며 응답은 컨테이너 안에서만 해석된다.
- 분석가는 직접 다운로드 링크만 쓸 수 있다. 로그인·자바스크립트 뒤의 자료(KOSIS 일부 등)는 오너가 `var/data/<slug>/`에 넣어야 한다.
- 구독 CLI는 계정 단위 사용량 한도가 있다. `providers.*.max_concurrency`로 조절하며, 한도 도달 시 초기화 시각까지 Claude를 끈다. 다른 공급자로 대체하지 않으므로 구독이 소진되면 호출이 실패하고 업무가 멈춘다.
- 에이전트에게 데이터 다운로드·분석 도구가 없다(가장 많이 요청된 역량 공백).
