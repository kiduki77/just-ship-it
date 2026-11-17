"""The host's UI probe opens a new product's screens in a real browser and finds what a screenshot cannot: focus lost
after a press, a list that scrolls back to the top, focus and selection that forced colours hide, buttons sharing one
name, and a live region rebuilt instead of updated. Its findings go to the reviewer and the fix round beside the look
check; a preview that does not start is one finding, not a failed job."""
import functools
import http.server
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from justshipit.engineering import style_packs, ui_probe
from justshipit.engineering.style_packs import design_md
from justshipit.services import projects
from test_given_design import _FIX, _PASS, _handoff, _reviews
from test_product_quality import _told_engineer

FIXTURES = Path(__file__).parent / "fixtures" / "ui_probe"
_BROWSERS = ("chromium", "chromium-browser", "google-chrome", "chrome",
             r"C:\Program Files\Google\Chrome\Application\chrome.exe",
             r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def _browser() -> str | None:
    for name in (os.environ.get("SHIP_TEST_CHROMIUM"), *_BROWSERS):
        if name and (found := shutil.which(name) or (name if Path(name).is_file() else None)):
            return found
    return None


@pytest.fixture(scope="module")
def probed(tmp_path_factory) -> ui_probe.Result:
    """One run of the real probe, in a real browser, over the page with every bug and the page with none."""
    browser = _browser()
    if not shutil.which("node") or not browser:
        pytest.skip("the probe needs node and a chromium browser")
    states = tmp_path_factory.mktemp("states") / "ui-states.txt"
    states.write_text("bad /bad.html\nclean /clean.html\n", encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(FIXTURES))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.RequestHandlerClass.log_message = lambda *a: None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        run = subprocess.run(ui_probe.command(browser, f"http://127.0.0.1:{server.server_address[1]}", str(states)),
                             capture_output=True, text=True, encoding="utf-8", timeout=ui_probe.BUDGET_S + 30,
                             check=False)
    finally:
        server.shutdown()
    return ui_probe.parse(run.stdout)


def _of(result: ui_probe.Result, state: str, check: str) -> list[str]:
    return [f for f in result.found if f.startswith(f"{state} [{check}]: ")]


def test_buttons_sharing_one_name_in_a_region_are_named(probed):
    assert _of(probed, "bad", "names") == [(
        'bad [names]: 5 buttons share the accessible name "Not a problem" in main#app (button); a screen reader user '
        "cannot tell which item each acts on")]


def test_focus_that_falls_to_the_body_after_a_keyboard_press_is_found(probed):
    [lost] = _of(probed, "bad", "focus")
    assert lost.startswith('bad [focus]: focus fell to the page body after pressing button "All" (button)')
    assert 'button "Not a problem" (button) x5' in lost


def test_a_list_that_scrolls_back_to_the_top_after_a_press_is_found(probed):
    reset, idle = _of(probed, "bad", "scroll")
    assert reset.startswith('bad [scroll]: ul scrolled back to the top after pressing button "All" (button)')
    assert idle.startswith("bad [scroll]: div.notes is made to scroll but rebuilt as new nodes after pressing"),         "a list too short to scroll yet is found by its rebuild"


def test_focus_and_selection_that_forced_colours_hide_are_found(probed):
    focus, selection = _of(probed, "bad", "forced-colors")
    assert focus.startswith("bad [forced-colors]: 8 of 8 focus stops show no focus in forced colors")
    assert "the focus ring is a box-shadow, which forced colors removes" in focus
    assert selection.startswith('bad [forced-colors]: the aria-pressed item "All" (button) looks the same')


def test_a_live_region_rebuilt_on_a_press_is_found(probed):
    [live] = _of(probed, "bad", "live-region")
    assert live.startswith("bad [live-region]: the status node (p) was replaced by a new node, not updated")


def test_a_page_that_updates_in_place_has_no_finding(probed):
    assert [f for f in probed.found if not f.startswith("bad ")] == []
    assert probed.note.startswith("It probed 3 screens and pressed"), "the first open, bad and clean"


def test_a_preview_that_does_not_start_is_one_finding():
    said = "[preview did not answer on http://127.0.0.1:4173]\nError: Cannot find module 'vite'\n"
    result = ui_probe.parse(said)
    assert result.note == "The probe could not run."
    [only] = result.found
    assert only.startswith("The preview did not start (preview did not answer on http://127.0.0.1:4173)")
    assert "Cannot find module 'vite'" in only


def test_a_probe_cut_short_says_so_and_keeps_what_it_found():
    said = "[ui-probe] report\tfocus\tfocus fell to the page body after pressing x\n"
    assert ui_probe.parse(said) == ui_probe.Result(
        ["report [focus]: focus fell to the page body after pressing x"],
        "The probe stopped early: it printed no result.")
    capped = ui_probe.parse("[ui-probe-done] states=4 presses=30 stopped-at-cap=usage-list,usage-empty\n")
    assert capped.note == ("It probed 4 screens and pressed 30 buttons with the keyboard. It stopped at its 90 s cap "
                           "before: usage-list, usage-empty.")


def _product(dest: Path, preview: bool = True) -> None:
    (dest / "scripts").mkdir(parents=True, exist_ok=True)
    (dest / "DESIGN.md").write_text(design_md(style_packs.default_look()), encoding="utf-8", newline="")
    if preview:
        (dest / "scripts" / "preview.sh").write_text("exec npx vite preview\n", encoding="utf-8")


_FOUND = ui_probe.Result(['report [scroll]: ul.rows scrolled back to the top after pressing button "Not a problem"'],
                         "It probed 3 screens and pressed 12 buttons with the keyboard.")


def test_the_reviewer_and_the_fix_round_get_the_probe_on_an_export_of_its_own(s, project, tmp_path, monkeypatch):
    probed_at: list[Path] = []

    def probe(export: Path, lang: str) -> ui_probe.Result:
        probed_at.append(export)
        assert (export / "scripts" / "preview.sh").is_file() and lang in ("en", "ko")
        return _FOUND

    monkeypatch.setattr(ui_probe, "probe", probe)
    told = _told_engineer(s, project, tmp_path, monkeypatch, _handoff(project.slug),
                          export=lambda repo, branch, dest: _product(dest), outputs=_reviews(_FIX, _PASS),
                          given=design_md(style_packs.default_look()))

    review = next(p for p in told if p.startswith("Review the current branch"))
    fix = next(p for p in told if p.startswith("Fix the problems below"))
    for prompt in (review, fix):
        assert "## UI probe (the host's, in a real browser" in prompt
        assert "It probed 3 screens and pressed 12 buttons with the keyboard." in prompt
        assert 'report [scroll]: ul.rows scrolled back to the top after pressing button "Not a problem"' in prompt
    assert len(probed_at) == 2, "every round probes the branch it reviews"
    assert all(p.name.startswith("ship-probe-") and not p.exists() for p in probed_at), \
        "the preview builds in its own export, never in the reviewer's directory"


def test_a_passing_probe_tells_the_reviewer_and_the_fix_round_hears_nothing(s, project, tmp_path, monkeypatch):
    monkeypatch.setattr(ui_probe, "probe", lambda export, lang: ui_probe.Result([], "It probed 1 screens."))
    told = _told_engineer(s, project, tmp_path, monkeypatch, _handoff(project.slug),
                          export=lambda repo, branch, dest: _product(dest), outputs=_reviews(_FIX, _PASS),
                          given=design_md(style_packs.default_look()))

    review = next(p for p in told if p.startswith("Review the current branch"))
    assert "## UI probe" in review and "Passed: no lost focus, scroll reset" in review
    assert "## UI probe" not in next(p for p in told if p.startswith("Fix the problems below"))


def test_no_probe_without_a_preview_script_or_for_a_product_that_keeps_its_own_look(s, project, tmp_path,
                                                                                    monkeypatch):
    calls: list[Path] = []
    monkeypatch.setattr(ui_probe, "probe", lambda export, lang: calls.append(export) or _FOUND)
    no_preview = _told_engineer(s, project, tmp_path, monkeypatch, _handoff(project.slug),
                                export=lambda repo, branch, dest: _product(dest, preview=False),
                                given=design_md(style_packs.default_look()))
    other = projects.create(s, "주문 정리", name="Its own look", start=False)
    own_look = _told_engineer(s, other, tmp_path / "old", monkeypatch, _handoff(other.slug),
                              export=lambda repo, branch, dest: _product(dest))

    for told in (no_preview, own_look):
        assert "## UI probe" not in next(p for p in told if p.startswith("Review the current branch"))
    assert calls == []


def test_a_preview_that_fails_reaches_the_reviewer_as_a_finding_not_a_failed_job(s, project, tmp_path, monkeypatch):
    from justshipit.db.models import DevJob
    from justshipit.sandbox import docker

    ran = docker.ContainerResult(exit_code=96, stdout=b"[preview did not answer on http://127.0.0.1:4173]\nboom\n",
                                 stderr=b"", timed_out=False)
    monkeypatch.setattr(docker, "run", lambda spec, timeout_s: ran)
    told = _told_engineer(s, project, tmp_path, monkeypatch, _handoff(project.slug),
                          export=lambda repo, branch, dest: _product(dest), given=design_md(style_packs.default_look()))

    review = next(p for p in told if p.startswith("Review the current branch"))
    assert "The probe could not run." in review
    assert "The preview did not start (preview did not answer on http://127.0.0.1:4173)" in review
    assert s.query(DevJob).filter(DevJob.error.is_not(None)).count() == 0
