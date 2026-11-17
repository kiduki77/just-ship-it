import { useState } from "react";
import { Row } from "../api";
import { useBusinessLive } from "../business";
import { Fix } from "../components/fix";
import { useWorker, WORKER_FIX } from "../components/frame";
import { t } from "../i18n";
import { Waiting } from "../kickoff/Waiting";
import { DecisionList, GateView, ProposalView, StoppedView } from "./Cards";
import { useNow } from "./format";
import { Strip, StageDetail } from "./Strip";
import { CycleBoard } from "./types";
import { CurrentView, WorkingNow } from "./Work";
import type { Decide } from "./Cards";

const PROJECT_MANAGER_WRITING = ["proposing", "revising", "closing", "retro"];

/** What the CEO is writing right now, as the kickoff chat shows a wait: what, how long it usually takes, a clock. With
 * the worker off nothing is being written, so the usual time gives way to the command that starts it. */
function ProjectManagerWorking({ board }: { board: CycleBoard }) {
  const worker = useWorker();
  const n = board.cycle?.number ?? 0;
  const stage = board.current?.stage.title ?? "";
  const by = {
    proposing: ["cycle.draft.title", "cycle.draft.expect", "cycle.draft.title_down"],
    revising: ["cycle.revise_wait.title", "cycle.draft.expect", "cycle.revise_wait.title_down"],
    closing: ["cycle.close_wait.title", "cycle.close_wait.expect", "cycle.close_wait.title_down"],
    retro: ["cycle.retro_wait.title", "cycle.retro_wait.expect", "cycle.retro_wait.title_down"],
  } as const;
  const keys = by[board.state as keyof typeof by];
  if (!keys) return null;
  return (
    <div className="box cy-card cy-ceo">
      <Waiting title={t(keys[0], { n, stage })} downTitle={t(keys[2], { n, stage })} expect={worker.down ? "" : t(keys[1])} since={board.since} slowAfter={5 * 60}
        slow={t("cycle.draft.slow")} />
      {worker.down && <p className="small cy-fix">{t("cycle.draft.worker_fix")} <Fix text={`\`${WORKER_FIX}\``} /></p>}
    </div>
  );
}

/**
 * The cards that wait for the owner's word on the cycle: the proposal, a stage's end, a stopped stage, and what the CEO
 * is writing. Decisions of the stage's work are items of the owner's list; `listed` names the ones the page already
 * shows there, so a decision is never on screen twice.
 */
export function CycleWaiting({ board, decide, listed }: { board: CycleBoard; decide: Decide; listed: Set<number> }) {
  const decisions = board.waiting.flatMap((w) => (w.kind === "decision" && !listed.has(w.request_id) ? [w] : []));
  const cards = board.waiting.filter((w) => w.kind !== "decision");
  if (!cards.length && !decisions.length && !PROJECT_MANAGER_WRITING.includes(board.state)) return null;
  return (
    <section id="cycle-waiting" className="cy-waiting" aria-label={t("cycle.waiting.aria")} tabIndex={-1}>
      {cards.map((w) => {
        if (w.kind === "proposal") return <ProposalView key="proposal" card={w} n={board.cycle?.number ?? 0} decide={decide} />;
        if (w.kind === "gate") return <GateView key="gate" card={w} decide={decide} />;
        if (w.kind === "stopped_stage") return <StoppedView key="stopped" card={w} decide={decide} />;
        return null;
      })}
      {board.state !== "revising" && <ProjectManagerWorking board={board} />}
      {decisions.length > 0 && <DecisionList cards={decisions} />}
    </section>
  );
}

/** Work started outside the running stage (a Run now, a project started by hand, an experiment), still queued or running. */
function useOutsideJobs(): Row[] {
  const { data } = useBusinessLive<Row[]>("/jobs", ["jobs"]);
  return (data ?? []).filter((j) => j.gate === "direct" && (j.status === "queued" || j.status === "running"));
}

/** The cycle at work: its stages in a row, the running stage (or the one picked in the row), and who is on what. */
export function CycleRun({ board, decide }: { board: CycleBoard; decide: Decide }) {
  const [picked, setPicked] = useState<number | null>(null);
  const now = useNow();
  const worker = useWorker();
  const outside = useOutsideJobs();
  const n = board.cycle?.number ?? 0;
  const cur = board.current;
  const pickedStage = picked !== null ? board.strip.find((s) => s.id === picked && !s.is_current) : undefined;
  const idle = board.state === "none" ? 0 : board.idle_agents;
  return (
    <div className="cy-board">
      {board.strip.length > 0 && (
        <section className="cy-strip-sec" aria-label={t("cycle.strip.title", { n })}>
          <h3 className="cy-h">{t("cycle.strip.title", { n })}</h3>
          <Strip n={n} strip={board.strip} picked={picked} onPick={setPicked} />
          {outside.length > 0 && (
            <p className="small muted cy-outside">
              {t(worker.down ? "cycle.outside.waiting" : "cycle.outside.running", { n: outside.length })}
            </p>
          )}
        </section>
      )}
      {(cur || board.working.length > 0) && (
        <div className="cy-run">
          {board.cycle && pickedStage ? (
            <StageDetail cycleId={board.cycle.id} stageId={pickedStage.id} onBack={() => setPicked(null)} />
          ) : cur && board.cycle ? (
            <CurrentView cur={cur} decide={decide} n={n} cycleId={board.cycle.id} />
          ) : null}
          <WorkingNow cards={board.working} idle={idle} stopped={board.state === "none" ? 0 : board.idle_stopped ?? 0} now={now} starting={cur?.starting ?? null} />
        </div>
      )}
    </div>
  );
}

/** Space held for the board while it loads, so the owner's list below it does not jump when it arrives. */
export function CycleSkeleton() {
  return <div className="cy-skel skel" aria-busy="true" aria-label={t("cycle.loading")}><p className="skel-bar" /><p className="skel-bar short" /></div>;
}
