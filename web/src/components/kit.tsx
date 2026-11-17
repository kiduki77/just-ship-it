import { ReactNode, useEffect, useRef, useState } from "react";
import { Link, NavLink } from "react-router-dom";
import { cleanName, useBusinessSelection } from "../business";
import { t } from "../i18n";
import { Fix } from "./fix";
import { useWorker, WORKER_FIX } from "./frame";
import { Icon } from "./icons";
import { PanelLink, PanelTarget } from "./panel";

/*
 * The console's shared building blocks: page header, metric tiles, sections, status board, view and tab switches,
 * banners and meters. Pages compose these instead of inventing their own containers, so every screen reads alike.
 */

export type Tone = "ok" | "run" | "warn" | "bad" | "info" | "muted";

/** Whose records a screen counts: the business on screen, or every business together. */
export type Scope = "business" | "company";

/**
 * "Showing: LogLens | Whole company", the same two words on every screen that can count either way (agents,
 * activity, model calls, background work). `notes` say on hover what each one counts on that screen.
 */
export function ScopeSwitch({ value, onChange, notes }: {
  value: Scope; onChange: (s: Scope) => void; notes?: { business: string; company: string };
}) {
  const { current } = useBusinessSelection();
  if (!current) return null;
  const items: [Scope, string, string | undefined][] = [
    ["business", cleanName(current.name), notes?.business], ["company", t("common.whole_company"), notes?.company]];
  return (
    <div className="scope">
      <span className="scope-l" id="scope-l">{t("common.scope.label")}</span>
      <div className="seg" role="group" aria-labelledby="scope-l">
        {items.map(([k, label, note]) => (
          <button key={k} type="button" className={value === k ? "on" : ""} aria-pressed={value === k} title={note} onClick={() => onChange(k)}>{label}</button>
        ))}
      </div>
    </div>
  );
}

/** A screen that counts one way only says so in the spot the switch takes elsewhere (costs, skills). */
export function ScopeLabel({ children }: { children: ReactNode }) {
  return <p className="scope scope-fixed"><span className="scope-l">{t("common.scope.label")}</span> <b>{children}</b></p>;
}

/** The state of a screen in one sentence: the conclusion first, then what the owner can do about it. */
export type Status = { tone?: Tone; text: ReactNode };

export function PageHead({ title, sub, status, actions }: { title: ReactNode; sub?: ReactNode; status?: Status | null; actions?: ReactNode }) {
  return (
    <header className="phead">
      <div className="phead-main">
        <h1>{title}</h1>
        {status && <p className={`phead-status t-${status.tone ?? "muted"}`}><span className="sdot" aria-hidden="true" />{status.text}</p>}
        {sub && <p className="phead-sub">{sub}</p>}
      </div>
      {actions && <div className="phead-actions">{actions}</div>}
    </header>
  );
}

/**
 * A number in a strip of numbers: what it counts, the value, one line of context, and where to go for more. Prefer a
 * PageHead status sentence; a number belongs on screen only when the owner has to act on it. `icon` is kept for
 * callers but drawn only as a state glyph, when `tone` says the number needs attention.
 */
export function Metric({ icon, label, value, sub, to, linkLabel, tone, meter, id, className }: {
  icon: string; label: string; value: ReactNode; sub?: ReactNode; to?: string; linkLabel?: string; tone?: Tone;
  meter?: { value: number; max: number; kind?: MeterKind }; id?: string; className?: string;
}) {
  const flagged = tone === "bad" || tone === "warn";
  return (
    <section id={id} className={`metric ${tone ? `t-${tone}` : ""} ${className ?? ""}`} aria-label={label}>
      <div className="metric-top">
        <p className="metric-l">{flagged && <Icon name={icon} size={14} />}{label}</p>
        {to && <Link className="metric-link" to={to}>{linkLabel ?? t("common.details")}<Icon name="chevron" size={12} /></Link>}
      </div>
      <p className="metric-v">{value}</p>
      {meter && <Meter value={meter.value} max={meter.max} label={label} kind={meter.kind} />}
      {sub && <p className="metric-s">{sub}</p>}
    </section>
  );
}

export function MetricGrid({ children }: { children: ReactNode }) {
  return <div className="metric-grid">{children}</div>;
}

/**
 * `cap`: share of a limit, neutral under 70% and amber from there; a full cap is the owner's call to make, not a
 * failure, so it is never red. `progress`: toward a goal, where more is good.
 */
export type MeterKind = "cap" | "progress";

export const capTone = (pct: number) => (pct >= 70 ? "warn" : "ok");

export function Meter({ value, max, label, kind = "cap" }: { value: number; max: number; label: string; kind?: MeterKind }) {
  const pct = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0;
  const tone = kind === "progress" ? "progress" : capTone(pct);
  return (
    <div className={`meter2 t-${tone}`} role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
      <span style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Section({ title, count, actions, children, className, id }: {
  title: ReactNode; count?: number; actions?: ReactNode; children: ReactNode; className?: string; id?: string;
}) {
  return (
    <section id={id} className={`sec ${className ?? ""}`}>
      <header className="sec-h">
        <h2>{title}{count != null && <span className="sec-n">{count}</span>}</h2>
        {actions && <div className="sec-a">{actions}</div>}
      </header>
      {children}
    </section>
  );
}

/** A bordered panel. Never nest one inside another. */
export function Box({ children, className, pad = true, id }: { children: ReactNode; className?: string; pad?: boolean; id?: string }) {
  return <div id={id} className={`box ${pad ? "" : "flush"} ${className ?? ""}`}>{children}</div>;
}

export function Chip({ tone = "muted", children, dot = true }: { tone?: Tone; children: ReactNode; dot?: boolean }) {
  return <span className={`chip t-${tone}`}>{dot && <span className="chip-dot" aria-hidden="true" />}{children}</span>;
}

export function Banner({ tone, icon = "alert", children, action }: {
  tone: Tone; icon?: string; children: ReactNode; action?: ReactNode;
}) {
  return (
    <div className={`banner t-${tone}`} role={tone === "bad" ? "alert" : "status"}>
      <Icon name={icon} />
      <div className="banner-t">{children}</div>
      {action && <div className="banner-a">{action}</div>}
    </div>
  );
}

/** `count` overrides the shown total when a column renders fewer cards than it holds. */
export type BoardColumn = { key: string; label: string; tone: Tone; items: ReactNode[]; empty?: ReactNode; hint?: string; count?: number; limit?: number;
  /** How much of the row the column takes next to the others (default 1), so a column that is only a record can be narrower. */
  grow?: number };

const COL_MIN = 232, EMPTY_MIN = 150, COL_GAP = 12;

/**
 * The signature view: a collection laid out by state, each column with its count. When the columns do not fit side by
 * side (the side panel is open, or the window is narrow) they wrap onto rows instead of squeezing or hiding past the
 * edge, and empty columns shrink to their header.
 */
export function Board({ columns, label }: { columns: BoardColumn[]; label: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState<number | null>(null);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setWidth(e.contentRect.width));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  const full = columns.filter((c) => c.items.length).length;
  const need = full * COL_MIN + (columns.length - full) * EMPTY_MIN + (columns.length - 1) * COL_GAP;
  const wrap = width != null && width < need;
  // an empty column keeps its header and a short note but gives its width to columns with cards
  const template = wrap ? `repeat(auto-fill, minmax(${COL_MIN}px, 1fr))`
    : columns.map((c) => (c.items.length ? `minmax(${COL_MIN}px, ${c.grow ?? 1}fr)` : `minmax(${EMPTY_MIN}px, ${(c.grow ?? 1) * .55}fr)`)).join(" ");
  return (
    <div ref={ref} className={`board ${wrap ? "wrap" : ""}`} role="region" aria-label={label} tabIndex={0} style={{ gridTemplateColumns: template }}>
      {columns.map((c) => <BoardCol key={c.key} c={c} />)}
    </div>
  );
}

const BOARD_LIMIT = 12;

function BoardCol({ c }: { c: BoardColumn }) {
  const [all, setAll] = useState(false);
  const narrow = typeof window !== "undefined" && window.matchMedia("(max-width: 900px)").matches;
  const limit = c.limit ?? (narrow ? 4 : BOARD_LIMIT);
  const shown = all ? c.items : c.items.slice(0, limit);
  const rest = c.items.length - shown.length;
  return (
        <section className={`bcol ${c.items.length ? "" : "is-empty"}`} aria-label={`${c.label} ${t("common.count", { n: c.count ?? c.items.length })}`}>
          <header className="bcol-h">
            <Chip tone={c.tone}>{c.label}</Chip>
            <span className="bcol-n">{c.count ?? c.items.length}</span>
            {c.hint && <span className="bcol-hint">{c.hint}</span>}
          </header>
          <ol className="bcol-list">
            {c.items.length ? shown.map((x, i) => <li key={i}>{x}</li>) : <li className={typeof c.empty === "string" || c.empty == null ? "bcol-empty" : "bcol-empty rich"}>{c.empty ?? t("common.none")}</li>}
          </ol>
          {rest > 0 && <button type="button" className="bcol-more" onClick={() => setAll(true)}>{t("common.more", { n: rest })}</button>}
          {all && c.items.length > limit && <button type="button" className="bcol-more" onClick={() => setAll(false)}>{t("common.fold")}</button>}
        </section>
  );
}

/** One item on a board: tags, a two-line title, one context line, and a footer of facts. The title names it; its code
 * (REQ-12, T-30) is not shown, and search still finds it by that code. */
export function BoardCard({ tags, title, sub, foot, to, panel, live, selected }: {
  tags?: ReactNode; title: string; sub?: ReactNode; foot?: ReactNode; to: string;
  panel?: PanelTarget | null; live?: boolean; selected?: boolean;
}) {
  const body = (
    <>
      {(tags || live) && (
        <div className="bcard-top">
          {tags}
          {live && <span className="live-dot" role="img" title={t("common.in_progress")} aria-label={t("common.in_progress")} />}
        </div>
      )}
      <p className="bcard-title">{title}</p>
      {sub && <p className="bcard-sub">{sub}</p>}
      {foot && <div className="bcard-foot">{foot}</div>}
    </>
  );
  const cls = `bcard ${selected ? "on" : ""}`;
  return panel
    ? <PanelLink className={cls} to={to} target={panel} label={title} current={selected}>{body}</PanelLink>
    : <Link className={cls} to={to} aria-current={selected ? "true" : undefined}>{body}</Link>;
}

export type View = "board" | "list" | "project";
const BASE_VIEWS: readonly View[] = ["board", "list"];

/** Board or list (and "by project" where a screen offers it), remembered per screen. */
export function useView(key: string, initial: View = "board", views: readonly View[] = BASE_VIEWS): [View, (v: View) => void] {
  const store = `ship.view.${key}`;
  const [v, setV] = useState<View>(() => {
    try {
      const s = localStorage.getItem(store);
      return views.includes(s as View) ? (s as View) : initial;
    } catch {
      return initial;
    }
  });
  return [v, (next) => {
    setV(next);
    try {
      localStorage.setItem(store, next);
    } catch {
      /* private mode */
    }
  }];
}

export function ViewSwitch({ value, onChange, views = BASE_VIEWS }: { value: View; onChange: (v: View) => void; views?: readonly View[] }) {
  const all = { board: [t("common.view_board"), "board"], list: [t("common.view_list"), "list"], project: [t("path.view.project"), "projects"] } as const;
  return (
    <div className="seg" role="group" aria-label={t("common.view_switch")}>
      {views.map((k) => (
        <button key={k} type="button" className={value === k ? "on" : ""} aria-pressed={value === k} onClick={() => onChange(k)}>
          <Icon name={all[k][1]} />{all[k][0]}
        </button>
      ))}
    </div>
  );
}

/** `countLabel` says what the number counts ("4 waiting"), read in place of the bare digit. */
export type TabItem<K extends string> = { key: K; label: string; count?: number; tone?: Tone; countLabel?: string };

/** Tabs that change the address: a row of links, the current one underlined. Use for sibling screens (`/audit/*`). */
export function RouteTabs({ items, label }: { items: { to: string; label: string; count?: number; tone?: Tone; end?: boolean }[]; label: string }) {
  return (
    <nav className="tabs" aria-label={label}>
      {items.map((i) => (
        <NavLink key={i.to} to={i.to} end={i.end ?? true} className={({ isActive }) => (isActive ? "on" : "")}>
          {i.label}
          {i.count != null && <span className={`tab-n ${i.tone ? `t-${i.tone}` : ""}`}>{i.count}</span>}
        </NavLink>
      ))}
    </nav>
  );
}

/**
 * The ARIA tab pattern: one tab in the Tab order, arrows and Home/End move between them, and with an `id` the open tab
 * names its panel (wrap the panel in `TabPanel` with the same `id` and key); only the open panel is drawn, so only
 * its tab points at one. Use it for sections of one screen. A
 * tab row that changes the address is a list of links (`.tabs`), and a filter is `ViewSwitch`-style pressed buttons.
 */
export function Tabs<K extends string>({ items, value, onChange, label, id }: {
  items: TabItem<K>[]; value: K; onChange: (k: K) => void; label: string; id?: string;
}) {
  const list = useRef<HTMLDivElement>(null);
  const onKey = (e: React.KeyboardEvent) => {
    const keys = items.map((x) => x.key);
    const at = keys.indexOf(value);
    const next = e.key === "ArrowRight" ? (at + 1) % keys.length : e.key === "ArrowLeft" ? (at - 1 + keys.length) % keys.length
      : e.key === "Home" ? 0 : e.key === "End" ? keys.length - 1 : -1;
    if (next < 0) return;
    e.preventDefault();
    onChange(keys[next]);
    (list.current?.children[next] as HTMLElement | undefined)?.focus();
  };
  return (
    <div className="tabs2" role="tablist" aria-label={label} ref={list} onKeyDown={onKey}>
      {items.map((t) => (
        <button key={t.key} type="button" role="tab" aria-selected={value === t.key} tabIndex={value === t.key ? 0 : -1}
          id={id ? `${id}-tab-${t.key}` : undefined} aria-controls={id && value === t.key ? `${id}-panel-${t.key}` : undefined}
          className={value === t.key ? "on" : ""} onClick={() => onChange(t.key)}>
          {t.label}
          {t.count != null && <span className={`tab-n ${t.tone ? `t-${t.tone}` : ""}`} aria-label={t.countLabel}>{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

/** The panel a tab opens, named by the tab (`id` and key as given to `Tabs`). Render only the open one. */
export function TabPanel({ id, tabKey, children, className }: { id: string; tabKey: string; children: ReactNode; className?: string }) {
  return <div role="tabpanel" id={`${id}-panel-${tabKey}`} aria-labelledby={`${id}-tab-${tabKey}`} tabIndex={0} className={className}>{children}</div>;
}

/**
 * The view toolbar under the page head: search and filters on the left, sort and the board/list switch on the
 * right (`right`). One per screen, always in this spot, so the controls never move between screens.
 */
export function Toolbar({ children, right, label }: { children?: ReactNode; right?: ReactNode; label?: string }) {
  return (
    <div className="toolbar" role="group" aria-label={label}>
      {children}
      {right && <div className="toolbar-r">{right}</div>}
    </div>
  );
}

/** Rows separated by hairlines, inside a flush Box or on the sheet itself. Each `Row` is one item. */
export function Rows({ children, className, label }: { children: ReactNode; className?: string; label?: string }) {
  return <ul className={`rows ${className ?? ""}`} aria-label={label}>{children}</ul>;
}

/**
 * One row: a leading mark (state dot, checkbox), the title and one meta line, and trailing facts or actions. A row
 * that opens something is a link or a panel link over the whole row (`to` + optional `panel`); `selected` marks the
 * one open in the side panel.
 */
export function Row({ lead, title, meta, end, to, panel, selected, children, className }: {
  lead?: ReactNode; title: ReactNode; meta?: ReactNode; end?: ReactNode; to?: string; panel?: PanelTarget | null;
  selected?: boolean; children?: ReactNode; className?: string;
}) {
  const body = (
    <>
      {lead && <span className="row-lead">{lead}</span>}
      <span className="row-main">
        <span className="row-t">{title}</span>
        {meta && <span className="row-m">{meta}</span>}
        {children}
      </span>
      {end && <span className="row-end">{end}</span>}
    </>
  );
  const cls = `row-i ${selected ? "on" : ""} ${className ?? ""}`;
  if (to && panel) return <li><PanelLink className={cls} to={to} target={panel} current={selected}>{body}</PanelLink></li>;
  if (to) return <li><Link className={cls} to={to} aria-current={selected ? "true" : undefined}>{body}</Link></li>;
  return <li className={cls}>{body}</li>;
}

/**
 * What a screen shows when a list has nothing in it. `kind` says why, which decides the way out: nothing yet (what
 * will fill it), nothing after filtering (clear the filter), or no business (start one, see FirstRun).
 */
export function EmptyState({ title, children, action, kind = "none" }: {
  title: ReactNode; children?: ReactNode; action?: ReactNode; kind?: "none" | "filtered";
}) {
  return (
    <div className={`empty-state k-${kind}`}>
      <p className="empty-t">{title}</p>
      {children && <p className="empty-b">{children}</p>}
      {action && <div className="empty-a">{action}</div>}
    </div>
  );
}

/** What fills a section before its data exists: what it will show and how to get there. */
export function Hint({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="hint">
      <p className="hint-t">{title}</p>
      {children && <p className="hint-b">{children}</p>}
      {action}
    </div>
  );
}

const STEPS = ["read", "critics", "ask", "go"] as const;

/** What starting a business does, in the three steps the owner meets first. The new-business page and every empty screen share it. */
export function StartSteps() {
  return (
    <ol className="start-steps">
      {STEPS.map((s) => <li key={s}><b>{t(`common.steps.${s}.t`)}</b> {t(`common.steps.${s}.b`)}</li>)}
    </ol>
  );
}

/** The one empty state for every screen that needs a business: why it is empty, how to start, and what starting does. */
export function FirstRun({ children }: { children: ReactNode }) {
  const worker = useWorker();
  return (
    <Box className="firstrun">
      {worker.down && (
        <p className="firstrun-worker" role="status">
          <span className="sdot t-bad" aria-hidden="true" />{t("shell.firstrun.worker")} <Fix text={`\`${WORKER_FIX}\``} />
        </p>
      )}
      <div className="firstrun-main">
        <h2 className="firstrun-t">{t("common.firstrun.title")}</h2>
        <p className="firstrun-b">{children}</p>
        <Link className="btn-primary" to="/businesses/new"><Icon name="plus" />{t("common.firstrun.start")}</Link>
      </div>
      <div className="firstrun-steps">
        <h3>{t("common.firstrun.then")}</h3>
        <StartSteps />
      </div>
    </Box>
  );
}

/** An address that names no screen: a titled page, the likely reason, and two ways on. */
export function NotFound() {
  return (
    <div className="notfound">
      <PageHead title={t("shell.notfound.title")} status={{ tone: "muted", text: t("shell.notfound.body") }} />
      <div className="row wrap">
        <Link className="btn-primary" to="/">{t("common.err.back_home")}</Link>
        <button type="button" className="btn-ghost" onClick={() => window.dispatchEvent(new Event("ship:palette"))}><Icon name="search" size={14} />{t("shell.notfound.search")}</button>
      </div>
    </div>
  );
}
