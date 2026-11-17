import { useId } from "react";
import { t } from "../i18n";
import type { HeardChoice } from "./types";

/** The longest answer the server keeps (`tracking.MAX_WORDS`). */
export const HEARD_WORDS_MAX = 120;

/** What the owner has picked and typed. `via` is "" while the pick is left on the row the list derived for the person. */
export type Heard = { via: string; words: string };

/** What the person holds now, as the people view and the person to-do carry it. */
export type HeardNow = { heard_via?: string | null; heard_words?: string | null; heard_row_label?: string };

/** The field's starting value: the stored answer, or nothing picked. */
export const heardStart = (p: HeardNow | null | undefined): Heard => ({ via: p?.heard_via ?? "", words: p?.heard_words ?? "" });

/** Whether the owner changed anything. Leaving the pick on the derived row with no words stores nothing. */
export const heardChanged = (now: Heard, p: HeardNow | null | undefined): boolean => {
  const start = heardStart(p);
  return now.via !== start.via || now.words.trim() !== start.words.trim();
};

/** The body of the save: the pick and the words. A pick left on the derived row is sent as no pick, so the server reads the words. */
export const heardBody = (now: Heard) => ({ heard_via: now.via || undefined, heard_words: now.words.trim() || undefined });

/**
 * "How did they hear about you?": a select of the business's channels, then Someone told them, Search and Other, with a
 * box for their words. While nothing is recorded, the select shows the row the list counts them in and leaving it
 * stores nothing. A refusal from the server (a contact detail in the words) is passed in as `error` and shown in place.
 */
export function HeardField({ value, onChange, choices, now, error, disabled }: {
  value: Heard; onChange: (v: Heard) => void; choices: HeardChoice[] | null; now: HeardNow | null | undefined;
  error?: string | null; disabled?: boolean;
}) {
  const uid = useId();
  const stored = now?.heard_via ?? "";
  // an answer that is not among this business's channels (the market changed) keeps its own option, so it is never silently replaced
  const held = stored && !choices?.some((c) => c.key === stored) ? stored : null;
  return (
    <div className="tk-heard">
      <div className="field">
        <label htmlFor={`${uid}-via`}>{t("business-tracking.heard.label")}</label>
        <select id={`${uid}-via`} value={value.via} disabled={disabled || !choices} aria-describedby={stored ? undefined : `${uid}-keep`}
          onChange={(e) => onChange({ ...value, via: e.target.value })}>
          {!stored && <option value="">{now?.heard_row_label ?? t("business-tracking.heard.loading")}</option>}
          {held && <option value={held}>{now?.heard_row_label ?? held}</option>}
          {(choices ?? []).map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
        </select>
        {!stored && <p id={`${uid}-keep`} className="field-note muted small">{t("business-tracking.heard.keep")}</p>}
      </div>
      <div className="field">
        <label htmlFor={`${uid}-words`}>{t("business-tracking.heard.words")}</label>
        <input id={`${uid}-words`} type="text" value={value.words} maxLength={HEARD_WORDS_MAX} autoComplete="off" disabled={disabled}
          aria-invalid={error ? true : undefined} aria-describedby={`${uid}-help${error ? ` ${uid}-err` : ""}`}
          onChange={(e) => onChange({ ...value, words: e.target.value })} />
        <p id={`${uid}-help`} className="field-note muted small">{t("business-tracking.heard.words_help", { n: HEARD_WORDS_MAX })}</p>
        {error && <p id={`${uid}-err`} className="field-note bad todo-field-bad small" role="alert">{error}</p>}
      </div>
    </div>
  );
}
