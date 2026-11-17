"""The host's probe of a new product's screens in a real browser: what a screenshot cannot show. For the first open and
each state in scripts/ui-states.txt it tabs through the page and presses each button with the keyboard, and finds
  - buttons and links in one region that share an accessible name (or have none),
  - focus that falls to the page body after a press,
  - a scrolled list that jumps back to the top after a press,
  - focus or a selected mark that forced colours make invisible (a box-shadow ring, a colour-only selection),
  - a live region (role=status, role=alert, aria-live) that a press replaces instead of updating.
A press that could lose or send data, or open a file, save or print dialog, is never made.

`ui_probe.mjs` does the work with Node's standard library, driving chromium over its debugging pipe, so it runs in the
engineer image as the screen capture does (`worker.capture_screens`) and adds no dependency. `worker.test_and_review`
runs it on its own export of the branch, and its findings go to the reviewer and the fix round beside the look check."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from justshipit.engineering import try_it
from justshipit.sandbox import docker
from justshipit.settings import company_config

PROBE = Path(__file__).with_name("ui_probe.mjs")
BUDGET_S = 90  # the probe's own time for one product, after the preview answers
_MARK = "[ui-probe] "
_DONE = re.compile(r"^\[ui-probe-done\] states=(\d+) presses=(\d+)(?: stopped-at-cap=(\S+))?", re.MULTILINE)
_ERROR = re.compile(r"^\[ui-probe-error\] (.*)$", re.MULTILINE)
_NOT_UP = re.compile(r"^\[(no scripts/preview\.sh|ci-setup failed|preview did not answer on [^\]]+)\]", re.MULTILINE)
_MAX_FINDINGS = 30
_FINDING_CHARS = 400
_IN = "/probe/ui_probe.mjs"


@dataclass(frozen=True)
class Result:
    found: list[str]  # one line per problem, "state: what (which element)"; empty when the screens pass
    note: str  # what the probe covered, or why it could not look


def command(browser: str, base: str, states: str | None, lang: str = "en-US", budget_s: int = BUDGET_S,
            probe: str = str(PROBE)) -> list[str]:
    return ["node", probe, "--browser", browser, "--base", base, "--budget", str(budget_s), "--lang", lang,
            *(["--states", states] if states else [])]


def parse(said: str) -> Result:
    """The probe's findings from what the run printed. A run with no closing line says why it stopped; a preview
    that never started is itself the one finding, since no one can use a product that does not open."""
    found = []
    for line in said.splitlines():
        if not line.startswith(_MARK):
            continue
        state, _, rest = line[len(_MARK):].partition("\t")
        check, _, text = rest.partition("\t")
        if text:
            found.append(f"{state} [{check}]: {text}"[:_FINDING_CHARS])
    if len(found) > _MAX_FINDINGS:
        found[_MAX_FINDINGS:] = [f"... and {len(found) - _MAX_FINDINGS} more."]
    if m := _NOT_UP.search(said):
        tail = said[m.end():].strip()[-600:]
        return Result([f"The preview did not start ({m[1]}), so no screen could be probed. Make scripts/preview.sh "
                       f"build and serve on {try_it.PREVIEW_ADDRESS}." + (f" Its log ends: {tail}" if tail else "")],
                      "The probe could not run.")
    if done := _DONE.search(said):
        note = f"It probed {done[1]} screens and pressed {done[2]} buttons with the keyboard."
        if done[3]:
            note += f" It stopped at its {BUDGET_S} s cap before: {done[3].replace(',', ', ')}."
        return Result(found, note)
    error = _ERROR.search(said)
    return Result(found, f"The probe stopped early: {error[1] if error else 'it printed no result'}.")


def _script(lang: str) -> str:
    """Run in the engineer image on an export of the branch: install, start scripts/preview.sh, probe."""
    up = try_it.PREVIEW_ADDRESS
    return f"""set -u
export PATH="$HOME/.local/bin:$PATH"
[ -f scripts/preview.sh ] || {{ echo '[no scripts/preview.sh]'; exit 98; }}
if [ -f scripts/ci-setup.sh ]; then sh scripts/ci-setup.sh > /tmp/ci-setup.log 2>&1 \\
  || {{ echo '[ci-setup failed]'; tail -30 /tmp/ci-setup.log; exit 97; }}; fi
sh scripts/preview.sh > /tmp/preview.log 2>&1 &
up=; i=0; while [ $i -lt 180 ]; do curl -s -o /dev/null {up}/ && {{ up=1; break; }}; i=$((i+1)); sleep 1; done
[ -n "$up" ] || {{ echo '[preview did not answer on {up}]'; tail -30 /tmp/preview.log; exit 96; }}
states=; [ -f scripts/ui-states.txt ] && states="--states scripts/ui-states.txt"
exec node {_IN} --browser chromium --base {up} --budget {BUDGET_S} --lang {lang} $states
"""


def probe(export: Path, lang: str) -> Result:
    """Probe the product exported at `export`, in the engineer image. `lang` is the browser's language tag."""
    if not docker.docker_ok():
        return Result([], "The probe did not run: Docker could not be reached.")
    image = company_config()["engineering"]["sandbox"]["image"]
    user = f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else "1000:1000"
    try:
        res = docker.run(docker.ContainerSpec(image=image, command=["sh", "-c", _script(lang)],
                                              mounts=[docker.Mount(export, "/work"),
                                                      docker.Mount(PROBE, _IN, read_only=True)],
                                              network="sandbox", memory="4g", cpus="2", user=user,
                                              env={"HOME": "/tmp"}, name_prefix="ship-ui-probe"),
                         timeout_s=int(company_config()["engineering"]["test_timeout_s"]) + BUDGET_S)
    except docker.SandboxUnavailable as exc:
        return Result([], f"The probe did not run: {exc}")
    result = parse(res.stdout.decode("utf-8", "replace"))
    return Result(result.found, result.note + (" It ran out of time." if res.timed_out else ""))
