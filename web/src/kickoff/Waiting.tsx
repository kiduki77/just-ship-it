import { ReactNode, useEffect, useRef, useState } from "react";
import { Fix } from "../components/fix";
import { useWorker } from "../components/frame";
import { t } from "../i18n";

/** Seconds since `since`, counted every second while shown. */
export function useElapsed(since: number): number {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return Math.max(0, Math.floor((now - since) / 1000));
}

export function elapsedText(s: number): string {
  const m = Math.floor(s / 60);
  return m ? t("kickoff.wait.min", { m, s: s % 60 }) : t("kickoff.wait.sec", { s });
}

/**
 * Work the owner waits on: what is happening, how long it usually takes, and a clock that keeps counting, so a quiet
 * screen never reads as a stuck one. Past `slowAfter` seconds it says so; with no worker running it says nothing will
 * move until one starts, the one case where the wait really is stuck, shows no clock, and `downTitle` says what waits
 * for it instead of `title` claiming someone is at work.
 */
export function Waiting({ title, downTitle, expect, since, slowAfter, held, slow }: {
  title: ReactNode; downTitle: ReactNode; expect: string; since: string | null; slowAfter: number; held?: string | null; slow?: string;
}) {
  const seen = useRef(Date.now());
  const from = since ? Date.parse(since) : NaN;
  const s = useElapsed(Number.isNaN(from) ? seen.current : Math.min(from, seen.current));
  const { down } = useWorker();
  return (
    <div className="tail wait">
      <span className={`think ${down ? "stopped" : ""}`} aria-hidden="true" />
      <div className="wait-b">
        <p role="status"><span className="strong">{down ? downTitle : title}</span><span className="muted">{down ? "" : s > slowAfter ? t("kickoff.wait.over") : expect}</span></p>
        {/* the clock sits outside the status region, so a screen reader is not read a new second every second; with no
            worker nothing is being worked on, so no clock counts as if it were */}
        {!down && <p className="small muted num">{t("kickoff.wait.elapsed", { time: elapsedText(s) })}</p>}
        {down ? (
          <p className="small bad-t" role="alert"><Fix text={t("kickoff.wait.no_worker")} /></p>
        ) : held ? (
          <p className="small bad-t" role="alert">{held}</p>
        ) : s > slowAfter && (
          <p className="small muted">{slow ?? t("kickoff.wait.slow")}</p>
        )}
      </div>
    </div>
  );
}
