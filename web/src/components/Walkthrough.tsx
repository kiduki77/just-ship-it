import { Fragment, ReactNode, useId, useMemo, useState } from "react";
import type {
  CheckedGroup, CheckedItem, CheckedKind, Row, WalkCapture, WalkFile, WalkJourney, WalkNotRun, WalkStep, Walkthrough as WalkRun,
} from "../api";
import { useAsk } from "../chat/Dock";
import { locale, PlainKey, t } from "../i18n";
import { PlannedVsBuilt } from "../wireflow/PlannedVsBuilt";
import { Icon } from "./icons";
import { Chip, Tone } from "./kit";
import { CopyButton } from "./panel";
import type { Review } from "./reviewModel";
import { TryIt } from "./TryIt";
import { Badge, dur } from "./ui";
import "./decision.css";

/** try_it.OWNER_PREVIEW_ADDRESS: where the product's preview opens on the owner's computer. It is `localhost`, never
 * the console's own host: the browser sends the console's cookie to every port of 127.0.0.1. */
const PREVIEW = "http://localhost:4173";
/** A capture shows this many lines until the owner asks for all of them. */
const LINES_SHOWN = 12;
const CLONE = /^(git clone |cd )/;

type View = "passed" | "differs" | "unjudged" | "not_run";
/** Icon and word, so a state is never a colour alone. Not run is never green. */
const VIEW: Record<View, { tone: Tone; icon: string; word: PlainKey }> = {
  passed: { tone: "ok", icon: "check", word: "decide.wt.state.passed" },
  differs: { tone: "bad", icon: "alert", word: "decide.wt.state.differs" },
  unjudged: { tone: "warn", icon: "flag", word: "decide.wt.state.unjudged" },
  not_run: { tone: "muted", icon: "pause", word: "decide.wt.state.not_run" },
};
const FAULT: Record<"product" | "scenario" | "environment", PlainKey> = {
  product: "decide.wt.fault.product", scenario: "decide.wt.fault.scenario", environment: "decide.wt.fault.environment",
};
const NOT_RUN: Record<Exclude<WalkNotRun, "needs">, PlainKey> = {
  no_docker: "decide.wt.not_run.no_docker", unbound: "decide.wt.not_run.unbound", setup_failed: "decide.wt.not_run.setup_failed",
  earlier_failed: "decide.wt.not_run.earlier_failed", out_of_time: "decide.wt.not_run.out_of_time",
  unreachable: "decide.wt.not_run.unreachable",
};
// The fold's groups the server leaves unnamed: the console words them. A story group brings its own title.
const GROUP: Record<Exclude<CheckedKind, "story">, [PlainKey, PlainKey]> = {
  core_flow: ["decide.wt.group.core_flow", "decide.wt.group.core_flow.purpose"],
  scenarios: ["decide.wt.group.scenarios", "decide.wt.group.scenarios.purpose"],
  rule: ["decide.wt.group.rule", "decide.wt.group.rule.purpose"],
  asked: ["decide.wt.group.asked", "decide.wt.group.asked.purpose"],
  spec: ["decide.wt.group.spec", "decide.wt.group.spec.purpose"],
};

const viewOf = (s: WalkStep): View => (s.state === "not_run" ? "not_run" : !s.judged ? "unjudged" : s.state);

/** Where an `open` step's path opens on the owner's computer, or null for a path that would leave the preview. */
function previewUrl(path: string): string | null {
  try {
    const url = new URL(path.startsWith("/") ? path : `/${path}`, PREVIEW);
    return url.origin === PREVIEW ? url.href : null;
  } catch {
    return null;
  }
}

/** What the owner copies for one step: its words as a comment, then the command or address, pasteable in a terminal. */
const stepText = (n: number, s: WalkStep) => `# ${n}. ${s.do}\n${s.kind === "open" ? previewUrl(s.action) ?? s.action : s.action}`;

const took = (ms: number) => (ms < 1000 ? t("decide.wt.under_s") : dur(ms));

function size(bytes: number): string {
  const [n, unit] = bytes < 1024 ? [bytes, "B"] : bytes < 1024 ** 2 ? [bytes / 1024, "KB"] : [bytes / 1024 ** 2, "MB"];
  return `${new Intl.NumberFormat(locale(), { maximumFractionDigits: 1 }).format(n)} ${unit}`;
}

/** A state as a chip with its icon and its word. */
function StateChip({ tone, icon, children }: { tone: Tone; icon: string; children: ReactNode }) {
  return <Chip tone={tone} dot={false}><Icon name={icon} size={12} />{children}</Chip>;
}

type Part = { text: string; volatile: boolean };

/** The lines of `text`, each as runs that are or are not volatile. Spans count code points, as the server does. */
function linesOf(text: string, spans: [number, number][]): Part[][] {
  const chars = Array.from(text);
  const volatile = new Uint8Array(chars.length);
  for (const [from, to] of spans) volatile.fill(1, Math.max(0, from), Math.min(chars.length, to));
  const lines: Part[][] = [];
  let line: Part[] = [];
  let run = "";
  let runVolatile = false;
  const flush = () => {
    if (run) line.push({ text: run, volatile: runVolatile });
    run = "";
  };
  chars.forEach((ch, i) => {
    if (ch === "\n") {
      flush();
      lines.push(line);
      line = [];
    } else {
      const v = volatile[i] === 1;
      if (run && v !== runVolatile) flush();
      runVolatile = v;
      run += ch;
    }
  });
  flush();
  if (line.length) lines.push(line);
  return lines;
}

/**
 * Terminal text as plain text: no markdown, no links, whatever the product printed. It shows the first lines and the
 * rest one click away, says where the agent's capture was cut, and scrolls sideways inside its own box.
 */
function TextBox({ text, volatile, cutAfter, printed }: { text: string; volatile: [number, number][]; cutAfter: number | null; printed: number }) {
  const [all, setAll] = useState(false);
  const lines = useMemo(() => linesOf(text, volatile), [text, volatile]);
  const shown = all ? lines : lines.slice(0, LINES_SHOWN);
  const cut = cutAfter === null ? 0 : printed - lines.length;
  const marker = cut > 0 && cutAfter !== null && cutAfter <= shown.length ? cutAfter : null;
  const rows: ReactNode[] = [];
  shown.forEach((parts, i) => {
    if (i > 0) rows.push("\n");
    if (marker === i) rows.push(<span key="cut" className="wt-cut">{t("decide.wt.cut", { n: cut })}</span>, "\n");
    rows.push(<Fragment key={i}>{parts.map((p, j) => (p.volatile ? <span key={j} className="wt-vol">{p.text}</span> : p.text))}</Fragment>);
  });
  if (marker !== null && marker === shown.length) rows.push("\n", <span key="cut" className="wt-cut">{t("decide.wt.cut", { n: cut })}</span>);
  return (
    <>
      <pre className="wt-pre" tabIndex={0}>{rows}</pre>
      {lines.length > LINES_SHOWN && (
        <button type="button" className="link small" onClick={() => setAll(!all)}>
          {all ? t("decide.wt.show_less") : t("decide.wt.show_all", { n: lines.length })}
        </button>
      )}
    </>
  );
}

const basename = (path: string) => path.split(/[\\/]/).pop() || path;

function FileHead({ file }: { file: WalkFile }) {
  if (file.binary) return <p className="small wt-file">{t("decide.wt.file_binary", { name: basename(file.path), size: size(file.size) })}</p>;
  return (
    <>
      <p className="muted small mono wt-file">{t("decide.wt.file_head", { path: file.path })}</p>
      <TextBox text={file.text} volatile={file.volatile} cutAfter={null} printed={file.lines} />
    </>
  );
}

/** A thumbnail that opens the screen at full size, like the screens of a planned-versus-built card. */
function Shot({ url, path }: { url: string; path: string }) {
  if (!/^(\/(?!\/)|https?:\/\/)/i.test(url)) return null;
  return (
    <a className="wt-shot" href={url} target="_blank" rel="noreferrer">
      <img src={url} alt={t("decide.wt.shot", { path })} loading="lazy" />
    </a>
  );
}

/** What one step printed: its exit and time, the text, the head of the file it wrote, or the screen it opened. */
function CaptureBox({ s, c }: { s: WalkStep; c: WalkCapture }) {
  const hidden = c.scrubbed.reduce((n, x) => n + x.n, 0);
  const meta = [s.kind === "run" && c.exit !== null ? t("decide.wt.exit", { code: c.exit }) : null, c.ms > 0 ? took(c.ms) : null]
    .filter(Boolean).join(" · ");
  const shaky = c.volatile.length > 0 || (c.file?.volatile.length ?? 0) > 0;
  return (
    <div className="wt-cap">
      {c.shot && <Shot url={c.shot} path={s.action} />}
      {c.text ? <TextBox text={c.text} volatile={c.volatile} cutAfter={c.cut_after} printed={c.lines} />
        : !c.shot && !c.file && <p className="muted small">{t("decide.wt.silent")}</p>}
      {c.file && <FileHead file={c.file} />}
      {shaky && <p className="muted small">{t("decide.wt.volatile")}</p>}
      {hidden > 0 && <p className="muted small">{t("decide.wt.scrubbed", { n: hidden })}</p>}
      {meta && <p className="muted small mono">{meta}</p>}
    </div>
  );
}

function notRunLine(s: WalkStep): string {
  if (s.not_run === "needs") return s.need ? t("decide.wt.not_run.needs", { need: s.need }) : t("decide.wt.not_run.needs_unnamed");
  return t(NOT_RUN[s.not_run ?? "unbound"]);
}

/**
 * What the agent got when it ran a step on this build: the state as a chip, the product agent's reading, the capture,
 * and whose fault a mismatch looks like. A step nobody ran says why and shows no capture box.
 */
function StepResult({ s }: { s: WalkStep }) {
  const view = viewOf(s);
  const v = VIEW[view];
  const fault = view === "differs" && s.fault ? s.fault : null;
  return (
    <div className="wt-res">
      <p className="wt-state">
        <StateChip tone={v.tone} icon={v.icon}>{t(v.word)}</StateChip>
        {view !== "not_run" && s.looks_like && <span className="wt-looks small">{t("decide.wt.looks_like", { text: s.looks_like })}</span>}
      </p>
      {view === "not_run" && <p className="small">{notRunLine(s)}</p>}
      {view === "unjudged" && <p className="small">{t("decide.wt.unjudged")}</p>}
      {view !== "not_run" && s.capture && <CaptureBox s={s} c={s.capture} />}
      {fault && (
        <p className="small wt-fault">
          {t(FAULT[fault])}
          {s.fault_by === "agent" && fault !== "scenario" && <> {t("decide.wt.fault.by_agent")}</>}
        </p>
      )}
      {view !== "not_run" && s.hint && <p className="muted small">{s.hint}</p>}
    </div>
  );
}

/** The command in a mono line with a copy button, or the address an `open` step goes to as a link. */
function Action({ s }: { s: WalkStep }) {
  const url = s.kind === "open" ? previewUrl(s.action) : null;
  return (
    <div className="wt-act">
      {url ? <a className="wt-code" href={url} target="_blank" rel="noopener noreferrer">{url}</a> : <code className="wt-code">{s.action}</code>}
      <CopyButton text={url ?? s.action} what={t("decide.wt.what_cmd")} />
    </div>
  );
}

type Wrong = ((s: WalkStep, n: number) => void) | null;

function Step({ s, n, onWrong }: { s: WalkStep; n: number; onWrong: Wrong }) {
  return (
    <li className="wt-step">
      <div className="wt-head">
        <div className="wt-say">
          <p className="wt-do">{s.do}</p>
          {s.why && <p className="wt-why muted">{s.why}</p>}
        </div>
        <Action s={s} />
      </div>
      {s.kind === "open" && <p className="muted small">{t("decide.wt.open_note")}</p>}
      {s.see && <p className="wt-see"><span className="mr-k">{t("decide.wt.see")}</span> {s.see}</p>}
      <p className="mr-k wt-got">{t("decide.wt.got")}</p>
      <StepResult s={s} />
      <div className="wt-end">
        <CopyButton text={stepText(n, s)} what={t("decide.wt.what_step")} />
        {onWrong && <button type="button" className="link small" onClick={() => onWrong(s, n)}>{t("decide.wt.wrong")}</button>}
      </div>
    </li>
  );
}

function Journey({ j, onWrong }: { j: WalkJourney; onWrong: Wrong }) {
  // each seed file once, at the journey: a step asks for it, the owner copies it from here
  const seeds = [...new Map(j.steps.flatMap((s) => s.seed).map((x) => [x.path, x])).values()];
  return (
    <details className="wt-journey" open={j.priority === "P1"}>
      <summary>
        <span className="wt-title">{j.title}</span>
        <Badge value={j.priority} tone={j.priority === "P1" ? "info" : "muted"} raw />
        <span className="muted small">{t("decide.wt.steps", { n: j.steps.length })}</span>
      </summary>
      <p className="wt-play">{t("decide.wt.play", { persona: j.persona, why: j.why })}</p>
      {j.brings && <p className="muted">{t("decide.wt.brings", { brings: j.brings })}</p>}
      {seeds.map((x) => (
        <details key={x.path} className="wt-seed">
          <summary className="small">{t("decide.wt.seed", { path: x.path })}</summary>
          <div className="dp-draft-h"><span className="mono small">{x.path}</span><CopyButton text={x.text} what={x.path} /></div>
          <pre className="wt-pre" tabIndex={0}>{x.text}</pre>
        </details>
      ))}
      <ol className="wt-steps">
        {j.steps.map((s, i) => <Step key={s.id} s={s} n={i + 1} onWrong={onWrong} />)}
      </ol>
      <p className="wt-success">{t("decide.wt.success", { success: j.success })}</p>
      <CopyButton text={j.steps.map((s, i) => stepText(i + 1, s)).join("\n\n")} what={t("decide.wt.copy_all")} label={t("decide.wt.copy_all")} />
    </details>
  );
}

/** What the owner needs, how long the agent's run took, and the commands that set the branch up, captures folded. */
function BeforeYouTry({ r, run, review, onWrong }: { r: Row; run: WalkRun; review: Review; onWrong: Wrong }) {
  const uid = useId();
  const steps = run.journeys.flatMap((j) => j.steps);
  const needs = (review.use?.needs ?? []).filter((n) => n.required);
  const tryIt: string[] = r.try_it?.steps ?? [];
  const given = review.use?.steps ?? tryIt;
  const firstOwn = given.findIndex((c) => !CLONE.test(c));
  const clone = firstOwn === -1 ? given : given.slice(0, firstOwn);
  const setup = run.setup.map((s) => s.action);
  // a product with screens is opened on its preview, which the owner starts before the first `open` step
  const preview = steps.some((s) => s.kind === "open") ? tryIt.filter((c) => !CLONE.test(c) && !setup.includes(c)) : [];
  const commands: { cmd: string; step: WalkStep | null }[] = [
    ...clone.map((cmd) => ({ cmd, step: null })),
    ...run.setup.map((s) => ({ cmd: s.action, step: s })),
    ...preview.map((cmd) => ({ cmd, step: null })),
  ];
  const measured = run.setup_ms > 0 || run.ms > 0;
  return (
    <section className="dp-sec wt-sec" aria-labelledby={`${uid}-h`}>
      <h3 id={`${uid}-h`}>{t("decide.wt.before")}</h3>
      {run.later && <p className="wt-note">{t("decide.wt.later")}</p>}
      <p className="mr-k">{t("decide.wt.needs")}</p>
      <ul className="dp-list">
        <li>{steps.some((s) => s.kind === "run") ? t("decide.wt.need_terminal") : t("decide.wt.need_browser")}</li>
        {needs.map((n) => (
          <li key={n.name}>
            {t("decide.mr.need_required", { name: n.name, what: n.what })}
            {n.without && <span className="muted"> {t("decide.mr.need_without", { without: n.without })}</span>}
          </li>
        ))}
      </ul>
      {measured && <p className="muted small">{t("decide.wt.time", { setup: took(run.setup_ms), steps: took(run.ms) })}</p>}
      {commands.length > 0 && (
        <>
          <div className="dp-draft-h">
            <p className="dp-sub">{t("decide.wt.setup")}</p>
            <CopyButton text={commands.map((c) => c.cmd).join("\n")} what={t("decide.wt.copy_cmds")} label={t("decide.wt.copy_cmds")} />
          </div>
          <ol className="wt-cmds">
            {commands.map(({ cmd, step }, i) => (
              <li key={`${i}-${cmd}`}>
                <div className="wt-act">
                  <code className="wt-code">{cmd}</code>
                  <CopyButton text={cmd} what={t("decide.wt.what_cmd")} />
                </div>
                {step && (
                  <details className="wt-fold">
                    <summary className="small">
                      {t("decide.wt.got")}{" "}
                      <StateChip tone={VIEW[viewOf(step)].tone} icon={VIEW[viewOf(step)].icon}>{t(VIEW[viewOf(step)].word)}</StateChip>
                    </summary>
                    <StepResult s={step} />
                    {onWrong && <button type="button" className="link small" onClick={() => onWrong(step, run.setup.indexOf(step) + 1)}>
                      {t("decide.wt.wrong")}</button>}
                  </details>
                )}
              </li>
            ))}
          </ol>
          <p className="muted small">{t("decide.try.run_note")}</p>
        </>
      )}
    </section>
  );
}

function CheckedItemRow({ c }: { c: CheckedItem }) {
  const line = c.guards || c.criterion;
  const tone: Tone = c.met ? "ok" : "bad";
  return (
    <li className="wt-item">
      <div className="wt-item-h">
        <StateChip tone={tone} icon={c.met ? "check" : "alert"}>{t(c.met ? "decide.mr.result_met" : "decide.mr.result_unmet")}</StateChip>
        <span className="wt-line">{line}</span>
      </div>
      {(c.scenario || c.evidence || c.guards) && (
        <details className="mr-more">
          <summary className="small">{t("decide.mr.more_text")}</summary>
          {c.scenario ? (
            <dl className="kv mr-gwt">
              <div><dt>{t("decide.spec.given")}</dt><dd>{c.scenario.given}</dd></div>
              <div><dt>{t("decide.spec.when")}</dt><dd>{c.scenario.when}</dd></div>
              <div><dt>{t("decide.spec.then")}</dt><dd>{c.scenario.then}</dd></div>
              {c.evidence && <div><dt>{t("decide.mr.result")}</dt><dd>{c.evidence}</dd></div>}
            </dl>
          ) : (
            <>
              <p className="mr-line">{c.criterion}</p>
              {c.evidence && <p className="muted small"><span className="mr-k">{t("decide.mr.result")}</span> {c.evidence}</p>}
            </>
          )}
        </details>
      )}
    </li>
  );
}

function CheckedGroupView({ g }: { g: CheckedGroup }) {
  const [title, purpose] = g.kind === "story" ? [g.title, g.purpose] : GROUP[g.kind].map((k) => t(k));
  const done = g.met === g.total;
  return (
    <section className="wt-group">
      <div className="mr-h">
        <h4 className="wt-group-t">{title}</h4>
        <StateChip tone={done ? "ok" : "bad"} icon={done ? "check" : "alert"}>{t("decide.mr.met", { met: g.met, n: g.total })}</StateChip>
      </div>
      {purpose && <p className="muted small">{purpose}</p>}
      <ul className="wt-items">{g.items.map((c, i) => <CheckedItemRow key={i} c={c} />)}</ul>
    </section>
  );
}

/** The developer's checks under the walkthrough: closed, grouped by what they protect, each with its purpose line. */
function CheckedFold({ review }: { review: Review }) {
  const checked = review.checked;
  if (!checked?.groups.length) return null;
  return (
    <details className="dp-fold wt-checked">
      <summary>{t("decide.wt.checked", { n: checked.total, m: checked.met })}</summary>
      <p className="muted small">{t("decide.wt.checked_hint")}</p>
      {checked.groups.map((g, i) => <CheckedGroupView key={`${g.kind}-${i}`} g={g} />)}
    </details>
  );
}

function WhatThis({ r, run }: { r: Row; run: WalkRun | null }) {
  const uid = useId();
  const about = run?.about;
  if (run && !about) return null;
  return (
    <section className="dp-sec wt-sec" aria-labelledby={`${uid}-h`}>
      <h3 id={`${uid}-h`}>{t("decide.wt.what")}</h3>
      {about ? (
        <>
          <p className="wt-what">{about.what}</p>
          <p className="muted">{t("decide.wt.for", { who: about.who, problem: about.problem })}</p>
        </>
      ) : (
        <>
          <p className="wt-what">{r.title}</p>
          <p className="muted">{t("decide.wt.legacy.what")}</p>
        </>
      )}
    </section>
  );
}

/**
 * The merge card's way to understand and try a build: what it is and who it is for, what to have ready, each user
 * journey step by step with what the agent really got when it ran this build, and the developer checks folded under
 * "Already checked". A card with no recorded walkthrough (a build accepted before them, or a spec with no journeys)
 * keeps the commands the product's own files give, said to be unrun. `asking` is a card still waiting: only then does
 * a step offer "Something went wrong", which opens team chat on the step.
 */
export function Walkthrough({ r, asking }: { r: Row; asking: boolean }) {
  const ask = useAsk();
  const review: Review | undefined = r.review;
  if (r.action_kind !== "dev.merge") return null;
  if (!review) return <TryIt r={r} />;
  const run = review.walkthrough;
  const onWrong: Wrong = asking && run
    ? (s, n) => ask({ kind: "request", ref: String(r.id) }, { kind: "walkthrough_step", step: s.id, head: run.head }, { n, do: s.do || s.action })
    : null;
  const built = r.screens?.mode === "built" ? r.screens : null;
  return (
    <>
      <WhatThis r={r} run={run} />
      {run && run.journeys.length > 0 ? (
        <>
          <BeforeYouTry r={r} run={run} review={review} onWrong={onWrong} />
          {built && <PlannedVsBuilt built={built} />}
          <TryJourneys run={run} onWrong={onWrong} />
        </>
      ) : <TryIt r={r} note={t("decide.wt.legacy.try")} />}
      <CheckedFold review={review} />
    </>
  );
}

function TryJourneys({ run, onWrong }: { run: WalkRun; onWrong: Wrong }) {
  const uid = useId();
  const journeys = [...run.journeys].sort((a, b) => Number(b.priority === "P1") - Number(a.priority === "P1"));
  return (
    <section className="dp-sec wt-sec" aria-labelledby={`${uid}-h`}>
      <h3 id={`${uid}-h`}>{t("decide.mr.try")}</h3>
      {journeys.map((j) => <Journey key={j.id} j={j} onWrong={onWrong} />)}
    </section>
  );
}
