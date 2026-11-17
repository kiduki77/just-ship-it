import { FormEvent, useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { post, Row, useLive } from "../api";
import { useBusinessLive, useBusinessSelection, useCompanyLive, useNoBusiness } from "../business";
import { cleanName } from "../business/types";
import { Fix } from "../components/fix";
import { useWorker, WORKER_FIX } from "../components/frame";
import { Box, Chip, EmptyState, PageHead, Section, Status, Tone } from "../components/kit";
import { CopyButton } from "../components/panel";
import { ActionButton, ConfirmBox, Loading, RefLink, refHref, Skeleton, Time, useShowTest } from "../components/ui";
import { lookup, PlainKey, t, tx } from "../i18n";
import { routineLabel } from "../labels";
import { refTitle, useRefs } from "../refs";
import "./misc.css";

/** What `POST /schedules/:id/run-now` answers: the job this click made or the one already in flight, and whether the
 * running stage would not run this kind itself (the owner's click runs it anyway). */
type RunNowAnswer = { ok: true; job_id: number | null; existing?: boolean; outside_stage?: boolean };

type Businesses = { id: number; name: string }[] | null;

/** An experiment's routine is named by what it does (the first sentence the experiment gave it), not its stored key. */
const firstSentence = (text: string) => (text.split(/(?<=[.!?。])\s+|\n/)[0] ?? text).trim();

/** A routine's name in the owner's words, from its kind and what it is for (the business or project by its name);
 * the stored name is an internal key. An experiment's routine says what it does. A kind the catalog does not name
 * keeps the name it was given. */
function routineName(s: Row, businesses: Businesses): string {
  if (s.kind === "routine_run" && s.payload?.what) return firstSentence(String(s.payload.what));
  const name = lookup(`company.routine.${s.kind}`);
  if (!name) return String(s.name ?? s.kind);
  const business = s.business_id ? businesses?.find((b) => b.id === s.business_id)?.name : null;
  // A test business is left out of the switcher's list unless test data shows; its project still names it.
  const subject = business ? cleanName(business) : s.project_id ? refTitle(`P-${s.project_id}`) : null;
  return subject ? t("company.routine.of", { subject, name }) : name;
}

/** Who a routine works for: its project, its business, or every business. */
function routineScope(s: Row, businesses: Businesses) {
  if (s.project_id) return <RefLink code={`P-${s.project_id}`} />;
  const business = s.business_id ? businesses?.find((b) => b.id === s.business_id) : null;
  if (business && s.kind === "routine_run") return <Link to={`/business?b=${business.id}&tab=bets`}>{t("work.routines.of_bets", { name: cleanName(business.name) })}</Link>;
  if (business) return cleanName(business.name);
  return t("common.whole_company");
}

/** Routines, the KPIs they move, and what the company actually sent out. */
export default function Operations() {
  // with no business yet the company's own routines (backups, the weekly review) still show, read company-wide
  const noBusiness = useNoBusiness();
  const scoped = useBusinessLive<Row[]>(noBusiness ? null : "/schedules", []);
  const all = useCompanyLive<Row[]>(noBusiness ? "/schedules" : null, []);
  const schedules = noBusiness ? all : scoped;
  const { businesses, current } = useBusinessSelection();
  const worker = useWorker();
  const [showTest] = useShowTest();
  const { data: projects } = useCompanyLive<Row[]>("/projects", ["projects"]);
  const testProjects = new Set((projects ?? []).filter((p) => p.is_test).map((p) => p.id));
  // A routine is named by its project's name: asked here so the heading's next routine and the list both get it.
  useRefs((schedules.data ?? []).filter((x) => x.project_id).map((x) => `P-${x.project_id}`));
  const kpis = useLive<Row[]>("/kpis", ["kpi_values"]);
  const actions = useLive<Row[]>("/actions", ["action_executions"]);
  const s = (schedules.data ?? []).filter((x) => showTest || !testProjects.has(x.project_id));
  const on = s.filter((x) => x.enabled);
  const now = Date.now();
  // the heading names the next run still ahead; a time already past is late, and the rows say so
  const next = [...on].filter((x) => x.next_run_at && new Date(x.next_run_at).getTime() > now)
    .sort((a, b) => String(a.next_run_at).localeCompare(String(b.next_run_at)))[0];
  const late = on.filter((x) => x.next_run_at && new Date(x.next_run_at).getTime() <= now).length;
  const k = (kpis.data ?? []).map(kpiState);
  const met = k.filter((x) => x.ok).length;
  const measured = k.filter((x) => x.ok !== null).length;
  const a = actions.data ?? [];
  const went = countDeliveries(a);
  const [adding, setAdding] = useState(false);
  const nothingYet = !!schedules.data && !!kpis.data && !!actions.data && s.length === 0 && kpis.data.length === 0 && a.length === 0;
  const status: Status = schedules.error && !schedules.data ? { tone: "bad", text: t("work.routines.load_failed") }
    : !schedules.data ? { text: t("company.routines.loading") } : {
      tone: late && worker.down ? "warn" : measured - met ? "warn" : on.length ? "ok" : "muted",
      text: <>
        {s.length ? t("company.routines.running", { n: s.length, on: on.length }) : t("company.routines.none_yet")}
        {next ? tx("company.routines.next", { name: routineName(next, businesses), when: <Time v={next.next_run_at} /> }) : null}
        {late ? <> {worker.down ? t("work.routines.late_worker", { n: late }) : t("work.routines.late", { n: late })}</> : null}
        {measured - met ? t("company.routines.kpis_short", { n: measured - met }) : null}
      </>,
    };
  return (
    <div className="owner-page kui mx">
      <PageHead title={t("company.routines.title")} status={status}
        sub={<>
          {t("work.routines.rule")}
          {a.length ? t("company.routines.sub_counts", { n: a.length, sent: went.sent }) : ""}
          {went.failed ? t("company.routines.sub_failed", { n: went.failed }) : ""}
          {went.waiting ? t("company.routines.sub_waiting", { n: went.waiting }) : ""}
        </>} />
      {nothingYet ? (
        // one empty page, not three empty boxes: what will show here, and the one thing the owner can add now
        <Box>
          <EmptyState title={t("work.routines.all_empty")}
            action={adding ? null : <button type="button" className="btn-ghost" onClick={() => setAdding(true)}>{t("company.kpi.add")}</button>}>
            {current?.brief_version != null ? t("work.routines.empty_planned") : t("work.routines.empty_unplanned")}{" "}
            {t("work.routines.all_empty_more")}
          </EmptyState>
        </Box>
      ) : <Schedules live={{ ...schedules, data: schedules.data ? s : null }} planned={current?.brief_version != null} businesses={businesses} />}
      {(!nothingYet || adding) && <Kpis live={kpis} projects={projects} open={adding} />}
      {!nothingYet && <Actions live={actions} />}
    </div>
  );
}

type Live = { data: Row[] | null; error: string | null; reload: () => void; failure?: any };

/**
 * Where an outside action got to, from its mode and status (`ActionExecution`: mode outbox | live, status started |
 * done | failed | not_sent). Only a live action that finished went out; a finished outbox action is a draft in the
 * outbox and never reads as done, unless the owner says they sent it by hand (`sent_by_owner`).
 */
type Delivery = "sent" | "by_owner" | "outbox" | "failed" | "not_sent" | "waiting";
const DELIVERY: Record<Delivery, { tone: Tone; key: PlainKey }> = {
  sent: { tone: "ok", key: "company.actions.state.sent" },
  by_owner: { tone: "ok", key: "company.actions.state.by_owner" },
  outbox: { tone: "muted", key: "company.actions.state.outbox" },
  failed: { tone: "bad", key: "company.actions.state.failed" },
  not_sent: { tone: "muted", key: "company.actions.state.not_sent" },
  waiting: { tone: "run", key: "company.actions.state.waiting" },
};
function delivery(a: Row): Delivery {
  if (a.status === "failed") return "failed";
  if (a.status === "not_sent") return "not_sent";
  if (a.status === "done") return a.mode === "live" ? "sent" : a.sent_by_owner ? "by_owner" : "outbox";
  return "waiting";
}
function countDeliveries(rows: Row[]): Record<Delivery, number> {
  const n: Record<Delivery, number> = { sent: 0, by_owner: 0, outbox: 0, failed: 0, not_sent: 0, waiting: 0 };
  for (const r of rows) n[delivery(r)] += 1;
  return n;
}
function DeliveryChip({ a }: { a: Row }) {
  const d = DELIVERY[delivery(a)];
  return <Chip tone={d.tone}>{t(d.key)}</Chip>;
}


const every = (hours: number) =>
  hours < 1 ? t("company.routines.every_min", { n: Math.round(hours * 60) })
    : hours % 24 === 0 ? t("company.routines.every_day", { n: hours / 24 }) : t("company.routines.every_hour", { n: hours });

/** Company-wide cleanup routines, and what stops when the owner switches one off: asked first, since nothing else
 * would tell the owner the backups or the clean-up stopped. */
const CLEANUP: Record<string, PlainKey> = { backup: "work.routines.off_backup", retention: "work.routines.off_retention" };

/** The on/off switch keeps its focus while it saves (busy, not disabled), and says a failed save in the row. */
function Toggle({ s, name, flip, busy, error }: { s: Row; name: string; flip: () => void; busy: boolean; error: string | null }) {
  return (
    <span className="mx-switch-w">
      <label className="mx-switch" title={t(s.enabled ? "company.routines.on" : "company.routines.off")}>
        <input type="checkbox" role="switch" checked={!!s.enabled} aria-busy={busy} onChange={flip} aria-label={name} />
        <span aria-hidden="true" />
      </label>
      {error && <span className="act-msg bad" role="alert">{error}</span>}
    </span>
  );
}

/** Switching a cleanup routine off asks first (`asking`, drawn by the row under the switch); any other flip saves at once. */
function useToggle(s: Row, reload: () => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  async function save() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await post(`/schedules/${s.id}/toggle`, {});
      setAsking(false);
      reload();
    } catch (e: any) {
      setError(t("work.routines.toggle_failed", { error: e.message ?? String(e) }));
    } finally {
      setBusy(false);
    }
  }
  const flip = () => (s.enabled && CLEANUP[s.kind] ? setAsking(true) : void save());
  return { busy, error, asking, flip, save, cancel: () => setAsking(false) };
}

/**
 * Run a routine once, now. Said before it happens: the owner's click runs it outside the current stage, uses the
 * subscription, and can leave a to-do; a routine switched off runs this once too. The answer says whether this click
 * queued it or found it already queued or running, so a second click never reads as a second run.
 */
function RunNow({ s, reload }: { s: Row; reload: () => void }) {
  const worker = useWorker();
  const [answer, setAnswer] = useState<RunNowAnswer | null>(null);
  async function run() {
    setAnswer(null);
    const r = await post<RunNowAnswer>(`/schedules/${s.id}/run-now`, {});
    setAnswer(r);
    reload();
  }
  return (
    <span className="mx-run">
      <ActionButton className="btn-ghost small" run={run} confirm={t("work.routines.run_confirm")} keep={t("work.routines.run_keep")}
        what={<>{t(s.admit === "run" ? "work.routines.run_what_stage" : "work.routines.run_what")}{!s.enabled ? <> {t("work.routines.run_off")}</> : null}</>}>
        {t("company.routines.run_now")}
      </ActionButton>
      {answer && (
        <span className="act-msg ok mx-run-msg" role="status">
          {answer.existing ? t("work.routines.run_existing", { id: answer.job_id ?? "?" })
            : answer.outside_stage === false ? t("work.routines.run_queued_stage", { id: answer.job_id ?? "?" })
              : t("work.routines.run_queued", { id: answer.job_id ?? "?" })}
          {worker.down ? <> {t("work.routines.run_waits")} <Fix text={`\`${WORKER_FIX}\``} /></> : null}
        </span>
      )}
    </span>
  );
}

function Schedules({ live, planned, businesses }: { live: Live; planned: boolean; businesses: Businesses }) {
  const { data, error, reload, failure } = live;
  const now = Date.now();
  return (
    <Section title={t("company.routines.title")} count={data?.length}>
      {!data ? (error ? <Loading error={error} failure={failure} retry={reload} /> : <Skeleton rows={4} />) : data.length === 0 ? (
        <Box>
          <EmptyState title={t("company.routines.empty")}>
            {planned ? t("work.routines.empty_planned") : t("work.routines.empty_unplanned")}
          </EmptyState>
        </Box>
      ) : (
        <Box pad={false}>
          <ul className="ilist mx-rows mx-routines">
            <li className="mx-head" aria-hidden="true">
              <span className="mx-switch-w" />
              <div className="grow" />
              <dl className="mx-cells mx-cells-3">
                <div><dt>{t("company.routines.every")}</dt></div>
                <div><dt>{t("company.routines.last")}</dt></div>
                <div><dt>{t("company.routines.next_run")}</dt></div>
              </dl>
              <span className="mx-run" />
            </li>
            {data.map((s) => <RoutineRow key={s.id} s={s} name={routineName(s, businesses)} businesses={businesses} reload={reload} now={now} />)}
          </ul>
        </Box>
      )}
    </Section>
  );
}

function RoutineRow({ s, name, businesses, reload, now }: { s: Row; name: string; businesses: Businesses; reload: () => void; now: number }) {
  const worker = useWorker();
  const toggle = useToggle(s, reload);
  const due = s.enabled && s.next_run_at && new Date(s.next_run_at).getTime() <= now;
  return (
    <li className={s.enabled ? "" : "mx-off"}>
      <Toggle s={s} name={name} flip={toggle.flip} busy={toggle.busy} error={toggle.asking ? null : toggle.error} />
      <div className="grow">
        <p className="t1" title={name}>{name}</p>
        <p className="t2 mx-wrap2" title={routineLabel(s.kind)}>
          {routineScope(s, businesses)}{" · "}<span>{routineLabel(s.kind)}</span>
        </p>
      </div>
      <dl className="mx-cells mx-cells-3">
        <div><dt className="sr-only">{t("company.routines.every")}</dt><dd>{every(s.interval_hours)}</dd></div>
        <div><dt className="sr-only">{t("company.routines.last")}</dt><dd>{s.last_run_at ? <Time v={s.last_run_at} /> : t("company.routines.not_yet")}</dd></div>
        <div>
          <dt className="sr-only">{t("company.routines.next_run")}</dt>
          <dd title={s.enabled && (s.admit === "skip" || s.admit === "park") ? t(s.admit === "skip" ? "work.routines.admit_skip" : "work.routines.admit_park") : undefined}>
            {!s.enabled ? <Chip>{t("company.routines.stopped")}</Chip>
              : due ? <Chip tone="warn">{worker.down ? t("work.routines.due_worker") : t("work.routines.due")}</Chip>
                : s.next_run_at ? <Time v={s.next_run_at} /> : "-"}
          </dd>
        </div>
      </dl>
      <RunNow s={s} reload={reload} />
      {toggle.asking && (
        <div className="mx-confirm-row">
          <ConfirmBox tone="danger" confirmLabel={t("work.routines.off_confirm", { name })} cancelLabel={t("work.routines.off_keep")}
            busy={toggle.busy} error={toggle.error} onConfirm={() => void toggle.save()} onCancel={toggle.cancel}>
            {t(CLEANUP[s.kind])}
          </ConfirmBox>
        </div>
      )}
    </li>
  );
}

function kpiState(k: Row) {
  const vals: number[] = k.values.map((v: Row) => v.value);
  const last = vals.length ? vals[vals.length - 1] : null;
  const ok = last == null ? null : k.direction === "up" ? last >= k.target : last <= k.target;
  return { vals, last, ok };
}

function Spark({ values, name }: { values: number[]; name: string }) {
  if (values.length < 2) return <span className="muted small mx-spark">{t("company.kpi.spark_few", { n: values.length })}</span>;
  const min = Math.min(...values), max = Math.max(...values), w = 120, h = 28;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * w},${h - ((v - min) / (max - min || 1)) * h}`).join(" ");
  return (
    <svg width={w} height={h} className="spark mx-spark" role="img" aria-label={t("company.kpi.trend", { name, values: values.join(", ") })}>
      <polyline points={pts} fill="none" strokeWidth="2" />
    </svg>
  );
}

const EMPTY_KPI = { project_id: "", name: "", unit: "", target: "", direction: "up" };
const isNumber = (v: string) => v.trim() !== "" && Number.isFinite(Number(v));

/** One KPI's value box: only a number goes in, and a failed save says so in place. */
function RecordValue({ k, onSaved }: { k: Row; onSaved: () => void }) {
  const [val, setVal] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const bad = val.trim() !== "" && !isNumber(val);
  async function record(e: FormEvent) {
    e.preventDefault();
    if (!isNumber(val) || busy) return;
    setBusy(true);
    setMsg(null);
    try {
      await post(`/kpis/${k.id}/values`, { value: Number(val), source: "manual" });
      setVal("");
      onSaved();
    } catch (x: any) {
      setMsg(t("work.routines.kpi_save_failed", { error: x.message ?? String(x) }));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="mx-record" onSubmit={record} noValidate>
      <input inputMode="decimal" aria-label={t("company.kpi.new_value_label", { name: k.name })} placeholder={t("company.kpi.new_value")} value={val}
        aria-invalid={bad} onChange={(e) => setVal(e.target.value)} />
      <button type="submit" className="btn-ghost" disabled={!isNumber(val) || busy}>{t("company.kpi.record")}</button>
      {bad && <span className="act-msg bad">{t("work.routines.kpi_number")}</span>}
      {msg && <span className="act-msg bad" role="alert">{msg}</span>}
    </form>
  );
}

/** The KPIs and, on demand, the form that adds one (`open` starts it open). */
function Kpis({ live, projects, open = false }: { live: Live; projects: Row[] | null; open?: boolean }) {
  const { data, error, reload } = live;
  const [showTest] = useShowTest();
  const [adding, setAdding] = useState(open);
  const [form, setForm] = useState(EMPTY_KPI);
  const [msg, setMsg] = useState<string | null>(null);
  async function create(e: FormEvent) {
    e.preventDefault();
    setMsg(null);
    try {
      await post("/kpis", { ...form, project_id: Number(form.project_id), target: Number(form.target) });
      setForm(EMPTY_KPI);
      reload();
    } catch (x: any) {
      setMsg(x.message ?? String(x));
    }
  }
  const choices = (projects ?? []).filter((p) => p.status !== "ARCHIVED" && (showTest || !p.is_test || String(p.id) === form.project_id));
  const targetBad = form.target.trim() !== "" && !isNumber(form.target);
  const ready = form.project_id && form.name.trim() && isNumber(form.target);
  return (
    <Section title={t("work.routines.kpi_title")} count={data?.length}>
      {!data ? <Loading error={error} retry={reload} /> : (
        <Box pad={false}>
          {data.length === 0 ? (
            <div className="mx-pad"><EmptyState title={t("company.kpi.empty")}>{t("company.kpi.empty_hint")}</EmptyState></div>
          ) : (
            <ul className="ilist mx-rows">
              {data.map((k) => {
                const { vals, last, ok } = kpiState(k);
                return (
                  <li key={k.id}>
                    <div className="grow">
                      <p className="t1" title={k.name}>{k.name}</p>
                      <p className="t2">
                        <RefLink code={`P-${k.project_id}`} />
                        {t("company.kpi.target", { target: `${k.target}${k.unit ? ` ${k.unit}` : ""}` })} · {t(k.direction === "up" ? "company.kpi.up" : "company.kpi.down")}
                      </p>
                    </div>
                    <dl className="mx-cells">
                      <div><dt>{t("company.kpi.latest")}</dt><dd className="num">{last ?? "-"}{last != null && k.unit ? ` ${k.unit}` : ""}</dd></div>
                    </dl>
                    <Spark values={vals} name={k.name} />
                    {ok == null ? <Chip>{t("company.kpi.no_values")}</Chip> : <Chip tone={ok ? "ok" : "warn"}>{t(ok ? "company.kpi.met" : "company.kpi.short")}</Chip>}
                    <RecordValue k={k} onSaved={reload} />
                  </li>
                );
              })}
            </ul>
          )}
          {choices.length === 0 ? (
            <p className="box-foot small muted">{t("work.routines.kpi_no_projects")}</p>
          ) : !adding ? (
            <p className="box-foot"><button type="button" className="btn-ghost small" onClick={() => setAdding(true)}>{t("company.kpi.add")}</button></p>
          ) : (
            <form className="box-foot mx-kpi-new" onSubmit={create} noValidate>
              <span className="strong">{t("company.kpi.add")}</span>
              <select aria-label={t("company.kpi.project")} value={form.project_id} onChange={(e) => setForm({ ...form, project_id: e.target.value })}>
                <option value="">{t("company.kpi.pick_project")}</option>
                {choices.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
              <input className="mx-grow" aria-label={t("company.kpi.name")} placeholder={t("company.kpi.name_hint")} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
              <input className="mx-w-s" aria-label={t("company.kpi.unit")} placeholder={t("company.kpi.unit")} value={form.unit} onChange={(e) => setForm({ ...form, unit: e.target.value })} />
              <input className="mx-w-s" aria-label={t("company.kpi.target_value")} inputMode="decimal" placeholder={t("company.kpi.target_value")} value={form.target}
                aria-invalid={targetBad} onChange={(e) => setForm({ ...form, target: e.target.value })} />
              <select aria-label={t("company.kpi.direction")} value={form.direction} onChange={(e) => setForm({ ...form, direction: e.target.value })}>
                <option value="up">{t("company.kpi.up")}</option><option value="down">{t("company.kpi.down")}</option>
              </select>
              <button type="submit" className="btn-primary" disabled={!ready}>{t("company.kpi.add")}</button>
              {targetBad && <span className="act-msg bad">{t("work.routines.kpi_number")}</span>}
              {msg && <span className="act-msg bad" role="alert">{msg}</span>}
            </form>
          )}
        </Box>
      )}
    </Section>
  );
}

// a local file an outbox record names: a path with a folder and a file extension
const PATH = /((?:[A-Za-z]:)?[\\/]?(?:[\w.-]+[\\/])+[\w.-]+\.\w{1,6})/;

/** A result summary with the file it names shown as code the owner can copy (an outbox item is opened by hand). */
function Summary({ text }: { text: string }) {
  const m = PATH.exec(text);
  if (!m) return <>{text}</>;
  return <>{text.slice(0, m.index)}<code className="mono">{m[1]}</code>{text.slice(m.index + m[1].length)} <CopyButton text={m[1]} what={t("work.routines.path")} /></>;
}

/** The time now as a `datetime-local` value in the owner's clock: the default and the latest a send can be. */
function localNow(): string {
  const d = new Date();
  d.setSeconds(0, 0);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

/**
 * The owner's word that they sent an outbox item by hand: when, and an optional link and note. It only records; the
 * server sends nothing and closes or makes the item's to-do.
 */
function SentByMe({ a, onSaved }: { a: Row; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ at: "", link: "", note: "" });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const first = useRef<HTMLInputElement>(null);
  const id = `sent-${a.id}`;
  // opening moves focus into the form; Cancel gives it back to the button that opened it
  const wasOpen = useRef(false);
  useEffect(() => {
    if (open) first.current?.focus();
    else if (wasOpen.current) opener.current?.focus();
    wasOpen.current = open;
  }, [open]);
  function start() {
    setForm({ at: localNow(), link: "", note: "" });
    setMsg(null);
    setOpen(true);
  }
  async function save(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setMsg(null);
    try {
      await post(`/actions/${a.id}/sent-by-owner`, {
        at: form.at ? new Date(form.at).toISOString() : null, link: form.link.trim() || null, note: form.note.trim() || null,
      });
      setOpen(false);
      onSaved();
    } catch (x: any) {
      setMsg(t("company.actions.sent_failed", { error: x.message ?? String(x) }));
    } finally {
      setBusy(false);
    }
  }
  if (!open) {
    return <button ref={opener} type="button" className="btn-ghost small" onClick={start}>{t("company.actions.sent_mark")}</button>;
  }
  return (
    <form className="mx-sent" onSubmit={save} aria-label={t("company.actions.sent_form")} aria-describedby={`${id}-says`} noValidate>
      <p id={`${id}-says`} className="small muted mx-sent-says">{t("company.actions.sent_says")}</p>
      <label>{t("company.actions.sent_at")}
        <input ref={first} type="datetime-local" required max={localNow()} value={form.at} onChange={(e) => setForm({ ...form, at: e.target.value })} />
      </label>
      <label className="mx-grow">{t("company.actions.sent_link")}
        <input type="url" inputMode="url" placeholder="https://" value={form.link} onChange={(e) => setForm({ ...form, link: e.target.value })} />
      </label>
      <label className="mx-grow">{t("company.actions.sent_note")}
        <input value={form.note} maxLength={2000} onChange={(e) => setForm({ ...form, note: e.target.value })} />
      </label>
      <span className="mx-sent-foot">
        <button type="submit" className="btn-primary" disabled={busy || !form.at}>{busy ? t("company.actions.sent_saving") : t("company.actions.sent_save")}</button>
        <button type="button" className="btn-ghost" onClick={() => setOpen(false)}>{t("common.cancel")}</button>
      </span>
      {msg && <p className="act-msg bad mx-sent-msg" role="alert">{msg}</p>}
    </form>
  );
}

/** What went out, or into the outbox. `/routines#sent` (Settings > Outgoing links it) lands here once the list loads. */
function Actions({ live }: { live: Live }) {
  const { data, error, reload } = live;
  const { hash } = useLocation();
  useEffect(() => {
    if (hash === "#sent" && data) document.getElementById("sent")?.scrollIntoView({ block: "start" });
  }, [hash, data]);
  return (
    <Section id="sent" title={t("company.actions.title")} count={data?.length}
      actions={<span className="small muted">{t("company.actions.recheck")}</span>}>
      <p className="small muted mx-flat">
        {tx("work.routines.outbox_note", { link: <Link to="/settings#set-outgoing">{t("work.routines.outbox_link")}</Link> })}
      </p>
      {!data ? <Loading error={error} retry={reload} /> : data.length === 0 ? (
        <Box><EmptyState title={t("company.actions.empty")}>{t("company.actions.empty_hint")}</EmptyState></Box>
      ) : (
        <Box pad={false}>
          <ul className="ilist mx-rows">
            {data.map((a) => (
              <li key={a.id}>
                <div className="grow">
                  <p className="t1"><RefLink code={`REQ-${a.request_id}`} /></p>
                  <p className="t2 mx-wrap2" title={a.result_summary ?? ""}>{a.result_summary ? <Summary text={a.result_summary} /> : t("company.actions.no_summary")}</p>
                  {a.sent_by_owner && (
                    <p className="t2">
                      {tx("company.actions.sent_by_you", { when: <Time v={a.sent_by_owner.at} /> })}{" "}
                      <Link to={refHref("OT", a.sent_by_owner.task_id) ?? "/my-tasks"}>{t("company.actions.sent_todo")}</Link>
                    </p>
                  )}
                </div>
                <span className="small nowrap">{a.connector}</span>
                <DeliveryChip a={a} />
                <span className="end"><Time v={a.created_at} /></span>
                {delivery(a) === "outbox" && <SentByMe a={a} onSaved={reload} />}
              </li>
            ))}
          </ul>
        </Box>
      )}
    </Section>
  );
}
