/**
 * The address of one job's row on the teams screen. The project narrows the list to the job's own project (it wins over
 * the business on screen), and `focus` names the row to bring into view. It travels as a query value, not a `#hash`,
 * because the shell rewrites the query on every business change and would drop a hash.
 */
export type CrewKind = "task" | "dev_job";

export const crewKey = (kind: CrewKind, id: number) => `${kind === "task" ? "task" : "job"}-${id}`;

export const crewHref = (kind: CrewKind, projectId: number, id: number) =>
  `/crews?project_id=${projectId}&focus=${crewKey(kind, id)}`;
