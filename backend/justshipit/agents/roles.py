"""The Project Manager's id, and the id it had before the staffing model (step 0.2d). Rows written before the
rename keep the old id: `llm_calls.agent`, `agent_requests.from_agent`, `activity_events.actor`, `bets.origin`, a
door rule's `decider`, a profile value's `written_by`, a response's `by` and `responded_by`, a chat thread's
`default_agent`, an agent's stop and a queued job's `agent`. A reader that matches one of them reads it through
`current`, and new rows get the new id. The one exception is `cycle_stages.summary_by`, a String(8) column the
new id does not fit (`SUMMARY_BY`)."""

PROJECT_MANAGER = "project_manager"
FORMER_PROJECT_MANAGER = "ceo"
# What `cycle_stages.summary_by` stores for a summary the Project Manager wrote, until a migration widens it.
SUMMARY_BY = FORMER_PROJECT_MANAGER


def current(agent: str) -> str:
    """The agent's id today, for an id read from a row that may predate the rename."""
    return PROJECT_MANAGER if agent == FORMER_PROJECT_MANAGER else agent


def stored_ids(agent: str) -> tuple[str, ...]:
    """Every id a stored row may hold for this agent, for a query that matches rows."""
    return (PROJECT_MANAGER, FORMER_PROJECT_MANAGER) if current(agent) == PROJECT_MANAGER else (agent,)
