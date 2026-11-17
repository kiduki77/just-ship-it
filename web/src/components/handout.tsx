import { useId } from "react";
import { Row } from "../api";
import { t } from "../i18n";
import { CopyButton } from "./panel";

/** The message the owner sends, or the steps the owner follows, as the company wrote them: whole, with a copy button,
 * on the decision card and on the to-do it makes. `folded` when the to-do's steps already list it line by line. */
export function HandoutSection({ handout, folded = false }: { handout: Row | null | undefined; folded?: boolean }) {
  const uid = useId();
  if (!handout?.body) return null;
  const message = handout.kind === "message";
  const head = t(message ? "work-todo.handout.message" : "work-todo.handout.steps");
  if (folded) {
    return (
      <details className="dp-sec dp-fold">
        <summary>{t("work-todo.handout.whole")}</summary>
        <div className="dp-draft-h"><span /><CopyButton text={handout.body} what={head} /></div>
        <pre className="dp-draft">{handout.body}</pre>
      </details>
    );
  }
  return (
    <section className="dp-sec" aria-labelledby={`${uid}-h`}>
      <div className="dp-draft-h">
        <h3 id={`${uid}-h`}>{head}</h3>
        <CopyButton text={handout.body} what={head} />
      </div>
      {message && handout.to && <p className="muted small">{t("work-todo.handout.to", { to: handout.to })}</p>}
      <pre className="dp-draft">{handout.body}</pre>
    </section>
  );
}
