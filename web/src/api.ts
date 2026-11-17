import { useCallback, useEffect, useRef, useState } from "react";
import { t } from "./i18n";

export type Row = Record<string, any>;

let loggedOut = false;
const loginSubs = new Set<() => void>();

/** True once the server has refused this browser's login; the console then only says how to log in. */
export function useLoggedOut(): boolean {
  const [v, setV] = useState(loggedOut);
  useEffect(() => {
    const f = () => setV(true);
    loginSubs.add(f);
    if (loggedOut) setV(true);
    return () => {
      loginSubs.delete(f);
    };
  }, []);
  return v;
}

function markLoggedOut() {
  if (loggedOut) return;
  loggedOut = true;
  source?.close();
  source = null;
  loginSubs.forEach((f) => f());
}

/**
 * Why a request failed, as the owner reads it. `offline` is the browser's own failure (no server answered), `login`
 * a refused cookie, `missing` a 404, `server` a 5xx, `rejected` any other 4xx (the server's own sentence, or the
 * fields to check). The message is already the owner's sentence, so a screen can show it as it is.
 */
// A 502 is an outside service (Slack) refusing or failing: the server's sentence says which and what to do, where a
// status line would tell the owner to wait for something only they can fix.
const OUTSIDE_SERVICE = 502;
export type ApiFailure = "offline" | "login" | "missing" | "server" | "rejected";

export class ApiError extends Error {
  constructor(public readonly kind: ApiFailure, public readonly status: number | null, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

const kindOf = (status: number): ApiFailure => (status === 401 ? "login" : status === 404 ? "missing" : status >= 500 ? "server" : "rejected");

export async function api<T = any>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      // The server refuses changes without this header: a page on another site cannot add it.
      headers: { "Content-Type": "application/json", "X-Ship": "1" },
      // a data call is never answered from the browser's memory: an old page kept for this address would be replayed
      cache: "no-store",
      ...init,
    });
  } catch (e) {
    // a cancelled request is the caller's doing, not an outage
    if ((e as Error)?.name === "AbortError") throw e;
    throw new ApiError("offline", null, t("common.err.offline", { cmd: "ship supervise" }));
  }
  if (res.status === 401) markLoggedOut();
  if (!res.ok) {
    const kind = kindOf(res.status);
    let detail = kind === "missing" ? t("common.err.missing") : kind === "login" ? t("common.err.login")
      : t("common.err.server", { status: res.status });
    if (kind === "rejected" || res.status === OUTSIDE_SERVICE) {
      try {
        const body = (await res.json()).detail;
        if (typeof body === "string") detail = body;
        // FastAPI's validation errors are a list of English messages; name the fields instead.
        else if (Array.isArray(body))
          detail = t("common.check_fields", { fields: body.map((e: Row) => (e.loc ?? []).slice(1).join(".")).join(", ") });
      } catch {
        /* not JSON: keep the status line */
      }
    }
    throw new ApiError(kind, res.status, detail);
  }
  // a 200 that is not JSON is the console's own page: the address is not one the server knows
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    throw new ApiError("server", res.status, t("common.err.not_json", { path: `/api${path}` }));
  }
}

/** The failure kind behind an error a hook or an action reported, for a screen that treats a missing item apart. */
export const failureOf = (e: unknown): ApiFailure | null => (e instanceof ApiError ? e.kind : null);

export const post = <T = any>(path: string, body: unknown) =>
  api<T>(path, { method: "POST", body: JSON.stringify(body) });

type Listener = (e: Row) => void;
const listeners = new Set<Listener>();
let source: EventSource | null = null;

export type Connection = "connecting" | "live" | "lost";
let connection: Connection = "connecting";
/** When live updates last flowed, so the frame can say how old what is on screen may be. */
export let liveUntil: string | null = null;
const connSubs = new Set<(c: Connection) => void>();
const setConnection = (c: Connection) => {
  if (c === "lost" && connection !== "lost") liveUntil = new Date().toISOString();
  connection = c;
  connSubs.forEach((f) => f(c));
};

/** Whether live updates are flowing; the console shows it so a quiet page is never mistaken for a stuck one. */
export function useConnection(): Connection {
  const [c, setC] = useState(connection);
  useEffect(() => {
    ensureSource();
    connSubs.add(setC);
    return () => {
      connSubs.delete(setC);
    };
  }, []);
  return c;
}

function ensureSource() {
  if (source || loggedOut) return;
  source = new EventSource("/api/events");
  source.onopen = () => {
    // Events sent while disconnected are gone: after a reconnect every live view refetches once.
    const missed = connection === "lost";
    setConnection("live");
    if (missed) listeners.forEach((l) => l({ table: "*" }));
  };
  source.onmessage = (m) => {
    try {
      const data = JSON.parse(m.data);
      listeners.forEach((l) => l(data));
    } catch {
      /* ignore malformed */
    }
  };
  source.onerror = () => {
    setConnection("lost");
    source?.close();
    source = null;
    setTimeout(ensureSource, 3000);
  };
}

/** Refetch every live view that reads one of `tables` now, instead of after the wait that batches a burst of events. */
export function refreshLive(tables: string[]) {
  for (const table of tables) listeners.forEach((l) => l({ table, now: true }));
}

export function useLiveEvents(fn: Listener) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    ensureSource();
    const l: Listener = (e) => ref.current(e);
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  }, []);
}

/**
 * Fetch `path`, refetching (debounced) whenever a change event matches `tables`. `error` is the owner's sentence and
 * `failure` says why (a missing item, the server, no server at all), so a screen can offer the right way out.
 */
export function useLive<T = any>(path: string | null, tables: string[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [failure, setFailure] = useState<ApiFailure | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const seq = useRef(0);
  const shown = useRef<string | null>(null);
  const wanted = useRef(path);
  wanted.current = path;

  const load = useCallback(() => {
    if (!path) return;
    // Only the newest request may land: a slow response for an old path or filter must not overwrite a newer one. The
    // exception is the first answer for this path: refetches on every reconnect would otherwise keep a slow screen empty.
    const mine = ++seq.current;
    api<T>(path)
      .then((d) => {
        if (path !== wanted.current || (mine !== seq.current && shown.current === path)) return;
        shown.current = path;
        setData(d);
        setError(null);
        setFailure(null);
      })
      .catch((e) => {
        if (path !== wanted.current || mine !== seq.current) return;
        setError(String(e.message ?? e));
        setFailure(failureOf(e) ?? "server");
      });
  }, [path]);

  useEffect(() => {
    setData(null);
    setError(null);
    setFailure(null);
    shown.current = null;
    load();
  }, [load]);

  useLiveEvents((e) => {
    if (e.table !== "*" && tables.length && !tables.includes(e.table)) return;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(load, e.now ? 0 : 600);
  });

  return { data, error, failure, reload: load };
}

/** `/api/health` as the console reads it: whether a worker has reported within its lease, and when it last did. */
export interface Health {
  ok: boolean;
  api: boolean;
  database: boolean;
  worker: { alive: boolean; last_seen: string | null };
}

const HEALTH_EVERY_MS = 30_000;

/**
 * The server's health, polled every half minute and again when a worker row changes, so the frame can say in one
 * place whether the company can run at all. Null until the first answer arrives.
 */
export function useHealth(): { health: Health | null; failed: boolean; reload: () => void } {
  const { data, failure, reload } = useLive<Health>("/health", ["workers"]);
  useEffect(() => {
    const id = window.setInterval(reload, HEALTH_EVERY_MS);
    return () => window.clearInterval(id);
  }, [reload]);
  return { health: data, failed: failure !== null, reload };
}

/** Other businesses with something waiting for the owner, as `/today` may list them (`elsewhere`, additive). */
export interface Elsewhere {
  business_id: number;
  name: string;
  decisions: number;
  todos: number;
}

/** The owner's timezone: daily charts bucket by the owner's day, not UTC's. */
export const TZ = (() => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
})();

/** Query string from an object, skipping empty values. */
export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "" && v !== false) u.set(k, String(v));
  return u.size ? `?${u}` : "";
}

/**
 * The merge card's walkthrough and its fold of developer checks, as `review.walkthrough` and `review.checked` carry
 * them (engineering/merge_review.py), and the journeys a spec card lists. A step is what the build really printed when
 * the agent ran it, never what the spec predicted.
 */
export type WalkState = "passed" | "differs" | "not_run";
export type WalkFault = "product" | "scenario" | "environment";
export type WalkNotRun = "unbound" | "no_docker" | "setup_failed" | "earlier_failed" | "out_of_time" | "needs" | "unreachable";

/** The first lines of the file a step's `show` names. A binary file has no text, only its size. `volatile` spans are
 * [start, end) offsets into `text`, counted in code points. */
export interface WalkFile { path: string; text: string; lines: number; volatile: [number, number][]; binary: boolean; size: number }

/** What one step printed, cleaned. `lines` counts the lines before cutting; `cut_after` is how many lines of `text`
 * come before the cut, null when nothing was cut. An `open` step has a `shot` (a URL) and no exit. */
export interface WalkCapture {
  text: string;
  lines: number;
  cut_after: number | null;
  exit: number | null;
  ms: number;
  shot: string | null;
  file: WalkFile | null;
  volatile: [number, number][];
  scrubbed: { kind: string; n: number }[];
}

export interface WalkSeed { path: string; text: string }

export interface WalkStep {
  id: string;
  do: string;
  why: string;
  see: string;
  kind: "run" | "open";
  action: string;
  expect: "ok" | "error";
  seed: WalkSeed[];
  capture: WalkCapture | null;
  state: WalkState;
  not_run: WalkNotRun | null;
  need: string | null;
  fault: WalkFault | null;
  /** Who said whose fault it is: the server's own signals, or the product agent's reading. */
  fault_by: "server" | "agent" | null;
  /** False for a captured step the product agent gave no verdict. */
  judged: boolean;
  looks_like: string;
  hint: string;
}

export interface WalkJourney {
  id: string;
  priority: "P1" | "P2";
  title: string;
  persona: string;
  why: string;
  brings: string;
  success: string;
  steps: WalkStep[];
}

export interface WalkAbout { what: string; who: string; problem: string }

export interface Walkthrough {
  head: string;
  spec_commit: string;
  ran_at: string;
  ms: number;
  setup_ms: number;
  /** The card's own commit has no run; this is the newest run, of a later commit. */
  later: boolean;
  about: WalkAbout | null;
  setup: WalkStep[];
  journeys: WalkJourney[];
}

export type CheckedKind = "core_flow" | "story" | "scenarios" | "rule" | "asked" | "spec";

export interface CheckedItem {
  criterion: string;
  met: boolean;
  evidence: string;
  /** What the test catches, in one line the owner reads. Empty when the spec gave none. */
  guards: string;
  scenario: { given: string; when: string; then: string } | null;
}

export interface CheckedGroup {
  kind: CheckedKind;
  /** The story's title for a `story` group; empty for the others, which the console words itself. */
  title: string;
  /** The story sentence for a `story` group; empty for the others. */
  purpose: string;
  total: number;
  met: number;
  items: CheckedItem[];
}

export interface Checked { total: number; met: number; groups: CheckedGroup[] }

/** A journey as the spec approval card lists it. */
export interface SpecJourney { id: string; priority: "P1" | "P2"; title: string; persona: string; success: string }
/** Why the spec writer gave no usable journeys, when it could not after one retry. */
export type JourneysMissing = "none" | "too_many" | "steps" | "see" | "test";
