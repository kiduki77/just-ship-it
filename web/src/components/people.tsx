import { Link, useSearchParams } from "react-router-dom";
import { qs, Row } from "../api";
import { AGENT_KIND, AgentStatus } from "../agentState";
import { cleanName, useBusinessLive, useBusinessSelection, useCompanyLive } from "../business";
import { Chip, Scope } from "./kit";
import { useShowTest } from "./ui";
import { useWorker } from "./frame";
import { t } from "../i18n";

/*
 * Pieces the people screens share (agents, an agent's page): which scope a screen counts in, and the
 * one chip and one work link an agent's status is drawn with.
 */

export type { Scope } from "./kit";

/**
 * The scope a screen counts in, kept in the address (`?scope=`). Each screen passes its own default, so adding the
 * switch did not change what any screen showed before: agents count in the business.
 */
export function useScope(fallback: Scope): [Scope, (s: Scope) => void, string] {
  const [params, setParams] = useSearchParams();
  const raw = params.get("scope");
  const scope: Scope = raw === "business" || raw === "company" ? raw : fallback;
  const set = (s: Scope) => {
    const next = new URLSearchParams(params);
    if (s === fallback) next.delete("scope");
    else next.set("scope", s);
    setParams(next, { replace: true });
  };
  return [scope, set, raw === "business" || raw === "company" ? `scope=${raw}` : ""];
}

/** The agent's status chip; "Working" never shows while no worker runs (`useWorker`). */
export function AgentChip({ s }: { s: AgentStatus }) {
  const worker = useWorker();
  const k = AGENT_KIND[s.kind];
  return <Chip tone={k.tone}>{s.kind === "waiting" ? worker.label ?? t(k.label) : t(k.label)}</Chip>;
}

/** One piece of current work, by its title, linked to where it lives. */
export function WorkLink({ w }: { w: Row }) {
  const to = w.task_id ? `/projects/${w.project_id}/tasks?task=${w.task_id}`
    : w.dev_job_id ? `/projects/${w.project_id}/dev?job=${w.dev_job_id}` : null;
  const body = <>{w.role === "review" && <span className="pp-role">{t("company.agents.review_role")}</span>}{w.title}</>;
  return to ? <Link to={to} title={w.title}>{body}</Link> : <span title={w.title}>{body}</span>;
}

/** A failed task, linked to its panel on the project's task board. */
export function TaskLink({ task }: { task: Row }) {
  return <Link to={`/projects/${task.project_id}/tasks?task=${task.id}`} title={task.title}>{task.title}</Link>;
}

const AGENT_TABLES = ["tasks", "dev_jobs", "activity_events", "llm_calls"];

/** The agents of the scope the owner picked, with the FAILED tasks that decide who is "Failed". */
export function useAgents(company: boolean) {
  const [showTest] = useShowTest();
  const list = `/agents${qs({ include_test: showTest })}`;
  const failed = `/tasks${qs({ status: "FAILED", include_test: showTest, limit: 500 })}`;
  const inBusiness = useBusinessLive<Row[]>(company ? null : list, AGENT_TABLES);
  const inCompany = useCompanyLive<Row[]>(company ? list : null, AGENT_TABLES);
  const fBusiness = useBusinessLive<{ tasks: Row[] }>(company ? null : failed, ["tasks"]);
  const fCompany = useCompanyLive<{ tasks: Row[] }>(company ? failed : null, ["tasks"]);
  return { agents: company ? inCompany : inBusiness, failed: company ? fCompany : fBusiness };
}

