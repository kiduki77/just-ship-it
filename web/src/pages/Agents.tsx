import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { qs, Row, TZ } from "../api";
import { ACTIVE_TASK, AGENT_KIND, AgentKind, agentStatus, AgentStatus, compareAgents, failedByOwner, neverWorked } from "../agentState";
import { cleanName, useBusinessLive, useBusinessSelection, useCompanyLive, useNoBusiness } from "../business";
import { BarChart } from "../components/charts";
import { Fix } from "../components/fix";
import { useWorker, WORKER_FIX } from "../components/frame";
import { Icon } from "../components/icons";
import { Banner, Box, Chip, EmptyState, FirstRun, Hint, PageHead, ScopeSwitch, Section, Status, TabPanel, Tabs, Tone, Toolbar } from "../components/kit";
import { AgentStopButton } from "../components/stop";
import { ago, dur, Loading, Markdown, num, RefLink, Time, usd, useShowTest, when } from "../components/ui";
import { language, lookup, t, tx } from "../i18n";
import { agentName, agentTitle, errorText, label, purposeLabel, verdictLabel } from "../labels";
import { AgentChip, TaskLink, useAgents, useScope, WorkLink } from "../components/people";
import "./company.css";
import "./people.css";

const TABLES = ["tasks", "dev_jobs", "activity_events", "llm_calls"];

type Tab = "all" | AgentKind;
const TABS: Tab[] = ["all", "working", "failed", "stopped", "idle"];

/** What the server's title says beyond the glossary name ("CMO" of "CMO · 마케팅"): its parts that are not that name
 * and not in the other language's script. */
const alsoKnownAs = (a: Row) => {
  const name = agentTitle(a);
  const parts = String(a.title ?? "").split(" · ")
    .filter((p) => p && p !== name && (language() === "ko" ? !/^[A-Za-z0-9 &/-]+$/.test(p) || /[\uac00-\ud7a3]/.test(p) : !/[\uac00-\ud7a3]/.test(p)));
  return parts.length ? parts.join(" · ") : null;
};
const dept = (d: string) => lookup(`company.dept.${d}`) ?? d;
const toolName = (tool: string) => lookup(`company-people.tool.${tool}`) ?? tool;
const taskTone = (s: string): Tone => (s === "DONE" ? "ok" : s === "FAILED" ? "bad" : ACTIVE_TASK.includes(s) ? "run" : "muted");

/** An agent's runs, costs and tasks belong to a business: with none, the page says how to start one. */
function NoAgentWork({ title }: { title: string }) {
  return (
    <div className="owner-page co-page pp-page">
      <PageHead title={title} sub={t("company-people.agents.sub")} />
      <FirstRun>{t("company.agents.firstrun")}</FirstRun>
    </div>
  );
}

/** What the agent is doing now, in one line: its work, its failure, its stop, or when it last worked. */
function NowCell({ a, s }: { a: Row; s: AgentStatus }) {
  if (s.work.length) {
    return (
      <span className="pp-now">
        {s.work.slice(0, 2).map((w, i) => <span key={i} className="pp-now-i">{i > 0 && ", "}<WorkLink w={w} /></span>)}
        {s.work.length > 2 && <span className="pp-quiet"> {t("company.more", { n: s.work.length - 2 })}</span>}
        {s.kind === "stopped" && s.stoppedSince && <span className="pp-quiet"> · {t("company-people.stopped_since", { since: ago(s.stoppedSince) })}</span>}
      </span>
    );
  }
  if (s.kind === "stopped") return <span className="pp-quiet">{t("company-people.stopped_since", { since: when(s.stoppedSince) })}</span>;
  if (s.failed.length) return <span className="pp-now pp-bad">{tx("company-people.task_failed", { n: s.failed.length, task: <TaskLink task={s.failed[0]} /> })}</span>;
  return (
    <span className="pp-quiet">
      {a.last_run_at ? tx("company-people.last_call", { when: <Time v={a.last_run_at} /> }) : t("company-people.no_calls")}
      {s.lastCallFailed && <span className="pp-callfail"> · {t("company-people.call_failed")}</span>}
    </span>
  );
}

function AgentRows({ rows, scopeQs }: { rows: [Row, AgentStatus][]; scopeQs: string }) {
  const navigate = useNavigate();
  const open = (id: string) => navigate(`/agents/${id}${scopeQs ? `?${scopeQs}` : ""}`);
  return (
    <Box pad={false} className="pp-tbl-box">
      <table className="tbl pp-tbl">
        <thead>
          <tr>
            <th scope="col">{t("company.agents.col.agent")}</th>
            <th scope="col">{t("company.agents.col.state")}</th>
            <th scope="col" className="pp-wide">{t("company-people.col.now")}</th>
            <th scope="col" className="r pp-opt2">{t("company.agents.calls_24h")}</th>
            <th scope="col" className="r">{t("company.agents.this_month")}</th>
            <th scope="col" className="r pp-opt">{t("company.agents.tasks")}</th>
            <th scope="col" className="r pp-opt">{t("company-people.col.calls_total")}</th>
            <th scope="col" className="pp-opt">{t("company.agents.col.model")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([a, s]) => (
            <tr key={a.id} className={`pp-tr k-${s.kind}`}
              onClick={(e) => !(e.target as HTMLElement).closest("a,button") && open(a.id)}>
              <th scope="row">
                <Link className="pp-name" to={`/agents/${a.id}${scopeQs ? `?${scopeQs}` : ""}`}>{agentTitle(a)}</Link>
                <span className="pp-meta">{dept(a.department)}{a.reviewer ? t("company.agents.reviewer") : ""}{a._alias ? ` · ${t("company-people.found_as", { alias: a._alias })}` : ""}</span>
              </th>
              <td>
                <span className="pp-state">
                  <AgentChip s={s} />
                  {s.kind === "stopped" && <AgentStopButton agent={a.id} stopped />}
                </span>
              </td>
              <td className="pp-wide"><NowCell a={a} s={s} /></td>
              <td className="r pp-opt2">{num(a.calls_24h)}{a.errors_24h > 0 && <span className="pp-quiet">{t("company.failures", { n: a.errors_24h })}</span>}</td>
              <td className="r">{usd(a.cost_month)}</td>
              <td className="r pp-opt">{num(a.tasks_total)}</td>
              <td className="r pp-opt">{num(a.calls_total)}</td>
              <td className="pp-opt"><span className="pp-model">{a.model ?? ""}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </Box>
  );
}

/** Who is on the team, what each is doing now, and whether anyone is stuck. */
export default function Agents() {
  const [params, setParams] = useSearchParams();
  const [scope, setScope, scopeQs] = useScope("business");
  const [q, setQ] = useState("");
  const worker = useWorker();
  const { current } = useBusinessSelection();
  const { agents, failed } = useAgents(scope === "company");
  const noBusiness = useNoBusiness();
  if (noBusiness) return <NoAgentWork title={t("company.agents.title")} />;
  const head = (status?: Status) => (
    <PageHead title={t("company.agents.title")} status={status}
      sub={<>{t("company-people.agents.sub")} {scope === "business" && current ? t("company-people.scope.business_note", { name: cleanName(current.name) }) : t("company-people.scope.company_note")}</>}
      actions={<Link className="btn-ghost" to={`/crews${scope === "business" && current ? `?business_id=${current.id}` : ""}`}><Icon name="users" />{t("shell.nav.crews")}</Link>} />
  );
  if (!agents.data) {
    return <div className="owner-page co-page pp-page">{head()}<Loading error={agents.error} retry={agents.reload} failure={agents.failure} /></div>;
  }

  const byOwner = failedByOwner(failed.data?.tasks);
  const all: [Row, AgentStatus][] = agents.data.map((a) => [a, agentStatus(a, byOwner.get(a.id) ?? [], worker)]);
  all.sort(([a, sa], [b, sb]) => compareAgents(a, sa, b, sb));
  const counts = Object.fromEntries(TABS.map((k) => [k, k === "all" ? all.length : all.filter(([, s]) => s.kind === k || (k === "working" && s.kind === "waiting")).length])) as Record<Tab, number>;
  const raw = params.get("state");
  const tab: Tab = raw === "running" ? "working" : raw === "error" ? "failed" : TABS.includes(raw as Tab) ? raw as Tab : "all";
  const setTab = (k: Tab) => {
    const next = new URLSearchParams(params);
    if (k === "all") next.delete("state");
    else next.set("state", k);
    setParams(next, { replace: true });
  };

  const needle = q.trim().toLowerCase();
  const matches = (a: Row) => {
    if (!needle) return { hit: true, alias: null };
    const named = [agentTitle(a), a.id, dept(a.department), a.model].some((x) => String(x ?? "").toLowerCase().includes(needle));
    const alias = alsoKnownAs(a);
    const byAlias = !named && String(a.title ?? "").toLowerCase().includes(needle);
    return { hit: named || byAlias, alias: byAlias ? alias ?? a.title : null };
  };
  const inTab = all.filter(([, s]) => tab === "all" || s.kind === tab || (tab === "working" && s.kind === "waiting"));
  const shown = inTab.flatMap(([a, s]) => {
    const m = matches(a);
    return m.hit ? [[{ ...a, _alias: m.alias }, s] as [Row, AgentStatus]] : [];
  });
  const folded = tab === "all" && !needle ? shown.filter(([a, s]) => neverWorked(a, s)) : [];
  const listed = folded.length ? shown.filter((x) => !folded.includes(x)) : shown;

  const working = all.filter(([, s]) => s.kind === "working").length;
  const waiting = all.filter(([, s]) => s.kind === "waiting").length;
  const nFailed = counts.failed;
  const nStopped = counts.stopped;
  const status: Status = {
    tone: waiting ? "warn" : nFailed ? "bad" : working ? "run" : nStopped ? "warn" : "muted",
    text: <>
      {waiting ? t(worker.down ? "company-people.agents.waiting" : "company-people.agents.waiting_stop", { n: waiting })
        : working ? t("company-people.agents.working", { n: working }) : t("company-people.agents.idle", { n: all.length })}
      {waiting && working ? ` ${t("company-people.agents.working", { n: working })}` : ""}
      {nFailed ? t("company-people.agents.failed", { n: nFailed }) : ""}
      {nStopped ? t("company-people.agents.stopped", { n: nStopped }) : ""}
    </>,
  };
  const tabLabel = (k: Tab) => (k === "all" ? t("company.all") : k === "working" && waiting ? t("company-people.state_tabs_waiting") : t(AGENT_KIND[k].label));
  const emptyTitle = needle ? t("company.agents.none_found")
    : t(tab === "working" ? "company-people.none_waiting" : tab === "failed" ? "company-people.none_failed" : tab === "stopped" ? "company-people.none_stopped" : "company-people.none_idle");

  return (
    <div className="owner-page co-page pp-page">
      {head(status)}
      {waiting > 0 && worker.down && (
        <Banner tone="warn">{t("company-people.agents.fix")} <Fix text={`\`${WORKER_FIX}\``} /></Banner>
      )}
      {failed.error && !failed.data && <p className="pp-note" role="status">{t("company-people.failed_load")}</p>}

      <Toolbar label={t("company.agents.state_tabs")} right={<ScopeSwitch value={scope} onChange={setScope} notes={current ? { business: t("company-people.scope.business_note", { name: cleanName(current.name) }), company: t("company-people.scope.company_note") } : undefined} />}>
        <Tabs<Tab> id="agents" label={t("company.agents.state_tabs")} value={tab} onChange={setTab}
          items={TABS.filter((k) => k !== "stopped" || counts.stopped || tab === "stopped").map((k) => ({
            key: k, label: tabLabel(k), count: counts[k], tone: k === "failed" && counts.failed ? "bad" : undefined,
          }))} />
        <label className="search-in">
          <Icon name="search" />
          <span className="sr-only">{t("company.agents.search")}</span>
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("company.agents.search_hint")} />
        </label>
      </Toolbar>

      <TabPanel id="agents" tabKey={tab}>
        {shown.length === 0 ? (
          needle ? (
            <EmptyState kind="filtered" title={emptyTitle} action={<button type="button" className="btn-ghost small" onClick={() => setQ("")}>{t("company-people.show_all")}</button>}>
              {t("company.agents.search_again")}
            </EmptyState>
          ) : (
            <Hint title={emptyTitle} action={<button type="button" className="btn-ghost small" onClick={() => setTab("all")}>{t("company-people.show_all")}</button>}>
              {t("company-people.none_hint")}
            </Hint>
          )
        ) : (
          <>
            {listed.length > 0 && <AgentRows rows={listed} scopeQs={scopeQs} />}
            {folded.length > 0 && (
              <p className="pp-folded">
                {t(scope === "company" ? "company-people.never_worked_any" : "company-people.never_worked", { n: folded.length })}{" "}
                {folded.map(([a], i) => <span key={a.id}>{i > 0 && ", "}<Link to={`/agents/${a.id}${scopeQs ? `?${scopeQs}` : ""}`}>{agentTitle(a)}</Link></span>)}
              </p>
            )}
          </>
        )}
      </TabPanel>
    </div>
  );
}

function sumKey(rows: Row[], key: string) {
  return rows.reduce((s, r) => s + Number(r[key] ?? 0), 0);
}

function RecentRuns({ runs, agent }: { runs: Row[]; agent: string }) {
  return (
    <Section title={t("company.agent.recent")} count={runs.length} actions={<Link className="small" to={`/audit/runs?agent=${agent}`}>{t("company.agent.all_runs")}</Link>}>
      <Box pad={false}>
        {runs.length === 0 ? <div className="box-foot muted">{t("company.agent.no_runs")}</div> : (
          <ul className="rows pp-runs">
            {runs.slice(0, 15).map((r) => {
              const ok = r.status === "ok";
              const code = r.task_id ? `T-${r.task_id}` : r.dev_job_id ? `J-${r.dev_job_id}` : r.project_id ? `P-${r.project_id}` : "";
              return (
                <li key={r.id} className="row-i">
                  <span className="row-lead"><Chip tone={ok ? "ok" : "bad"}>{t(ok ? "company.ok" : "company.failed")}</Chip></span>
                  <span className="row-main">
                    <Link className="row-t" to={`/audit/runs/${r.id}`}
                      aria-label={t("company-people.agent.run_name", { id: r.id, status: t(ok ? "company.ok" : "company.failed"), when: ago(r.created_at) })}>
                      {purposeLabel(r.purpose)}{r.attempt > 1 ? ` · ${t("company.attempt", { n: r.attempt })}` : ""}
                    </Link>
                    {(code || (!ok && r.error)) && (
                      <span className="row-m">
                        {code && <RefLink code={code} projectId={r.project_id} />}
                        {!ok && r.error ? <span className="pp-bad">{code ? " · " : ""}{errorText(r.error)}</span> : null}
                      </span>
                    )}
                  </span>
                  <span className="row-end pp-run-facts">
                    <span>{dur(r.duration_ms)}</span>
                    <span className="pp-strong">{usd(r.cost_usd)}</span>
                    <Time v={r.created_at} />
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </Box>
    </Section>
  );
}

function ByPurpose({ rows }: { rows: Row[] }) {
  const sorted = [...rows].sort((a, b) => b.calls - a.calls);
  if (sorted.length === 0) return null;
  return (
    <Section title={t("company.agent.by_purpose")} actions={<span className="small muted">{t("company.cumulative")}</span>}>
      <Box pad={false} className="pp-tbl-box">
        <table className="tbl pp-tbl pp-tbl-plain">
          <thead><tr><th scope="col">{t("company.runs.purpose")}</th><th scope="col" className="r">{t("company.costs.runs")}</th>
            <th scope="col" className="r">{t("company.failed")}</th>
            <th scope="col" className="r">{t("company.agent.avg_time")}</th><th scope="col" className="r">{t("company.costs.spend")}</th></tr></thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={r.purpose}><td>{purposeLabel(r.purpose)}</td><td className="r">{num(r.calls)}</td>
                <td className="r">{r.errors ? <span className="pp-bad">{r.errors}</span> : "-"}</td>
                <td className="r">{dur(r.duration_ms)}</td><td className="r">{usd(r.cost_usd)}</td></tr>
            ))}
          </tbody>
        </table>
      </Box>
    </Section>
  );
}

const GROUPS = [
  { key: "failed", label: "company-people.agent.group.failed", has: (s: string) => s === "FAILED" },
  { key: "open", label: "company-people.agent.group.open", has: (s: string) => s !== "FAILED" && s !== "DONE" && s !== "CANCELLED" },
  { key: "done", label: "company-people.agent.group.done", has: (s: string) => s === "DONE" || s === "CANCELLED" },
] as const;

function OwnedTasks({ tasks, agent }: { tasks: Row[]; agent: string }) {
  return (
    <Section title={t("company.agent.owned")} count={tasks.length} actions={<Link className="small" to={`/tasks?owner=${agent}`}>{t("company.agent.in_tasks")}</Link>}>
      {tasks.length === 0 ? <Hint title={t("company.agent.no_tasks")} /> : (
        <Box pad={false}>
          {GROUPS.map((g) => {
            const rows = tasks.filter((x) => g.has(x.status));
            if (!rows.length) return null;
            return (
              <div key={g.key} className={`pp-group g-${g.key}`}>
                <p className="pp-group-h">{t(g.label)}<span className="sec-n">{rows.length}</span></p>
                <ul className="rows">
                  {rows.slice(0, g.key === "done" ? 8 : 15).map((task) => (
                    <li key={task.id}>
                      <Link className="row-i" to={`/projects/${task.project_id}/tasks?task=${task.id}`} title={task.title}>
                        <span className="row-main">
                          <span className="row-t">{task.title}</span>
                          <span className="row-m">{task.project}{task.verdict ? t("company.agent.verdict", { verdict: verdictLabel(task) }) : ""}</span>
                        </span>
                        <span className="row-end"><Chip tone={taskTone(task.status)}>{label(task.status)}</Chip><Time v={task.updated_at} /></span>
                      </Link>
                    </li>
                  ))}
                </ul>
                {g.key === "done" && rows.length > 8 && (
                  <p className="pp-group-more"><Link to={`/tasks?owner=${agent}`}>{t("company.agent.rest", { n: rows.length - 8 })}</Link></p>
                )}
              </div>
            );
          })}
        </Box>
      )}
    </Section>
  );
}

/** The agent's fixed properties: who it is, what it may use, and the instructions it works from. */
function Properties({ data }: { data: Row }) {
  const skills: Row[] = data.skills ?? [];
  const tools: string[] = data.tools ?? [];
  const on = skills.filter((k) => k.status === "ACTIVE");
  const off = skills.filter((k) => k.status !== "ACTIVE" && k.status !== "RETIRED");
  const alias = alsoKnownAs(data);
  return (
    <aside className="pp-props" aria-labelledby="pp-props-h">
      <h2 id="pp-props-h" className="pp-props-h">{t("company-people.agent.properties")}</h2>
      <dl className="pp-dl">
        <dt>{t("company-people.agent.department")}</dt>
        <dd>{dept(data.department)}{alias ? <span className="pp-quiet"> · {alias}</span> : null}</dd>
        <dt>{t("company-people.agent.model")}</dt>
        <dd><span className="pp-model">{data.model ?? "-"}</span></dd>
        {data.reviewer && (
          <>
            <dt>{t("company-people.agent.reviews")}</dt>
            <dd>{t("company-people.agent.reviewer_of")}</dd>
          </>
        )}
        <dt>{t("company.agent.tools")}</dt>
        <dd>
          {tools.length ? <span className="pp-chips">{tools.map((tool) => <span key={tool} className="tag" title={tool}>{toolName(tool)}</span>)}</span>
            : t("company.none")}
          <span className="pp-quiet pp-block">{t("company-people.agent.tools_note")}</span>
        </dd>
        <dt className="pp-dt-link">{t("company-people.agent.skills_on")}<Link to="/skills">{t("company.agent.skills")}</Link></dt>
        <dd>
          {on.length ? <span className="pp-chips">{on.map((k) => <Link key={k.id} className="tag" to={`/skills?id=${k.id}`}>{k.name} <span className="mono">v{k.version}</span></Link>)}</span>
            : t("company.agent.no_skills")}
        </dd>
        {off.length > 0 && (
          <>
            <dt>{t("company-people.agent.skills_wait")}</dt>
            <dd className="pp-list">
              {off.map((k) => <span key={k.id}><Link to={`/skills?id=${k.id}`}>{k.name} v{k.version}</Link><span className="pp-quiet"> · {label(k.status)}</span></span>)}
            </dd>
          </>
        )}
      </dl>
      {data.prompt_text && (
        <details className="pp-prompt">
          <summary>{t("company.agent.prompt")}</summary>
          <p className="pp-quiet">{t("company-people.agent.prompt_path")} <span className="mono">prompts/agents/{data.prompt}</span>{t("company.agent.prompt_note")}</p>
          <Markdown text={data.prompt_text} />
        </details>
      )}
    </aside>
  );
}

/** The sentence under the agent's name: what it holds, what failed, its stop, or when it last worked. */
function agentSentence(data: Row, s: AgentStatus, worker: { down: boolean }): Status {
  const work = <>{s.work.map((w, i) => <span key={i}>{i ? ", " : ""}<WorkLink w={w} /></span>)}</>;
  const callFailed = s.lastCallFailed && data.last_run_at
    ? tx("company-people.agent.call_failed", { link: <Link to={`/audit/runs?agent=${data.id}&status=error`}>{t("company-people.agent.call_failed_link")}</Link> }) : null;
  if (s.kind === "stopped") {
    const pending = (data.tasks ?? []).filter((x: Row) => ACTIVE_TASK.includes(x.status) || x.status === "READY");
    return { tone: "warn", text: <>
      {t("company-people.agent.stopped", { since: when(s.stoppedSince) })}
      {s.work.length ? tx("company.agent.finishing", { work }) : null}
      {pending.length ? tx("company-people.agent.stopped_pending", { n: pending.length,
        tasks: <>{pending.slice(0, 3).map((x: Row, i: number) => <span key={x.id}>{i ? ", " : ""}<TaskLink task={x} /></span>)}</> }) : null}
    </> };
  }
  if (s.kind === "working") return { tone: "run", text: tx("company-people.agent.working", { work }) };
  if (s.kind === "waiting") return { tone: "warn", text: <>
    {tx(worker.down ? "company-people.agent.waiting" : "company-people.agent.waiting_stop", { work })}
    {worker.down && <> <Fix text={`\`${WORKER_FIX}\``} /></>}
  </> };
  if (s.kind === "failed") return { tone: "bad", text: <>{tx("company-people.agent.failed", { n: s.failed.length, task: <TaskLink task={s.failed[0]} /> })}{callFailed}</> };
  return { text: <>
    {t("company-people.agent.idle")}
    {data.last_run_at ? tx("company-people.agent.idle_last", { when: <Time v={data.last_run_at} /> }) : t("company-people.agent.idle_never")}
    {callFailed}
  </> };
}

/** One agent: what it holds and how its tasks went first, its call and spend history folded below, its properties beside. */
export function AgentPage() {
  const { id } = useParams();
  const [showTest] = useShowTest();
  const [scope] = useScope("business");
  const worker = useWorker();
  const path = `/agents/${id}${qs({ tz: TZ, include_test: showTest })}`;
  const inBusiness = useBusinessLive<Row>(scope === "business" ? path : null, TABLES);
  const inCompany = useCompanyLive<Row>(scope === "company" ? path : null, TABLES);
  const { data, error, reload, failure } = scope === "company" ? inCompany : inBusiness;
  const noBusiness = useNoBusiness();
  if (noBusiness) return <NoAgentWork title={agentName(id ?? "")} />;
  if (!data) {
    return (
      <div className="owner-page co-page pp-page">
        <PageHead title={agentName(id ?? "")} />
        <Loading error={error} retry={reload} failure={failure} />
        {failure === "missing" && <p><Link to="/agents">{t("company.agents.title")}</Link></p>}
      </div>
    );
  }

  const tasks: Row[] = data.tasks ?? [];
  const s = agentStatus(data, tasks.filter((x) => x.status === "FAILED"), worker);
  const series: Row[] = data.series ?? [];
  const runs: Row[] = data.runs ?? [];
  const byPurpose: Row[] = data.by_purpose ?? [];
  const n = (pred: (x: string) => boolean) => tasks.filter((x) => pred(x.status)).length;
  const done = n((x) => x === "DONE");
  const failedN = n((x) => x === "FAILED");
  const calls14 = sumKey(series, "ok") + sumKey(series, "error");
  const openTasks = tasks.filter((x) => GROUPS[1].has(x.status));

  return (
    <div className="owner-page co-page pp-page">
      <PageHead
        title={agentTitle(data)}
        status={agentSentence(data, s, worker)}
        sub={<>
          <span className="pp-headchip"><AgentChip s={s} /></span>
          {dept(data.department)}{data.reviewer ? t("company.agents.reviewer") : ""}{data.model ? ` · ${data.model}` : ""}
          {" · "}<Link to="/audit/costs">{t("company-people.agent.month", { cost: usd(data.cost_month) })}</Link>
          {" · "}{t("company-people.agent.tasks", { n: tasks.length >= 40 ? 40 : tasks.length, done, failed: failedN, open: tasks.length - done - failedN })}
        </>}
        actions={<>
          <Link className="btn-ghost" to={`/audit/runs?agent=${data.id}`}><Icon name="runs" />{t("company.runs.title")}</Link>
          <span className="pp-stop">
            <AgentStopButton agent={data.id} stopped={!!data.stopped} quiet={s.kind === "idle"} />
          </span>
        </>} />

      <div className="pp-detail">
        <div className="pp-main">
          <Section title={t("company-people.agent.now")} count={s.work.length || undefined}>
            {s.work.length ? (
              <Box pad={false}>
                <ul className="rows">
                  {s.work.map((w, i) => (
                    <li key={i} className="row-i">
                      <span className="row-lead"><AgentChip s={s} /></span>
                      <span className="row-main"><span className="row-t"><WorkLink w={w} /></span>{w.project && <span className="row-m">{w.project}</span>}</span>
                    </li>
                  ))}
                </ul>
              </Box>
            ) : <p className="pp-quiet pp-none">{openTasks.length
              ? tx("company-people.agent.now_queued", { n: openTasks.length, task: <TaskLink task={openTasks[0]} /> })
              : t("company-people.agent.now_none")}</p>}
          </Section>
          <OwnedTasks tasks={tasks} agent={data.id} />
          <details className="pp-history">
            <summary>
              <span className="pp-history-t">{t("company-people.agent.history")}</span>
              <span className="pp-quiet">{t("company-people.agent.history_sum", { calls: num(calls14), cost: usd(sumKey(series, "cost_usd")) })}</span>
            </summary>
            <div className="pp-charts">
              <Box>
                <div className="chart-head"><h3>{t("company.agent.runs_chart")}</h3>
                  <span className="muted small">{t("company.agent.last_14", { n: num(calls14) })}</span></div>
                <BarChart label={t("company.agent.runs_chart_label")} data={series} empty={t("company.agent.runs_empty")}
                  series={[{ key: "ok", label: t("company.ok"), color: "var(--ok)" }, { key: "error", label: t("company.failed"), color: "var(--bad)" }]} />
              </Box>
              <Box>
                <div className="chart-head"><h3>{t("company.agent.cost_chart")}</h3>
                  <span className="muted small">{t("company.agent.last_14_cost", { cost: usd(sumKey(series, "cost_usd")) })}</span></div>
                <BarChart label={t("company.agent.cost_chart_label")} data={series} fmt={(v) => `$${v.toFixed(2)}`} empty={t("company.agent.cost_empty")}
                  series={[{ key: "cost_usd", label: t("company.costs.spend"), color: "var(--accent)" }]} />
              </Box>
            </div>
            <RecentRuns runs={runs} agent={data.id} />
            <ByPurpose rows={byPurpose} />
          </details>
        </div>
        <Properties data={data} />
      </div>
    </div>
  );
}
