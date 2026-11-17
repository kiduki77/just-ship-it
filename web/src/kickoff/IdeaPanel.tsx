import { useEffect, useRef, useState } from "react";
import { post, useLive } from "../api";
import { useWorker } from "../components/frame";
import { Chip, Hint, Section, Tone } from "../components/kit";
import { ExtLink, safeUrl } from "../components/panel";
import { ActionButton } from "../components/ui";
import { PlainKey, t } from "../i18n";
import "./idea-panel.css";

/*
 * The idea review panel (아이디어 평가단): three critics who look for why the idea may not work now. It advises and
 * never blocks, so it reads as calm notes beside the work, with the warn tone kept for the one level that earns it.
 */

/** The level words the critics write and the rows store, an enum the server keeps as it is, read here by name. */
const LEVEL_OF = { 경고: "warning", 주의: "caution", 참고: "note" } as const;
type Level = keyof typeof LEVEL_OF;
type LevelName = (typeof LEVEL_OF)[Level];
/** found: the page holds the quote. blocked: the site refused the check. none: no link. Older rows have no check. */
type Check = "found" | "blocked" | "none";
type Evidence = { label: string; url: string | null; quote?: string; check?: Check };
type IdeaWarning = { title: string; why: string; level: Level; to_overcome: string; evidence: Evidence[]; dropped?: number };
type Similar = { name: string; url: string; how_it_differs: string; check?: Check };
type Critique = { headline: string; warnings: IdeaWarning[]; similar: Similar[] };
type Lens = {
  key: string; title: string; status: "queued" | "done" | "failed"; result: Critique | null; note: string | null;
  created_at: string; finished_at: string | null;
};
type IdeaReviewView = { business_id: number; round: number; can_rerun: boolean; lenses: Lens[] };

/** Worst first, the order the counts read in. */
const LEVELS = [
  { name: "warning", tone: "warn", word: "kickoff.panel.level.warning", count: "kickoff.panel.count.warning" },
  { name: "caution", tone: "info", word: "kickoff.panel.level.caution", count: "kickoff.panel.count.caution" },
  { name: "note", tone: "muted", word: "kickoff.panel.level.note", count: "kickoff.panel.count.note" },
] as const satisfies readonly { name: LevelName; tone: Tone; word: PlainKey; count: string }[];
const levelOf = (w: IdeaWarning) => LEVELS.find((l) => l.name === LEVEL_OF[w.level]) ?? LEVELS[2];

function useIdeaReview(businessId: number | undefined) {
  return useLive<IdeaReviewView>(businessId === undefined ? null : `/businesses/${businessId}/idea-review`, ["idea_reviews"]);
}

function counts(c: Critique): string {
  return LEVELS.map((l) => [l, c.warnings.filter((w) => levelOf(w) === l).length] as const)
    .filter(([, n]) => n > 0).map(([l, n]) => t(l.count, { n })).join(" · ");
}

/** `held` is the worker's word while it is off: a queued critic is not reviewing then. */
function lensState(l: Lens, held: string | null): string {
  if (l.status === "queued") return held ?? t("kickoff.panel.reviewing");
  if (l.status === "failed") return t("kickoff.panel.failed");
  return l.result ? counts(l.result) : "";
}

function Rerun({ view, reload }: { view: IdeaReviewView; reload: () => void }) {
  return (
    <ActionButton
      className="btn-ghost"
      disabled={!view.can_rerun}
      title={t(view.can_rerun ? "kickoff.panel.rerun_title" : "kickoff.panel.busy")}
      run={async () => {
        await post(`/businesses/${view.business_id}/idea-review`, {});
        reload();
      }}
    >
      {t(view.round ? "kickoff.panel.rerun" : "kickoff.panel.run")}
    </ActionButton>
  );
}

/** A source as the panel lists it. With no link it is the critic's guess, named here in the owner's language: the
 * critic's own label for it came out in either language. */
function Source({ e }: { e: Evidence }) {
  if (!e.url) return <span className="muted">{t("kickoff.panel.no_source")}</span>;
  // The critic wrote the address: only a web address becomes a link, and anything else is read as text.
  const url = safeUrl(e.url);
  return (
    <>
      {url ? <a href={url} target="_blank" rel="noopener noreferrer" title={e.check === "found" ? e.quote : undefined}>{e.label || url}</a>
        : <span>{e.label || e.url}</span>}
      {e.check !== "found" && <span className="muted"> · {t("kickoff.panel.unchecked")}</span>}
    </>
  );
}

function WarningItem({ w }: { w: IdeaWarning }) {
  const dropped = w.dropped ?? 0;
  const level = levelOf(w);
  const evidence = [...w.evidence.filter((e) => e.url), ...w.evidence.filter((e) => !e.url).slice(0, 1)];
  return (
    <li className="ip-w">
      <p className="ip-w-top"><Chip tone={level.tone}>{t(level.word)}</Chip><span className="ip-w-t">{w.title}</span></p>
      <p className="ip-why">{w.why}</p>
      <p className="ip-over">
        <span className="ip-over-l">{t("kickoff.panel.overcome")}</span>{" "}
        {w.to_overcome || <span className="muted">{t("kickoff.panel.no_overcome")}</span>}
      </p>
      {(evidence.length > 0 || dropped > 0) && (
        <ul className="ip-src" aria-label={t("kickoff.panel.sources")}>
          {evidence.map((e, i) => <li key={i}><Source e={e} /></li>)}
          {dropped > 0 && <li className="muted">{t("kickoff.panel.dropped", { n: dropped })}</li>}
        </ul>
      )}
    </li>
  );
}

function LensBody({ l }: { l: Lens }) {
  const { down } = useWorker();
  if (l.status === "queued") {
    return (
      <div className="ip-wait">
        <span className={`think ${down ? "stopped" : ""}`} aria-hidden="true" />
        <p className={`small ${down ? "bad-t" : "muted"}`}>{down ? t("kickoff.panel.worker_off") : l.note ?? t("kickoff.panel.waiting")}</p>
      </div>
    );
  }
  if (l.status === "failed" || !l.result) return <p className="small muted">{l.note ?? t("kickoff.panel.lens_failed")}</p>;
  const c = l.result;
  return (
    <>
      <p className="ip-head">{c.headline}</p>
      <ol className="ip-ws">{c.warnings.map((w, i) => <WarningItem key={i} w={w} />)}</ol>
      {c.similar.length > 0 && (
        <details className="ip-sim">
          <summary>{t("kickoff.panel.similar")} <span className="num">{c.similar.length}</span></summary>
          <ul>
            {c.similar.map((x, i) => (
              <li key={i}>
                <ExtLink href={x.url}>{x.name}</ExtLink>
                {x.check !== "found" && <span className="muted"> · {t("kickoff.panel.unchecked")}</span>}{" "}
                <span className="muted">{x.how_it_differs}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}

function Lenses({ view }: { view: IdeaReviewView }) {
  const { label: held } = useWorker();
  return (
    <div className="ip-lenses">
      {view.lenses.map((l) => (
        <section key={l.key} className="ip-lens" aria-label={l.title} aria-busy={l.status === "queued"}>
          <header className="ip-lens-h"><h3 tabIndex={-1}>{l.title}</h3><span className="small muted">{lensState(l, held)}</span></header>
          <LensBody l={l} />
        </section>
      ))}
    </div>
  );
}

const note = () => t("kickoff.panel.note", { unchecked: t("kickoff.panel.unchecked") });

/** The kickoff page's one-line band and its drawer share this. Open is per visit, so the conversation is never hidden on arrival. */
export function useIdeaDrawer(businessId: number | undefined) {
  const { data, reload } = useIdeaReview(businessId);
  const [open, setOpen] = useState(false);
  return { data, reload, open: open && !!data?.round, setOpen };
}
export type IdeaDrawer = ReturnType<typeof useIdeaDrawer>;

/** One line of where each lens stands. The detail opens as a drawer over the conversation column, never beside it. */
export function IdeaPanelBand({ drawer }: { drawer: IdeaDrawer }) {
  const { data, open, setOpen } = drawer;
  const { label: held } = useWorker();
  if (!data || !data.round) return null;
  return (
    <section className="ip-band" aria-label={t("kickoff.panel.title")}>
      <div className="ip-band-top">
        <h2 className="ip-band-t">{t("kickoff.panel.title")}</h2>
        <ul className="ip-band-sum">
          {data.lenses.map((l) => <li key={l.key}><span className="ip-band-l">{l.title}</span> <span className="muted">{lensState(l, held)}</span></li>)}
        </ul>
        <button type="button" id="ip-toggle" aria-expanded={open} aria-controls="ip-drawer" onClick={() => setOpen(!open)}>{t(open ? "kickoff.panel.hide" : "kickoff.panel.show")}</button>
      </div>
    </section>
  );
}

/** Covers the conversation column and nothing else, so the column keeps its width and the 업무 지시서 stays in view. */
export function IdeaPanelDrawer({ drawer }: { drawer: IdeaDrawer }) {
  const { data, reload, open, setOpen } = drawer;
  const wasOpen = useRef(false);
  useEffect(() => {
    // opening moves focus to the first angle; closing gives it back to the button that opened the drawer
    if (open) (document.querySelector("#ip-drawer .ip-lens-h h3") as HTMLElement | null)?.focus();
    else if (wasOpen.current) document.getElementById("ip-toggle")?.focus();
    wasOpen.current = open;
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, setOpen]);
  if (!open || !data) return null;
  return (
    <div id="ip-drawer" className="ip-drawer" role="region" aria-label={t("kickoff.panel.detail")}>
      <Lenses view={data} />
      <div className="ip-foot"><p className="small muted">{note()}</p><Rerun view={data} reload={reload} /></div>
    </div>
  );
}

/** On the business overview: the latest round in full, with 다시 평가. */
export function IdeaPanelSection({ businessId }: { businessId: number }) {
  const { data, error, reload } = useIdeaReview(businessId);
  return (
    <Section title={t("kickoff.panel.title")} actions={data ? <Rerun view={data} reload={reload} /> : undefined}>
      {error && <p className="quiet-error" role="alert">{t("kickoff.panel.load_failed")} <button className="link" onClick={reload}>{t("common.retry")}</button></p>}
      {data && !data.round && (
        <Hint title={t("kickoff.panel.none_title")}>{t("kickoff.panel.none_body")}</Hint>
      )}
      {data && data.round > 0 && (
        <div className="box">
          <Lenses view={data} />
          <div className="ip-foot"><p className="small muted">{data.round > 1 ? t("kickoff.panel.round", { n: data.round }) : ""}{note()}</p></div>
        </div>
      )}
    </Section>
  );
}
