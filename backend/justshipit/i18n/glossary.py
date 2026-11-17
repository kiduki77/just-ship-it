"""The owner's vocabulary: one word per idea, in every language, in plain words a first-time founder knows.

This table is the single source. The console reads it through `web/src/i18n/areas/term.ts`, which
`python -m justshipit.i18n.glossary` writes from it; the AI reads it through the output-language rule every system prompt
ends with (`justshipit.i18n.prompting`). PRODUCT.md's terminology table repeats it for people. Tests fail when the console
file, PRODUCT.md or a catalog drifts from it, or a catalog uses a retired word.

Code names stay what they are: the `bets` table is still `bets` and the API still says `brief`. Only what the owner
reads changes.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from justshipit.agents.roles import current
from justshipit.i18n import LANGS, Lang


@dataclass(frozen=True)
class Term:
    id: str
    code: str  # what the code, API and database call it
    words: dict[Lang, str]
    means: dict[Lang, str]
    ref: str = ""  # the id prefix the owner sees, as in "B-3"
    retired: dict[Lang, tuple[str, ...]] = field(default_factory=dict)  # earlier words that confused testers


TERMS: tuple[Term, ...] = (
    Term("business", "business", {"en": "Business", "ko": "사업"},
         {"en": "The idea you are turning into a company. Each has its own plan, people and work.",
          "ko": "회사로 키우고 있는 아이디어예요. 사업마다 계획, 사람, 일이 따로 있어요."}, ref="B"),
    Term("kickoff", "kickoff", {"en": "Kickoff chat", "ko": "시작 대화"},
         {"en": "The short first conversation where the Project Manager asks about your idea and writes the plan with you.",
          "ko": "프로젝트 매니저가 아이디어를 묻고 오너님과 함께 계획을 쓰는 짧은 첫 대화예요."}),
    Term("plan", "brief", {"en": "Plan", "ko": "계획"},
         {"en": "What you and the Project Manager agreed: what the business is, who it is for, what success looks like, and what "
                "is off limits. The company works inside it.",
          "ko": "오너님과 프로젝트 매니저가 합의한 내용이에요. 무슨 사업인지, 누가 쓸지, 어떻게 되면 성공인지, 하지 않을 일은 "
                "무엇인지 적혀 있어요. 회사는 이 안에서 일해요."},
         retired={"ko": ("지시서",), "en": ("work brief",)}),
    Term("cycle", "cycle", {"en": "Cycle", "ko": "사이클"},
         {"en": "One round of work toward the plan, made of stages the Project Manager proposes. You start each stage, and the "
                "company stops after it and waits for you.",
          "ko": "계획을 향해 일하는 한 바퀴예요. 프로젝트 매니저가 단계로 나눠 제안해요. 단계는 오너님이 하나씩 시작하고, 회사는 "
                "단계가 끝날 때마다 멈추고 기다려요."}),
    Term("stage", "cycle_stage", {"en": "Stage", "ko": "단계"},
         {"en": "One part of a cycle, such as Validate or Promote. Its card says what the company and you will do and "
                "what it may cost; nothing in it runs until you start it.",
          "ko": "검증이나 홍보처럼 사이클을 이루는 한 부분이에요. 카드에 회사와 오너님이 할 일, 들 수 있는 비용이 적혀 "
                "있고, 오너님이 시작하기 전에는 아무것도 돌지 않아요."}),
    Term("experiment", "bet", {"en": "Experiment", "ko": "실험"},
         {"en": "One time-boxed try at reaching users: a guess to test, a few weeks, then a look at what happened.",
          "ko": "기한을 정하고 사용자를 만나 보는 시도예요. 확인할 짐작을 하나 정하고 몇 주 해 본 뒤 결과를 돌아봐요."},
         retired={"ko": ("도전",)}),
    Term("project", "project", {"en": "Project", "ko": "프로젝트"},
         {"en": "A piece of work the Project Manager plans to move the plan forward, with its own tasks and budget.",
          "ko": "계획을 이루려고 프로젝트 매니저가 묶은 일이에요. 업무와 예산이 따로 있어요."}, ref="P"),
    Term("task", "task", {"en": "Task", "ko": "업무"},
         {"en": "One piece of work an agent does inside a project.",
          "ko": "에이전트가 프로젝트 안에서 하는 일 하나예요."}, ref="T"),
    Term("decision", "request", {"en": "Decision", "ko": "결정"},
         {"en": "Something the company needs you to decide before it acts: approve, decline, or answer. Anything that "
                "cannot be undone comes to you as a decision.",
          "ko": "회사가 움직이기 전에 오너님이 정할 일이에요. 승인, 반려, 답 중 하나로 정해요. 되돌릴 수 없는 일은 "
                "모두 결정으로 와요."}, ref="REQ", retired={"ko": ("결정함",)}),
    Term("todo", "owner_task", {"en": "To-do", "ko": "내 할 일"},
         {"en": "Something only you can do, such as posting, paying, signing up or talking to someone. The company "
                "prepares it; you do it and mark it done.",
          "ko": "오너님만 할 수 있는 일이에요. 글 올리기, 결제, 가입, 사람 만나기 같은 일이요. 회사가 준비해 두면 직접 "
                "하고 완료를 눌러요."}, ref="OT", retired={"ko": ("오너 할 일",)}),
    Term("idea", "idea", {"en": "Idea", "ko": "아이디어"},
         {"en": "A note you drop in at any time. The Project Manager reads it, then uses it, asks you about it, or suggests "
                "setting it aside.",
          "ko": "오너님이 언제든 던져 넣는 메모예요. 프로젝트 매니저가 읽고 반영하거나, 묻거나, 버리자고 제안해요."}),
    Term("evidence", "evidence", {"en": "Evidence", "ko": "근거"},
         {"en": "What a claim stands on: a checked fact, a source, or a guess, always labelled as which.",
          "ko": "주장의 바탕이에요. 확인한 사실인지, 출처가 있는 말인지, 짐작인지 늘 함께 표시해요."}, ref="EV"),
    Term("decision_log", "decision", {"en": "Decision log", "ko": "결정 기록"},
         {"en": "What was decided, by whom and why, kept so the company remembers.",
          "ko": "무엇을 누가 왜 정했는지 남겨 둔 기록이에요."}, ref="DEC"),
    Term("work_path", "work_path", {"en": "Work path", "ko": "일의 경로"},
         {"en": "How the work behind a decision connects: the goal, the experiment, the project, the task, and what "
                "waits on your answer.",
          "ko": "결정 뒤의 일이 어떻게 이어지는지예요. 목표, 실험, 프로젝트, 업무, 그리고 답을 기다리는 일이 "
                "차례로 보여요."}),
    Term("meeting", "meeting", {"en": "Meeting", "ko": "회의"},
         {"en": "When agents disagree, they meet and the Project Manager decides.",
          "ko": "에이전트 의견이 갈리면 모여서 이야기하고 프로젝트 매니저가 정해요."}, ref="M"),
    Term("product_plan", "plan", {"en": "Product plan", "ko": "기획안"},
         {"en": "What a build is for: the problem, who it serves, what the first version does and leaves out, and how "
                "to tell it worked. You approve it before the spec is written.",
          "ko": "만들 것의 방향을 적은 글이에요. 풀 문제, 누가 쓸지, 첫 버전에서 할 일과 하지 않을 일, 성공을 확인하는 "
                "방법이 적혀 있어요. 오너님이 승인해야 명세를 써요."}),
    Term("spec", "spec", {"en": "Spec", "ko": "명세"},
         {"en": "The written description of what to build. You approve it before building starts.",
          "ko": "무엇을 만들지 적은 글이에요. 오너님이 승인해야 개발이 시작돼요."}),
    Term("build", "dev_job", {"en": "Build", "ko": "개발 작업"},
         {"en": "The engineering team building an approved spec.",
          "ko": "승인한 명세대로 개발팀이 만드는 일이에요."}, ref="J"),
    Term("agent", "agent", {"en": "Agent", "ko": "에이전트"},
         {"en": "A role on the roster that is called in per job, such as research or marketing.",
          "ko": "리서치나 마케팅처럼 인력풀에 있다가 일마다 불려 오는 역할이에요."}),
    Term("project_manager", "project_manager", {"en": "Project Manager", "ko": "프로젝트 매니저"},
         {"en": "The AI role that runs the work: it holds the kickoff chat, plans each cycle and project, says which "
                "roles a job needs, reviews the results and decides when agents disagree.",
          "ko": "일을 이끄는 AI 역할이에요. 시작 대화를 하고, 사이클과 프로젝트를 계획하고, 일마다 필요한 역할을 "
                "말하고, 결과를 검토하고, 에이전트 의견이 갈리면 정해요."},
         retired={"en": ("CEO", "AI CEO"), "ko": ("CEO", "AI CEO")}),
    Term("staffing_manager", "staffing", {"en": "Staffing Agency Manager", "ko": "인력 파견 매니저"},
         {"en": "Not an AI but a fixed rule. It places the team for each task and build from the roster, records why, "
                "and lets the team go when the job ends. It makes no model calls.",
          "ko": "AI가 아니라 정해진 규칙이에요. 업무와 개발 작업마다 인력풀에서 팀을 꾸리고, 왜 그렇게 꾸렸는지 "
                "기록하고, 일이 끝나면 팀을 풀어요. 모델을 호출하지 않아요."}),
    Term("crew", "crew", {"en": "Team for this job", "ko": "꾸린 팀"},
         {"en": "The roles placed for one task or build, with the reason for each. It is put together when the job "
                "starts and let go when it ends.",
          "ko": "업무나 개발 작업 하나에 꾸린 역할들과 각 역할을 고른 이유예요. 일이 시작될 때 꾸리고 끝나면 풀어요."},
         retired={"en": ("org chart",), "ko": ("조직도",)}),
    Term("roster", "agents", {"en": "Roster", "ko": "인력풀"},
         {"en": "Every role that can be called in for a job, with its model. A role on it works only when a job's "
                "team needs it.",
          "ko": "일에 불려 올 수 있는 모든 역할과 각 역할의 모델이에요. 일의 팀에 필요할 때만 일해요."}),
    Term("people", "people", {"en": "People list", "ko": "명단"},
         {"en": "The people you may talk to, and how far each has come.",
          "ko": "만나 볼 사람과 각자 어디까지 왔는지 적어 둔 목록이에요."}),
    Term("got_value", "first_value", {"en": "Got value", "ko": "도움 됨"},
         {"en": "A person who used it and got what they came for at least once.",
          "ko": "써 보고 원하던 걸 한 번이라도 얻은 사람이에요."},
         retired={"ko": ("첫 가치",), "en": ("first value",)}),
    Term("came_back", "repeat", {"en": "Came back", "ko": "재사용"},
         {"en": "A person who used it again on their own.",
          "ko": "누가 시키지 않아도 다시 쓴 사람이에요."}),
    Term("first_users", "funnel", {"en": "Steps to first users", "ko": "사용자가 되기까지"},
         {"en": "Found, contacted, talked, got value, came back: how far your people have come.",
          "ko": "찾음, 연락함, 대화함, 도움 됨, 재사용. 명단의 사람이 각자 어디까지 왔는지 보여 줘요."},
         retired={"ko": ("사다리",), "en": ("ladder",)}),
    Term("routine", "routine", {"en": "Routine", "ko": "정기 업무"},
         {"en": "Work the company repeats on a schedule, such as a weekly review.",
          "ko": "주간 점검처럼 회사가 정해진 주기로 되풀이하는 일이에요."}),
    Term("skill", "skill", {"en": "Skill", "ko": "스킬"},
         {"en": "A written way of doing a recurring job that agents reuse.",
          "ko": "자주 하는 일의 방법을 적어 둔 글이에요. 에이전트가 꺼내 다시 써요."}),
    Term("outbox", "outbox", {"en": "Outbox", "ko": "보관함"},
         {"en": "Where what the company would send out waits until you turn sending on. Nothing in it has left; you "
                "open it and send it yourself.",
          "ko": "실제로 보내기를 켜기 전까지 밖으로 보낼 것을 모아 두는 곳이에요. 여기 있는 건 아직 나가지 않았고, "
                "오너님이 열어 보고 직접 보내요."},
         retired={"ko": ("outbox", "아웃박스")}),
    Term("sending_mode", "connector mode", {"en": "Sending mode", "ko": "보내기 방식"},
         {"en": "How each connector sends, set in Settings: Off (nothing is made or sent), Outbox (made and kept for "
                "you to send yourself), Send what I approve (only what you approve, one by one, goes out) or Send "
                "right away (goes out without asking you).",
          "ko": "커넥터마다 설정에서 정하는 보내는 방식이에요. 끔(아무것도 만들거나 보내지 않음), 보관함(만들어 두고 "
                "오너님이 직접 보냄), 승인한 것만 보내기(하나씩 승인한 것만 나감), 바로 보내기(묻지 않고 나감) 중 "
                "하나예요."}),
    Term("holding_up_work", "blocking", {"en": "Holding up work", "ko": "일을 막음"},
         {"en": "A decision the work behind it can't go on without. That work waits until you decide.",
          "ko": "정하기 전까지 그 뒤의 일이 기다리는 결정이에요."}),
    Term("on_hold", "DEFERRED", {"en": "On hold", "ko": "보류"},
         {"en": "A decision set aside for now. Nothing it asks for happens, and an agent asks again if it is still "
                "needed.",
          "ko": "지금은 정하지 않고 미뤄 둔 결정이에요. 결정이 하려던 일은 일어나지 않고, 아직 필요하면 에이전트가 "
                "다시 물어요."}, retired={"ko": ("뺌",)}),
    Term("expires", "expires_at", {"en": "Expires", "ko": "만료"},
         {"en": "When a decision can no longer be approved. A to-do has a due date instead.",
          "ko": "결정을 더는 승인할 수 없게 되는 때예요. 내 할 일에는 만료 대신 마감이 있어요."}),
    Term("model_call", "llm_call", {"en": "Model call", "ko": "모델 호출"},
         {"en": "One request an agent sends to Claude. Each is recorded with what it read and answered, and counts "
                "toward usage.",
          "ko": "에이전트가 Claude에 보내는 요청 하나예요. 무엇을 읽고 답했는지 기록되고 사용량에 잡혀요."}),
    Term("monthly_budget", "monthly_budget_usd", {"en": "Monthly budget", "ko": "월 한도"},
         {"en": "The most the whole company may spend in a month, at list price. At it, new model calls are refused "
                "and projects stop until next month or until you raise it.",
          "ko": "회사 전체가 한 달에 쓸 수 있는 최대 금액이에요(정가 환산). 닿으면 새 모델 호출을 거절하고, 다음 달이 "
                "되거나 오너님이 올릴 때까지 프로젝트가 멈춰요."}),
    Term("usage_cap", "usage", {"en": "Usage cap", "ko": "사용량 상한"},
         {"en": "How much of your Claude subscription the company may use per day and per week. At the cap, work "
                "waits and picks up after the reset; nothing fails.",
          "ko": "회사가 Claude 구독을 하루와 한 주에 얼마나 쓸지 정한 양이에요. 닿으면 일이 기다렸다가 초기화 뒤에 "
                "이어서 하고, 실패하는 일은 없어요."}),
    Term("pause", "stop", {"en": "Pause", "ko": "멈춤"},
         {"en": "Stopping something for now in a way you can undo. It picks up where it left off when you resume it.",
          "ko": "되돌릴 수 있게 잠시 세우는 거예요. 다시 시작하면 하던 곳부터 이어서 해요."}),
    Term("end", "end", {"en": "End", "ko": "끝내기"},
         {"en": "Finishing something, such as a chat or a cycle, instead of pausing it. It doesn't carry on by itself.",
          "ko": "시작 대화나 사이클처럼 무언가를 멈춰 두지 않고 마치는 거예요. 저절로 다시 이어지지 않아요."}),
    Term("team_chat", "chat", {"en": "Team chat", "ko": "팀 대화"},
         {"en": "A conversation with the team about what is on your screen. Anything that cannot be undone still "
                "comes to you as a decision.",
          "ko": "지금 화면에 있는 일을 두고 팀과 나누는 대화예요. 되돌릴 수 없는 일은 지금처럼 결정으로 와요."}),
    Term("background_process", "worker", {"en": "Background process", "ko": "백그라운드 프로세스"},
         {"en": "The program that does the company's work behind the console: jobs, stages and routines. Start it "
                "with `ship supervise`; while it is off, work waits.",
          "ko": "콘솔 뒤에서 회사의 일(작업, 단계, 정기 업무)을 하는 프로그램이에요. `ship supervise`로 켜고, 꺼져 "
                "있으면 일이 기다려요."}),
    Term("user_journey", "journey", {"en": "User journey", "ko": "사용 흐름"},
         {"en": "How one kind of person uses the product from start to finish: who they are, why they came, each step, "
                "and what they see when it worked.",
          "ko": "한 종류의 사람이 제품을 처음부터 끝까지 쓰는 과정이에요. 누구인지, 왜 왔는지, 각 단계, 잘 됐을 때 "
                "무엇이 보이는지가 적혀 있어요."}),
    Term("customer_profile", "customer_profile", {"en": "Customer description", "ko": "고객 설명"},
         {"en": "Who buys, how they are reached, and what you already have. The company picks places and people "
                "from it.",
          "ko": "누가 사는지, 어떻게 닿는지, 이미 가진 것이 무엇인지 적은 내용이에요. 회사는 이걸 보고 홍보할 곳과 "
                "찾을 사람을 골라요."}),
    Term("tracked_link", "tracked_link", {"en": "Your link", "ko": "내 링크"},
         {"en": "The address on a post card that leads to your own site, store page or repository, with a tag when the "
                "place would hide where visitors came from.",
          "ko": "홍보 카드에 붙는, 오너님의 사이트, 가게 페이지, 저장소로 가는 주소예요. 방문자가 어디서 왔는지 숨기는 "
                "곳에는 태그를 달아요."}),
    Term("first_ten_kit", "first_ten_kit", {"en": "First 10 kit", "ko": "첫 10명 키트"},
         {"en": "Who to reach today, a message for each, and wording for your profile and invites, all written for you to copy. The company sends nothing; you send it yourself.",
          "ko": "오늘 연락할 사람, 사람마다 메시지 한 개, 프로필과 초대 문구를 모아 둔 곳이에요. 회사가 써 주면 오너님이 복사해서 직접 보내요. 회사는 아무것도 보내지 않아요."}),
    Term("rules_checked", "rules_checked", {"en": "Rules checked", "ko": "규칙 확인됨"},
         {"en": "The company read this place's rules on the place's own page and shows the date. It writes for the place, "
                "and you still post by hand.",
          "ko": "회사가 그 채널의 규칙을 채널 자체 페이지에서 읽었고, 읽은 날짜를 보여 줘요. 회사는 그 채널에 올릴 글을 "
                "준비하고, 올리는 건 오너님이 직접 해요."}),
    Term("rules_unchecked", "rules_unchecked", {"en": "Rules unchecked", "ko": "규칙 미확인"},
         {"en": "The company did not read this place's rules. It writes nothing for the place. You can still post there "
                "yourself after reading the rules.",
          "ko": "회사가 그 채널의 규칙을 읽지 못했어요. 그 채널에는 회사가 글을 쓰지 않아요. 오너님이 규칙을 직접 읽은 뒤 "
                "직접 올릴 수는 있어요."}),
)

BY_ID: dict[str, Term] = {t.id: t for t in TERMS}

# Each agent's one name, by its id in config/agents.yaml. The console's `label.agent.<id>` entries say the same
# (tests/test_agent_names.py), so a reviewer named in a server sentence is the one the sidebar shows.
ROLES: dict[str, dict[Lang, str]] = {
    "project_manager": {"en": "Project Manager", "ko": "프로젝트 매니저"},
    "strategy": {"en": "Strategy", "ko": "사업전략"},
    "research": {"en": "Market research", "ko": "시장조사"},
    "marketing": {"en": "Marketing", "ko": "마케팅"},
    "finance": {"en": "Finance", "ko": "재무"},
    "product": {"en": "Product", "ko": "제품기획"},
    "risk": {"en": "Risk & audit", "ko": "리스크·감사"},
    "analyst": {"en": "Data analysis", "ko": "데이터 분석"},
    "skill_builder": {"en": "Skill builder", "ko": "스킬 빌더"},
    "engineer": {"en": "Engineer", "ko": "엔지니어"},
    "eng_reviewer": {"en": "Code reviewer", "ko": "코드 리뷰어"},
    "critic": {"en": "Idea critics", "ko": "아이디어 평가단"},
}


# Each department's one name, by `department` in config/agents.yaml. The console's `company.dept.<id>` entries say the
# same (tests/test_agent_names.py), on the Agents page and in chat alike. "business" is not called Business:
# that word is the owner's business (B-n).
DEPARTMENTS: dict[str, dict[Lang, str]] = {
    "executive": {"en": "Leadership", "ko": "경영"},
    "business": {"en": "Strategy & growth", "ko": "전략·성장"},
    "audit": {"en": "Audit", "ko": "감사"},
    "capability": {"en": "Capability", "ko": "역량"},
    "engineering": {"en": "Engineering", "ko": "개발"},
    "advisory": {"en": "Advisory", "ko": "자문"},
}


def role(agent_id: str | None, lang: Lang) -> str:
    """An agent's name for the owner; an id the table does not know is shown as it is."""
    return ROLES.get(current(agent_id or ""), {}).get(lang, agent_id or "")


def word(term_id: str, lang: Lang) -> str:
    return BY_ID[term_id].words[lang]


def retired(lang: Lang) -> tuple[str, ...]:
    return tuple(w for t in TERMS for w in t.retired.get(lang, ()))


WEB_FILE = Path(__file__).resolve().parents[3] / "web" / "src" / "i18n" / "areas" / "term.ts"


def web_source() -> str:
    """The console's copy of the table, as `term.<id>` and `term.<id>.means` catalog entries."""
    lines = ["// Written by `uv run python -m justshipit.i18n.glossary` from backend/justshipit/i18n/glossary.py. Do not edit.",
             'import type { Area } from "../types";', "", "export const term = {"]
    for t in TERMS:
        for key, texts in ((f"term.{t.id}", t.words), (f"term.{t.id}.means", t.means)):
            cells = ", ".join(f"{lang}: {json.dumps(texts[lang], ensure_ascii=False)}" for lang in LANGS)
            lines.append(f"  {json.dumps(key)}: {{ {cells} }},")
    lines += ['} as const satisfies Area<"term">;', ""]
    return "\n".join(lines)


if __name__ == "__main__":
    WEB_FILE.write_text(web_source(), encoding="utf-8", newline="\n")
    sys.stdout.write(f"wrote {WEB_FILE}\n")
