import { FormEvent, ReactNode, useEffect, useRef } from "react";
import { ActionButton, ConfirmBox, settleFocus, usd } from "../components/ui";
import { t } from "../i18n";
import { SectionIdProvider, useDirty, useSectionId } from "./dirty";
import { Guard, Saver } from "./useSave";

/**
 * One settings section: a bordered box with its heading and one lead paragraph, reachable from the index by `id`.
 * On a wide screen the heading and lead sit beside the controls, so the form keeps its width and the screen is used.
 * Whether it holds unsaved edits is the form's to say (`useDirtyWhen`), from its inputs against the saved values.
 */
export function Section({ id, title, lead, children }: { id: string; title: string; lead: ReactNode; children: ReactNode }) {
  return (
    <section className="set-sec box" id={id} aria-labelledby={`${id}-h`}>
      <header className="set-sec-h">
        <h2 id={`${id}-h`} className="set-h" tabIndex={-1}>{title}</h2>
        <p className="set-lead">{lead}</p>
      </header>
      <div className="set-sec-b"><SectionIdProvider value={{ id, title }}>{children}</SectionIdProvider></div>
    </section>
  );
}

/**
 * Save, what saving will change, the fields still to fix, and, when the owner has changed this section before, a way
 * back to the file's values. A save that opens something up waits here on a confirm box (`guard`).
 */
export function SaveBar({ saver, guard, disabled, changed, summary, invalid = 0, resetSays, confirmLabel }: {
  saver: Saver; guard?: Guard; disabled?: boolean; changed: boolean; summary?: ReactNode; invalid?: number;
  /** The confirm button's name when the save waits on the owner: what saving does. */
  confirmLabel?: string;
  /** What going back to the defaults puts back, said with the confirm. */
  resetSays?: ReactNode;
}) {
  const id = useSectionId();
  const { sections } = useDirty();
  const err = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!saver.error || !err.current) return;
    err.current.scrollIntoView({ block: "nearest" });
    err.current.focus();
  }, [saver.error]);
  // a save can leave nothing to save, so Save turns off and drops the focus it had: focus stays on Save while Save can
  // take it, else it moves to the "Saved" line
  const said = useRef<HTMLSpanElement>(null);
  const submit = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!saver.saved) return;
    settleFocus(() => (submit.current && !submit.current.disabled ? submit.current : said.current), said.current);
  }, [saver.saved]);
  // the confirm takes Save's place, so Save is gone before the confirm can note it as the opener: Keep as is or Escape
  // gives focus back to Save here
  const pending = !!guard?.pending;
  const wasPending = useRef(false);
  useEffect(() => {
    if (wasPending.current && !pending) submit.current?.focus();
    wasPending.current = pending;
  }, [pending]);
  const others = [...sections].filter(([k]) => k !== id).map(([, title]) => title);
  if (guard?.pending) {
    return (
      <div className="set-save">
        <ConfirmBox tone="danger" confirmLabel={confirmLabel ?? t("settings.confirm.go")} cancelLabel={t("settings.confirm.keep")} busy={saver.busy} error={saver.error}
          onConfirm={guard.confirm} onCancel={guard.cancel}>
          <p><b>{t("settings.confirm.title")}</b></p>
          <ul className="set-confirm-list">{guard.pending.lines.map((l, i) => <li key={i}>{l}</li>)}</ul>
        </ConfirmBox>
      </div>
    );
  }
  return (
    <div className="set-save">
      {summary && <p className="small set-summary">{summary}</p>}
      <div className="row wrap">
        <button ref={submit} type="submit" className="btn-primary" disabled={saver.busy || disabled || invalid > 0}>
          {saver.busy ? t("settings.saving") : t("settings.save")}
        </button>
        {changed && (
          <ActionButton className="quiet" confirm={t("settings.reset.confirm")} what={resetSays ?? t("settings.reset.says")} run={() => saver.save({})}>
            {t("settings.reset")}
          </ActionButton>
        )}
        {invalid > 0 && <span className="small bad-t" role="status">{t("settings.invalid", { n: invalid })}</span>}
        <span ref={said} tabIndex={-1} className="small muted" role="status">{saver.saved ? t("settings.saved") : ""}</span>
      </div>
      {others.length > 0 && (
        <p className="small set-unsaved" role="status">{t("settings.others_unsaved", { sections: others.join(", ") })}</p>
      )}
      {saver.error && <div ref={err} tabIndex={-1} className="error" role="alert">{saver.error}</div>}
    </div>
  );
}

/** The fields of one section, as a form: Enter in a field saves, like the Save button. */
export function Fields({ saver, label, children, onSubmit }: { saver: Saver; label: string; children: ReactNode; onSubmit: () => void }) {
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!saver.busy) onSubmit();
  };
  return <form className="set-form" aria-label={label} aria-busy={saver.busy} onSubmit={submit} noValidate>{children}</form>;
}

/** The sentence under a field that is wrong, tied to it by `aria-describedby`. */
export function FieldError({ id, text }: { id: string; text: string | null }) {
  return text ? <p id={id} className="small bad-t set-field-err" role="alert">{text}</p> : null;
}

/** Money reads the same everywhere in the console: dollars with cents, as `usd` writes it. */
export const usdText = usd;

export const pctText = (n: number) => `${Number.isInteger(n) ? n : n.toFixed(1)}%`;
