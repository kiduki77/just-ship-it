# Just Ship It 외부 연동 타당성 조사 (2026-11-03 기준)

표기: **[확인]** = 공식 문서에서 직접 확인(페이지 본문을 가져와 읽음), **[2차]** = 공식 문서를 가리키는 2차 자료 또는 포럼, **[미확인]** = 이번 조사에서 공식 근거를 못 찾음(기억/추정). "찾지 못함"은 추측하지 않고 그대로 적음.
제3자 서비스 자격증명(OAuth 토큰, 봇 토큰, API 키)은 LLM API 키와 별개로 오너가 결정할 사안이므로, 각 항목에 "필요한 자격증명"을 명시했다.

공통 전제: 앱은 `127.0.0.1`에서만 열리고 공개 인바운드 URL이 없다. 따라서 **폴링 또는 아웃바운드 연결(WebSocket)만 쓰는 방식**이 기준이다. 모든 외부 쓰기는 CLAUDE.md 규칙에 따라 outbox/dry-run을 거치게 하고, 토큰은 환경변수 이름으로만 전달한다.

---

## 1. Slack (알림 + 승인/응답)

### 1-A. Incoming Webhook (송신 전용)
- 인증: 웹훅 URL 자체가 비밀. 별도 토큰 없음. [확인] https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/
- 설정: 앱 생성 -> Incoming Webhooks 활성화 -> "Add New Webhook to Workspace" -> 채널 선택 -> URL 복사. OAuth 설치 흐름으로 만들 땐 `incoming-webhook` 스코프. [확인] 같은 문서
- 수신 불가: 웹훅은 보내기만 한다. 버튼 클릭 콜백을 받는 방법은 해당 문서에 없다(별도 인터랙티브 문서로 링크). [확인: 문서에 명시 없음]
- 레이트 리밋: 해당 문서에는 "찾지 못함". (chat.postMessage는 아래 참고)
- 비용: 무료 워크스페이스 가능 여부는 이번에 확인하지 못함. [미확인]

### 1-B. Slack 앱 + 봇 토큰 + Socket Mode (권장)
- 공개 URL 불필요: Socket Mode는 Events API와 인터랙티브 페이로드(슬래시 커맨드, Block Kit 버튼, 모달, App Home)를 **WebSocket**으로 받는다. WebSocket URL은 `apps.connections.open`으로 런타임에 발급. [확인] https://docs.slack.dev/apis/events-api/using-socket-mode/
- 토큰 2종:
  - 앱 레벨 토큰 `xapp-...` (Basic Information에서 생성; `apps.connections.open` 호출용, Authorization 헤더로 전송). 문서 본문에는 필요한 스코프명(`connections:write`)이 적혀 있지 않았다. 스코프명은 [미확인]이나 Slack UI에서 토큰 생성 시 선택하는 항목이다.
  - 봇 토큰 `xoxb-...` (메시지 전송 등 Web API용).
- 필요한 봇 스코프(추정, [미확인]): 메시지 보내기 `chat:write`, DM 열기 `im:write`, 오너 DM 응답 수신을 하려면 `im:history` + 이벤트 `message.im`. 정확한 목록은 앱 매니페스트를 만들 때 공식 스코프 문서로 확정해야 한다.
- 이벤트마다 `envelope_id`로 ack 필요(SDK 미사용 시). 버튼 클릭 등도 같은 소켓으로 들어온다. 소켓은 몇 시간마다 갱신되며 끊기기 약 10초 전에 경고가 온다 -> 재연결 루프 필요. 연결은 앱당 최대 10개. [확인] 위 문서
- 요청 서명 검증: Socket Mode는 소켓 자체가 인증되어 있어 검증 불필요. [확인] 위 문서
- 제약: Socket Mode 앱은 공개 Slack Marketplace에 올릴 수 없음(자기 워크스페이스 전용 사용에는 무관). [확인] 위 문서
- 레이트 리밋: `chat.postMessage`는 Special tier, "채널당 초당 1개 정도, 워크스페이스 전체 한도도 있음", 초과 시 HTTP 429 + `Retry-After`. [확인] https://docs.slack.dev/apis/web-api/rate-limits/ Tier 1~4 = 분당 1+/20+/50+/100+. [확인] 같은 문서
- 비용: 이번 조사에서 확인하지 못함(무료 워크스페이스에서 자체 앱 사용 가능 여부는 공식 요금 페이지 확인 필요). [미확인]
- 오너가 손으로 할 일 (약 10~15분):
  1. api.slack.com/apps에서 앱 생성(매니페스트로 가능; `socket_mode_enabled: true` 지원 [확인])
  2. Socket Mode 켜고 앱 레벨 토큰 생성
  3. 봇 스코프 추가 후 워크스페이스에 설치
  4. 봇을 쓸 채널에 초대하거나 오너와의 DM 사용
  5. `xoxb`/`xapp` 토큰 두 개를 .env 또는 OS 보관소에 저장
  6. 오너의 Slack 사용자 ID 확인(승인 허용 목록용)

### 1-C. Slack CLI / MCP
- Slack CLI: Slack 앱을 만들고 관리하는 개발 도구(Bolt, Deno SDK와 함께). 알림 송신이나 상호작용 수신용 런타임이 아니다. [확인] https://docs.slack.dev/tools/slack-cli/ 배포/실행 여부는 해당 페이지에 명시 없음.
- Slack MCP 서버: Slack 호스팅 엔드포인트 `https://mcp.slack.com/mcp`(Streamable HTTP), 사용자별 OAuth, 디렉터리 게시 앱이나 내부 앱만 허용. [2차] https://docs.litellm.ai/docs/mcp_servers/slack , https://gamut.so/blog/slack-mcp-server-guide . 공식 문서 직접 확인은 못 했다(공식 도움말 링크: https://slack.com/help/articles/17388188499099 [미확인]). 이것은 "에이전트가 Slack을 읽고 쓰는 도구"에 가깝고, 버튼 승인 같은 이벤트 수신 경로가 아니다. 또한 앱 서버가 스스로 푸시하는 알림 용도로는 부적합하다고 판단한다(추정).
- 결론: CLI와 MCP는 이번 요구(알림 + 승인)에 대한 단순 해법이 아니다.

### 보안: Slack에서 승인하기
- 위험: 승인 요청의 승인 권한이 "Slack 채널에 있는 누구든"이 되면 안 된다. 인터랙션 페이로드의 사용자 ID가 허용 목록(오너 1명)과 일치할 때만 처리해야 한다(설계 권고).
- 승인 요청 ID, 해시, 만료 시간을 버튼 `value`에 넣고, 서버는 DB 상태와 대조한 뒤 일회성으로 처리(재클릭/리플레이 방지, 멱등).
- 봇은 오너와의 DM 또는 비공개 채널에만 사용. 봇 토큰/앱 토큰은 유출 시 해당 봇 권한으로 메시지 위조 가능하므로 로그에 남기지 않는다.
- 고위험 승인(결제, 외부 발송, 삭제)은 Slack 버튼이 아니라 콘솔에서만 하도록 제한하는 것을 권장.
- Slack 메시지 내용(예: 이메일 본문)은 외부에서 온 신뢰할 수 없는 텍스트이므로 에이전트 지시로 취급하면 안 된다.
- Socket Mode는 아웃바운드 연결만 쓰므로 PC에 인바운드 포트를 열 필요가 없다. 이 점이 웹훅 기반 방식 대비 가장 큰 보안 이점.

**권장: 1-B (Slack 앱 + Socket Mode).** 알림만 먼저 필요하면 1-A로 시작해 1-B로 확장 가능.

---

## 2. Gmail (읽기/보내기)

### 스코프 분류 [확인] https://developers.google.com/workspace/gmail/api/auth/scopes
| 스코프 | 분류 |
|---|---|
| gmail.readonly | Restricted |
| gmail.send | Sensitive |
| gmail.modify | Restricted |
| gmail.compose | Restricted |

### 인증 방식 비교
**2-A. Gmail API OAuth (데스크톱 앱 클라이언트)**
- 흐름: Google Cloud 프로젝트 생성 -> Gmail API 활성화 -> OAuth 동의 화면 설정 -> "Desktop app" OAuth 클라이언트 ID 생성 -> 로컬에서 루프백(127.0.0.1) 리디렉션으로 1회 동의 -> refresh token 저장. 공개 URL 불필요(루프백 사용). 데스크톱 클라이언트와 루프백 리디렉션 세부는 이번에 페이지로 확인하지 못함. [미확인]
- **Testing 상태 7일 만료**: "외부(external) 사용자 유형 + 게시 상태 Testing"이면 refresh token이 7일 후 만료(이름/이메일/프로필 스코프만 쓰는 경우 예외). [확인] https://developers.google.com/identity/protocols/oauth2#expiration
- 그 외 refresh token 무효화 사유: 사용자가 접근 취소, 6개월 미사용, **Gmail 스코프 토큰은 비밀번호 변경 시 무효**, 보유 토큰 수 초과, 관리자 정책. [확인] 같은 문서
- **Production(게시됨) 상태로 전환하면** 7일 규칙이 적용되지 않는다는 것은 위 문서가 "Testing 상태일 때"로 한정한 표현에서 읽히는 해석이다. 개인용으로 Production 전환 시 동작은 직접 확인하지 못했다. [미확인]
- 검증/100명 제한: 민감/제한 스코프를 쓰는 미검증 앱은 경고 화면이 뜨고 신규 사용자 총 100명 제한. 검증은 사용자 대상 앱 출시 전에 필요하고 몇 달 걸릴 수 있음. 개발/테스트용은 검증 불필요. [확인] https://support.google.com/cloud/answer/7454865 . 제한 스코프의 보안 평가(CASA) 절차는 이 문서에 없어 "찾지 못함". 오너 1명만 쓰는 용도라면 미검증 Production + 경고 화면 통과("고급 -> 이동")로 운영하는 방식이 현실적이나, 이 경로의 안정성은 공식 문서로 보증되지 않는다. [미확인]
- 쿼터: 사용자당 프로젝트당 분당 6,000 유닛(약 초당 100). messages.list 5, messages.get 20(full/raw/html은 60), messages.send 100. 일일 임계 8천만 유닛/프로젝트(초과 과금은 2026년 중 90일 이상 사전 고지로 도입 예정). [확인] https://developers.google.com/workspace/gmail/api/reference/quota 솔로 사용에는 사실상 무제한.
- 비용: Gmail API 자체 무료. (과금 도입 예정은 위 일일 임계 초과분에 한함)

**2-B. IMAP/SMTP + 앱 비밀번호**
- 요건: 2단계 인증 켜짐, 보안 키만 쓰는 2SV 불가, **회사/학교 계정 불가**, 고급 보호 계정 불가, 비밀번호 변경 시 앱 비밀번호 모두 폐기, 16자리 비밀번호는 한 번만 표시. Google은 사용을 권장하지 않음. [확인] https://support.google.com/accounts/answer/185833
- 장점: GCP 프로젝트/동의 화면/7일 만료 문제 없음. 설정이 몇 분.
- 단점: 스코프 분리 불가(앱 비밀번호 = 메일 전체 접근). 읽기 전용이나 전송 전용으로 권한을 좁힐 수 없다. 에이전트가 쓰는 자격증명으로는 권한이 과하다.
- IMAP/SMTP 포트, 레이트 한도: 이번에 확인하지 못함. [미확인]

### 권장 (솔로, 로컬)
**Gmail API OAuth, 데스크톱 클라이언트, 게시 상태는 Production(미검증).** 이유: 최소 스코프(`gmail.readonly` + `gmail.send`; 라벨 변경이 필요할 때만 `gmail.modify`)를 지킬 수 있다. Testing 상태로 두면 7일마다 재동의가 필요해 무인 운영에 부적합하다.
- 오너 수작업: GCP 프로젝트 만들기, API 켜기, 동의 화면(앱 이름, 지원 이메일, 스코프 추가) 입력, Desktop 클라이언트 생성 후 `client_secret` JSON 보관, 1회 브라우저 동의(경고 화면 통과), 게시 상태를 Production으로 변경. 약 20~30분.
- 대안(더 단순, 권한은 과함): 개인 Gmail이고 2SV가 켜져 있다면 2-B로 5분 설정 가능. 발송 승인 흐름은 outbox를 거치게 해야 한다.
- 주의: 비밀번호를 바꾸면 Gmail 스코프 토큰이 무효화되므로 재동의 필요. 이는 운영 알림에 포함할 것.

---

## 3. GitHub 지표

### 엔드포인트별 정리
| 지표 | 엔드포인트 | 권한/제약 | 상태 |
|---|---|---|---|
| 스타 + 시각 | `GET /repos/{o}/{r}/stargazers` + `Accept: application/vnd.github.star+json` (응답에 `starred_at`) | per_page 최대 100. **2026년 7월부터 stargazers 목록 엔드포인트는 관리자/협업자로 제한**. fine-grained PAT 권한이 "Contents (write)"로 안내됨(이상하므로 실제 호출로 검증 필요) | [확인] https://docs.github.com/en/rest/activity/starring , https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens |
| 포크 | forks 목록 | fine-grained: Metadata (read) | [확인] 권한 문서 |
| 이슈 | `GET /repos/{o}/{r}/issues` | Issues (read) | [확인] 권한 문서 |
| 트래픽 (views, clones, referrers, popular paths) | `/traffic/views`, `/traffic/clones`, `/traffic/popular/referrers`, `/traffic/popular/paths` | **저장소 쓰기 권한 필요**, 최근 14일 데이터, referrers/paths는 상위 10개. fine-grained: Administration (read) | [확인] https://docs.github.com/en/rest/metrics/traffic , 권한 문서 |
| 릴리스 다운로드 | 릴리스 asset의 `download_count` | 릴리스 목록: Contents (read). asset 목록 페이징 per_page 최대 100 | [확인] https://docs.github.com/en/rest/releases/assets |

- 14일 창 때문에 트래픽은 **최소 주 1회(권장 매일) 폴링해서 DB에 누적 저장**해야 장기 추이가 남는다(설계 권고).
- 주의: 이 저장소는 "no remote"라 아직 GitHub 저장소가 없을 수 있다. 공개 릴리스 후에 의미가 있다.
- 레이트 리밋: 비인증 시간당 60, 인증(PAT) 시간당 5,000. 2차 한도: 동시 100, 분당 900 포인트(GET 1점). [확인] https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api 솔로 지표 수집은 한도에 근접하지 않는다.

### `gh` CLI vs fine-grained PAT
- `gh auth login`은 브라우저 OAuth 또는 토큰 입력. 오너 PC에 로그인 상태를 두고 `gh api ...`로 호출하면 PAT를 코드에 두지 않아도 된다. 다만 `gh`의 기본 OAuth 토큰 스코프는 넓다(`repo` 등). 이 부분의 정확한 기본 스코프는 이번에 확인하지 못함. [미확인]
- Fine-grained PAT: 특정 저장소 1개로 한정, 위 표의 권한만 부여(Metadata, Issues read, Contents read, Administration read 등) 가능. 최소권한 원칙에는 이쪽이 낫다. 단, 트래픽용 Administration read는 민감한 권한이므로 읽기 전용임을 확인.
- 권장: **fine-grained PAT (대상 저장소 1개, 읽기 전용 권한 묶음, 만료 90일 등으로 설정) + 워커가 `GITHUB_TOKEN` 환경변수로 REST 호출.** `gh`를 쓰면 이미 로그인된 환경에서 설정이 가장 빠르다. 오너 수작업: PAT 발급 약 5분, 만료 알림 관리.

---

## 4. 모바일 앱 지표

### 4-A. App Store Connect API (Analytics Reports API)
- 인증: API 키 -> `.p8` 개인키, Key ID, Issuer ID로 **ES256 JWT**, 유효기간 최대 20분. 키 생성은 Account Holder/Admin, `.p8`는 **한 번만 다운로드 가능**. [2차: 내용은 모델 설명, 페이지 본문 직접 확인 실패] https://developer.apple.com/documentation/appstoreconnectapi/creating-api-keys-for-app-store-connect-api
- 역할: 새 분석 보고서 유형을 **처음 요청할 때는 Admin 역할** 키 필요, 이후 **Sales and Reports 또는 Finance Management** 역할 키로 다운로드 가능. [확인] https://developer.apple.com/kr/help/app-store-connect-analytics/overview/analytics-reports-api
- 흐름: report request 생성(`ONGOING` 또는 `ONE_TIME_SNAPSHOT`) -> reports -> instances -> segments(다운로드 URL). 형식은 탭 구분 `.txt.gz`. [확인] 위 도움말 + 흐름 세부는 [2차]
- 지연: ONGOING은 요청 후 첫 보고서까지 약 24~48시간. 특정 날짜 데이터는 보고일로부터 2일이 지나야 완전. [확인] 도움말
- 포함 보고서: 다운로드, 사전 주문, 구입, 구독 이벤트/상태, 설치/삭제, 세션, 충돌, App Store 노출/페이지 조회 등. [확인] 도움말
- 제한: 사용자 5명 미만 또는 기기 5대 미만 행은 생략, 통계적 노이즈 추가 -> **소규모 앱은 빈 데이터가 나올 수 있음**. 일부 사용 보고서는 임계치 미달 시 생성 안 됨. [확인] 도움말
- ONE_TIME_SNAPSHOT은 포럼에서 "최근 35일 일별 인스턴스만 반환", 0건 반환 보고 등 이슈가 있다. 인스턴스 보관은 제한 기간. [2차: 포럼] https://developer.apple.com/forums/thread/843936
- 비용: 이번에 확인하지 못함(Apple Developer Program 연 가입비 외 API 별도 요금 언급은 찾지 못함). [미확인]
- 오너 수작업: App Store Connect > Users and Access > Integrations에서 키 생성(Admin 키 1개로 최초 요청), `.p8` 안전 보관, Issuer ID/Key ID 전달, 앱 ID 확인. 약 15분.

### 4-B. Google Play Console
- **다운로드(설치) 데이터**: Cloud Storage 버킷(`pubsite_prod_rev_...`) 대량 내보내기. `stats/installs/`에 국가, 기기, 앱 버전 등 차원별 월별 CSV. 통계는 **3~7일 지연**으로 월별 CSV에 반영. 정확한 갱신 시각에 의존하지 말라고 안내. [확인] https://support.google.com/googleplay/android-developer/answer/6135870
- 권한: Play Console 사용자 권한 중 "View app information"이 Global이어야 함. 서비스 계정을 만들고 Play Console에서 그 이메일을 사용자로 초대, 읽기 전용 Cloud Storage 스코프(`devstorage.read_only`)로 접근. [확인] 같은 문서
- Store Performance 내보내기는 `stats/store_performance/`. 잔존 설치자 보고서는 갱신 중단. [확인] 같은 문서
- **Play Developer Reporting API**: Android vitals(크래시율, ANR, 웨이크업 등) 제공. **설치/다운로드는 문서에 언급 없음**("찾지 못함"). 인증 방식과 지연도 해당 페이지에는 설명 없음. [확인] https://developers.google.com/play/developer/reporting
- 따라서 자동화로 "다운로드 수"를 얻는 길은 Cloud Storage CSV(지연 3~7일). 리뷰는 `reviews/` 경로. [확인]
- 비용: 버킷 읽기 비용 등은 확인하지 못함. [미확인]
- 오너 수작업: GCP 서비스 계정 생성, Play Console에서 초대 및 권한 부여, 버킷 URI 복사. 서비스 계정 키(JSON)는 민감 정보이므로 보관 방식 결정 필요. 약 20분.

**권장:** 앱이 실제로 출시되기 전에는 구현하지 말고, 출시 후 iOS는 Analytics Reports ONGOING, Android는 Cloud Storage CSV로 일 단위 동기화. 지연이 있으므로 UI에는 "데이터 기준일"을 함께 표시.

---

## 5. 랜딩 페이지 + 가입 + 방문 분석

### 정적 호스팅 (모두 무료 가능)
- Cloudflare Pages Free: 월 500 빌드, 파일 20,000개, 동시 빌드 1개. Functions 요청은 Workers 쿼터에 합산. 대역폭 한도는 문서에서 찾지 못함. [확인] https://developers.cloudflare.com/pages/platform/limits/
- GitHub Pages / Vercel Hobby의 현재 한도: 이번에 확인하지 못함. [미확인]

### 가입 수집 옵션
| 옵션 | 로컬 앱이 읽는 방법 | 비용/제약 | 상태 |
|---|---|---|---|
| Cloudflare Pages Functions + D1 | Cloudflare REST API(D1 query) 또는 읽기용 Worker 엔드포인트를 폴링 | D1 Free: 하루 읽기 500만 행, 쓰기 10만 행, 저장 5GB, DB 10개. 한도 도달 시 쿼리 거부 | [확인] https://developers.cloudflare.com/d1/platform/pricing/ |
| Formspree | API로 제출 목록 조회 | 무료는 월 50건, **API 접근은 Professional($30/월, 연 결제 $20/월) 이상**. Free/Personal은 API 불가 | [확인] https://formspree.io/plans |
| Tally | `GET https://api.tally.so/forms/{formId}/submissions` (Bearer 키, 페이징 limit 최대 500) | 무료 플랜에서 API 사용 가능 여부와 레이트 리밋은 "찾지 못함". 실시간은 웹훅 권장(공개 URL 필요하므로 폴링 사용) | [확인] https://developers.tally.so/api-reference/endpoint/forms/submissions/list |
| Supabase | REST/Postgres로 읽기 | 이번에 확인하지 못함 | [미확인] |

### 방문 분석 옵션
| 옵션 | 읽기 API | 비고 | 상태 |
|---|---|---|---|
| Cloudflare Web Analytics | GraphQL Analytics API (엔드포인트 `https://api.cloudflare.com/client/v4/graphql`) | 프라이버시 중심, DNS 변경/프록시 없이 동작. Web Analytics 데이터셋(`rumPageloadEventsAdaptiveGroups`)과 토큰 권한은 문서에서 못 찾음 | 엔드포인트와 프록시 불필요 [확인] https://developers.cloudflare.com/web-analytics/ , https://developers.cloudflare.com/analytics/graphql-api/ ; 데이터셋명/권한 [미확인] |
| Plausible | Stats API, Bearer 키, 키당 기본 시간당 600건 | **Stats API는 Business 플랜 기능**(가격은 해당 페이지에 없음). 셀프호스트 언급 없음 | [확인] https://plausible.io/docs/stats-api |
| Umami Cloud | `https://api.umami.is/v1`, Bearer 키, 키당 15초에 50회 | 무료 플랜에서 API 키 사용 가능 여부는 "찾지 못함" | [확인] https://docs.umami.is/docs/cloud/api-key |

### 권장 기본값 (가장 적은 구성요소)
**Cloudflare 한 곳으로 통일:** Cloudflare Pages(정적 페이지) + Pages Function 1개(이메일 POST를 D1에 저장) + Cloudflare Web Analytics(스크립트 한 줄). 로컬 앱은 Cloudflare API 토큰 하나(D1 읽기, Analytics 읽기 권한)로 폴링.
- 근거: 계정 1개, 무료 한도가 솔로 규모에 충분(D1 쓰기 하루 10만 행), 별도 폼 서비스 유료 API 불필요(Formspree는 API가 유료).
- 단, Web Analytics GraphQL의 정확한 쿼리/권한은 구현 시 Cloudflare 문서에서 재확인 필요(이번 조사에서는 못 찾음).
- 대안: 가입 수집 코드를 쓰기 싫다면 Tally(폼 임베드) + 폴링. 무료 API 가능 여부는 먼저 확인할 것.
- 개인정보: 이메일 수집이 들어가므로 개인정보 처리방침과 동의 문구가 필요하다(법률 검토는 범위 밖).
- 오너 수작업: Cloudflare 계정, Pages 프로젝트 연결, D1 DB 생성 및 바인딩, Web Analytics 사이트 추가, API 토큰 발급. 약 30분.

---

## 6. 로컬 저장소에서 배포

| 대상 | 비대화형 인증 | 명령 | 상태 |
|---|---|---|---|
| Cloudflare Pages (wrangler) | 환경변수 `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID`. 토큰은 Custom Token에 "Account, Cloudflare Pages, Edit" 권한 | `npx wrangler pages deploy <DIR> --project-name=<NAME>` (Node 16.17+) | [확인] https://developers.cloudflare.com/pages/how-to/use-direct-upload-with-continuous-integration/ (로컬 CLI에서 토큰 환경변수 사용 방식은 그 페이지에 직접 설명 없음. CI 시크릿 이름으로 안내됨) |
| Vercel | 토큰 사용 방식(`--token`, `VERCEL_TOKEN`, 프로젝트/조직 ID)은 해당 페이지에 없어 "찾지 못함". 문서에서 확인된 것: `vercel`/`vercel deploy`, 최초 배포는 프로덕션, `vercel build` + `vercel deploy --prebuilt`로 로컬 빌드 업로드, stdout이 배포 URL | `vercel deploy --prebuilt` | 명령 [확인] https://vercel.com/docs/cli/deploying-from-cli ; 토큰 방식 [미확인] |
| GitHub Pages | 이번에 공식 문서 확인 못 함. 일반적으로 `gh-pages` 브랜치 푸시 또는 Actions 필요하며 원격 저장소가 있어야 함 | - | [미확인] |

- 워커에서 비대화형 실행: Cloudflare는 토큰 + 계정 ID만 있으면 가능. 프로젝트 최초 생성(`wrangler pages project create`)은 1회 수동으로 해두는 것이 안전하다(추정).
- 이 저장소에는 원격이 없으므로 GitHub Pages는 현재 구성과 맞지 않는다. Cloudflare Pages Direct Upload가 로컬 디렉터리를 곧바로 올릴 수 있어 가장 잘 맞는다.
- 배포는 외부 공개 행위이므로 CLAUDE.md 규칙상 outbox/dry-run + 오너 승인을 거치게 할 것.
- **권장:** 5번과 같은 Cloudflare 계정으로 `wrangler pages deploy`. 토큰은 Pages Edit 권한만 가진 전용 토큰으로 분리.

---

## 오너 수작업 부담 요약
| 항목 | 권장 | 대략 수작업 | 정기 유지 |
|---|---|---|---|
| Slack | 앱 + Socket Mode | 10~15분 | 거의 없음 |
| Gmail | Gmail API OAuth Production | 20~30분 | 비밀번호 변경 시 재동의 |
| GitHub | fine-grained PAT 1개 | 5분 | PAT 만료 갱신 |
| 모바일 | 출시 후 ASC API 키 / Play 서비스 계정 | 각 15~20분 | 키 보관 |
| 랜딩 | Cloudflare Pages + D1 + Web Analytics | 30분 | 거의 없음 |
| 배포 | wrangler + 전용 토큰 | 10분 | 토큰 관리 |

## 이번 조사에서 확인하지 못한 항목 (구현 전 재확인 필요)
- Slack 봇 스코프 정확한 목록, Slack 요금 정책, 앱 레벨 토큰 스코프명
- Gmail Production(미검증) 개인 사용의 refresh token 장기 유효 여부, 데스크톱 클라이언트 루프백 세부
- Cloudflare Web Analytics GraphQL 데이터셋명/토큰 권한, Supabase, Tally 무료 API, GitHub Pages/Vercel 한도 및 토큰 배포 방식
- ASC API 키 생성 문서는 직접 본문을 가져오지 못해 [2차]로 분류
- GitHub stargazers 권한 안내("Contents write")는 이상하므로 실제 호출로 검증
