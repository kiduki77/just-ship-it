# Korean style

How to write the Korean the owner reads: console copy, server sentences, and what the agents write. Read this
whole page before you write a Korean string. The goal is copy a Korean founder reads as written by a Korean, in the
tone of 토스 or 당근: 해요체, short, concrete, active.

`tests/test_korean_style.py` fails on the most mechanical patterns below in every ko catalog. It is a guard, not a
judge. Passing it does not make a sentence natural.

## Rules

1. **해요체, always.** Every sentence the owner reads ends in 해요체: "저장했어요", "기다려요", "골라 주세요". No
   합니다체 (습니다, 습니까, 십시오), no 해라체, no mix within a screen. Labels, badges, column heads and table cells
   are noun phrases ("완료", "결정 대기"). Anything that reads as a sentence, such as an alert, a toast, an empty
   state or an error, is 해요체.
2. **Conclusion first.** Open with what happened or what to do. The reason follows, in the same sentence or the next.
   "예산이 모자라 개발이 멈췄어요. 예산을 늘리면 이어서 해요."
3. **Active, with a real subject.** Say who does it: the company, the CEO, an agent, or the owner. "저장되었습니다" →
   "저장했어요". "승인이 기록되고" → "승인을 기록하고". Use a passive only when the doer does not matter, as in
   "되돌릴 수 없는 일은 결정으로 와요".
4. **Short and concrete.** One idea per sentence. Name the thing ("결정 3건", "$8.20") rather than a category ("관련
   항목"). Write the way people talk: "가진 자료" → "기획서나 메모".
5. **Drop the subject when it is obvious.** Korean leaves out the person. "오너님이 할 일은 없어요" → "지금 할 일은
   없어요".
6. **Use the glossary's words.** `backend/justshipit/i18n/glossary.py` names one word per idea. Never use a retired word
   (지시서, 도전, 결정함, 오너 할 일, 첫 가치).
7. **Write English as English.** The en catalog is natural English written for an English reader. It is not a
   translation of the Korean, and the Korean is not a translation of the English. Write each from the meaning.

## 번역체 patterns

Each row is a pattern that reads like translation, a bad example, and a fix. "Lint" marks the ones the test fails on.

| Pattern | Bad | Good | Lint |
|---|---|---|---|
| ~에 대해 / ~에 대한 ("about") | 계획에 대해 질문이 있어요 | 계획에서 궁금한 게 있어요 | over 2% of messages |
| ~을 통해 ("through") | 대시보드를 통해 확인할 수 있어요 | 대시보드에서 볼 수 있어요 | yes |
| ~의 경우 ("in the case of") | 실패의 경우 다시 시도해요 | 실패하면 다시 시도해요 | yes |
| ~하는 것이 가능하다 / 할 수 있습니다 | 수정하는 것이 가능합니다 | 고칠 수 있어요 | yes |
| ~되어지다, ~되었습니다 (double or stiff passive) | 저장되어졌습니다 | 저장했어요 | yes (되어지, 되었, 하였) |
| ~에 의해 ("by") | CEO에 의해 승인됐어요 | CEO가 승인했어요 | yes |
| Noun stacking (~의 ~의, ~함, ~하기 위한 ~) | 명세 작성 작업의 대기 | 명세를 쓸 차례를 기다려요 | no |
| ~를 가지다 / 갖고 있다 ("have") | 권한을 갖고 있어요 | 권한이 있어요 | yes (을/를 가지고·갖고 있) |
| 그것, 이것, 그들 (pronouns) | 그것을 승인하면 | 승인하면 | yes (그것, 그들) |
| ~들 on every plural | 에이전트들이 업무들을 해요 | 에이전트가 업무를 해요 | no |
| ~에 있어서 ("in terms of") | 개발에 있어서 중요한 점 | 개발할 때 중요한 점 | yes |
| ~하도록 하겠습니다 | 확인하도록 하겠습니다 | 확인할게요 | yes |
| English order: subject first, long modifiers | 오너님이 승인해야 하는, 지금 기다리고 있는 결정 3건 | 결정 3건이 기다려요 | no |
| ~하세요 where a request fits | 토큰을 다시 넣으세요 | 토큰을 다시 넣어 주세요 | no |
| Calqued UI words (성공적으로, 완료되었습니다) | 성공적으로 저장되었습니다 | 저장했어요 | yes (성공적으로) |
| 당신 for "you" | 당신의 사업 | 사업 / 오너님의 사업 | yes |
| 합니다체 in the console | 예산이 부족합니다 | 예산이 모자라요 | yes (습니다, 습니까, 십시오) |
| 음슴체 inside 해요체 copy | (근거: 오너님이 직접 봤음) | · 직접 본 일 | yes (a past tense + 음; 있음 stays a label) |
| A particle after a Latin value | 마지막으로 CEO이(가) 일했어요 | 마지막 실행은 5분 전, CEO 에이전트예요 | yes (`test_i18n_particles.py`) |

A justified exception goes in `ALLOW` in `tests/test_korean_style.py` with its reason. Keep that list short.

## Addressing the owner

Write without an address by default. Korean drops the person, and a screen full of "오너님" reads like a form
letter. Say **오너님** only when the sentence must say who acts, usually to set the owner apart from the company or
an agent: "결제는 오너님만 할 수 있어요", "CEO가 정하고 오너님께 알려요". Never 당신, 사용자님 or 고객님. In
the kickoff chat the CEO speaks to the owner, so "오너님" can open a question there, at most once per message.

A person's name takes 님 after a space: "박은주 님과 이야기하기", "정대현 님에게 다시 연락하기". A title or role
takes it with no space: 오너님, 사장님, 실장님.

## Numbers and units

- Arabic numerals, no space before a counter: 결정 3건, 업무 7개, 에이전트 2명, 3번, 10분, 82%.
- Counters: **건** for decisions, alerts and records; **개** for tasks, items and posts; **명** for people and
  agents; **번** for times. People are never 건.
- Put the noun before the number: "결정 3건이 기다려요", not "3건의 결정이 기다리고 있어요".
- Money: USD as `$8.20` (the console's costs are in dollars). Won as "3만 원", "10,700원".
- Dates and times come from `locale()`. In copy: "11월 6일까지", "오늘까지", "약 23분", "45분 전".
- A range uses a tilde: "1~40자", "10~20분".

## Buttons and links

- A button is the action as a verb: "승인", "보내기", "다시 시도", "예산 늘리고 재개". Avoid a bare "확인" when a
  more specific verb exists.
- Keep the button's verb in the result: "보내기" → "보냈어요", "다시 시도" → "다시 대기열에 넣었어요".
- A link says where it goes: "결정하러 가기", "내 할 일 보기".

## Errors

An error says what happened and what to do next, in that order, in 해요체. It never blames the owner and never
says "오류가 발생했습니다".

- Bad: "요청 처리 중 오류가 발생했습니다."
- Good: "결정을 저장하지 못했어요. 잠시 뒤 다시 시도해 주세요."
- A code or status the owner may need goes last, in parentheses: "서버가 요청을 처리하지 못했어요(502)."

## English terms

- Keep names as their owners write them: Slack, GitHub, Claude, Gmail. Keep code, commands, IDs and file paths as
  written: `uv run ship worker`, REQ-12, PR.
- Keep a technical word in English or as a loanword only when the owner meets it in that form in another tool and
  a Korean word would be less clear: PR, 머지, 브랜치, 토큰.
- Translate jargon that has a plain Korean word: 에스컬레이션 → 판단 요청, 명확화 → 확인 질문, 워크플로 → 일하는
  순서, KPI or OKR in a sentence → 목표 숫자.
- Common loanwords stay in Hangul: 대시보드, 프로젝트, 에이전트, 메모.
- After a value that ends in a Latin letter, put a fixed word between it and the particle ("{cmd} 명령을"), or use
  `{name|을}` and accept "을(를)".

## AI output

Every agent call ends with the output-language rule (`justshipit.i18n.prompting.output_rule`). For Korean it adds a short
style line that follows this page: 해요체 for owner-facing text, conclusion first, active voice, no 번역체. JSON keys,
enum values (a `verdict` such as `approve`), code and ids stay exactly as the schema gives them.

The model copies the prompt's own Korean. A prompt full of 번역체 or retired words produces cards full of them,
whatever the rule says. Every lane checks its prompts and its contracts' `Field(description=)` text against this
page when it converts them.

A message the owner sends to someone else (a KakaoTalk note to a buyer, an email to a partner) keeps one register
from its first sentence to its last: 해요체, or 합니다체 throughout for a formal business contact. "오랜만에
연락드립니다. 이준호예요." mixes them. The server checks every drafted message and post body for both endings
(`i18n/match/handout.py`, set phrases such as 안녕하세요 and 감사합니다 aside) and sends a mixed one back to its author
before the owner sees it (`owner_tasks.author_problems`).

Instructions in a prompt can stay in 해라체 ("근거를 단다"). That is how Korean writes instructions, and the
rule tells the model which register the owner gets. Examples written in the owner's voice must be the 해요체 the
owner should read.

Prompt files still to fix, worst first. The counts are the lint's patterns (합니다체 left out, since instructions
are 해라체) and retired words, measured on this branch. Almost every agent prompt also opens with "당신은 ...다",
the English "You are"; "너는" or no address at all reads as Korean.

| File | Lane | Found |
|---|---|---|
| `prompts/idea_triage.md` | kickoff | 지시서 11, 도전 10, 당신 6 |
| `prompts/bet_retro.md` | business | 도전 12, 첫 가치 5, 지시서 2, 되었 1 |
| `prompts/kickoff.md` | kickoff | 지시서 9, 첫 가치 2, 그것 2, 도전 1, 당신 1 |
| `prompts/promotion.md` | business | 지시서 3, 도전 3 |
| `prompts/agents/critic.md` | kickoff | 당신 4, 그것 1 |
| `prompts/door_rules.md` | decide | 지시서 2 |
| `prompts/agents/product.md` | build | 지시서 1, 당신 1 |
| `contracts/requests.py` | decide | the `why` example the model copies says 지시서 |

`company_preamble.md` (지시서 4, 오너 할 일 2, 도전 1, 당신 2) and `agents/ceo.md` (지시서 8, 오너 할 일 1, 당신 1)
are fixed on this branch. A task now cites a plan line as `(l-ab12cd)`, which `contracts.owner_text` strips from
owner text like the old `(지시서 l-ab12cd)`. The work lane's agent prompts (analyst, finance, marketing, research, risk,
strategy) are English now, and so is the context every agent reads (`orchestration/context.py`): it heads the plan
"Plan" and the evidence "Evidence list", the glossary's words, and `ceo.md` names those headings.

## Glossary choices

These Korean terms changed on this branch. The old word is not retired, because nothing shipped with it.

| Term | Was | Now | Why |
|---|---|---|---|
| People list | 사람 명단 | 명단 | "명단" already means a list of people. The activity labels already say 명단. |
| Got value | 도움 받음 | 도움 됨 | "도움 됐어요" is how people say a product helped. "도움 받음" sounds like receiving aid. |
| Came back | 다시 씀 | 재사용 | "다시 씀" also reads as "rewrote". 재사용 is what a founder says. |
| Steps to first users | 첫 사용자까지 | 사용자가 되기까지 | "첫 사용자까지" is a fragment. The new title says what the stages track. |
| Outbox | outbox | 보관함 | Korean screens said "outbox" untranslated. 보관함 is what it is: things kept, not sent. "outbox" is retired in Korean. |

Kept on purpose: 시작 대화 (a "첫 대화" would collide with the people list's 대화함 stage), 개발 작업 (natural, and
J-n needs a noun distinct from 명세), 정기 업무, 결정 기록, 근거.

Labels in `label.ts` renamed on this branch. Where a lane's file still writes the old word inline, use the new one
when converting it, so a badge reads the same on every screen.

| Was | Now | Still inline in |
|---|---|---|
| 오너 대기 | 내 차례 | `pages/Projects.tsx` |
| 오너 답 대기 | 내 답 기다림 | |
| 강한 승인 | 재확인 승인 | `pages/Approvals.tsx`, `components/decision.tsx`, `settings/Rules.tsx`, `services/requests.py` |
| 에스컬레이션, 상신 | 판단 요청 | |
| 명확화 | 확인 질문 | |
| 정책 거부 | 규칙상 막힘 | `pages/Approvals.tsx` |
| 정책 자동 | 규칙대로 자동 | |
| 근거 원장 | 근거 목록 | `pages/Project.tsx`, `components/evidence.tsx` |
| 수용 검수 | 완료 기준 확인 | `pages/Audit.tsx`, `pages/Engineering.tsx`, `api/console.py` |
| 의사결정 | 결정 | |
| 실행, 보고서 (project phase) | 일하는 중, 보고서 쓰는 중 | |

## Worked examples

Strings still inline in files the lanes own, rewritten in this voice. Each lane uses these when it moves the string
into its catalog. Values in braces are the catalog's placeholders.

| # | Where | Before | After |
|---|---|---|---|
| 1 | `services/requests.py` | {REQ} 요청이 없습니다. | {REQ\|을} 찾지 못했어요. |
| 2 | `services/requests.py` | {REQ}: 이미 처리된 요청입니다(상태 {status}). | 이미 정한 결정이에요({status}). |
| 3 | `services/requests.py` | 승인 기한이 지났습니다. 거절하거나 보류하세요. | 승인 기한이 지났어요. 반려하거나 보류해 주세요. |
| 4 | `services/requests.py` | 강한 승인이 필요합니다. 확인란에 {REQ} 값을 그대로 입력하세요. | 한 번 더 확인해야 해요. 확인란에 {REQ\|을} 그대로 적어 주세요. |
| 5 | `services/requests.py` | 질문에는 답이나 선택지를 골라 주세요. | 답을 적거나 선택지를 골라 주세요. |
| 6 | `services/requests.py` | '{label}'은(는) 하지 않는 선택지인데 승인하면 실행돼요. 반려나 보류로 정해 주세요. | '{label}'은 하지 않겠다는 선택지예요. 승인하면 실행되니 반려나 보류로 정해 주세요. |
| 7 | `services/requests.py` | 결정 권한 규칙({action})에 따라 CEO가 승인했습니다. | CEO에게 맡긴 일이라 CEO가 승인했어요({action}). |
| 8 | `services/requests.py` | {REQ}: 결정 권한 규칙 {n}건을 무시했습니다 | 결정 권한 규칙 {n}건은 적용하지 않았어요 |
| 9 | `services/requests.py` | {REQ}의 외부 실행은 이미 끝났습니다 (A-{id}) | 이미 실행을 끝냈어요(A-{id}). |
| 10 | `services/requests.py` | {REQ}: 다시 요청할 외부 실행이 아닙니다 | 다시 요청할 수 있는 실행이 아니에요. |
| 11 | `services/requests.py` | 외부 실행이 실패해 오너가 다시 요청했습니다. | 실행이 실패해서 오너님이 다시 요청했어요. |
| 12 | `services/requests.py` | 게시 카드를 거부했습니다. {refusal} | 올릴 글 카드를 받지 않았어요. {refusal} |
| 13 | `services/requests.py` | 문장을 쓰지 않는 채널이라 에이전트가 쓴 {fields}을(를) 지웠습니다 | 직접 쓴 글만 받는 채널이라 에이전트가 쓴 {fields\|을} 지웠어요 |
| 14 | `services/requests.py` | 답이 개발 작업에 전달되고 멈춘 개발이 이어져요 | 답을 개발팀에 넘기고, 멈춘 개발을 이어 가요 |
| 15 | `services/requests.py` | 명세가 확정되고 개발이 시작돼요. 머지는 따로 물어요 | 이 명세로 개발을 시작해요. 머지는 따로 물어볼게요 |
| 16 | `services/requests.py` | 스킬이 켜져 이후 에이전트의 일에 쓰여요 | 스킬을 켜요. 다음 일부터 에이전트가 이 스킬을 써요 |
| 17 | `services/requests.py` | 답이 기록되고 이후 모든 에이전트가 읽어요 | 답을 남겨 두고, 앞으로 모든 에이전트가 읽어요 |
| 18 | `services/requests.py` | 승인이 기록되고, 시스템이 대신 실행하는 일은 없어요 | 승인만 기록해요. 대신 실행하지는 않아요 |
| 19 | `services/requests.py` | 이 도전이 다음 도전 후보로 올라가요. 시작은 오너가 따로 눌러요 | 이 실험을 다음 실험 후보로 올려요. 시작은 오너님이 직접 눌러요 |
| 20 | `services/requests.py` | 되돌리려면 손이 가요 | 되돌리기 번거로워요 |
| 21 | `engineering/status.py` | 명세 작성을 기다려요. | 명세를 쓸 차례를 기다려요. |
| 22 | `engineering/status.py` | 명세 작성 작업이 대기열에 없어요. 결정을 다시 확인해 주세요. | 명세를 쓸 작업이 대기열에 없어요. 결정을 다시 확인해 주세요. |
| 23 | `engineering/status.py` | 개발 착수를 준비하고 있어요. | 개발을 시작할 준비를 하고 있어요. |
| 24 | `engineering/status.py` | 개발 착수 준비에 실패했어요. | 개발을 시작하지 못했어요. |
| 25 | `engineering/status.py` | 처리하는 워커 작업이 없어요. 새 시도로 다시 시작할 수 있어요. | 맡아서 하는 워커가 없어요. '처음부터 다시'로 새로 시작해 주세요. |
| 26 | `engineering/status.py` | 개발팀 질문에 답을 기다려요. | 개발팀이 질문의 답을 기다려요. |
| 27 | `engineering/status.py` | 전달한 결과에 오너 조치가 필요해요. | 전달한 결과를 확인해 주세요. |
| 28 | `engineering/status.py` | 코드는 완성됐어요(테스트·리뷰 통과). 전달 단계에서 막혔어요. | 코드는 다 만들었어요(테스트·리뷰 통과). 전달하다 막혔어요. |
| 29 | `engineering/status.py` | CPO가 수용 기준대로 확인하고 있어요. | CPO가 완료 기준을 하나씩 확인하고 있어요. |
| 30 | `engineering/status.py` | 이전 워커가 하던 일을 끝내도록 {time}까지 기다려요. | {time}까지 기다려요. 이전 워커가 하던 일을 마저 끝낼 수 있게요. |
| 31 | `services/owner_tasks.py` | {OT}: 오너만 바꿀 수 있습니다 | 오너님만 바꿀 수 있어요. |
| 32 | `services/owner_tasks.py` | 숫자의 이름은 1자 이상 40자 이하여야 합니다 | 숫자 이름은 1~40자로 적어 주세요. |
| 33 | `services/owner_tasks.py` | '{name}'은 숫자여야 합니다 | '{name}'에는 숫자를 적어 주세요. |
| 34 | `services/owner_tasks.py` | 게시한 글의 링크를 붙여 주세요. 링크가 있어야 완료로 표시할 수 있습니다 | 올린 글의 링크를 붙여 주세요. 링크가 있어야 완료할 수 있어요. |
| 35 | `services/owner_tasks.py` | {OT} {title}: 오너 할 일이 생겼습니다 (REQ-12 승인) | REQ-12를 승인해서 내 할 일이 생겼어요: {title} |
| 36 | `services/owner_tasks.py` | {OT}: {from} 상태에서 {to}(으)로 바꿀 수 없습니다 | {from}인 일은 {to\|으로} 바꿀 수 없어요. |
| 37 | `services/owner_tasks.py` | 알 수 없는 채널입니다: {channel} | 모르는 채널이에요: {channel} |
| 38 | `services/owner_tasks.py` | {channel} 게시 카드는 아직 만들 수 없습니다. 출시 관문이 남았습니다: | {channel}에는 아직 올릴 수 없어요. 먼저 끝낼 일이 남았어요: |
| 39 | `api/console.py` | 외부 실행은 승인 한 번에 한 번만 시도합니다. … '다시 요청'을 누르세요. 결정함에 새 요청이 올라가고, 승인해야 실행됩니다. | 승인 한 번에 한 번만 실행해요. 이미 나갔는지 먼저 확인해 주세요. 다시 보내려면 '다시 요청'을 눌러 주세요. 새 결정이 올라오고, 승인하면 실행해요. |
| 40 | `api/console.py` | 아무것도 외부로 나가지 않았습니다. 원인(설정, 수신 동의 등)을 고친 뒤 '다시 시도'하면 같은 승인으로 한 번 더 보냅니다. | 밖으로 나간 건 없어요. 원인(설정, 수신 동의 등)을 고치고 '다시 시도'를 누르면 같은 승인으로 한 번 더 보내요. |
| 41 | `api/console.py` | {reason}에 닿아 {until}까지 기다립니다. 대기 중: {waiting}. | {reason}에 닿아서 {until}까지 기다려요. 기다리는 일: {waiting}. |
| 42 | `api/console.py` | uv run ship worker 명령으로 워커를 켜세요. | 워커가 꺼져 있어요. 터미널에서 `uv run ship worker`로 켜 주세요. |
| 43 | `api/console.py` | {why}. 새 토큰을 저장하면 쌓인 알림 {n}건이 나갑니다. | {why}. 새 토큰을 저장하면 밀린 알림 {n}건을 보내요. |
| 44 | `api/console.py` | 실행 중으로 표시되지만 처리 중인 워커 작업이 없습니다. | 실행 중인데 맡아서 하는 워커가 없어요. |
| 45 | `api/console.py` | 알 수 없는 연결 정보입니다 | 없는 연결이에요. |
| 46 | `api/console.py` | 계획 재정렬 · 스킬 팩토리 · 포트폴리오 검토 | 계획 다시 맞추기 · 스킬 만들기 · 사업 전체 점검 |
| 47 | `components/decision.tsx` | 명세가 승인된 커밋으로 고정되고 개발팀이 착수해요. 머지는 따로 승인받아요. | 승인한 명세로 개발팀이 바로 시작해요. 머지는 따로 물어볼게요. |
| 48 | `components/decision.tsx` | CPO가 개발 명세를 작성해 다시 승인을 요청해요. | CPO가 명세를 쓰면 한 번 더 승인을 받아요. |
| 49 | `components/decision.tsx` | 에이전트는 이 동작을 하지 않아요. | 이 일은 하지 않아요. |
| 50 | `components/decision.tsx` | 답변과 메모는 이후 모든 에이전트에게 전달되고, 진행 중인 프로젝트는 아직 시작하지 않은 업무를 다시 맞춰요. | 답과 메모는 앞으로 모든 에이전트가 읽어요. 진행 중인 프로젝트는 아직 시작하지 않은 업무를 여기에 맞춰 고쳐요. |
| 51 | `pages/Approvals.tsx` | 지금은 오너님을 기다리는 결정이 없어요. 에이전트가 승인이나 답이 필요하면 여기에 올라와요. | 지금은 정할 일이 없어요. 승인이나 답이 필요한 일이 생기면 여기에 올라와요. |
| 52 | `pages/Approvals.tsx` | 실패하거나 멈춘 일 {n}건이 손을 기다려요. | 실패하거나 멈춘 일이 {n}건 있어요. |
| 53 | `pages/Approvals.tsx` | 기다리는 결정 {n}건 · 막힘 {m}건 · 가장 오래된 것은 {ago} 올라왔어요 | 결정 {n}건이 기다려요 · 일을 막는 결정 {m}건 · 가장 오래된 건 {ago}에 올라왔어요 |
| 54 | `pages/NewBusiness.tsx` | 한 줄로 말하면 · 누구를 위한 무엇인가요 | 한 줄 소개 · 누가 무엇을 하게 돕나요 |
| 55 | `pages/NewBusiness.tsx` | 가진 자료가 있으면 붙여 넣으세요 | 기획서나 메모가 있으면 붙여 넣어 주세요 |
| 56 | `pages/NewBusiness.tsx` | 기획서나 메모가 있으면 CEO가 먼저 읽고 지시서를 미리 채워요. | 기획서나 메모가 있으면 CEO가 먼저 읽고 계획을 미리 채워요. |
| 57 | `kickoff/Composer.tsx` | 맞아요를 누르면 이 줄들이 확인함이 돼요 | '맞아요'를 누르면 이 줄을 확인한 것으로 표시해요 |
| 58 | `kickoff/Composer.tsx` | 새로 확인함이 되는 줄은 없어요. | 새로 확인할 줄은 없어요. |
| 59 | `pages/Kickoff.tsx` | 지시서 접기 · 지시서 펼치기 | 계획 접기 · 계획 펼치기 |
| 60 | `pages/Engineering.tsx` | 승인된 명세를 격리된 컨테이너에서 구현하고, 다른 검토자의 리뷰와 CPO 수용 검수를 거쳐 전달해요. | 승인한 명세를 따로 떨어진 컨테이너에서 만들어요. 다른 검토자가 코드를 보고 CPO가 완료 기준을 확인한 뒤 넘겨 드려요. |
| 61 | `pages/Engineering.tsx` | 승인 센터에 머지 승인 요청을 올렸어요. | 머지 결정을 다시 올렸어요. |
| 62 | `pages/Engineering.tsx` | 보고서나 CEO가 개발을 제안하면 결정함에 올라와요. | 보고서나 CEO가 개발을 제안하면 결정에 올라와요. |
| 63 | `pages/Engineering.tsx` | 명세가 승인되면 개발팀이 작업(J-n)을 만들어 구현을 시작해요. | 명세를 승인하면 개발팀이 개발 작업(J-n)을 만들고 시작해요. |
| 64 | `pages/Engineering.tsx` | 기다리는 작업 없음 | 기다리는 작업이 없어요 |
| 65 | `pages/Operations.tsx` | 등록된 정기 업무가 없어요 | 아직 정기 업무가 없어요 |
| 66 | `pages/Audit.tsx` | 이 실행이 보낸 프롬프트와 받은 응답 파일은 정리되어 볼 수 없어요. | 보관 기간이 지나 프롬프트와 응답 파일을 지웠어요. 요약 값은 위에 남아 있어요. |
| 67 | `pages/Business.tsx` | 지금 하는 도전이 없어요 | 지금 하는 실험이 없어요 |

Kept as they are, because they already read well: "내용이 바뀌었어요. 다시 읽고 결정해 주세요."
(`services/requests.py`), "워커가 꺼져 있어 시작하지 못해요. 터미널에서 `uv run ship worker`로 켜 주세요."
(`engineering/status.py`), "이 채널은 직접 쓴 글만 받아요. 아래 사실을 넣어 오너님 말로 써 주세요."
(`components/decision.tsx`), "제일 틀렸을 것 같은 곳을 적어 주세요" (`kickoff/Composer.tsx`).
