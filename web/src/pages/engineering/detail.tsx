import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { Row } from "../../api";
import { crewHref } from "../../crews";
import { Icon } from "../../components/icons";
import { BuiltBy } from "../../components/MergeReview";
import { Chip, Section, TabPanel, Tabs, Tone } from "../../components/kit";
import { ExtLink, safeUrl } from "../../components/panel";
import { when } from "../../components/ui";
import { t } from "../../i18n";
import { agentName, label } from "../../labels";
import { JobSt, St, StageCard } from "./parts";
import { BuildItem, groupOf, Items, itemsOf, PHASES, phaseState, Stage } from "./model";

type Tab = "overview" | "log";

/** The five steps of a build across the page: finished ones done, the current one marked, a stopped build red after the
 * last step it reached. A running step shows it is alive (a slow ring on its dot, a thin line sweeping its track);
 * nothing moves while the work is held, waiting for the owner or over, so stillness means no progress. */
function Stepper({ job, turn, held, paused }: { job: Row; turn: boolean; held: boolean; paused: boolean }) {
  const { stopped, at } = phaseState(job, turn);
  const merged = job.status === "MERGED";
  const running = !stopped && !paused && groupOf(job.status) === "running";
  const steps = PHASES.flatMap((ph, i) => {
    const state = stopped ? (i <= at ? "done" : "todo") : merged || i < at ? "done" : i === at ? "cur" : "todo";
    const still = held && !stopped && i === at && ph !== "decide" && ph !== "merged";
    const item = { key: ph, state, name: t(`build.phase.${ph}`), note: still ? t("build.phase.held") : null, live: running && i === at, ask: state === "cur" && ph === "decide" };
    return stopped && i === at
      ? [item, { key: "stopped", state: "stop", name: t(job.status === "DISCARDED" ? "build.phase.dropped" : "build.phase.stopped"), note: null, live: false, ask: false }]
      : [item];
  });
  return (
    <ol className="eng-steps" aria-label={t("build.job.stages")}>
      {steps.map((s, i) => (
        <li key={s.key} className={`eng-step s-${s.state} ${s.live ? "is-live" : ""} ${s.ask ? "is-ask" : ""}`}
          aria-current={s.state === "cur" || s.state === "stop" ? "step" : undefined}>
          {i < steps.length - 1 && <span className="eng-track" aria-hidden="true" />}
          <span className="eng-dot" aria-hidden="true" />
          <span className="eng-name">{s.name}</span>
          {s.note && <span className="eng-note">{s.note}</span>}
        </li>
      ))}
    </ol>
  );
}

/** What the build is making, item by item from its plan: how many are done, a bar of them, and the list, the done ones
 * folded so the one being built leads. An item is current only while the build is building; the bar's current item
 * moves only while that build runs, as the stepper's running step does. */
function BuildItems({ items, building, live }: { items: Items; building: boolean; live: boolean }) {
  const current = building ? items.current : null;
  const state = (x: BuildItem) => (x.done ? "done" : x.n === current ? "cur" : "todo");
  const done = items.tasks.filter((x) => x.done);
  const rest = items.tasks.filter((x) => !x.done);
  const row = (x: BuildItem) => (
    <li key={x.n} className={`eng-item s-${state(x)}`} aria-current={state(x) === "cur" ? "step" : undefined}>
      <span className="eng-mark" aria-hidden="true">{x.done && <Icon name="check" size={10} />}</span>
      <span className="sr-only">{t(`build.items.state.${state(x)}`)}</span>
      <span className="eng-item-n">{x.n}</span>
      <span className="eng-item-b">
        <span className="eng-item-t" title={x.title}>{x.title}</span>
        {x.detail && <span className="eng-item-d" title={x.detail}>{x.detail}</span>}
      </span>
    </li>
  );
  return (
    <section className="eng-items" aria-label={t("build.items.title")}>
      <h3 className="eng-h">{t("build.items.title")}</h3>
      <p className="eng-items-s">
        {rest.length === 0 ? t("build.items.all", { n: items.tasks.length })
          : <>{t("build.items.done", { done: done.length, total: items.tasks.length })}{current !== null && <> · {t("build.items.working", { n: current })}</>}</>}
      </p>
      <div className={`eng-bar ${live ? "is-live" : ""}`} aria-hidden="true">
        {items.tasks.map((x) => <span key={x.n} className={`s-${state(x)}`} />)}
      </div>
      {done.length > 0 && (
        <details className="eng-items-fold">
          <summary className="small"><Icon name="chevron" size={12} />{t("build.items.folded", { n: done.length })}</summary>
          <ol className="eng-items-l">{done.map(row)}</ol>
        </details>
      )}
      {rest.length > 0 && <ol className="eng-items-l">{rest.map(row)}</ol>}
    </section>
  );
}

/** A log step's colour by what it says: a stop is red, a finish green, the rest neutral. */
function stepTone(status: string): Tone {
  if (["FAILED", "DISCARDED"].includes(status)) return "bad";
  if (["DELIVERED", "MERGED", "ACCEPTED"].includes(status)) return "ok";
  if (status === "CLARIFICATION_REQUIRED") return "warn";
  return "muted";
}

/** One build in three tabs: where it stands and what it met, the log of its steps, and the engineer's view. A build with
 * an error opens on the engineer tab. Keyed by the build, so picking another starts again at its own first tab. */
export function BuildDetail({ job, title, spec, stage, offered, turn, page, held, paused, onChange }: {
  job: Row; title: string; spec?: Row; stage?: Stage; offered: boolean; turn: boolean; page: boolean; held: boolean; paused: boolean;
  onChange: () => void;
}) {
  const [tab, setTab] = useState<Tab>("overview");
  const acceptance = job.result?.acceptance;
  const items = itemsOf(job);
  const building = job.status === "IMPLEMENTING";
  const steps: Row[] = job.steps ?? [];
  const meta = [
    job.branch && <span key="b"><span className="mono">{job.branch}</span></span>,
    safeUrl(job.pr_url) && <ExtLink key="p" href={job.pr_url}>{t("build.job.pr")}</ExtLink>,
    spec && <span key="s">{t("build.job.spec")}: {spec.title}</span>,
    spec && <span key="t" className="mono">products/{spec.target_repository}</span>,
  ].filter(Boolean);
  return (
    <Section title={<span className="eng-dt" title={title}>{title}</span>} actions={<JobSt v={job.status} />} className="eng-detail">
      {meta.length > 0 && (
        <p className="eng-meta small muted">{meta.map((m, i) => <Fragment key={i}>{i > 0 && " · "}{m}</Fragment>)}</p>
      )}
      <BuiltBy team={job.team} />
      {spec && <p className="small eng-crew"><Link to={crewHref("dev_job", spec.project_id, job.id)}>{t("build.job.crew_link")}</Link></p>}
      <Tabs id="eng" label={t("build.tab.label")} value={tab} onChange={setTab} items={[
        { key: "overview", label: t("build.tab.overview") },
        { key: "log", label: t("build.tab.log"), count: steps.length || undefined },
      ]} />
      <TabPanel id="eng" tabKey={tab} className="eng-panel">
        {tab === "overview" && (
          <>
            <Stepper job={job} turn={turn} held={held} paused={paused} />
            {items && <BuildItems items={items} building={building} live={building && !held && !paused && (!stage || stage.tone === "run")} />}
            {job.error && (
              <section className="eng-error" role="alert" aria-label={t("build.job.raw_error")}>
                <h3 className="eng-h">{t("build.job.raw_error")}</h3>
                <pre className="pre">{job.error}</pre>
              </section>
            )}
            {stage && (
              <ul className="eng-now" aria-label={t("build.job.now")}>
                <StageCard st={stage} page={page} lead={false} held={held} actions={!offered} onChange={onChange} />
              </ul>
            )}
            {acceptance && (
              <section className="eng-accept" aria-label={t("build.job.acceptance")}>
                <h3 className="eng-h">{t("build.job.acceptance")} <St v={acceptance.accepted ? "ACCEPTED" : "REJECTED"} /></h3>
                <ul className="eng-criteria">
                  {acceptance.criteria_results.map((c: Row, i: number) => (
                    <li key={i}>
                      <Chip tone={c.met ? "ok" : "bad"}>{c.met ? t("build.job.met") : t("build.job.unmet")}</Chip>
                      <span className="eng-crit">{c.criterion}<span className="muted">: {c.evidence}</span></span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </>
        )}
        {tab === "log" && (steps.length === 0 ? <p className="small muted">{t("build.job.log.empty")}</p> : (
          <div className="timeline">
            {steps.map((s, i) => (
              <div key={i} className="tl">
                <div className="row between small">
                  <span className="eng-who"><Chip tone={stepTone(s.status)}>{label(s.status)}</Chip>{s.by && <span>{agentName(s.by)}</span>}</span>
                  <span className="muted">{when(s.at)}</span>
                </div>
                {s.say && <p className="eng-say">{s.say}</p>}
                {(s.detail || s.lists?.length > 0) && (
                  <details className="eng-raw">
                    <summary className="small">{t("build.job.details")}</summary>
                    {s.detail && <div className="pre-wrap small">{s.detail}</div>}
                    {s.lists?.map((l: Row) => (
                      <div key={l.label} className="small">
                        <p className="eng-say"><b>{l.label}</b></p>
                        <ul>{l.items.map((x: string, k: number) => <li key={k}>{x}</li>)}</ul>
                      </div>
                    ))}
                  </details>
                )}
              </div>
            ))}
          </div>
        ))}
      </TabPanel>
    </Section>
  );
}
