import { matchPath } from "react-router-dom";
import type { PageRef, Subject } from "./subject";

type Make = (params: Readonly<Record<string, string | undefined>>) => Subject;

const page = (ref: PageRef): Make => () => ({ kind: "page", ref });
const general: Make = () => ({ kind: "general" });
const project: Make = ({ id }) => (id && /^\d+$/.test(id) ? { kind: "project", ref: id } : { kind: "general" });

/**
 * What each screen is about when it says nothing itself, by the path of its `<Route>` in main.tsx. Every route has an
 * entry, so no screen is without a subject (tests/test_chat_routes.py fails on one that is missing, and on a page
 * the server does not know). A redirect and the not-found screen are about General.
 */
export const ROUTE_SUBJECTS: Record<string, Make> = {
  "/": page("home"),
  "/my-tasks": page("my_tasks"),
  "/business": page("business"),
  "/businesses/new": page("new_business"),
  "/kickoff/:id": page("kickoff"),
  "/inbox": page("inbox"),
  "/approvals": general,
  "/tasks": page("tasks"),
  "/projects": page("projects"),
  "/projects/:id/*": project,
  "/agents": page("agents"),
  "/agents/:id": page("agents"),
  "/audit/*": page("audit"),
  "/usage": general,
  "/operations": general,
  "/crews": page("crews"),
  "/org": page("crews"),
  "/engineering": page("engineering"),
  "/skills": page("skills"),
  "/routines": page("routines"),
  "/settings": general,
  "*": general,
};

export function subjectForPath(pathname: string): Subject {
  for (const [path, make] of Object.entries(ROUTE_SUBJECTS)) {
    if (path === "*") continue;
    const m = matchPath(path, pathname);
    if (m) return make(m.params);
  }
  return ROUTE_SUBJECTS["*"]({});
}
