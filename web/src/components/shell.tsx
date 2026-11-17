import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, qs, Row, useLiveEvents } from "../api";
import { cleanName, SwitchNoteToast, useBusinessLive, useBusinessSelection } from "../business";
import { PlainKey, t } from "../i18n";
import { agentTitle, label } from "../labels";
import { eventData, eventHref, eventText } from "./activity";
import { refHref, useAnnouncements, useShowTest } from "./ui";

const KIND_NAME: Record<string, PlainKey> = {
  P: "term.project", T: "term.task", REQ: "term.decision", EV: "term.evidence", DEC: "term.decision_log", J: "term.build",
  M: "term.meeting", AGENT: "term.agent",
};
/** Every screen by the name the sidebar shows, plus the other names the owner may type for it. */
const PAGES: [string, PlainKey, PlainKey[]][] = [
  ["/", "shell.nav.today", ["shell.page.home", "shell.nav.dashboard"]], ["/inbox", "shell.nav.decisions", []], ["/my-tasks", "shell.nav.todos", []],
  ["/business", "shell.nav.business", ["term.people", "term.experiment"]], ["/business?tab=brief", "term.plan", ["term.kickoff"]],
  ["/businesses/new", "shell.page.new_business", []], ["/tasks", "shell.nav.tasks", []], ["/projects", "shell.nav.projects", []],
  ["/agents", "shell.nav.agents", []], ["/audit", "shell.nav.activity", ["shell.page.activity"]], ["/audit/runs", "shell.nav.runs", ["shell.page.runs"]],
  ["/audit/queue", "shell.nav.queue", []], ["/audit/costs", "shell.nav.costs", ["shell.page.costs"]], ["/crews", "shell.nav.crews", []],
  ["/engineering", "shell.nav.engineering", ["shell.page.engineering", "term.build"]], ["/skills", "shell.nav.skills", []],
  ["/routines", "shell.nav.routines", ["shell.page.routines"]], ["/settings", "shell.nav.settings", []],
];

/** What the owner can start from anywhere, each by the screen that already does it. `words` are other ways to say it. */
type Action = "business" | "project" | "idea" | "instruct";
const ACTIONS: Action[] = ["idea", "instruct", "project", "business"];
function actionHref(a: Action, pathname: string, business: number | null, projects: Row[] | null): string {
  const project = /^\/projects\/(\d+)/.exec(pathname);
  if (a === "business") return "/businesses/new";
  if (a === "project") return "/projects?new=1";
  // the address names its business, or the console adds `?b=` itself and the `#idea-text` that scrolls to the form is lost
  if (a === "idea") return `/business?tab=ideas${business ? `&b=${business}` : ""}#idea-text`;
  // the instruction box sits on a project's overview: this project's, the business's one open project, or the list to pick from
  const open = (projects ?? []).filter((p) => p.status !== "ARCHIVED" && !p.is_test);
  const id = project ? project[1] : open.length === 1 ? String(open[0].id) : null;
  return id ? `/projects/${id}#d-${id}` : "/projects";
}

function hrefOf(r: Row): string {
  if (r.kind === "J") return refHref("J", r.id, r.project_id) ?? "/engineering";
  if (r.kind === "AGENT") return `/agents/${r.id}`;
  return refHref(r.kind, r.id, r.project_id) ?? "/";
}

/** Ctrl+K / ⌘K: jump to any id (P-5, T-57, REQ-66, EV-1633) or search titles, claims and pages. */
export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const [sel, setSel] = useState(0);
  const [showTest] = useShowTest();
  const { businesses, current, select } = useBusinessSelection();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const { data: projects } = useBusinessLive<Row[]>(open ? "/projects" : null, ["projects"]);
  const input = useRef<HTMLInputElement>(null);
  const back = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        back.current = document.activeElement as HTMLElement;
        setOpen((o) => !o);
      }
    };
    const onOpen = () => {
      back.current = document.activeElement as HTMLElement;
      setOpen(true);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("ship:palette", onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("ship:palette", onOpen);
    };
  }, []);
  useEffect(() => {
    if (open) {
      setQ("");
      setRows([]);
      setSel(0);
      setTimeout(() => input.current?.focus(), 0);
    } else back.current?.focus?.();
  }, [open]);
  useEffect(() => {
    if (!open || !q.trim()) {
      setRows([]);
      return;
    }
    let live = true;
    const t = window.setTimeout(() => {
      api<Row[]>(`/search${qs({ q, include_test: showTest })}`).then((r) => live && (setRows(r), setSel(0))).catch(() => live && setRows([]));
    }, 160);
    return () => {
      live = false;
      window.clearTimeout(t);
    };
  }, [q, open, showTest]);

  if (!open) return null;
  const needle = q.trim().toLowerCase();
  const pages = PAGES.map(([to, key, aliases]) => ({ kind: "PAGE", id: to, title: t(key), words: [t(key), ...aliases.map((a) => t(a))] }))
    .filter((p) => needle && p.words.some((w) => w.toLowerCase().includes(needle)));
  // the owner's businesses, by name: picking one switches to it, like the switcher
  const bizRows = (businesses ?? []).filter((b) => needle && cleanName(b.name).toLowerCase().includes(needle))
    .map((b) => ({ kind: "BUSINESS", id: b.id, title: cleanName(b.name) }));
  // with nothing typed the palette offers what the owner can start; typing narrows them like the screens
  const actions = ACTIONS.map((a) => ({ kind: "ACTION", id: a, title: t(`shell.palette.do.${a}`),
    words: [t(`shell.palette.do.${a}`), ...t(`shell.palette.do.words.${a}`).split(" ")] }))
    .filter((a) => !needle || a.words.some((w) => w.toLowerCase().includes(needle)));
  // a screen whose name starts with what was typed comes first ("proj" is Projects); one that only contains it ("ask"
  // in Tasks) comes after the actions it also matches
  const lead = pages.filter((p) => p.words.some((w) => w.toLowerCase().startsWith(needle)));
  const items: Row[] = [...rows, ...bizRows, ...lead, ...actions, ...pages.filter((p) => !lead.includes(p))];
  const go = (r: Row) => {
    setOpen(false);
    if (r.kind === "BUSINESS") {
      select(r.id);
      return;
    }
    navigate(r.kind === "PAGE" ? r.id : r.kind === "ACTION" ? actionHref(r.id, pathname, current?.id ?? null, projects) : hrefOf(r));
  };
  return (
    <div className="palette-back" onMouseDown={(e) => e.target === e.currentTarget && setOpen(false)}
      onKeyDown={(e) => {
        // the dialog owns Escape, and Tab stays on its one input: focus never leaves a modal that is still open
        if (e.key === "Escape") setOpen(false);
        if (e.key === "Tab") { e.preventDefault(); input.current?.focus(); }
      }}>
      <div className="palette" role="dialog" aria-modal="true" aria-label={t("shell.palette.label")}>
        <input ref={input} value={q} placeholder={t("shell.palette.placeholder")} aria-label={t("shell.palette.input")}
          role="combobox" aria-expanded={items.length > 0} aria-controls="palette-list"
          aria-activedescendant={items[sel] ? `pal-${sel}` : undefined}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(items.length - 1, s + 1)); }
            if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(0, s - 1)); }
            // closing hands focus back to where the owner was before this Enter is done; left to the browser, the same
            // Enter would press that field's form (Settings would save the edit the leave question is about)
            if (e.key === "Enter") e.preventDefault();
            if (e.key === "Enter" && items[sel]) go(items[sel]);
          }} />
        <ul id="palette-list" role="listbox" className="palette-list">
          {items.map((r, i) => (
            <li key={`${r.kind}-${r.id}`} id={`pal-${i}`} role="option" aria-selected={i === sel}
              className={i === sel ? "on" : ""} onMouseEnter={() => setSel(i)} onMouseDown={(e) => { e.preventDefault(); go(r); }}>
              <span className="palette-k small muted">{r.kind === "PAGE" ? t("shell.palette.page") : r.kind === "BUSINESS" ? t("shell.palette.business")
                : r.kind === "ACTION" ? t("shell.palette.action") : KIND_NAME[r.kind] ? t(KIND_NAME[r.kind]) : r.kind}</span>
              <span className="palette-t">{r.kind === "AGENT" ? agentTitle(r) : r.title}</span>
              {r.status && <span className="small muted">{label(r.status)}</span>}
            </li>
          ))}
          {q.trim() && items.length === 0 && <li className="palette-empty small muted">{t("shell.palette.none")}</li>}
          {!q.trim() && <li className="palette-empty small muted" role="presentation">{t("shell.palette.keys")}</li>}
        </ul>
      </div>
    </div>
  );
}

/** One notice. A burst of the same `kind` folds into it: `count` grows and the text becomes the summary. */
type Toast = { id: number; kind: string; text: string; href: string | null; tone: string; count: number };

const TOAST_MS = 9000;
const NOTICE_MS = 6000;
const PROJECT_OUTCOMES: Record<string, string> = { DONE: "ok", FAILED: "bad", PAUSED: "warn" };

/** What deserves a corner notice: a request that blocks work, or a project that finished, failed or stopped. */
function toastOf(e: Row): Pick<Toast, "kind" | "tone"> | null {
  const d = eventData(e);
  if (e.entity === "request" && e.verb === "created" && d.status === "PENDING" && d.blocking) return { kind: "decision", tone: "bad" };
  if (e.entity === "project" && e.verb === "status" && PROJECT_OUTCOMES[d.to]) return { kind: `project-${d.to}`, tone: PROJECT_OUTCOMES[d.to] };
  return null;
}

/** The stack shows two notices, one on a phone: the screen under it matters more than a pile of alerts. */
const narrow = () => window.matchMedia("(max-width: 899px)").matches;

/**
 * Corner notices for what needs the owner right now: new blocking decisions and project outcomes. Task and dev-job
 * stage changes, requests that wait without blocking, and other businesses' events stay in the activity log.
 */
export function Toasts() {
  const [items, setItems] = useState<Toast[]>([]);
  const timers = useRef(new Map<string, number>());
  // the result of an owner action, kept on screen for a while after the button that ran it is gone
  const [notices, setNotices] = useState<{ id: number; text: string; tone: "ok" | "bad" }[]>([]);
  useAnnouncements((n) => {
    setNotices((xs) => [...xs.slice(-1), n]);
    window.setTimeout(() => setNotices((xs) => xs.filter((x) => x.id !== n.id)), NOTICE_MS);
  });
  const { businesses, current } = useBusinessSelection();
  // the notices follow the business on screen, like every list (services/scope.py): its own events and the company's;
  // with no business, the company's alone
  const mine = useRef<(e: Row) => boolean>(() => false);
  mine.current = (e) => {
    if (!businesses) return false;
    const pid = e.project_id ?? (e.entity === "project" ? e.entity_id : null);
    return (current !== null && e.business_id === current.id) || (e.business_id == null && pid == null);
  };
  const [phone, setPhone] = useState(narrow);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 899px)");
    const on = () => setPhone(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);

  const drop = (kind: string) => {
    window.clearTimeout(timers.current.get(kind));
    timers.current.delete(kind);
    setItems((xs) => xs.filter((x) => x.kind !== kind));
  };
  useLiveEvents((e) => {
    if (e.table !== "activity_events" || e.actor === "owner" || !mine.current(e)) return;
    const hit = toastOf(e);
    if (!hit) return;
    const one = { text: eventText(e), href: eventHref(e) };
    setItems((xs) => {
      const old = xs.find((x) => x.kind === hit.kind);
      const count = (old?.count ?? 0) + 1;
      const burst = hit.kind === "decision"
        ? { text: t("shell.toast.decisions", { n: count }), href: "/inbox" }
        : { text: t("shell.toast.projects", { n: count, state: label(hit.kind.slice("project-".length)) }), href: "/projects" };
      const toast: Toast = { id: e.id, ...hit, ...(count > 1 ? burst : one), count };
      // a decision that blocks work stays on top; project outcomes fill the rest, newest first
      const next = [toast, ...xs.filter((x) => x.kind !== hit.kind)];
      return [...next.filter((x) => x.kind === "decision"), ...next.filter((x) => x.kind !== "decision")].slice(0, 2);
    });
    window.clearTimeout(timers.current.get(hit.kind));
    timers.current.set(hit.kind, window.setTimeout(() => drop(hit.kind), TOAST_MS));
  });
  useEffect(() => () => timers.current.forEach((id) => window.clearTimeout(id)), []);
  return (
    <div className="toasts" role="status" aria-live="polite">
      <SwitchNoteToast />
      {notices.map((n) => (
        <div key={n.id} className={`toast ${n.tone}`}>
          <span className={`dot ${n.tone}`} aria-hidden="true" />
          <span>{n.text}</span>
          <button className="link small" aria-label={t("shell.toast.close")} onClick={() => setNotices((xs) => xs.filter((x) => x.id !== n.id))}>{t("common.close")}</button>
        </div>
      ))}
      {items.slice(0, phone ? 1 : 2).map((n) => (
        <div key={n.kind} className={`toast ${n.tone}`}>
          <span className={`dot ${n.tone}`} aria-hidden="true" />
          {n.href ? <Link to={n.href} onClick={() => drop(n.kind)}>{n.text}</Link> : <span>{n.text}</span>}
          <button className="link small" aria-label={t("shell.toast.close")} onClick={() => drop(n.kind)}>{t("common.close")}</button>
        </div>
      ))}
    </div>
  );
}
