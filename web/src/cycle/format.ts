import { useEffect, useState } from "react";
import { lookup, t } from "../i18n";
import { minutesText } from "../today";
import { usd } from "../components/ui";
import { BoardState, CycleState, StageEstimate, StageStatus } from "./types";
import type { Tone } from "../components/kit";

/** The wall clock, ticking once a second: elapsed times on cards are counted here from the server's start time. */
export function useNow(): number {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return now;
}

export function elapsedShort(since: string | null, now: number): string {
  if (!since) return "";
  const s = Math.max(0, Math.floor((now - Date.parse(since)) / 1000));
  if (s < 60) return t("cycle.dur.sec", { s });
  const m = Math.floor(s / 60);
  return m < 60 ? t("cycle.dur.min", { m }) : t("cycle.dur.hour", { h: Math.floor(m / 60), m: m % 60 });
}

/** "about 2 h · about $3 of usage", or that there is none: always followed by what kind of number it is. */
export function estimateParts(e: StageEstimate): { text: string; lines: string[]; basis: string } {
  const bits = [
    e.minutes != null ? t("cycle.est.time", { time: minutesText(e.minutes) }) : null,
    e.usd != null ? t("cycle.est.usd", { usd: usd(e.usd) }) : null,
  ].filter(Boolean);
  return {
    text: bits.length ? bits.join(" · ") : t("cycle.est.none"),
    lines: bits.length ? (bits as string[]) : [t("cycle.est.none")],
    basis: bits.length ? t(e.basis === "history" ? "cycle.est.history" : "cycle.est.guess") : "",
  };
}

export const STAGE_TONE: Record<StageStatus, Tone> = {
  planned: "muted", running: "run", closing: "run", awaiting_owner: "warn", done: "ok", skipped: "muted", stopped: "warn",
};

const CYCLE_TONE: Record<CycleState, Tone> = { draft: "warn", running: "run", retro: "run", closed: "ok", stopped: "muted" };

/**
 * A stage's or a cycle's status word. Work the company would be doing now says the worker's word instead while no
 * worker runs or the owner stopped the company (`held`, useWorker().label), as the projects and tasks of it do:
 * "Running" would claim work that is not moving.
 */
export function stageWord(status: StageStatus, held: string | null): { word: string; tone: Tone } {
  if (held && (status === "running" || status === "closing")) return { word: held, tone: "warn" };
  return { word: t(`cycle.status.${status}`), tone: STAGE_TONE[status] };
}
export function cycleWord(state: CycleState, held: string | null): { word: string; tone: Tone } {
  if (held && (state === "running" || state === "retro")) return { word: held, tone: "warn" };
  return { word: t(`cycle.state.${state}`), tone: CYCLE_TONE[state] };
}

/** The status line's dot: waiting on the owner is amber, the company at work is blue, nothing open is grey. */
export const BOARD_TONE: Record<BoardState, Tone> = {
  none: "muted", proposing: "run", proposal: "warn", legacy: "warn", revising: "run", proposal_failed: "bad",
  running: "run", closing: "run", gate: "warn", stage_stopped: "warn", retro: "run", stopped: "muted",
};

export const NEEDS_OWNER: BoardState[] = ["proposal", "legacy", "proposal_failed", "gate", "stage_stopped"];

/** A job kind or kind of to-do a stage lists, in the owner's words; a code the catalog does not know shows as it is. */
export const routineWord = (kind: string) => lookup(`label.routine.${kind}`) ?? kind;
export const todoWord = (kind: string) => lookup(`label.todoKind.${kind}`) ?? kind;
