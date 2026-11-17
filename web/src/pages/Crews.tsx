import { Fragment, ReactNode, useEffect, useRef, useState } from "react";
import { Link, Navigate, useLocation, useSearchParams } from "react-router-dom";
import { Row } from "../api";
import { cleanName, useBusinessSelection, useCompanyLive, useNoBusiness } from "../business";
import { useWorker } from "../components/frame";
import { crewKey } from "../crews";
import { Icon } from "../components/icons";
import { Banner, Box, Chip, EmptyState, PageHead, Rows, Section, Status, Tone, Toolbar } from "../components/kit";
import { dur, Loading, RefLink, tid, Time, toneOf, usd, useShowTest } from "../components/ui";
import { t, tx } from "../i18n";
import { agentName, label } from "../labels";
import "./people.css";

/** `GET /crews` (backend staffing/views.py): one row per task and build, and why its team is the one it is. */
type Kind = "task" | "dev_job";
type Standing = "waiting" | "working" | "dissolved" | "cancelled_before_start" | "no_record";
type Shape = "solo" | "checked" | "chain";
type Duty = "author" | "verifier";
interface Slot { duty: Duty; role: string; model: string | null }
interface Said { rule: string; text: string; at: string | null }
interface Usage { role: string; model: string | null; calls: number; failed_calls: number }
interface Team { shape: Shape; after: number[]; alongside: number[]; slots: Slot[]; idle: Slot[]; why: Said[] }
interface Outcome {
  calls: number; failed_calls: number; cost_usd: number; busy_ms: number; wall_ms: number | null; verdict: string | null;
  reworks: number; fixed_path_calls: number | null; audit: { calls: number; cost_usd: number };
}
interface CrewView {
  subject: { kind: Kind; id: number; title: string; status: string; project_id: number; project: string };
  standing: Standing; team: Team | null; note: string | null; members: Usage[]; outcome: Outcome;
}

const STANDINGS: Standing[] = ["working", "waiting", "dissolved", "cancelled_before_start", "no_record"];
/** A job whose team is let go or never formed. A run of at least FOLD_AT of them in a row folds into one line. */
const ENDED: Standing[] = ["dissolved", "cancelled_before_start"];
const FOLD_AT = 3;
const EVERY_BUSINESS = "all";
const TABLES = ["tasks", "dev_jobs", "llm_calls", "crews", "crew_turns"];
const digits = (v: string | null) => (v && /^\d+$/.test(v) ? v : null);
const withParam = (params: URLSearchParams, key: string, value: string) => {
  const next = new URLSearchParams(params);
  next.set(key, value);
  return next;
};

const rowKey = (r: CrewView) => crewKey(r.subject.kind, r.subject.id);
const jobRef = (s: CrewView["subject"]) => (s.kind === "task" ? tid(s.id) : `J-${s.id}`);
const jobHref = (s: CrewView["subject"]) =>
  s.kind === "task" ? `/projects/${s.project_id}/tasks?task=${s.id}` : `/projects/${s.project_id}/dev?job=${s.id}`;

/**
 * Who worked each task and build and why (docs/design/staffing-structure.md section 9). With more than one business the
 * list is the business on screen, unless the address names another (`?business_id=`, or `all` for every business) or a
 * project (`?project_id=`, which wins); `?state=` keeps one standing; `?focus=` brings one job's row into view. Everything
 * a row says is the record's: the server writes the reasons and the notes, the ledger gives the members and the numbers.
 */
export default function Crews() {
  const [params, setParams] = useSearchParams();
  const [showTest] = useShowTest();
  const worker = useWorker();
  const noBusiness = useNoBusiness();
  const { businesses, current, error } = useBusinessSelection();
  const { data: projects } = useCompanyLive<Row[]>("/projects", ["projects"]);
  const several = (businesses?.length ?? 0) > 1;
  const projectRaw = digits(params.get("project_id"));
  const asked = params.get("business_id");
  const everyBusiness = asked === EVERY_BUSINESS;
  // the owner is in one business, so its jobs are what the screen opens on; the select still reaches every business
  const businessId = everyBusiness ? null : digits(asked) ?? (several && !projectRaw && current ? String(current.id) : null);
  const focus = params.get("focus");
  const state = STANDINGS.find((s) => s === params.get("state")) ?? null;
  const project = projectRaw ? (projects ?? []).find((p) => String(p.id) === projectRaw) : undefined;
  const badProject = !!params.get("project_id") && projects !== null && !project;
  const projectId = project ? String(project.id) : null;

  const query = new URLSearchParams();
  if (projectId) query.set("project_id", projectId);
  else if (businessId) query.set("business_id", businessId);
  if (state) query.set("state", state);
  // the list waits for what it is narrowed by: a project from the address, and the business the owner is in
  const waitForProject = !!projectRaw && projects === null;
  const waitForBusiness = businesses === null && !error;
  const crews = useCompanyLive<{ crews: CrewView[] }>(waitForProject || waitForBusiness ? null : `/crews${query.toString() ? `?${query}` : ""}`, TABLES);

  const set = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) (v ? next.set(k, v) : next.delete(k));
    setParams(next, { replace: true });
  };
  // what the owner narrowed it to; the business the screen opened on is not a narrowing the owner chose
  const filtered = !!(projectId || digits(asked) || state);
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const toggle = (key: string) => setOpen((was) => {
    const next = new Set(was);
    if (!next.delete(key)) next.add(key);
    return next;
  });
  const choices = (projects ?? []).filter((p) => (showTest || !p.is_test || String(p.id) === projectId)
    && (!businessId || String(p.business_id) === businessId || String(p.id) === projectId));

  const rows = crews.data?.crews ?? null;
  const working = rows?.filter((r) => r.standing === "working").length ?? 0;
  const noRecord = rows?.filter((r) => r.standing === "no_record").length ?? 0;
  const held = working > 0 ? worker.label : null;
  // with a state picked the list holds one standing, so the sentence counts what is shown instead of judging it
  const status: Status | null = !rows || (state && rows.length === 0) ? null : state ? {
    tone: "muted", text: t("company-people.crews.showing", { n: rows.length, state: t(`company-people.crews.state.${state}`) }),
  } : {
    tone: held ? "warn" : working ? "run" : "muted",
    text: <>{held ? t("company-people.crews.held", { n: working, state: held })
      : working ? t("company-people.crews.working", { n: working }) : t("company-people.crews.idle")}
      {noRecord > 0 && <> <Link to={`?${withParam(params, "state", "no_record")}`}>{t("company-people.crews.no_record", { n: noRecord })}</Link></>}</>,
  };
  const businessOf = new Map((projects ?? []).map((p) => [p.id as number, p.business_id as number | null]));
  const businessName = new Map((businesses ?? []).map((b) => [b.id, cleanName(b.name)]));
  const named = !businessId && !projectId && several;

  // `?focus=` (the link from a task or a build) brings its row into view once the list has it, and opens a fold around it
  const target = focus && rows ? rows.find((r) => rowKey(r) === focus) ?? null : null;
  const arrived = useRef<string | null>(null);
  useEffect(() => {
    if (!focus || !target || arrived.current === focus) return;
    const el = document.getElementById(`crew-${focus}`);
    if (!el) return;
    arrived.current = focus;
    const calm = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView({ block: "center", behavior: calm ? "auto" : "smooth" });
    el.focus({ preventScroll: true });
  }, [focus, target, rows, open]);
  const lost = !!focus && !!rows && !target;

  return (
    <div className="owner-page kui pp-crews">
      <PageHead title={t("shell.nav.crews")} status={status} sub={t("company-people.crews.sub")}
        actions={<Link className="btn-ghost" to="/agents?scope=company"><Icon name="users" />{t("company-people.crews.roster")}</Link>} />
      {noBusiness && (
        <Banner tone="info" icon="plus" action={<Link className="btn-ghost" to="/businesses/new">{t("common.firstrun.start")}</Link>}>
          {t("company-people.crews.no_business")}
        </Banner>
      )}
      {badProject && <p className="pp-note" role="status">{t("company-people.crews.bad_project")}</p>}
      {lost && (
        <Banner tone="info" icon="search" action={projectId || businessId || state ? (
          <button type="button" className="btn-ghost" onClick={() => set({ business_id: EVERY_BUSINESS, project_id: null, state: null })}>{t("company-people.crews.clear")}</button>
        ) : undefined}>{t("company-people.crews.bad_focus")}</Banner>
      )}

      <Toolbar label={t("company-people.crews.filters")}>
        {several && (
          <Filter name={t("company-people.crews.business")} value={businessId ?? (project?.business_id ? String(project.business_id) : EVERY_BUSINESS)}
            onChange={(v) => set({ business_id: v, project_id: null, focus: null })}
            options={[[EVERY_BUSINESS, t("company-people.crews.all_businesses")], ...(businesses ?? []).map((b): [string, string] => [String(b.id), cleanName(b.name)])]} />
        )}
        <Filter name={t("company-people.crews.project")} value={projectId ?? ""} onChange={(v) => set({ project_id: v, focus: null })}
          options={[["", t("company-people.crews.all_projects")], ...choices.map((p): [string, string] => [String(p.id), `P-${p.id} ${p.name}`])]} />
        <Filter name={t("company-people.crews.state")} value={state ?? ""} onChange={(v) => set({ state: v, focus: null })}
          options={[["", t("company-people.crews.any_state")], ...STANDINGS.map((s): [string, string] => [s, t(`company-people.crews.state.${s}`)])]} />
      </Toolbar>

      {!rows ? <Loading error={crews.error} retry={crews.reload} failure={crews.failure} />
        : rows.length === 0 ? (filtered
          ? <EmptyState kind="filtered" title={t("company-people.crews.filtered")}
              action={<button type="button" className="btn-ghost" onClick={() => set({ business_id: EVERY_BUSINESS, project_id: null, state: null, focus: null })}>{t("company-people.crews.clear")}</button>} />
          : <EmptyState title={t("company-people.crews.empty")}>{t("company-people.crews.empty_body")}</EmptyState>)
        : byProject(rows).map(([pid, name, jobs]) => (
          <Section key={pid} count={jobs.length} title={<>
            <Link to={`/projects/${pid}`}>{name}</Link>
            {named && businessName.get(businessOf.get(pid) ?? -1) && <span className="crew-biz">{businessName.get(businessOf.get(pid) ?? -1)}</span>}
          </>}>
            <Box pad={false}>
              <Rows className="crew-rows">
                {/* a state the owner picked is shown whole: folding would hide what was asked for */}
                {parts(jobs, state ? null : open, target ? rowKey(target) : null).map((part) => "fold" in part
                  ? <Fold key={`fold-${part.fold.key}`} run={part.fold} onToggle={() => toggle(part.fold.key)} />
                  : <Job key={rowKey(part.row)} row={part.row} target={rowKey(part.row) === focus} />)}
              </Rows>
            </Box>
          </Section>
        ))}
    </div>
  );
}

/** `/org` was the org chart; its address keeps working (`?project=` is a project, `?scope=business` the business on screen). */
export function FromOrg() {
  const { search } = useLocation();
  const { businesses, current, error } = useBusinessSelection();
  const old = new URLSearchParams(search);
  const scoped = old.get("scope") === "business";
  if (scoped && businesses === null && !error) return null;
  const next = new URLSearchParams();
  const project = digits(old.get("project"));
  if (project) next.set("project_id", project);
  else if (scoped && current) next.set("business_id", String(current.id));
  return <Navigate to={`/crews${next.toString() ? `?${next}` : ""}`} replace />;
}

function Filter({ name, value, options, onChange }: {
  name: string; value: string; options: [string, string][]; onChange: (v: string | null) => void;
}) {
  return (
    <label className="pp-filter">
      <span className="scope-l">{name}</span>
      <select value={value} onChange={(e) => onChange(e.target.value || null)}>
        {options.map(([v, text]) => <option key={v} value={v}>{text}</option>)}
      </select>
    </label>
  );
}

function byProject(rows: CrewView[]): [number, string, CrewView[]][] {
  const out = new Map<number, [number, string, CrewView[]]>();
  for (const r of rows) {
    const group = out.get(r.subject.project_id) ?? [r.subject.project_id, r.subject.project, []];
    group[2].push(r);
    out.set(r.subject.project_id, group);
  }
  return [...out.values()];
}

/** A run of finished jobs that sit next to each other in plan order, and whether the owner has opened it. */
interface Run { key: string; rows: CrewView[]; open: boolean; cost: number }
type Part = { row: CrewView } | { fold: Run };

/**
 * A project's rows in plan order, with each run of FOLD_AT or more finished jobs folded into one line, so what is
 * working or waiting stays in view. Folding keeps the order, since a row's "after T-11" reads against its neighbours.
 * `open` is null when nothing may fold; the row named by `keep` opens the run it sits in.
 */
function parts(rows: CrewView[], open: ReadonlySet<string> | null, keep: string | null): Part[] {
  const out: Part[] = [];
  let i = 0;
  while (i < rows.length) {
    let j = i;
    while (j < rows.length && ENDED.includes(rows[j].standing)) j++;
    const run = rows.slice(i, j);
    if (!open || run.length < FOLD_AT) {
      for (const row of run.length ? run : [rows[i]]) out.push({ row });
    } else {
      const key = rowKey(run[0]);
      const shown = open.has(key) || run.some((r) => rowKey(r) === keep);
      out.push({ fold: { key, rows: run, open: shown, cost: run.reduce((sum, r) => sum + r.outcome.cost_usd, 0) } });
      if (shown) for (const row of run) out.push({ row });
    }
    i = Math.max(j, i + 1);
  }
  return out;
}

function Fold({ run, onToggle }: { run: Run; onToggle: () => void }) {
  const n = run.rows.length;
  const text = t(run.open ? "company-people.crews.fold_close" : "company-people.crews.fold_open", { n, cost: usd(run.cost) });
  return (
    <li className="crew-fold">
      <button type="button" aria-expanded={run.open} onClick={onToggle}>
        <Icon name="chevron" size={14} />{text}
      </button>
    </li>
  );
}

function Job({ row, target }: { row: CrewView; target: boolean }) {
  const { subject, team, outcome } = row;
  const worker = useWorker();
  const held = row.standing === "working" ? worker.running(subject.status) : null;
  return (
    <li className={target ? "crew is-target" : "crew"} id={`crew-${rowKey(row)}`} tabIndex={-1} aria-current={target ? "location" : undefined}>
      <div className="crew-h">
        <h3 className="crew-t"><Link to={jobHref(subject)}><span className="mono">{jobRef(subject)}</span> {subject.title}</Link></h3>
        <AskWhy row={row} />
        <Chip tone={held ? "warn" : toneOf(subject.status) as Tone}>{held ?? label(subject.status)}</Chip>
      </div>
      <dl className="crew-d">
        <dt>{t("company-people.crews.line.team")}</dt>
        <dd>{team ? <TeamLine row={row} team={team} /> : <span className="pp-quiet">{row.note}</span>}</dd>
        {team && <>
          <dt>{t("company-people.crews.line.why")}</dt>
          <dd><Why team={team} /></dd>
        </>}
        {(team || outcome.calls > 0) && <>
          <dt>{t("company-people.crews.line.outcome")}</dt>
          <dd><OutcomeLine kind={subject.kind} o={outcome} /></dd>
        </>}
      </dl>
    </li>
  );
}

/** Where recall's "Ask {role} why" goes (design section 4.1, unit 12). It draws nothing until recall exists. */
function AskWhy(_: { row: CrewView }) {
  return null;
}

function TeamLine({ row, team }: { row: CrewView; team: Team }) {
  const pid = row.subject.project_id;
  const refs = (ids: number[]) => ids.map((id, i) => (
    <Fragment key={id}>{i > 0 && ", "}<RefLink code={tid(id)} projectId={pid} /></Fragment>
  ));
  const shape: ReactNode[] = [t(`company-people.crews.shape.${team.shape}`)];
  if (team.after.length) shape.push(tx("company-people.crews.after", { refs: refs(team.after) }));
  if (team.alongside.length) shape.push(tx("company-people.crews.alongside", { refs: refs(team.alongside) }));
  const duty = new Map(team.slots.map((s) => [s.role, s.duty]));
  const idle = new Set(team.idle.map((s) => s.role));
  return (
    <>
      <span className="crew-shape">{shape.map((x, i) => <Fragment key={i}>{i > 0 && " · "}{x}</Fragment>)}</span>
      <ul className="crew-members">
        {row.members.map((m) => (
          <li key={`${m.role}-${m.model ?? ""}`}>
            <b>{agentName(m.role)}</b>
            <span className="pp-quiet">{[duty.has(m.role) && t(`company-people.crews.duty.${duty.get(m.role)!}`), m.model,
              t("company-people.crews.calls", { n: m.calls }), m.failed_calls > 0 && t("company-people.crews.failed", { n: m.failed_calls })]
              .filter(Boolean).join(" · ")}</span>
          </li>
        ))}
        {team.slots.filter((s) => idle.has(s.role)).map((s) => (
          <li key={`idle-${s.role}`}>
            <b>{agentName(s.role)}</b>
            <span className="pp-quiet">{[t(`company-people.crews.duty.${s.duty}`), s.model, t("company-people.crews.idle_slot")].filter(Boolean).join(" · ")}</span>
          </li>
        ))}
      </ul>
    </>
  );
}

function Why({ team }: { team: Team }) {
  // a slot added later can overturn the first placement's sentence ("one role does it alone"), so the two are told apart
  const amended = team.why.some((w) => w.at);
  return (
    <ul className="crew-why">
      {team.why.map((w, i) => (
        <li key={`${w.rule}-${i}`}>
          {i === 0 && <span className="pp-quiet">{t(amended ? "company-people.crews.placed_first" : "company-people.crews.placed_by")} </span>}
          {w.at && <span className="pp-quiet">{tx("company-people.crews.added", { when: <Time v={w.at} /> })} </span>}
          {w.text}
        </li>
      ))}
    </ul>
  );
}

function OutcomeLine({ kind, o }: { kind: Kind; o: Outcome }) {
  const result = o.verdict === null ? t("company-people.crews.no_result")
    : kind === "dev_job" ? t(o.verdict === "accepted" ? "company-people.crews.accepted" : "company-people.crews.not_accepted")
    : o.verdict === "unchecked" ? t("company-people.crews.unchecked") : label(o.verdict);
  const parts = [
    result,
    t("company-people.crews.calls", { n: o.calls }) + (o.failed_calls ? ` (${t("company-people.crews.failed", { n: o.failed_calls })})` : ""),
    o.reworks > 0 && t(kind === "task" ? "company-people.crews.reworks" : "company-people.crews.fix_rounds", { n: o.reworks }),
    o.wall_ms !== null && t("company-people.crews.took", { d: dur(o.wall_ms) }),
    o.busy_ms > 0 && (o.wall_ms === null || dur(o.busy_ms) !== dur(o.wall_ms)) && t("company-people.crews.busy", { d: dur(o.busy_ms) }),
    usd(o.cost_usd),
  ].filter(Boolean);
  return (
    <>
      <span className="crew-num">{parts.join(" · ")}</span>
      {o.fixed_path_calls !== null && <span className="crew-aside pp-quiet">{t("company-people.crews.fixed_path", { n: o.fixed_path_calls })}</span>}
      {o.audit.calls > 0 && <span className="crew-aside pp-quiet">{t("company-people.crews.spot_check", { n: o.audit.calls, cost: usd(o.audit.cost_usd) })}</span>}
    </>
  );
}
