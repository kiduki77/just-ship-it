"""The three tracking keys of every channel (`tag`, `link`, `link_basis`) and the load checks that guard them."""
import re
from types import SimpleNamespace

import pytest
from justshipit.services import channels

HN_TAIL = "    self_written: true\n    fits: {buyer: [developers], reach: [communities]}"
HN_KEYS = "    tag: hn\n    link: plain\n    link_basis: none\n" + HN_TAIL


def _registry_from(tmp_path, monkeypatch, old: str, new: str) -> None:
    text = (channels.settings().config_dir / "channels.yaml").read_text(encoding="utf-8")
    assert old in text
    (tmp_path / "channels.yaml").write_text(text.replace(old, new, 1), encoding="utf-8")
    monkeypatch.setattr(channels, "settings", lambda: SimpleNamespace(config_dir=tmp_path))
    channels.registry.cache_clear()


def _refused(match: str) -> None:
    try:
        with pytest.raises(ValueError, match=match):
            channels.registry()
    finally:
        channels.registry.cache_clear()


def test_every_channel_names_its_tag_its_link_and_what_the_link_rests_on():
    tags = [c.tag for c in channels.registry().values()]
    assert len(tags) == len(set(tags))
    assert all(re.fullmatch("[a-z]{2,4}", t) for t in tags)
    assert {k for k, c in channels.registry().items() if c.link_basis == "verified"} == {"lobsters"}
    assert {c.link_basis for c in channels.registry().values()} == {"none", "verified"}


def test_hacker_news_reddit_and_the_places_that_name_themselves_get_the_plain_address():
    by_link = {k: c.link for k, c in channels.registry().items()}
    assert {k for k, v in by_link.items() if v == "plain"} == {
        "hacker_news", "show_hn", "reddit", "reddit_opensource", "reddit_sideproject", "reddit_selfhosted",
        "reddit_claudeai", "reddit_local", "product_hunt", "github_release", "awesome_list", "lobsters"}
    assert {k for k, v in by_link.items() if v == "none"} == {
        "daangn_community", "daangn_jobs", "instagram_local", "nextdoor"}
    assert by_link["discord_global"] == by_link["naver_cafe"] == by_link["other"] == "tagged"


@pytest.mark.parametrize(("keys", "lacks"), [
    ("    link: plain\n    link_basis: none\n", "tag"),
    ("    tag: hn\n    link_basis: none\n", "link"),
    ("    tag: hn\n    link: plain\n", "link_basis"),
])
def test_a_channel_without_one_of_its_tracking_keys_stops_the_registry_at_load(tmp_path, monkeypatch, keys, lacks):
    _registry_from(tmp_path, monkeypatch, HN_KEYS, keys + HN_TAIL)
    _refused(rf"hacker_news lacks {lacks}\b")


def test_a_tag_two_channels_share_stops_the_registry_at_load_and_names_both(tmp_path, monkeypatch):
    _registry_from(tmp_path, monkeypatch, "    tag: sh\n", "    tag: hn\n")
    _refused(r"hn \(hacker_news, show_hn\)")


@pytest.mark.parametrize("new", ["    tag: H\n", "    tag: hnews1\n", "    tag: h\n", "    tag: h-n\n"])
def test_a_tag_that_is_not_two_to_four_lowercase_letters_stops_the_registry_at_load(tmp_path, monkeypatch, new):
    _registry_from(tmp_path, monkeypatch, "    tag: hn\n", new)
    _refused("hacker_news tag")


def test_an_unknown_link_or_basis_stops_the_registry_at_load(tmp_path, monkeypatch):
    _registry_from(tmp_path, monkeypatch, HN_KEYS, HN_KEYS.replace("link: plain", "link: short"))
    _refused("hacker_news link 'short'")
