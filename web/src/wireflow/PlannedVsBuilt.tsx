import { useEffect, useId, useRef, useState } from "react";
import { PlainKey, t } from "../i18n";
import { Badge } from "../components/ui";
import { named, Sketch } from "./Wireflow";
import type { PlanScreen } from "./types";
import "./built.css";

export interface Shot { name: string; url: string }
export type Verdict = "yes" | "partly" | "no" | "unseen";
export interface BuiltScreen extends Pick<PlanScreen, "name" | "state" | "shows" | "blocks"> {
  built: { shot: Shot | null; verdict: Verdict | null; differs: string };
}
/** A merge card's screens (`mode: "built"`, engineering/wireflow.py `built`): each planned screen with the capture of it
 * and the product agent's word on it, in the order a user meets them, and the captures no planned screen claims. */
export interface Built {
  mode: "built";
  screens: Record<string, BuiltScreen>;
  order: string[];
  extra: Shot[];
}

const WORD: Record<Verdict | "none", { key: PlainKey; tone: string }> = {
  yes: { key: "decide.wfb.yes", tone: "ok" },
  partly: { key: "decide.wfb.partly", tone: "warn" },
  no: { key: "decide.wfb.no", tone: "bad" },
  unseen: { key: "decide.wfb.unseen", tone: "muted" },
  none: { key: "decide.wfb.none_said", tone: "muted" },
};

/** Which half of a pair is open large. */
type Side = "plan" | "shot";
const slotOf = (id: string, side: Side) => `${id}.${side}`;

function Verdict({ screen }: { screen: BuiltScreen }) {
  const { shot, verdict, differs } = screen.built;
  if (!shot) return null;
  const word = WORD[verdict ?? "none"];
  return (
    <p className="wfb-verdict">
      <Badge value={t(word.key)} tone={word.tone} raw />
      {differs && <span>{differs}</span>}
    </p>
  );
}

/** The capture, or the line that says there is none. An image that does not load says so too. */
function Capture({ shot, big }: { shot: Shot | null; big?: boolean }) {
  const [broken, setBroken] = useState(false);
  if (!shot) return <span className="wfb-none">{t("decide.wfb.no_shot")}</span>;
  if (broken) return <span className="wfb-none">{t("decide.wfb.load_failed")}</span>;
  return <img className={big ? "wfb-img wfb-img-big" : "wfb-img"} src={shot.url} alt="" loading="lazy" onError={() => setBroken(true)} />;
}

/** One half of a pair opened large in place, under the pair: the sketch with every block, or the capture at the panel's
 * width with a way to open it at its own size. Esc or Close shuts it. */
function Big({ screen, side, onClose }: { screen: BuiltScreen; side: Side; onClose: () => void }) {
  const shot = screen.built.shot;
  const box = useRef<HTMLElement>(null);
  useEffect(() => box.current?.querySelector("button")?.focus(), []);
  return (
    <figure className="wf-big wfb-big" ref={box} onKeyDown={(e) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      onClose();
    }}>
      <figcaption className="wf-big-h">
        <span className="wf-big-t">
          {named(screen.name, screen.state)}
          <span className="muted"> · {t(side === "plan" ? "decide.wfb.planned" : "decide.wfb.built")}</span>
        </span>
        <span className="wfb-acts">
          {side === "shot" && shot && <a className="small" href={shot.url} target="_blank" rel="noreferrer">{t("decide.wfb.full")}</a>}
          <button type="button" className="btn-ghost small" onClick={onClose}>{t("decide.wf.close")}</button>
        </span>
      </figcaption>
      {side === "plan" ? <Sketch screen={screen} /> : <Capture shot={shot} big />}
    </figure>
  );
}

function Pair({ id, screen, open, setOpen, triggers }: {
  id: string; screen: BuiltScreen; open: string | null; setOpen: (slot: string | null) => void;
  triggers: Map<string, HTMLButtonElement>;
}) {
  const here = open === slotOf(id, "plan") ? "plan" : open === slotOf(id, "shot") ? "shot" : null;
  const close = () => {
    const slot = open;
    setOpen(null);
    if (slot) triggers.get(slot)?.focus();
  };
  const trigger = (side: Side) => (el: HTMLButtonElement | null) => {
    if (el) triggers.set(slotOf(id, side), el); else triggers.delete(slotOf(id, side));
  };
  const toggle = (side: Side) => setOpen(open === slotOf(id, side) ? null : slotOf(id, side));
  const label = named(screen.name, screen.state);
  const shot = screen.built.shot;
  return (
    <li className="wfb-pair">
      <p className="wfb-name">{label}</p>
      <div className="wfb-cmp">
        <div className="wfb-half">
          <p className="wfb-lbl">{t("decide.wfb.planned")}</p>
          <button type="button" className="wf-f wfb-f" ref={trigger("plan")} aria-expanded={here === "plan"}
            aria-label={t("decide.wfb.plan_alt", { name: label })} onClick={() => toggle("plan")}>
            <Sketch screen={screen} />
          </button>
        </div>
        <div className="wfb-half">
          <p className="wfb-lbl">{t("decide.wfb.built")}</p>
          {shot ? (
            <button type="button" className="wfb-s" ref={trigger("shot")} aria-expanded={here === "shot"}
              aria-label={t("decide.wfb.shot_alt", { name: label })} onClick={() => toggle("shot")}>
              <Capture shot={shot} />
            </button>
          ) : <Capture shot={null} />}
        </div>
      </div>
      <Verdict screen={screen} />
      {here && <Big key={here} screen={screen} side={here} onClose={close} />}
    </li>
  );
}

/** The planned screens beside what was built, a pair for each in the order a user meets them and one line on it. A
 * planned screen with no capture says so; the captures nobody planned stay in the list below (TryIt). */
export function PlannedVsBuilt({ built }: { built: Built }) {
  const [open, setOpen] = useState<string | null>(null);
  const triggers = useRef(new Map<string, HTMLButtonElement>()).current;
  const titleId = useId();
  const ids = built.order.filter((id) => built.screens[id]);
  if (!ids.length) return null;
  const any = ids.some((id) => built.screens[id].built.shot);
  return (
    <section className="dp-sec wfb" aria-labelledby={titleId}>
      <h3 id={titleId}>{t("decide.wfb.title")}</h3>
      {any ? (
        <>
          <ol className="wfb-list">
            {ids.map((id) => <Pair key={id} id={id} screen={built.screens[id]} open={open} setOpen={setOpen} triggers={triggers} />)}
          </ol>
          <p className="muted small">{t("decide.wfb.note")}</p>
        </>
      ) : <p className="dp-notice">{t("decide.wfb.none")}</p>}
    </section>
  );
}
