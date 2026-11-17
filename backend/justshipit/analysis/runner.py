"""Analysis loop: the analyst agent writes a script, the server fetches its inputs and runs it offline.

Division of trust:
- The model never touches the network or a shell. It only proposes input URLs and a script.
- The server downloads inputs itself (http/https to public addresses only, size- and time-capped) and records
  url + sha256 for every file, so each number traces back to an exact byte-identical source.
- Scripts run in `ship/analysis` with --network none, a read-only root, inputs and script mounted
  read-only and only `out/` writable.
- The final script is re-run in a fresh container; matching results mark the evidence "reproduced".
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy import select

from justshipit.agents.executor import AgentFailed, run_agent
from justshipit.contracts.analysis import AnalysisAction, AnalysisStepOut
from justshipit.contracts.work import AgentResultOut
from justshipit.db.models import Artifact, Evidence, Task
from justshipit.db.session import session_scope
from justshipit.i18n import Lang, msg
from justshipit.orchestration.context import clip
from justshipit.sandbox import docker, plainfile
from justshipit.services import budget, netguard, queue, registry
from justshipit.settings import company_config, settings

_UA = "Mozilla/5.0 (ship analysis fetcher)"
_RESULTS_BYTES = 10_000_000  # of out/results.json; a longer one is no result
_TABLE_BYTES = 500_000  # of an out/*.csv kept in the registry; a longer one stays in the job directory
_SAFE_NAME = re.compile(r"[^0-9A-Za-z\uac00-\ud7a3._-]+")  # Hangul syllables stay: a Korean dataset keeps its name


def _cfg() -> dict:
    return {"image": "ship/analysis:1", "max_steps": 6, "max_download_mb": 100, "max_inputs": 12,
            "download_timeout_s": 300, "run_timeout_s": 300, "memory": "2g", "cpus": 2, **company_config().get("analysis", {})}


@dataclass
class Observation:
    step: int
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    results: str | None
    files: list[str]
    fetch_notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and self.results is not None

    def render(self, *, full: bool) -> str:
        n = 1 if not full else 3
        lines = [f"### Run {self.step} result: exit={self.exit_code}{' (timed out)' if self.timed_out else ''}"]
        if self.fetch_notes:
            lines.append("Fetching inputs:\n" + "\n".join(f"- {x}" for x in self.fetch_notes))
        lines.append(f"stdout (the end):\n```\n{self.stdout[-3000 * n:]}\n```")
        if self.stderr.strip():
            lines.append(f"stderr (the end):\n```\n{self.stderr[-1500 * n:]}\n```")
        lines.append(f"out/results.json:\n```json\n{clip(self.results, 4000 * n) if self.results else '(none)'}\n```")
        if self.files:
            lines.append("Files in out/: " + ", ".join(self.files[:30]))
        return "\n".join(lines)


@dataclass
class AnalysisRecord:
    workdir: Path
    manifest: list[dict] = field(default_factory=list)
    final_script: str | None = None
    final_results: str | None = None
    reproduced: bool = False
    steps: int = 0


# ---------- inputs ----------

_public_address, _transport = netguard.public_address, netguard.transport


def safe_name(name: str, url: str) -> str:
    base = Path(name or urlparse(url).path).name or "input"
    base = _SAFE_NAME.sub("_", base).strip("._") or "input"
    return base[:120]


def fetch(url: str, dest: Path, max_bytes: int, timeout_s: int = 300) -> dict:
    """GET a public file. Redirects are followed by hand so every hop passes the address check. Each hop
    connects to the address it checked (no second DNS lookup), through the owner's HTTP proxy when one is
    set; TLS still verifies the URL's hostname."""
    deadline = time.monotonic() + timeout_s
    current = url
    for _ in range(6):
        u = urlparse(current)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError(f"unsupported URL: {current}")
        ip = _public_address(u.hostname, u.port or (443 if u.scheme == "https" else 80))
        host = (f"[{u.hostname}]" if ":" in u.hostname else u.hostname) + (f":{u.port}" if u.port else "")
        h = hashlib.sha256()
        size = 0
        with httpx.Client(timeout=max(1.0, min(60.0, deadline - time.monotonic())), headers={"User-Agent": _UA},
                          follow_redirects=False, transport=_transport(u.scheme, u.hostname), trust_env=False) as c, \
                c.stream("GET", httpx.URL(current).copy_with(host=ip), headers={"Host": host},
                         extensions={"sni_hostname": u.hostname}) as r:
            if r.is_redirect:
                current = urljoin(current, r.headers.get("location", ""))
                continue
            if r.status_code >= 400:
                raise ValueError(f"HTTP {r.status_code}")
            ctype = r.headers.get("content-type", "")
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes():
                    queue.check_lease()  # a slow stream is otherwise an unbounded stretch without a check
                    size += len(chunk)
                    problem = (f"larger than {max_bytes // 1_000_000} MB" if size > max_bytes else
                               f"over the {timeout_s}s download time limit" if time.monotonic() > deadline else None)
                    if problem:
                        f.close()
                        dest.unlink(missing_ok=True)
                        raise ValueError(problem)
                    h.update(chunk)
                    f.write(chunk)
        return {"url": url, "final_url": current, "sha256": h.hexdigest(), "bytes": size, "content_type": ctype,
                "fetched_at": datetime.now(timezone.utc).isoformat()}
    raise ValueError("too many redirects")


def owner_data_dir(project_slug: str) -> Path:
    return settings().var_dir / "data" / project_slug


def _local(name: str, project_slug: str, dest: Path) -> dict:
    root = owner_data_dir(project_slug).resolve()
    src = (root / name).resolve()
    if root not in src.parents or not src.is_file():
        raise ValueError(f"no owner-provided file named {name}")
    shutil.copyfile(src, dest)
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    return {"url": f"local:{name}", "final_url": None, "sha256": digest, "bytes": dest.stat().st_size,
            "content_type": None, "fetched_at": datetime.now(timezone.utc).isoformat()}


_LEDGER_TASK = re.compile(r"T-?(\d+)(?:\.md)?", re.I)


def ledger_files(project_id: int) -> list[str]:
    """Names the analyst may request as `ledger:<name>`: the evidence ledger and finished task outputs."""
    with session_scope() as s:
        done = s.scalars(select(Task.id).where(Task.project_id == project_id, Task.artifact_id.is_not(None))
                         .order_by(Task.id)).all()
    return ["evidence.json", *(f"T-{i}.md" for i in done)]


def _ledger(name: str, project_id: int, dest: Path) -> dict:
    """Export this project's own records (never another project's) as an input file."""
    with session_scope() as s:
        if name == "evidence.json":
            rows = s.scalars(select(Evidence).where(Evidence.project_id == project_id).order_by(Evidence.id))
            body = json.dumps([{"id": f"EV-{e.id}", "task": f"T-{e.task_id}" if e.task_id else None,
                                "agent": e.agent, "kind": e.kind, "claim": e.claim, "quote": e.quote,
                                "source_url": e.source_url, "source_title": e.source_title,
                                "published_at": e.published_at, "verification_status": e.verification_status,
                                "superseded_by": f"EV-{e.superseded_by}" if e.superseded_by else None}
                               for e in rows], ensure_ascii=False, indent=1)
        elif m := _LEDGER_TASK.fullmatch(name):
            task = s.get(Task, int(m.group(1)))
            art = s.get(Artifact, task.artifact_id) if task and task.artifact_id else None
            if task is None or task.project_id != project_id or art is None:
                raise ValueError(f"no finished task output named {name} in this project")
            body = registry.read(art.path)
        else:
            raise ValueError(f"unknown ledger file {name} (evidence.json or T-<number>.md)")
    dest.write_text(body, encoding="utf-8")
    return {"url": f"ledger:{name}", "final_url": None, "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "bytes": dest.stat().st_size, "content_type": None, "fetched_at": datetime.now(timezone.utc).isoformat()}


def _get_inputs(rec: AnalysisRecord, step: AnalysisStepOut, project_slug: str, project_id: int) -> list[str]:
    cfg = _cfg()
    inputs = rec.workdir / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    notes = []
    for item in step.inputs:
        if len(rec.manifest) >= cfg["max_inputs"]:
            notes.append(f"{item.url}: skipped, over the limit of {cfg['max_inputs']} input files")
            continue
        name = safe_name(item.filename, item.url)
        if any(m["filename"] == name for m in rec.manifest):
            notes.append(f"{name}: already fetched")
            continue
        try:
            if item.url.startswith("local:"):
                meta = _local(item.url[6:], project_slug, inputs / name)
            elif item.url.startswith("ledger:"):
                meta = _ledger(item.url[7:], project_id, inputs / name)
            else:
                meta = fetch(item.url, inputs / name, int(cfg["max_download_mb"]) * 1_000_000,
                             int(cfg["download_timeout_s"]))
        except (ValueError, httpx.HTTPError, OSError) as exc:
            notes.append(f"{item.url}: failed: {exc}")
            continue
        rec.manifest.append({"filename": name, **meta})
        notes.append(f"{name}: {meta['bytes']:,} bytes, {meta['content_type'] or '-'}, sha256 {meta['sha256'][:12]}")
    return notes


# ---------- execution ----------

def execute(workdir: Path, script: str, run_dir: Path) -> tuple[int | None, bool, str, str, str | None, list[str]]:
    cfg = _cfg()
    out = run_dir / "out"
    out.mkdir(parents=True, exist_ok=True)
    (run_dir / "analysis.py").write_text(script, encoding="utf-8")
    (workdir / "inputs").mkdir(parents=True, exist_ok=True)
    spec = docker.ContainerSpec(
        image=cfg["image"], command=["python", "analysis.py"],
        mounts=[docker.Mount(workdir / "inputs", "/work/inputs", read_only=True),
                docker.Mount(run_dir / "analysis.py", "/work/analysis.py", read_only=True),
                docker.Mount(out, "/work/out")],
        network="none", memory=cfg["memory"], cpus=str(cfg["cpus"]), pids=256, read_only_root=True,
        env={"MPLCONFIGDIR": "/tmp", "HOME": "/tmp"}, name_prefix="ship-analysis",
    )
    res = docker.run(spec, timeout_s=int(cfg["run_timeout_s"]))
    # out/ is the script's to write, so nothing in it is followed: a link there could point at any host file.
    results = None
    if (raw := plainfile.read_plain(run_dir, "out/results.json", _RESULTS_BYTES, whole=True)) is not None:
        try:
            results = json.dumps(json.loads(raw), ensure_ascii=False, indent=1)
        except json.JSONDecodeError:
            results = None
    files = sorted(Path(top, name).relative_to(out).as_posix()
                   for top, _, names in os.walk(out) for name in names
                   if stat.S_ISREG(os.lstat(Path(top, name)).st_mode))
    return (res.exit_code, res.timed_out, res.stdout.decode("utf-8", "replace"),
            res.stderr.decode("utf-8", "replace"), results, files)


def _step_prompt(base: str, rec: AnalysisRecord, history: list[tuple[AnalysisStepOut, Observation]],
                 local_files: list[str], steps_left: int, ledger: list[str] = ()) -> str:
    parts = [base, "## How the data analysis sandbox works",
             "You don't use the internet or a shell yourself. Each step, you give `inputs` (URLs of files to fetch) "
             "and `script` (the whole analysis.py). The server fetches the files into `inputs/`, runs the script in a "
             "container with no network (python 3.13, pandas, numpy, openpyxl, xlrd, pyarrow, scipy, statsmodels, "
             "matplotlib, a Korean font) and returns the result.",
             "- A URL must return its content on a single GET (no page that needs a login, JavaScript or a POST). "
             "Find the download link of the source data with web search first; when there is none, fetch an HTML "
             "page that holds the table as an input too and read it with `pandas.read_html`.\n"
             "- **Don't type numbers into the script.** Calculate only from values read from the files in inputs/. "
             "A result calculated from numbers copied by hand doesn't count as backed by source data.\n"
             "- First print the files' structure (sheets, header rows, column names, the first few rows), then "
             "calculate.\n"
             "- The script always writes the key values to `out/results.json` (keys that say what they mean, values "
             "that are numbers or short strings). Tables can go in `out/*.csv` and charts in `out/*.png`.\n"
             "- When the result is enough, action=finish. The last successful run is the final result, and the server "
             "runs the same script again in a fresh container to check it reproduces.",
             f"Steps left: {steps_left}"]
    if local_files:
        parts.append("Files the owner uploaded (use as `local:<filename>`): " + ", ".join(local_files))
    if ledger:
        parts.append("This project's own records (use as `ledger:<filename>`; don't ask the owner for them): "
                     "`ledger:evidence.json` is the whole evidence list (EV-id, claim, quote, source, verification "
                     "status), and `ledger:T-<number>.md` is a finished task's result document. Read earlier tasks' "
                     "assumptions and numbers here. Available: " + ", ".join(ledger[:60]))
    if rec.manifest:
        parts.append("Files already fetched:\n" + "\n".join(
            f"- inputs/{m['filename']} ← {m['url']} ({m['bytes']:,} bytes)" for m in rec.manifest))
    for i, (step, obs) in enumerate(history):
        recent = i >= len(history) - 2
        parts.append(f"### What step {obs.step} meant to do\n{clip(step.reasoning, 600)}\n\n"
                     f"```python\n{step.script if recent else clip(step.script, 1200)}\n```")
        parts.append(obs.render(full=recent))
    return "\n\n".join(parts)


def run_task(owner: str, base_prompt: str, *, project_id: int, task_id: int, project_slug: str,
             attempt: int, lang: Lang) -> tuple[AgentResultOut, AnalysisRecord]:
    """The analysis loop, then the write-up. `lang` is the language the owner reads the result in; the prompts are
    English and the output-language rule picks the language of the write-up."""
    cfg = _cfg()
    budget.gate_step("analysis", int(cfg["max_steps"]))  # a wait mid-way restarts at step 1
    if not docker.docker_ok():
        raise AgentFailed("analysis sandbox unavailable: Docker is not running")
    if not docker.image_present(cfg["image"]):
        raise AgentFailed(f"analysis sandbox image {cfg['image']} missing — run `ship sandbox build`")
    workdir = settings().var_dir / "analysis" / f"P{project_id}-T{task_id}-i{attempt}"
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    rec = AnalysisRecord(workdir=workdir)
    local = owner_data_dir(project_slug)
    local_files = sorted(p.name for p in local.iterdir() if p.is_file()) if local.is_dir() else []
    ledger = ledger_files(project_id)
    history: list[tuple[AnalysisStepOut, Observation]] = []
    last_ok: tuple[AnalysisStepOut, Observation] | None = None
    max_steps = int(cfg["max_steps"])
    for n in range(1, max_steps + 1):
        step = run_agent(owner, "analysis", _step_prompt(base_prompt, rec, history, local_files, max_steps - n + 1, ledger),
                         AnalysisStepOut, project_id=project_id, task_id=task_id, attempt=attempt)
        if step.action == AnalysisAction.FINISH and last_ok:
            break
        if not step.script.strip():
            obs = Observation(n, None, False, "", "script is empty. Give the analysis.py to run.", None, [])
            history.append((step, obs))
            continue
        queue.check_lease()  # downloads and a container run are stretches of their own, each checked
        notes = _get_inputs(rec, step, project_slug, project_id)
        queue.check_lease()
        code, timed_out, out, err, results, files = execute(workdir, step.script, workdir / f"step{n:02d}")
        obs = Observation(n, code, timed_out, out, err, results, files, notes)
        history.append((step, obs))
        rec.steps = n
        if obs.ok:
            last_ok = (step, obs)
    if last_ok:
        rec.final_script, rec.final_results = last_ok[0].script, last_ok[1].results
        queue.check_lease()
        code, _, _, _, results, _ = execute(workdir, rec.final_script, workdir / "repro")
        rec.reproduced = code == 0 and results is not None and json.loads(results) == json.loads(rec.final_results)
    write_up = "\n\n".join([
        base_prompt,
        "## Data analysis results (run by the server)",
        "Input files:\n" + ("\n".join(f"- {m['filename']} ← {m['url']} (sha256 {m['sha256'][:12]})" for m in rec.manifest)
                          or "(none)"),
        (f"Final script:\n```python\n{rec.final_script}\n```\n\nout/results.json:\n```json\n{rec.final_results}\n```\n"
         f"Rerun: {'matched' if rec.reproduced else 'did not match, or failed'}")
        if rec.final_script else "No run succeeded. Report plainly what blocked it (file links, formats and so on).\n\n"
        + (history[-1][1].render(full=True) if history else ""),
        "Write the task result from the results above. For a value calculated from data given as evidence, write "
        "kind=sourced_claim, source_url=the URL of the input file it was calculated from (exactly as listed above, "
        "local: and ledger: included), quote=the value exactly as it is in results.json (for example \"12345\" or "
        "\"0.183\"), and source_title=the dataset's name. In the text, explain the method (which sheet, columns, "
        "filters and aggregation).",
    ])
    out = run_agent(owner, "work", write_up, AgentResultOut, project_id=project_id, task_id=task_id, attempt=attempt)
    return out, rec


def _value_in(quote: str, results: str) -> bool:
    q = quote.strip().strip('"')
    if not q:
        return False
    values: list[str] = []

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        else:
            values.append(str(v))

    walk(json.loads(results))
    try:
        num = float(q.replace(",", ""))
        return any(_close(num, x) for x in values)
    except ValueError:
        return any(q == x.strip() for x in values)


def _close(num: float, text: str) -> bool:
    try:
        x = float(text)
    except ValueError:
        return False
    return abs(x - num) <= max(1e-9, abs(x) * 1e-3)


def classify_evidence(rec: AnalysisRecord, source_url: str | None, quote: str | None, *,
                      lang: Lang) -> tuple[str, str] | None:
    """Server-side status for one evidence item produced by an analysis task, with the note the owner reads in
    `lang`, or None to fall back to ordinary URL checking."""
    if not source_url or not any(m["url"] == source_url for m in rec.manifest):
        return None
    m = next(m for m in rec.manifest if m["url"] == source_url)
    if source_url.startswith("ledger:"):
        # Inputs are this project's own claims and assumptions: arithmetic on them is not primary evidence.
        rep = msg("work.analysis.reproduced" if rec.reproduced else "work.analysis.not_reproduced", lang)
        return "derived", msg("work.analysis.from_ledger", lang, file=m["filename"], rep=rep)
    if rec.final_results and quote and _value_in(quote, rec.final_results):
        if rec.reproduced:
            return "reproduced", msg("work.analysis.computed_reproduced", lang, file=m["filename"],
                                     sha=m["sha256"][:12])
        return "computed", msg("work.analysis.computed", lang, file=m["filename"])
    if source_url.startswith("local:"):
        return "unverifiable", msg("work.analysis.owner_file", lang)
    return None


def classify(rec: AnalysisRecord, source_url: str | None, quote: str | None, *,
             lang: Lang) -> tuple[str, str] | None:
    """Like classify_evidence, but also catches values computed from numbers the model typed into the
    script: those were never fetched by the server, so they are labelled instead of looking reproduced."""
    if (status := classify_evidence(rec, source_url, quote, lang=lang)) is not None:
        return status
    if rec.final_results and quote and _value_in(quote, rec.final_results):
        return "transcribed", msg("work.analysis.transcribed", lang)
    return None


def artifact_files(rec: AnalysisRecord, base_rel: str) -> dict[str, str]:
    files = {f"{base_rel}/manifest.json": json.dumps(
        {"reproduced": rec.reproduced, "steps": rec.steps, "inputs": rec.manifest,
         "inputs_source": "server_fetched" if rec.manifest else "none (values typed into the script)"},
        ensure_ascii=False, indent=2)}
    if rec.final_script:
        files[f"{base_rel}/analysis.py"] = rec.final_script
        files[f"{base_rel}/results.json"] = rec.final_results or ""
        final_out = None
        for d in sorted(rec.workdir.glob("step*"), reverse=True):
            if (d / "analysis.py").read_text(encoding="utf-8") == rec.final_script:
                final_out = f"{d.name}/out"
                break
        for table in plainfile.plain_files(rec.workdir, final_out) if final_out else []:
            if not table.endswith(".csv"):
                continue
            # Read as a plain file only: what the script linked in out/ is never committed to the registry.
            text = plainfile.read_plain(rec.workdir, f"{final_out}/{table}", _TABLE_BYTES, whole=True)
            if text is not None:
                # The registry takes ASCII names only, and a script may name a table in Korean.
                name = table if re.fullmatch(r"[A-Za-z0-9._-]+", table) else \
                    f"table-{hashlib.sha256(table.encode()).hexdigest()[:12]}.csv"
                files[f"{base_rel}/{name}"] = text.replace("\r\n", "\n").replace("\r", "\n")  # as text mode reads it
    return files


def appendix(rec: AnalysisRecord, base_rel: str, *, lang: Lang) -> str:
    """The record of the analysis under the task's findings, which the owner reads with the result."""
    repro = msg("work.analysis.repro_ok" if rec.reproduced else "work.analysis.repro_no", lang)
    lines = [f"\n\n## {msg('work.analysis.appendix', lang)}",
             "- " + msg("work.analysis.steps", lang, n=rec.steps, repro=repro, where=f"`registry/{base_rel}/`")]
    lines += [f"- {msg('work.analysis.input', lang)} `{m['filename']}` ← {m['url']} · {m['bytes']:,} bytes · "
              f"sha256 `{m['sha256'][:16]}`" for m in rec.manifest]
    if rec.final_script and not rec.manifest:
        lines.append("- " + msg("work.analysis.no_inputs", lang))
    if rec.final_script:
        lines.append(f"\n{msg('work.analysis.final_script', lang)}:\n```python\n{clip(rec.final_script, 6000)}\n```")
    if rec.final_results:
        lines.append(f"\nresults.json:\n```json\n{clip(rec.final_results, 3000)}\n```")
    return "\n".join(lines)
