import { useEffect, useSyncExternalStore } from "react";
import { api } from "./api";

/**
 * What the codes in an agent's text point at (`GET /api/refs`): a task, decision or to-do by its title, a piece of
 * evidence by its claim. The console shows these, never the code. Every text on a page asks at once, in one request.
 */
const known = new Map<string, string | null>(); // null: no such record, or the server could not say
const projects = new Map<string, number>(); // a piece of evidence's project
const asked = new Set<string>(); // queued or on the way
let queued: string[] = [];
const subs = new Set<() => void>();
let version = 0;

function flush() {
  for (let i = 0; i < queued.length; i += 200) load(queued.slice(i, i + 200)); // the server reads 200 at a time
  queued = [];
}

function load(codes: string[]) {
  api<Record<string, { title: string; project_id?: number | null }>>(`/refs?codes=${encodeURIComponent(codes.join(","))}`)
    .then((got) => codes.forEach((c) => {
      known.set(c, got[c]?.title ?? null);
      const pid = got[c]?.project_id;
      if (pid) projects.set(c, pid);
    }))
    .catch(() => codes.forEach((c) => known.set(c, null)))
    .finally(() => {
      version++;
      subs.forEach((f) => f());
    });
}

function ask(codes: string[]) {
  const fresh = codes.filter((c) => !asked.has(c));
  fresh.forEach((c) => asked.add(c));
  if (fresh.length && !queued.length) setTimeout(flush, 0);
  queued.push(...fresh);
}

const subscribe = (f: () => void) => {
  subs.add(f);
  return () => void subs.delete(f);
};

/** The title for a code, once known; undefined while it is being asked, null when there is none. */
export const refTitle = (code: string): string | null | undefined => known.get(code);

/** The project a piece of evidence belongs to, once known (`EV-12` → 3). */
export const refProject = (code: string): number | undefined => projects.get(code);

/** Ask for the codes' titles; the returned number changes when an answer arrives, for a memo to recompute on. */
export function useRefs(codes: string[]): number {
  const v = useSyncExternalStore(subscribe, () => version);
  const key = codes.join(",");
  useEffect(() => {
    if (key) ask(key.split(","));
  }, [key]);
  return v;
}
