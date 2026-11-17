# 런치 경로별 플레이북 (첫 100명 기준) - 리서치 결과

작성일: 2026-11-03. 대상: "아무것도 모르겠다"는 비전문가 1인 창업자.

## 0. 읽는 법

근거 표시는 세 가지다.
- **[Verified]** 내가 해당 페이지를 직접 읽고 문구를 확인했다.
- **[Secondary]** 다른 글이 요약한 것을 읽었다. 원문은 못 읽었다.
- **[Unverified]** 주장은 있으나 확인하지 못했다.
- **[제안]** 출처가 있는 사실이 아니라, 이 제품에 쓰자고 내가 정한 정의/설계안이다. 사실로 읽으면 안 된다.

못 찾은 것은 "not found"로 적었다. 숫자와 사례는 출처가 있는 것만 적었다.

### 이번 조사의 한계 (먼저 읽을 것)
- Reddit 공식 정책 페이지(redditinc.com, reddit.com wiki, support.reddithelp.com)는 접근이 막혀 **원문을 못 읽었다**. Reddit 규칙은 [Unverified]/[Secondary]로만 적었다.
- Product Hunt 공식 가이드라인 페이지도 **못 읽었다**(404). 공식 포럼 글(Verified)과 제3자 요약(Secondary)만 있다.
- Figma / VS Code 마켓플레이스 정책은 이번에 조사하지 않았다. "not found"다.
- YC 강연 영상 원본은 못 봤다. 요약 글만 봤다 [Secondary].
- 아래 "첫 100명까지 걸리는 시간"은 사례 글의 숫자일 뿐, 일반 평균이 아니다.

---

## 1. 모든 경로에 공통인 근거 (먼저 알아둘 것)

| 내용 | 근거 | 라벨 |
|---|---|---|
| 첫 사용자는 창업자가 직접 한 명씩 모집한다. "The most common unscalable thing founders have to do at the start is to recruit users manually." "You can't wait for users to come to you. You have to go out and get them." | Paul Graham, Do Things That Don't Scale: https://paulgraham.com/ds.html | Verified |
| Stripe는 "Right then, give me your laptop"라며 그 자리에서 설치해 줬다. | 위와 같음 | Verified |
| 소비자 앱 100곳 이상의 초기 성장 사례를 7가지 전략으로 정리: 오프라인 현장, 온라인 커뮤니티, 친구 초대, 대기명단/희소성, 인플루언서, 언론, 출시 전 커뮤니티. | Lenny's Newsletter: https://www.lennysnewsletter.com/p/how-the-biggest-consumer-apps-got | Verified (페이지 읽음) |
| 사례: Dropbox는 2007년 4월 Hacker News에 데모 영상을 올려 첫 사용자를 얻었다. Buffer는 게스트 블로그 약 150편으로 9개월에 약 10만 명("Solely through guest blogging we've acquired around 100,000 users"). Loom은 Product Hunt 출시일+1~2일에 약 3천 명. | 위와 같음 | Verified (Lenny 글에 적힌 대로. 원 사례 자체는 Secondary) |
| 팀들은 대개 한 가지 전략에 집중했고 3개를 넘긴 곳은 없었다는 요약. | 요약 글 (Lenny 요약) | Secondary |
| B2B 첫 10곳은 신뢰 순서로: 지인 네트워크 > 정밀한 콜드 아웃바운드 > 투자자 네트워크 > 커뮤니티 > 콘텐츠 > 언론 > 그냥 출시. | Lenny's: https://www.lennysnewsletter.com/p/how-to-win-your-first-10-b2b-customers?open=false | Verified |

**제품 설계에 주는 시사점**: "첫 100명"은 마케팅 자동화로 얻는 숫자가 아니다. 에이전트는 **후보 목록, 초안, 측정**을 맡고, **사람 대 사람 접촉과 최종 게시**는 창업자가 한다.

---

## 2. 자동화/AI 게시를 금지하거나 처벌하는 곳 (원문 인용)

가장 중요한 표다. Just Ship It의 "아웃박스/드라이런" 원칙(CLAUDE.md)과 정확히 맞는다. 이 채널들에는 **에이전트가 직접 게시하게 하면 안 된다.**

| 플랫폼 | 규칙 (원문) | URL | 라벨 |
|---|---|---|---|
| **Hacker News** | "Please don't put generated text in HN posts." / "For the same reason, please don't automate posting." / "Don't post generated text or AI-edited text." / "Don't solicit upvotes, comments, or submissions." / "Please don't use HN primarily for promotion." | https://news.ycombinator.com/newsguidelines.html | Verified |
| **Show HN** | "Show HN is for something you've made that other people can play with." 대상 아님: "blog posts, sign-up pages, newsletters, lists, and other reading material." "Don't post quickly-generated one-offs; anybody can do that now." "Please don't ask friends to upvote or comment." "Please make it easy for users to try your thing out, ideally without barriers such as signups or emails." "Don't post landing pages or fundraisers." | https://news.ycombinator.com/showhn.html | Verified |
| **Product Hunt** | 공식 가이드라인 원문은 **못 읽음**. 공식 포럼 글(작성자는 일반 메이커)에서 "Send unsolicited messages begging for votes or support"는 하지 말라, "Accept offers from charlatans promising votes or engagement for money"는 받지 말라, 가짜 업보트는 "can trigger Product Hunt system and your launch can be disqualified"라고 함. 제3자 요약에는 "Community Guidelines의 Do Not Spam 항목이 봇, 대량 메시지, 인위적 활동 부풀리기를 다루고 AI가 생성한 행동도 금지"하며 신고 메뉴에 AI 기여 신고 옵션이 있다고 적혀 있음. | https://www.producthunt.com/p/producthunt/dos-and-don-ts-before-the-product-hunt-launch (포럼 글) / https://launchloop.dev/blog/how-to-launch-on-product-hunt (제3자) | 포럼 글은 Verified(해당 글의 내용만). "AI 금지" 문구는 **Secondary. 공식 원문 not found** |
| **Reddit** | 공식 원문 **못 읽음**. 사이트 전체 규칙보다 서브레딧별 규칙이 더 엄격하다. 흔히 "자기 홍보는 전체 활동의 10% 이하"라는 관행이 있으나 하드 룰이 아니라 커뮤니티 관행으로 설명됨. 사례: r/cybersecurity는 "10% 미만, 홍보 대상당 주 1회 이하"를 요구. Infisical 글도 "Reddit 글의 약 10% 넘게 자기 홍보하지 말고, 먼저 카르마를 쌓아라"고 조언. | https://infisical.com/blog/how-we-got-3000-github-stars-in-2-months (Verified: 글에 그렇게 적혀 있음) / https://redship.io/blog/reddit-self-promotion-rules-2026 (Secondary) | Secondary. **봇/자동화에 대한 Reddit 공식 문구 not found** |
| **GitHub** | "inauthentic interactions, such as fake accounts and automated inauthentic activity" 금지. "rank abuse, such as automated starring or following" 금지. 즉 별(star)을 자동/구매로 늘리는 것은 약관 위반. | https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies | Verified |
| **가짜 별 연구** | 카네기멜런 등 연구: 초기 탐지 약 453만 개, 정제 후 약 310만 개의 의심 가짜 별 (27.8만 계정, 15,835 저장소). "fake stars only have a promotion effect in the short term" - 장기적으론 오히려 부담. 대부분은 단명 악성코드 저장소 홍보용. | https://arxiv.org/html/2412.13459v1 | Verified (초록과 본문 일부. 수치 중 4.53M→3.1M은 Secondary 기사로도 교차 확인) |
| **LinkedIn** | 사용자 약관 8.2: "Create a false identity on LinkedIn, misrepresent your identity" 금지. "Use bots or other unauthorized automated methods to access the Services" 금지(연락처 추가, 메시지 발송, 가짜 참여 포함). B2B 콜드 아웃리치를 자동화 도구로 하면 위반. | https://www.linkedin.com/legal/user-agreement | Verified |
| **Apple App Store** | 5.6.3 Discovery Fraud: "Manipulating any element of the App Store customer experience such as charts, search, reviews, or referrals to your app erodes customer trust and is not permitted." 5.6.1: "do not include personal information, spam, or marketing in your response" (리뷰 답글), 리뷰 요청은 제공된 API로만("we will disallow custom review prompts"). 5.6: "Repeated manipulative or misleading behavior or other fraudulent conduct will lead to your removal from the Apple Developer Program." 2.3.7: 키워드에 "trademarked terms, popular app names, pricing information, or other irrelevant phrases just to game the system" 금지. 2.2: TestFlight 베타는 "cannot be distributed to testers in exchange for compensation of any kind". | https://developer.apple.com/app-store/review/guidelines/ | Verified |
| **Google Play** | 평점/리뷰/설치 정책: "We prohibit any manipulation of ratings, reviews, or install counts." "Don't offer rewards or incentives for ratings or reviews." "Don't use automated services to inflate installs or ratings." (페이지 요약 응답으로 확인 - 문구는 도구가 인용한 것) | https://support.google.com/googleplay/android-developer/answer/9898684 | Verified (인용 문구는 fetch 도구 출력 기준. 최종 게시 전 원문 재확인 권장) |
| **Chrome Web Store** | "Developers must not attempt to manipulate the placement of any extensions in the Chrome Web Store." 금지: "inflating product ratings, reviews, or install counts by illegitimate means" "such as fraudulent or incentivized downloads, reviews and ratings." 또 "multiple extensions that provide duplicate experiences or functionality" 금지. | https://developer.chrome.com/docs/webstore/program-policies/spam-and-abuse | Verified |
| **Substack** | "We don't allow imports of email addresses that were purchased, scraped, or harvested from third party websites." "Don't add people to your mailing list without their consent." | https://substack.com/content | Verified |

### 설계 결론
1. **게시 금지 채널(에이전트가 직접 게시 불가)**: Hacker News (글/댓글 모두), Product Hunt (댓글/투표), Reddit (사실상), LinkedIn (자동 DM/연결 요청). 에이전트는 "초안 + 근거 + 올릴 시각 제안"까지만, 게시는 창업자 본인 계정에서 사람이 한다.
2. **측정은 자동화해도 되지만 "수치 조작"은 모든 스토어에서 금지**: 리뷰 보상, 구매 별, 품앗이 업보트 기능은 제품에 넣지 않는다.
3. HN은 "AI가 편집한 글"도 금지라서, "AI가 초안 쓴 걸 사람이 다듬어 올리는 것" 자체가 규칙상 회색지대다. 가장 안전한 방식은 **창업자가 자기 말로 직접 쓰는 것**이다. 에이전트는 체크리스트와 질문 목록만 준다. (이 해석은 [제안]. 규칙 문구는 Verified.)

---

## 3. 경로별 플레이북

### 3-1. 오픈소스 개발자 도구 (GitHub)

**북극성 지표(첫 100)**: GitHub 스타 100개 (오너 제시 예시와 동일).
- 문제: 스타는 허영 지표이고 가짜가 많다(위 연구). 그래서 **보조 지표 2개를 같이 센다**.
- 선행 지표 1: 주간 고유 방문자(Traffic > Unique visitors)와 README 조회 대비 클론/다운로드 비율.
- 선행 지표 2: 외부 사람이 올린 이슈/PR 수, 또는 패키지 레지스트리(npm/PyPI 등) 주간 다운로드.
- **카운팅 정의 [제안]**: "활성 사용자" = 최근 28일에 설치/실행한 고유 사용자(텔레메트리가 없다면 다운로드 수로 대체하고, 한계를 표시). 스타는 "계정 생성 후 30일 이상, 공개 활동이 있는 계정"만 의미 있는 별로 별도 표기 [제안].

**효과가 있었던 채널과 실제 사례**
- Infisical: 2022-11-21 오픈소스 공개. 당일 r/opensource, r/github, r/node 게시, 다음 날 TLDR 뉴스레터, 며칠 뒤 Console.dev, 이후 Vercel CEO의 트윗과 한국 포럼, Show HN 3위. 스타는 기존 약 90개에서 시작해 약 20주 차트상 2.9k. 글 제목은 "2 months"지만 차트는 약 20주여서 서로 어긋난다. 글은 "Hacker News는 큰 성장 채널"이라 평가하고, 채널별 스타 기여도는 **not found**. https://infisical.com/blog/how-we-got-3000-github-stars-in-2-months [Verified: 글 읽음]
- 초기 100개를 Awesome List PR과 X 공유로 얻고, 이후 Reddit 타깃 게시로 늘린 사례 (DEV 글, 요약만 봄): https://dev.to/dorianb/how-i-got-500-stars-on-my-first-open-source-project-3inb [Secondary]
- 뉴스레터 투고로 이틀에 800스타 사례: https://dev.to/quanticdev/my-project-got-800-stars-in-two-days-on-github-47in [Secondary, 요약만]
- Cockroach/Plausible가 Hacker News·Reddit·Twitter로 초기 성장을 얻었다는 목록형 글: https://github.com/octolens/first-1000-members [Secondary]
- PHPStan(Ondřej Mirtes) 3개월 0→1000 스타 글 존재: https://freek.dev/704-how-i-got-from-0-to-1-000-stars-on-github-in-three-months-with-my-open-source-side-project [제목만 확인. 전술 내용은 Unverified]
- 게시 금지 채널: HN, Reddit(위 표). 별 구매/자동 스타는 GitHub 약관 위반.

**주차별 흐름(4~8주) [제안, 위 사례 구조를 바탕으로 한 설계안]**
- 1주: README(한 문장 가치, 설치 3줄, GIF 데모), 라이선스, "good first issue" 5개, 설치가 1분 안에 되는지 외부인에게 테스트. 측정 기준선 기록.
- 2주: 소수 지인/관련 커뮤니티 5~10곳에서 직접 피드백. 설치 실패 로그 수집·수정.
- 3주: 공개 출시 준비물(데모 영상, Show HN 제목, 블로그 글 1편, 뉴스레터 투고 목록). Show HN 규칙(가입 없이 써볼 수 있어야 함)에 맞는지 점검.
- 4주(출시 주): 창업자가 직접 Show HN 올리고, 처음 몇 시간 댓글에 직접 답한다. 같은 주에 Reddit 서브레딧 규칙을 확인한 뒤 1~2곳, 뉴스레터(TLDR, Console.dev 등) 투고.
- 5~8주: 이슈에 24시간 내 응답, 릴리스 노트 주 1회, Awesome List PR, 사용 사례 글. 주간 스타·방문자·외부 이슈를 점검.

**추가 역할**
- 개발자 관계(DevRel) 에이전트: 이슈/디스커션 분류, 답변 초안, 첫 기여자 환영 메시지 초안(사람이 승인), 주간 릴리스 노트.
- 문서/예제 에이전트: README, 퀵스타트, 예제 저장소를 유지. 설치 실패율 추적.
- 커뮤니티 리서처: 적합한 서브레딧·뉴스레터·Awesome List 목록과 각 규칙 요약(게시는 안 함).

**창업자가 직접 할 일**: Show HN 게시와 댓글 응답, Reddit 게시(본인 계정, 규칙 확인), 첫 외부 기여자와의 대화, 보안 이슈 판단, 라이선스 결정.
**에이전트가 미리 준비**: README 초안, 게시 문안 초안(참고용. 단 HN에는 직접 쓴 글을 권장), 뉴스레터 투고 목록, 타이밍 제안.

**측정 데이터 소스**: GitHub REST API(stargazers, traffic, issues, releases), npm/PyPI 다운로드 통계, 선택적으로 Plausible 같은 사이트 분석.

---

### 3-2. 모바일 앱 (iOS / Google Play)

**북극성 지표(첫 100)**: 100 "활성" 설치.
- 다운로드 수만 세면 허수가 많다. **카운팅 정의 [제안]**: 활성 사용자 = 설치 후 7일 안에 핵심 행동(제품이 정하는 1가지)을 1회 이상 한 사용자. 다운로드 100이 아니라 "첫 주 리텐션 후 100".
- 선행 지표 1: 스토어 페이지 조회 → 설치 전환율(Conversion Rate).
- 선행 지표 2: 1일/7일 리텐션.

**채널과 사례**
- 앱 이름에 검색어를 넣어 스토어 검색에서 트래픽을 얻은 사례: Musical.ly(TikTok 전신)는 긴 이름으로 검색 노출. Lenny 글 [Verified]. 단 현재 Apple은 앱 이름을 30자로 제한하고 무관한 용어 끼워넣기를 금지(2.3.7). 따라서 지금 그대로 따라 하면 안 된다 [Verified: Apple 가이드라인].
- 오프라인/지인: Tinder(USC 동아리), DoorDash(스탠퍼드 전단), Snapchat(쇼핑몰 전단) [Verified: Lenny 글].
- 대기명단+희소성: Robinhood, Mailbox, Superhuman [Verified: Lenny 글].
- Product Hunt 출시: Loom 약 3천 명 [Verified: Lenny 글].
- 인디 앱 창업자 사례의 구체적 일화(ASO 후 몇 위, 몇 다운로드)는 이번에 **not found**.

**스토어 규칙에서 꼭 알아야 할 것**
- Apple 4.2: "your app should include features, content, and UI that elevate it beyond a repackaged website." 4.3(b): 이미 흔한 앱의 변형은 거절. 심사 거절이 일정을 가장 크게 흔든다 [Verified].
- Apple 2.3.3: 스크린샷은 앱이 실제 쓰이는 모습이어야 하며 타이틀/로그인/스플래시만은 안 된다 [Verified].
- Apple 베타는 TestFlight로: 테스터에게 보상 지급 금지 [Verified].
- Google Play 신규 개인 개발자 계정(2023-11-13 이후 생성): 프로덕션 신청 전에 비공개 테스트에서 "a minimum of 12 testers who have been opted in continuously for at least 14 days" 필요. **첫 출시가 최소 2주 늦어진다** https://support.google.com/googleplay/android-developer/answer/14151465 [Verified].
- 리뷰 요청은 Apple의 공식 API로만, 보상 리뷰 금지(양 스토어).

**주차별 흐름 [제안]**
- 1주: 개발자 계정 등록(Apple은 연 $99, Google은 1회 가입비. 금액은 이번에 **미확인**), 핵심 행동 1개 정의, 이벤트 로깅.
- 2주: TestFlight + Google 비공개 테스트 시작(12명 모집이 병목. 이 12명이 곧 첫 사용자 후보). 스토어 문구/스크린샷 초안.
- 3주: 베타 피드백으로 온보딩 수정. Google 14일 요건 충족 대기. 심사 제출.
- 4주: 심사 통과 시 공개. 지인/관련 커뮤니티 직접 초대, 소규모 출시 포스트(창업자 계정).
- 5~8주: 스토어 조회→설치 전환 실험(아이콘/스크린샷/제목 한 번에 하나), 리텐션 개선, 리뷰 요청 타이밍(핵심 행동 직후).

**추가 역할**
- ASO/스토어 리스팅 매니저: 키워드 조사(규칙 2.3.7 준수), 스크린샷·설명 A/B 안, 현지화 초안. 주 1회 전환율 보고.
- 리뷰/지원 에이전트: 리뷰 답글 초안(개인정보·마케팅 문구 금지, 5.6.1). 사람이 승인.
- 심사 대응 에이전트: 심사 가이드라인 체크리스트(4.2, 2.3, 개인정보), 거절 사유 분석.

**창업자가 직접 할 일**: 개발자 계정 본인 인증/결제(법적 신원, 5.6.2 개발자 신원 정확성), 심사 제출 승인, 테스터 12명 직접 모집, 리뷰 답글 최종 승인.
**준비 가능**: 스토어 문구, 스크린샷 시안, 프라이버시 질문 답변 초안, 베타 초대 문안.

**측정 데이터 소스**: App Store Connect Analytics(조회·다운로드·리텐션), Google Play Console 통계, 앱 내 이벤트 분석(제품 자체 로그).

---

### 3-3. 웹 SaaS / 웹앱 (대기명단 → 가입 → 활성화)

**북극성 지표(첫 100)**: **활성화된 가입자 100명**.
- **카운팅 정의 [제안]**: 활성화 = 가입 후 7일 안에 "아하 순간" 행동(제품이 정함, 예: 첫 프로젝트 생성+첫 결과 확인)을 완료. 가입자 수만이 아니라 이 숫자.
- 선행 지표 1: 방문자 → 대기명단/가입 전환율. 사례: 한 Chrome 확장 글에서 27%가 나왔지만(트래픽은 0에 가까움) 일반 기준값은 **not found**.
- 선행 지표 2: 가입 → 활성화 전환율. 사례(B2B): Stytch는 가입자의 약 40~50%가 데모를 예약하고 그중 약 20%가 온보딩 [Verified: Lenny B2B 글].

**채널과 사례**
- 대기명단 + 창업자 SNS: Linear는 창업자들의 트위터 팔로워로 대기명단을 만들었다. Front는 콘텐츠로 약 3천 베타 가입 [Verified: Lenny B2B 글].
- Superhuman: 간단한 랜딩 페이지에서 "현재 쓰는 이메일과 불편"을 물었다. 또 Mailbox 종료 에세이를 4일 만에 써서 "north of 5,000 signups" [Verified: Lenny 소비자 글].
- Hacker News 대기명단: Robinhood는 HN 첫 페이지 후 동시 접속 약 600 [Verified: Lenny 글].
- Canva: 약 2년간 약 1만 명 대기명단, 첫 주 수천 가입 [Verified: Lenny B2B 글].
- 게시 금지/주의: HN(Show HN은 "sign-up pages" 비대상, 가입장벽 없는 체험 요구) [Verified], Product Hunt, Reddit.

**주차별 흐름 [제안]**
- 1주: 문제 인터뷰 5~10명(지인 우선), 랜딩 페이지+대기명단, 분석 설치.
- 2주: 핵심 기능 1개 MVP, 대기명단 상위 인원에게 직접 연락해 온보딩 통화.
- 3~4주: 초대 순서 발송(희소성), 온보딩 관찰, 활성화 정의 확정.
- 5~6주: 창업자 콘텐츠 2~3편, Show HN 가능 여부 판단(가입 없이 써볼 수 있을 때만), Product Hunt는 선택.
- 7~8주: 활성화 퍼널 개선, 활성 사용자와 인터뷰해 추천/레퍼럴 설계.

**추가 역할**
- 성장/콘텐츠 에이전트: 주제 목록·초안(사람이 다듬음), SEO 기본.
- 온보딩/고객 성공 에이전트: 가입자별 상태 추적, 막힌 사용자 목록과 연락 초안.
- 분석 에이전트: 퍼널(방문→가입→활성화) 주간 리포트.

**창업자가 직접 할 일**: 사용자 통화(Stripe식 "laptop 줘봐" 수준의 직접 온보딩 [Verified: PG]), 커뮤니티 게시, 가격 결정.
**준비 가능**: 대기명단 후보 DM 초안, 인터뷰 스크립트, 퍼널 대시보드.

**측정 데이터 소스**: 제품 DB(가입·이벤트), 웹 분석(Plausible/GA/PostHog), 이메일 도구(대기명단), Stripe(결제 시).

---

### 3-4. B2B 서비스/도구 (첫 유료 파일럿 / LOI)

**북극성 지표(첫 100)**: 여기서 "100명"은 비현실적일 수 있다. 근거 있는 기준은 Lenny의 "첫 10 고객"이다 [Verified]. 제품에는 **첫 3~10개 유료 파일럿 또는 서명된 LOI**를 북극성으로 두고, "100"은 **100개 후보 계정 접촉**으로 해석할 것을 제안한다 [제안].
- **카운팅 정의 [제안]**: 파일럿 = 돈을 냈거나(소액 포함) 시작일·성공기준이 적힌 문서에 서명한 고객. 무료 체험·"관심 있어요"는 제외. (YC 요약에 "charge early, 유료가 정직한 신호"라는 취지가 있음 [Secondary].)
- 선행 지표 1: 주간 아웃리치 수 → 응답률 → 미팅 예약 수.
- 선행 지표 2: 미팅 → 파일럿 제안 전환율. 참고: Okta는 6개월간 월 15~18명의 신규 IT 담당자를 목표로 했다 [Verified: Lenny B2B 글].

**채널과 사례 (모두 Lenny B2B 글, Verified)**
- 지인 네트워크: Figma(필드의 지인, 첫 회사 Coda), Gong(첫 12곳이 창업자 지인), Census(첫 10곳 중 절반이 공동창업자 네트워크), Okta.
- 콜드 아웃바운드: Retool(Crunchbase로 운영 중심 회사를 걸러 CTO/운영 VP에 메일), Zip(첫 10곳 전부 LinkedIn 콜드, 조언 구하는 대화로 시작), Figma(스크립트로 영향력 있는 디자이너를 찾아 DM).
- 투자자 네트워크: Vanta(YC 포럼에서 "compliance"를 언급한 회사들에 메일).
- 커뮤니티: Snyk(밋업·개발자 커뮤니티·오픈소스 메인테이너), Databricks(해커톤 AMP Camps 약 70명).
- 출시: Segment(Hacker News에서 시작).
- **금지/주의**: LinkedIn 자동 DM은 약관 위반(8.2) [Verified]. 이메일 대량 발송은 스팸 법규가 있으나 이번 조사에서 국가별 법규는 **not found**(법무 확인 필요).

**주차별 흐름 [제안]**
- 1주: ICP(이상적 고객) 정의, 지인 중 해당자 20명 목록, 문제 인터뷰 일정.
- 2주: 인터뷰 8~10건 진행(창업자 직접), 반복되는 고통점 1개 확정.
- 3주: 소규모 시연/프로토타입, 후보 100개 계정 리스트 구축(에이전트).
- 4주: 개인화 이메일 20~30통/주 발송(창업자 승인 후 창업자 계정에서), 가격·파일럿 조건 초안.
- 5~6주: 시연 미팅, 파일럿 제안서(범위·기간·성공 기준·가격).
- 7~8주: 첫 파일럿/LOI 목표. 미달 시 ICP 또는 문제를 다시 정의.

**추가 역할**
- 영업 조사 에이전트: 후보 계정 조사, 담당자 찾기, 개인화 근거 메모(공개 정보만).
- 아웃리치 초안 에이전트: 이메일 초안, 후속 메일 일정. **발송은 아웃박스에서 사람이 승인**.
- 제안서/계약 에이전트: 파일럿 제안서·LOI 템플릿 초안(법률 검토는 사람).
- CRM 에이전트: 파이프라인 상태 갱신, 후속 알림.

**창업자가 직접 할 일**: 모든 고객 통화·시연, 가격 협상, 계약 서명, 신뢰 관계. 이건 자동화하면 안 되고 자동화해도 효과가 없다(Lenny 글의 핵심은 "신뢰").
**준비 가능**: 조사 메모, 이메일 초안, 제안서 틀, 통화 후 요약.

**측정 데이터 소스**: CRM(HubSpot/스프레드시트), 메일 도구 발송·응답 기록, 캘린더(미팅 수), 결제 도구(Stripe 등).

---

### 3-5. 콘텐츠 / 커뮤니티 / 뉴스레터

**북극성 지표(첫 100)**: **구독자 100명 중 "활성" 구독자 수**.
- **카운팅 정의 [제안]**: 활성 = 최근 4번 발송 중 1번 이상 열람/클릭한 확인된(더블 옵트인) 구독자. 단순 이메일 수집은 제외. (메일 클라이언트의 프라이버시 기능 때문에 오픈율은 부정확할 수 있음. 이건 일반 상식이며 이번에 출처 **not found**.)
- 선행 지표 1: 글/게시물당 신규 구독 전환율.
- 선행 지표 2: 답장/공유 수(신뢰의 신호).

**채널과 사례**
- 추천(레퍼럴) 프로그램: 공급사 자료에 따르면 Morning Brew 출신 성장 담당이 "구독자들이 친구나 동료에게 공유하는 것이 보이면 도입"을 권한다. 한 Substack 사례는 도입 후 7일 안에 1,500명으로 2배, 약 5명이 100명 이상을 데려왔다. https://sparkloop.app/blog/when-should-i-add-a-newsletter-referral-program-7 , https://referralhero.com/blog/mailchimp-newsletter-referral-program-case-study [Secondary. 모두 레퍼럴 도구 회사 글이라 이해관계가 있음]
- 게스트 블로깅: Buffer 약 10만 명/9개월 (위 Lenny 글 인용) [Verified: 그 글에 적힌 대로].
- 출시 전 커뮤니티: Stack Overflow는 창업자들의 기존 블로그 독자를 비공개 베타에 초대 [Verified: Lenny 글].
- 구독자 100명 달성 개인 글 사례는 있으나(예: https://michaeldwarden.substack.com/p/we-just-broke-100-subscribers) 방법 세부는 **not found** [제목만 확인].
- **주의**: Substack은 구매/스크랩한 이메일 목록 가져오기와 동의 없는 추가를 금지 [Verified]. 지인 이메일을 동의 없이 일괄 등록하면 안 된다.
- 게시 금지 채널: HN/Reddit/LinkedIn 자동화.

**주차별 흐름 [제안]**
- 1주: 독자 1명(구체적인 사람)과 주제 선택, 플랫폼 선택(Substack/beehiiv 등), 웰컴 메일.
- 2주: 첫 3편 비축 후 지인·관련 커뮤니티에 직접 초대(동의 기반).
- 3~4주: 주 1회 발송 리듬, 글마다 구독 전환율 기록.
- 5~6주: 가장 반응 좋은 주제 2배로, 다른 뉴스레터와 상호 추천 제안(직접 연락).
- 7~8주: 추천 프로그램 검토(구독자가 공유하기 시작했을 때), 100 활성 구독자 평가.

**추가 역할**
- 에디터/리서치 에이전트: 주제 조사, 소스 링크, 초안(최종 글은 창업자 목소리로 사람이 다듬음).
- 커뮤니티 매니저 에이전트: 질문 모음, 회신 초안, 활동 요약(커뮤니티 있는 경우).
- 분석 에이전트: 글별 구독/열람 보고.

**창업자가 직접 할 일**: 글의 관점과 목소리, 독자 회신, 상호 추천 제안, 커뮤니티 운영자와의 관계. "누가 쓰는가"가 상품이다.
**준비 가능**: 주제 후보, 자료 정리, 발송 일정, 구독 전환 지표 리포트.

**측정 데이터 소스**: 뉴스레터 플랫폼 통계(Substack/beehiiv/Mailchimp 등의 구독자·열람·클릭), 웹 분석, 소셜 플랫폼 인사이트.

---

### 3-6. 마켓플레이스 확장 프로그램/플러그인 (Chrome Web Store, Figma, VS Code) - 짧게

**북극성 지표(첫 100)**: 주간 활성 사용자(WAU) 100 (Chrome Web Store 대시보드가 제공하는 숫자 기준).
- **카운팅 정의 [제안]**: 설치 수가 아니라 최근 7일 안에 실제 실행한 사용자. 설치 후 제거 비율을 같이 본다.
- 선행 지표: 스토어 페이지 조회 → 설치 전환율. 사례: PasteSafe 창업자 글에서 첫 약 100 조회 후 설치 30건, 약 27% (사례 1건, 일반 기준 아님) [Secondary: IH 글 요약].

**채널과 사례 (모두 Indie Hackers 등 개인 글 요약, Secondary)**
- 스토어 검색이 첫 유입: 한 창업자는 첫 사용자가 Chrome Web Store 검색에서 왔고 유료 광고는 키워드가 작아 효과 없었다고 함.
- ViralSort: 홍보 없이 30명, 약 25일에 114명, 이후 Product Hunt. https://www.indiehackers.com/post/got-my-first-100-users-in-25-days-3fa8f934c1
- Chestr: Reddit 서브레딧과 Product Hunt로 약 300 설치.
- 한 개인 글 주장: "Chrome Web Store가 WAU를 주요 순위 신호로 쓴다"는 것은 **작성자 한 명의 주장 [Unverified]**, 공식 근거 not found.
- 한 PDF 하이라이터 개발자는 한 달 뒤 사용자 4명(본인·형제·어머니·친구)이었다가 이후 10만 명으로 성장했다고 함 [Secondary]. 첫 100이 느린 것이 정상일 수 있다는 참고 사례.
- 가짜 리뷰 판매 제안을 받은 개발자 글이 있음 [Secondary]: 스토어 정책상 금지(위 표).
- Figma / VS Code 마켓플레이스 사례와 정책: **not found** (미조사).

**스토어 규칙**: 확장은 단일 목적이어야 한다("we require Chrome extensions to have a single purpose.", 위반 시 "will be removed"), 목적과 무관한 과도한 권한도 위반 https://developer.chrome.com/docs/webstore/program-policies/quality-guidelines-faq [Verified]. 심사 소요 시간은 문서에 **not found**.

**주차별 흐름 [제안]**
- 1주: 단일 목적 확정, 권한 최소화, MVP.
- 2주: 스토어 리스팅(제목·스크린샷·설명, 검색어가 자연스럽게 들어가게), 심사 제출.
- 3주: 승인 후 지인·관련 커뮤니티에 직접 공유, 첫 리뷰 요청(보상 없이).
- 4~8주: 설치→제거율, WAU 모니터링, 리스팅 전환율 실험, 사용 사례 글/영상 1~2편.

**추가 역할**: 스토어 리스팅/ASO 매니저(모바일과 같은 역할 재사용), 지원 에이전트(리뷰·이슈 응답 초안).
**창업자 직접**: 개발자 계정 본인 인증, 권한 정당화 문구 승인, 커뮤니티 게시.
**데이터 소스**: Chrome Web Store 개발자 대시보드(설치·WAU·조회), 확장 내 이벤트 로그(개인정보 정책 준수 필요).

---

## 4. 경로별 요약표 (제품 설계용)

| 경로 | 북극성(첫 100) | 보조 지표 | 핵심 채널 | 추가 역할 | 게시 금지 주의 |
|---|---|---|---|---|---|
| 오픈소스 | GitHub 스타 100 (+실사용 지표 병행) | 방문자, 외부 이슈/PR, 패키지 다운로드 | Show HN, Reddit, 개발자 뉴스레터, Awesome List | DevRel, 문서/예제, 커뮤니티 리서처 | HN, Reddit, GitHub 별 조작 |
| 모바일 앱 | 7일 내 핵심 행동 한 활성 설치 100 | 스토어 전환율, D1/D7 리텐션 | 지인/베타, 스토어 검색(ASO), 오프라인, Product Hunt | ASO 매니저, 리뷰/지원, 심사 대응 | 리뷰/차트 조작, 보상 리뷰, Product Hunt 투표 |
| 웹 SaaS | 활성화 가입자 100 | 방문→가입, 가입→활성화 전환 | 대기명단+창업자 SNS, 콘텐츠, Show HN(가입장벽 없을 때) | 성장/콘텐츠, 온보딩, 분석 | HN, Product Hunt |
| B2B | 첫 3~10 유료 파일럿/LOI (+접촉 100계정) | 아웃리치→응답→미팅→제안 | 지인, 정밀 콜드, 투자자 네트워크, 커뮤니티 | 영업 조사, 아웃리치 초안, 제안서, CRM | LinkedIn 자동화, 대량 스팸 |
| 콘텐츠/뉴스레터 | 활성 구독자 100 | 글당 구독 전환, 회신/공유 | 직접 초대, 상호 추천, 레퍼럴, 게스트 글 | 에디터/리서치, 커뮤니티 매니저 | 구매/스크랩 이메일 목록 금지 (Substack) |
| 확장/플러그인 | WAU 100 | 조회→설치, 제거율 | 스토어 검색, Reddit, Product Hunt | 리스팅/ASO, 지원 | 리뷰/설치 조작 (Chrome Web Store) |

## 5. 공통 설계 제안 [제안]

1. 모든 경로에 공통인 코어 역할(CEO, 리서치, 제품, 엔지니어링, 성장) 위에, 경로별로 위 "추가 역할"만 켠다.
2. 외부에 나가는 모든 게시/메일/DM은 **아웃박스 → 오너 승인 → 오너 계정에서 수동 게시**. HN, Product Hunt, Reddit, LinkedIn은 아예 "초안 전용" 플래그를 둔다.
3. 북극성 지표 카드에 **카운팅 정의**를 화면에 항상 보여준다(예: "활성 = 7일 내 핵심 행동 1회"). 스타/다운로드 같은 허영 지표는 보조 지표와 함께 표시한다.
4. 제품이 스토어 수치 조작(리뷰 보상, 구매 별, 품앗이 투표)을 제안하지 않도록 금지 목록으로 하드코딩한다.
5. 주차별 흐름은 "사례 기반 가설"이다. 4주 안에 지표가 안 움직이면 채널이 아니라 **문제/ICP를 의심**하라는 경고를 넣는다(Lenny/PG: 직접 만나 배우는 것이 먼저).

## 6. 출처 목록

- Hacker News 가이드라인: https://news.ycombinator.com/newsguidelines.html (Verified)
- Show HN 규칙: https://news.ycombinator.com/showhn.html (Verified)
- Apple App Review Guidelines: https://developer.apple.com/app-store/review/guidelines/ (Verified)
- Google Play 평점/리뷰/설치 정책: https://support.google.com/googleplay/android-developer/answer/9898684 (Verified)
- Google Play 신규 개인 계정 테스트 요건: https://support.google.com/googleplay/android-developer/answer/14151465 (Verified)
- Chrome Web Store 스팸 정책: https://developer.chrome.com/docs/webstore/program-policies/spam-and-abuse (Verified)
- Chrome Web Store 품질 가이드 FAQ: https://developer.chrome.com/docs/webstore/program-policies/quality-guidelines-faq (Verified)
- GitHub Acceptable Use Policies: https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies (Verified)
- LinkedIn User Agreement: https://www.linkedin.com/legal/user-agreement (Verified)
- Substack Content Guidelines: https://substack.com/content (Verified)
- 4.5M 의심 가짜 별 연구: https://arxiv.org/html/2412.13459v1 (Verified)
- Product Hunt 포럼 Dos and Don'ts: https://www.producthunt.com/p/producthunt/dos-and-don-ts-before-the-product-hunt-launch (Verified, 단 일반 메이커 글)
- Product Hunt 가이드 제3자 요약: https://launchloop.dev/blog/how-to-launch-on-product-hunt (Secondary)
- Lenny's, 소비자 앱 첫 1,000명: https://www.lennysnewsletter.com/p/how-the-biggest-consumer-apps-got (Verified)
- Lenny's, B2B 첫 10 고객: https://www.lennysnewsletter.com/p/how-to-win-your-first-10-b2b-customers?open=false (Verified)
- Paul Graham, Do Things That Don't Scale: https://paulgraham.com/ds.html (Verified)
- Infisical, GitHub 스타 사례: https://infisical.com/blog/how-we-got-3000-github-stars-in-2-months (Verified)
- DEV 500스타 / 800스타 글: https://dev.to/dorianb/how-i-got-500-stars-on-my-first-open-source-project-3inb , https://dev.to/quanticdev/my-project-got-800-stars-in-two-days-on-github-47in (Secondary)
- Indie Hackers Chrome 확장 사례: https://www.indiehackers.com/post/got-my-first-100-users-in-25-days-3fa8f934c1 (Secondary)
- 뉴스레터 레퍼럴 글: https://sparkloop.app/blog/when-should-i-add-a-newsletter-referral-program-7 , https://referralhero.com/blog/mailchimp-newsletter-referral-program-case-study (Secondary, 판매사 이해관계)
- Reddit 자기 홍보 관행 요약: https://redship.io/blog/reddit-self-promotion-rules-2026 (Secondary)
- YC 강연 요약(Gustaf Alstromer, Michael Seibel): 원문 영상 **not found**. 검색 요약만 확인 (Secondary)

## 7. 아직 못 한 것 (후속 조사 후보)
1. Reddit 공식 콘텐츠 정책·봇 정책 원문, Product Hunt 공식 가이드라인 원문 (접근 차단/404).
2. Figma 플러그인, VS Code 마켓플레이스 정책과 사례.
3. 모바일 앱 인디 창업자의 실제 첫 100 다운로드 사례(ASO 구체 수치).
4. 이메일 콜드 아웃리치 관련 국가별 법규(한국 정보통신망법 등).
5. 개발자 계정 가입 비용(Apple $99/연, Google 가입비) - 이번에 미확인.
