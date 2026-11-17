"""The engineer binds each journey step of the spec to how a person does it on the build (scripts/walkthrough.yaml),
and the lint says what keeps a step from running before the acceptance check runs it. The journeys are uxscope's,
the CLI REQ-7 asked the owner to merge: Mina turns her habit app's feature list into screen specs."""
import os

import pytest
from justshipit.contracts.handoff import AboutOut, Handoff, JourneyOut, JourneyStepOut
from justshipit.engineering import journeys, walkthrough, worker
from justshipit.services import registry


def _step(do: str) -> JourneyStepOut:
    return JourneyStepOut(do=do, why="so she has a place for her app", see="a folder habit-app with a features file")


DOC = journeys.number(
    AboutOut(what="uxscope turns a feature list into a flow spec per screen", who="a solo founder",
             problem="they don't know which screens they need"),
    [JourneyOut(title="Turn a feature list into screen specs", persona="Mina, planning a habit app alone",
                why="she wants to know which screens to build", brings="6 features in a text file",
                success="one spec per screen", priority="P1",
                steps=[_step("Start a project for her app"), _step("Make the specs from her list")]),
     JourneyOut(title="Fix a broken list", persona="Mina again", why="her list has a typo", brings="a broken file",
                success="she knows what to fix", priority="P2",
                steps=[_step("Check the broken list"), _step("See the line to fix")])])

ENTRIES = {
    "JN1.1": '{ run: "npx uxscope init habit-app" }',
    "JN1.2": '{ run: "npx uxscope generate habit-app/features.md --out habit-app/out", show: "habit-app/out/spec.md", '
             'seed: { "habit-app/features.md": "fixtures/journeys/habit-features.md" } }',
    "JN2.1": '{ run: "npx uxscope check broken.md", expect: error }',
    "JN2.2": '{ open: "/check?file=broken.md" }',
}


def _binding(changed: dict[str, str | None] | None = None) -> str:
    """The binding of ENTRIES with `changed` entries replaced, and an entry of None left out."""
    entries = {**ENTRIES, **(changed or {})}
    return "setup:\n  - sh scripts/ci-setup.sh\nsteps:\n" + "".join(f"  {k}: {v}\n" for k, v in entries.items() if v)


def _quoted(run: str) -> str:
    return "{ run: '" + run.replace("'", "''") + "' }"


def _branch(root, binding: str):
    for rel, text in {walkthrough.BINDING: binding, "fixtures/journeys/habit-features.md": "- streaks\n"}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def _lint(tmp_path, binding: str) -> list[str]:
    read, problems = walkthrough.read_binding(_branch(tmp_path, binding))
    return problems + walkthrough.lint(read, DOC)


def test_a_binding_that_does_every_step_as_a_person_would_lints_clean(tmp_path):
    read, problems = walkthrough.read_binding(_branch(tmp_path, _binding()))

    assert problems == [] and walkthrough.lint(read, DOC) == []
    assert read["setup"] == ["sh scripts/ci-setup.sh"]
    assert read["steps"]["JN1.2"] == {"run": "npx uxscope generate habit-app/features.md --out habit-app/out",
                                      "show": "habit-app/out/spec.md",
                                      "seed": {"habit-app/features.md": "fixtures/journeys/habit-features.md"}}
    assert read["steps"]["JN2.1"] == {"run": "npx uxscope check broken.md", "expect": "error"}
    assert read["steps"]["JN2.2"] == {"open": "/check?file=broken.md"}


def test_a_step_the_binding_leaves_out_is_named(tmp_path):
    assert _lint(tmp_path, _binding({"JN2.1": None})) == [
        ("These journey steps have no entry under `steps:` in `scripts/walkthrough.yaml`: JN2.1. Bind each to the `run` "
        "line or the `open` path a person uses for it.")]


def test_a_branch_with_no_binding_is_told_to_write_one_and_a_spec_without_journeys_asks_for_none(tmp_path):
    assert walkthrough.read_binding(tmp_path) == (None, [])
    [told] = walkthrough.lint(None, DOC)
    assert told.startswith("There is no `scripts/walkthrough.yaml`, so the worker cannot run any step")
    assert walkthrough.lint(None, {"about": None, "journeys": []}) == []


def test_an_id_no_journey_step_has_is_named(tmp_path):
    found = _lint(tmp_path, _binding({"JN3.1": '{ run: "npx uxscope init x" }', "JN1-1": '{ run: "npx uxscope" }'}))

    assert found == [("These `scripts/walkthrough.yaml` steps name no step of `.ship/spec/journeys.yaml`: JN3.1, JN1-1. "
                     "Use the ids exactly as journeys.yaml gives them.")]


@pytest.mark.parametrize("run", ['echo "Created habit-app"', "printf 'ok\\n' > out.txt && cat out.txt",
                                 "cat habit-app/out/spec.md", "true; :", 'DONE=1 echo "spec.md written"',
                                 'echo "$(cat habit-app/out/spec.md)"'])
def test_a_run_that_only_prints_is_refused(tmp_path, run):
    assert _lint(tmp_path, _binding({"JN1.1": _quoted(run)})) == [
        ("JN1.1's `run` never calls the product: it only runs echo, printf, cat, true or :. Run the product's own "
        "command, the way its README tells a person to, and let it print.")]


@pytest.mark.parametrize("run", ["npx uxscope init habit-app && cat habit-app/features.md",
                                 'echo "$(npx uxscope init habit-app)"', "cd habit-app && python -m uxscope init ."])
def test_a_run_that_calls_the_product_beside_a_print_is_fine(tmp_path, run):
    assert _lint(tmp_path, _binding({"JN1.1": _quoted(run)})) == []


@pytest.mark.parametrize("show", ["../secret.txt", "/etc/passwd", "habit-app/../../x", ""])
def test_a_show_outside_the_journey_folder_is_refused(tmp_path, show):
    found = _lint(tmp_path, _binding({"JN1.1": '{ run: "npx uxscope init habit-app", show: "' + show + '" }'}))

    assert found == ["JN1.1's `show` must be a file inside the folder the step runs in: a relative path with no `..`."]


def test_a_seed_from_outside_fixtures_journeys_or_put_outside_the_folder_is_refused(tmp_path):
    found = _lint(tmp_path, _binding({"JN1.2": '{ run: "npx uxscope generate in.md", seed: { "in.md": "README.md", '
                                               '"../up.md": "fixtures/journeys/habit-features.md", '
                                               '"x.md": "fixtures/journeys/../../.env" } }'}))

    assert found == [
        "JN1.2's `seed` for in.md must come from `fixtures/journeys/`: README.md does not.",
        "JN1.2's `seed` puts a file at ../up.md, outside the folder the step runs in: give a relative path with no `..`.",
        "JN1.2's `seed` for x.md must come from `fixtures/journeys/`: fixtures/journeys/../../.env does not."]


def test_a_seed_the_branch_does_not_ship_is_named(tmp_path):
    assert _lint(tmp_path, _binding().replace("habit-features.md", "missing.md")) == [
        ("JN1.2's `seed` source fixtures/journeys/missing.md is not a plain file of at most 20000 bytes in the branch: "
        "commit it there.")]


_LONG = "npx uxscope init " + "x" * 300


@pytest.mark.parametrize("entry, said", [
    ('{ run: "npx uxscope init", open: "/" }', "JN1.1 has both `run` and `open`: a step is one or the other."),
    ("{ expect: error }", "JN1.1 has neither `run` nor `open`."),
    (_quoted(_LONG), f"JN1.1's `run` is {len(_LONG)} characters: keep it to one line of at most 300."),
    ('{ run: "npx uxscope init\\nrm -rf habit-app" }', "JN1.1's `run` has a line break: it must be one shell line."),
    ('{ run: "npx uxscope init \'habit app" }', "JN1.1's `run` is not a valid shell line: a quote is left open."),
    ('{ open: "today" }', "JN1.1's `open` is not a path on the preview address: it starts with / and has no spaces."),
    ('{ run: "npx uxscope init", expect: fails }', "JN1.1's `expect` is 'fails': it is ok (the default) or error."),
    ('{ run: "npx uxscope init", output: "Created" }',
     "JN1.1 has an unknown key `output`: a step takes run, open, show, seed, expect."),
])
def test_each_entry_that_cannot_run_as_written_says_why(tmp_path, entry, said):
    assert _lint(tmp_path, _binding({"JN1.1": entry})) == [said]


def test_the_binding_is_read_only_as_a_plain_file_of_a_bounded_size(tmp_path):
    big = _branch(tmp_path / "big", _binding() + "#" * 64_000 + "\n")
    assert walkthrough.read_binding(big) == (None, [("`scripts/walkthrough.yaml` is larger than 64000 bytes: it holds "
                                                    "actions only, never output.")])
    assert walkthrough.read_binding(_branch(tmp_path / "list", "- npx uxscope init\n")) == (
        None, ["`scripts/walkthrough.yaml` must be a mapping with `setup:` and `steps:`."])

    outside = tmp_path / "outside.yaml"
    outside.write_text(_binding(), encoding="utf-8")
    linked = tmp_path / "linked"
    (linked / "scripts").mkdir(parents=True)
    try:
        os.symlink(outside, linked / walkthrough.BINDING)
    except OSError:
        pytest.skip("this system makes no symbolic links")
    assert walkthrough.read_binding(linked) == (None, [("`scripts/walkthrough.yaml` is a link or not a plain file: make "
                                                       "it a plain file the branch commits.")])


def _handoff(path: str, files: dict[str, str]) -> Handoff:
    sha = registry.write_files({f"{path}/{k}": v for k, v in files.items()}, "binding spec")
    return Handoff(spec_id="S", project="p", title="t", target={"repository": "x"},
                   source={"path": path, "commit_sha": sha})


def test_the_review_and_fix_rounds_tell_the_engineer_what_the_binding_gets_wrong(tmp_path):
    with_journeys = _handoff("specs/walk-bound", {"prd.md": "p", journeys.FILE: journeys.dump(DOC)})
    branch = _branch(tmp_path / "printing", _binding({"JN2.1": "{ run: 'echo broken', expect: error }"}))

    told = worker._walkthrough_lint(branch, with_journeys)

    assert told.startswith("\n\n## Walkthrough binding\n- JN2.1's `run` never calls the product")
    assert worker._walkthrough_lint(_branch(tmp_path / "good", _binding()), with_journeys) == ""
    assert worker._walkthrough_lint(branch, _handoff("specs/walk-none", {"prd.md": "p"})) == "", \
        "a spec from before journeys asks for no binding"


def test_the_engineer_is_told_to_bind_the_journeys_only_where_the_spec_has_them():
    told = worker.core_flow_build(_handoff("specs/walk-told", {"prd.md": "p"}))

    assert "When `.ship/spec/journeys.yaml` is there" in told and "`scripts/walkthrough.yaml`" in told
    assert "never write output into this file" in told


def test_a_binding_with_anchors_is_refused_before_an_alias_can_multiply_it(tmp_path):
    bomb = "a: &a [x, x, x, x, x, x, x, x, x]\nb: &b [*a, *a, *a, *a, *a, *a, *a, *a, *a]\nsetup: [*b, *b, *b]\nsteps: {}\n"

    read, problems = walkthrough.read_binding(_branch(tmp_path, bomb))

    assert read is None and len(problems) == 1 and "aliases" in problems[0]


@pytest.mark.parametrize("hidden", ["‮", "​", "⁦"])  # a bidi override, a zero-width space, an isolate
def test_a_line_with_a_character_the_card_would_not_show_is_refused(tmp_path, hidden):
    found = _lint(tmp_path, _binding({"JN1.1": _quoted(f"npx uxscope init habit{hidden}-app"),
                                      "JN2.2": "{ open: '/check" + hidden + "' }"}))

    assert any(x.startswith("JN1.1's `run` has a control or invisible character") for x in found)
    assert any(x.startswith("JN2.2's `open` is not a path") for x in found)
