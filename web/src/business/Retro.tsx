import { t } from "../i18n";
import { Code } from "./parts";
import { CONFIDENCE, Retro, VERDICT } from "./types";

const CONF_TONE = { high: "ok", mid: "warn", low: "muted" } as const;

function Verdict({ r }: { r: Retro }) {
  return (
    <div className="retro-line">
      <dt>{t("business-bets.retro.verdict")}</dt>
      <dd><span className={`tag t-${r.verdict === "stop" ? "muted" : "run"}`}>{t(VERDICT[r.verdict])}</span> {r.verdict_reason}</dd>
    </div>
  );
}

function NextBets({ r }: { r: Retro }) {
  if (!r.next_bets.length) return null;
  return (
    <div className="retro-line">
      <dt>{t("business-bets.retro.next_bets")}</dt>
      <dd>
        <ul className="retro-list">
          {r.next_bets.map((b) => (
            <li key={b.title}>
              <strong>{b.title}</strong>
              {!b.in_scope && <> <span className="tag t-warn">{t("business-bets.retro.out_of_scope")}</span></>}
              <span className="muted"> · {b.reason}</span>
            </li>
          ))}
        </ul>
      </dd>
    </div>
  );
}

function Notes({ r }: { r: Retro }) {
  return r.notes.length ? <div className="retro-notes">{r.notes.map((n) => <p key={n}>{n}</p>)}</div> : null;
}

/** The retro as the bet stored it. Under 30 people it is what people said, in three lines. Above that it is causes and the verdict. */
export function RetroView({ r }: { r: Retro }) {
  if (r.mode === "people_quotes") {
    return (
      <div className="retro">
        <dl>
          <div className="retro-line"><dt>{t("business-bets.retro.did")}</dt><dd>{r.did}</dd></div>
          <div className="retro-line">
            <dt>{t("business-bets.retro.users_said")}</dt>
            <dd>
              {r.users_said?.length ? (
                <ul className="retro-list">
                  {r.users_said.map((u) => <li key={`${u.person_ref}${u.quote}`}>“{u.quote}” <Code>{u.person_ref}</Code></li>)}
                </ul>
              ) : <span className="muted">{t("business-bets.retro.no_quotes")}</span>}
            </dd>
          </div>
          <div className="retro-line"><dt>{t("business-bets.retro.next")}</dt><dd>{r.next}</dd></div>
          <Verdict r={r} />
          <NextBets r={r} />
        </dl>
        <Notes r={r} />
      </div>
    );
  }
  return (
    <div className="retro">
      <dl>
        <div className="retro-line"><dt>{t("business-bets.retro.hypothesis")}</dt><dd>{r.hypothesis}</dd></div>
        <div className="retro-line">
          <dt>{t("business-bets.retro.causes")}</dt>
          <dd>
            <ul className="retro-list">
              {r.causes?.map((c) => (
                <li key={c.cause}>
                  {c.cause} <span className={`tag t-${CONF_TONE[c.confidence]}`}>{t(CONFIDENCE[c.confidence])}</span>
                  {c.evidence_ids.map((id) => <span key={id}> <Code>{id}</Code></span>)}
                </li>
              ))}
            </ul>
          </dd>
        </div>
        {!!r.counter_evidence?.length && (
          <div className="retro-line">
            <dt>{t("business-bets.retro.counter")}</dt>
            <dd><ul className="retro-list">{r.counter_evidence.map((c) => <li key={c}>{c}</li>)}</ul></dd>
          </div>
        )}
        <Verdict r={r} />
        <NextBets r={r} />
      </dl>
      <Notes r={r} />
    </div>
  );
}
