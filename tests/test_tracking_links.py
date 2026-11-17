"""The owner's own link on a post card: what `resolve` gives for each channel rule and place, that the server (never the
model) writes it where a to-do is made or a revision replaces its card, that a retry converges, that a card made before
0.5 reads a derived link with no write, and the checks on the places the owner saves."""
import re
from datetime import UTC, datetime

import pytest
from justshipit.contracts.post_card import PostCardDraft
from justshipit.db.models import ActivityEvent, Business, OwnerTask
from justshipit.contracts.requests import HumanDecision, HumanResponse
from justshipit.services import channels, owner_tasks, people, requests, tracking
from justshipit.services.tracking import Door
from sqlalchemy import select

from ask_kit import by_project_manager
from owner_task_kit import approve, draft, post_card, what

SITE = Door("site", "https://acme.dev", "ref")
UTM = Door("site", "https://acme.dev/start?plan=free", "utm_source")
STORE = Door("store", "https://store.example.com/acme", None)
REPO = Door("repo", "https://github.com/acme/tool", None, derived=True)


@pytest.mark.parametrize(("link", "door", "expected"), [
    ("tagged", SITE, "https://acme.dev/?ref=li-1c"),
    ("tagged", UTM, "https://acme.dev/start?plan=free&utm_source=li-1c"),
    ("tagged", STORE, "https://store.example.com/acme"),
    ("tagged", REPO, "https://github.com/acme/tool"),
    ("plain", SITE, "https://acme.dev"),
    ("plain", UTM, "https://acme.dev/start?plan=free"),
    ("none", SITE, None),
    ("none", REPO, None),
])
def test_resolve_tags_only_a_tagged_channel_and_a_place_that_keeps_a_query(link, door, expected):
    assert tracking.resolve(link, "li-1c", door) == expected


def test_a_value_the_address_already_holds_for_the_tag_name_is_replaced_not_repeated():
    door = Door("site", "https://acme.dev/?ref=old&a=1", "ref")

    assert tracking.resolve("tagged", "li-1c", door) == "https://acme.dev/?a=1&ref=li-1c"


def test_without_a_tag_every_link_is_the_plain_address():
    assert tracking.resolve("tagged", None, SITE) == "https://acme.dev"


def test_a_tag_is_the_channel_code_and_the_task_id_in_base_36_and_holds_nothing_else():
    tags = {tracking.tag_of(c, n) for c in channels.registry().values() for n in (1, 35, 36, 1295, 46655, 99999999)}

    assert len(tags) == len(channels.registry()) * 6
    assert all(re.fullmatch("[a-z]{2,4}-[0-9a-z]{1,7}", t) for t in tags)
    assert not [t for t in tags if people.contact_info_problem(t)]
    assert tracking.tag_of(channels.get("linkedin"), 46655) == "li-zzz"


def test_the_model_has_no_field_to_write_a_link_in():
    assert not {"track", "tag", "link", "links"} & set(PostCardDraft.model_fields)


@pytest.fixture
def business(s, project):
    b = s.get(Business, project.business_id)
    tracking.set_doors(s, b.id, [{"kind": "site", "url": "https://acme.dev"}])
    b.github_repo = "acme/tool"
    s.flush()
    return b


def make(s, project, channel="linkedin", **kw) -> OwnerTask:
    req = requests.create(s, draft(post_card=post_card(channel), **kw), from_agent="marketing", project_id=project.id,
                          origin=by_project_manager(s, project.id))
    approve(s, req)
    s.flush()
    return s.scalar(select(OwnerTask).where(OwnerTask.request_id == req.id))


def test_a_new_post_card_carries_the_link_the_server_made_for_its_channel(s, project, business):
    task = make(s, project, "linkedin")

    track = task.card["track"]
    assert track["tag"] == f"li-{tracking._base36(task.id)}" and track["link"] == "tagged"
    assert track["links"] == [{"door": "site", "url": f"https://acme.dev/?ref={track['tag']}", "param": "ref"},
                              {"door": "repo", "url": "https://github.com/acme/tool", "param": None}]


def test_hacker_news_gets_the_plain_address_and_no_tag(s, project, business):
    task = make(s, project, "hacker_news")

    assert task.card["track"]["link"] == "plain"
    assert task.card["track"]["links"] == [{"door": "site", "url": "https://acme.dev", "param": None},
                                           {"door": "repo", "url": "https://github.com/acme/tool", "param": None}]


def test_a_place_with_no_link_gives_the_spoken_line_and_a_business_with_no_place_says_so(s, project, business):
    # No place that gives no link has its rules read, so no card is made for one now: this is a card stored before.
    gone = owner_tasks.create_post_task(s, business.id, {"channel": "daangn_community", "what": {"facts": ["a"]}}, "동네 글")
    assert gone.card["track"]["links"] == []
    said = owner_tasks.view(gone, "en")["track"]
    assert (said["state"], said["say"]) == ("say", f"Tell us you saw this on {channels.get('daangn_community').names['en']}")
    assert owner_tasks.view(gone, "ko")["track"]["say"].endswith("보고 왔다고 말해 주세요")

    business.doors, business.github_repo = [], None
    s.flush()
    bare = make(s, project, "linkedin", title="다른 글")
    assert (bare.card["track"]["links"], owner_tasks.view(bare, "en")["track"]["state"]) == ([], "no_door")


def test_the_view_gives_the_link_on_the_card_and_beside_it_and_never_disturbs_the_card(s, project, business):
    task = make(s, project, "linkedin")

    view = owner_tasks.view(task, "en")

    assert view["track"]["state"] == "tagged" and view["card"]["track"] == view["track"] and view["track"]["derived"] is False
    assert view["card"]["channel"] == "linkedin" and view["card"]["what"]["facts"]


def test_asking_again_gives_the_same_task_and_the_same_tag(s, project, business):
    req = requests.create(s, draft(post_card=post_card("linkedin")), from_agent="marketing", project_id=project.id,
                          origin=by_project_manager(s, project.id))
    approve(s, req)
    first = s.scalar(select(OwnerTask).where(OwnerTask.request_id == req.id))
    before = dict(first.card["track"])

    again = owner_tasks.create_for_request(s, req)
    tracking.attach(s, again)

    assert again.id == first.id and again.card["track"] == before


def test_a_revision_to_another_channel_gets_that_channels_tag_on_the_same_to_do(s, project, business):
    first = requests.create(s, draft(post_card=post_card("linkedin"), title="LinkedIn에 소개"), from_agent="marketing",
                            project_id=project.id, origin=by_project_manager(s, project.id))
    approve(s, first)
    task = s.scalar(select(OwnerTask).where(OwnerTask.request_id == first.id))
    old = task.card["track"]["tag"]

    again = requests.create(s, draft(post_card=post_card("devto"), title="dev.to에 소개", replaces=f"REQ-{first.id}"),
                            from_agent="marketing", project_id=project.id, origin=by_project_manager(s, project.id))
    requests.respond(s, again.id, HumanResponse(decision=HumanDecision.APPROVE, confirm_text=f"REQ-{again.id}"))

    assert task.request_id == again.id and task.card["channel"] == "devto"
    assert old.startswith("li-") and task.card["track"]["tag"] == f"dv-{tracking._base36(task.id)}"
    assert task.card["track"]["links"][0]["url"].endswith(f"?ref={task.card['track']['tag']}")


def test_a_stage_that_holds_the_to_do_leaves_no_row_and_nothing_to_attach(s, project, business, monkeypatch):
    monkeypatch.setattr(owner_tasks, "allowed", lambda *a, **kw: False)
    req = requests.create(s, draft(post_card=post_card("linkedin")), from_agent="marketing", project_id=project.id,
                          origin=by_project_manager(s, project.id))

    approve(s, req)

    assert s.scalar(select(OwnerTask).where(OwnerTask.request_id == req.id)) is None
    assert s.scalar(select(ActivityEvent.id).where(ActivityEvent.verb == "owner_task_held",
                                                   ActivityEvent.business_id == business.id)) is not None


def test_a_weekly_card_keeps_its_fit_record_beside_its_link(s, project, business):
    card, _ = owner_tasks.build_post_card(post_card("linkedin"), None, "en")
    fit = {"codes": {"buyer": ["developers"]}, "guessed": []}

    task = owner_tasks.create_post_task(s, business.id, {**card, "fit": fit}, "Weekly post")

    assert task.card["fit"] == fit and task.card["track"]["link"] == "tagged"
    assert owner_tasks.view(task, "en")["fit_view"] is not None


def test_a_card_made_before_0_5_reads_a_derived_link_and_nothing_is_written(s, project, business):
    task = make(s, project, "linkedin")
    task.card = {k: v for k, v in task.card.items() if k != "track"}
    s.flush()

    track = owner_tasks.view(task, "en")["track"]

    assert track["derived"] is True and track["tag"] == f"li-{tracking._base36(task.id)}"
    assert track["links"][0]["url"] == f"https://acme.dev/?ref={track['tag']}"
    s.expire(task)
    assert "track" not in task.card, "reading never writes the derived link"


def test_a_finished_post_made_before_0_5_has_no_link_to_show(s, project, business):
    task = make(s, project, "linkedin")
    task.card = {k: v for k, v in task.card.items() if k != "track"}
    task.status, task.done_at = "DONE", datetime.now(UTC)
    s.flush()

    assert owner_tasks.view(task, "en")["track"] is None


def test_a_to_do_about_a_person_has_no_link(s, project, business):
    task = OwnerTask(business_id=business.id, project_id=None, kind="contact", title="Talk", card={"person_id": 1}, status="OPEN")
    s.add(task)
    s.flush()
    tracking.attach(s, task)

    assert "track" not in task.card and owner_tasks.view(task, "en")["track"] is None


def test_a_card_filed_under_other_that_names_a_place_is_refused_when_built(s, project, business):
    from justshipit.contracts.post_card import PostCardDraft
    draft = PostCardDraft(channel="other", where_url=None, when={"window": "토요일", "reason": "추측"}, why="가설",
                          what={"title": "당근 동네생활에 첫 글", "body": "본문", "facts": [], "expected_questions": [], "checklist": []})
    with pytest.raises(owner_tasks.CardRefused) as err:
        owner_tasks.build_post_card(draft, None, "en")
    assert err.value.rule == "rules_unchecked"


def test_a_card_stored_before_under_other_that_names_a_place_still_takes_that_places_rule(s, project, business):
    card = {"channel": "other", "what": {"title": "당근 동네생활에 첫 글", "body": "본문"}}
    task = owner_tasks.create_post_task(s, business.id, card, "당근 동네생활에 첫 글")

    assert task.card["track"]["link"] == "none" and task.card["track"]["tag"].startswith("dac-")


# ------------------------------------------------------------------ the owner's places

def doors(s, business, rows):
    return tracking.set_doors(s, business.id, rows)


def test_a_site_defaults_to_ref_and_the_rest_to_no_tag_and_an_explicit_null_keeps_none(s, business):
    saved = doors(s, business, [{"kind": "site", "url": "https://acme.dev"}, {"kind": "store", "url": "https://shop.example.com/a"},
                                {"kind": "site", "url": "https://b.example.com", "param": None},
                                {"kind": "map", "url": "https://map.example.com/p/1", "param": "utm_source"}])

    assert [(d.kind, d.param, d.derived) for d in saved] == [
        ("site", "ref", False), ("store", None, False), ("site", None, False), ("map", "utm_source", False),
        ("repo", None, True)]
    assert all(row["kind"] != "repo" for row in business.doors), "the repository is derived, never stored"


def test_saving_replaces_the_list_and_a_repeated_place_is_kept_once(s, business):
    doors(s, business, [{"kind": "site", "url": "https://old.example.com"}])

    saved = doors(s, business, [{"kind": "site", "url": "https://acme.dev"}, {"kind": "site", "url": "https://acme.dev"}])

    assert [d.url for d in saved if not d.derived] == ["https://acme.dev"]
    assert doors(s, business, []) == [Door("repo", "https://github.com/acme/tool", None, derived=True)]


@pytest.mark.parametrize("row", [
    {"kind": "site", "url": "ftp://acme.dev"},
    {"kind": "site", "url": "not a link"},
    {"kind": "site", "url": "https://a.com\\@b.com"},
    {"kind": "site", "url": "https://user@acme.dev"},
    {"kind": "site", "url": "https://acme.dev/?contact=kim@example.com"},
    {"kind": "site", "url": "https://acme.dev/" + "a" * 600},
    {"kind": "repo", "url": "https://github.com/acme/tool"},
    {"kind": "blog", "url": "https://acme.dev"},
    {"kind": "site", "url": "https://acme.dev", "param": "utm_medium"},
])
def test_an_address_that_cannot_be_used_is_refused_and_nothing_is_saved(s, business, row):
    with pytest.raises(tracking.TrackingError):
        doors(s, business, [{"kind": "site", "url": "https://kept.example.com"}, row])

    assert [d["url"] for d in business.doors] == ["https://acme.dev"]


def test_more_places_than_the_limit_are_refused(s, business):
    rows = [{"kind": "site", "url": f"https://s{i}.example.com"} for i in range(tracking.MAX_DOORS + 1)]

    with pytest.raises(tracking.TrackingError):
        doors(s, business, rows)


def test_links_for_gives_the_primary_first_and_the_plain_address_without_a_tag(s, business):
    assert [(x["door"], x["url"]) for x in tracking.links_for(business, "linkedin")] == [
        ("site", "https://acme.dev"), ("repo", "https://github.com/acme/tool")]
    assert tracking.links_for(business, "nextdoor") == [] and tracking.links_for(business, "nowhere") == []


def test_a_place_added_after_the_card_was_made_reaches_the_open_card_and_not_a_finished_one(s, project, business):
    business.doors, business.github_repo = [], None
    s.flush()
    open_one = make(s, project, "linkedin")
    posted = make(s, project, "linkedin", title="이미 올린 글")
    posted.status, posted.done_at = "DONE", datetime.now(UTC)
    assert owner_tasks.view(open_one, "en")["track"]["state"] == "no_door"

    tracking.set_doors(s, business.id, [{"kind": "site", "url": "https://acme.dev"}])
    s.flush()

    track = owner_tasks.view(open_one, "en")["track"]
    assert (track["state"], track["derived"]) == ("tagged", False)
    assert track["links"] == [{"door": "site", "url": f"https://acme.dev/?ref={track['tag']}", "param": "ref"}]
    assert owner_tasks.view(posted, "en")["track"]["state"] == "no_door"


def test_finishing_a_post_keeps_the_link_as_the_places_stood_then(s, project, business):
    business.doors, business.github_repo = [], None
    s.flush()
    task = make(s, project, "linkedin")
    assert task.card["track"]["links"] == []
    tracking.set_doors(s, business.id, [{"kind": "site", "url": "https://acme.dev"}])

    owner_tasks.done(s, task.id, note="올렸다")
    tracking.set_doors(s, business.id, [{"kind": "site", "url": "https://elsewhere.dev"}])
    s.flush()

    tag = task.card["track"]["tag"]
    assert task.card["track"]["links"] == [{"door": "site", "url": f"https://acme.dev/?ref={tag}", "param": "ref"}]
    assert owner_tasks.view(task, "en")["track"]["links"][0]["url"] == f"https://acme.dev/?ref={tag}"
