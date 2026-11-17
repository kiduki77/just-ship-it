"""In a business that runs in cycles, an experiment's retro proposes the next work one way only: its suggestions stay in
the retro for the CEO's next cycle proposal, with no new experiment and no decision card."""
import pytest
import bet_loop_kit as kit
from justshipit.contracts.retro import RetroDraft
from justshipit.db.models import AgentRequest, Bet
from justshipit.services import bet_retro
from sqlalchemy import func, select
from test_bet_retro import draft, ended_bet, next_bet, people_draft


pytestmark = pytest.mark.usefixtures("own_jobs_settled")


def test_a_retro_in_a_cycle_keeps_its_next_steps_in_the_retro(s, monkeypatch):
    kit.reset_world()
    b = kit.make_business(s, name="사이클 회고")
    bet = ended_bet(s, b)
    bets_before = s.scalar(select(func.count()).select_from(Bet).where(Bet.business_id == b.id))
    cards_before = s.scalar(select(func.count()).select_from(AgentRequest))
    picks = [next_bet("서브레딧 세 곳으로"), next_bet("범위 밖 실험", scope=False, lines=())]

    def project_manager(agent, purpose, prompt, output_model, **kw):
        return (draft(verdict="routine", bets=picks) if output_model is RetroDraft
                else people_draft(verdict="routine", bets=picks))
    monkeypatch.setattr(bet_retro, "run_agent", project_manager)
    bet_retro.run(bet.id, lang="ko")
    s.expire_all()
    settled = s.get(Bet, bet.id)
    assert [x["title"] for x in settled.retro["next_bets"]] == ["서브레딧 세 곳으로", "범위 밖 실험"]
    assert s.scalar(select(func.count()).select_from(Bet).where(Bet.business_id == b.id)) == bets_before
    assert s.scalar(select(func.count()).select_from(AgentRequest)) == cards_before
    assert settled.status in ("DONE", "STOPPED")
    kit.reset_world()
