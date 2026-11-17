import { ReactNode, SyntheticEvent, useId, useState } from "react";
import { PlainKey, t } from "../i18n";

/** What the owner is good at (backend `services/strengths.py`). `null` is not answered yet; `[]` is "none of these
 * yet", the owner's answer that every area is new to them. */
export type Strength = "software" | "marketing" | "design" | "operations";
export type Strengths = Strength[] | null;
export const STRENGTHS: readonly Strength[] = ["software", "marketing", "design", "operations"];

const LABEL: Record<Strength, PlainKey> = {
  software: "settings.about.area.software", marketing: "settings.about.area.marketing",
  design: "settings.about.area.design", operations: "settings.about.area.operations",
};
const HINT: Record<Strength, PlainKey> = {
  software: "settings.about.area.software.hint", marketing: "settings.about.area.marketing.hint",
  design: "settings.about.area.design.hint", operations: "settings.about.area.operations.hint",
};

export const strengthName = (a: Strength) => t(LABEL[a]);

/** Which of a screen's areas the owner is strong in and new to (backend `services/strengths.fit`). A strong area's
 * detail is open; one they are new to leads with the conclusion and folds the detail. Neither is the screen as it was.
 * With no area named, any strong or new area counts. */
export interface Fit { strong?: Strength[]; weak?: Strength[] }
export const deep = (fit: Fit | null | undefined, a?: Strength) => (a ? !!fit?.strong?.includes(a) : !!fit?.strong?.length);
export const plain = (fit: Fit | null | undefined, a?: Strength) => (a ? !!fit?.weak?.includes(a) : !!fit?.weak?.length);

/** A section's detail by the owner's fit: open in an area they are strong in; in one they are new to, folded behind
 * its heading and `lead`, its one-line conclusion; otherwise folded under its heading as it always was. */
export function AreaFold({ fit, head, lead, className, children, memo }: {
  fit: Fit | null | undefined; head: ReactNode; lead?: string; className?: string; children: ReactNode; memo?: string;
}) {
  // `memo` keeps the owner's own open or close for this tab, so coming back from a footnote finds the section as left
  const [open, setOpen] = useState(() => {
    try {
      const kept = memo ? sessionStorage.getItem(`ship.fold.${memo}`) : null;
      return kept ? kept === "open" : deep(fit);
    } catch {
      return deep(fit);
    }
  });
  const onToggle = (e: SyntheticEvent<HTMLDetailsElement>) => {
    const now = e.currentTarget.open;
    setOpen(now);
    if (!memo) return;
    try {
      sessionStorage.setItem(`ship.fold.${memo}`, now ? "open" : "closed");
    } catch {
      /* private mode */
    }
  };
  return (
    <details className={className} open={open} onToggle={onToggle}>
      <summary>{head}{!open && plain(fit) && lead && <span className="fold-lead">{lead}</span>}</summary>
      {children}
    </details>
  );
}

/** The owner's strengths as one phrase, in the table's order. */
export function strengthList(value: Strength[]): string {
  return STRENGTHS.filter((a) => value.includes(a)).map(strengthName).join(", ");
}

/** Picking an area clears "none yet"; picking "none yet" clears the areas; clearing the last pick is not answered. */
function toggle(value: Strengths, a: Strength): Strengths {
  const now = value ?? [];
  const next = now.includes(a) ? now.filter((x) => x !== a) : STRENGTHS.filter((x) => x === a || now.includes(x));
  return next.length ? next : null;
}

/** One row per area and "none of these yet". The legend names the question; a screen whose heading already asks it
 * keeps the legend for screen readers only. */
export function StrengthsPicker({ value, onChange, disabled, legend, showLegend = false, note, compact = false }: {
  value: Strengths; onChange: (next: Strengths) => void; disabled?: boolean; legend: ReactNode; showLegend?: boolean;
  note?: string;
  /** One row of toggle chips, each area's hint in its tooltip: for the kickoff chat's answer area, where the CEO's
   * question above already says what the answer changes. */
  compact?: boolean;
}) {
  const uid = useId();
  const none = value !== null && value.length === 0;
  if (compact) {
    const chip = (key: string, on: boolean, label: string, hint: string, flip: () => void) => (
      <label key={key} className={`strength-chip ${on ? "on" : ""}`} title={hint}>
        <input type="checkbox" checked={on} disabled={disabled} onChange={flip} />
        {label}
      </label>
    );
    return (
      <fieldset className="strengths-compact" disabled={disabled}>
        <legend className={showLegend ? undefined : "sr-only"}>{legend}</legend>
        <div className="strength-chips">
          {STRENGTHS.map((a) => chip(a, !!value?.includes(a), t(LABEL[a]), t(HINT[a]), () => onChange(toggle(value, a))))}
          {chip("none", none, t("settings.about.area.none"), t("settings.about.area.none.hint"), () => onChange(none ? null : []))}
        </div>
        {note && <p className="small muted strengths-note">{note}</p>}
      </fieldset>
    );
  }
  const row = (key: string, on: boolean, label: string, hint: string, flip: () => void) => (
    <label key={key} className={`choice strength ${on ? "on" : ""}`}>
      <input type="checkbox" checked={on} disabled={disabled} onChange={flip} aria-describedby={`${uid}-${key}`} />
      <span className="choice-t">
        <span className="strength-l">{label}</span>
        <span id={`${uid}-${key}`} className="strength-h">{hint}</span>
      </span>
      <span />
    </label>
  );
  return (
    <fieldset className="choices strengths" disabled={disabled}>
      <legend className={showLegend ? undefined : "sr-only"}>{legend}</legend>
      {note && <p className="small muted strengths-note">{note}</p>}
      {STRENGTHS.map((a) => row(a, !!value?.includes(a), t(LABEL[a]), t(HINT[a]), () => onChange(toggle(value, a))))}
      {row("none", none, t("settings.about.area.none"), t("settings.about.area.none.hint"), () => onChange(none ? null : []))}
    </fieldset>
  );
}
