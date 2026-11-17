# 운영 안내

[English](OPERATIONS.md)

Just Ship It을 내 PC에서 계속 돌리고, 콘솔에 로그인하고, 샌드박스를 관리하는 방법입니다. 처음 설치는
[README](../README.ko.md#시작하기)에 있습니다.

## 지켜보지 않는 PC에서 계속 돌리기

워커와 콘솔을 따로 켜는 대신 감시 명령 하나로 둘 다 켭니다. 어느 쪽이든 꺼지면 다시 켜고, 곧바로 또 꺼지면
5초·15초·1분·5분으로 간격을 늘립니다(재부팅 직후 DB가 아직 안 떴을 때).

```sh
uv run ship supervise                # 워커(동시 4개)와 콘솔(:8000). Ctrl+C로 둘 다 멈춤
uv run ship supervise --install      # Windows 로그온 작업 "Just Ship It" 등록: 로그인할 때마다 위 명령이 켜짐
uv run ship supervise --uninstall    # 그 작업 삭제
uv run ship backup                   # DB 백업을 var/backups/에 지금 하나 만듦(워커가 매일 한 번 자동으로 함)
```

- 재부팅 뒤 자동으로 다시 돌게 하려면 `--install` 한 번, Docker Desktop의 "Start Docker Desktop when you sign in"을
  켜 두세요. Postgres 컨테이너는 `restart: unless-stopped`라 Docker가 뜨면 같이 뜹니다. 로그온 작업이라
  Windows에 로그인해야 시작합니다(Claude 구독 로그인이 사용자 계정에 있기 때문).
- Ctrl+C(첫 번째)는 워커에게 멈추라는 뜻입니다. 실행 중인 호출을 끝내고 작업을 대기열로 돌려놓으므로, 다시 켠 워커가
  체크포인트에서 바로 이어 갑니다. 창을 닫거나 두 번째 Ctrl+C로 강제로 끄면 리스가 끊길 때까지(약 1시간) 기다립니다.
- 로그: `var/logs/worker.log`, `var/logs/api.log`, `var/logs/supervisor.log`(각 10MB에서 넘겨 5개 보관).
- 상태: 콘솔은 `/api/health`로 API·DB·워커 상태를 봅니다. 워커가 10분 넘게 응답하지 않으면 콘솔 프로세스가
  Slack(설정의 '실패' 알림)으로 한 번 알립니다. 같은 중단으로는 다시 알리지 않습니다.
- Redis를 없애기 전에 설치했다면, 업데이트한 뒤 `docker compose up -d --remove-orphans`를 한 번 실행합니다. 옛 Redis
  컨테이너가 지워집니다. `aiteam_redisdata` 볼륨은 디스크에 남으니 필요 없으면 직접 지웁니다
  (`docker volume rm aiteam_redisdata`). `.env`의 `SHIP_REDIS_PASSWORD` 줄은 더 이상 읽지 않습니다.
- 백업은 `docker compose exec postgres pg_dump`로 만들고 최근 7개(`operations.backups_kept`)를 남깁니다. 복원은 워커와
  콘솔을 끄고 다음 명령을 실행합니다.

  ```sh
  docker compose exec -T postgres pg_restore -U ship -d ship --clean --if-exists < var/backups/<파일>
  ```
- 이전 이름(`aiteam` 명령, `AITEAM_` 설정)으로 쓰던 설치를 옮기는 순서는 [RENAME-CUTOVER.md](RENAME-CUTOVER.md)에 있습니다.

## 콘솔 로그인

콘솔 API는 어떤 주소로 열든 모든 `/api` 요청과 실시간 갱신(SSE)에 토큰이 필요합니다. 같은 PC의 다른 프로그램(스크립트,
`curl`, 엔지니어 컨테이너)도 Host와 헤더는 마음대로 꾸밀 수 있기 때문입니다. 토큰은 `var/api_token`(0600)에 한 번
만들어지고, 주소창이나 로그에는 나오지 않습니다.

1. `uv run ship api`나 `supervise`를 실행하면 터미널에 로그인 주소(`http://127.0.0.1:8000/?login=…`)가 나옵니다.
2. 그 주소를 브라우저에서 엽니다. 주소의 코드는 10분 안에 한 번만 쓸 수 있고, 열면 브라우저가 로그인 쿠키
   (HttpOnly, SameSite=Lax, 30일)를 받고 코드 없는 주소로 넘어갑니다.
3. 주소가 만료됐거나 다른 브라우저에서 열려면 서버를 켜 둔 채 `uv run ship api --login`으로 새 주소를 받습니다.
   콘솔이 "로그인이 필요합니다"를 보이면 같은 방법으로 다시 로그인하세요.

스크립트는 `var/api_token`의 토큰을 `Authorization: Bearer` 헤더로 보냅니다. 변경 요청에는 `X-AITeam: 1` 헤더도
필요합니다. 토큰을 명령줄 인자로 넘기지 말고 파일에서 읽으세요. 토큰이 새어 나갔다고 의심되면
`uv run ship api-token rotate`로 바꿉니다. 켜져 있는 서버에도 바로 적용되며, 기존 로그인 쿠키, 예전 토큰,
아직 쓰지 않은 로그인 주소가 모두 거부됩니다.

로그인 쿠키에는 토큰 자체가 아니라 토큰에서 만든 값이 들어 있어, 이 PC의 다른 포트(개발 서버 등)가 쿠키를 받아도
토큰은 알 수 없습니다. 다만 쿠키를 가로챈 쪽은 토큰을 바꿀 때까지 그 쿠키로 콘솔 API를 모두 쓸 수 있습니다. 쿠키에
토큰을 그대로 담던 이전 버전에서 올라오면 첫 `ship api` 실행 때 토큰을 한 번 새로 만들고(`var/cookie_is_hmac`에
기록) 그 이유를 터미널에 알립니다. 그때 한 번 다시 로그인하세요.

서버가 로그인을 스스로 끝내는 일은 없고, 콘솔에 로그아웃 버튼도 없습니다. 로그인 쿠키는 토큰이 바뀔 때까지
통합니다(브라우저는 30일 뒤에 쿠키를 지웁니다). 모든 브라우저를 로그아웃시키려면 `uv run ship api-token rotate`를
실행하고, `uv run ship api --login`으로 다시 로그인하세요. 스크립트는 `var/api_token`에서 새 토큰을 읽어야 합니다.

다른 기기에서 콘솔을 쓰려면 `ship api --host 0.0.0.0`보다 Tailscale 같은 사설 네트워크(`--host <Tailscale 주소>`)나
SSH 터널(서버는 기본값으로 켜고 `ssh -L 8000:127.0.0.1:8000 <이 PC>`)을 쓰세요. http는 암호화되지 않으므로 여럿이
쓰는 네트워크에 열면 같은 네트워크의 누구든 로그인 주소와 쿠키를 엿볼 수 있습니다. 요청이 https로 들어왔으면
(앞단 프록시의 `X-Forwarded-Proto` 포함) 쿠키를 Secure로 줍니다.

다른 사이트의 페이지가 보내는 변경 요청과, 이 PC에서만 열었을 때 다른 Host로 들어온 요청은 토큰과 별개로 거부합니다.
서버 접근 로그에는 쿼리 문자열을 남기지 않습니다.

이 PC에서 콘솔은 `localhost`가 아니라 `127.0.0.1`로만 답합니다. `http://localhost:8000`으로 연 페이지는
`http://127.0.0.1:8000`으로 옮겨 가고, `localhost`로 보낸 API 요청은 거부됩니다. 제품 미리보기는
`http://localhost:4173`에서 엽니다. 브라우저는 같은 호스트의 모든 포트에 쿠키를 보내므로, 이름을 이렇게 나눠 두면
에이전트가 쓴 코드인 미리보기에 콘솔 로그인이 넘어가지 않습니다. 콘솔 앞에 프록시를 두면 Host를 `127.0.0.1`로
보내야 합니다.

모든 응답은 브라우저에게 스크립트, 스타일, 글꼴, 이미지를 콘솔에서만 불러오고 콘솔을 다른 페이지 안에 띄우지
말라고 알립니다. API 설명을 그려 주는 페이지(Swagger UI, ReDoc)는 다른 사이트의 코드를 불러오므로 콘솔이 제공하지
않습니다. 설명 자체는 `/api/openapi.json`에 있고 토큰이 필요합니다.

## 엔지니어 컨테이너는 내 구독으로 로그인합니다

설정할 것이 없습니다. 엔지니어 에이전트는 매 호출마다 새 컨테이너(`ship/engineer`)에서 실행되고, 그 개발 작업의
clone만 봅니다. 컨테이너 안의 Claude Code는 **이 PC에 로그인된 Claude 구독**을 씁니다(API 키 없음). 호스트의
`~/.claude/.credentials.json`에서 단기 접근 토큰만 환경변수로 넘기고, 갱신 토큰은 넘기지 않으므로 컨테이너가 호스트
로그인을 끊을 수 없습니다. 만료가 가까우면 호스트 `claude`가 정상 경로로 갱신한 뒤 넘깁니다.

상태 확인은 `uv run ship sandbox check`입니다. `claude setup-token`으로 만든 장기 토큰(`SHIP_CLAUDE_OAUTH_TOKEN`)은
컨테이너에 넘기지 않습니다. 유출되면 1년 동안 유효하기 때문입니다.

로그인이 없으면 개발 작업은 첫 단계에서 이유를 남기고 멈춥니다(호스트 실행으로 몰래 돌아가지 않음).
예전 방식(호스트 worktree + 금지 명령 규칙)이 필요하면 `config/company.yaml`의 `engineering.isolation: host`.

엔지니어 이미지(`ship/engineer`)는 pstack 플러그인(`michael-denyer/pstack-claude`의 고정 커밋)을 빌드 시 설치합니다.

## 데이터 분석

`analyst` 에이전트는 공개 원자료(통계 Excel·CSV)를 받아 코드로 집계합니다. 파일은 서버가 받고(공개 주소의 http/https만,
파일당 100MB), 스크립트는 네트워크 없는 컨테이너에서 실행되며, 최종 스크립트를 새 컨테이너에서 다시 돌려 결과가 같으면
근거가 `reproduced`로 표시됩니다.

로그인 뒤에 있는 자료는 `var/data/<프로젝트 slug>/`에 직접 넣으면 `local:<파일명>`으로 쓸 수 있습니다.

## git 버전

git은 2.45.1 이상을 권장합니다. 워커는 버전 번호 대신 CVE-2024-32465(CVE-2024-32004와 같은 수정) 공격을 임시 저장소에서
직접 시험하고, 막지 못하는 git이나 2.41보다 오래된 git이면 컨테이너 엔지니어링 작업을 받지 않습니다. 2.41보다 오래된
git에서도 레지스트리 문서는 저장하지만, 그 사실을 경고로 한 번 남깁니다. 시험 자체를 끝내지 못한 경우에도 받지 않고,
다음 작업 접수 때 다시 시험합니다. 보안 업데이트를 적용한 Ubuntu 24.04의 2.43.0은 통과합니다. 결과는
`ship sandbox check`에 나옵니다.

## 명령 모음

대부분의 일은 콘솔에서 합니다. CLI는 같은 일을 터미널에서 합니다.

```sh
uv run ship init                     # Postgres 무작위 비밀번호(.env)와 서비스 재생성, 마이그레이션, registry/, 체크포인터
uv run ship sandbox build            # 엔지니어·데이터분석 컨테이너 이미지 (처음 한 번, 수 분; 태그는 config/company.yaml)
uv run ship sandbox check            # 준비 상태
uv run ship worker                   # 워커만: 회사 실행·재개·명세·개발 작업 처리 (supervise가 대신 켬)
uv run ship api                      # 콘솔만: http://127.0.0.1:8000

uv run ship project new "전남 방문요양기관 대상 AI 업무 보조 SaaS의 사업성을 검증하라 ..." --slug care-saas
uv run ship project list
uv run ship project show 1
uv run ship project report 1
uv run ship inbox                    # 오너를 기다리는 결정
uv run ship show REQ-3
uv run ship answer REQ-3 "기관(센터장) 우선"
uv run ship approve REQ-7 --note "MVP 범위로 진행"
uv run ship approve REQ-9 --confirm REQ-9     # 강한 승인(머지·광고·계약 등)
uv run ship dev                      # 명세/개발 작업 상태
uv run ship usage                    # 에이전트별 호출·토큰·USD 환산치
uv run ship skills [--factory]       # 스킬·역량 공백, Skill Factory 실행
uv run ship kpi list | add | record
uv run ship schedules [--run ID]     # 정기 업무
uv run ship consent <contact_id>     # 연락 동의 기록(사람만 가능)
```

새 사업은 콘솔에서 시작합니다. 대시보드의 **새 사업 시작**으로 시작 대화를 엽니다. CLI의 `project new`는 시작 대화
없이 목표 한 줄로 프로젝트를 바로 만드는 지름길입니다.
