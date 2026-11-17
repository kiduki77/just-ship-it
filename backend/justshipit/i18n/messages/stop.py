"""The owner's stops (`services/stops.py`): the whole company, or one agent. `{agent}` is the agent's name for the
owner (glossary.ROLES)."""
from justshipit.i18n import Entry

MESSAGES: dict[str, Entry] = {
    "stop.waiting": {"en": "Waiting: {agent} is paused.", "ko": "대기 중: {agent} 에이전트가 멈춰 있어요."},
    "stop.activity.company": {"en": "Paused the whole company", "ko": "회사 전체를 멈췄어요"},
    "stop.activity.company_resumed": {"en": "Resumed the company", "ko": "회사를 다시 시작했어요"},
    "stop.activity.agent": {"en": "Paused {agent}", "ko": "{agent} 에이전트를 멈췄어요"},
    "stop.activity.agent_resumed": {"en": "Resumed {agent}", "ko": "{agent} 에이전트를 다시 시작했어요"},
}
