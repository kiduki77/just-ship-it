import { useState } from "react";
import { Link } from "react-router-dom";
import { useLive } from "../api";
import { Box, Chip, Section } from "../components/kit";
import { useWorker } from "../components/frame";
import { QuietError, SkeletonRows } from "../components/today";
import { usd, when } from "../components/ui";
import { t } from "../i18n";
import { cycleWord, stageWord } from "./format";
import { CycleDetail, CycleRow } from "./types";

function Stages({ id }: { id: number }) {
  const { data, error, reload } = useLive<CycleDetail>(`/cycles/${id}`, ["cycle_stages", "cycle_proposals"]);
  const held = useWorker().label;
  if (error) return <QuietError text={t("cycle.detail.load_failed")} retry={reload} />;
  if (!data) return <p className="quiet-line">{t("cycle.detail.loading")}</p>;
  return (
    <ol className="cy-hist-stages">
      {data.stages.filter((s) => s.end_cause !== "revised_away").map((s) => (
        <li key={s.id}>
          <Chip tone={stageWord(s.status, held).tone}>{stageWord(s.status, held).word}</Chip>
          <div className="grow">
            <p className="strong">{s.title}</p>
            {s.summary && <p className="small muted">{s.summary.headline}</p>}
          </div>
          {s.summary && <span className="small muted num nowrap">{usd(s.summary.cost_usd)}</span>}
        </li>
      ))}
    </ol>
  );
}

function Row({ c, open }: { c: CycleRow; open: boolean }) {
  // the cycle still running (or waiting to start) is the one the owner acts on: it opens with its stages showing
  const [shown, setShown] = useState(open);
  const state = cycleWord(c.state, useWorker().label);
  return (
    <li>
      <details open={open || undefined} onToggle={(e) => setShown((e.currentTarget as HTMLDetailsElement).open)}>
        <summary>
          <span className="strong">{t("cycle.n", { n: c.number })}</span>
          <Chip tone={state.tone}>{state.word}</Chip>
          <span className="small muted num">{t("cycle.history.stages", { done: c.stages_done, total: c.stages_total })}</span>
          <span className="small muted num">{usd(c.cost_usd)}</span>
          <span className="small muted num end">{when(c.closed_at ?? c.started_at)}</span>
        </summary>
        {open && <p className="small"><Link to="/">{t("cycle.history.now")}</Link></p>}
        {shown && <Stages id={c.id} />}
      </details>
    </li>
  );
}

/** Past and current cycles of one business, each opening to its stages and what each one's summary said. */
export function CycleHistory({ businessId }: { businessId: number }) {
  const { data, error, reload } = useLive<CycleRow[]>(`/businesses/${businessId}/cycles`, ["cycles", "cycle_stages"]);
  return (
    <Section title={t("cycle.history.title")} count={data?.length || undefined}>
      {error ? <QuietError text={t("cycle.load_failed")} retry={reload} /> : !data ? <SkeletonRows /> : data.length ? (
        <Box pad={false} className="cy-hist">
          <ul>{data.map((c) => <Row key={c.id} c={c} open={c.state === "running" || c.state === "draft" || c.state === "retro"} />)}</ul>
        </Box>
      ) : <p className="quiet-line">{t("cycle.history.empty")}</p>}
    </Section>
  );
}
