"""The business, its plan and its experiments (`services/roadmap.py`, `services/completion.py`): the plan's sections,
where each line came from, the refusals the console shows, and the activity lines the server writes."""
from justshipit.i18n import Entry

_SECTIONS: dict[str, dict[str, Entry]] = {
    "one_liner": {
        "title": {"en": "One-line pitch", "ko": "한 줄 소개"},
        "holds": {"en": "Who it is for and what it does", "ko": "누구를 위한 무엇인지"},
        "drives": {"en": "Scope check for every task", "ko": "모든 업무의 범위 확인"},
        "default": {"en": "Uses the line you typed first", "ko": "처음 적은 한 줄을 써요"},
    },
    "customer_problem": {
        "title": {"en": "Customer and problem", "ko": "대상 고객과 문제"},
        "holds": {"en": "One person, what happened to them, what they use today, and how you know: seen firsthand, "
                        "heard or a guess",
                  "ko": "한 사람의 모습, 그 사람이 겪은 일, 지금 쓰는 대안, 어떻게 알았는지(직접 본 일, 들은 일, 짐작)"},
        "drives": {"en": "The first channel and the first research", "ko": "첫 채널과 첫 조사"},
    },
    "alternative_value": {
        "title": {"en": "Value over alternatives", "ko": "대안보다 나은 점"},
        "holds": {"en": "Why someone would use this instead of what they do now",
                  "ko": "지금 쓰는 방법 대신 이걸 써야 하는 이유"},
        "drives": {"en": "The message and the focus of the first test", "ko": "메시지와 첫 시험의 초점"},
        "default": {"en": "The first research experiment compares it with the alternatives",
                    "ko": "대안과 비교하는 일을 첫 조사 실험으로 잡아요"},
    },
    "path": {
        "title": {"en": "Launch path", "ko": "시작 방식"},
        "holds": {"en": "Open source, an app, a web service or something else, and why",
                  "ko": "오픈소스, 앱, 웹 서비스 중 무엇으로 시작할지와 그 이유"},
        "drives": {"en": "The first channel, the team and the numbers to collect", "ko": "첫 채널, 팀 구성, 모을 숫자"},
        "default": {"en": "The first research experiment picks the launch path",
                    "ko": "시작 방식을 정하는 일을 첫 조사 실험으로 잡아요"},
    },
    "goal_deadline": {
        "title": {"en": "Goal and deadline", "ko": "목표와 기한"},
        "holds": {"en": "The one number that matters most and the date to reach it by. In the chat you choose what "
                        "it counts: people who showed interest, people it helped, or people who came back",
                  "ko": "가장 중요한 숫자 하나와 그 숫자에 닿을 날짜예요. 관심을 보인 사람, 도움이 된 사람, 다시 온 사람 "
                        "중 무엇을 셀지는 대화에서 정해요"},
        "drives": {"en": "How long one experiment runs and how it's judged", "ko": "실험 한 번의 기간과 판정 기준"},
    },
    "owner_time": {
        "title": {"en": "Your time", "ko": "내 시간"},
        "holds": {"en": "The hours a week you can give, and on which days", "ko": "한 주에 쓸 수 있는 시간과 요일"},
        "drives": {"en": "How many to-dos you get, and when", "ko": "내 할 일의 양과 일정"},
        "default": {"en": "Assumes 3 to 4 hours a week, with at most 3 to-dos a day",
                    "ko": "주 3~4시간으로 보고, 내 할 일은 하루 3개까지만 만들어요"},
    },
    "red_lines": {
        "title": {"en": "Red lines", "ko": "금지선"},
        "holds": {"en": "What the company must never do (post under your name, run paid ads, ...), and legal or policy "
                        "cautions",
                  "ko": "절대 하지 않을 일(내 이름으로 글 올리기, 유료 광고 등)과 법이나 규칙상 조심할 점"},
        "drives": {"en": "What is off limits, and what needs a typed confirmation",
                   "ko": "금지할 일과 재확인 승인이 필요한 일"},
        "default": {"en": "Uses the standard rules: the Project Manager decides internal work and research and tells you, and "
                          "everything else waits for your approval",
                    "ko": "기본 규칙을 써요. 내부 일과 조사는 프로젝트 매니저가 정하고 알려 드리고, 그 밖의 일은 오너님이 승인해요"},
    },
    "known_people": {
        "title": {"en": "People you already know", "ko": "이미 아는 사람"},
        "holds": {"en": "Names or handles and how you know them. If empty, the company starts from people who talked "
                        "about the problem in public",
                  "ko": "이름이나 아이디와 어떤 사이인지. 비어 있으면 이 문제를 공개적으로 말한 사람부터 찾아요"},
        "drives": {"en": "The first rows of the people list", "ko": "명단의 첫 줄"},
    },
    "reverse_100": {
        "title": {"en": "Working back from {goal}", "ko": "{goal}에서 거꾸로 계산"},
        "holds": {"en": "Channel × expected reach × conversion, the total, and how far it falls short of the goal",
                  "ko": "채널 × 예상 도달 × 전환, 합계, 목표까지 모자란 수"},
        "drives": {"en": "The first channel and the size of the experiment", "ko": "첫 채널과 실험의 크기"},
    },
    "top_risks": {
        "title": {"en": "Top 3 risks", "ko": "가장 큰 위험 3가지"},
        "holds": {"en": "For each risk, the first sign to watch for and the response", "ko": "위험마다 먼저 보일 신호와 대응"},
        "drives": {"en": "The questions for each checkpoint", "ko": "점검일에 볼 질문"},
    },
    "scope": {
        "title": {"en": "Scope", "ko": "범위"},
        "holds": {"en": "What to do and what not to do", "ko": "할 일과 하지 않을 일"},
        "drives": {"en": "Which tasks the company may create", "ko": "회사가 만들 수 있는 업무의 범위"},
    },
    "budget_usage": {
        "title": {"en": "Budget and usage", "ko": "예산과 사용량"},
        "holds": {"en": "The cash cap and the subscription usage cap", "ko": "현금 상한과 구독 사용량 상한"},
        "drives": {"en": "Budgets and usage caps", "ko": "예산과 사용량 상한"},
    },
    "pricing_hypothesis": {
        "title": {"en": "Pricing guess", "ko": "가격 가설"},
        "holds": {"en": "Who would pay for what, and one signal to watch", "ko": "누가 무엇에 돈을 낼지와 지켜볼 신호 하나"},
        "drives": {"en": "Signs that people will pay", "ko": "돈을 낼 의사가 있다는 신호"},
    },
    "background": {
        "title": {"en": "Background and purpose", "ko": "배경과 목적"},
        "holds": {"en": "Why you are doing this, why now, and what changes for you",
                  "ko": "왜 하는지, 왜 지금인지, 오너님에게 무엇이 달라지는지"},
        "drives": {"en": "The tiebreaker when a call is close", "ko": "판단이 갈릴 때의 기준"},
    },
    "decision_rights": {
        "title": {"en": "Decision rights", "ko": "결정 권한"},
        "holds": {"en": "What you decide, what the Project Manager decides and tells you, and where you only give an opinion",
                  "ko": "오너님이 정할 일, 프로젝트 매니저가 정하고 알려 줄 일, 의견만 줄 일"},
        "drives": {"en": "Which actions need your approval. Unless you say otherwise, the standard rules",
                   "ko": "오너님이 승인할 일의 범위. 따로 말하지 않으면 기본 규칙"},
    },
    "owner_words": {
        "title": {"en": "In your words", "ko": "오너님이 한 말"},
        "holds": {"en": "What you said in the chat, word for word", "ko": "대화에서 오너님이 한 말 그대로"},
        "drives": {"en": "What the Project Manager quotes in the plan", "ko": "프로젝트 매니저가 계획에서 인용하는 말"},
    },
}

MESSAGES: dict[str, Entry] = {
    **{f"roadmap.section.{key}.{part}": entry for key, parts in _SECTIONS.items() for part, entry in parts.items()},
    "roadmap.section.legacy": {"en": "Earlier section", "ko": "이전 칸"},
    "roadmap.section.your_goal": {"en": "your goal", "ko": "목표"},
    "roadmap.section.guess_default": {"en": "Uses the line written as a guess, and checks it in the first research",
                                      "ko": "짐작으로 적은 줄을 그대로 쓰고, 맞는지 첫 조사에서 확인해요"},

    "roadmap.provenance.said": {"en": "You said", "ko": "오너가 말함"},
    "roadmap.provenance.confirmed": {"en": "Confirmed", "ko": "확인함"},
    "roadmap.provenance.guess": {"en": "Guess", "ko": "짐작"},
    "roadmap.provenance.unknown": {"en": "Unknown", "ko": "모름"},
    "roadmap.provenance.blank": {"en": "Blank", "ko": "빈칸"},

    "roadmap.list.rungs": {"en": "Stages", "ko": "단계"},
    "roadmap.list.tracks": {"en": "Tracks", "ko": "트랙"},
    "roadmap.list.models": {"en": "Business models", "ko": "사업 모델"},
    "roadmap.list.door_policy": {"en": "Decision rights", "ko": "결정 권한"},
    "roadmap.list.checkpoints": {"en": "Checkpoints", "ko": "점검일"},
    "roadmap.list.sections": {"en": "Plan sections", "ko": "계획 칸"},
    "roadmap.list.assumptions": {"en": "Assumptions", "ko": "가정"},

    "roadmap.refuse.no_business": {"en": "Business B-{id} not found.", "ko": "B-{id} 사업을 찾지 못했어요."},
    "roadmap.refuse.no_bet": {"en": "Experiment BET-{id} not found.", "ko": "BET-{id} 실험을 찾지 못했어요."},
    "roadmap.refuse.no_brief": {"en": "Plan #{id} not found.", "ko": "계획 #{id|을} 찾지 못했어요."},
    "roadmap.refuse.item": {"en": "{what}, item {n}: {detail}", "ko": "{what} {n}번째 항목: {detail}"},
    "roadmap.refuse.duplicate_id": {"en": "{what} has the id {id} twice.", "ko": "{what}에 같은 id가 두 번 있어요: {id}"},
    "roadmap.refuse.duplicate_line": {"en": "The plan has the line id {id} twice.",
                                      "ko": "계획에 같은 줄 id가 두 번 있어요: {id}"},
    "roadmap.refuse.no_name": {"en": "Give the business a name.", "ko": "사업 이름을 적어 주세요."},
    "roadmap.refuse.not_a_list": {"en": "'{name}' is not a list you can edit.",
                                  "ko": "고칠 수 있는 목록이 아니에요: {name}"},
    "roadmap.refuse.held": {"en": "A running experiment uses these, so they can't be removed: {bets}",
                            "ko": "지금 하는 실험이 쓰고 있어서 지울 수 없어요: {bets}"},
    "roadmap.refuse.checkpoint_rung": {"en": "Checkpoint '{title}' points to a stage that no longer exists.",
                                       "ko": "'{title}' 점검이 가리키는 단계가 없어요."},
    "roadmap.refuse.kickoff_live": {"en": "B-{id}: the kickoff chat isn't finished yet. Finish it, then try again.",
                                    "ko": "B-{id}: 시작 대화가 아직 끝나지 않았어요. 대화를 끝낸 뒤 다시 해 주세요."},
    "roadmap.refuse.abandoned": {
        "en": "B-{id}: the kickoff chat was ended partway, so its draft plan is only partly filled and can't be "
              "approved or started. Reopen the chat and finish it first.",
        "ko": "B-{id}: 중간에 끝낸 시작 대화라 계획 초안이 덜 채워져 있어요. 승인하거나 시작하려면 대화를 다시 열어 "
              "끝내 주세요."},
    "roadmap.refuse.draft_exists": {"en": "B-{id} already has a draft plan waiting for approval.",
                                    "ko": "B-{id}에는 승인을 기다리는 계획 초안이 이미 있어요."},
    "roadmap.refuse.gates": {
        "en": "B-{id}: can't approve yet. Each of these needs a line you said or confirmed yourself: {sections}. A "
              "guess or \"I don't know\" isn't enough.",
        "ko": "B-{id}: 아직 승인할 수 없어요. {sections} 칸에는 오너님이 직접 말했거나 맞다고 한 줄이 있어야 해요. "
              "모르겠다는 답이나 짐작만으로는 승인할 수 없어요."},
    "roadmap.refuse.no_track": {"en": "B-{id} has no track '{track}'.", "ko": "B-{id}에 '{track}' 트랙이 없어요."},
    "roadmap.refuse.no_rung": {"en": "B-{id} has no stage '{rung}'.", "ko": "B-{id}에 '{rung}' 단계가 없어요."},
    "roadmap.refuse.choice": {"en": "{name} must be one of: {choices}", "ko": "{name}: {choices} 중 하나여야 해요"},
    "roadmap.refuse.no_title": {"en": "Write a title and a one-line pitch.", "ko": "제목과 한 줄 설명을 적어 주세요."},
    "roadmap.refuse.archived": {"en": "B-{id} is archived.", "ko": "B-{id}: 보관한 사업이에요."},
    "roadmap.refuse.bet_fields": {"en": "Experiments have no field {fields}.", "ko": "실험에 없는 항목이에요: {fields}"},
    "roadmap.refuse.started": {
        "en": "BET-{id} has already started. Its pitch and limits can only change before it starts.",
        "ko": "BET-{id}: 이미 시작한 실험이에요. 한 줄 설명과 한도는 시작 전에만 고칠 수 있어요."},
    "roadmap.refuse.finished": {"en": "BET-{id} is over.", "ko": "BET-{id}: 끝난 실험이에요."},
    "roadmap.refuse.slots": {
        "en": {"one": "BET-{bet}: B-{business} already runs {n} experiment at a time (limit {cap}). Start this one after "
                      "it ends, or raise the limit in Settings.",
               "other": "BET-{bet}: B-{business} already runs {n} experiments at a time (limit {cap}). Start this one "
                        "after one ends, or raise the limit in Settings."},
        "ko": "BET-{bet}: B-{business}에서 이미 실험 {n}개를 동시에 하고 있어요(상한 {cap}개). 하나가 끝난 뒤 시작하거나 "
              "설정에서 상한을 올려 주세요."},
    "roadmap.refuse.has_retro": {"en": "BET-{id} already has a review, so it can't go back to running.",
                                 "ko": "BET-{id}: 이미 회고가 있어서 다시 실행할 수 없어요."},
    "roadmap.refuse.not_a_status": {"en": "'{to}' is not an experiment status.", "ko": "실험 상태가 아니에요: {to}"},
    "roadmap.refuse.move": {"en": "BET-{id} can't move from {was} to {to}.",
                            "ko": "BET-{id}: {was}에서 {to} 상태로는 옮길 수 없어요."},
    "roadmap.refuse.not_open_cycle": {"en": "Cycle #{id} is not an open cycle of this business.",
                                      "ko": "사이클 #{id}: 이 사업의 열린 사이클이 아니에요."},

    "roadmap.bet.paused": {"en": "Experiment stopped", "ko": "실험을 멈췄어요"},
    "roadmap.retro.budget_stop": {"en": "There wasn't enough budget to write the review.",
                                  "ko": "예산이 모자라 회고를 쓰지 못했어요."},

    "roadmap.activity.rules_withdrawn": {
        "en": {"one": "B-{id} {name}: approving plan v{version} took back {n} rule that let the Project Manager decide. You decide "
                      "until the new rules are set.",
               "other": "B-{id} {name}: approving plan v{version} took back {n} rules that let the Project Manager decide. You "
                        "decide until the new rules are set."},
        "ko": "B-{id} {name}: 계획 v{version|을} 승인해서 프로젝트 매니저가 정하던 규칙 {n}건을 거뒀어요. 새 규칙을 정할 때까지 "
              "오너님이 정해요."},
    "roadmap.activity.rules_derived": {
        "en": {"one": "B-{id} {name}: set {n} decision right from plan v{version}",
               "other": "B-{id} {name}: set {n} decision rights from plan v{version}"},
        "ko": "B-{id} {name}: 계획 v{version}에서 결정 권한 {n}건을 정했어요"},
    "roadmap.activity.rules_problem": {
        "en": {"one": "B-{id} {name}: couldn't apply {n} decision right",
               "other": "B-{id} {name}: couldn't apply {n} decision rights"},
        "ko": "B-{id} {name}: 결정 권한 {n}건을 적용하지 못했어요"},
    "roadmap.problem.owner_rule": {"en": "Your rule {action}: {problem}", "ko": "오너님의 규칙 {action}: {problem}"},
    "roadmap.problem.plan_rule": {"en": "Rule read from the plan, {action}: {problem}",
                                  "ko": "계획에서 읽은 규칙 {action}: {problem}"},
    "roadmap.activity.lists_edited": {"en": "B-{id} {name}: you edited {lists}", "ko": "B-{id} {name}: 오너님이 고쳤어요({lists})"},
    "roadmap.activity.brief_approved": {"en": "B-{id}: plan v{version} approved", "ko": "B-{id}: 계획 v{version|을} 승인했어요"},
    "roadmap.activity.bet_edited": {"en": "BET-{id} {title}: edited {fields}", "ko": "BET-{id} {title}: 고쳤어요({fields})"},

    "roadmap.goal.plan": {"en": "Work from plan v{version}.", "ko": "계획 v{version}대로 해요."},
    "roadmap.goal.question": {"en": "Hypothesis (what to find out): {text}", "ko": "가설(알아낼 것): {text}"},
    "roadmap.goal.audience": {"en": "Audience: {text}", "ko": "대상: {text}"},
    "roadmap.goal.exit_signal": {"en": "Success signal: {text}", "ko": "성공 신호: {text}"},
    "roadmap.goal.circuit_breaker": {"en": "Stop if: {text}", "ko": "멈출 조건: {text}"},
    "roadmap.goal.days": {"en": {"one": "{n} day", "other": "{n} days"}, "ko": "기간 {n}일"},
    "roadmap.goal.budget": {"en": "a ${usd} budget", "ko": "예산 ${usd}"},
    "roadmap.goal.limits": {"en": "Limits: {limits}. The run stops when it reaches one.",
                            "ko": "한도: {limits}. 한도에 닿으면 실행을 멈춰요."},
    "roadmap.goal.business": {"en": "Business: B-{id} {name}", "ko": "사업: B-{id} {name}"},

    "roadmap.done.default_repeat": {"en": "10 outside users who used it twice or more",
                                    "ko": "두 번 이상 쓴 외부 사용자 10명"},
    "roadmap.done.default_talked": {"en": "5 of them you talked to yourself", "ko": "그중 오너님이 직접 대화한 5명"},
    "roadmap.done.default_unit": {"en": "people", "ko": "명"},
    "roadmap.done.no_people_list": {
        "en": "The people list isn't connected, so the company can't count who reached the goal.",
        "ko": "명단이 연결되지 않아서 목표에 닿은 사람을 셀 수 없어요."},
}
