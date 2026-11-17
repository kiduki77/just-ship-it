import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useFollowBusiness } from "../business";
import { PageHead } from "../components/kit";
import { ConfirmBox, LoadError, Loading } from "../components/ui";
import { t } from "../i18n";
import { BriefHead, BriefPanel, FlashStatus, useFlash } from "../kickoff/Brief";
import { Dock } from "../kickoff/Composer";
import { Conversation } from "../kickoff/Conversation";
import { IdeaPanelBand, IdeaPanelDrawer, useIdeaDrawer } from "../kickoff/IdeaPanel";
import { scrollBehavior } from "../kickoff/motion";
import { ProductCard } from "../kickoff/ProductCard";
import { stageOf } from "../kickoff/types";
import { useBusiness, useKickoff } from "../kickoff/useKickoff";

const WIDE = "(min-width: 1280px)";

function useWide(): boolean {
  const [wide, setWide] = useState(() => window.matchMedia(WIDE).matches);
  useEffect(() => {
    const mq = window.matchMedia(WIDE);
    const on = () => setWide(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return wide;
}

export default function KickoffPage() {
  const sid = Number(useParams().id);
  const { view, error, failure, reload, act } = useKickoff(sid);
  const business = useBusiness(view?.session.business_id);
  // the kickoff belongs to one business: this tab follows it once (and says so), without changing the business kept for
  // next time, and a later pick in the switcher is not undone
  useFollowBusiness(view ? view.session.business_id ?? null : undefined, `kickoff:${sid}`, t("term.kickoff"));
  const wide = useWide();
  const [ending, setEnding] = useState(false);
  const [endBusy, setEndBusy] = useState(false);
  const [endError, setEndError] = useState<string | null>(null);
  const [jumped, setJumped] = useState<number | null>(null);
  const [sheet, setSheet] = useState(false);
  const jumpTimer = useRef<number | undefined>(undefined);
  const flash = useFlash(view?.brief?.sections);
  const ideas = useIdeaDrawer(view?.session.business_id);
  useEffect(() => () => window.clearTimeout(jumpTimer.current), []);
  // the confirm takes the button's place, so it is gone before the confirm can note it as the opener: Keep talking or
  // Escape gives focus back to it here
  const endButton = useRef<HTMLButtonElement>(null);
  const wasEnding = useRef(false);
  useEffect(() => {
    if (wasEnding.current && !ending) endButton.current?.focus();
    wasEnding.current = ending;
  }, [ending]);

  if (!view && failure === "missing") {
    return (
      <div className="kick kui">
        <PageHead title={t("term.kickoff")} />
        <div className="load-error" role="alert">
          <p className="load-error-t">{t("kickoff.page.missing")}</p>
          <p className="load-error-b">{t("kickoff.page.missing_body")}</p>
          <div className="row wrap tight"><Link className="btn-ghost small" to="/business">{t("kickoff.page.to_business")}</Link></div>
        </div>
      </div>
    );
  }
  if (!view) return error ? <LoadError error={error} retry={reload} failure={failure} /> : <Loading />;
  const { session } = view;
  const stage = stageOf(view);
  const live = stage.kind === "thinking" || stage.kind === "usage_wait" || stage.kind === "owner" || stage.kind === "paused";
  async function end() {
    setEndBusy(true);
    setEndError(null);
    try {
      await act("/abandon");
      setEnding(false);
    } catch (e: any) {
      setEndError(e.message ?? String(e));
    } finally {
      setEndBusy(false);
    }
  }

  function jump(n: number) {
    const el = document.getElementById(`turn-${n}`);
    if (!el) return;
    setSheet(false);
    ideas.setOpen(false);
    el.scrollIntoView({ block: "center", behavior: scrollBehavior() });
    el.focus({ preventScroll: true });
    setJumped(n);
    window.clearTimeout(jumpTimer.current);
    jumpTimer.current = window.setTimeout(() => setJumped(null), 2400);
  }

  return (
    <div className="kick kui">
      <PageHead
        title={business?.name ?? t("term.business")}
        sub={<>{t("term.kickoff")} · <span className="num">{session.turns_used > 0
          ? t("kickoff.page.questions_of", { used: session.turns_used, max: session.max_turns })
          : t("kickoff.page.questions_max", { max: session.max_turns })}</span></>}
        actions={live && !ending && (
          <button ref={endButton} type="button" className="btn-ghost" onClick={() => setEnding(true)}>{t("kickoff.page.stop")}</button>
        )} />
      {ending && live && (
        <div className="kick-end">
          <ConfirmBox confirmLabel={t("kickoff.page.stop_confirm")} cancelLabel={t("kickoff.page.stop_keep")} tone="danger" busy={endBusy} error={endError}
            onConfirm={end} onCancel={() => setEnding(false)}>
            <p>{t("kickoff.page.stop_says")}</p>
          </ConfirmBox>
        </div>
      )}
      <FlashStatus flash={flash} />
      <IdeaPanelBand drawer={ideas} />
      {view.product && <ProductCard product={view.product} onReadAgain={() => act("/read-again")} inTalk={stage.kind === "thinking"} />}
      {!wide && (
        <div className="kbar">
          <div className="kbar-top">
            <BriefHead view={view} as="span" />
            <div className="kbar-act">
              {flash.titles.length > 0 && <span className="small muted">{t("kickoff.page.just_changed", { titles: flash.titles.join(", ") })}</span>}
              <button type="button" aria-expanded={sheet} aria-controls="brief-sheet" onClick={() => setSheet(!sheet)}>
                {sheet ? t("kickoff.page.hide_plan") : t("kickoff.page.show_plan")}
              </button>
            </div>
          </div>
          {sheet && (
            <div id="brief-sheet" className="kbar-sheet">
              <BriefPanel view={view} flash={flash} onJump={jump} head={false} />
            </div>
          )}
        </div>
      )}
      <div className={`kick-grid ${wide ? "wide" : ""}`}>
        <section className="kick-talk" aria-label={t("kickoff.page.talk")}>
          <div className="kick-log-w">
            <IdeaPanelDrawer drawer={ideas} />
            <Conversation view={view} stage={stage} business={business} jumped={jumped} onResume={() => act("/resume")} />
          </div>
          <Dock view={view} stage={stage} act={act} business={business} reload={reload} />
        </section>
        {wide && (
          <aside className="kick-brief" aria-label={t("kickoff.brief.title")}>
            <BriefPanel view={view} flash={flash} onJump={jump} />
          </aside>
        )}
      </div>
    </div>
  );
}
