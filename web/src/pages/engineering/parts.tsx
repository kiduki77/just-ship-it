import { ReactNode, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Row } from "../../api";
import { Fix } from "../../components/fix";
import { useWorker, WORKER_FIX } from "../../components/frame";
import { Icon } from "../../components/icons";
import { Box, Chip, Row as ListRow, Rows, Section, Tone } from "../../components/kit";
import { RefLink, Time, toneOf } from "../../components/ui";
import { t } from "../../i18n";
import { label } from "../../labels";
import { StageActions } from "./actions";
import { countOf, DOT, groupOf, lastMoved, PHASES, phaseState, Stage } from "./model";

export function St({ v }: { v: string }) {
  return <Chip tone={toneOf(v) as Tone}>{label(v)}</Chip>;
}

/** A build's status word: one the company is moving says the worker's word while no worker runs, as its project,
 * its stage and Alerts do, instead of "Testing" on a build that is not moving. */
export function JobSt({ v }: { v: string }) {
  const held = useWorker().label;
  return held && groupOf(v) === "running" ? <Chip tone="warn">{held}</Chip> : <St v={v} />;
}

/** One development: its name and where it stands in one line, the server's detail (commands as code), and its ways on.
 * `actions` is false where the page already offers the buttons, so each one is drawn once. */
export function StageCard({ st, page, lead, held, actions = true, onChange }: {
  st: Stage; page: boolean; lead: boolean; held: boolean; actions?: boolean; onChange: () => void;
}) {
  const stalled = held && st.tone === "run";
  return (
    <li className={`eng-now-i t-${st.tone}`}>
      <span className={`dot ${stalled ? "" : DOT[st.tone]}`} aria-hidden="true" />
      <div className="grow">
        <p className="eng-now-h"><span className="eng-now-n">{st.title}</span><span className="eng-now-s">{st.headline}</span></p>
        {st.detail && <p className="eng-now-d"><Fix text={st.detail} /></p>}
        {stalled && <p className="eng-now-d warn-t">{t("build.now.worker_down")} <Fix text={`\`${WORKER_FIX}\``} /></p>}
        <p className="small muted eng-now-m">
          {st.ref}
          {st.since && <> · <Time v={st.since} /></>}
          {page && st.project_id && <> · <RefLink code={`P-${st.project_id}`} /></>}
        </p>
      </div>
      {actions && <StageActions st={st} lead={lead} onChange={onChange} />}
    </li>
  );
}

/** One thing that waits on the owner, as the "Your turn" strip draws it. `actions` gets whether this row leads. */
export type TurnItem = {
  key: string; tone: "bad" | "warn"; title: string; line: ReactNode; detail?: string | null; since?: string | null;
  projectId?: number | null; openJob?: number | null; way: boolean; actions: (lead: boolean) => ReactNode;
};

/** What waits on the owner, one compact row each with its buttons: builds that need an answer or a fix, other stages that
 * stopped, and merged builds nobody can use yet. Not drawn at all when nothing waits. */
export function TurnStrip({ items, page, href }: { items: TurnItem[]; page: boolean; href: (job: number) => string }) {
  if (!items.length) return null;
  const lead = items.find((i) => i.way)?.key;
  return (
    <Section title={t("build.turn.title")} count={items.length} className="eng-turn-sec">
      <Box pad={false}>
        <ul className="eng-turn" aria-label={t("build.turn.title")}>
          {items.map((i) => (
            <li key={i.key} className={`eng-turn-i t-${i.tone}`}>
              <span className={`dot ${DOT[i.tone]}`} aria-hidden="true" />
              <div className="grow">
                <p className="eng-turn-h"><span className="eng-turn-n" title={i.title}>{i.title}</span><span className="eng-turn-s">{i.line}</span></p>
                {i.detail && <p className="eng-turn-d"><Fix text={i.detail} /></p>}
                <p className="small muted eng-turn-m">
                  {i.since && <Time v={i.since} />}
                  {page && i.projectId && <>{i.since ? " · " : ""}<RefLink code={`P-${i.projectId}`} /></>}
                  {i.openJob && <>{i.since || (page && i.projectId) ? " · " : ""}<Link to={href(i.openJob)}>{t("build.turn.open")}</Link></>}
                </p>
              </div>
              {i.actions(i.key === lead)}
            </li>
          ))}
        </ul>
      </Box>
    </Section>
  );
}

/** Below 900px the list is a single row until the owner opens it. */
function useNarrow() {
  const q = "(max-width: 900px)";
  const [narrow, setNarrow] = useState(() => window.matchMedia(q).matches);
  useEffect(() => {
    const mq = window.matchMedia(q);
    const on = () => setNarrow(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return narrow;
}

/** The builds, the ones that need the owner and the ones moving first, the finished ones folded. Each row names the
 * build, where it stands and when it last moved; the picked one is in the address (`href`). */
export function BuildList({ turn, active, finished, selected, titleOf, needsYou, href }: {
  turn: Row[]; active: Row[]; finished: Row[]; selected: Row; titleOf: (j: Row) => string; needsYou: (j: Row) => boolean;
  href: (job: number) => string;
}) {
  const narrow = useNarrow();
  const [open, setOpen] = useState(false);
  const row = (j: Row) => {
    const phase = t(`build.phase.${PHASES[phaseState(j, needsYou(j)).at]}`);
    // a merged or delivered build is past its items, so only one still building or stopped says how far it came
    const count = ["running", "failed"].includes(groupOf(j.status)) ? countOf(j) : null;
    return (
      <ListRow key={j.id} to={href(j.id)} selected={j.id === selected.id}
        title={<span title={titleOf(j)}>{titleOf(j)}</span>}
        meta={<>
          {needsYou(j) && <><span className="warn-t">{t("build.jobs.owner_action")}</span> · </>}
          {phase}
          {count && <> <span className="eng-count" aria-hidden="true">{count.done}/{count.total}</span><span className="sr-only">{t("build.items.done", count)}</span></>}
          {" · "}<Time v={lastMoved(j)} />
        </>}
        end={<JobSt v={j.status} />} />
    );
  };
  const ordered = [...turn, ...active];
  const inFinished = finished.some((j) => j.id === selected.id);
  const list = (
    // choosing a build closes the list again on a narrow screen
    <div id="eng-list" className="eng-list" onClick={(e) => { if (narrow && (e.target as HTMLElement).closest("a")) setOpen(false); }}>
      <Box pad={false}>
        {ordered.length > 0 && <Rows label={t("build.jobs.title")}>{ordered.map(row)}</Rows>}
        {finished.length > 0 && (
          <details className="eng-fold" open={inFinished || ordered.length === 0 ? true : undefined}>
            <summary><Icon name="chevron" size={14} />{t("build.list.finished", { n: finished.length })}</summary>
            <Rows label={t("build.list.finished", { n: finished.length })}>{finished.map(row)}</Rows>
          </details>
        )}
      </Box>
    </div>
  );
  if (!narrow) return list;
  return (
    <div className="eng-pick">
      <button type="button" className="eng-pick-b" aria-expanded={open} aria-controls="eng-list" onClick={() => setOpen(!open)}>
        <span className="eng-pick-t" title={titleOf(selected)}>{titleOf(selected)}</span>
        <JobSt v={selected.status} />
        <span className="small muted eng-pick-c">{t("build.list.change")}<Icon name="chevron" size={14} /></span>
      </button>
      {open && list}
    </div>
  );
}

export function SpecList({ specs, page }: { specs: Row[]; page: boolean }) {
  return (
    <Box pad={false}>
      <ul className="ilist">
        {specs.map((s) => (
          <li key={s.id}>
            <div className="grow">
              <p className="t1" title={s.title}>{s.title}</p>
              <p className="t2"><span className="mono">products/{s.target_repository}</span>{s.approved_commit_sha ? <> · {t("build.specs.approved")}</> : null}</p>
            </div>
            {page && <span className="small nowrap"><RefLink code={`P-${s.project_id}`} /></span>}
            <St v={s.state ?? s.status} />
            <span className="end"><Time v={s.updated_at} /></span>
          </li>
        ))}
      </ul>
    </Box>
  );
}
