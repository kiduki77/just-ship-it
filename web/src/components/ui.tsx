import DOMPurify from "dompurify";
import { MouseEvent, ReactNode, useEffect, useId, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ApiFailure } from "../api";
import { locale, t } from "../i18n";
import { agentName, label, prose } from "../labels";
import { markdownHtml, servedImage } from "../markdown";
import { refProject, refTitle, useRefs } from "../refs";
import { ItemLink, panelTargetOf, usePanel } from "./panel";

/** Link targets for in-text references; `projectId` is needed for project-scoped ones. */
export function refHref(kind: string, n: string | number, projectId?: number | null): string | null {
  if (kind === "REQ") return `/inbox?req=${n}`;
  if (kind === "T") return projectId ? `/projects/${projectId}/tasks?task=${n}` : `/tasks?task=${n}`;
  if (kind === "P") return `/projects/${n}`;
  if (kind === "AGENT") return `/agents/${n}`;
  if (kind === "OT") return `/my-tasks?ot=${n}`;
  if (!projectId) return null;
  if (kind === "EV") return `/projects/${projectId}/evidence?ev=${n}`;
  if (kind === "DEC") return `/projects/${projectId}/decisions#dec-${n}`;
  if (kind === "M") return `/projects/${projectId}/decisions#m-${n}`;
  if (kind === "J") return `/projects/${projectId}/dev?job=${n}`;
  return null;
}

/**
 * A piece of evidence in its project's ledger, with the way back to `from` (the address the owner read it from: a
 * report, a decision panel, a stage summary), which the ledger offers as a link. Without a project, null.
 */
export function evidenceHref(n: string | number, projectId: number | null | undefined, from?: string): string | null {
  const pid = projectId ?? refProject(`EV-${n}`);
  if (!pid) return null;
  const base = refHref("EV", n, pid)!;
  return from ? `${base}&from=${encodeURIComponent(from)}` : base;
}

const here = () => window.location.pathname + window.location.search;
const EVIDENCE_PATH = /^\/projects\/\d+\/evidence$/;

// What agents cite: evidence as "(EV-1913, EV-2004)", "[EV-12]" or a bare "EV-12", and a task, decision or to-do as
// "T-103" or "[REQ-92]", never part of a longer token or path. Groups 1-4 are set for a task, decision or to-do.
const EV = String.raw`\[?EV-\d+\]?`;
const REF = new RegExp(
  String.raw`\s*\(\s*${EV}(?:\s*[,·]\s*${EV})*\s*\)|\s*\[EV-\d+\](?:\s*[,·]?\s*\[EV-\d+\])*|\s*(?<![\w/-])EV-\d+(?![\w-])` +
    String.raw`|\[(REQ|T|OT)-(\d+)\]|(?<![\w/-])(REQ|T|OT)-(\d+)(?![\w-])`,
  "g",
);
const CODES = /(?<![\w/-])(?:EV|REQ|T|OT)-\d+/g;
const NAMED = { T: "common.ref.task", REQ: "common.ref.decision", OT: "common.ref.todo" } as const;

/**
 * Turn the codes in rendered text into links the owner reads: a task, decision or to-do by its title, and evidence as
 * a numbered mark whose hover says the claim, in the place of the code and the brackets around it. Works on text
 * nodes, so existing links, URLs and code never change.
 */
function linkRefs(html: string, projectId?: number | null): string {
  const tpl = document.createElement("template");
  tpl.innerHTML = html;
  const walker = document.createTreeWalker(tpl.content, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (!n.parentElement?.closest("a, code, pre") && /(EV|REQ|T|OT)-\d/.test((n as Text).data)) nodes.push(n as Text);
  }
  const cited = new Map<string, number>(); // a piece of evidence's mark, numbered in the order the text cites it
  for (const node of nodes) {
    const text = node.data;
    const frag = document.createDocumentFragment();
    let last = 0;
    for (const m of text.matchAll(REF)) {
      frag.append(text.slice(last, m.index));
      const kind = (m[1] ?? m[3]) as keyof typeof NAMED | undefined;
      if (kind) {
        const n = m[2] ?? m[4];
        const a = document.createElement("a");
        a.setAttribute("href", refHref(kind, n, projectId)!);
        a.textContent = refTitle(`${kind}-${n}`) || t(NAMED[kind]);
        frag.append(a);
      } else {
        frag.append(evidenceMarks([...m[0].matchAll(/EV-(\d+)/g)].map((x) => x[1]), cited, projectId));
      }
      last = m.index! + m[0].length;
    }
    if (last === 0) continue;
    frag.append(text.slice(last));
    node.replaceWith(frag);
  }
  return tpl.innerHTML;
}

function evidenceMarks(ids: string[], cited: Map<string, number>, projectId?: number | null): HTMLElement {
  const sup = document.createElement("sup");
  ids.forEach((id, i) => {
    if (!cited.has(id)) cited.set(id, cited.size + 1);
    const n = cited.get(id)!;
    const href = evidenceHref(id, projectId);
    if (i) sup.append(",");
    if (!href) return void sup.append(String(n));
    const a = document.createElement("a");
    a.setAttribute("href", href);
    a.setAttribute("title", refTitle(`EV-${id}`) || t("common.ref.evidence", { n }));
    a.textContent = String(n);
    sup.append(a);
  });
  return sup;
}

/*
 * Agent prose is untrusted model output rendered next to the decision buttons. Only the elements marked produces
 * survive: no style, class or id (agent text could otherwise relabel or hide controls), no image but a build's
 * capture (`servedImage`), and links that leave the console open in a new tab and say so.
 */
const PURIFY = {
  ALLOWED_TAGS: ["p", "br", "hr", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "strong", "em", "del", "sup", "code",
    "pre", "blockquote", "a", "table", "thead", "tbody", "tr", "th", "td", "img", "input"],
  ALLOWED_ATTR: ["href", "title", "alt", "src", "type", "checked", "disabled", "align"],
  FORBID_ATTR: ["style", "class", "id"],
};
const sameOrigin = (url: string) => {
  try {
    return new URL(url, location.href).origin === location.origin;
  } catch {
    return false;
  }
};
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "IMG" && !servedImage(node.getAttribute("src") ?? "", location.href)) node.remove();
  if (node.tagName === "INPUT" && node.getAttribute("type") !== "checkbox") node.remove();
  if (node.tagName === "INPUT") node.setAttribute("disabled", "");
  if (node.tagName === "A" && node.getAttribute("href") && !sameOrigin(node.getAttribute("href")!)) {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
    node.setAttribute("data-ext", "");
  }
});

/** Agents write one fact per line. In a reading view each line is its own paragraph, and a short "Label: value" line shows its label in <strong>, which the sanitizer keeps (it drops class and span). */
const SECTION_FROM = 80;
const readableLines = (html: string) =>
  html.replace(/<p>([\s\S]*?)<\/p>/g, (_, inner: string) => inner.split(/<br\s*\/?>\s*/).map((line) => {
    const labelled = /^([^<>:：]{1,24})[:：]\s+(?=\S)/.exec(line);
    if (!labelled) return `<p>${line}</p>`;
    // cards made before the label was renamed still carry the old one
    const label = labelled[1] === t("decide.label.problem_old") ? t("decide.label.problem") : labelled[1];
    const value = line.slice(labelled[0].length);
    // a label with a long text, or a run of links, is a section of its own; a short fact keeps its label inline
    if (/^<a /.test(value)) {
      const items = value.split(/(?<=<\/a>),\s*(?=<a )/);
      return `<h3>${label}</h3><ul>${items.map((x) => `<li>${x}</li>`).join("")}</ul>`;
    }
    return value.length > SECTION_FROM ? `<h3>${label}</h3><p>${value}</p>` : `<p><strong>${label}</strong> ${value}</p>`;
  }).join(""));

/** A spec's acceptance scenario is one line, "US1.1. **Given** a, **When** b, **Then** c". In a reading view it becomes
 * a small table of three rows, and the writer's English labels are said in the owner's words. */
const SCENARIO = /^(US\d+\.\d+)\.\s*(?:\(([^)]*)\)\s*)?<strong>Given<\/strong>\s*([\s\S]*?),?\s*<strong>When<\/strong>\s*([\s\S]*?),?\s*<strong>Then<\/strong>\s*([\s\S]*)$/;
const specLines = (html: string) =>
  html.replace(/<p>([\s\S]*?)<\/p>/g, (_, inner: string) => inner.split(/<br\s*\/?>\s*/).map((line) => {
    const m = SCENARIO.exec(line);
    if (m) {
      const [, id, note, given, when, then] = m;
      const aside = note ? `<p><em>${/nothing has happened yet/i.test(note) ? t("decide.spec.empty_state") : note}</em></p>` : "";
      return `<h5>${id}</h5>${aside}<table><tbody>`
        + `<tr><th>${t("decide.spec.given")}</th><td>${given}</td></tr>`
        + `<tr><th>${t("decide.spec.when")}</th><td>${when}</td></tr>`
        + `<tr><th>${t("decide.spec.then")}</th><td>${then}</td></tr></tbody></table>`;
    }
    const said = line
      .replace(/<strong>Why (P\d)<\/strong>/, (_x, p: string) => `<strong>${t("decide.spec.why", { p })}</strong>`)
      .replace("<strong>Independent test</strong>", `<strong>${t("decide.spec.test")}</strong>`)
      .replace("<strong>Acceptance scenarios</strong>", `<strong>${t("decide.spec.scenarios")}</strong>`);
    return `<p>${said}</p>`;
  }).join(""));

export function Markdown({ text, projectId, readable = false, spec = false }: { text: string | null | undefined; projectId?: number | null; readable?: boolean; spec?: boolean }) {
  const navigate = useNavigate();
  const panel = usePanel();
  const codes = useMemo(() => [...new Set(text?.match(CODES) ?? [])], [text]);
  const known = useRefs(codes);
  // Sanitize last, so nothing touches the HTML after DOMPurify has checked it.
  const html = useMemo(
    () => DOMPurify.sanitize(linkRefs(readable ? (spec ? specLines : readableLines)(markdownHtml(prose(text ?? ""))) : markdownHtml(prose(text ?? "")), projectId), PURIFY) as string,
    [text, projectId, known, readable, spec],
  );
  // Keep in-app links inside the SPA instead of reloading the page; anything else is a normal (new-tab) link.
  const onClick = (e: MouseEvent<HTMLDivElement>) => {
    const a = (e.target as HTMLElement).closest("a");
    if (!a || e.metaKey || e.ctrlKey || e.shiftKey || !sameOrigin(a.href)) return;
    const u = new URL(a.href);
    e.preventDefault();
    const target = panelTargetOf(u.pathname + u.search);
    if (target) return panel.open(target);
    // evidence opened from a text keeps the way back to the text, its folds and the panel over it included
    if (EVIDENCE_PATH.test(u.pathname) && !u.searchParams.has("from")) u.searchParams.set("from", here());
    navigate(u.pathname + u.search + u.hash);
  };
  return <div className={readable ? "md md-read" : "md"} onClick={onClick} dangerouslySetInnerHTML={{ __html: html }} />;
}

const TONE: Record<string, string> = {
  DONE: "ok", PASS: "ok", pass: "ok", APPROVED: "ok", ANSWERED: "ok", AUTO_APPROVED: "ok", ACCEPTED: "ok",
  DELIVERED: "ok", MERGED: "ok", ACTIVE: "ok", adopt: "ok", EFFECTIVE: "ok", support: "ok",
  RUNNING: "run", REVIEW: "run", executing: "run", IN_DEVELOPMENT: "run", IMPLEMENTING: "run", QA: "run",
  REVIEWING: "run", EVALUATING: "run", PLANNING: "run", EXECUTING: "run", REPORTING: "run", planning: "run",
  REWORK: "warn", rework: "warn", PENDING: "warn", WAITING_HUMAN: "warn", PAUSED: "warn", APPROVAL_REQUIRED: "warn",
  conditional: "warn", defer: "warn", DEFERRED: "warn", CLARIFICATION_REQUIRED: "warn", AWAITING_APPROVAL: "warn",
  PENDING_APPROVAL: "warn", medium: "warn", BUDGET_STOPPED: "warn",
  FAILED: "bad", REJECTED: "bad", reject: "bad", oppose: "bad", DENIED_BY_POLICY: "bad", EVAL_FAILED: "bad",
  escalate: "bad", unreviewed: "bad", high: "bad", critical: "bad", strong_approve: "bad", EXPIRED: "muted",
  CANCELLED: "muted", SUPERSEDED: "muted", ARCHIVED: "muted", low: "muted",
};

export const toneOf = (value: unknown) => TONE[String(value)] ?? "muted";

/**
 * Verification is a filled pill whose tone says how far the server and an independent reviewer got.
 * Evidence kind is drawn differently (outlined tag with a mark, see KindTag), so the two never look alike.
 */
/**
 * The twelve verification statuses in five groups, one tone each: the badge on a row and the confidence bar
 * (components/evidence.tsx) read this one table, so the same status never has two colours.
 */
export const VERIFY_GROUPS: [key: "supported" | "sourced" | "weak" | "unchecked" | "contradicted", tone: string, statuses: string[]][] = [
  ["supported", "ok", ["supported", "reproduced", "quote_found", "computed", "deterministic"]],
  ["sourced", "run", ["url_ok", "derived", "transcribed"]],
  ["weak", "warn", ["weak", "unverifiable"]],
  ["unchecked", "muted", ["unchecked"]],
  ["contradicted", "bad", ["contradicted", "url_unreachable"]],
];
const VERIFY_TONE: Record<string, string> = Object.fromEntries(VERIFY_GROUPS.flatMap(([, tone, codes]) => codes.map((c) => [c, tone])));
export const verifyTone = (status: string) => VERIFY_TONE[status] ?? "muted";

/** `tone` overrides the status's own tone when the row's record says less than its status (evidence.checkTone). */
export function Verification({ value, tone }: { value: string; tone?: string }) {
  return <Badge value={value} tone={tone ?? verifyTone(value)} />;
}

/** Mark per evidence kind: solid = verified, ring = sourced, dashed ring = assumed or inferred. */
function KindMark({ kind }: { kind: string }) {
  const common = { width: 10, height: 10, viewBox: "0 0 10 10", "aria-hidden": true } as const;
  if (kind === "verified_fact") return <svg {...common}><circle cx="5" cy="5" r="4.5" fill="currentColor" /></svg>;
  if (kind === "sourced_claim" || kind === "calculation")
    return <svg {...common}><circle cx="5" cy="5" r="3.75" fill="none" stroke="currentColor" strokeWidth="1.5" /></svg>;
  return (
    <svg {...common}>
      <circle cx="5" cy="5" r="3.75" fill="none" stroke="currentColor" strokeWidth="1.5" strokeDasharray="2 1.6" />
    </svg>
  );
}

export function KindTag({ kind, title }: { kind: string; title?: string }) {
  return (
    <span className={`kind ${kind}`} title={title}>
      <KindMark kind={kind} />
      {label(kind)}
    </span>
  );
}

const TEST_KEY = "ship.showTestProjects";
const testSubs = new Set<() => void>();
const readShowTest = () => {
  try {
    return localStorage.getItem(TEST_KEY) === "1";
  } catch {
    return false;
  }
};

/** Whether [TEST] projects are listed; shared by the sidebar and the dashboard, remembered per browser. */
export function useShowTest(): [boolean, (v: boolean) => void] {
  const [v, setV] = useState(readShowTest);
  useEffect(() => {
    const f = () => setV(readShowTest());
    testSubs.add(f);
    return () => {
      testSubs.delete(f);
    };
  }, []);
  const set = (nv: boolean) => {
    try {
      localStorage.setItem(TEST_KEY, nv ? "1" : "0");
    } catch {
      /* private mode: keep it in memory only */
    }
    setV(nv);
    testSubs.forEach((f) => f());
  };
  return [v, set];
}

/** Status pill. A code is shown as its label in the owner's language. */
export function Badge({ value, tone, raw, title }: { value: ReactNode; tone?: string; raw?: boolean; title?: string }) {
  const t = tone ?? toneOf(value);
  const code = typeof value === "string" ? value : undefined;
  return (
    <span className={`badge ${t}`} title={title}>
      {code && !raw ? label(code) : value}
    </span>
  );
}

export function Agent({ id }: { id: string }) {
  return <span>{agentName(id)}</span>;
}

export function Card({ title, actions, children, className }:
  { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className ?? ""}`}>
      {(title || actions) && (
        <header className="card-h">
          <h2>{title}</h2>
          <div className="card-a">{actions}</div>
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="stat">
      <div className="stat-l">{label}</div>
      <div className="stat-v">{value}</div>
      {sub && <div className="stat-s">{sub}</div>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

/**
 * A load that failed, as a visible error with the way out: the sentence `api()` wrote for the cause (no server, a
 * missing item, a server error), a retry, and for a missing item the way home. Never a stuck "Loading…".
 */
export function LoadError({ error, retry, failure: given }: { error: string; retry?: () => void; failure?: ApiFailure | null }) {
  // a caller that passes only the sentence still gets the right way out for a missing item
  const failure = given ?? (error === t("common.err.missing") ? "missing" : null);
  return (
    <div className="load-error" role="alert">
      <p className="load-error-t">{t("common.load_failed")}</p>
      <p className="load-error-b">{error}</p>
      <div className="row wrap tight">
        {retry && failure !== "missing" && <button type="button" className="small" onClick={retry}>{t("common.retry")}</button>}
        {failure === "missing" && <Link className="btn-ghost small" to="/">{t("common.err.back_home")}</Link>}
      </div>
    </div>
  );
}

/** What to show while `useLive` has no data: the error with its way out, or a quiet loading line. */
export function Loading({ error, retry, failure }: { error?: string | null; retry?: () => void; failure?: ApiFailure | null }) {
  if (error) return <LoadError error={error} retry={retry} failure={failure} />;
  return <div className="loading" aria-busy="true">{t("common.loading")}</div>;
}

/** Grey bars where rows will be, so a loading list keeps its shape. */
export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="skel" aria-busy="true" aria-label={t("common.loading")}>
      {Array.from({ length: rows }, (_, i) => <div key={i} className="skel-row" aria-hidden="true"><p className="skel-bar" /><p className="skel-bar short" /></div>)}
    </div>
  );
}

type Notice = { id: number; text: string; tone: "ok" | "bad" };
const noticeSubs = new Set<(n: Notice) => void>();
let noticeSeq = 0;

/**
 * The result of an owner action, said once in the frame's status region so it outlives the button that ran it (a
 * row that disappears, a banner that closes). The toasts render it; `ActionButton` sends it on success.
 */
export function announce(text: string, tone: "ok" | "bad" = "ok") {
  const n = { id: ++noticeSeq, text, tone };
  noticeSubs.forEach((f) => f(n));
}

export function useAnnouncements(fn: (n: Notice) => void) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    const f = (n: Notice) => ref.current(n);
    noticeSubs.add(f);
    return () => void noticeSubs.delete(f);
  }, []);
}

const SETTLE_MS = 2000;
const PREFER_MS = 250;
const lost = () => {
  const a = document.activeElement;
  return !a || a === document.body || !document.contains(a);
};

/**
 * After an action, focus must land somewhere the owner can read: the element asked for, else the heading of the panel
 * the action ran in (`near` says where it ran), else the page's main area. The element that had focus often goes only
 * when React redraws or fresh data arrives, so this watches for a while instead of checking once. A function `prefer`
 * is read each time, so it can name an element React has yet to draw (a button that comes back after its confirm).
 */
export function settleFocus(prefer?: HTMLElement | null | (() => HTMLElement | null), near?: HTMLElement | null) {
  const from = near ?? (typeof prefer === "function" ? null : prefer) ?? (document.activeElement as HTMLElement | null);
  const home = from?.closest(".side-panel, .wk-detail")?.querySelector<HTMLElement>("h2[tabindex]") ?? null;
  const t0 = performance.now();
  const check = () => {
    const now = performance.now();
    if (lost()) {
      const want = typeof prefer === "function" ? prefer() : prefer;
      if (want && document.contains(want)) want.focus({ preventScroll: true });
      if (lost() && (now - t0 > PREFER_MS || !prefer)) {
        (home && document.contains(home) ? home : document.getElementById("main"))?.focus({ preventScroll: true });
      }
    }
    if (now - t0 < SETTLE_MS) requestAnimationFrame(check);
  };
  queueMicrotask(check);
}

/**
 * What an action will do, said before it does it, under its row rather than in a dialog. The first focus goes to
 * the safe button, so the key that opened the box cannot also confirm it; Escape closes it and focus returns to
 * what opened it. Name the confirm by its action ("Erase the record") and the other by what keeping means.
 */
export function ConfirmBox({ children, confirmLabel, cancelLabel, busy, error, onConfirm, onCancel, tone }: {
  children: ReactNode; confirmLabel: string; cancelLabel?: string; busy: boolean; error: string | null;
  onConfirm: () => void; onCancel: () => void; tone?: "danger";
}) {
  const safe = useRef<HTMLButtonElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  useEffect(() => {
    opener.current = document.activeElement as HTMLElement | null;
    safe.current?.focus();
    return () => {
      const back = opener.current;
      if (back && back !== document.body && document.contains(back)) back.focus();
      else settleFocus();
    };
  }, []);
  return (
    <div className="confirm-box" role="group" onKeyDown={(e) => e.key === "Escape" && !busy && (e.stopPropagation(), onCancel())}>
      <div className="confirm-text">{children}</div>
      {error && <p className="act-msg bad" role="alert">{error}</p>}
      <div className="row wrap">
        <button ref={safe} type="button" className="btn-ghost" disabled={busy} onClick={onCancel}>{cancelLabel ?? t("common.keep")}</button>
        <button type="button" className={tone === "danger" ? "danger-solid" : "btn-primary"} disabled={busy} onClick={onConfirm}>
          {busy ? t("common.working") : confirmLabel}
        </button>
      </div>
    </div>
  );
}

const NAMED_TOO = { ...NAMED, P: "common.ref.project", DEC: "common.ref.decision", J: "common.ref.build" } as const;

/**
 * A task, decision, to-do, project or build named where its code would be, linked by its title (a project by its
 * name, a build by its spec's). Text that is not such a code shows as it is.
 */
export function RefLink({ code, projectId }: { code: string; projectId?: number | null }) {
  const m = /^(REQ|T|OT|P|DEC|J)-(\d+)$/.exec(code);
  useRefs(m ? [code] : []);
  if (!m) return <>{code}</>;
  const kind = m[1] as keyof typeof NAMED_TOO;
  const said = refTitle(code) || t(NAMED_TOO[kind]);
  const href = refHref(kind, m[2], projectId);
  return href ? <ItemLink to={href} title={said}>{said}</ItemLink> : <span title={said}>{said}</span>;
}

/**
 * Evidence as links into the project's ledger, each by its claim, or (`marks`) by its number with the claim on hover,
 * for a line that already says what it claims. Accepts 12, "12" or "EV-12". A screen that does not know the project
 * (a stage summary) leaves `projectId` out: the reference lookup names it.
 */
export function EvRefs({ ids, projectId, marks }: { ids: (string | number)[]; projectId?: number | null; marks?: boolean }) {
  const { pathname, search } = useLocation();
  const ns = (ids ?? []).map((x) => String(x).replace(/^EV-/i, ""));
  useRefs(ns.map((n) => `EV-${n}`));
  if (!ns.length) return <>-</>;
  return (
    <>
      {ns.map((n, i) => {
        const said = refTitle(`EV-${n}`) || t("common.ref.evidence", { n: i + 1 });
        const href = evidenceHref(n, projectId, pathname + search);
        const text = marks ? String(i + 1) : said;
        return (
          <span key={`${n}-${i}`}>
            {i > 0 && (marks ? ", " : " · ")}
            {href ? <Link to={href} className="evref" title={said} aria-label={marks ? said : undefined}>{text}</Link> : <span title={said}>{text}</span>}
          </span>
        );
      })}
    </>
  );
}

/** One way to write a date on an owner screen: "Nov 12" (with the year when it is not this year). */
export function shortDate(v?: string | Date | null) {
  if (!v) return "";
  const d = new Date(v);
  return d.toLocaleDateString(locale(), { month: "short", day: "numeric", ...(d.getFullYear() === new Date().getFullYear() ? {} : { year: "numeric" }) });
}

/** The exact moment, spelled the same way everywhere: "Nov 6, 4:55 PM". */
export function when(v?: string | null) {
  if (!v) return "";
  const d = new Date(v);
  return `${shortDate(d)}, ${d.toLocaleTimeString(locale(), { hour: "numeric", minute: "2-digit" })}`;
}

const WEEK = 7 * 86400;

/** The recent past and the near future in relative words ("3 minutes ago", "tomorrow"); a date further off than a week
 * is the date ("Nov 12"). The exact time goes in the element's title. */
export function ago(v?: string | null) {
  if (!v) return "";
  const rtf = new Intl.RelativeTimeFormat(locale(), { numeric: "auto" });
  const s = (new Date(v).getTime() - Date.now()) / 1000;
  const a = Math.abs(s);
  if (a < 60) return rtf.format(Math.round(s), "second");
  if (a < 3600) return rtf.format(Math.round(s / 60), "minute");
  if (a < 86400) return rtf.format(Math.round(s / 3600), "hour");
  if (a < WEEK) {
    // whole calendar days, so "yesterday" and "2 days ago" agree with the day headings the activity record uses
    const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    return rtf.format(Math.round((day(new Date(v)) - day(new Date())) / 86_400_000), "day");
  }
  return shortDate(v);
}

export function Time({ v }: { v?: string | null }) {
  if (!v) return null;
  return <time dateTime={v} title={when(v)}>{ago(v)}</time>;
}

export const usd = (n?: number | null) => `$${(n ?? 0).toFixed(2)}`;
export const tid = (id: number) => `T-${id}`;
export const rid = (id: number) => `REQ-${id}`;

/**
 * A button that runs an owner action and reports its outcome (no browser dialog). With `confirm`, the first click
 * arms it: the confirm is named by its action, the other button keeps things as they are and takes the first focus.
 * `done` is said in place and in the frame's status region, so it survives the button going away; focus never falls
 * to the body (the button again, else the keep button, else the page).
 */
export function ActionButton({ run, children, confirm, className, done, disabled, title, keep, what, why }: {
  run: () => Promise<unknown>; children: ReactNode; confirm?: ReactNode; className?: string; done?: string;
  disabled?: boolean; title?: string; keep?: string;
  /** What confirming does, shown with the confirm pair and read with it; use it instead of a tooltip. */
  what?: ReactNode;
  /** Why the button is off, shown beside it and read with it while `disabled` holds. */
  why?: string;
}) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const alive = useRef(true);
  const main = useRef<HTMLButtonElement>(null);
  const safe = useRef<HTMLButtonElement>(null);
  const box = useRef<HTMLSpanElement>(null);
  const whatId = useId();
  const whyId = useId();
  const off = !!disabled && !busy && !!why;
  useEffect(() => () => {
    alive.current = false;
  }, []);
  useEffect(() => {
    if (armed) safe.current?.focus();
  }, [armed]);
  async function go() {
    setBusy(true);
    setMsg(null);
    try {
      await run();
      if (done) announce(done, "ok");
      if (alive.current) setMsg(done ? { ok: true, text: done } : null);
    } catch (e: any) {
      if (alive.current) setMsg({ ok: false, text: e.message ?? String(e) });
    } finally {
      if (alive.current) {
        setBusy(false);
        setArmed(false);
      }
      // the button comes back once React redraws (it is not drawn while the confirm pair shows), or goes for good
      settleFocus(() => main.current, box.current);
    }
  }
  const disarm = () => {
    setArmed(false);
    settleFocus(() => main.current, box.current);
  };
  return (
    <span className="act" ref={box}>
      {armed ? (
        <span className="act-armed" role="group" aria-describedby={what ? whatId : undefined}
          onKeyDown={(e) => e.key === "Escape" && !busy && (e.stopPropagation(), disarm())}>
          {what && <span id={whatId} className="act-what">{what}</span>}
          <span className="row tight wrap">
            <button ref={safe} type="button" className="btn-ghost" disabled={busy} onClick={disarm}>{keep ?? t("common.keep")}</button>
            <button type="button" className={className === "danger" ? "danger-solid" : "primary"} disabled={busy} onClick={go}>
              {busy ? t("common.working") : confirm}
            </button>
          </span>
        </span>
      ) : (
        <>
          {off && <span id={whyId} className="act-why">{why}</span>}
          <button ref={main} type="button" className={className} disabled={busy || disabled} title={title}
            aria-describedby={off ? whyId : undefined} onClick={() => (confirm ? setArmed(true) : go())}>
            {busy ? t("common.working") : children}
          </button>
        </>
      )}
      {msg && <span className={`act-msg ${msg.ok ? "ok" : "bad"}`} role={msg.ok ? "status" : "alert"}>{msg.text}</span>}
    </span>
  );
}

/** A link to a place on a screen lands there: the place scrolls in, and a field there takes focus so typing starts at once. */
export function landOn(id: string, block: ScrollLogicalPosition = "start") {
  const el = document.getElementById(id);
  if (!el) return;
  el.scrollIntoView({ block });
  if (el.matches("input, textarea, select")) el.focus({ preventScroll: true });
}

export const pct = (n: number) => `${Math.round(n * 100)}%`;
export const num = (n?: number | null) => (n ?? 0).toLocaleString(locale());
export const dur = (ms?: number | null) => {
  const s = Math.round((ms ?? 0) / 1000);
  return s < 60 ? t("common.duration.s", { s }) : s < 3600 ? t("common.duration.ms", { m: Math.floor(s / 60), s: s % 60 })
    : t("common.duration.hm", { h: Math.floor(s / 3600), m: Math.floor((s % 3600) / 60) });
};

/** Long agent prose shows its start; the rest is one click away, so the decision never sinks below it. */
export function Folded({ text, projectId, limit = 900, readable = false }: { text: string; projectId?: number | null; limit?: number; readable?: boolean }) {
  const [open, setOpen] = useState(false);
  const long = (text ?? "").length > limit;
  return (
    <div className={`folded ${long && !open ? "is-folded" : ""}`}>
      <Markdown text={text} projectId={projectId} readable={readable} />
      {long && <button className="link small" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? t("common.fold") : t("common.unfold_all", { n: text.length.toLocaleString(locale()) })}</button>}
    </div>
  );
}

const LEAD_MAX = 120;

/** A heading for a long text: its first sentence without markdown, clipped, and whether the full text needs a fold. */
export function leadOf(text: string): [string, boolean] {
  const plain = (text ?? "").replace(/\*\*|__|`/g, "").trim();
  const first = plain.match(/^[\s\S]+?[.!?。](?=\s|$)/)?.[0] ?? plain;
  const lead = first.length > LEAD_MAX ? `${first.slice(0, LEAD_MAX - 1).trimEnd()}…` : first;
  return [lead, lead !== plain];
}

/** The one-line conclusion of a markdown section: its first sentence, from its first line of text that is not a
 * heading, with list and quote marks dropped. */
export function leadLine(md: string): string {
  const line = (md ?? "").split("\n").filter((l) => !/^\s*#/.test(l))
    .map((l) => l.replace(/^\s*(?:[-*+>]|\d+[.)])\s+/, "").trim()).find(Boolean) ?? "";
  return leadOf(line)[0];
}
