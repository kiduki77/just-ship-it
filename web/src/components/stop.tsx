import { useEffect, useRef, useState } from "react";
import { post, refreshLive } from "../api";
import { useBusinessSelection } from "../business";
import { t, tx } from "../i18n";
import { agentName } from "../labels";
import { useFrame } from "./frame";
import { Icon } from "./icons";
import { Banner } from "./kit";
import { ActionButton, announce, when } from "./ui";

/** A stop in force, as the server keeps it (services/stops.py). */
export type Stop = { at: string; by: string; reason: string | null };

/** Every view a stop or a resume changes: it writes one activity row, and work moves on the queue. */
const MOVED = ["activity_events", "jobs", "tasks", "dev_jobs", "cycle_stages"];

async function send(path: string): Promise<void> {
  await post(path, {});
  refreshLive(MOVED);
}

/** The owner's stop of the whole company, from `/api/company`: true whatever business is on screen, or none. */
export function useCompanyStop(): Stop | null {
  return useFrame().stop;
}

/**
 * The emergency stop, always in the top bar: quiet while the company runs, a plain amber "Resume" once stopped.
 * The first click opens a confirm in place that says what stopping does and names every business; the safe button
 * takes the first focus, so the key that opened the box cannot also confirm it.
 */
export function StopAllControl() {
  const stopped = useCompanyStop();
  const { stopKnown, reloadStop } = useFrame();
  const { businesses } = useBusinessSelection();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const safe = useRef<HTMLButtonElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const resumeBtn = useRef<HTMLButtonElement>(null);
  const focusNext = useRef<"stop" | "resume" | null>(null);

  useEffect(() => {
    if (!open) return;
    safe.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    const onDown = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && close();
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, [open]);
  // after a stop or a resume the control changes shape; focus follows it instead of falling to the body
  useEffect(() => {
    if (focusNext.current === "resume" && stopped) resumeBtn.current?.focus();
    if (focusNext.current === "stop" && !stopped) opener.current?.focus();
    if ((focusNext.current === "resume") === !!stopped) focusNext.current = null;
  }, [stopped]);

  function close() {
    setOpen(false);
    setError(null);
    opener.current?.focus();
  }

  async function run(path: string, next: "stop" | "resume", said: string) {
    setBusy(true);
    setError(null);
    try {
      await send(path);
      focusNext.current = next;
      reloadStop();
      announce(said, "ok");
      setOpen(false);
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  if (stopped) {
    return (
      <div className="stopall">
        <button ref={resumeBtn} type="button" className="btn-ghost stopall-resume t-warn" disabled={busy}
          onClick={() => run("/company/resume", "stop", t("shell.stop.resumed"))} title={t("shell.stop.stopped_since", { since: when(stopped.at) })}>
          <Icon name="play" />
          <span className="stopall-l">{busy ? t("common.working") : t("shell.stop.resume_all")}</span>
        </button>
        {error && <span className="act-msg bad" role="alert">{error}</span>}
      </div>
    );
  }
  return (
    <div className="stopall" ref={box}>
      <button ref={opener} type="button" className="icon-btn stopall-btn" aria-expanded={open} aria-haspopup="dialog" disabled={!stopKnown}
        aria-label={t("shell.stop.all")} title={t("shell.stop.all")} onClick={() => (open ? close() : setOpen(true))}>
        <Icon name="stop" />
      </button>
      {open && (
        <div className="stopall-pop" role="dialog" aria-modal="false" aria-labelledby="stopall-q">
          <p id="stopall-q" className="stopall-q">{t("shell.stop.confirm_q")}</p>
          <p className="small muted">{t("shell.stop.confirm_what", { n: businesses?.length ?? 0 })}</p>
          <div className="row tight wrap">
            <button ref={safe} type="button" className="btn-ghost" disabled={busy} onClick={close}>{t("shell.stop.confirm_no")}</button>
            <button type="button" className="danger-solid" disabled={busy} onClick={() => run("/company/stop", "resume", t("shell.stop.stopped_now"))}>
              {busy ? t("common.working") : t("shell.stop.confirm_yes")}
            </button>
          </div>
          {error && <p className="act-msg bad" role="alert">{error}</p>}
        </div>
      )}
    </div>
  );
}

/** On every page while the company is stopped: since when, what that means, and the way back. */
export function CompanyStoppedBanner() {
  const stopped = useCompanyStop();
  const { reloadStop } = useFrame();
  if (!stopped) return null;
  return (
    <Banner tone="warn" icon="pause"
      action={<ActionButton className="btn-ghost" done={t("shell.stop.resumed")} run={() => send("/company/resume").then(reloadStop)}><Icon name="play" />{t("shell.stop.resume_all")}</ActionButton>}>
      {tx("shell.stop.banner", { since: <b>{when(stopped.at)}</b> })}
      {stopped.reason && <span className="muted"> {t("shell.stop.reason", { reason: stopped.reason })}</span>}
    </Banner>
  );
}

/**
 * Stop one agent, or resume it. Stopping asks once in place, with what it means written beside the button rather
 * than hidden in a tooltip; resuming does not ask. `quiet` is for an agent holding no work: same confirm, no red.
 */
export function AgentStopButton({ agent, stopped, quiet }: { agent: string; stopped: boolean; quiet?: boolean }) {
  return stopped ? (
    <ActionButton className="btn-ghost" done={t("common.activity.agent_resumed", { name: agentName(agent) })} run={() => send(`/agents/${agent}/resume`)}>
      <Icon name="play" />{t("shell.stop.resume")}
    </ActionButton>
  ) : (
    <ActionButton className={quiet ? "btn-ghost" : "danger"} what={t("shell.stop.agent_what")} confirm={t("shell.stop.agent_yes", { name: agentName(agent) })}
      done={t("common.activity.agent_stopped", { name: agentName(agent) })} run={() => send(`/agents/${agent}/stop`)}>
      {t("shell.stop.agent")}
    </ActionButton>
  );
}
