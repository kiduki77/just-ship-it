import { ReactNode, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { shortDate } from "../components/ui";
import { t } from "../i18n";

/** Runs an owner action and keeps its state: working, failed with the server's own sentence, or done. */
export function useDo() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);
  useEffect(() => () => {
    alive.current = false;
  }, []);
  const run = useCallback(async (fn: () => Promise<unknown>): Promise<boolean> => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      return true;
    } catch (e: any) {
      if (alive.current) setError(e.message ?? String(e));
      return false;
    } finally {
      if (alive.current) setBusy(false);
    }
  }, []);
  return { busy, error, run };
}

export function Fail({ text }: { text: string | null }) {
  return text ? <p className="biz-fail" role="alert">{text}</p> : null;
}

/** A tab's loading and failure state: the page never blanks for one list that did not load. */
export function Loaded({ error, retry, ready, rows = 2, children }: {
  error: string | null; retry: () => void; ready: boolean; rows?: number; children: ReactNode;
}) {
  if (ready) return <>{children}</>;
  if (error) {
    return (
      <p className="quiet-error" role="alert">
        {t("business.load_failed")} <button className="link" onClick={retry}>{t("common.retry")}</button>
      </p>
    );
  }
  return (
    <div aria-busy="true" aria-label={t("common.loading")}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="biz-skel" aria-hidden="true"><p className="skel-bar" /><p className="skel-bar short" /></div>
      ))}
    </div>
  );
}

/** The shared confirm box (safe button first); Bets, Ideas and People import it from here. */
export { ConfirmBox } from "../components/ui";

export function Code({ children, title }: { children: ReactNode; title?: string }) {
  return <span className="code" title={title}>{children}</span>;
}

export const dayText = (v: string | null | undefined) => shortDate(v);

/** A label and its value, read as one line of a definition list. */
export function Fact({ k, children }: { k: string; children: ReactNode }) {
  return <div className="biz-fact"><dt>{k}</dt><dd>{children}</dd></div>;
}

/** Long text cut to three lines, with a way to read the rest. Line breaks the author typed are kept. */
export function Clamp({ text, lines = 3 }: { text: string; lines?: number }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useState(false);
  const [over, setOver] = useState(false);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && !open) setOver(el.scrollHeight > el.clientHeight + 1);
  }, [text, open]);
  return (
    <>
      <span ref={ref} className={`biz-clamp ${open ? "" : "on"}`} style={{ WebkitLineClamp: lines }}>{text}</span>
      {(over || open) && (
        <button type="button" className="link small biz-more" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? t("common.fold") : t("business.unfold")}</button>
      )}
    </>
  );
}

/** One step of a left-to-right ladder: its name, the number, and one line on what it means or why it is unknown. */
export type Step = { key: string; label: string; value: ReactNode; note?: ReactNode; state?: "reached" | "current" | "ahead" };

export function Steps({ steps, label }: { steps: Step[]; label: string }) {
  return (
    <ol className="biz-steps" aria-label={label}>
      {steps.map((s) => (
        <li key={s.key} className={`biz-step ${s.state ? `s-${s.state}` : ""}`} aria-current={s.state === "current" ? "step" : undefined}>
          <p className="biz-step-l">{s.label}</p>
          <p className="biz-step-v">{s.value}</p>
          {s.note && <p className="biz-step-n">{s.note}</p>}
        </li>
      ))}
    </ol>
  );
}

export const TAB_KEYS = ["overview", "bets", "numbers", "people", "ideas", "brief", "look"] as const;
export type TabKey = (typeof TAB_KEYS)[number];
const isTab = (v: string | null): v is TabKey => TAB_KEYS.includes(v as TabKey);

/**
 * The tab lives in `?tab=` next to `?b=`, so a link the server writes (`/business?b=5`) lands on the overview and a
 * link into a tab survives a reload. `href` keeps every other parameter.
 */
export function useBizTabs() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const raw = params.get("tab");
  const tab: TabKey = isTab(raw) ? raw : "overview";
  const href = useCallback((t: TabKey, hash?: string, extra?: Record<string, string>) => {
    const next = new URLSearchParams(params);
    next.delete("ask");
    for (const [k, v] of Object.entries(extra ?? {})) next.set(k, v);
    if (t === "overview") next.delete("tab");
    else next.set("tab", t);
    if (t !== "people") next.delete("stage");
    const q = next.toString();
    return `?${q}${hash ? `#${hash}` : ""}`;
  }, [params]);
  const go = useCallback((t: TabKey, hash?: string) => navigate(href(t, hash)), [navigate, href]);
  return { tab, href, go };
}
