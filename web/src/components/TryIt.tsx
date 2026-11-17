import { useId, useState } from "react";
import { Row } from "../api";
import { t } from "../i18n";
import { PlannedVsBuilt } from "../wireflow/PlannedVsBuilt";
import { UseIt } from "./MergeReview";
import { CopyButton, safeUrl } from "./panel";
import type { Review } from "./reviewModel";
import { deep, plain } from "./strengths";
import "./decision.css";

interface Shot { name: string; url: string }
interface Code { branch: string; pr_url: string | null; changes: string | null }
export interface TryCard { screens: Shot[]; steps: string[]; address: string | null; sample: string | null; unseen?: string[]; code?: Code | null }

/** The branch, its pull request and the files it changes: open for an owner who builds software, a fold for others. */
function CodeFacts({ code, open }: { code: Code; open: boolean }) {
  const pr = safeUrl(code.pr_url);
  return (
    <details className="dp-fold dp-fold-in dp-code" open={open}>
      <summary>{t("decide.code.title")}</summary>
      <dl className="kv">
        <div><dt>{t("decide.code.branch")}</dt><dd className="mono">{code.branch}</dd></div>
        {pr && <div><dt>{t("decide.code.pr")}</dt><dd><a href={pr} target="_blank" rel="noreferrer">{pr}</a></dd></div>}
      </dl>
      {code.changes && <><p className="dp-sub">{t("decide.code.changes")}</p><pre className="dp-draft dp-changes">{code.changes}</pre></>}
    </details>
  );
}
const SHOTS_SHOWN = 4;

/** The build behind a merge card that recorded no walkthrough, before the owner decides it: the screens the CPO
 * checked, and the commands that open the branch on the owner's computer (engineering/try_it.py). With neither, the
 * build's own steps to use it (engineering/merge_review.py), else it says nobody can click it yet. The code is the
 * review's, below it. `note` is a line under the heading, for a card whose commands nobody ran. */
export function TryIt({ r, note }: { r: Row; note?: string }) {
  const uid = useId();
  const [all, setAll] = useState(deep(r.fit, "design"));
  const x: TryCard | undefined = r.try_it;
  if (r.action_kind !== "dev.merge" || !x) return null;
  const review: Review | undefined = r.review;
  const use = !x.screens.length && !x.steps.length ? review?.use ?? null : null;
  // The pairs above hold every capture a planned screen claims; the list keeps the rest.
  const built = r.screens?.mode === "built" ? r.screens : null;
  const list = built ? x.screens.filter((sh) => built.extra.some((e: Shot) => e.name === sh.name)) : x.screens;
  const shots = all ? list : list.slice(0, SHOTS_SHOWN);
  return (
    <>
    {built && <PlannedVsBuilt built={built} />}
    <section className="dp-sec dp-try" aria-labelledby={`${uid}-h`}>
      <h3 id={`${uid}-h`}>{t(use ? "decide.mr.try" : r.status === "PENDING" ? "decide.try.title" : "decide.try.title_done")}</h3>
      {note && <p className="muted small">{note}</p>}
      {use && <UseIt use={use} />}
      {!x.screens.length && !x.steps.length && !use && <p className="dp-notice">{t("decide.try.none")}</p>}
      {x.screens.length > 0 && x.unseen?.length ? <p className="dp-notice">{t("decide.try.unseen", { what: x.unseen.join("; ") })}</p> : null}
      {x.screens.length > 0 && x.sample && <p className="muted small">{t("decide.try.sample", { why: x.sample })}</p>}
      {list.length > 0 && (
        <>
          <p className="muted small">{built ? t("decide.wfb.extra", { n: list.length }) : t("decide.try.screens", { n: x.screens.length })}</p>
          <ul className="dp-shots">
            {shots.map((sh) => (
              <li key={sh.url}>
                <a href={sh.url} target="_blank" rel="noreferrer">
                  <img src={sh.url} alt={sh.name} loading="lazy" />
                  <span className="mono small">{sh.name}</span>
                </a>
              </li>
            ))}
          </ul>
          {!all && list.length > SHOTS_SHOWN && (
            <button type="button" className="link small" onClick={() => setAll(true)}>
              {t("decide.try.screens_more", { n: list.length - SHOTS_SHOWN })}
            </button>
          )}
        </>
      )}
      {x.steps.length > 0 && (plain(r.fit, "software")
        ? <details className="dp-fold dp-fold-in"><summary>{t("decide.try.run")}</summary><RunSteps x={x} /></details>
        : <RunSteps x={x} />)}
      {x.code && !review && <CodeFacts code={x.code} open={deep(r.fit, "software")} />}
    </section>
    </>
  );
}

function RunSteps({ x }: { x: TryCard }) {
  return (
    <>
      <div className="dp-draft-h">
        <p className="dp-sub">{t("decide.try.run")}</p>
        <CopyButton text={x.steps.join("\n")} what={t("decide.try.run")} />
      </div>
      <ol className="dp-steps">{x.steps.map((c) => <li key={c}><code>{c}</code></li>)}</ol>
      {x.address && <p className="small">{t("decide.try.open", { address: x.address })}</p>}
      <p className="muted small">{t("decide.try.run_note")}</p>
    </>
  );
}
