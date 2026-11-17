import { ReactNode, RefObject, useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { Row, useLive } from "../api";
import { cleanName } from "../business";
import { t } from "../i18n";
import { actionLabel, cycleTarget, elsewhereCounts, ElsewhereRow, metaText, panelTarget, Today, TodayItem } from "../today";
import { PanelLink, PanelTarget } from "./panel";
import { Badge } from "./ui";
import "./today.css";

const TYPING = "input, textarea, select, [contenteditable]";

/** j and k walk the rows' buttons, so Enter on the focused one follows its link. */
function useRowKeys(list: RefObject<HTMLOListElement | null>) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.key !== "j" && e.key !== "k") || e.ctrlKey || e.metaKey || e.altKey) return;
      if ((e.target as HTMLElement).closest(`${TYPING}, .side-panel`)) return;
      const lists = Array.from(document.querySelectorAll(".tlist2"));
      if ((lists.find((l) => l.contains(document.activeElement)) ?? lists[0]) !== list.current) return;
      const go = Array.from(list.current?.querySelectorAll<HTMLElement>("a.row-go") ?? []);
      if (!go.length) return;
      const at = go.indexOf(document.activeElement as HTMLElement);
      e.preventDefault();
      go[e.key === "j" ? Math.min(go.length - 1, at + 1) : Math.max(0, at - 1)].focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [list]);
}

export function RowList({ current, count, children }: { current?: string | null; count: number; children: ReactNode }) {
  const list = useRef<HTMLOListElement>(null);
  useRowKeys(list);
  useEffect(() => {
    if (current) list.current?.querySelector(`[data-ref="${CSS.escape(current)}"]`)?.scrollIntoView({ block: "center" });
  }, [current, count]);
  return <ol className="tlist2" ref={list}>{children}</ol>;
}

/** A tag is its text, red by default; a `[text, tone]` pair picks the tone (red is for failure, amber for waiting). */
export type RowTag = string | [string, string];

export function RowItem({ rowRef, on, label, tags = [], title, meta, to, target, button }: {
  rowRef: string; on: boolean; label?: string; tags?: RowTag[]; title: string; meta: string; to: string;
  target: PanelTarget | null; button: string;
}) {
  const aria = `${button}: ${title}`;
  return (
    <li className={`tr ${on ? "on" : ""}`} data-ref={rowRef} aria-current={on ? "true" : undefined}>
      <div className="tr-body">
        <p className="tr-title">
          {label && <Badge value={label} tone="info" raw />}
          {tags.map((x) => {
            const [text, tone] = typeof x === "string" ? [x, "bad"] : x;
            return <span key={text}> <Badge value={text} tone={tone} raw /></span>;
          })}
          {" "}<span title={title}>{title}</span>
        </p>
        <p className="tr-meta">{meta}</p>
      </div>
      {target
        ? <PanelLink className="btn outline row-go" to={to} target={target} label={aria}>{button}</PanelLink>
        : <Link className="btn outline row-go" to={to} aria-label={aria}>{button}</Link>}
    </li>
  );
}

/** Company work waits on this item. It is the owner's turn, not a failure, so it is amber. */
export const waitingTag = (): [string, string] => [t("home.list.waiting"), "warn"];

export function TodayList({ items, current }: { items: TodayItem[]; current?: string | null }) {
  const now = new Date();
  return (
    <RowList current={current} count={items.length}>
      {items.map((it) => {
        const cycle = cycleTarget(it);
        return (
          <RowItem key={it.ref} rowRef={it.ref} on={it.ref === current} label={it.label} tags={it.tier === "blocking" ? [waitingTag()] : []}
            title={it.title} meta={metaText(it, now)} to={cycle ?? it.link} target={panelTarget(it)}
            button={cycle ? t("home.list.see_cycle") : actionLabel(it.action)} />
        );
      })}
    </RowList>
  );
}

export function SkeletonRows({ n = 3 }: { n?: number }) {
  return (
    <ol className="tlist2" aria-busy="true" aria-label={t("home.list.loading")}>
      {Array.from({ length: n }, (_, i) => (
        <li key={i} className="tr skel" aria-hidden="true"><div className="tr-body"><p className="skel-bar" /><p className="skel-bar short" /></div></li>
      ))}
    </ol>
  );
}

/**
 * Other businesses that have something waiting for the owner, one link per business that switches to it on `to`.
 * `what` picks the counts that matter on this screen; nothing renders when no other business waits.
 */
export function ElsewhereLine({ today, what, to }: { today: Today | null; what: "decisions" | "todos" | "both"; to: string }) {
  // a failure waits on the owner like a decision: the home and the Decisions page (its Alerts tab) both say it
  const shown = (b: ElsewhereRow) => {
    const n = elsewhereCounts(b);
    return { decisions: what !== "todos" ? n.decisions : 0, todos: what !== "decisions" ? n.todos : 0, failed: what !== "todos" ? n.failed : 0 };
  };
  const rows = (today?.elsewhere ?? []).filter((b) => Object.values(shown(b)).some((n) => n > 0));
  if (!rows.length) return null;
  const counts = (b: ElsewhereRow) => {
    const n = shown(b);
    return [
      n.decisions > 0 ? t("home.elsewhere.decisions", { n: n.decisions }) : null,
      n.todos > 0 ? t("home.elsewhere.todos", { n: n.todos }) : null,
      what !== "todos" && (b.blocking ?? 0) > 0 ? t("home.elsewhere.blocking", { n: b.blocking! }) : null,
      n.failed > 0 ? t("home.elsewhere.failed", { n: n.failed }) : null,
    ].filter(Boolean).join(" · ");
  };
  return (
    <p className="elsewhere" role="status">
      <span className="sdot t-warn" aria-hidden="true" />
      <span>{t("home.elsewhere.lead")}</span>
      {rows.map((b) => (
        <span key={b.business_id} className="elsewhere-b">
          <Link to={`${to}${to.includes("?") ? "&" : "?"}b=${b.business_id}`} aria-label={t("home.elsewhere.switch", { name: cleanName(b.name) })}>
            {cleanName(b.name)}
          </Link>
          <span className="muted"> {counts(b)}</span>
        </span>
      ))}
    </p>
  );
}

/** The kickoff chat while the CEO writes its next question, or after the owner stopped it: not an item of the owner's
 * list (the server lists it only on the owner's turn), so it is one quiet line with the way back to it. */
export function KickoffLine({ business }: { business: { id: number; brief_version: number | null } }) {
  // A chat is only live before the plan is approved; asking after that answers 404 for a business that never had one.
  const { data } = useLive<Row>(business.brief_version == null ? `/businesses/${business.id}/kickoff` : null,
    ["kickoff_sessions", "kickoff_turns"]);
  const status = data?.session?.status;
  if (status !== "OPEN" && status !== "ABANDONED") return null;
  return (
    <p className="owner-line">
      <span className="sdot t-run" aria-hidden="true" />
      {t(status === "OPEN" ? "home.kickoff.writing" : "home.kickoff.abandoned")}{" "}
      <Link to={`/kickoff/${data!.session.id}`}>{t(status === "OPEN" ? "home.kickoff.open" : "home.kickoff.reopen")}</Link>
    </p>
  );
}

/** A failed load says so in one line and offers the retry; it never blanks the page. */
export function QuietError({ text, retry }: { text: string; retry: () => void }) {
  return (
    <p className="quiet-error" role="alert">
      {text} <button className="link" onClick={retry}>{t("common.retry")}</button>
    </p>
  );
}
