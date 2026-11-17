import { FormEvent, Fragment, ReactNode, useEffect, useId, useState } from "react";
import { post, useLive } from "../api";
import { Box, Hint, Section } from "../components/kit";
import { shortDate, Skeleton, num } from "../components/ui";
import { locale, t } from "../i18n";
import { Places, SignupAsk } from "./Places";
import { useDo } from "./parts";
import type { TrackFigure, TrackHost, TrackMilestone, TrackRow, Tracking } from "./types";
import "./tracking.css";

const TABLES = ["metric_points", "people", "owner_tasks", "businesses"];
export const CAME_ANCHOR = "came-from";

/** A figure's number with the word for what it rests on, so no number stands alone: "12 read", "at least 52". */
function Fig({ f }: { f: TrackFigure }) {
  const n = <b className="num">{num(f.n)}</b>;
  const basis = <span className="tk-basis">{f.basis_label}</span>;
  return <span className="tk-fig">{f.basis === "at_least" ? <>{basis} {n}</> : <>{n} {basis}</>}</span>;
}

const Dash = ({ what }: { what: string }) => <span className="tk-none"><span aria-hidden="true">–</span><span className="sr-only">{what}</span></span>;

const joined = (names: string[]) => {
  try {
    return new Intl.ListFormat(locale(), { style: "long", type: "conjunction" }).format(names);
  } catch {
    return names.join(", ");
  }
};

/** First 10 and First 100: who the first users came from, and the day the last of them got value. */
function Milestone({ m }: { m: TrackMilestone }) {
  const mix = m.by_key.map((k) => t("business-tracking.came.mix", { n: k.n, label: k.label })).join(", ");
  return (
    <div className="tk-first">
      <dt>{t("business-tracking.came.first", { n: m.n })}</dt>
      <dd>
        {m.have === 0 ? t("business-tracking.came.first_none") : <>{mix}. </>}
        {m.have > 0 && (m.reached_on ? t("business-tracking.came.first_done", { n: m.n, date: shortDate(m.reached_on) }) : t("business-tracking.came.first_far", { have: m.have, n: m.n }))}
        {m.estimated > 0 && <span className="tk-sub">{t("business-tracking.came.first_est", { n: m.estimated })}</span>}
      </dd>
    </div>
  );
}

/** A box for each tag of one row: what the owner's own analytics shows for it today. */
function TagCounts({ businessId, row, done, cancel }: { businessId: number; row: TrackRow; done: () => void; cancel: () => void }) {
  const uid = useId();
  const start = (v: number | null) => (v === null ? "" : String(v));
  const [vals, setVals] = useState(() => Object.fromEntries(row.tags.map((g) => [g.tag, { visits: start(g.visits), signups: start(g.signups) }])));
  const d = useDo();
  const [saved, setSaved] = useState<string | null>(null);
  const whole = (v: string) => /^\d{1,7}$/.test(v.trim());
  const bad = (v: string) => v.trim() !== "" && !whole(v);
  const changes = row.tags.flatMap((g) => {
    const cur = vals[g.tag];
    const body: { tag: string; visits?: number; signups?: number } = { tag: g.tag };
    if (whole(cur.visits) && cur.visits.trim() !== start(g.visits)) body.visits = Number(cur.visits);
    if (whole(cur.signups) && cur.signups.trim() !== start(g.signups)) body.signups = Number(cur.signups);
    return body.visits === undefined && body.signups === undefined ? [] : [body];
  });
  const anyBad = row.tags.some((g) => bad(vals[g.tag].visits) || bad(vals[g.tag].signups));
  const set = (tag: string, k: "visits" | "signups", v: string) => {
    setVals({ ...vals, [tag]: { ...vals[tag], [k]: v } });
    setSaved(null);
  };
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!changes.length || anyBad || d.busy) return;
    const ok = await d.run(async () => {
      for (const c of changes) await post(`/businesses/${businessId}/tag-counts`, c);
    });
    if (ok) {
      setSaved(t("business-tracking.came.analytics_saved", { tag: changes.map((c) => c.tag).join(", ") }));
      done();
    }
  };
  return (
    <form className="tk-tags" onSubmit={submit} aria-busy={d.busy} aria-label={t("business-tracking.came.analytics_for", { label: row.label })}
      onKeyDown={(e) => e.key === "Escape" && !d.busy && (e.stopPropagation(), cancel())}>
      <p className="tk-help">{t("business-tracking.came.analytics_help")}</p>
      {row.tags.map((g) => (
        <fieldset key={g.tag} className="tk-tag">
          <legend><span className="code">{g.post}</span> · <span className="code">{g.tag}</span></legend>
          {(["visits", "signups"] as const).map((k) => (
            <div className="field" key={k}>
              <label htmlFor={`${uid}-${g.tag}-${k}`}>{t(k === "visits" ? "business-tracking.came.analytics_visits" : "business-tracking.came.analytics_signups", { tag: g.tag })}</label>
              <input id={`${uid}-${g.tag}-${k}`} inputMode="numeric" autoComplete="off" value={vals[g.tag][k]} aria-invalid={bad(vals[g.tag][k]) || undefined}
                onChange={(e) => set(g.tag, k, e.target.value)} />
              {bad(vals[g.tag][k]) && <p className="field-note bad" role="alert">{t("business-tracking.came.analytics_bad")}</p>}
            </div>
          ))}
        </fieldset>
      ))}
      {d.error && <p className="biz-fail" role="alert">{d.error}</p>}
      <div className="row wrap">
        <button className="btn-primary" type="submit" disabled={d.busy || !changes.length || anyBad}>
          {t(d.busy ? "business-tracking.came.analytics_saving" : "business-tracking.came.analytics_save")}
        </button>
        <button type="button" className="btn-ghost" disabled={d.busy} onClick={cancel}>{t("common.cancel")}</button>
        {!changes.length && !d.busy && <span className="muted small">{t("business-tracking.came.analytics_nothing")}</span>}
        {saved && <span className="act-msg ok" role="status">{saved}</span>}
      </div>
    </form>
  );
}

type Opt = { today: number; n: number };

/** The count of people not on the list who said they came from a place: +1 and, for a slip, one back. */
function Tally({ label, basis, today, n, busy, onPlus, onMinus }: {
  label: string; basis: string; today: number; n: number | null; busy?: boolean; onPlus: () => void; onMinus: () => void;
}) {
  return (
    <span className="tk-tally">
      <span className="tk-told">
        {n === null ? <Dash what={t("business-tracking.came.none")} /> : <span className="tk-fig"><b className="num">{num(n)}</b> <span className="tk-basis">{basis}</span></span>}
      </span>
      <span className="tk-tap">
        <button type="button" className="btn-ghost small" aria-label={t("business-tracking.tally.plus", { label })} disabled={busy} onClick={onPlus}>
          <span aria-hidden="true">+1</span>
        </button>
        {today > 0 && (
          <>
            <button type="button" className="link small" aria-label={t("business-tracking.tally.minus", { label })} disabled={busy} onClick={onMinus}>-1</button>
            <span className="tk-sub">{t("business-tracking.tally.today", { n: today })}</span>
          </>
        )}
      </span>
    </span>
  );
}

function Cell({ label, children }: { label: string; children: ReactNode }) {
  return <td data-label={label}><div className="tk-v">{children}</div></td>;
}

/** One reading of GitHub's visits by website: the newest value, and the total over reads that never share a day. */
function Hosts({ hosts, state }: { hosts: TrackHost[]; state: Tracking["referrers"]["state"] }) {
  if (!hosts.length) return null;
  const sorted = [...hosts].sort((a, b) => b.last_14d.n - a.last_14d.n || a.host.localeCompare(b.host));
  return (
    <div className="tk-hosts">
      <h3>{t("business-tracking.hosts.title")}</h3>
      <p className="tk-help">{t("business-tracking.hosts.note")}</p>
      <ul>
        {sorted.map((h) => (
          <li key={h.host}>
            <p className="tk-host"><span className="code">{h.host}</span>
              <span className="tk-sub">{h.organic ? t("business-tracking.hosts.organic") : h.shared ? t("business-tracking.hosts.shared", { names: joined(h.channels.map((c) => c.label)) }) : h.channels[0]?.label}</span>
            </p>
            <p className="tk-host-n">
              <Fig f={h.last_14d} /> <span className="tk-sub">{t("business-tracking.came.window", { date: shortDate(h.last_14d.as_of) })}</span>
              {h.at_least.n > h.last_14d.n && <> <Fig f={h.at_least} /> <span className="tk-sub">{t("business-tracking.hosts.total")}</span></>}
            </p>
          </li>
        ))}
      </ul>
      {state === "unreadable" && <p className="tk-help">{t("business-tracking.traffic.unreadable")}</p>}
    </div>
  );
}

/**
 * "Where people came from": the first 10 and the first 100 by place, then one row per place with posts, visits, sign-ups,
 * the people on the list and who got value, each figure with the word for what it rests on. The owner taps +1 for people
 * who are not on the list, types in what their own analytics shows, and says where the post cards should link. Counts
 * only: no rate, no ranking, and "none" is never drawn as 0.
 */
export function CameFrom({ businessId }: { businessId: number }) {
  const { data, error, reload } = useLive<Tracking>(`/businesses/${businessId}/tracking`, TABLES);
  const [opt, setOpt] = useState<Record<string, Opt>>({});
  const [openRow, setOpenRow] = useState<string | null>(null);
  const [tallyError, setTallyError] = useState<{ key: string; text: string } | null>(null);
  const [said, setSaid] = useState("");
  const [pick, setPick] = useState("");
  // the server's fresh view replaces what a tap showed before its answer came
  useEffect(() => setOpt({}), [data]);

  const title = t("business-tracking.came.title");
  if (!data) {
    return (
      <Section id={CAME_ANCHOR} title={title} className="tk">
        {error ? (
          <p className="quiet-error" role="alert">{t("business-tracking.came.load_failed")} <button type="button" className="link" onClick={reload}>{t("common.retry")}</button></p>
        ) : <Skeleton rows={4} />}
      </Section>
    );
  }

  const label = (key: string) => data.rows.find((r) => r.key === key)?.label ?? data.choices.find((c) => c.key === key)?.label ?? key;
  const standing = (key: string): Opt => {
    const r = data.rows.find((x) => x.key === key);
    return opt[key] ?? { today: r?.tallied_today ?? 0, n: r?.tallied?.n ?? 0 };
  };
  const tap = async (key: string, delta: 1 | -1) => {
    const before = standing(key);
    const today = Math.max(0, before.today + delta);
    if (today === before.today) return;
    const next = { today, n: Math.max(0, before.n + (today - before.today)) };
    setTallyError(null);
    setOpt((o) => ({ ...o, [key]: next }));
    setSaid(t("business-tracking.tally.today", { n: today }) + ` ${label(key)}`);
    try {
      const r = await post<{ count: number; total: number }>(`/businesses/${businessId}/tally`, { key, count: today, day: data.today });
      setOpt((o) => (o[key] === next ? { ...o, [key]: { today: r.count, n: r.total } } : o));
    } catch (e: any) {
      setOpt((o) => (o[key] === next ? { ...o, [key]: before } : o));
      setTallyError({ key, text: t("business-tracking.tally.failed", { label: label(key), reason: e?.message ?? String(e) }) });
      reload();
    }
  };

  const { rows, referrers: ref } = data;
  const readable = ref.state !== "unreadable";
  const tallyFor = (r: TrackRow) => r.kind === "channel" || r.kind === "word";
  const empty = rows.length === 0;
  const noDoor = data.doors.length === 0;

  return (
    <Section id={CAME_ANCHOR} title={title} className="tk">
      <p className="tk-lead">{t("business-tracking.came.lead")}</p>
      {error && (
        <p className="quiet-error" role="alert">{t("business-tracking.came.stale_error")} <button type="button" className="link" onClick={reload}>{t("common.retry")}</button></p>
      )}
      {data.too_few && (
        <div className="tk-few" role="note">
          <p>{t("business-tracking.came.too_few")}</p>
          <p className="tk-sub">{t("business-tracking.came.too_few_n", { n: data.answers, min: data.min_answers })}</p>
        </div>
      )}
      <Box>
        <dl className="tk-firsts">{data.milestones.map((m) => <Milestone key={m.n} m={m} />)}</dl>
      </Box>

      {ref.state === "unreadable" && (
        <p className="tk-warn" role="status">{t("business-tracking.traffic.unreadable")}{ref.reason ? ` ${ref.reason}` : ""}</p>
      )}
      {ref.stale && ref.as_of && <p className="tk-warn" role="status">{t("business-tracking.traffic.stale", { date: shortDate(ref.as_of) })}</p>}
      {noDoor && <p className="tk-warn" role="status">{t("business-tracking.came.no_door")}</p>}

      {empty ? (
        <Hint title={t("business-tracking.came.empty")}>{t("business-tracking.came.empty_body")}</Hint>
      ) : (
        <Box pad={false} className="tk-box">
          <table className="tk-table">
            <caption className="sr-only">{t("business-tracking.came.caption")}</caption>
            <thead>
              <tr>
                <th scope="col">{t("business-tracking.came.col.place")}</th>
                <th scope="col">{t("business-tracking.came.col.posts")}</th>
                <th scope="col">{t("business-tracking.came.col.visits")}</th>
                <th scope="col">{t("business-tracking.came.col.signups")}</th>
                <th scope="col">{t("business-tracking.came.col.people")}</th>
                <th scope="col">{t("business-tracking.came.col.got_value")}</th>
                <th scope="col">{t("business-tracking.came.col.told")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const o = standing(r.key);
                const unknownVisits = !r.visits && !readable && r.kind !== "word" && r.kind !== "derived";
                return (
                  <Fragment key={r.key}>
                    <tr>
                      <th scope="row">
                        <span className="tk-place">{r.label}</span>
                        {r.tags.length > 0 && (
                          <button type="button" className="link small tk-open" aria-expanded={openRow === r.key} aria-controls={`tk-tags-${r.key}`}
                            aria-label={t("business-tracking.came.analytics_for", { label: r.label })} onClick={() => setOpenRow(openRow === r.key ? null : r.key)}>
                            {t("business-tracking.came.analytics")}
                          </button>
                        )}
                      </th>
                      <Cell label={t("business-tracking.came.col.posts")}>
                        {r.kind === "channel" ? <b className="num">{num(r.posts)}</b> : <Dash what={t("business-tracking.came.na")} />}
                      </Cell>
                      <Cell label={t("business-tracking.came.col.visits")}>
                        {r.visits ? (
                          <>
                            <Fig f={r.visits} />
                            {r.visits.basis === "read" && <span className="tk-sub">{t("business-tracking.came.window", { date: shortDate(r.visits.as_of) })}</span>}
                            {r.visits_at_least && r.visits_at_least.n > r.visits.n && <span className="tk-sub"><Fig f={r.visits_at_least} /></span>}
                          </>
                        ) : unknownVisits ? <span className="tk-unknown">{t("business-tracking.came.unknown")}</span>
                          : <Dash what={t("business-tracking.came.none")} />}
                      </Cell>
                      <Cell label={t("business-tracking.came.col.signups")}>
                        {r.signups ? <><Fig f={r.signups} />{r.signups.as_of && <span className="tk-sub">{t("business-tracking.came.as_of", { date: shortDate(r.signups.as_of) })}</span>}</>
                          : <Dash what={t("business-tracking.came.none")} />}
                      </Cell>
                      <Cell label={t("business-tracking.came.col.people")}>
                        {r.people.n ? <Fig f={r.people} /> : <Dash what={t("business-tracking.came.none")} />}
                      </Cell>
                      <Cell label={t("business-tracking.came.col.got_value")}>
                        {r.got_value.n ? <Fig f={r.got_value} /> : <Dash what={t("business-tracking.came.none")} />}
                      </Cell>
                      <Cell label={t("business-tracking.came.col.told")}>
                        {tallyFor(r) ? (
                          <Tally label={r.label} basis={r.tallied?.basis_label ?? t("business-tracking.came.basis_told")} today={o.today} n={r.tallied || o.n ? o.n : null} onPlus={() => tap(r.key, 1)} onMinus={() => tap(r.key, -1)} />
                        ) : <Dash what={t("business-tracking.came.na")} />}
                        {tallyError?.key === r.key && <span className="tk-bad" role="alert">{tallyError.text}</span>}
                      </Cell>
                    </tr>
                    {openRow === r.key && (
                      <tr className="tk-more" id={`tk-tags-${r.key}`}>
                        <td colSpan={7}>
                          <TagCounts businessId={businessId} row={r} done={() => { setOpenRow(null); reload(); }} cancel={() => setOpenRow(null)} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
          <p className="tk-legend">{t("business-tracking.came.legend")}</p>
        </Box>
      )}

      <div role="status" className="sr-only">{said}</div>
      <NewTally choices={data.choices} pick={pick} setPick={setPick} tap={(k) => tap(k, 1)} today={(k) => standing(k).today} label={label}
        error={tallyError && !rows.some((r) => r.key === tallyError.key) ? tallyError.text : null} />

      <Hosts hosts={data.hosts} state={ref.state} />
      <Places businessId={businessId} data={data} reload={reload} />
      <SignupAsk signup={data.signup} answered={data.answers > 0} />
    </Section>
  );
}

/** +1 for a place that has no row yet: someone who told the owner where they heard about the product and is not on the list. */
function NewTally({ choices, pick, setPick, tap, today, label, error }: {
  choices: Tracking["choices"]; pick: string; setPick: (v: string) => void; tap: (key: string) => void; today: (key: string) => number;
  label: (key: string) => string; error: string | null;
}) {
  const uid = useId();
  return (
    <div className="tk-new">
      <h3>{t("business-tracking.tally.new_title")}</h3>
      <p className="tk-help">{t("business-tracking.tally.new_help")}</p>
      <div className="tk-new-row">
        <div className="field">
          <label htmlFor={`${uid}-pick`}>{t("business-tracking.tally.new_pick")}</label>
          <select id={`${uid}-pick`} value={pick} onChange={(e) => setPick(e.target.value)}>
            <option value="">{t("business-tracking.tally.new_pick_ph")}</option>
            {choices.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
          </select>
        </div>
        <button type="button" className="btn-ghost" disabled={!pick} aria-label={pick ? t("business-tracking.tally.plus", { label: label(pick) }) : undefined} onClick={() => tap(pick)}>
          <span aria-hidden="true">+1</span>{!pick && <span className="sr-only">+1</span>}
        </button>
        {pick && today(pick) > 0 && <span className="tk-sub">{label(pick)} · {t("business-tracking.tally.today", { n: today(pick) })}</span>}
      </div>
      {error && <p className="tk-bad" role="alert">{error}</p>}
    </div>
  );
}

