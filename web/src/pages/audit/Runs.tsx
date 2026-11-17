import { ReactNode, useCallback, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, qs, Row, useLive } from "../../api";
import { cleanName, useBusinessSelection, useFollowBusiness, useNoBusiness } from "../../business";
import { Icon } from "../../components/icons";
import { Banner, Box, Chip, EmptyState, FirstRun, Section, Status, Tone } from "../../components/kit";
import { PanelFooter, PanelSkeleton, SidePanel } from "../../components/panel";
import { dur, Loading, num, RefLink, Skeleton, tid, Time, usd, useShowTest, when } from "../../components/ui";
import { t, tx } from "../../i18n";
import { agentName, agentTitle, purposeLabel } from "../../labels";
import { AuditFrame, ScopeSwitch, useAuditScope, useOlderPages, useScopeLive, useScopePath, currentParams } from "./frame";

type RunsPage = { runs: Row[]; more: boolean; total: number; purposes: string[] };


const okTone = (s: string): Tone => (s === "ok" ? "ok" : "bad");
const okLabel = (s: string) => t(s === "ok" ? "company.ok" : "company.failed");
/** The current address with one filter set, so the scope and other filters stay as they are. */
const withParam = (params: URLSearchParams, k: string, v: string) => {
  const next = new URLSearchParams(params);
  next.set(k, v);
  return `?${next}`;
};
const firstLine = (s: unknown) => String(s ?? "").split("\n", 1)[0].trim();

/** What a call was for: its task, else its build, else its project, else the business it ran for, else the company. */
function Target({ r }: { r: Row }) {
  const { businesses } = useBusinessSelection();
  if (r.task_id || r.dev_job_id || r.project_id) {
    return <span className="au-target"><RefLink code={r.task_id ? tid(r.task_id) : r.dev_job_id ? `J-${r.dev_job_id}` : `P-${r.project_id}`} projectId={r.project_id} /></span>;
  }
  if (r.business_id != null) {
    if (!businesses) return null;
    const b = businesses.find((x) => x.id === r.business_id);
    return <span className="au-target">{b ? cleanName(b.name) : t("audit.queue.business_n", { n: r.business_id })}</span>;
  }
  return <span className="muted">{t("common.whole_company")}</span>;
}

const COLS_KEY = "ship.audit.runs.cols";
function useWideColumns(): [boolean, (v: boolean) => void] {
  const [v, setV] = useState(() => {
    try {
      return localStorage.getItem(COLS_KEY) === "1";
    } catch {
      return false;
    }
  });
  return [v, (next) => {
    setV(next);
    try {
      localStorage.setItem(COLS_KEY, next ? "1" : "0");
    } catch {
      /* private mode: remembered until the page closes */
    }
  }];
}

type Result = "" | "ok" | "error";
const RESULTS: [Result, "company.all" | "company.ok" | "company.failed"][] = [["", "company.all"], ["ok", "company.ok"], ["error", "company.failed"]];

export function Runs() {
  const [params, setParams] = useSearchParams();
  const [showTest] = useShowTest();
  const [scope, setScope] = useAuditScope("business");
  const [wide, setWide] = useWideColumns();
  const { current } = useBusinessSelection();
  const f = { agent: params.get("agent") ?? "", status: params.get("status") ?? "", purpose: params.get("purpose") ?? "",
    project_id: params.get("project") ?? "" };
  const query = { ...f, include_test: showTest };
  const path = `/runs${qs({ ...query, limit: 100 })}`;
  const noBusiness = useNoBusiness();
  const live = useScopeLive<RunsPage>(scope, path, ["activity_events"]);
  // the failure count is the server's, over the same filters, so it matches the list the link opens
  const failed = useScopeLive<RunsPage>(scope, f.status ? null : `/runs${qs({ ...query, status: "error", limit: 1 })}`, ["activity_events"]);
  const { data: agents } = useScopeLive<Row[]>(scope, "/agents", []);
  const pages = useOlderPages(`${scope}|${path}`, live.data?.runs ?? null, !!live.data?.more);
  const scopePath = useScopePath(scope);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  // the call under the cursor opens beside the list (`?call=`), so the owner goes through failures without losing the list
  const open = params.get("call");
  const closePanel = useCallback(() => {
    const next = currentParams();
    next.delete("call");
    setParams(next, { replace: true });
  }, [setParams]);
  const set = (k: string, v: string) => {
    const next = currentParams();
    if (v) next.set(k, v);
    else next.delete(k);
    setParams(next, { replace: true });
  };
  const clear = () => {
    const next = currentParams();
    ["agent", "status", "purpose", "project"].forEach((k) => next.delete(k));
    setParams(next, { replace: true });
  };
  async function loadMore() {
    const last = pages.rows[pages.rows.length - 1]?.id;
    const p = scopePath(`/runs${qs({ ...query, limit: 100, before: last })}`);
    if (!p) return;
    try {
      const r = await api<RunsPage>(p);
      pages.append(r.runs, r.more);
      setLoadErr(null);
    } catch (e: any) {
      setLoadErr(e.message ?? String(e));
    }
  }

  const data = live.data;
  const filtered = !!(f.agent || f.status || f.purpose || f.project_id);
  const failedTotal = f.status === "error" ? data?.total ?? 0 : f.status === "ok" ? 0 : failed.data?.total ?? null;
  const where = scope === "business" && current ? cleanName(current.name) : t("common.whole_company");
  const status: Status = (() => {
    if (!data) return live.error ? { tone: "bad", text: t("audit.runs.load_failed") } : { text: t("company.runs.loading") };
    if (filtered) return { text: t("company.runs.matches", { n: num(data.total) }) };
    if (!data.total) return { text: t("audit.runs.empty_status", { where }) };
    const latestFailed = data.runs[0]?.status !== "ok";
    return {
      tone: latestFailed ? "bad" : failedTotal ? "warn" : "muted",
      text: <>
        {latestFailed ? t("company.runs.last_failed") : null}
        {t("company.runs.total", { n: num(data.total) })}{" "}
        {failedTotal ? <Link to={withParam(params, "status", "error")}>{t("audit.runs.see_failed", { n: num(failedTotal) })}</Link>
          : failedTotal === 0 ? t("audit.runs.none_failed") : null}
      </>,
    };
  })();
  const frame = (children: ReactNode) => (
    <AuditFrame title={t("company.runs.title")} status={status}
      sub={tx("audit.runs.sub", { costs: <Link to="/audit/costs">{t("audit.runs.costs_link")}</Link> })}
      actions={<ScopeSwitch value={scope} onChange={setScope} />}>
      {children}
    </AuditFrame>
  );
  if (noBusiness && scope === "business") return frame(<FirstRun>{t("company.runs.firstrun")}</FirstRun>);

  const none = !!data && data.total === 0 && !filtered;
  const purposes = [...(data?.purposes ?? [])].sort((a, b) => purposeLabel(a).localeCompare(purposeLabel(b)));
  const runs = pages.rows;
  const company = scope === "company";
  return frame(
    <>
      <div className="toolbar" role="group" aria-label={t("audit.runs.filters")}>
        <div className="seg" role="group" aria-label={t("company.runs.result")}>
          {RESULTS.map(([k, key]) => (
            <button key={k} type="button" className={f.status === k ? "on" : ""} aria-pressed={f.status === k} disabled={none} onClick={() => set("status", k)}>{t(key)}</button>
          ))}
        </div>
        <label className="sr-only" htmlFor="r-agent">{t("company.runs.agent")}</label>
        <select id="r-agent" value={f.agent} disabled={none} onChange={(e) => set("agent", e.target.value)}>
          <option value="">{t("company.runs.all_agents")}</option>
          {(agents ?? []).map((a) => <option key={a.id} value={a.id}>{agentTitle(a)}</option>)}
        </select>
        <label className="sr-only" htmlFor="r-purpose">{t("company.runs.purpose")}</label>
        <select id="r-purpose" value={f.purpose} disabled={none} onChange={(e) => set("purpose", e.target.value)}>
          <option value="">{t("company.runs.all_purposes")}</option>
          {purposes.map((p) => <option key={p} value={p}>{purposeLabel(p)}</option>)}
        </select>
        {f.project_id && <span className="tag"><RefLink code={`P-${f.project_id}`} /> <button type="button" className="link small" onClick={() => set("project", "")}>{t("company.runs.clear")}</button></span>}
        {filtered && <button type="button" className="link small" onClick={clear}>{t("company.clear_filters")}</button>}
        <div className="toolbar-r">
          {data && <span className="small muted num" role="status">{t("audit.runs.showing", { shown: num(runs.length), total: num(data.total) })}</span>}
          <button type="button" className="btn-ghost small" aria-pressed={wide} onClick={() => setWide(!wide)}>{t(wide ? "audit.runs.fewer_cols" : "audit.runs.more_cols")}</button>
        </div>
      </div>
      {pages.fresh > 0 && (
        <button type="button" className="au-fresh" onClick={pages.showFresh}>{t("audit.runs.fresh", { n: pages.fresh })}</button>
      )}
      <Box pad={false} className="au-list">
        {!data ? (live.error ? <div className="au-pad"><Loading error={live.error} failure={live.failure} retry={live.reload} /></div> : <Skeleton rows={6} />)
          : none ? (
            <EmptyState title={t("audit.runs.none_title", { where })} action={<Link className="btn-ghost small" to="/">{t("audit.runs.none_go")}</Link>}>
              {t("audit.runs.none_body")}
            </EmptyState>
          ) : runs.length === 0 ? (
            <EmptyState kind="filtered" title={t("company.runs.none_match")} action={<button type="button" className="btn-ghost small" onClick={clear}>{t("company.clear_filters")}</button>}>
              {t("company.runs.none_match_hint")}
            </EmptyState>
          ) : (
            <div className="au-scroll">
              <table className="tbl au-tbl">
                <caption className="sr-only">{t("audit.runs.caption", { where })}</caption>
                <thead>
                  <tr>
                    <th scope="col">{t("company.runs.at")}</th><th scope="col">{t("company.runs.result")}</th>
                    <th scope="col">{t("audit.runs.call")}</th><th scope="col">{t("company.runs.target")}</th>
                    {company && <th scope="col" className="au-side-hide">{t("audit.runs.business")}</th>}
                    <th scope="col" className="au-side-hide">{t("company.runs.agent")}</th>
                    <th scope="col" className="r au-side-hide">{t("company.runs.time")}</th><th scope="col" className="r">{t("audit.runs.usage")}</th>
                    {wide && <><th scope="col" className="au-side-hide">{t("company.runs.model")}</th><th scope="col" className="r au-side-hide">{t("company.runs.tokens")}</th></>}
                  </tr>
                </thead>
                <tbody>
                  {runs.map((r) => {
                    const why = firstLine(r.error);
                    return (
                      <tr key={r.id} className={[r.status !== "ok" ? "is-bad" : "", String(r.id) === open ? "on" : ""].join(" ").trim() || undefined}>
                        <td className="nowrap muted"><Time v={r.created_at} /></td>
                        <td className="au-result">
                          <Chip tone={okTone(r.status)}>{okLabel(r.status)}</Chip>
                          {r.status !== "ok" && (why ? <span className="au-err" title={why}>{why}</span> : <span className="au-err au-err-none">{t("audit.runs.no_reason")}</span>)}
                        </td>
                        <td className="au-call">
                          <Link to={withParam(params, "call", String(r.id))} replace aria-current={String(r.id) === open ? "true" : undefined}>{purposeLabel(r.purpose)}</Link>
                          <span className="muted mono au-id">#{r.id}</span>
                          {r.attempt > 1 && <span className="muted small"> · {t("company.attempt", { n: r.attempt })}</span>}
                        </td>
                        <td className="au-for"><Target r={r} /></td>
                        {company && <td className="au-for au-side-hide"><BusinessCell id={r.business_id} /></td>}
                        <td className="nowrap au-side-hide"><Link className="au-quiet" to={`/agents/${r.agent}`}>{agentName(r.agent)}</Link></td>
                        <td className="r nowrap num au-side-hide">{dur(r.duration_ms)}</td>
                        <td className="r num">{usd(r.cost_usd)}</td>
                        {wide && <><td className="au-side-hide"><span className="au-model">{r.model || r.provider}</span></td><td className="r small num nowrap au-side-hide">{num(r.input_tokens)} / {num(r.output_tokens)}</td></>}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        {(pages.more || loadErr) && data && (
          <div className="au-more-row">
            {pages.more && <button type="button" className="btn-ghost small" onClick={loadMore}>{t("company.runs.more")}</button>}
            {loadErr && <span className="small bad" role="alert">{t("company.load_more_failed", { error: loadErr })}</span>}
          </div>
        )}
      </Box>
      {open && <RunPanel id={open} onClose={closePanel} />}
    </>,
  );
}

function BusinessCell({ id }: { id: number | null }) {
  const { businesses } = useBusinessSelection();
  if (id == null) return <span className="muted">{t("common.whole_company")}</span>;
  if (!businesses) return null;
  const b = businesses.find((x) => x.id === id);
  return <>{b ? cleanName(b.name) : t("audit.queue.business_n", { n: id })}</>;
}

/** Where to look after a failed call: the work it was for says whether that work went on or waits for the owner. */
function failedNext(data: Row): ReactNode {
  if (data.task) return tx("audit.run.failed_next_task", { link: <Link to={`/projects/${data.project_id}/tasks?task=${data.task.id}`}>{data.task.title}</Link> });
  if (data.dev_job_id || data.project_id) return tx("audit.run.failed_next_work", { link: <Target r={data} /> });
  return t("audit.run.failed_next_none");
}

export function RunDetail() {
  const { id } = useParams();
  const { data, error, failure, reload } = useLive<Row>(`/runs/${id}`, []);
  const title = t("company.run.title", { id: id ?? "" });
  useFollowBusiness(data ? data.business_id ?? null : undefined, `run:${id}`, title);
  const back = <Link className="btn-ghost" to="/audit/runs"><Icon name="runs" />{t("company.run.list")}</Link>;
  if (!data) {
    if (failure === "missing") {
      return (
        <AuditFrame title={title} status={null}>
          <Box><EmptyState title={t("audit.run.missing", { id: id ?? "" })} action={<Link className="btn-ghost small" to="/audit/runs">{t("company.run.list")}</Link>}>{t("audit.run.missing_body")}</EmptyState></Box>
        </AuditFrame>
      );
    }
    return (
      <AuditFrame title={title} status={error ? { tone: "bad", text: t("audit.run.load_failed") } : null} actions={back}>
        {error ? <Loading error={error} failure={failure} retry={reload} /> : <Skeleton rows={4} />}
      </AuditFrame>
    );
  }
  const ok = data.status === "ok";
  return (
    <AuditFrame
      title={<span className="au-title">{t("company.run.title", { id: data.id })}<Chip tone={okTone(data.status)}>{okLabel(data.status)}</Chip></span>}
      status={ok ? null : { tone: "bad", text: t("audit.run.failed") }}
      sub={<RunLine data={data} />}
      actions={back}>
      <RunBody data={data} />
    </AuditFrame>
  );
}

/** Who made the call, for what, and when: the detail's second line. */
function RunLine({ data }: { data: Row }) {
  return <><Link to={`/agents/${data.agent}`}>{agentName(data.agent)}</Link> · {purposeLabel(data.purpose)} · {when(data.created_at)}</>;
}

/** One call beside the list. The page at /audit/runs/:id stays for links from elsewhere. */
function RunPanel({ id, onClose }: { id: string; onClose: () => void }) {
  const { data, error, failure, reload } = useLive<Row>(`/runs/${id}`, []);
  const title = t("company.run.title", { id });
  return (
    <SidePanel item={`run-${id}`} title={title} onClose={onClose}>
      {!data ? (
        failure === "missing" ? <EmptyState title={t("audit.run.missing", { id })}>{t("audit.run.missing_body")}</EmptyState>
          : error ? <Loading error={error} failure={failure} retry={reload} /> : <PanelSkeleton />
      ) : (
        <div className="au-panel">
          <p className="au-panel-line">
            <Chip tone={okTone(data.status)}>{okLabel(data.status)}</Chip>
            <span className="muted"><RunLine data={data} /></span>
          </p>
          <RunBody data={data} />
        </div>
      )}
      <PanelFooter>
        <Link className="btn-ghost small" to={`/audit/runs/${id}`}>{t("audit.run.open_page")}</Link>
      </PanelFooter>
    </SidePanel>
  );
}

/** What one call did: the next step when it failed, the summary, then its prompt and answer files. */
function RunBody({ data }: { data: Row }) {
  const f = data.files ?? {};
  const ok = data.status === "ok";
  const raw = String(data.error ?? "").trim();
  return (
    <>
      {!ok && (
        <Banner tone="bad">
          <p className="au-banner-t">{failedNext(data)}</p>
          {raw ? (
            <details className="au-more">
              <summary>{t("audit.run.raw")}</summary>
              <pre className="pre au-pre">{raw}</pre>
            </details>
          ) : <p className="au-note">{t("audit.runs.no_reason")}</p>}
        </Banner>
      )}
      {ok && raw && <Banner tone="muted" icon="alert">{raw}</Banner>}
      <Section title={t("company.run.summary")}>
        <Box>
          <dl className="au-dl">
            <div><dt>{t("company.runs.target")}</dt><dd>
              {data.task ? <Link to={`/projects/${data.project_id}/tasks?task=${data.task.id}`}>{data.task.title}</Link> : <Target r={data} />}
              {data.attempt > 1 && ` · ${t("company.attempt", { n: data.attempt })}`}
            </dd></div>
            <div><dt>{t("company.runs.agent")}</dt><dd><Link to={`/agents/${data.agent}`}>{agentName(data.agent)}</Link></dd></div>
            <div><dt>{t("company.run.duration")}</dt><dd className="num">{dur(data.duration_ms)}</dd></div>
            <div><dt>{t("audit.runs.usage")}</dt><dd className="num">{usd(data.cost_usd)}</dd></div>
          </dl>
          <details className="au-more au-tech">
            <summary>{t("audit.run.tech")}</summary>
            <dl className="au-dl">
              <div><dt>{t("company.runs.model")}</dt><dd><span className="au-model">{data.model || data.provider}</span></dd></div>
              <div><dt>{t("company.run.tokens")}</dt><dd className="num">{t("company.run.tokens_value",
                { input: num(data.input_tokens), output: num(data.output_tokens), cached: num(data.cached_tokens) })}</dd></div>
              <div><dt>{t("company.run.call_id")}</dt><dd className="mono au-break">{data.call_id}</dd></div>
            </dl>
          </details>
        </Box>
      </Section>
      {Object.keys(f).length === 0 ? (
        <Box><EmptyState title={t("audit.run.no_files")}>{t("audit.run.no_files_body")}</EmptyState></Box>
      ) : (
        <>
          {f.output !== undefined && (
            <Section title={t("company.run.output")}>
              <Box><pre className="pre tall au-pre-flat">{typeof f.output === "string" ? f.output : JSON.stringify(f.output, null, 2)}</pre></Box>
            </Section>
          )}
          <div className="cols cols-2">
            {f.prompt && (
              <Section title={t("company.run.prompt")}>
                <Box><details><summary>{t("company.chars", { n: num(f.prompt.length) })}</summary><pre className="pre tall au-pre-flat">{f.prompt}</pre></details></Box>
              </Section>
            )}
            {f.system_prompt && (
              <Section title={t("company.run.system")}>
                <Box><details><summary>{t("company.chars", { n: num(f.system_prompt.length) })}</summary><pre className="pre tall au-pre-flat">{f.system_prompt}</pre></details></Box>
              </Section>
            )}
          </div>
        </>
      )}
    </>
  );
}
