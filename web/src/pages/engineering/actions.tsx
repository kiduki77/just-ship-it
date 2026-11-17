import { Link } from "react-router-dom";
import { post } from "../../api";
import { ItemLink } from "../../components/panel";
import { ActionButton } from "../../components/ui";
import { PlainKey, t } from "../../i18n";
import { AfterMerge, Stage } from "./model";

type DevAction = "deliver_again" | "accept_again" | "fix_or_drop_again" | "fix_again" | "merge_again" | "discard";

/** The owner's ways on for a job that nothing else will move: the server lists which apply. */
const DEV_ACTIONS: Record<DevAction, { path: string; label: PlainKey; done: PlainKey; hint: PlainKey }> = {
  deliver_again: { path: "deliver-again", label: "build.action.deliver_again", done: "build.action.deliver_again.done",
    hint: "build.action.deliver_again.hint" },
  accept_again: { path: "accept-again", label: "build.action.accept_again", done: "build.action.accept_again.done",
    hint: "build.action.accept_again.hint" },
  fix_or_drop_again: { path: "fix-or-drop-again", label: "build.action.fix_or_drop_again",
    done: "build.action.fix_or_drop_again.done", hint: "build.action.fix_or_drop_again.hint" },
  fix_again: { path: "fix-again", label: "build.action.fix_again", done: "build.action.fix_again.done",
    hint: "build.action.fix_again.hint" },
  merge_again: { path: "merge-again", label: "build.action.merge_again", done: "build.action.merge_again.done",
    hint: "build.action.merge_again.hint" },
  discard: { path: "discard", label: "build.action.discard", done: "build.action.discard.done",
    hint: "build.action.discard.hint" },
};

/** The one way on that leads, the first of these the server offers. */
const PRIMARY: DevAction[] = ["deliver_again", "fix_or_drop_again", "fix_again", "merge_again"];

/** `lead`: these are the owner's first way on; a stage whose link leads (raise the budget first), or a row below the
 * first one, passes false so the page keeps one primary button. */
export function DevJobActions({ jobId, actions, onChange, lead = true }: { jobId: number; actions: string[]; onChange: () => void; lead?: boolean }) {
  const primary = lead ? PRIMARY.find((k) => actions.includes(k)) : undefined;
  return (
    <>
      {actions.filter((k): k is DevAction => k in DEV_ACTIONS).map((k) => (
        <ActionButton key={k} className={k === "discard" ? "danger" : k === primary ? "btn-primary" : "btn-ghost"}
          confirm={k === "discard" ? t("build.action.discard.confirm") : undefined} title={t(DEV_ACTIONS[k].hint)}
          run={() => post(`/dev-jobs/${jobId}/${DEV_ACTIONS[k].path}`, {}).then(onChange)} done={t(DEV_ACTIONS[k].done)}>
          {t(DEV_ACTIONS[k].label)}
        </ActionButton>
      ))}
    </>
  );
}

/** A stage's buttons. `lead` is true for the page's first item with a way on: only it draws a primary button. */
export function StageActions({ st, lead, onChange }: { st: Stage; lead: boolean; onChange: () => void }) {
  const ownAction = !!st.link || st.actions.length > 0;
  if (!ownAction && !st.retry_dev && !st.retry_job) return null;
  return (
    <div className="row wrap eng-act">
      {st.link && <ItemLink className={lead ? "btn-primary" : "btn-ghost"} to={st.link.to}>{st.link.label}</ItemLink>}
      {st.dev_job_id && st.actions.length > 0 && <DevJobActions jobId={st.dev_job_id} actions={st.actions} onChange={onChange} lead={lead && !st.link} />}
      {st.retry_job && (
        <ActionButton className={lead && !ownAction ? "btn-primary" : "btn-ghost"} run={() => post(`/jobs/${st.retry_job}/retry`, {}).then(onChange)}
          done={t("build.retry.done")}>{t("build.retry")}</ActionButton>
      )}
      {st.retry_dev && st.dev_job_id && (
        // a rebuild costs time and budget again: never the page's primary button
        <ActionButton className="btn-ghost" confirm={t("build.rebuild.confirm")}
          title={st.retry_wait ?? t("build.rebuild.hint")} disabled={!!st.retry_wait}
          run={() => post(`/dev-jobs/${st.dev_job_id}/retry`, {}).then(onChange)} done={t("build.rebuild.done")}>
          {t("build.rebuild")}
        </ActionButton>
      )}
    </div>
  );
}

/** The last step of a merged build, which is the owner's: decide, do the to-do, or ask again where people use it. */
export function UseAction({ a, lead, onChange }: { a: AfterMerge; lead: boolean; onChange: () => void }) {
  const { p, u } = a;
  const cls = lead ? "btn-primary" : "btn-ghost";
  return (
    <div className="row wrap eng-act">
      {u.state === "decide" ? <Link className={cls} to={`/inbox?project=${p.id}`}>{t("work.go_decide")}</Link>
        : u.state === "todo" ? <ItemLink className={cls} to={`/my-tasks?ot=${u.task_id}`}>{t("work.go_todo")}</ItemLink>
        : <ActionButton className={cls} run={() => post(`/dev-jobs/${u.job_id}/use-again`, {}).then(onChange)} done={t("build.use.again_done")}>{t("work.use_again")}</ActionButton>}
    </div>
  );
}
