"""The build items the owner watches on the Engineering screen come from the `## Tasks` list the engineer writes at the
end of `.ship/plan.md`. That file is the engineer's, so a list that does not read as one, or runs past the caps, is no
list: never a short or guessed one."""
from justshipit.engineering.build_items import MAX_DETAIL, MAX_ITEMS, MAX_TITLE, tasks_of

PLAN = """# Plan: support digest

## Architecture
1. This numbered list is not the tasks.

## Tasks

1. Upload form
   - accepts .eml and .txt
   1. a nested step is part of its task
2. Redaction of names and emails
### Later
3. Group by topic
4. Digest page

## Risks
5. Not a task either.
"""


def test_the_tasks_section_gives_its_numbered_items_in_order_and_nothing_else():
    assert tasks_of(PLAN) == [
        {"n": 1, "title": "Upload form"},
        {"n": 2, "title": "Redaction of names and emails"},
        {"n": 3, "title": "Group by topic"},
        {"n": 4, "title": "Digest page"},
    ]


def test_each_way_an_engineer_writes_an_item_reads_as_its_name_alone():
    plan = "\n".join([
        "## Tasks (build order)",
        "1) Paren numbered",
        "- [ ] 2. Unchecked box",
        "- [x] 3. **Bold name** - what it does, cut",
        "**4.** Bold number: the description, cut",
        "5. [ ] Box after the number",
        "6. **Whole item bold**",
        "7. Link [the docs](https://evil.example/x) and `code` and <b>tags</b> stay words",
        "8. Name \u2014 an em dash description, cut",
    ])
    assert [x["title"] for x in tasks_of(plan)] == [
        "Paren numbered", "Unchecked box", "Bold name", "Bold number", "Box after the number", "Whole item bold",
        "Link the docs and code and tags stay words", "Name",
    ]


def test_a_plan_without_a_tasks_list_has_no_items():
    assert tasks_of("") is None
    assert tasks_of("# Plan\n\n1. Upload\n2. Redact\n") is None, "numbered lines outside the tasks section"
    assert tasks_of("# Plan\n\n## Tasks\n\nWe will figure them out while building.\n\n## Risks\n1. Late\n") is None
    assert tasks_of("## Tasks\n0. zero is no task number\n") is None


def test_a_number_written_twice_keeps_its_first_item():
    assert tasks_of("## Tasks\n1. First\n1. Again\n2. Second\n") == [{"n": 1, "title": "First"}, {"n": 2, "title": "Second"}]


def test_more_items_than_the_cap_is_no_list_rather_than_a_cut_one():
    many = "## Tasks\n" + "".join(f"{i}. Task {i}\n" for i in range(1, 201))
    assert tasks_of(many) is None
    at_cap = "## Tasks\n" + "".join(f"{i}. Task {i}\n" for i in range(1, MAX_ITEMS + 1))
    assert len(tasks_of(at_cap)) == MAX_ITEMS


def test_a_title_is_one_capped_line_of_plain_characters():
    [long] = tasks_of("## Tasks\n1. " + "x" * 5000 + "\n")
    assert len(long["title"]) == MAX_TITLE and long["title"].endswith("\u2026")
    [odd] = tasks_of("## Tasks\n1. Up\u202eload\u200b\x07   form\tnow\n")
    assert odd["title"] == "Upload form now"


def test_a_task_written_as_the_plan_asks_gives_its_name_and_what_works_without_the_technical_notes():
    plan = "\n".join([
        "## Tasks",
        "1. **UX rules and verdicts**: a reviewer sees which rules a screen breaks \u00b7 `rules.ts` + `judge.ts`, S01",
        "2. **Sign-up form:** a visitor can make an account",
        "3. **Command line**",
        "   - `cli.ts`, exit codes, US1.1~US1.5",
        "- [ ] 4. **Checks** \u2014 no colon, so no detail",
        "5. Old style: package.json, tsconfig",
        "6. **Spec page**: one sentence \u00b7 first notes \u00b7 more notes",
        "7. **Rules\u00b7verdicts**: a dot with no space around it stays in the words",
    ])
    assert tasks_of(plan) == [
        {"n": 1, "title": "UX rules and verdicts", "detail": "a reviewer sees which rules a screen breaks"},
        {"n": 2, "title": "Sign-up form", "detail": "a visitor can make an account"},
        {"n": 3, "title": "Command line"},
        {"n": 4, "title": "Checks"},
        {"n": 5, "title": "Old style"},
        {"n": 6, "title": "Spec page", "detail": "one sentence"},
        {"n": 7, "title": "Rules\u00b7verdicts", "detail": "a dot with no space around it stays in the words"},
    ]


def test_an_engineer_note_in_the_old_shape_is_never_shown_as_what_the_task_does():
    plan = "\n".join([
        "## Tasks",
        "1. \uc800\uc7a5\uc18c \ubf08\ub300: package.json(`bin: uxscope`, type module, Node 22), tsconfig, vitest",
        "2. `rules.ts` + `judge.ts`: S01~S12, Kleene",
        "3. **Upload** form: a bold word that is not the whole name",
        "4. __init__: a dunder is no bold name",
    ])
    items = tasks_of(plan)
    assert [x["title"] for x in items] == ["\uc800\uc7a5\uc18c \ubf08\ub300", "rules.ts + judge.ts", "Upload form", "init"]
    assert not any("detail" in x for x in items)


def test_what_works_is_one_capped_line_of_plain_characters():
    [long] = tasks_of("## Tasks\n1. **Upload**: " + "y" * 5000 + " \u00b7 notes\n")
    assert len(long["detail"]) == MAX_DETAIL and long["detail"].endswith("\u2026")
    [odd] = tasks_of("## Tasks\n1. **Up\u202eload**: a [user](https://evil.example/x) <b>sends</b>\u200b\x07   a `file`"
                     "\tnow \u00b7 upload.ts\n")
    assert odd == {"n": 1, "title": "Upload", "detail": "a user sends a file now"}
    [bare] = tasks_of("## Tasks\n1. **Upload**: \u00b7 upload.ts only\n")
    assert bare == {"n": 1, "title": "Upload"}, "nothing before the dot is no detail, never the notes after it"


def test_the_plan_prompt_asks_in_the_owners_language_for_the_shape_the_list_reads():
    import re

    from justshipit.engineering.worker import plan_tasks

    assert "in Korean" in plan_tasks("ko") and "in English" in plan_tasks("en")
    [example] = re.findall(r"For example: `([^`]+)`", plan_tasks("en"))
    assert tasks_of("## Tasks\n" + example) == [
        {"n": 3, "title": "Sign-up form", "detail": "a visitor can make an account with an email and a password"}]


def test_old_and_new_shapes_mix_in_one_list_each_number_once():
    plan = "## Tasks\n1. **Upload**: a user sends a file\n2) Redact: names go\n1. **Again**: a repeat\n**3.** **Group**: topics\n"
    assert tasks_of(plan) == [
        {"n": 1, "title": "Upload", "detail": "a user sends a file"},
        {"n": 2, "title": "Redact"},
        {"n": 3, "title": "Group", "detail": "topics"},
    ]
