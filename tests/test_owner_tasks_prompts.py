"""What the agents are told about posting by hand."""
from justshipit.agents.executor import system_prompt


def test_the_agents_are_told_the_channels_their_modes_and_the_forbidden_asks():
    for agent in ("marketing", "project_manager"):
        text = system_prompt(agent)
        usable, closed = text.split("규칙을 읽지 못한 채널이다.")
        assert "`show_hn`: Show HN (outline)" in usable and "`devto`: dev.to (outline)" in usable
        assert "`hashnode`: Hashnode (draft)" in usable and "`reddit`" not in usable and "`x`" not in usable
        assert "`reddit`: Reddit" in closed and "`x`: X (Twitter)" in closed and "`show_hn`" not in closed
        assert "post_card" in text and "투표, star, 리뷰를 부탁" in text
    assert "오너가 직접 하는 일의 실제 상태" in system_prompt("project_manager")
