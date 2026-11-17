import { ReactNode, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, post, qs, Row } from "../../api";
import { useBusinessLive, useBusinessSelection, useNoBusiness } from "../../business";
import { ActivityList, isNoise, isOwnerEvent } from "../../components/activity";
import { Box, FirstRun, Section, Status } from "../../components/kit";
import { ago, Loading, Skeleton, Time, useShowTest } from "../../components/ui";
import { keys, locale, t, tx } from "../../i18n";
import { agentName, entityLabel, verbLabel } from "../../labels";
import { AuditFrame, Scope, ScopeSwitch, useAuditScope, useOlderPages, useScopeLive, useScopePath, currentParams } from "./frame";

/** Every kind of record the log can name, by its label. */
const ENTITIES = ["project", "task", "request", "decision", "meeting", "dev_job", "spec", "skill", "artifact", "company",
  "business", "cycle", "stage", "brief", "owner_task", "person", "bet", "idea", "action"];

/** Every kind of change that has a name, one per name, sorted by it (several CEO rule verbs share one name). */
function verbChoices(): string[] {
  const byName = new Map<string, string>();
  for (const k of keys("label.verb.")) {
    const v = k.slice("label.verb.".length);
    const name = verbLabel(v);
    if (name && !byName.has(name)) byName.set(name, v);
  }
  return [...byName.entries()].sort(([a], [b]) => a.localeCompare(b, locale())).map(([, v]) => v);
}

/** A quick period, turned into the `since` instant when the owner picks it. */
type Period = "visit" | "1h" | "today" | "yesterday" | "7d";
const PERIODS: Period[] = ["visit", "1h", "today", "yesterday", "7d"];
function periodStart(p: Period, visit: string | null): string | null {
  const now = new Date();
  const day = (offset: number) => new Date(now.getFullYear(), now.getMonth(), now.getDate() - offset).toISOString();
  switch (p) {
    case "visit": return visit;
    case "1h": return new Date(now.getTime() - 3600_000).toISOString();
    case "today": return day(0);
    case "yesterday": return day(1);
    case "7d": return day(7);
  }
}

/** `since` arrives as an ISO instant; the date input shows the owner's local day. */
const sinceDay = (v: string) => {
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return "";
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

/**
 * When the owner last opened this business's activity, kept on the server so the owner's two PCs share it. The time
 * read on arrival is the one this visit compares against; the server moves the stored time to now as the page opens.
 * This browser's own copy answers only when the server has none or can't be reached.
 */
function useLastVisit(businessId: number | null): string | null {
  const [prev, setPrev] = useState<{ id: number | null; at: string | null }>({ id: null, at: null });
  useEffect(() => {
    if (businessId == null) return;
    const store = `ship.audit.seen.${businessId}`;
    let alive = true;
    let local: string | null = null;
    try {
      local = localStorage.getItem(store);
      localStorage.setItem(store, new Date().toISOString());
    } catch {
      /* private mode: the server's time alone */
    }
    post<{ previous: string | null }>("/activity/visit", { business_id: businessId })
      .then((r) => r.previous ?? local, () => local)
      .then((at) => alive && setPrev({ id: businessId, at }));
    return () => {
      alive = false;
    };
  }, [businessId]);
  return prev.id === businessId ? prev.at : null;
}

const DAY = 24 * 3600 * 1000;

/** Activity in one sentence: what changed since the last visit (and how much of it is worth a look), else the last day. */
function activityStatus(events: Row[], more: boolean, filtered: boolean, visit: string | null): Status {
  const shown = events.filter((e) => !isNoise(e));
  if (filtered) {
    return { text: !shown.length ? t("company.activity.none_match")
      : more ? t("company.activity.matches_more", { n: shown.length }) : t("company.activity.matches", { n: shown.length }) };
  }
  if (!shown.length) return { text: t("company.activity.empty_status") };
  const last = shown[0];
  const idle = Date.now() - new Date(last.at).getTime() > 3600 * 1000;
  if (visit) {
    const fresh = shown.filter((e) => e.at > visit);
    const worth = fresh.filter(isOwnerEvent).length;
    const all = more && fresh.length === shown.length;
    if (!fresh.length) return { tone: "muted", text: tx("audit.activity.nothing_since", { visit: <Time v={visit} />, last: <Time v={last.at} /> }) };
    return {
      tone: worth ? "run" : "muted",
      text: <>{tx(all ? "audit.activity.since_many" : "audit.activity.since", { n: fresh.length, visit: <Time v={visit} /> })}
        {" "}{worth ? t("audit.activity.worth", { n: worth }) : t("audit.activity.worth_none")}</>,
    };
  }
  const day = shown.filter((e) => Date.now() - new Date(e.at).getTime() < DAY).length;
  return {
    tone: idle ? "muted" : "run",
    text: !day ? tx("company.activity.quiet", { last: <Time v={last.at} /> })
      : more && day === shown.length ? tx("company.activity.day_more", { n: day, last: <Time v={last.at} /> })
      : tx("company.activity.day", { n: day, last: <Time v={last.at} /> }),
  };
}

type Page = { events: Row[]; more: boolean; actors: string[] };

/** The audit trail, filterable; `projectId` scopes it to one project (the project page's tab keeps this export). */
export function ActivityLog({ projectId }: { projectId?: number }) {
  const [params, setParams] = useSearchParams();
  const [showTest] = useShowTest();
  const [pickedScope, setScope] = useAuditScope("business");
  const scope: Scope = projectId ? "business" : pickedScope;
  const { current, today } = useBusinessSelection();
  const visit = useLastVisit(projectId ? null : current?.id ?? null);
  const f = { project: params.get("project") ?? "", entity: params.get("entity") ?? "", actor: params.get("actor") ?? "",
    verb: params.get("verb") ?? "", since: params.get("since") ?? "", period: params.get("period") ?? "" };
  // opens on what is worth a look; "All" is the owner's switch (`?view=all`)
  const onlyWorth = params.get("view") !== "all";
  const query = { project_id: projectId ?? (f.project || undefined), entity: f.entity, actor: f.actor, verb: f.verb,
    since: f.since, include_test: showTest };
  const path = `/activity${qs({ ...query, limit: 100 })}`;
  const noBusiness = useNoBusiness();
  const live = useScopeLive<Page>(scope, path, ["activity_events"]);
  const scopePath = useScopePath(scope);
  const { data: projects } = useBusinessLive<Row[]>(projectId || scope === "company" ? null : "/projects", ["projects"]);
  const pages = useOlderPages(`${scope}|${path}`, live.data?.events ?? null, !!live.data?.more);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  // "From a date…" chosen but no date typed yet: the date box shows without filtering anything.
  const [picking, setPicking] = useState(false);
  const set = (changes: Record<string, string>) => {
    const next = currentParams();
    for (const [k, v] of Object.entries(changes)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    setParams(next, { replace: true });
  };
  const clear = () => { setPicking(false); set({ project: "", entity: "", actor: "", verb: "", since: "", period: "" }); };
  async function loadMore() {
    const p = scopePath(`/activity${qs({ ...query, limit: 100, before: pages.rows[pages.rows.length - 1]?.id })}`);
    if (!p) return;
    try {
      const r = await api<Page>(p);
      pages.append(r.events, r.more);
      setLoadErr(null);
    } catch (e: any) {
      setLoadErr(e.message ?? String(e));
    }
  }

  const data = live.data;
  const filtered = !!(f.project || f.entity || f.actor || f.verb || f.since);
  const events = pages.rows;
  const listed = onlyWorth ? events.filter(isOwnerEvent) : events;
  // with a filter the sentence counts the rows the owner sees; without one it counts every change and says how many
  // of them are worth a look, which is what the list opens on
  const status: Status | undefined = !data
    ? (live.error ? { tone: "bad", text: t("audit.activity.load_failed") } : { text: t("company.activity.loading") })
    : activityStatus(filtered ? listed : events, pages.more, filtered, scope === "business" ? visit : null);
  const page = (node: ReactNode) => projectId ? node : (
    <AuditFrame title={t("company.activity.title")} status={status} sub={t("audit.activity.sub")}
      actions={<ScopeSwitch value={scope} onChange={setScope} />}>{node}</AuditFrame>
  );
  if (noBusiness && scope === "business") return page(<FirstRun>{t("company.activity.firstrun")}</FirstRun>);

  const shownProjects = (projects ?? []).filter((p) => showTest || !String(p.name ?? "").startsWith("[TEST]"));
  const pickPeriod = (p: Period | "") => {
    if (!p) return set({ since: "", period: "" });
    set({ since: periodStart(p, visit) ?? "", period: p });
  };
  const period: Period | "" | "date" = (PERIODS as string[]).includes(f.period) ? (f.period as Period) : f.since ? "date" : "";
  const yesterday = !projectId && scope === "business" && !filtered ? today?.yesterday ?? [] : [];
  const body = (
    <>
      <div className="toolbar au-feed-tools" role="group" aria-label={t("audit.activity.filters")}>
        <div className="seg" role="group" aria-label={t("audit.activity.view")}>
          <button type="button" className={onlyWorth ? "on" : ""} aria-pressed={onlyWorth} onClick={() => set({ view: "" })}>{t("audit.activity.view_worth")}</button>
          <button type="button" className={!onlyWorth ? "on" : ""} aria-pressed={!onlyWorth} onClick={() => set({ view: "all" })}>{t("audit.activity.view_all")}</button>
        </div>
        {!projectId && scope === "business" && (
          <>
            <label className="sr-only" htmlFor="a-proj">{t("company.activity.project")}</label>
            <select id="a-proj" value={f.project} onChange={(e) => set({ project: e.target.value })}>
              <option value="">{t("company.activity.all_projects")}</option>
              {shownProjects.map((p) => <option key={p.id} value={p.id}>{String(p.name ?? "").replace(/^\[TEST\]\s*/, "")}</option>)}
            </select>
          </>
        )}
        <label className="sr-only" htmlFor="a-ent">{t("company.activity.entity")}</label>
        <select id="a-ent" value={f.entity} onChange={(e) => set({ entity: e.target.value })}>
          <option value="">{t("company.activity.all_entities")}</option>
          {[...ENTITIES].sort((a, b) => entityLabel(a).localeCompare(entityLabel(b), locale()))
            .map((x) => <option key={x} value={x}>{entityLabel(x)}</option>)}
        </select>
        <label className="sr-only" htmlFor="a-verb">{t("company.activity.verb")}</label>
        <select id="a-verb" value={f.verb} onChange={(e) => set({ verb: e.target.value })}>
          <option value="">{t("company.activity.all_verbs")}</option>
          {verbChoices().map((x) => <option key={x} value={x}>{verbLabel(x)}</option>)}
        </select>
        <label className="sr-only" htmlFor="a-actor">{t("audit.activity.actor_label")}</label>
        <select id="a-actor" value={f.actor} title={t("audit.activity.actor_label")} onChange={(e) => set({ actor: e.target.value })}>
          <option value="">{t("audit.activity.anyone")}</option>
          {(data?.actors ?? []).map((x) => <option key={x} value={x}>{agentName(x)}</option>)}
        </select>
        <label className="sr-only" htmlFor="a-period">{t("company.activity.since_label")}</label>
        <select id="a-period" value={period === "date" || picking ? "date" : period}
          onChange={(e) => { const v = e.target.value; setPicking(v === "date"); if (v !== "date") pickPeriod(v as Period | ""); }}>
          <option value="">{t("audit.activity.period.any")}</option>
          {PERIODS.map((p) => (
            <option key={p} value={p} disabled={p === "visit" && !visit}>
              {p === "visit" && visit ? t("audit.activity.period.visit_at", { at: ago(visit) }) : t(`audit.activity.period.${p}` as const)}
            </option>
          ))}
          <option value="date">{t("audit.activity.period.pick")}</option>
        </select>
        {(period === "date" || picking) && (
          <input type="date" lang={locale()} value={f.since ? sinceDay(f.since) : ""} aria-label={t("audit.activity.period.date")} autoFocus={picking}
            onChange={(e) => set({ since: e.target.value ? new Date(`${e.target.value}T00:00`).toISOString() : "", period: "" })} />
        )}
        {filtered && <button type="button" className="link small" onClick={clear}>{t("company.clear_filters")}</button>}
        <div className="toolbar-r">
          <a className="au-csv small" href={`/api${scopePath(`/activity.csv${qs({ ...query })}`) ?? "/activity.csv"}`} download
            title={t("audit.activity.csv_hint")}>{t("company.activity.csv")}</a>
        </div>
      </div>
      {/* under the filters, so the toolbar sits right under the tabs as on the other record screens */}
      {yesterday.length > 0 && (
        <section className="au-yesterday" aria-label={t("audit.activity.yesterday")}>
          <h2>{t("audit.activity.yesterday")}</h2>
          <ul>{yesterday.map((line, i) => <li key={i}>{line}</li>)}</ul>
        </section>
      )}
      {pages.fresh > 0 && <button type="button" className="au-fresh" onClick={pages.showFresh}>{t("audit.activity.fresh", { n: pages.fresh })}</button>}
      <a className="au-skip" href="#activity-end">{t("audit.activity.skip")}</a>
      <Box className="au-feed" pad={false}>
        {!data ? (live.error ? <div className="au-pad"><Loading error={live.error} failure={live.failure} retry={live.reload} /></div> : <Skeleton rows={6} />) : (
          <>
            <ActivityList events={listed} grouped dayLevel={projectId ? 3 : 2} since={scope === "business" && !projectId ? visit : null}
              showProject={!projectId && !f.project} showBusiness={scope === "company" && !projectId}
              empty={onlyWorth && events.length ? t("audit.activity.none_worth") : t(filtered ? "company.activity.none_match" : "company.activity.none_yet")} />
            <div className="au-more-row" id="activity-end">
              <span className="small muted num">{t(onlyWorth ? "audit.activity.loaded_worth" : "audit.activity.loaded", { n: listed.length, all: events.length })}</span>
              {pages.more && <button type="button" className="btn-ghost small" onClick={loadMore}>{t("company.activity.older")}</button>}
              {loadErr && <span className="small bad" role="alert">{t("company.load_more_failed", { error: loadErr })}</span>}
            </div>
          </>
        )}
      </Box>
    </>
  );
  return page(projectId ? <Section title={t("company.activity.project_section")} className="au-page">{body}</Section> : body);
}
