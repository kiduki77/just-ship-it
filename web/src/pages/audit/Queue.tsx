import { ReactNode, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { post, Row } from "../../api";
import { Business, cleanName, useBusinessSelection, useNoBusiness } from "../../business";
import { Fix } from "../../components/fix";
import { useWorker, WORKER_FIX } from "../../components/frame";
import { Icon } from "../../components/icons";
import { Box, Chip, EmptyState, FirstRun, Rows, Status, Tone } from "../../components/kit";
import { ActionButton, Loading, RefLink, Skeleton, tid, Time, when } from "../../components/ui";
import { lookup, PlainKey, t, tx } from "../../i18n";
import { agentName, jobLabel } from "../../labels";
import { AuditFrame, ScopeSwitch, useAuditScope, useScopeLive, currentParams } from "./frame";

/** A queue job's kind in the owner's words; an unknown kind is shown as its code. */

type JobStatus = "queued" | "gated" | "running" | "failed" | "done" | "cancelled";
type JobTab = "all" | JobStatus;

/** Each status: its name, its tone, and what an empty tab says. Tabs and chips read this one table. */
const STATUS: Record<JobStatus, { label: PlainKey; tone: Tone; empty: PlainKey }> = {
  queued: { label: "company.queue.status.queued", tone: "warn", empty: "audit.queue.empty.queued" },
  gated: { label: "audit.queue.status.gated", tone: "info", empty: "audit.queue.empty.gated" },
  running: { label: "company.queue.status.running", tone: "run", empty: "audit.queue.empty.running" },
  failed: { label: "company.queue.status.failed", tone: "bad", empty: "audit.queue.empty.failed" },
  done: { label: "company.queue.status.done", tone: "ok", empty: "audit.queue.empty.done" },
  cancelled: { label: "audit.queue.status.cancelled", tone: "muted", empty: "audit.queue.empty.cancelled" },
};
const TABS: JobTab[] = ["all", "queued", "gated", "running", "failed", "done", "cancelled"];
const isStatus = (v: string | null): v is JobStatus => !!v && v in STATUS;
const CANCELLABLE = new Set(["queued", "gated"]);
const ALL_LIMIT = 100;
const TAB_LIMIT = 200;

/** What the same work means for the queue: one kind with one payload. A copy that waits is a duplicate. */
const sameWork = (j: Row) => `${j.kind}:${JSON.stringify(j.payload ?? {})}`;

/** The job's work: the task, build or project it is for, else what its payload names; null when it names nothing. */
function targetOf(j: Row, businesses: Business[] | null): ReactNode {
  const p = j.payload ?? {};
  const parts: ReactNode[] = [];
  if (p.task_id) parts.push(<RefLink key="t" code={tid(p.task_id)} projectId={p.project_id} />);
  else if (p.dev_job_id) parts.push(<RefLink key="j" code={`J-${p.dev_job_id}`} projectId={p.project_id} />);
  else if (p.project_id) parts.push(<RefLink key="p" code={`P-${p.project_id}`} />);
  if (p.request_id) parts.push(<RefLink key="r" code={`REQ-${p.request_id}`} />);
  if (p.cycle_id) parts.push(<span key="c">{t("audit.queue.for_cycle", { n: p.cycle_id })}</span>);
  if (p.session_id) parts.push(<span key="s">{t("audit.queue.for_kickoff")}</span>);
  if (p.idea_id) parts.push(<span key="i">{t("audit.queue.for_idea", { n: p.idea_id })}</span>);
  if (p.directive) parts.push(<span key="d">{t("audit.queue.for_directive", { n: p.directive })}</span>);
  // a notice names what it would send: its title, when the payload holds plain text rather than a message key
  if (j.kind === "notify" && typeof p.title === "string" && p.title.trim()) parts.push(<span key="n">{t("audit.queue.for_notice", { title: p.title.trim() })}</span>);
  if (!parts.length && p.business_id) {
    const b = businesses?.find((x) => x.id === p.business_id);
    if (b) parts.push(<span key="b">{cleanName(b.name)}</span>);
  }
  if (!parts.length) return null;
  return <>{parts.map((x, i) => <span key={i}>{i > 0 && " · "}{x}</span>)}</>;
}

/** " · " and the business a job belongs to, or "Whole company" for the company's own work; nothing until the
 * businesses load, so the title never ends in a dangling separator. */
function BusinessName({ id }: { id: number | null }) {
  const { businesses } = useBusinessSelection();
  if (id != null && !businesses) return null;
  const b = id == null ? null : businesses?.find((x) => x.id === id);
  const name = id == null ? t("common.whole_company") : b ? cleanName(b.name) : t("audit.queue.business_n", { n: id });
  return <span className="au-job-for"> · {name}</span>;
}

const future = (v: string | null | undefined) => !!v && new Date(v).getTime() > Date.now();
const firstLine = (s: string | null | undefined) => String(s ?? "").split("\n", 1)[0].trim();

/** The cycle closed this job when the owner stopped it: it failed without ever being tried. */
const closedByCycle = (j: Row) => j.status === "failed" && j.gate === "stage" && Number(j.attempts) === 0;

type Ctx = { workerDown: boolean; stopped: boolean; alert: Row | undefined; retriedAs: number | undefined };

/** The chip and the one-line reason a row reads with, from the job's own fields. */
function jobState(j: Row, c: Ctx): { chip: [string, Tone]; reason: ReactNode; quiet: boolean } {
  const st: JobStatus | null = isStatus(j.status) ? j.status : null;
  const base: [string, Tone] = st ? [t(STATUS[st].label), STATUS[st].tone] : [String(j.status), "muted"];
  if (st === "running" && c.workerDown) return { chip: [t("audit.queue.status.stalled"), "warn"], reason: t("audit.queue.why.stalled"), quiet: true };
  if (st === "queued") {
    if (j.waits_on) return { chip: base, reason: t("audit.queue.why.agent", { agent: agentName(j.waits_on) }), quiet: true };
    if (j.wait_reason && future(j.run_after)) {
      const reason = lookup(`audit.queue.reason.${j.wait_reason}`) ?? j.wait_reason;
      return { chip: base, reason: t("audit.queue.why.until", { until: when(j.run_after), reason }), quiet: true };
    }
    if (future(j.held_until)) return { chip: base, reason: t("audit.queue.why.held"), quiet: true };
    if (future(j.run_after)) return { chip: base, reason: t("audit.queue.why.scheduled", { at: when(j.run_after) }), quiet: true };
    if (c.workerDown) return { chip: base, reason: null, quiet: true };
    if (c.stopped) return { chip: base, reason: t("audit.queue.why.stopped"), quiet: true };
    return { chip: base, reason: j.last_error ? firstLine(j.last_error) : null, quiet: true };
  }
  if (st === "gated") return { chip: base, reason: t("audit.queue.why.gated"), quiet: true };
  if (st === "failed") {
    if (closedByCycle(j)) return { chip: [t("audit.queue.status.closed"), "muted"], reason: firstLine(j.last_error) || t("audit.queue.why.closed"), quiet: true };
    const said = c.alert?.reason ?? (j.last_error ? null : t("audit.queue.why.no_reason"));
    return { chip: base, reason: said ?? firstLine(j.last_error), quiet: !c.alert };
  }
  if (st === "cancelled") return { chip: base, reason: firstLine(j.last_error) || null, quiet: true };
  return { chip: base, reason: null, quiet: true };
}

type Dupes = { cancel: Row[]; keep: number } | null;

function JobActions({ j, c, dupes, onChange }: { j: Row; c: Ctx; dupes: Dupes; onChange: () => void }) {
  const cancel = (id: number) => post(`/jobs/${id}/cancel`, {});
  if (CANCELLABLE.has(j.status)) {
    return (
      <div className="au-acts">
        <ActionButton className="small" run={() => cancel(j.id).then(onChange)} confirm={t("audit.queue.cancel_confirm")}
          keep={t("audit.queue.cancel_keep")} what={t("audit.queue.cancel_what")} done={t("audit.queue.cancelled", { id: j.id })}>
          {t("audit.queue.cancel")}
        </ActionButton>
        {dupes && (
          <ActionButton className="small" keep={t("audit.queue.cancel_keep")}
            confirm={t("audit.queue.dupes_confirm", { n: dupes.cancel.length })}
            what={t("audit.queue.dupes_what", { n: dupes.cancel.length, id: dupes.keep })}
            done={t("audit.queue.dupes_done", { n: dupes.cancel.length })}
            run={async () => {
              const failed: string[] = [];
              for (const d of dupes.cancel) await cancel(d.id).catch((e) => failed.push(`#${d.id}: ${e.message ?? e}`));
              onChange();
              if (failed.length) throw new Error(failed.join(" "));
            }}>
            {t("audit.queue.dupes", { n: dupes.cancel.length })}
          </ActionButton>
        )}
      </div>
    );
  }
  if (j.status !== "failed") return null;
  if (c.retriedAs) {
    return <p className="au-note">{tx("audit.queue.retried_as", { job: <Link to={`?job=${c.retriedAs}`}>#{c.retriedAs}</Link> })}</p>;
  }
  const closed = closedByCycle(j);
  const blocked = c.alert && c.alert.retryable === false && c.alert.hint;
  return (
    <div className="au-acts">
      <ActionButton className="small" run={() => post(`/jobs/${j.id}/retry`, {}).then(onChange)} done={t("company.queue.retried")}
        {...(closed ? { confirm: t("audit.queue.retry_confirm"), what: t("audit.queue.retry_closed_what"), keep: t("audit.queue.retry_keep") } : {})}>
        {t("company.queue.retry")}
      </ActionButton>
      {j.kind === "execute_action" && (
        <ActionButton className="small" run={() => post(`/jobs/${j.id}/re-request`, {}).then(onChange)} done={t("company.queue.re_requested")}>
          {t("company.queue.re_request")}
        </ActionButton>
      )}
      {j.alert_dismissed_at
        ? <span className="small muted">{t("company.queue.dismissed")}</span>
        : <ActionButton className="small" run={() => post(`/jobs/${j.id}/dismiss`, {}).then(onChange)} done={t("audit.queue.dismissed_done")}>{t("company.queue.dismiss")}</ActionButton>}
      {blocked && <p className="au-note">{c.alert!.hint}</p>}
    </div>
  );
}

function JobRow({ j, c, dupes, highlight, onChange }: { j: Row; c: Ctx; dupes: Dupes; highlight: boolean; onChange: () => void }) {
  const ref = useRef<HTMLLIElement>(null);
  const [open, setOpen] = useState(highlight);
  useEffect(() => {
    if (highlight) ref.current?.scrollIntoView({ block: "center" });
  }, [highlight]);
  const { chip, reason, quiet } = jobState(j, c);
  const { businesses } = useBusinessSelection();
  const target = targetOf(j, businesses);
  const detailId = `job-${j.id}-detail`;
  // tries show once a job has been tried; a waiting job's 0 of 5 says nothing
  const tried = j.attempts > 0 && j.status !== "done";
  return (
    <li ref={ref} className={`au-job ${highlight ? "on" : ""}`}>
      <div className="au-job-line">
        <span className="au-job-lead"><Chip tone={chip[1]}>{chip[0]}</Chip></span>
        <span className="au-job-main">
          <span className="au-job-t">{jobLabel(j.kind)}<BusinessName id={j.business_id} /></span>
          {(target || reason || tried) && (
            <span className="au-job-m">
              {target}{target && reason ? " · " : null}
              {reason && <span className={quiet ? "" : "bad"}>{reason}</span>}
              {tried && <span className="num">{target || reason ? " · " : null}{t("audit.queue.tries", { n: j.attempts, max: j.max_attempts })}</span>}
            </span>
          )}
        </span>
        <span className="au-job-end">
          <JobActions j={j} c={c} dupes={dupes} onChange={onChange} />
          <Time v={j.created_at} />
          <button type="button" className="icon-btn au-job-toggle" aria-expanded={open} aria-controls={detailId}
            aria-label={t("audit.queue.details_of", { id: j.id })} onClick={() => setOpen(!open)}>
            <Icon name="chevron" size={14} />
          </button>
        </span>
      </div>
      {open && (
        <div className="au-job-detail" id={detailId}>
          <dl className="au-props">
            <div><dt>{t("audit.queue.job_id")}</dt><dd className="mono">#{j.id}</dd></div>
            <div><dt>{t("company.queue.attempts")}</dt><dd className="num">{j.attempts}/{j.max_attempts}</dd></div>
            <div><dt>{t("company.queue.created")}</dt><dd>{when(j.created_at)}</dd></div>
            {j.finished_at && <div><dt>{t("audit.queue.finished")}</dt><dd>{when(j.finished_at)}</dd></div>}
            <div><dt>{t("audit.queue.kind")}</dt><dd className="mono">{j.kind}</dd></div>
          </dl>
          {j.last_error && <><p className="au-note">{t("audit.queue.raw")}</p><pre className="pre au-pre">{j.last_error}</pre></>}
        </div>
      )}
    </li>
  );
}

export function Queue() {
  const [params, setParams] = useSearchParams();
  const [scope, setScope] = useAuditScope("company");
  const raw = params.get("status");
  const tab: JobTab = isStatus(raw) ? raw : "all";
  const openId = Number(params.get("job")) || null;
  const noBusiness = useNoBusiness();
  const worker = useWorker();
  const tables = ["projects", "tasks", "dev_jobs", "activity_events", "jobs"];
  const all = useScopeLive<Row[]>(scope, `/jobs?limit=${ALL_LIMIT}`, tables);
  const one = useScopeLive<Row[]>(scope, tab === "all" ? null : `/jobs?status=${tab}&limit=${TAB_LIMIT}`, tables);
  const { data: alerts } = useScopeLive<Row[]>(scope, "/alerts", tables);
  const list = tab === "all" ? all : one;
  const setTab = (k: JobTab) => {
    const next = currentParams();
    if (k === "all") next.delete("status");
    else next.set("status", k);
    next.delete("job");
    setParams(next, { replace: true });
  };
  const reload = () => {
    all.reload();
    one.reload();
  };

  const failedAlerts = new Map((alerts ?? []).filter((a) => a.kind === "job_failed").map((a) => [a.job_id, a]));
  const workerAlert = (alerts ?? []).find((a) => a.kind === "worker_down");
  const rows = all.data ?? [];
  const capped = rows.length >= ALL_LIMIT;
  const count = (s: JobStatus) => rows.filter((j) => j.status === s).length;
  const queued = count("queued") + count("gated");
  const running = count("running");
  const failedOpen = rows.filter((j) => j.status === "failed" && !j.alert_dismissed_at && !closedByCycle(j)).length;
  // the server counts every waiting job while the worker is off; the loaded rows stop at the list's limit
  const waiting = workerAlert?.waiting ?? queued;

  const status: Status = (() => {
    if (all.error && !all.data) return { tone: "bad", text: t("audit.queue.load_failed") };
    if (!all.data) return { text: t("company.queue.loading") };
    const failedNote = failedOpen ? tx("company.queue.failed_open", { n: failedOpen }) : null;
    if (worker.down) {
      return { tone: "bad", text: <span role="status">
        {waiting ? t("audit.queue.worker_off", { n: waiting }) : t("audit.queue.worker_off_none")}{" "}
        <span className="au-fix">{t("audit.queue.worker_fix")} <Fix text={`\`${WORKER_FIX}\``} /></span> {failedNote}
      </span> };
    }
    if (worker.stopped) return { tone: "warn", text: <span role="status">{t("audit.queue.stopped", { n: queued })} {failedNote}</span> };
    if (failedOpen) return { tone: "bad", text: <span role="status">{failedNote}</span> };
    if (running) return { tone: "run", text: <span role="status">{t("company.queue.running", { n: running })}{queued ? t("company.queue.running_queued", { n: queued }) : null}.</span> };
    if (queued) return { tone: "warn", text: <span role="status">{t("audit.queue.waiting", { n: queued })}</span> };
    return { tone: "ok", text: <span role="status">{t("company.queue.idle")}</span> };
  })();

  const frame = (children: ReactNode) => (
    <AuditFrame title={t("company.queue.title")} status={status} sub={t("audit.queue.sub")}
      actions={<ScopeSwitch value={scope} onChange={setScope} />}>
      {children}
    </AuditFrame>
  );
  if (noBusiness && scope === "business") return frame(<FirstRun>{t("audit.queue.firstrun")}</FirstRun>);

  const shown = list.data ?? null;
  // the newest waiting copy of the same work carries the "cancel the duplicates" control; the oldest copy stays
  const groups = new Map<string, Row[]>();
  for (const j of rows) if (CANCELLABLE.has(j.status)) groups.set(sameWork(j), [...(groups.get(sameWork(j)) ?? []), j]);
  const dupesOf = (j: Row): Dupes => {
    const g = groups.get(sameWork(j));
    return g && g.length > 1 && g[0].id === j.id ? { cancel: g.slice(0, -1), keep: g[g.length - 1].id } : null;
  };
  const newerCopy = (j: Row) => rows.find((x) => x.id > j.id && sameWork(x) === sameWork(j))?.id;
  const ctx = (j: Row): Ctx => ({ workerDown: worker.down, stopped: worker.stopped, alert: failedAlerts.get(j.id), retriedAs: j.status === "failed" ? newerCopy(j) : undefined });
  // no count until the list arrives: a list that failed to load counts nothing, not zero
  const tabCount = (k: JobTab) => (capped || !all.data ? null : k === "all" ? rows.length : count(k));
  const tabTone = (k: JobTab): Tone | undefined => (k === "failed" && failedOpen ? "bad" : k === "queued" && queued && worker.down ? "warn" : undefined);

  return frame(
    <>
      <div className="toolbar" role="group" aria-label={t("company.queue.tabs")}>
        <div className="seg au-filter" role="group" aria-label={t("company.queue.tabs")}>
          {TABS.map((k) => {
            const n = tabCount(k);
            return (
              <button key={k} type="button" className={[tab === k ? "on" : "", n === 0 && tab !== k ? "au-zero" : ""].join(" ").trim() || undefined}
                aria-pressed={tab === k} onClick={() => setTab(k)}>
                {k === "all" ? t("company.all") : t(STATUS[k].label)}
                {n != null && <span className={`tab-n ${tabTone(k) ? `t-${tabTone(k)}` : ""}`}>{n}</span>}
              </button>
            );
          })}
        </div>
        <span className="toolbar-r small muted num" role="status">
          {shown ? t(tab === "all" ? "audit.queue.count_all" : "audit.queue.count_tab", { n: shown.length }) : null}
          {shown && shown.length >= (tab === "all" ? ALL_LIMIT : TAB_LIMIT) ? ` ${t("audit.queue.capped")}` : null}
        </span>
      </div>
      <Box pad={false} className="au-list">
        {!shown ? (list.error ? <div className="au-pad"><Loading error={list.error} failure={list.failure} retry={reload} /></div> : <Skeleton rows={5} />)
          : shown.length === 0 ? (
            tab === "all"
              ? <EmptyState title={t("company.queue.none")}>{t(worker.down ? "audit.queue.empty.all_worker_off" : "audit.queue.empty.all")}</EmptyState>
              : <EmptyState title={t(STATUS[tab].empty)} kind="filtered" action={<button type="button" className="btn-ghost small" onClick={() => setTab("all")}>{t("audit.queue.see_all")}</button>} />
          ) : (
            <Rows label={t("company.queue.title")}>
              {shown.map((j) => <JobRow key={j.id} j={j} c={ctx(j)} dupes={dupesOf(j)} highlight={openId === j.id} onChange={reload} />)}
            </Rows>
          )}
      </Box>
      {tab === "all" && capped && <p className="au-foot small muted">{t("audit.queue.older_hint", { n: ALL_LIMIT })}</p>}
    </>,
  );
}

