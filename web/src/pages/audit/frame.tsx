import { ReactNode, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Row } from "../../api";
import { cleanName, useBusinessLive, useBusinessSelection, useCompanyLive, useScoped } from "../../business";
import { PageHead, RouteTabs, Scope, Status } from "../../components/kit";
import { t } from "../../i18n";
import "../audit.css";

/*
 * What the four record screens (activity, model calls, background work, costs) share: the head, the record tabs in
 * sidebar order, the scope the list reads (one business or the whole company), and the "show older" pages that a live
 * update never throws away.
 */

/** The address's query as it is now. A setter builds on this, not on the render's `params`: the router applies a
 * navigation in a transition, so two quick changes (a view toggle, then a period) would otherwise drop the first. */
export const currentParams = () => new URLSearchParams(window.location.search);

/** Whose records a list shows. Each screen keeps the default it always had; the switch says which one is on. */
export type { Scope } from "../../components/kit";
export { ScopeLabel, ScopeSwitch } from "../../components/kit";

export function useAuditScope(fallback: Scope): [Scope, (s: Scope) => void] {
  const [params, setParams] = useSearchParams();
  const raw = params.get("scope");
  const scope: Scope = raw === "company" || raw === "business" ? raw : fallback;
  const set = (s: Scope) => {
    const next = currentParams();
    if (s === fallback) next.delete("scope");
    else next.set("scope", s);
    setParams(next, { replace: true });
  };
  return [scope, set];
}

/** A live list in the chosen scope: the selected business's rows (and the company's own), or every business's. */
export function useScopeLive<T>(scope: Scope, path: string | null, tables: string[]) {
  const one = useBusinessLive<T>(scope === "business" ? path : null, tables);
  const all = useCompanyLive<T>(scope === "company" ? path : null, tables);
  return scope === "business" ? one : all;
}

/** The same scope for a one-off request (the next page of a list). */
export function useScopePath(scope: Scope): (path: string) => string | null {
  const scoped = useScoped();
  return scope === "business" ? scoped : (path) => path;
}


const TABS = [
  { to: "/audit", key: "company.audit.tab.activity", end: true },
  { to: "/audit/runs", key: "company.audit.tab.runs", end: false },
  { to: "/audit/queue", key: "company.audit.tab.queue", end: true },
  { to: "/audit/costs", key: "company.audit.tab.costs", end: true },
] as const;

/** Head, record tabs (in the sidebar's order), then the screen. Every record screen opens with this. */
export function AuditFrame({ title, status, sub, actions, children }: {
  title: ReactNode; status?: Status | null; sub?: ReactNode; actions?: ReactNode; children: ReactNode;
}) {
  return (
    <div className="owner-page au-page">
      <PageHead title={title} status={status} sub={sub} actions={actions} />
      <RouteTabs label={t("company.audit.tabs")} items={TABS.map((x) => ({ to: x.to, label: t(x.key), end: x.end }))} />
      {children}
    </div>
  );
}

/**
 * The first page of a live list and the older pages the owner asked for. Older pages are dropped only when the list's
 * filters change (`key`). While older pages are open, a new first page does not reshuffle what is on screen: the new
 * rows are counted (`fresh`) and join the top when the owner asks (`showFresh`).
 */
export function useOlderPages<T extends Row>(key: string, first: T[] | null, firstMore: boolean) {
  type S = { key: string; older: T[]; pinned: T[] | null; more: boolean | null };
  const [state, setState] = useState<S>({ key, older: [], pinned: null, more: null });
  let s = state;
  if (s.key !== key) {
    s = { key, older: [], pinned: null, more: null };
    setState(s);
  }
  const head = s.pinned ?? first ?? [];
  const seen = new Set(head.map((r) => r.id));
  const rows = [...head, ...s.older.filter((r) => !seen.has(r.id))];
  const shown = new Set(rows.map((r) => r.id));
  const fresh = s.pinned && first ? first.filter((r) => !shown.has(r.id)).length : 0;
  return {
    rows,
    more: s.more ?? firstMore,
    fresh,
    /** The next page arrived: keep the first page as it is on screen and add the page under it. */
    append: (page: T[], more: boolean) => setState((p) => ({ ...p, pinned: p.pinned ?? first, older: [...p.older, ...page], more })),
    /** Put the new rows on top, keeping every row already on screen. */
    showFresh: () => setState((p) => {
      const top = first ?? [];
      const ids = new Set(top.map((r) => r.id));
      return { ...p, pinned: [...top, ...(p.pinned ?? []).filter((r) => !ids.has(r.id))] };
    }),
  };
}
