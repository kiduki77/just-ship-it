import { Fragment, ReactNode, RefObject, useEffect, useRef, useState } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { qs, Row } from "../api";
import { AgentKind, agentStatus, failedByOwner } from "../agentState";
import { alertCounts } from "../alerts";
import { Business, cleanName, useBusinessLive, useBusinessSelection } from "../business";
import { TeamButton } from "../chat/Dock";
import { t } from "../i18n";
import { agentName, agentTitle } from "../labels";
import { elsewhereCounts, ElsewhereRow, todayCounts } from "../today";
import { useTheme } from "../theme";
import { Fix } from "./fix";
import { CompanyState, useFrame, useWorker, WORKER_FIX } from "./frame";
import { Icon } from "./icons";
import { CopyButton, ItemLink } from "./panel";
import { StopAllControl } from "./stop";
import { ago, useShowTest, when } from "./ui";
import { refTitle, useRefs } from "../refs";
import { useAgents } from "./people";
import { SendingChip } from "../settings/sending";

/*
 * The frame around every page: the sidebar grouped around the owner's recurring jobs, the business
 * switcher with what waits in each business, and the top bar that says
 * where the owner is and whether the company can run. Every route keeps its address; only its place in the menu moved.
 */

/** What Tab can reach inside the drawer. */
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

type Entry = {
  to: string; label: string; icon: string; count?: number; tone?: "bad" | "soft"; end?: boolean; what?: "shell.nav.count" | "shell.nav.count_failed";
  sub?: ReactNode; countTo?: string;
};

/** `countTo` makes the count its own link to the items it counts (the failed tasks), beside the link to the page. */
function NavItem({ to, label, icon, count, tone, end, what, sub, countTo }: Entry) {
  const name = count ? t(what ?? "shell.nav.count", { n: count }) : "";
  const cls = `count ${tone === "bad" ? "" : "soft"}`;
  const link = (
    <NavLink to={to} end={end ?? to === "/"} className={({ isActive }) => `nav-i ${isActive ? "on" : ""}`}>
      <Icon name={icon} size={15} /><span className="nav-l">{label}</span>
      {!!count && !countTo && <span className={cls} aria-label={name} title={name}>{count}</span>}
    </NavLink>
  );
  return (
    <>
      {countTo && count ? (
        <div className="nav-i-count">
          {link}
          <Link className="count-hit" to={countTo} aria-label={name} title={name}><span className={cls}>{count}</span></Link>
        </div>
      ) : link}
      {sub}
    </>
  );
}

/** A group of places. A collapsible one remembers whether it is open; its count stays visible while it is folded. */
function Group({ id, label, children, collapsible, count }: { id: string; label: string; children: ReactNode; collapsible?: boolean; count?: number }) {
  const [open, setOpen] = useState(() => {
    if (!collapsible) return true;
    try {
      return localStorage.getItem(`ship.nav.${id}`) !== "closed";
    } catch {
      return true;
    }
  });
  const toggle = () => {
    setOpen(!open);
    try {
      localStorage.setItem(`ship.nav.${id}`, open ? "closed" : "open");
    } catch {
      /* private mode */
    }
  };
  return (
    <div className="nav-g" role="group" aria-label={label}>
      {collapsible ? (
        <button type="button" className="nav-gl nav-gl-btn" aria-expanded={open} aria-label={t("shell.nav.group_toggle", { name: label })} onClick={toggle}>
          <Icon name="chevron" size={11} />{label}
          {!open && !!count && <span className="count" aria-hidden="true">{count}</span>}
        </button>
      ) : (
        <p className="nav-gl">{label}</p>
      )}
      {open && children}
    </div>
  );
}

/**
 * Sidebar: Today, your turn, the business, the company's work (folded), records, costs, settings. On a narrow screen
 * it is a modal drawer (`drawer`): focus moves to its close button, Tab stays inside, and Escape, the close button or
 * a click on the scrim closes it.
 */
export function Nav({ drawer = false, onClose }: { drawer?: boolean; onClose?: () => void }) {
  const box = useRef<HTMLElement>(null);
  useEffect(() => {
    if (drawer) box.current?.querySelector<HTMLElement>(".nav-close")?.focus();
  }, [drawer]);
  const onDrawerKey = (e: React.KeyboardEvent) => {
    if (!drawer) return;
    if (e.key === "Escape") {
      e.preventDefault();
      onClose?.();
      return;
    }
    if (e.key !== "Tab" || !box.current) return;
    const stops = [...box.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.getClientRects().length > 0);
    if (!stops.length) return;
    const first = stops[0], last = stops[stops.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };
  const { today } = useBusinessSelection();
  const [showTest] = useShowTest();
  // the failed tasks among the alerts the home and the Decisions page count, so the badge is a part of their number
  const { data: alerts } = useBusinessLive<Row[]>(`/alerts${qs({ include_test: showTest })}`, ["projects", "tasks", "dev_jobs", "jobs", "workers"]);
  const { agents: { data: agents }, failed: { data: failedTasks } } = useAgents(false);
  // the home, decisions and to-dos count the same list
  const counts = todayCounts(today);
  const failed = alertCounts(alerts ?? []).tasks;
  // this business's own failures, as the switcher counts every other business's (services/today.elsewhere)
  const stopped = alertCounts((alerts ?? []).filter((a) => !a.company_wide)).need;
  const elsewhere = today?.elsewhere ?? [];
  type Part = "decisions" | "todos" | "failed";
  const total = (part: Part) => elsewhere.reduce((n, b) => n + elsewhereCounts(b)[part], 0);
  const elseLink = (part: Part, key: "shell.nav.elsewhere" | "shell.nav.elsewhere_todos" | "shell.nav.elsewhere_failed", to: string) => {
    const n = total(part);
    const first = elsewhere.find((b) => elsewhereCounts(b)[part] > 0);
    return n > 0 && first && (
      <Link className="nav-else" to={`${to}${to.includes("?") ? "&" : "?"}b=${first.business_id}`}>
        <Icon name="arrow" size={11} />{t(key, { n })}
      </Link>
    );
  };

  return (
    <>
    {drawer && <div className="nav-scrim" aria-hidden="true" onClick={onClose} />}
    <nav ref={box} className={`nav ${drawer ? "is-drawer" : ""}`} id="main-menu" aria-label={t("shell.nav.menu")} onKeyDown={onDrawerKey}
      role={drawer ? "dialog" : undefined} aria-modal={drawer || undefined}>
      {drawer && (
        <button type="button" className="icon-btn nav-close" aria-label={t("shell.menu.close")} title={t("shell.menu.close")} onClick={onClose}>
          <Icon name="close" />
        </button>
      )}
      <div className="nav-top">
        <Link to="/" className="brand">
          <span className="logo logo-mark" aria-hidden="true" />
          Just Ship It
        </Link>
        <div className="nav-biz"><BusinessSwitcher elsewhere={elsewhere} counts={counts} stopped={stopped} /></div>
        <button type="button" className="nav-search" onClick={() => window.dispatchEvent(new Event("ship:palette"))}>
          <Icon name="search" size={14} /><span>{t("shell.nav.search")}</span><kbd>Ctrl K</kbd>
        </button>
      </div>

      <div className="nav-scroll">
        <div className="nav-g" role="group" aria-label={t("shell.nav.your_turn")}>
          <NavItem to="/" label={t("shell.nav.today")} icon="home" />
          <NavItem to="/inbox" label={t("shell.nav.decisions")} icon="inbox" count={counts.decisions} tone={counts.blocking ? "bad" : "soft"}
            sub={<>{elseLink("decisions", "shell.nav.elsewhere", "/inbox")}{elseLink("failed", "shell.nav.elsewhere_failed", "/inbox?tab=alerts")}</>} />
          <NavItem to="/my-tasks" label={t("shell.nav.todos")} icon="tasks" count={counts.mine} tone="soft"
            sub={elseLink("todos", "shell.nav.elsewhere_todos", "/my-tasks")} />
        </div>

        <Group id="business" label={t("shell.nav.business")}>
          <NavItem to="/business" label={t("shell.nav.business")} icon="business" />
          <NavItem to="/projects" label={t("shell.nav.projects")} icon="projects" />
        </Group>

        <Group id="work" label={t("shell.nav.company_work")} collapsible count={failed || undefined}>
          <NavItem to="/tasks" label={t("shell.nav.tasks")} icon="board" count={failed || undefined} tone="bad" what="shell.nav.count_failed"
            countTo="/tasks?group=blocked" />
          <NavItem to="/engineering" label={t("shell.nav.engineering")} icon="dev" />
          <NavItem to="/routines" label={t("shell.nav.routines")} icon="routines" />
          <AgentGroup agents={agents ?? []} failed={failedTasks?.tasks} />
          <NavItem to="/crews" label={t("shell.nav.crews")} icon="users" />
          <NavItem to="/skills" label={t("shell.nav.skills")} icon="skills" />
        </Group>

        <Group id="records" label={t("shell.nav.records")} collapsible>
          <NavItem to="/audit" label={t("shell.nav.activity")} icon="activity" end />
          <NavItem to="/audit/runs" label={t("shell.nav.runs")} icon="runs" />
          <NavItem to="/audit/queue" label={t("shell.nav.queue")} icon="clock" />
        </Group>

        <div className="nav-g" role="group" aria-label={t("shell.nav.company")}>
          <NavItem to="/audit/costs" label={t("shell.nav.costs")} icon="costs" />
          <NavItem to="/settings" label={t("shell.nav.settings")} icon="settings" />
        </div>
      </div>
    </nav>
    </>
  );
}

const SIDEBAR_AGENTS = 5;

/** Pages of one business's item: a pick of another business in the switcher leaves them for that business's list. */
const ITEM_PAGE = /^\/(kickoff\/\d+|projects\/\d+)/;

type AgentSaid = "shell.nav.agent_working" | "shell.nav.agent_waiting" | "shell.nav.agent_failed" | "shell.nav.agent_stopped";
const AGENT_NAV: Record<AgentKind, { dot: string; rank: number; said: AgentSaid | null }> = {
  working: { dot: "running", rank: 2, said: "shell.nav.agent_working" },
  waiting: { dot: "stopped", rank: 2, said: "shell.nav.agent_waiting" },
  failed: { dot: "error", rank: 1, said: "shell.nav.agent_failed" },
  stopped: { dot: "stopped", rank: 1, said: "shell.nav.agent_stopped" },
  idle: { dot: "idle", rank: 0, said: null },
};

/**
 * The agents, folded under one link: everyone working or failing now, then the most recently active, at most five.
 * The full roster lives on the agents page, so the sidebar stays the same height whether the company has eleven
 * agents or fifty.
 */
function AgentGroup({ agents, failed }: { agents: Row[]; failed: Row[] | undefined }) {
  const worker = useWorker();
  const [open, setOpen] = useState(() => {
    try {
      return localStorage.getItem("ship.nav.agents") === "open";
    } catch {
      return false;
    }
  });
  const toggle = () => {
    setOpen(!open);
    try {
      localStorage.setItem("ship.nav.agents", open ? "closed" : "open");
    } catch {
      /* private mode */
    }
  };
  // the status the agents page shows: nobody is "working" while no worker runs
  const byOwner = failedByOwner(failed);
  const kind = new Map<string, AgentKind>(agents.map((a) => [a.id, agentStatus(a, byOwner.get(a.id) ?? [], worker).kind]));
  const kindOf = (a: Row): AgentKind => kind.get(a.id) ?? "idle";
  const urgent = (a: Row) => AGENT_NAV[kindOf(a)].rank;
  const ranked = [...agents].sort((a, b) => urgent(b) - urgent(a) || String(b.last_run_at ?? "").localeCompare(String(a.last_run_at ?? "")));
  const live = agents.filter((a) => kindOf(a) === "working").length;
  const waiting = agents.filter((a) => kindOf(a) === "waiting").length;
  const shown = ranked.slice(0, Math.max(SIDEBAR_AGENTS, agents.filter((a) => urgent(a) > 0).length));
  return (
    <>
      <div className="nav-agents-h">
        <NavItem to="/agents" label={agents.length ? t("shell.nav.agents_all_n", { n: agents.length }) : t("shell.nav.agents_all")} icon="agents" end />
        {agents.length > 0 && (
          <button type="button" className="nav-fold-btn" aria-expanded={open} aria-label={t("shell.nav.group_toggle", { name: t("shell.nav.agents") })} onClick={toggle}>
            {live > 0 ? <span className="nav-gl-live">{t("shell.nav.agents_working", { n: live })}</span>
              : waiting > 0 && <span className="nav-gl-wait" title={t("shell.nav.agents_waiting_why", { n: waiting })}>{t("shell.nav.agents_waiting", { n: waiting })}</span>}
            <Icon name="chevron" size={11} />
          </button>
        )}
      </div>
      {open && shown.map((a) => {
        const look = AGENT_NAV[kindOf(a)];
        const said = look.said ? t(look.said, { name: agentTitle(a) }) : agentTitle(a);
        return (
          <NavLink key={a.id} to={`/agents/${a.id}`} className={({ isActive }) => `nav-i nav-agent ${isActive ? "on" : ""}`}
            title={said} aria-label={said}>
            <span className={`adot s-${look.dot}`} aria-hidden="true" />
            <span className="nav-l">{agentTitle(a)}</span>
          </NavLink>
        );
      })}
    </>
  );
}

/**
 * The business on screen and the way to another. Each row says what waits there, so the owner picks the business
 * with work instead of opening each in turn. Arrow keys move between rows; "Start a business" ends the list.
 */
function BusinessSwitcher({ elsewhere, counts, stopped }: { elsewhere: ElsewhereRow[]; counts: ReturnType<typeof todayCounts>; stopped: number }) {
  const { businesses, current, select, error, reload } = useBusinessSelection();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const { pathname } = useLocation();
  const navigate = useNavigate();

  useEffect(() => setOpen(false), [pathname]);
  useEffect(() => {
    if (!open) return;
    (list.current?.querySelector("[aria-current]") as HTMLElement | null ?? list.current?.querySelector("button") as HTMLElement | null)?.focus();
    const away = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      if (!open) return;
      e.stopPropagation();
      setOpen(false);
      button.current?.focus();
      return;
    }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp" && e.key !== "Home" && e.key !== "End") return;
    const items = [...(list.current?.querySelectorAll<HTMLElement>("[role=menuitem]") ?? [])];
    if (!items.length) return;
    e.preventDefault();
    const at = items.indexOf(document.activeElement as HTMLElement);
    const next = e.key === "Home" ? 0 : e.key === "End" ? items.length - 1 : (at + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    items[next].focus();
  };

  if (error && !businesses) {
    return (
      <div className="pick-failed" role="alert">
        {t("shell.picker.failed")} <button type="button" className="link small" onClick={reload}>{t("common.retry")}</button>
      </div>
    );
  }
  if (!businesses) return <div className="pick-name muted" aria-busy="true">{t("shell.picker.loading")}</div>;
  if (!current) return <Link className="pick-name" to="/businesses/new"><Icon name="plus" size={14} />{t("shell.picker.new")}</Link>;
  const waiting = (b: Business) => {
    if (b.id === current.id) return { d: counts.decisions, t: counts.mine, f: stopped };
    const e = elsewhere.find((x) => x.business_id === b.id);
    const n = e ? elsewhereCounts(e) : { decisions: 0, todos: 0, failed: 0 };
    return { d: n.decisions, t: n.todos, f: n.failed };
  };
  return (
    <div className="pick" ref={box} onKeyDown={onKey}>
      <button ref={button} type="button" className="pick-btn" aria-expanded={open} aria-controls="pick-list" aria-haspopup="menu"
        title={current.one_liner} onClick={() => setOpen(!open)}>
        <span className="pick-l">{t("shell.picker.label")}</span>
        <span className="pick-cur">{cleanName(current.name)}</span>
        <Icon name="chevron" size={12} />
      </button>
      {open && (
        <div className="pick-pop" id="pick-list" role="menu" aria-label={t("shell.picker.label")} ref={list}>
          {businesses.map((b) => {
            const w = waiting(b);
            return (
              <button key={b.id} type="button" role="menuitem" className={`pick-opt ${b.id === current.id ? "on" : ""}`}
                aria-current={b.id === current.id ? "true" : undefined}
                onClick={() => {
                  select(b.id);
                  setOpen(false);
                  button.current?.focus();
                  // a page of one business's item (its kickoff chat, one project) does not belong to the business picked
                  if (b.id !== current.id && ITEM_PAGE.test(pathname)) navigate(`${pathname.startsWith("/projects/") ? "/projects" : "/"}?b=${b.id}`);
                }}>
                <span className="pick-opt-n">{cleanName(b.name)}</span>
                <span className="pick-opt-s">
                  {w.d > 0 && <span className={`pick-n ${b.id === current.id && counts.blocking ? "t-bad" : ""}`}>{t("shell.picker.decisions", { n: w.d })}</span>}
                  {w.t > 0 && <span className="pick-n">{t("shell.picker.todos", { n: w.t })}</span>}
                  {w.f > 0 && <span className="pick-n t-bad">{t("shell.picker.stopped", { n: w.f })}</span>}
                </span>
              </button>
            );
          })}
          <Link className="pick-new" role="menuitem" to="/businesses/new"><Icon name="plus" size={14} />{t("shell.picker.new")}</Link>
        </div>
      )}
    </div>
  );
}

/**
 * One slot that says whether the company can run: the most serious condition wins (connection, the owner's stop, a
 * worker that is down, a usage wait), and the way to fix it is one click away. Quiet while everything runs.
 */
function CompanyStateChip() {
  const { company, worker, recheck } = useFrame();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const kind = company.kind;
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const onDown = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, [open]);
  useEffect(() => setOpen(false), [kind]);
  const tone = kind === "running" ? "ok" : kind === "unknown" ? "muted" : kind === "usage_wait" || kind === "stopped" ? "warn" : "bad";
  const text = kind === "usage_wait" ? t("shell.state.usage_wait") : t(`shell.state.${kind}`);
  const detail = stateDetail(company, worker.lastSeen, recheck);
  return (
    <div className={`cstate t-${tone}`} ref={box}>
      <button type="button" className="cstate-btn" aria-expanded={open} aria-haspopup="dialog" aria-label={`${t("shell.state.label")}: ${text}`}
        onClick={() => setOpen(!open)} disabled={!detail}>
        <span className="dot" aria-hidden="true" />
        <span className="cstate-t" role="status" aria-live="polite">
          <span className="cstate-full">{text}</span><span className="cstate-short">{t(`shell.state.short.${kind}`)}</span>
        </span>
      </button>
      {open && detail && (
        <div className="cstate-pop" role="dialog" aria-label={t("shell.state.label")}>
          {detail}
        </div>
      )}
    </div>
  );
}

function stateDetail(c: CompanyState, lastSeen: string | null, recheck: () => void): ReactNode {
  if (c.kind === "worker_never" || c.kind === "worker_silent") {
    return (
      <>
        <p className="cstate-q">{t(`shell.state.${c.kind}`)}</p>
        {lastSeen && <p className="small muted">{t("shell.state.last_seen", { when: when(lastSeen) })}</p>}
        <p className="small">{t("shell.state.fix")}</p>
        <p className="cstate-cmd"><Fix text={`\`${WORKER_FIX}\``} /> <CopyButton text={WORKER_FIX} what={t("shell.state.copy_cmd")} /></p>
        <p className="small muted">{t("shell.state.fix_hint")}</p>
        <div className="row"><button type="button" className="btn-ghost small" onClick={recheck}>{t("shell.state.check_again")}</button></div>
      </>
    );
  }
  if (c.kind === "lost" || c.kind === "unreachable") {
    return (
      <>
        <p className="cstate-q">{t(`shell.state.${c.kind}`)}</p>
        {c.kind === "lost" && c.since && Date.now() - new Date(c.since).getTime() > 30_000 && <p className="small muted">{t("shell.state.stale", { ago: ago(c.since) })}</p>}
        <p className="small">{t("shell.state.fix")}</p>
        <p className="cstate-cmd"><Fix text={`\`${WORKER_FIX}\``} /> <CopyButton text={WORKER_FIX} what={t("shell.state.copy_cmd")} /></p>
        <div className="row"><button type="button" className="btn-ghost small" onClick={recheck}>{t("shell.state.check_again")}</button></div>
      </>
    );
  }
  if (c.kind === "stopped") {
    return (
      <>
        <p className="cstate-q">{t("shell.stop.stopped_since", { since: when(c.stop.at) })}</p>
        {c.stop.reason && <p className="small muted">{t("shell.stop.reason", { reason: c.stop.reason })}</p>}
        <p className="small muted">{t("shell.stop.banner", { since: when(c.stop.at) })}</p>
      </>
    );
  }
  if (c.kind === "usage_wait") {
    return (
      <>
        <p className="cstate-q">{c.text}</p>
        <ItemLink className="btn-ghost small" to={c.link}>{t("common.details")}</ItemLink>
      </>
    );
  }
  return null;
}

type Crumb = { label: string; to?: string };

/**
 * Where each address sits: the trail the top bar shows and the document title comes from. Specific patterns come
 * before general ones, so `/businesses/new` is never read as the business page.
 */
const ROUTES: [RegExp, (m: RegExpMatchArray) => Crumb[]][] = [
  [/^\/$/, () => [{ label: t("shell.nav.today") }]],
  [/^\/inbox/, () => [{ label: t("shell.nav.decisions") }]],
  [/^\/my-tasks/, () => [{ label: t("shell.nav.todos") }]],
  [/^\/tasks/, () => [{ label: t("shell.nav.tasks") }]],
  [/^\/projects\/(\d+)/, (m) => [{ label: t("shell.nav.projects"), to: "/projects" }, { label: `P-${m[1]}` }]],
  [/^\/projects/, () => [{ label: t("shell.nav.projects") }]],
  [/^\/routines/, () => [{ label: t("shell.nav.routines") }]],
  [/^\/engineering/, () => [{ label: t("shell.nav.engineering") }]],
  [/^\/agents\/([^/]+)/, (m) => [{ label: t("shell.nav.agents"), to: "/agents" }, { label: agentName(decodeURIComponent(m[1])) }]],
  [/^\/agents/, () => [{ label: t("shell.nav.agents") }]],
  [/^\/businesses\/new/, () => [{ label: t("shell.crumb.new_business") }]],
  [/^\/business(?:[/?#]|$)/, () => [{ label: t("shell.nav.business") }]],
  [/^\/kickoff/, () => [{ label: t("shell.nav.business"), to: "/business" }, { label: t("shell.crumb.kickoff") }]],
  [/^\/crews/, () => [{ label: t("shell.nav.crews") }]],
  [/^\/skills/, () => [{ label: t("shell.nav.agents"), to: "/agents" }, { label: t("shell.nav.skills") }]],
  [/^\/audit\/costs/, () => [{ label: t("shell.nav.costs") }]],
  [/^\/audit\/runs\/(\d+)/, (m) => [{ label: t("shell.nav.records") }, { label: t("shell.nav.runs"), to: "/audit/runs" }, { label: `#${m[1]}` }]],
  [/^\/audit\/runs/, () => [{ label: t("shell.nav.records") }, { label: t("shell.nav.runs") }]],
  [/^\/audit\/queue/, () => [{ label: t("shell.nav.records") }, { label: t("shell.nav.queue") }]],
  [/^\/audit/, () => [{ label: t("shell.nav.records") }, { label: t("shell.nav.activity") }]],
  [/^\/settings/, () => [{ label: t("shell.nav.settings") }]],
];

const PROJECT = /^P-\d+$/;

export function crumbs(path: string): Crumb[] {
  for (const [re, f] of ROUTES) {
    const m = path.match(re);
    if (m) return f(m);
  }
  return [];
}

function ThemeButton() {
  const [pref, set] = useTheme();
  const dark = pref === "dark" || (pref === "system" && document.documentElement.dataset.theme === "dark");
  return (
    <button type="button" className="icon-btn" onClick={() => set(dark ? "light" : "dark")}
      aria-label={t(dark ? "shell.theme.light" : "shell.theme.dark")} title={t(dark ? "shell.theme.light" : "shell.theme.dark")}>
      <Icon name={dark ? "sun" : "moon"} />
    </button>
  );
}

/** Where the owner is (business › screen › item, each a link), the company's state in one slot, the stop, the theme. */
export function Topbar({ menu, onMenu, menuButton }: { menu: boolean; onMenu: () => void; menuButton: RefObject<HTMLButtonElement | null> }) {
  const { current } = useBusinessSelection();
  const { pathname } = useLocation();
  const trail = crumbs(pathname);
  useRefs(trail.map((c) => c.label).filter((c) => PROJECT.test(c)));
  const said = trail.map((c) => ({ ...c, label: PROJECT.test(c.label) ? refTitle(c.label) || t("common.ref.project") : c.label }));
  const onNewBusiness = pathname.startsWith("/businesses/new");
  const full: Crumb[] = current && !onNewBusiness ? [{ label: cleanName(current.name), to: "/" }, ...said] : said;
  const page = said[said.length - 1]?.label ?? t("shell.notfound.title");
  useEffect(() => {
    document.title = [page, current && !onNewBusiness ? cleanName(current.name) : null, t("shell.title.app")].filter(Boolean).join(" · ");
  }, [page, current, onNewBusiness]);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    document.getElementById("main")?.focus({ preventScroll: true });
  }, [pathname]);
  return (
    <header className="topbar">
      <button ref={menuButton} type="button" className="icon-btn menu-btn" aria-expanded={menu} aria-controls="main-menu" aria-haspopup="dialog"
        aria-label={t("shell.menu.open")} onClick={onMenu}>
        <Icon name="list" />
      </button>
      <nav className="crumbs" aria-label={t("shell.crumbs")}>
        {full.map((c, i) => {
          const last = i === full.length - 1;
          return (
            <Fragment key={i}>
              {i > 0 && <span className="crumb-sep" aria-hidden="true">/</span>}
              {c.to && !last ? <Link to={c.to}>{c.label}</Link> : <span className={last ? "cur" : ""} aria-current={last ? "page" : undefined}>{c.label}</span>}
            </Fragment>
          );
        })}
      </nav>
      <div className="topbar-a">
        <CompanyStateChip />
        <SendingChip />
        <StopAllControl />
        <TeamButton />
        <ThemeButton />
      </div>
    </header>
  );
}
