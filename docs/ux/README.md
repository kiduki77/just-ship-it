# 콘솔 UX 재설계

Just Ship It 콘솔은 기능이 생길 때마다 화면을 덧붙여 만들어졌습니다. 이 폴더는 콘솔을 다시 설계하는 산출물을 담고,
작업 순서는 **설계를 먼저 고정하고 구현은 그다음**입니다. 오너가 승인하기 전에는 `web/src` 코드를 고치지 않습니다.

## 단계와 산출물

| 단계 | 산출물 | 상태 |
|---|---|---|
| 0. 현황 분석 | [01-current-state.md](01-current-state.md): 화면, 사용자 작업, 상태 전이, 화면 관계, 실측 문제 | 초안 |
| 1. UX 구조 | [02-ux-structure.md](02-ux-structure.md): 역할, 핵심 업무 시나리오, 동선, 정보 구조(IA), 내비게이션 | 초안 |
| 2. 화면·인터랙션 | [03-screens.md](03-screens.md): 화면 목록, 화면별 내용 우선순위, 상태(로딩·빈·오류·끊김), 인터랙션 규칙 · [wireframes/](wireframes/) ([캔버스](https://claude.ai/artifact/JxrpqqdcKFsjTaPUajhJp9)) | 초안 |
| 3. 디자인 시스템 | [04-design-system.md](04-design-system.md): 토큰, 타이포, 간격, 색 역할, 컴포넌트, 문구 규칙 | 초안 |
| 4. 구현·검증 | [05-validation.md](05-validation.md): 시나리오 측정 기준, 일관성 검사, 구현 순서, 백엔드 의존성 | 초안 |
| 5. Paperclip 수준 | [06-paperclip-parity.md](06-paperclip-parity.md): 기능 비교, 구현한 것과 뺀 것 | 구현됨 (`feat/console-v2`) |
| 6. 로드맵 | [07-roadmap.md](07-roadmap.md): 시작 인터뷰, 사업 방식, 사다리·줄·도전·판, 쌓이는 보고서 · [캔버스](https://claude.ai/artifact/FkF7D7XDRMkw9LqD6mCRJo) · [research/](research/) | 초안, 오너 검토 대기 |
| 7. 첫 100명 | [08-first-100.md](08-first-100.md): 출발 경로별 플레이북과 조직 조립, 시작 대화와 업무 지시서, 밖으로 나가는 일의 규칙, 설정 화면 · [research/launch-paths.md](research/launch-paths.md) · [research/integrations.md](research/integrations.md) | 초안. 오너 결정 1~8 반영, CLAUDE.md 반영됨 |
| 결정 기록 | [decisions.md](decisions.md): 설계 결정과 이유, 오너 확인이 필요한 항목 | 열림 |

**승인 게이트.** 오너가 01–05와 와이어프레임을 승인하면 04를 루트의 `DESIGN.md`로 옮깁니다. 그 뒤에는 impeccable과
설계 리뷰가 화면마다 이 규칙을 기준으로 삼고, 구현은 05의 순서대로 `ui/…` 브랜치에서 합니다.

## 도구 선택

외부 설계 프레임워크 네 개를 2026-11-01에 직접 확인했습니다. 아무것도 설치하지 않았습니다.

| 저장소 | 실체 | 이 프로젝트에 대한 판단 |
|---|---|---|
| buildermethods/design-os (MIT, 약 1.9k★) | 별도로 띄우는 Vite 웹앱입니다. 기획 → 디자인 시스템 → 섹션 설계 → 내보내기 순서로 진행합니다. | 새 제품을 처음 기획할 때 맞는 도구입니다. 이미 돌아가는 콘솔을 재설계하는 데는 별도 앱이 하나 더 생기는 셈이라 채택하지 않았습니다. |
| denysosadchyi/design-spec-framework (MIT, 37★) | Claude Code 템플릿 저장소입니다. `/dsf:*` 명령 11단계로 구성되고, 단계마다 검증 게이트와 git 태그가 있습니다. | 저장소 루트를 통째로 템플릿으로 쓰는 구조여서 이 저장소와 충돌합니다. 대신 **산출물 구성**(sitemap, flows, wireframes, microcopy, DESIGN.md, decisions)과 **단계별 게이트**를 그대로 차용했습니다. |
| ghaida/intent (CC0, 233★) | Claude Code 플러그인입니다(`/journey`, `/organize`, `/wireframe` 등 스킬 18개). | 쓸 만합니다. 다만 설치하려면 `npx`로 외부 코드를 받아 실행해야 해서 오너 허락이 필요합니다. 필요하면 다음 단계에서 프로젝트 한정으로 설치합니다. |
| dragonoasis/open-design-agent (MIT, 0★) | Copilot CLI용 마크다운 스킬 모음입니다. | 검증 사례가 없어 참고만 합니다. |

이미 설치된 도구는 이렇게 씁니다.
- **impeccable**: PRODUCT.md, `shape`, `critique`, `audit`, 디자인 감지 훅.
- **pstack**: `plan-design-review`, `qa`, 헤드리스 브라우저 측정.
- **PatternFly 패턴**: 운영 콘솔의 참고 기준입니다. 상태·심각도, 필터, 기본-상세(primary-detail), 대시보드, 빈 상태, 오래된 데이터 경고 패턴을 씁니다.

## 범위

- 대상은 웹 콘솔 전체(`web/src`)입니다. 백엔드 데이터 모델은 유지하고, 화면에 꼭 필요한 조회·조작 API만 추가합니다. 추가할 API는 05에 정리했습니다.
- 사용자 검증은 이 문서들로 끝나지 않습니다. 실제 사용 시나리오에서 결정 시간, 클릭 수, 오판 횟수를 재야 합니다. 측정 방법은 05에 있습니다.
