from justshipit.services import tasks
from justshipit.settings import staffing_config


def test_the_staffing_settings_are_present_and_in_range():
    cfg = staffing_config()["staffing"]

    assert isinstance(cfg["always_check"], bool)
    assert isinstance(cfg["solo_max_criteria"], int) and cfg["solo_max_criteria"] >= 0
    assert cfg["audit_budget_usd"] > 0
    for rate in ("audit_rate", "solo_max_unmet_rate", "model_tolerance", "explore_rate"):
        assert 0 <= cfg[rate] <= 1, rate
    assert isinstance(cfg["min_samples"], int) and cfg["min_samples"] >= 1


def test_small_leaf_tasks_run_alone_now_that_the_records_are_in_place():
    assert staffing_config()["staffing"]["always_check"] is False, "unit 5 turns alone tasks on (design section 11)"


def test_the_family_list_is_a_workable_size_and_has_a_landing_place():
    families = staffing_config()["families"]

    assert 8 <= len(families) <= 12
    assert "other" in families
    assert all(isinstance(about, str) and about.strip() for about in families.values())


def test_every_assignable_role_has_a_default_family_on_the_list():
    cfg = staffing_config()

    assert set(cfg["owner_default_family"]) == tasks._assignable_owners()
    assert set(cfg["owner_default_family"].values()) <= set(cfg["families"])
