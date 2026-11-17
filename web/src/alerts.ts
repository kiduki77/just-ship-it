import { Row } from "./api";
import { usd, when } from "./components/ui";
import { t } from "./i18n";

const devState = (a: Row) =>
  t(a.stop === "budget" ? "common.alert.dev_budget" : a.stop === "deliver" ? "common.alert.dev_deliver" : "common.alert.dev_failed")
  + (a.actions?.includes("discard") ? t("common.alert.dev_pr_open") : "");

const ALERT_TEXT: Record<string, (a: Row) => string> = {
  budget_stop: (a) => t("common.alert.budget_stop", { project: a.project }),
  project_failed: (a) => t("common.alert.project_failed", { project: a.project }),
  project_paused: (a) => a.detail ? t("common.alert.project_paused_because", { project: a.project, detail: a.detail })
    : t("common.alert.project_paused", { project: a.project }),
  budget_warning: (a) => t("common.alert.budget_warning", { project: a.project, detail: a.detail }),
  month_budget_warning: (a) => t("common.alert.month_budget_warning", { detail: a.detail }),
  task_failed: (a) => a.handled ? t("common.alert.task_handled", { title: a.title, reason: a.reason })
    : t(budgetShort(a) ? "common.alert.task_budget" : "common.alert.task_failed", { title: a.title }),
  dev_failed: (a) => t("common.alert.dev", { title: a.title, state: devState(a) }),
  dev_delivered: (a) => t("common.alert.dev", { title: a.title, state: t(
    a.request_id ? "common.alert.dev_reapprove" : a.actions.includes("accept_again") ? "common.alert.dev_accept_failed" : "common.alert.dev_merge") }),
  // What failed and why: a queue job's number means nothing to the owner, and its error is the server's `reason`.
  job_failed: (a) => budgetShort(a) ? t("common.alert.job_budget", { title: a.title })
    : a.reason ? t("common.alert.job_failed_because", { title: a.title, reason: a.reason }) : t("common.alert.job_failed", { title: a.title }),
  worker_down: (a) => t(a.at ? "common.alert.worker_silent" : "common.alert.worker_never"),
  system_problem: (a) => t("common.alert.system", { title: a.title, fix: a.detail }),
  usage_wait: (a) => t("common.alert.usage_wait", { reason: a.reason_label, until: a.until_local, n: a.jobs.length,
    jobs: a.waiting_work }),
  slack_disconnected: (a) => t("common.alert.slack", { n: a.waiting }),
  run_stalled: (a) => t("common.alert.run_stalled", { project: a.project }),
  dev_stalled: (a) => t("common.alert.dev_stalled", { title: a.title }),
  job_waiting: (a) => t(a.resumes ? "common.alert.job_waiting_resumes" : "common.alert.job_waiting",
    { what: `${a.project ? `${a.project} ` : ""}${a.title}`, when: when(a.until) }),
};

/** Work a budget stopped that a retry would not get past: the owner's way on is raising that budget. */
export const budgetShort = (a: Row): boolean => a.stop === "budget" && !!a.budget_short;
/** When the company has already asked for the money, the way on is that decision: the same raise, one place to do it. */
export const budgetHref = (a: Row): string =>
  a.budget_ask ? `/inbox?req=${a.budget_ask.id}`
    : a.budget_project_id ? `/projects/${a.budget_project_id}/settings#budget` : "/audit/costs";
export const budgetAction = (a: Row): string =>
  a.budget_ask ? t("decide.alert.review_ask", { amount: usd(a.budget_ask.amount_usd) }) : t("decide.alert.raise_budget");

export function alertHref(a: Row): string {
  if ((a.kind === "job_failed" || a.kind === "task_failed") && budgetShort(a)) return budgetHref(a);
  if (a.kind === "task_failed") return `/projects/${a.project_id}/tasks?task=${a.task_id}`;
  if (a.kind === "dev_failed" && a.stop === "budget") return `/projects/${a.project_id}/settings#budget`;
  if (a.kind === "dev_failed" || a.kind === "dev_stalled") return `/projects/${a.project_id}/dev?job=${a.dev_job_id}`;
  if (a.kind === "dev_delivered") return a.request_id ? `/inbox?req=${a.request_id}` : `/projects/${a.project_id}/dev?job=${a.dev_job_id}`;
  // where the owner can act on it: the worker and usage caps are fixed from Settings > This PC and Usage, not from a queue table
  if (a.kind === "worker_down") return "/settings#set-system";
  if (a.kind === "usage_wait") return "/settings#set-usage";
  // the failed job itself, opened on the background-work list with its error and its way on
  if (a.kind === "job_failed" && a.job_id) return `/audit/queue?status=failed&job=${a.job_id}`;
  if (a.kind === "job_failed" && a.project_id) return `/projects/${a.project_id}`;
  if (a.kind === "job_failed" || (a.kind === "job_waiting" && !a.project_id)) return "/audit/queue";
  if (a.kind === "slack_disconnected") return "/settings#set-connections";
  if (a.kind === "system_problem") return "/settings#set-system";
  if (a.kind === "month_budget_warning") return "/audit/costs";
  if (a.kind === "budget_stop" && a.budget_ask) return budgetHref(a);
  if (a.kind === "budget_stop" || a.kind === "budget_warning") return `/projects/${a.project_id}/settings#budget`;
  return `/projects/${a.project_id}`;
}
/** An alert of no business (the worker, the monthly budget, Slack, usage) shows under every business and says so. */
export const alertText = (a: Row) => {
  const text = (ALERT_TEXT[a.kind] ?? ((x: Row) => x.title))(a);
  return a.company_wide && a.kind !== "system_problem" ? t("common.alert.company_wide", { text }) : text;
};

/** Hitting a cap, a pause or a wait is the owner's turn, not a failure: amber. Red is kept for things that broke and
 * wait on the owner; what the company is already fixing is calm. */
const WAITING_KINDS = new Set(["budget_stop", "budget_warning", "month_budget_warning", "project_paused", "usage_wait", "job_waiting"]);
export const alertTone = (a: Row): "bad" | "warn" | "calm" =>
  a.handled ? "calm"
    : (a.severity === "bad" && !WAITING_KINDS.has(a.kind) && !(a.kind === "dev_failed" && a.stop === "budget") && !budgetShort(a) ? "bad" : "warn");

/**
 * The one count of "things stopped or failed" every screen shows (home box, Decisions banner and Alerts tab, Tasks
 * badge): the alerts the company is not already redoing. A failed task the CEO is redoing is listed, calm, and counted
 * apart. The Tasks badge is the tasks among them.
 */
export function alertCounts(alerts: Row[]) {
  const need = alerts.filter((a) => alertTone(a) !== "calm");
  const of = (kind: string) => need.filter((a) => a.kind === kind).length;
  const tasks = of("task_failed");
  const projects = of("project_failed") + of("budget_stop") + of("project_paused") + of("run_stalled");
  // the worker and the monthly budget, which every business shows and the switcher leaves out of each business's count
  const company = need.filter((a) => a.company_wide).length;
  return { need: need.length, redoing: alerts.length - need.length, tasks, projects, company,
    other: need.length - tasks - projects - company, bad: need.some((a) => alertTone(a) === "bad") };
}
