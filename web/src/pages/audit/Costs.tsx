import { FormEvent, MouseEvent, ReactNode, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { post, qs, Row, TZ, useLive } from "../../api";
import { cleanName, useBusinessSelection } from "../../business";
import { BarChart, ShareBar } from "../../components/charts";
import { Box, FirstRun, Meter, Section, Status } from "../../components/kit";
import { ago, ConfirmBox, landOn, Loading, num, Skeleton, usd, useShowTest } from "../../components/ui";
import { t, tx } from "../../i18n";
import { agentName, purposeLabel } from "../../labels";
import { projectWord } from "../../components/project";
import { useWorker } from "../../components/frame";
import { AuditFrame, ScopeLabel, currentParams } from "./frame";

const PERIODS = ["7", "14", "30", "90"] as const;

/** A link to the month's limit lands in its field, not only near it. */
function toLimit(e: MouseEvent) {
  e.preventDefault();
  landOn("m-limit", "center");
}
type Period = (typeof PERIODS)[number];

/** Lowering the month's limit below half of what it is now asks first; raising it never does. */
const BIG_CUT = 0.5;

function MonthLimit({ month, fallback, onChange }: { month: Row | undefined; fallback: number; onChange: () => void }) {
  const limit = month?.limit_usd ?? fallback;
  const spent = (month?.spent_usd ?? 0) + (month?.reserved_usd ?? 0);
  const [value, setValue] = useState(String(limit));
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState(false);
  const result = useRef<HTMLParagraphElement>(null);
  useEffect(() => setValue(String(limit)), [limit]);
  useEffect(() => {
    if (msg) result.current?.focus();
  }, [msg]);
  const n = Number(value);
  const invalid = !Number.isFinite(n) || n <= spent;
  async function save() {
    setBusy(true);
    try {
      await post("/budget/month", { limit_usd: n });
      setMsg({ ok: true, text: t("company.limit.saved") });
      setAsking(false);
      onChange();
    } catch (x: any) {
      setMsg({ ok: false, text: x.message });
    } finally {
      setBusy(false);
    }
  }
  function submit(e: FormEvent) {
    e.preventDefault();
    setMsg(null);
    if (n < limit * BIG_CUT) setAsking(true);
    else void save();
  }
  return (
    <form className="au-limit" onSubmit={submit}>
      <div className="au-limit-row">
        <label htmlFor="m-limit" className="au-strong">{t("company.limit.label")}</label>
        <input id="m-limit" type="number" min={0} step="any" inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)}
          aria-invalid={invalid} aria-describedby="m-limit-help" />
        <button type="submit" className={n === limit ? "btn-ghost" : "btn-primary"} disabled={busy || invalid || n === limit || asking}
          aria-describedby={n === limit ? "m-limit-same" : undefined}>{t(busy ? "company.limit.saving" : "company.limit.save")}</button>
        {n === limit && !busy && <span id="m-limit-same" className="small muted">{t("company.limit.same")}</span>}
      </div>
      {invalid && value !== "" && <p className="small bad" role="alert">{t("company.limit.too_low", { spent: usd(spent) })}</p>}
      {asking && (
        <ConfirmBox busy={busy} error={null} confirmLabel={t("audit.limit.cut_confirm", { limit: usd(n) })} cancelLabel={t("audit.limit.cut_keep", { limit: usd(limit) })}
          onConfirm={() => void save()} onCancel={() => setAsking(false)}>
          {t("audit.limit.cut_what", { limit: usd(n), now: usd(limit), left: usd(Math.max(0, n - spent)) })}
        </ConfirmBox>
      )}
      {msg && <p ref={result} tabIndex={-1} className={`small ${msg.ok ? "au-ok" : "bad"}`} role={msg.ok ? "status" : "alert"}>{msg.text}</p>}
      <p id="m-limit-help" className="small muted">{t("company.limit.help", { fallback: usd(fallback) })}</p>
    </form>
  );
}

/** One usage window (today, this week) in the limits box: used, the cap, the share, and when it starts over. */
function WindowFact({ name, w }: { name: string; w: Row | undefined }) {
  const cap = w?.cap_usd_eq;
  return (
    <div className="au-limit-fact">
      <dt>{name}</dt>
      <dd>
        {cap ? <>
          <span className="num">{t("audit.costs.window_used", { used: usd(w?.used_usd_eq), cap: usd(cap), pct: Math.round(Number(w?.used_pct ?? 0)) })}</span>
          <Meter value={Number(w?.used_usd_eq ?? 0)} max={cap} label={name} />
          <span className="small muted">{t("audit.costs.resets", { at: ago(w?.resets_at) })}</span>
        </> : <span className="muted">{t("audit.costs.no_cap")}</span>}
      </dd>
    </div>
  );
}

/** A window close to its cap, said in one sentence; the link to change it lives in the limits box. */
function windowNote(key: "audit.costs.near_day" | "audit.costs.near_week", w: Row | undefined): string | null {
  const pct = Math.round(Number(w?.used_pct ?? 0));
  if (!w?.cap_usd_eq || pct < 70) return null;
  return t(key, { pct, resets: ago(w.resets_at) });
}

export function Costs() {
  const [params, setParams] = useSearchParams();
  const raw = params.get("days");
  const days: Period = (PERIODS as readonly string[]).includes(raw ?? "") ? (raw as Period) : "30";
  const { data, error, failure, reload } = useLive<Row>(`/costs${qs({ tz: TZ, days })}`, ["activity_events", "projects"]);
  const { data: settings } = useLive<Row>("/settings", ["app_settings"]);
  const held = useWorker().label;
  const [showTest] = useShowTest();
  const { businesses } = useBusinessSelection();
  const setDays = (k: Period) => {
    const next = currentParams();
    if (k === "30") next.delete("days");
    else next.set("days", k);
    setParams(next, { replace: true });
  };
  const frame = (children: ReactNode, status: Status, near = false) => (
    <AuditFrame title={t("company.costs.title")} status={status} sub={t("company.costs.sub")}
      actions={<>{near && <a className="btn-primary" href="#month-limit" onClick={toLimit}>{t("company.costs.change_limit")}</a>}{businesses && <ScopeLabel>{t("audit.costs.scope", { n: businesses.length })}</ScopeLabel>}</>}>
      {children}
    </AuditFrame>
  );
  if (!data) {
    return frame(error ? <Loading error={error} failure={failure} retry={reload} /> : <Skeleton rows={5} />,
      error ? { tone: "bad", text: t("audit.costs.load_failed") } : { text: t("company.costs.loading") });
  }

  const thisMonth = new Date().toISOString().slice(0, 7);
  const months: Row[] = data.months ?? [];
  const month = months.find((m) => m.month === thisMonth);
  const limit = month?.limit_usd ?? data.default_month_limit;
  const spent = month?.spent_usd ?? 0;
  const reserved = month?.reserved_usd ?? 0;
  const byDay: Row[] = data.by_day ?? [];
  const total = byDay.reduce((a, d) => a + Number(d.cost_usd ?? 0), 0);
  const calls = byDay.reduce((a, d) => a + Number(d.calls ?? 0), 0);
  const errors = byDay.reduce((a, d) => a + Number(d.errors ?? 0), 0);
  const projects: Row[] = (data.by_project ?? []).filter((p: Row) => showTest || !p.is_test);
  const agents: Row[] = [...(data.by_agent ?? [])].sort((a, b) => b.cost_usd - a.cost_usd);
  const maxAgent = Math.max(0, ...agents.map((a) => a.cost_usd));
  const models: Row[] = [...(data.by_model ?? [])].sort((a, b) => b.cost_usd - a.cost_usd);
  const purposes: Row[] = [...(data.by_purpose ?? [])].sort((a, b) => b.cost_usd - a.cost_usd);
  const maxPurpose = Math.max(0, ...purposes.map((p) => p.cost_usd));
  const usedPct = limit ? (spent + reserved) / limit : 0;
  const near = usedPct >= 0.7;
  const stopped = projects.filter((p) => p.project_id && p.budget_short);
  const usage = data.usage ?? {};
  const capacity = Number(settings?.effective?.usage?.capacity_week_usd_eq ?? 0) || null;
  const weekUsed = Number(usage.week?.used_usd_eq ?? 0);
  const dayUsed = Number(usage.day?.used_usd_eq ?? 0);
  const windows = [windowNote("audit.costs.near_day", usage.day), windowNote("audit.costs.near_week", usage.week)].filter(Boolean) as string[];
  // The headline leads with what this month's calls cost at list price, the number Agents, the stage card and the
  // by-project table add up; the budget's own charge is named apart when the two differ.
  const used = Number(data.month_used_usd ?? spent);
  const left = usd(Math.max(0, limit - spent - reserved));
  const monthLine = Math.abs(used - spent) > 0.005
    ? <>{tx("company.costs.used", { used: <b>{usd(used)}</b> })}{" "}{t("company.costs.charged", { spent: usd(spent), limit: usd(limit), left })}</>
    : tx("company.costs.spent", { spent: <b>{usd(spent)}</b>, limit: usd(limit), left });
  const status: Status = {
    tone: near || windows.length || stopped.length ? "warn" : "ok",
    text: <>
      {capacity ? <>{tx("audit.costs.share", { week: <b>{Math.round((weekUsed / capacity) * 100)}%</b>, day: Math.round((dayUsed / capacity) * 100),
        usd: usd(weekUsed) })}{" "}{monthLine}</> : monthLine}
      {near ? t("company.costs.near", { pct: Math.round(usedPct * 100) }) : !stopped.length && !windows.length ? t("company.costs.fine") : null}
      {stopped.length ? tx("company.costs.project_stopped", { n: stopped.length,
        link: <Link to={`/projects/${stopped[0].project_id}/settings#budget`}>{cleanName(String(stopped[0].name ?? ""))}</Link> }) : null}
      {windows.map((w) => ` ${w}`).join("")}
      {errors ? <>{" "}{t("audit.costs.errors", { days, calls: num(calls), n: num(errors) })}{" "}
        <Link to="/audit/runs?status=error&scope=company">{t("audit.costs.errors_link")}</Link></> : null}
    </>,
  };

  if (businesses?.length === 0 && calls === 0 && spent === 0) return frame(<FirstRun>{t("company.costs.first_run")}</FirstRun>, status);

  const diff = projects.some((p) => p.budget_usd != null && Math.abs(Number(p.spent_usd ?? 0) - Number(p.cost_usd ?? 0)) > 0.005);
  return frame(
    <>
      <Section title={t("audit.costs.limits")}>
        <Box className="au-limits">
          <dl className="au-limit-facts">
            <WindowFact name={t("audit.costs.day_cap")} w={usage.day} />
            <WindowFact name={t("audit.costs.week_cap")} w={usage.week} />
            <div className="au-limit-fact">
              <dt>{t("audit.costs.month_budget")}</dt>
              <dd>
                <span className="num">{t("audit.costs.month_used", { used: usd(spent + reserved), limit: usd(limit) })}</span>
                <Meter value={spent + reserved} max={limit} label={t("audit.costs.month_budget")} />
                <span className="small muted">{month?.limit_usd != null ? t("audit.costs.month_set") : t("audit.costs.month_default")}</span>
              </dd>
            </div>
          </dl>
          <p className="au-limit-rule">{t("audit.costs.rule")}</p>
          <p className="au-limit-links small">
            <Link to="/settings#set-usage">{t("audit.costs.to_settings")}</Link>
            <a href="#month-limit" onClick={toLimit}>{t("audit.costs.to_month")}</a>
            {!capacity && <span className="muted">{t("audit.costs.no_capacity")}</span>}
          </p>
        </Box>
      </Section>

      <Section title={t("company.costs.daily")} actions={<>
        <span className="small muted num">{t("audit.costs.this_week", { usd: usd(weekUsed) })}{" · "}
          {Math.abs(total - spent) > 0.005 ? t("audit.costs.range_vs_budget", { n: days, total: usd(total), spent: usd(spent) }) : t("audit.costs.range_total", { n: days, total: usd(total) })}</span>
        <div className="seg" role="group" aria-label={t("company.costs.period")}>
          {PERIODS.map((p) => <button key={p} type="button" className={days === p ? "on" : ""} aria-pressed={days === p} onClick={() => setDays(p)}>{t("company.costs.days", { n: p })}</button>)}
        </div>
      </>}>
        <Box>
          <BarChart label={t("company.costs.daily")} data={byDay} fmt={usd} axisFmt={(v) => `$${v < 10 ? v.toFixed(1) : Math.round(v)}`}
            empty={t("company.costs.daily_empty", { n: days })}
            series={[{ key: "cost_usd", label: t("company.costs.spend"), color: "var(--accent)" }]} />
        </Box>
      </Section>

      <div className="au-cols">
        <Section title={t("company.costs.by_project")} count={projects.length} actions={<span className="small muted">{t("company.costs.by_project_note")}</span>}>
          <Box pad={false} className="au-list">
            {projects.length === 0 ? <p className="au-pad muted">{t("company.no_records")}</p> : (
              <div className="au-scroll">
                <table className="tbl au-tbl">
                  <thead><tr><th scope="col">{t("company.activity.project")}</th><th scope="col">{t("audit.runs.business")}</th>
                    <th scope="col">{t("company.costs.budget_used")}</th>
                    <th scope="col" className="r">{t("company.costs.runs")}</th><th scope="col" className="r">{t("company.costs.spend")}</th></tr></thead>
                  <tbody>
                    {projects.map((p) => (
                      <tr key={p.project_id ?? "none"}>
                        <td className="au-proj">
                          {p.project_id
                            ? <Link to={`/projects/${p.project_id}/settings#budget`} title={p.name}>{cleanName(String(p.name ?? ""))}</Link>
                            : <span className="muted">{t("company.costs.outside")}</span>}
                          {p.owner_state && (p.owner_state !== "running" || (held && p.needs_worker)) && (
                            <span className={`tag au-tag-gap t-${projectWord(p, held).tone}`}>{projectWord(p, held).word}</span>
                          )}
                        </td>
                        <td className="au-for">{p.business ? cleanName(String(p.business)) : <span className="muted">{p.project_id ? "-" : t("common.whole_company")}</span>}</td>
                        <td className="au-budget">
                          {p.budget_usd != null ? (
                            <>
                              <span className="small num">{usd(p.spent_usd)} / {usd(p.budget_usd)}</span>
                              <Meter value={p.spent_usd ?? 0} max={p.budget_usd} label={t("company.costs.budget_meter", { name: String(p.name ?? "") })} />
                            </>
                          ) : <span className="small muted">{t("company.costs.no_budget")}</span>}
                        </td>
                        <td className="r num">{num(p.calls)}{p.errors > 0 && <span className="bad small">{t("company.failures", { n: p.errors })}</span>}</td>
                        <td className="r num">{usd(p.cost_usd)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {diff && <p className="box-foot small muted">{t("audit.costs.budget_vs_spend")}</p>}
          </Box>
        </Section>
        <Section title={t("company.costs.by_agent")} count={agents.length} actions={<span className="small muted">{t("company.cumulative")}</span>}>
          <Box pad={false} className="au-list">
            {agents.length === 0 ? <p className="au-pad muted">{t("company.no_records")}</p> : (
              <table className="tbl au-tbl">
                <thead><tr><th scope="col">{t("company.runs.agent")}</th><th scope="col" className="r">{t("company.costs.runs")}</th>
                  <th scope="col" className="r">{t("company.costs.spend")}</th>
                  {agents.length > 1 && <th scope="col"><span className="sr-only">{t("company.costs.share")}</span></th>}</tr></thead>
                <tbody>
                  {agents.map((a) => (
                    <tr key={`${a.agent}-${a.provider}`}>
                      <td><Link className="au-quiet-strong" to={`/agents/${a.agent}`}>{agentName(a.agent)}</Link></td>
                      <td className="r num">{num(a.calls)}{a.errors > 0 && <span className="bad small">{t("company.failures", { n: a.errors })}</span>}</td>
                      <td className="r num">{usd(a.cost_usd)}</td>
                      {agents.length > 1 && <td className="au-share"><ShareBar value={a.cost_usd} max={maxAgent} label={`${agentName(a.agent)} ${usd(a.cost_usd)}`} /></td>}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Box>
        </Section>
      </div>

      <div className="au-cols">
        <Section title={t("company.costs.by_model")} actions={<span className="small muted">{t("company.cumulative")}</span>}>
          <Box pad={false} className="au-list">
            {models.length === 0 ? <p className="au-pad muted">{t("company.no_records")}</p> : (
              <table className="tbl au-tbl">
                <thead><tr><th scope="col">{t("company.runs.model")}</th><th scope="col" className="r">{t("company.costs.runs")}</th>
                  <th scope="col" className="r">{t("company.costs.model_tokens")}</th><th scope="col" className="r">{t("company.costs.spend")}</th></tr></thead>
                <tbody>
                  {models.map((m) => (
                    <tr key={`${m.provider}-${m.model}`}>
                      <td><span className="au-model">{m.model || m.provider}</span></td>
                      <td className="r num">{num(m.calls)}</td><td className="r small num">{num(m.input_tokens)} / {num(m.output_tokens)}</td><td className="r num">{usd(m.cost_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Box>
        </Section>
        <Section title={t("company.costs.by_purpose")} actions={<span className="small muted">{t("company.cumulative")}</span>}>
          <Box pad={false} className="au-list">
            {purposes.length === 0 ? <p className="au-pad muted">{t("company.no_records")}</p> : (
              <table className="tbl au-tbl">
                <thead><tr><th scope="col">{t("company.runs.purpose")}</th><th scope="col" className="r">{t("company.costs.runs")}</th>
                  <th scope="col" className="r">{t("company.costs.spend")}</th>
                  {purposes.length > 1 && <th scope="col"><span className="sr-only">{t("company.costs.share")}</span></th>}</tr></thead>
                <tbody>
                  {purposes.map((m) => (
                    <tr key={m.purpose}>
                      <td>{purposeLabel(m.purpose)}</td><td className="r num">{num(m.calls)}{m.errors > 0 && <span className="bad small">{t("company.failures", { n: m.errors })}</span>}</td>
                      <td className="r num">{usd(m.cost_usd)}</td>
                      {purposes.length > 1 && <td className="au-share"><ShareBar value={m.cost_usd} max={maxPurpose} label={`${purposeLabel(m.purpose)} ${usd(m.cost_usd)}`} /></td>}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Box>
        </Section>
      </div>

      <div className="au-cols au-cols-main" id="month-limit">
        <Section title={t("company.costs.month_budget")}>
          <Box><MonthLimit month={month} fallback={data.default_month_limit} onChange={reload} /></Box>
        </Section>
        <Section title={t("company.costs.by_month")} count={months.length}>
          <Box pad={false} className="au-list">
            {months.length === 0 ? <p className="au-pad muted">{t("company.costs.no_months")}</p> : (
              <table className="tbl au-tbl">
                <thead><tr><th scope="col">{t("company.costs.month")}</th><th scope="col" className="r">{t("company.costs.month_used")}</th>
                  <th scope="col" className="r">{t("company.costs.month_limit")}</th>
                  <th scope="col"><span className="sr-only">{t("company.costs.against_limit")}</span></th></tr></thead>
                <tbody>
                  {months.map((m) => (
                    <tr key={m.month}>
                      <td className="num">{m.month}</td><td className="r num">{usd(m.spent_usd)}</td><td className="r num">{usd(m.limit_usd)}</td>
                      <td className="au-share"><Meter value={m.spent_usd ?? 0} max={m.limit_usd ?? 0} label={t("company.costs.month_meter", { month: m.month })} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Box>
        </Section>
      </div>
    </>,
    status,
    near,
  );
}
