import { createContext, ReactNode, useContext, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { t } from "../i18n";

export type PanelTarget = { key: "req" | "task"; id: number };

/** Address parameter per panel. Owner to-dos use `?ot=`: `?task=` belongs to project tasks (T-n) on the work pages. */
const PARAM = { req: "req", task: "ot" } as const;

export function usePanel() {
  const [params, setParams] = useSearchParams();
  const { pathname } = useLocation();
  const req = Number(params.get("req")) || null;
  // links written before `?ot=` (notifications, agent documents) said /my-tasks?task=n
  const legacy = pathname.startsWith("/my-tasks") ? params.get("task") : null;
  const ot = Number(params.get("ot") ?? legacy) || null;
  const current: PanelTarget | null = ot ? { key: "task", id: ot } : req ? { key: "req", id: req } : null;
  const without = () => {
    const next = new URLSearchParams(params);
    next.delete("req");
    next.delete("ot");
    if (legacy) next.delete("task");
    return next;
  };
  return {
    current,
    open(t: PanelTarget, replace = false) {
      const next = without();
      next.set(PARAM[t.key], String(t.id));
      setParams(next, { replace });
    },
    close() {
      setParams(without(), { replace: true });
    },
  };
}

/**
 * The panel an in-app address names, if it names one: a decision (`/inbox?req=n`) or an owner to-do (`/my-tasks?ot=n`,
 * or the older `?task=n`). Decisions and to-dos open over the page the owner is on, from any link, so the page and its
 * tab, filter and selection stay where they were; only the list pages themselves are their home.
 */
export function panelTargetOf(to: string): PanelTarget | null {
  let u: URL;
  try {
    u = new URL(to, "http://console.invalid");
  } catch {
    return null;
  }
  if (u.origin !== "http://console.invalid") return null;
  const n = (k: string) => Number(u.searchParams.get(k)) || null;
  if (u.pathname === "/inbox" && n("req")) return { key: "req", id: n("req")! };
  if (u.pathname === "/my-tasks" && (n("ot") ?? n("task"))) return { key: "task", id: (n("ot") ?? n("task"))! };
  return null;
}

/** A link to any in-app address: a decision or to-do opens as the panel over this page, anything else navigates. */
export function ItemLink({ to, className, label, title, children }: {
  to: string; className?: string; label?: string; title?: string; children: ReactNode;
}) {
  const target = panelTargetOf(to);
  if (target) return <PanelLink to={to} target={target} className={className} label={label} title={title}>{children}</PanelLink>;
  return <Link to={to} className={className} aria-label={label} title={title}>{children}</Link>;
}

export function PanelLink({ target, to, className, label, title, children, current }: {
  target: PanelTarget; to: string; className?: string; label?: string; title?: string; children: ReactNode; current?: boolean;
}) {
  const { open } = usePanel();
  return (
    <Link to={to} className={className} aria-label={label} title={title} aria-current={current ? "true" : undefined} onClick={(e) => {
      if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      open(target);
    }}>
      {children}
    </Link>
  );
}

export function safeUrl(url: unknown): string | null {
  return typeof url === "string" && /^https?:\/\//i.test(url.trim()) ? url.trim() : null;
}

export function ExtLink({ href, children, className }: { href: unknown; children: ReactNode; className?: string }) {
  const url = safeUrl(href);
  if (!url) return <span className={className}>{children}</span>;
  return <a className={className} href={url} target="_blank" rel="noopener noreferrer">{children}</a>;
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const box = document.createElement("textarea");
    box.value = text;
    box.setAttribute("readonly", "");
    box.style.position = "fixed";
    box.style.opacity = "0";
    document.body.append(box);
    box.select();
    const ok = document.execCommand("copy");
    box.remove();
    return ok;
  }
}

/** Copies `text`. `what` names it for a screen reader ("Copy the command"); `label` replaces the visible "Copy" with
 * words that already name it ("Copy all steps"), and then is the button's whole name. */
export function CopyButton({ text, what, label }: { text: string; what: string; label?: string }) {
  const [state, setState] = useState<"idle" | "done" | "failed">("idle");
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  return (
    <button type="button" className="btn-copy" aria-label={label ? undefined : t("decide.side.copy_what", { what })} onClick={async () => {
      setState((await copyText(text)) ? "done" : "failed");
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setState("idle"), 2000);
    }}>
      <span aria-live="polite">{state === "done" ? t("decide.side.copied") : state === "failed" ? t("decide.side.copy_failed") : label ?? t("decide.side.copy")}</span>
    </button>
  );
}

const FootSlot = createContext<HTMLElement | null>(null);

export function PanelFooter({ children }: { children: ReactNode }) {
  const slot = useContext(FootSlot);
  return slot ? createPortal(children, slot) : null;
}

/**
 * The panel beside the page. It does not block the page behind it: the owner can open another row while it is open.
 * Focus moves to its heading on open and returns to the row that opened it on close.
 */
/** `item` names what the panel shows, so the heading takes focus again when it changes; the title is what the owner reads. */
export function SidePanel({ item, title, onClose, children }: {
  item?: string; title: string; onClose: () => void; children: ReactNode;
}) {
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  useEffect(() => {
    opener.current = document.activeElement as HTMLElement | null;
    document.body.classList.add("has-panel");
    return () => {
      document.body.classList.remove("has-panel");
      // back to the row that opened it; a panel opened by its address (a reload, a link) has none, so the page takes it
      const back = opener.current;
      if (back && back !== document.body && document.contains(back)) back.focus();
      else document.getElementById("main")?.focus();
    };
  }, []);
  useEffect(() => {
    heading.current?.focus();
  }, [item]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <aside className="side-panel" role="dialog" aria-modal="false" aria-labelledby="sp-title">
      <header className="sp-head">
        <div className="sp-head-main">
          <h2 id="sp-title" ref={heading} tabIndex={-1}>{title}</h2>
        </div>
        <button type="button" className="sp-close" aria-label={t("decide.side.close")} onClick={onClose}>
          <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
            <path d="M3.5 3.5 12.5 12.5M12.5 3.5 3.5 12.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          </svg>
        </button>
      </header>
      <FootSlot.Provider value={slot}>
        <div className="sp-scroll">{children}</div>
      </FootSlot.Provider>
      <footer className="sp-foot" ref={setSlot} />
    </aside>
  );
}

export function PanelSkeleton() {
  return (
    <div className="sp-scroll" aria-busy="true" aria-label={t("common.loading")}>
      <p className="skel-bar" /><p className="skel-bar short" /><p className="skel-bar" />
    </div>
  );
}
