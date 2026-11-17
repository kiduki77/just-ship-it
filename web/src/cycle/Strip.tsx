import { useLive } from "../api";
import { Chip } from "../components/kit";
import { when } from "../components/ui";
import { t } from "../i18n";
import { QuietError } from "../components/today";
import { StageBlock, Summary } from "./Cards";
import { stageWord } from "./format";
import { useWorker } from "../components/frame";
import { CycleDetail, StageChip } from "./types";

/**
 * The cycle's stages in one row: done green, the current one blue (amber while it waits for the owner), the rest plain.
 * The stop marker sits on the current stage, since the company stops after it. A click picks a stage to read below.
 */
export function Strip({ n, strip, picked, onPick }: {
  n: number; strip: StageChip[]; picked: number | null; onPick: (id: number | null) => void;
}) {
  const held = useWorker().label;
  return (
    <ol className="cy-strip" aria-label={t("cycle.strip.aria", { n })} style={{ ["--cols" as string]: strip.length }}>
      {strip.map((s) => {
        const shown = picked === s.id || (picked === null && s.is_current);
        const counted = s.tasks_total != null && s.tasks_total > 0;
        return (
          <li key={s.id} className={`cy-step s-${s.status} ${s.is_current ? "is-current" : ""} ${stageWord(s.status, held).tone === "warn" && held ? "is-held" : ""}`}>
            <button type="button" className={shown ? "on" : ""} aria-pressed={shown} aria-current={s.is_current ? "step" : undefined}
              onClick={() => onPick(s.is_current ? null : s.id)}>
              <span className="cy-step-top">
                <span className="cy-step-n num">{s.position + 1}</span>
                <span className="cy-step-status">{stageWord(s.status, held).word}</span>
              </span>
              <span className="cy-step-title">{s.title}</span>
              <span className="cy-step-meta small muted num">
                {s.status === "planned" ? (s.adopted ? t("cycle.strip.adopted") : " ")
                  : counted ? t("cycle.tasks_done", { done: s.tasks_done ?? 0, total: s.tasks_total! })
                    : s.status === "skipped" ? " " : t("cycle.strip.no_tasks")}
              </span>
              {s.is_current && <span className="cy-step-stop small" title={t("cycle.current.ends")}>{t("cycle.strip.stop")}</span>}
            </button>
          </li>
        );
      })}
    </ol>
  );
}

/** A stage the owner picked that is not the current one: its summary once done, its card while it is still ahead. */
export function StageDetail({ cycleId, stageId, onBack }: { cycleId: number; stageId: number; onBack: () => void }) {
  const { data, error, reload } = useLive<CycleDetail>(`/cycles/${cycleId}`, ["cycles", "cycle_stages", "cycle_proposals"]);
  const stage = data?.stages.find((s) => s.id === stageId);
  const ready = data ? [...data.proposals].reverse().find((p) => p.status === "ready") : undefined;
  const card = stage ? ready?.stages.find((c) => c.key === stage.key) : undefined;
  const held = useWorker().label;
  const word = stage ? stageWord(stage.status, held) : null;
  return (
    <div className="box cy-current">
      <header className="cy-current-h">
        <h3>{stage?.title ?? "…"}</h3>
        {word && <Chip tone={word.tone}>{word.word}</Chip>}
        <button type="button" className="link small" onClick={onBack}>{t("cycle.detail.back")}</button>
      </header>
      {error ? <QuietError text={t("cycle.detail.load_failed")} retry={reload} /> : !data || !stage ? (
        <p className="muted">{t("cycle.detail.loading")}</p>
      ) : stage.summary ? (
        <>
          {stage.finished_at && <p className="small muted">{when(stage.finished_at)}</p>}
          <Summary summary={stage.summary} />
        </>
      ) : stage.status === "skipped" ? (
        <p className="muted">{t("cycle.detail.skipped")}</p>
      ) : card ? (
        <>
          <p className="muted">{t("cycle.detail.planned")}</p>
          <ol className="cy-stages"><StageBlock stage={card} /></ol>
        </>
      ) : (
        <p className="muted">{t("cycle.detail.no_summary")}</p>
      )}
    </div>
  );
}
