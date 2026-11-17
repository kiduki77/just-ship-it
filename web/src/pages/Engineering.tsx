import { useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { Row } from "../api";
import { useBusinessLive, useNoBusiness } from "../business";
import { AboutSubject } from "../chat/subject";
import { Fix } from "../components/fix";
import { useWorker, WORKER_FIX } from "../components/frame";
import { Icon } from "../components/icons";
import { FirstRun, Hint, PageHead, Status } from "../components/kit";
import { Loading, Skeleton, useShowTest } from "../components/ui";
import { t } from "../i18n";
import { scrollBehavior } from "../kickoff/motion";
import { label } from "../labels";
import { DevJobActions, StageActions, UseAction } from "./engineering/actions";
import { BuildDetail } from "./engineering/detail";
import {
  AfterMerge, defaultJob, groupOf, hasWay, lastMoved, ownerTurn, sectionsOf, Stage, USE_LINE, USE_WAITS,
} from "./engineering/model";
import { BuildList, SpecList, StageCard, TurnItem, TurnStrip } from "./engineering/parts";
import "./misc.css";
import "./engineering/engineering.css";

export { DevJobActions };

/** Dev team view. With `projectId` it shows only that project's specs and jobs (project page tab). */
export default function Engineering({ projectId }: { projectId?: number }) {
  const { data, error, failure, reload } = useBusinessLive<{ specs: Row[]; jobs: Row[]; board: Stage[] }>(
    projectId === undefined ? "/engineering" : `/engineering?project_id=${projectId}`, ["specs", "dev_jobs", "agent_requests", "jobs"]);
  // the projects carry `dev_use`, the step after a merge, and say which are test projects
  const { data: projects } = useBusinessLive<Row[]>("/projects", ["projects", "dev_jobs", "agent_requests"]);
  const noBusiness = useNoBusiness();
  const worker = useWorker();
  const [showTest] = useShowTest();
  const [params] = useSearchParams();
  // the picked build lives in the address (?job=), so a link and a reload keep it
  const sel = Number(params.get("job")) || null;
  const href = (id: number) => {
    const next = new URLSearchParams(params);
    next.set("job", String(id));
    return `?${next}`;
  };
  const detail = useRef<HTMLDivElement>(null);
  const landed = useRef(false);
  const picked = useRef(sel);
  // with none picked, the build that opens is chosen once, so it does not change under the owner as the page updates
  const auto = useRef<number | null>(null);
  const loaded = !!data;
  useEffect(() => {
    // arriving from elsewhere with ?job= brings the build into view once it has loaded; a pick made here only does so if needed
    if (!loaded) return;
    if (sel && (sel !== picked.current || !landed.current)) detail.current?.scrollIntoView({ block: landed.current ? "nearest" : "start", behavior: landed.current ? scrollBehavior() : "auto" });
    landed.current = true;
    picked.current = sel;
  }, [sel, loaded]);
  const page = projectId === undefined;
  const head = (status: Status | null) => page && <PageHead title={t("build.title")} status={status} />;
  if (noBusiness) {
    return (
      <div className="owner-page kui mx eng">
        <PageHead title={t("build.title")} />
        <FirstRun>{t("build.firstrun")}</FirstRun>
      </div>
    );
  }
  if (!data) {
    return (
      <div className={page ? "owner-page kui mx eng" : "mx eng"}>
        {head(error ? { tone: "bad", text: t("build.load_failed") } : null)}
        {error ? <Loading error={error} failure={failure} retry={reload} /> : <Skeleton rows={4} />}
      </div>
    );
  }
  // Same rule as the sidebar: [TEST] projects stay out of company-wide views unless the owner shows them.
  const testIds = new Set((projects ?? []).filter((p) => p.is_test).map((p) => p.id));
  const visible = (pid: number | null) => !page || showTest || pid === null || !testIds.has(pid);
  const stages = (data.board ?? []).filter((st) => visible(st.project_id));
  const uses: AfterMerge[] = (projects ?? [])
    .filter((p) => (page ? visible(p.id) : p.id === projectId) && p.status !== "ARCHIVED" && USE_WAITS.includes(p.dev_use?.state))
    .map((p) => ({ p, u: p.dev_use }));
  const specs = data.specs.filter((s) => visible(s.project_id));
  const specIds = new Set(specs.map((s) => s.id));
  const jobs = data.jobs.filter((j) => specIds.has(j.spec_id));
  const jobIds = new Set(jobs.map((j) => j.id));
  const stageOfJob = new Map(stages.filter((st) => st.dev_job_id && jobIds.has(st.dev_job_id)).map((st) => [st.dev_job_id!, st]));
  // a stage no build on this page carries (a spec being written, a budget to raise) has no build to sit in
  const loose = stages.filter((st) => !st.dev_job_id || !jobIds.has(st.dev_job_id));
  const specOf = (id: number) => data.specs.find((s) => s.id === id);
  const title = (j: Row) => specOf(j.spec_id)?.title ?? t("build.jobs.spec_n", { id: j.spec_id });
  // a draft that no card of its own decides, or that the owner turned down, folds under the product's live spec
  const folded = (s: Row) => ["SUPERSEDED", "REJECTED"].includes(s.state);
  const current = specs.filter((s) => !folded(s));
  const past = specs.filter(folded);
  const needsYou = (j: Row) => ownerTurn(stageOfJob.get(j.id)) || j.status === "CLARIFICATION_REQUIRED" || j.owner_actions?.length > 0;

  if (!stages.length && !jobs.length && !specs.length && !uses.length) {
    return (
      <div className={page ? "owner-page kui mx eng" : "mx eng"}>
        {head({ text: t("build.none") })}
        {/* the page's status line already says there is none; the box says how one starts */}
        <Hint title={page ? t("build.how.title") : t("build.none")}>{t("build.how.body")}</Hint>
      </div>
    );
  }

  const { turn, active, finished } = sectionsOf(jobs, needsYou);
  const chosen = jobs.find((j) => j.id === sel) ?? jobs.find((j) => j.id === auto.current);
  const job = chosen ?? defaultJob(jobs, needsYou);
  auto.current = chosen ? auto.current : job?.id ?? null;

  const turnItems: TurnItem[] = [
    ...turn.map((j): TurnItem => {
      const st = stageOfJob.get(j.id);
      return {
        key: `job-${j.id}`, tone: st?.tone === "bad" ? "bad" : "warn", title: title(j), line: st?.headline ?? label(j.status),
        since: st?.since ?? lastMoved(j), projectId: st?.project_id ?? specOf(j.spec_id)?.project_id, openJob: j.id === job?.id ? null : j.id,
        way: st ? hasWay(st) : j.owner_actions?.length > 0,
        actions: (lead) => st ? <StageActions st={st} lead={lead} onChange={reload} />
          : j.owner_actions?.length > 0 ? <div className="row wrap eng-act"><DevJobActions jobId={j.id} actions={j.owner_actions} onChange={reload} lead={lead} /></div> : null,
      };
    }),
    ...loose.filter(ownerTurn).map((st): TurnItem => ({
      key: `stage-${st.key}`, tone: st.tone === "bad" ? "bad" : "warn", title: st.title, line: st.headline, detail: st.detail, since: st.since,
      projectId: st.project_id, way: hasWay(st), actions: (lead) => <StageActions st={st} lead={lead} onChange={reload} />,
    })),
    ...uses.map((a): TurnItem => ({
      key: `use-${a.p.id}`, tone: "warn", title: a.p.name, line: t(USE_LINE[a.u.state]), projectId: a.p.id,
      openJob: a.u.job_id && a.u.job_id !== job?.id && jobIds.has(a.u.job_id) ? a.u.job_id : null, way: true,
      actions: (lead) => <UseAction a={a} lead={lead} onChange={reload} />,
    })),
  ];
  const waiting = loose.filter((st) => !ownerTurn(st));
  const detailOf = job && (
    <div ref={detail} className="scroll-target eng-job">
      <BuildDetail key={job.id} job={job} title={title(job)} spec={specOf(job.spec_id)} stage={stageOfJob.get(job.id)} offered={needsYou(job)}
        turn={ownerTurn(stageOfJob.get(job.id))} page={page} held={worker.down} paused={!!worker.label} onChange={reload} />
    </div>
  );

  return (
    <div className={page ? "owner-page kui mx eng" : "mx eng"}>
      <AboutSubject subject={job ? { kind: "dev_job", ref: String(job.id) } : null} layer="page" />
      {head(devStatus(jobs, stages, uses, turnItems.length, worker.down))}
      {page && (
        <details className="eng-about">
          <summary className="small"><Icon name="chevron" size={12} />{t("build.about")}</summary>
          <p className="small">{t("build.sub")}</p>
        </details>
      )}

      <TurnStrip items={turnItems} page={page} href={href} />

      {!job ? (
        <Hint title={t("build.jobs.empty.title")}>{t("build.jobs.empty.body")}</Hint>
      ) : jobs.length === 1 ? detailOf : (
        <div className="eng-split">
          <BuildList turn={turn} active={active} finished={finished} selected={job} titleOf={title} needsYou={needsYou} href={href} />
          {detailOf}
        </div>
      )}

      <details className="eng-specs" open={!jobs.length || waiting.length > 0 ? true : undefined}>
        <summary>
          <Icon name="chevron" size={14} />
          <span className="eng-specs-t">{t("build.specs.title")}</span>
          <span className="sec-n">{current.length + past.length}</span>
        </summary>
        <div className="eng-specs-b">
          {waiting.length > 0 && (
            <ul className="eng-now" aria-label={t("build.specs.title")}>
              {waiting.map((st) => <StageCard key={st.key} st={st} page={page} lead={false} held={worker.down} onChange={reload} />)}
            </ul>
          )}
          {current.length === 0 ? (waiting.length === 0 && <Hint title={t("build.specs.empty.title")}>{t("build.specs.empty.body")}</Hint>)
            : <SpecList specs={current} page={page} />}
          {past.length > 0 && (
            <details className="eng-raw">
              <summary className="small">{t("build.specs.past", { n: past.length })}</summary>
              <SpecList specs={past} page={page} />
            </details>
          )}
        </div>
      </details>
    </div>
  );
}

/** The dev team in one sentence: what waits on the owner (the strip below names each), then what is moving, which
 * waits for the worker when none is running. */
function devStatus(jobs: Row[], stages: Stage[], uses: AfterMerge[], waits: number, workerDown: boolean): Status {
  // Before the first job, the server's stage line (spec being written, waiting on budget...) is the conclusion.
  if (!jobs.length && !uses.length) {
    const lead = stages[0];
    return lead ? { tone: lead.tone === "wait" ? "muted" : lead.tone, text: lead.headline }
      : { text: t("build.status.none") };
  }
  const at = (key: string) => jobs.filter((j) => groupOf(j.status) === key);
  // a spec's latest attempt only: an older failed attempt is not a stop
  const failed = stages.some((st) => st.tone === "bad" && st.dev_job_id);
  const running = at("running").length;
  const queued = at("queued").length;
  const moving = workerDown && (running || queued)
    ? <>{t("build.status.worker_down", { n: running + queued })} <Fix text={`\`${WORKER_FIX}\`.`} /></>
    : running ? (queued ? t("build.status.running_queued", { n: running, queued }) : t("build.status.running", { n: running }))
    : queued ? t("build.status.queued", { n: queued }) : null;
  return {
    tone: failed ? "bad" : waits ? "warn" : running && !workerDown ? "run" : "muted",
    text: <>
      {waits ? <>{t("build.status.turn", { n: waits })} </> : null}
      {moving}
      {!waits && !moving ? t("build.status.quiet", { n: at("merged").length }) : null}
    </>,
  };
}
