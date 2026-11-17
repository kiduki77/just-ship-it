# 개발 안내

[English](DEVELOPMENT.md)

Just Ship It 저장소 자체를 고치는 방법입니다. 작업 약속(지켜야 할 제약, 브랜치 규칙, 어떤 pstack 스킬을 언제 쓰는지)은
[CLAUDE.md](../CLAUDE.md)에 있습니다.

## 이 저장소용 Claude Code 준비

개발에는 Lauren Tan의 스킬 묶음인 pstack을 Claude Code 플러그인 `pstack@cursor-plugins`로 씁니다. Claude Code에서
`/plugin marketplace add cursor/plugins`, 이어서 `/plugin install pstack@cursor-plugins`를 실행하고,
변경은 `/pstack:poteto-mode`로 시작하세요.

회사가 제품을 만들 때 쓰는 엔지니어 컨테이너는 따로입니다. 그 이미지는 `michael-denyer/pstack-claude`의 고정 커밋을
설치합니다([운영 안내](OPERATIONS.ko.md#엔지니어-컨테이너는-내-구독으로-로그인합니다)).

## 콘솔 고치기

웹 콘솔을 수정했거나 새로 받았다면 다시 빌드합니다.

```sh
cd web && npm install && npm run build
```

개발 모드는 `web/`에서 `npm run dev`입니다. :5173에서 열리고 /api는 :8000으로 프록시합니다.

## Windows에서 테스트 돌리기

Windows 호스트에서는 `uv run pytest -q tests` 전체 실행을 믿을 수 없습니다. 테스트가 Linux를 전제로 쓰였기 때문입니다.
`test_worker_recovery.py`는 `os.kill(os.getpid(), SIGTERM)`으로 pytest 자신을 죽이고, `os.fchmod`와 `git init`,
`#!/bin/sh` 가짜 `claude`/`docker` 스크립트, `https_proxy` 대소문자도 Windows에서 다르게 동작합니다. 그래서 전체 실행은
Linux 컨테이너에서 하고, 호스트에서는 한두 파일만 돌립니다.

```bash
docker compose up -d                                  # Postgres
scripts/test-linux.sh                                 # 지금 작업 트리 전체 (커밋 안 한 변경 포함)
scripts/test-linux.sh <커밋 또는 worktree 경로>          # 그 커밋만 (git archive로 내보내 실행)
scripts/test-linux.sh --shards 1 <커밋>                  # 컨테이너 하나로 (기본은 3개로 나눠 병렬)
scripts/test-linux.sh <커밋> tests/test_queue_lease.py -k expiry   # 이후 인자는 pytest로
scripts/test-linux-diff.sh [--shards K] <base> <head>   # PR 게이트: head에서만 새로 실패한 테스트 목록
```

스크립트는 소스를 컨테이너로 스트리밍하므로 호스트 `.venv`, `.env`, 파일 잠금이 섞이지 않습니다. 실행마다 테스트 DB를
따로 쓰고, 컨테이너 안의 127.0.0.1:55432를 compose Postgres로 이어 주므로 어떤 커밋의 `conftest.py`도 그대로 돌아갑니다.
Redis를 없애기 전 커밋은 Redis 테스트가 실패합니다.
끝에 통과·실패·오류·건너뜀 수와 실패한 테스트 id를 출력하고, 결과(`junit.xml`, `failures.tsv`)는 `.test-linux/`에 남습니다.

`test-linux-diff.sh`는 base와 head를 함께 돌려 NEW(head만 실패), FIXED, PRE-EXISTING으로 나눕니다. NEW로 잡힌 테스트는 head에서
단독으로 다시 돌려, 통과하면 부하 때문에 흔들린 것(LOAD SENSITIVE)으로 따로 보여 줍니다. 컨테이너 이미지는
`scripts/test-linux/Dockerfile`(Python 3.13, git 2.4x 이상, uv, docker CLI)이며 처음 한 번만 의존성을 받습니다.

전체 실행은 기본으로 테스트 파일을 K=3개 컨테이너에 나눠 돌립니다(`--shards K` 또는 `SHIP_TEST_SHARDS`, `--shards 1`이면 하나).
조각마다 테스트 DB가 따로이고, 파일은 겹치지 않습니다. 가장 최근 전체 실행의 `.test-linux/**/junit.xml`에서 파일별
시간을 읽어 균형을 맞추고(없으면 파일 수), 끝에 junit과 `failures.tsv`를 합쳐 RESULT를 한 줄로 냅니다. pytest 인자를 주면 나누지
않습니다. 프로세스 안 병렬(xdist)은 쓰지 않습니다. 테스트가 모듈 전역 상태와 큐, var 디렉터리를 공유하기 때문입니다.

`scripts/dev_pipeline_smoke.py`는 [TEST] 프로젝트로 개발 파이프라인을 처음부터 끝까지 확인합니다.

## 목표가 코드를 지나가는 흐름

```
Owner goal ─► CEO plan(DAG) ─► [plan gate: blocking 질문] ─► 병렬 업무 실행
   ─► 업무마다 작성자와 다른 역할의 독립 검토(모두 Claude) ─► 재작업(최대 N회)
   ─► CEO 검토 ─┬─ 이견 → 회의(입장·반박·결정 기록) ─► 후속 업무
               ├─ 미충족 기준 → 후속 업무 라운드(최대 N회)
               └─ 완료 → 최종 의사결정 ─► [final gate: 승인] ─► 최종 보고서
   ─► 개발 필요 → CPO가 기획안(plan.md) 작성 ─► (승인, dev.plan_approve) 기획안 커밋 고정
      ─► 그 기획안으로 CPO가 명세 작성 ─► (승인) 명세 커밋 고정
   ─► 개발팀: intake → pstack:architect → 구현/테스트 → pstack:thermo-nuclear-review + 독립 리뷰
      → 수정 루프 → 가드된 ship(PR 또는 로컬 브랜치) → CPO 수용 검수 → (강한 승인) 머지
```

각 단계와 그 이유는 [ARCHITECTURE.md](ARCHITECTURE.md)에 있습니다.

## 디렉터리

```
backend/justshipit/
  contracts/      # 기술 계약: work(업무·근거), requests(Agent Request Protocol), handoff(Spec→Code), skills, planning
  runtime/        # claude -p 어댑터 (구조화 출력, 격리, 재시도, 동시성)
  agents/         # 에이전트 실행기 (프롬프트 합성, 예산, 원장)
  orchestration/  # LangGraph 회사 그래프, 컨텍스트 빌더, 실행/재개
  services/       # policy, requests(승인), budget, queue(작업 큐), tasks(DAG), evidence, finance, registry
  engineering/    # specs(PRD·승인·고정), worker(pstack 플러그인 개발 파이프라인, 수용 검수, 머지)
  capability/     # Skill Factory (gap → proposal → package → safety → replay A/B → promotion → injection)
  operations/     # scheduler, gateway(connectors), jobs(change watch, KPI review, portfolio review, reassess)
  workers/        # 잡 워커
  api/ · cli.py
config/           # company.yaml(한도·자율성), agents.yaml(조직·모델·도구), tool_policy.yaml
prompts/          # 공통 원칙 + 역할별 프롬프트 (버전 해시가 호출 원장에 기록됨)
registry/         # (별도 git) 계획·업무 산출물·회의록·보고서·명세·스킬
products/         # (각각 별도 git) 개발 대상 저장소
web/              # React 콘솔
scripts/          # dev_pipeline_smoke.py와 Linux 테스트 실행기
var/calls/        # 호출별 원문(프롬프트·출력), 감사용, gitignore
var/outbox/       # 외부 실행 보관함 (보내지 않은 메일 .eml, 일정 .ics, 설문 패키지)
```
