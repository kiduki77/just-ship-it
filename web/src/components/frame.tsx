import { createContext, ReactNode, useContext, useMemo } from "react";
import { Connection, Health, liveUntil, useConnection, useHealth, useLive } from "../api";
import { useBusinessSelection } from "../business";
import { t } from "../i18n";
import type { Stop } from "./stop";

/**
 * The one place the console reads "can the company run right now". Every page reads it through `useFrame`,
 * `useWorker` or `useCompanyState`, so no screen draws "Working" from a database row while the worker is down.
 */

export type WorkerState = "unknown" | "alive" | "silent" | "never";

/** The company's state, one value, the most serious condition first. */
export type CompanyState =
  | { kind: "unknown" }
  | { kind: "lost"; since: string | null }
  | { kind: "unreachable" }
  | { kind: "stopped"; stop: Stop }
  | { kind: "worker_never" }
  | { kind: "worker_silent"; lastSeen: string | null }
  | { kind: "usage_wait"; text: string; link: string }
  | { kind: "running" };

interface Frame {
  health: Health | null;
  healthFailed: boolean;
  connection: Connection;
  /** The owner's stop of the whole company, from `/api/company`, which no business or list stands between. */
  stop: Stop | null;
  stopKnown: boolean;
  reloadStop: () => void;
  /** Ask `/api/health` again now, after the owner started the worker. */
  recheck: () => void;
  worker: { state: WorkerState; lastSeen: string | null };
  company: CompanyState;
}

const Ctx = createContext<Frame | null>(null);

/** The fix for a worker that is down: the one command every screen names. */
export const WORKER_FIX = "uv run ship supervise";

function workerOf(health: Health | null): Frame["worker"] {
  if (!health) return { state: "unknown", lastSeen: null };
  const seen = health.worker.last_seen;
  return { state: health.worker.alive ? "alive" : seen ? "silent" : "never", lastSeen: seen };
}

export function FrameProvider({ children }: { children: ReactNode }) {
  const { health, failed, reload: recheck } = useHealth();
  const connection = useConnection();
  const { data: companyRow, reload: reloadStop } = useLive<{ stopped: Stop | null }>("/company", ["activity_events"]);
  const { today } = useBusinessSelection();
  const worker = useMemo(() => workerOf(health), [health]);
  const stop = companyRow?.stopped ?? null;
  const value = useMemo<Frame>(() => {
    const company: CompanyState =
      connection === "lost" ? { kind: "lost", since: liveUntil }
        : failed ? { kind: "unreachable" }
        : stop ? { kind: "stopped", stop }
          : worker.state === "never" ? { kind: "worker_never" }
            : worker.state === "silent" ? { kind: "worker_silent", lastSeen: worker.lastSeen }
              : today?.banner ? { kind: "usage_wait", text: today.banner.text, link: today.banner.link }
                : worker.state === "alive" ? { kind: "running" } : { kind: "unknown" };
    return { health, healthFailed: failed, connection, stop, stopKnown: companyRow !== null, reloadStop, recheck, worker, company };
  }, [health, failed, connection, stop, companyRow, reloadStop, recheck, worker, today?.banner]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useFrame(): Frame {
  const v = useContext(Ctx);
  if (!v) throw new Error("FrameProvider is missing");
  return v;
}

/**
 * The worker as a page should read it before it says anything is "working". `down` is true when no worker is alive,
 * `stopped` when the owner stopped the company, and `label` is the sentence to show instead of "Working" in either
 * case ("Waiting for the background process", "Paused by you"); null while work can run. `running(code)` gives the word for a
 * row in a running state: the same label, or "Stopping at its next step" while a stop is settling.
 */
export function useWorker(): {
  state: WorkerState; down: boolean; stopped: boolean; lastSeen: string | null; label: string | null; running: (code?: string) => string | null;
} {
  const { worker, stop } = useFrame();
  const down = worker.state === "silent" || worker.state === "never";
  const stopped = stop !== null;
  const label = down ? t("shell.state.waiting_worker") : stopped ? t("shell.state.stopped_work") : null;
  const running = (code?: string) => (down ? label : stopped ? (code === "RUNNING" ? t("shell.state.stopping_work") : label) : null);
  return { ...worker, down, stopped, label, running };
}

export function useCompanyState(): CompanyState {
  return useFrame().company;
}
