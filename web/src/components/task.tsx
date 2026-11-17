import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, failureOf, post, Row, useLive } from "../api";
import { withBusiness } from "../business";
import { Heard, HeardField, heardBody, heardChanged, heardStart } from "../business/HeardField";
import type { FitView, HeardChoice, TrackView } from "../business/types";
import { useChatSubject } from "../chat/subject";
import { LANGS, lookup, lookupIn, t, tx } from "../i18n";
import { dueText } from "../today";
import { HandoutSection } from "./handout";
import { CopyButton, ExtLink, ItemLink, PanelFooter, PanelSkeleton, safeUrl, SidePanel } from "./panel";
import { RulesMark } from "./rules";
import { Badge, shortDate, Time, usd } from "./ui";
import { NextItem, nextLabel } from "./decision";
import { useWorker } from "./frame";
import { keepOnEscape, useDraft } from "./typing";
import "../pages/todos.css";

const PERSON_KINDS = ["contact", "onboard", "follow_up", "referral"];
/** The reminder in either language, for a card stored before the server said `disclosed`. */
const DISCLOSE = LANGS.map((lang) => lookupIn(lang, "work-todo.disclose")).filter(Boolean) as string[];

const stageName = (code: unknown): string => lookup(`label.stage.${String(code ?? "")}`) ?? String(code ?? "");
/** Where a person or thread was found, by its platform key ("direct", "naver_cafe"). */
const platformName = (code: unknown): string => lookup(`business.platform.${String(code ?? "")}`) ?? String(code ?? "");
const monthDay = (iso: string) => shortDate(iso);

/** A `code span` as the server writes a command into a to-do's text. */
export const CODE_SPAN = /`([^`\n]+)`/g;

/** Text with its code spans shown as code, and nothing else read as markup. */
function WithCode({ text }: { text: string }) {
  return <>{text.split(CODE_SPAN).map((part, i) => (i % 2 ? <code key={i}>{part}</code> : part))}</>;
}

/** What a failed save says, by why it failed. A refusal is already the server's own sentence. */
function saveError(e: unknown): string {
  switch (failureOf(e)) {
    case "offline": return t("work-todo.err.offline");
    case "server": return t("work-todo.err.server", { status: (e as ApiError).status ?? "" });
    case "missing": return t("work-todo.err.missing");
    default: return e instanceof Error ? e.message : String(e);
  }
}

/** The error after a failed save, focused so the owner hears it and lands next to what they typed. */
function SaveError({ text }: { text: string }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    box.current?.focus();
  }, [text]);
  return <div className="error" role="alert" tabIndex={-1} ref={box}>{text}</div>;
}

/** `keep` names where the ticks live for this tab, so closing the panel and coming back finds them again. */
function Checklist({ lines, legend, keep }: { lines: string[]; legend: string; keep: string }) {
  const uid = useId();
  const [done, setDone] = useDraft<Record<string, boolean>>(keep, {});
  if (!lines.length) return null;
  return (
    <fieldset className="dp-check">
      <legend>{legend}</legend>
      {lines.map((l, i) => (
        <label key={l} htmlFor={`${uid}-${i}`}>
          <input id={`${uid}-${i}`} type="checkbox" checked={!!done[i]} onChange={(e) => setDone({ [i]: e.target.checked })} />
          <span>{l}</span>
        </label>
      ))}
    </fieldset>
  );
}

interface StepRow { key: string; text: string; minutes: number; done: boolean }

/** The to-do's separate actions in order, each one sitting (backend `services/todo_steps.py`). A tick is kept, so the
 * owner can stop after any step and come back to the next. */
function Steps({ task, onTicked }: { task: Row; onTicked: (row: Row) => void }) {
  const uid = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const steps: StepRow[] = task.steps ?? [];
  if (steps.length === 0) return null;
  const open = task.status === "OPEN";
  const left = steps.filter((x) => !x.done);
  // Numbered lines share the to-do's minutes evenly, so a minute count per line says nothing the total does not.
  const timed = new Set(steps.map((x) => x.minutes)).size > 1;
  async function flip(x: StepRow) {
    setBusy(true);
    setError(null);
    try {
      onTicked(await post<Row>(`/owner-tasks/${task.id}/steps`, { key: x.key, done: !x.done }));
    } catch (e) {
      setError(saveError(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="dp-sec" aria-labelledby={`${uid}-h`} aria-busy={busy}>
      <h3 id={`${uid}-h`}>{t("work-todo.steps")}</h3>
      {open && <p className="muted small">{left.length
        ? t("work-todo.steps_left", { n: left.length, min: left.reduce((m, x) => m + x.minutes, 0) })
        : t("work-todo.steps_all")}</p>}
      <ol className="dp-todo-steps">
        {steps.map((x, i) => (
          <li key={x.key} className={x.done ? "done" : undefined}>
            <input id={`${uid}-${i}`} type="checkbox" checked={x.done} disabled={!open || busy} onChange={() => flip(x)} />
            <label htmlFor={`${uid}-${i}`}>
              <span className="step-n">{i + 1}.</span> <WithCode text={x.text} />
              {timed && <span className="muted small"> · {t("home.list.about_min", { n: x.minutes })}</span>}
            </label>
          </li>
        ))}
      </ol>
      {error && <SaveError text={error} />}
    </section>
  );
}

function Where({ url, label }: { url: unknown; label: string }) {
  const safe = safeUrl(url);
  if (!safe) return null;
  return (
    <div className="dp-where">
      <p className="dp-url" title={safe}>{safe}</p>
      <div className="row wrap">
        <ExtLink className="btn outline" href={safe}>{t("work-todo.open")}<span className="sr-only">{t("work-todo.new_tab", { label })}</span></ExtLink>
        <CopyButton text={safe} what={label} />
      </div>
    </div>
  );
}

const DOOR_NAME = {
  site: "business-tracking.door.site", store: "business-tracking.door.store", map: "business-tracking.door.map",
  package: "business-tracking.door.package", repo: "business-tracking.door.repo",
} as const;
const doorName = (kind: string) => (kind in DOOR_NAME ? t(DOOR_NAME[kind as keyof typeof DOOR_NAME]) : kind);

/**
 * "Your link": the address on the post card that leads to the owner's own place, one line per place, and the one line
 * that says why it is tagged, plain or missing. The server builds the link and the tag (`track` on the to-do); the model
 * wrote none of it, so nothing here is made up.
 */
function YourLink({ track, place, businessId }: { track: TrackView; place: string; businessId: number | null }) {
  const door = businessId ? (
    <ItemLink to={`/business?b=${businessId}&tab=numbers#places`}>{t("business-tracking.link.go_places")}</ItemLink>
  ) : null;
  const numbers = businessId ? (
    <ItemLink to={`/business?b=${businessId}&tab=numbers#came-from`}>{t("business-tracking.link.go_numbers")}</ItemLink>
  ) : null;
  const tagged = track.links.find((l) => l.param);
  return (
    <section className="dp-sec" aria-labelledby="tp-link">
      <h3 id="tp-link">{t("term.tracked_link")}</h3>
      {track.links.map((l) => (
        <div className="dp-door" key={`${l.door}-${l.url}`}>
          <p className="dp-door-n">{doorName(l.door)}</p>
          <Where url={l.url} label={doorName(l.door)} />
        </div>
      ))}
      {track.state === "tagged" && tagged && (
        <p className="small dp-link-why">{tx("business-tracking.link.tagged", { tag: <code>{`${tagged.param}=${track.tag}`}</code> })}</p>
      )}
      {track.state === "plain" && (
        <p className="small dp-link-why">{track.link === "plain" ? t("business-tracking.link.plain", { place }) : t("business-tracking.link.plain_dropped")} {track.link !== "plain" && numbers}</p>
      )}
      {track.state === "say" && (
        <>
          {track.say && (
            <div className="dp-say">
              <p className="dp-door-n" id="tp-say">{t("business-tracking.link.say_head")}</p>
              <p className="dp-say-t" aria-labelledby="tp-say"><q>{track.say}</q></p>
              <CopyButton text={track.say} what={t("business-tracking.link.say_copy")} />
            </div>
          )}
          <p className="small dp-link-why">{t("business-tracking.link.say_help")} {numbers}</p>
        </>
      )}
      {track.state === "no_door" && <p className="small dp-link-why">{t("business-tracking.link.no_door")} {door}</p>}
    </section>
  );
}

const FIT_GUESS = {
  buyer: "work-todo.fit_guess.buyer",
  reach: "work-todo.fit_guess.reach",
  has: "work-todo.fit_guess.has",
} as const;

/**
 * Where the pick came from when the customer description it used was a guess, or that the description changed since.
 * The server sends `fit_view` on the to-do, and nothing renders without it.
 */
function FitMark({ fit, businessId }: { fit: FitView | null | undefined; businessId: number | null }) {
  const lines = (fit?.guessed ?? []).filter((g) => g.line in FIT_GUESS && g.labels.length);
  if (!fit || (!fit.changed && !lines.length)) return null;
  const check = businessId ? <ItemLink to={`/business?b=${businessId}#customers`}>{t("business-profile.check")}</ItemLink> : null;
  return (
    <div className="dp-fit" role="note">
      {fit.changed ? (
        <p>{t("work-todo.fit_changed")} {check}</p>
      ) : lines.map((g, i) => (
        <p key={g.line}>{t(FIT_GUESS[g.line as keyof typeof FIT_GUESS], { list: g.labels.join("; ") })}{i === lines.length - 1 && <> {check}</>}</p>
      ))}
    </div>
  );
}

function PostCard({ card, keep, fit, businessId, track }: { card: Row; keep: string; fit?: FitView | null; businessId: number | null; track: TrackView | null }) {
  const what = card.what ?? {};
  const outline = card.mode === "outline";
  return (
    <>
      <FitMark fit={fit} businessId={businessId} />
      <section className="dp-sec" aria-labelledby="tp-where">
        <h3 id="tp-where">{t("work-todo.where")}</h3>
        <p><strong>{card.channel_name}</strong></p>
        <Where url={card.where_url} label={t("work-todo.where_url")} />
        {!safeUrl(card.where_url) && <p className="muted small">{t("work-todo.no_url")}</p>}
        <RulesMark card={card} businessId={businessId} />
        {card.rules_state !== "own" && card.rules_summary && <div className="todo-rules"><p className="muted small">{t("work-todo.rules")}</p><p>{card.rules_summary}</p></div>}
        {safeUrl(card.rules_source) && <p className="small"><ExtLink href={card.rules_source}>{t("work-todo.rules_source")}</ExtLink></p>}
      </section>
      {track && <YourLink track={track} place={card.channel_name ?? ""} businessId={businessId} />}
      {card.when && (
        <section className="dp-sec" aria-labelledby="tp-when">
          <h3 id="tp-when">{t("work-todo.when")}</h3>
          <p><strong>{card.when.window}</strong>{card.when.reason && <span className="muted"> · {card.when.reason}</span>}</p>
        </section>
      )}
      {card.why && (
        <section className="dp-sec" aria-labelledby="tp-why">
          <h3 id="tp-why">{t("work-todo.why")}</h3>
          <p>{card.why}</p>
        </section>
      )}
      <section className="dp-sec" aria-labelledby="tp-what">
        <h3 id="tp-what">{t("work-todo.what")}</h3>
        {outline ? (
          <>
            <p className="dp-notice">{t("work-todo.outline")}</p>
            {what.facts?.length > 0 && (
              <>
                <p className="dp-sub">{t("work-todo.facts")}</p>
                <ul className="dp-list">{what.facts.map((f: string) => <li key={f}>{f}</li>)}</ul>
              </>
            )}
            {what.expected_questions?.length > 0 && (
              <>
                <p className="dp-sub">{t("work-todo.expected")}</p>
                <ul className="dp-qs">
                  {what.expected_questions.map((q: Row) => (
                    <li key={q.q}>
                      <strong>{q.q}</strong>
                      <ul className="dp-list">{q.talking_points.map((x: string) => <li key={x}>{x}</li>)}</ul>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </>
        ) : (
          <>
            <div className="dp-draft-h">
              <p className="dp-sub">{t("work-todo.title")}</p>
              <CopyButton text={what.title ?? ""} what={t("work-todo.title")} />
            </div>
            <p className="dp-draft-title">{what.title}</p>
            <div className="dp-draft-h">
              <p className="dp-sub">{t("work-todo.body")}</p>
              <CopyButton text={what.body ?? ""} what={t("work-todo.body")} />
            </div>
            <pre className="dp-draft">{what.body}</pre>
            <p className="muted small">{card.edited_by_owner ? t("work-todo.edited") : t("work-todo.draft")}</p>
          </>
        )}
      </section>
      <Checklist lines={what.checklist ?? []} legend={t("work-todo.check_post")} keep={keep} />
    </>
  );
}

function PersonCard({ card, fresh, keep }: { card: Row; fresh: Row | null; keep: string }) {
  const p = card.person ?? {};
  const checklist: string[] = card.checklist ?? [];
  const reminded = card.disclosed === true || checklist.some((l) => DISCLOSE.includes(l.trim()));
  const source = lookup(`label.source.${p.source}`) ?? p.source_ko;
  return (
    <>
      <section className="dp-sec" aria-labelledby="tp-who">
        <h3 id="tp-who">{t("work-todo.who")}</h3>
        <p className="dp-who">
          <strong>{p.display_name || p.handle}</strong>
          {p.display_name && p.display_name !== p.handle && <span className="muted"> @{p.handle}</span>}
          <span className="muted"> · {platformName(p.platform)}</span> <Badge value={stageName(fresh ? fresh.stage : p.stage)} tone="muted" raw />
        </p>
        <p className="muted small">
          {source}
          {safeUrl(p.where_found_url) && <> · <ExtLink href={p.where_found_url}>{t("work-todo.found_at")}</ExtLink></>}
        </p>
      </section>
      {card.quotes?.length > 0 && (
        <section className="dp-sec" aria-labelledby="tp-quote">
          <h3 id="tp-quote">{t("work-todo.said")}</h3>
          {card.quotes.map((q: Row) => (
            <p key={q.text} className="dp-quote">
              “{q.text}”{safeUrl(q.url) && <> <ExtLink className="small" href={q.url}>{t("work-todo.original")}</ExtLink></>}
            </p>
          ))}
        </section>
      )}
      <section className="dp-sec" aria-labelledby="tp-why">
        <h3 id="tp-why">{t("work-todo.why_them")}</h3>
        <p>{card.why}</p>
        {card.problem && <p className="muted small">{t("work-todo.problem", { problem: card.problem })}</p>}
      </section>
      {card.stop_rule && (
        <section className="dp-sec" aria-labelledby="tp-stop">
          <h3 id="tp-stop">{t("work-todo.stop_rule")}</h3>
          <p>{card.stop_rule}</p>
        </section>
      )}
      {card.questions?.length > 0 && (
        <section className="dp-sec" aria-labelledby="tp-ask">
          <h3 id="tp-ask">{t("work-todo.ask")}</h3>
          <ol className="dp-list ordered">{card.questions.map((q: string) => <li key={q}>{q}</li>)}</ol>
        </section>
      )}
      {card.talking_points?.length > 0 && (
        <section className="dp-sec" aria-labelledby="tp-points">
          <h3 id="tp-points">{t("work-todo.points")}</h3>
          <ul className="dp-list">{card.talking_points.map((q: string) => <li key={q}>{q}</li>)}</ul>
        </section>
      )}
      <HandoutSection handout={card.handout} />
      <section className="dp-sec">
        {!reminded && <p className="dp-notice">{t("work-todo.disclose")}</p>}
        <Checklist lines={checklist} legend={t("work-todo.check_contact")} keep={keep} />
        {card.note && <p className="muted small">{card.note}</p>}
      </section>
    </>
  );
}

function ThreadCard({ card, keep }: { card: Row; keep: string }) {
  const th = card.thread ?? {};
  return (
    <>
      <section className="dp-sec" aria-labelledby="tp-thread">
        <h3 id="tp-thread">{t("work-todo.thread")}</h3>
        <p><strong>{platformName(th.platform)}</strong>{th.asker && <span className="muted"> · {t("work-todo.asker", { asker: th.asker })}</span>}</p>
        <Where url={card.thread_url} label={t("work-todo.thread_url")} />
        {card.quote && <p className="dp-quote">“{card.quote}”</p>}
      </section>
      <section className="dp-sec" aria-labelledby="tp-asks">
        <h3 id="tp-asks">{t("work-todo.asks")}</h3>
        <p>{card.asks}</p>
      </section>
      {card.talking_points?.length > 0 && (
        <section className="dp-sec" aria-labelledby="tp-share">
          <h3 id="tp-share">{t("work-todo.share")}</h3>
          <ul className="dp-list">{card.talking_points.map((q: string) => <li key={q}>{q}</li>)}</ul>
        </section>
      )}
      <section className="dp-sec">
        <p className="dp-notice">{t("work-todo.just_help")}</p>
        <Checklist lines={card.checklist ?? []} legend={t("work-todo.check_reply")} keep={keep} />
        {card.note && <p className="muted small">{card.note}</p>}
      </section>
    </>
  );
}

/** `stepsFrom` names the field the to-do's steps were read from, folded here so the steps are not read twice. */
function GenericCard({ card, stepsFrom }: { card: Row; stepsFrom: string | null }) {
  return (
    <>
      {card.what && (stepsFrom === "what" ? (
        <details className="dp-sec dp-fold">
          <summary>{t("work-todo.what_whole")}</summary>
          <p className="pre-wrap"><WithCode text={String(card.what)} /></p>
        </details>
      ) : (
        <section className="dp-sec" aria-labelledby="tp-gwhat">
          <h3 id="tp-gwhat">{t("work-todo.what")}</h3>
          <p className="pre-wrap"><WithCode text={String(card.what)} /></p>
        </section>
      ))}
      <HandoutSection handout={card.handout} folded={stepsFrom === "handout"} />
      {card.why && (
        <section className="dp-sec" aria-labelledby="tp-gwhy">
          <h3 id="tp-gwhy">{t("work-todo.why")}</h3>
          <p className="pre-wrap">{card.why}</p>
        </section>
      )}
    </>
  );
}

function Updates({ updates }: { updates: Row[] }) {
  if (!updates.length) return null;
  const last = updates[updates.length - 1];
  const body: string | undefined = last.post_card?.what?.body;
  return (
    <section className="dp-sec" aria-labelledby="tp-upd">
      <h3 id="tp-upd">{updates.length > 1 ? t("work-todo.revised_n", { n: updates.length }) : t("work-todo.revised")}</h3>
      <p className="muted small">{t("work-todo.revised_body")}</p>
      <p><strong>{last.title}</strong></p>
      {body ? (
        <>
          <div className="dp-draft-h">
            <p className="dp-sub">{t("work-todo.body")}</p>
            <CopyButton text={body} what={t("work-todo.revised_copy")} />
          </div>
          <pre className="dp-draft">{body}</pre>
        </>
      ) : last.summary && <p className="pre-wrap">{last.summary}</p>}
    </section>
  );
}

/** What the owner pays for a decision that costs money (owner_tasks._payment): the amount the decision estimated and
 * the place its payload names, or that it names none. The company pays for nothing by hand. */
function Payment({ payment }: { payment: Row | undefined }) {
  if (!payment) return null;
  const where = typeof payment.where === "string" ? payment.where : null;
  return (
    <section className="dp-sec" aria-labelledby="tp-pay">
      <h3 id="tp-pay">{t("work-todo.payment")}</h3>
      <p>{t("work-todo.payment.by_you")}</p>
      {payment.amount_usd != null && <p>{t("work-todo.payment.amount", { amount: usd(payment.amount_usd) })}</p>}
      {where ? (safeUrl(where) ? <Where url={where} label={t("work-todo.payment.where_label")} />
        : <p>{t("work-todo.payment.where", { where })}</p>)
        : <p className="muted small">{t("work-todo.payment.where_unknown")}</p>}
    </section>
  );
}

function CardView({ task, person }: { task: Row; person: Row | null }) {
  const card = task.card ?? {};
  const keep = `ship.todo.checks.${task.id}`;
  const body = card.person ? <PersonCard card={card} fresh={person} keep={keep} />
    : card.thread ? <ThreadCard card={card} keep={keep} />
    : card.mode ? <PostCard card={card} keep={keep} fit={task.fit_view} businessId={task.business_id ?? null} track={task.track ?? card.track ?? null} />
    : <GenericCard card={card} stepsFrom={task.steps_from ?? null} />;
  return (
    <>
      <Updates updates={card.updates ?? []} />
      <Payment payment={card.payment} />
      {body}
    </>
  );
}

/** Owner to-dos that put something up in public: two of them back to back may be the same post twice. */
export const POST_KINDS = ["post", "publish"];

/** `collects` marks a post saved with its link, whose reactions the worker reads for two weeks. */
type Finished = { lines: string[]; collects?: boolean };

function Result({ task, finished, onReopen, busy, error, next, onClose }: {
  task: Row; finished: Finished | null; onReopen: () => void; busy: boolean; error: string | null;
  next: NextItem | null; onClose: () => void;
}) {
  const done = task.status === "DONE";
  const r = task.result ?? {};
  const box = useRef<HTMLElement>(null);
  const head = useRef<HTMLParagraphElement>(null);
  const worker = useWorker();
  useEffect(() => {
    box.current?.scrollIntoView({ block: "nearest" });
    // only right after the owner finished it here; opening a closed to-do leaves focus on the panel heading
    if (finished) head.current?.focus();
  }, []);
  return (
    <section className="receipt" role="status" aria-label={t("work-todo.record")} ref={box}>
      <p className="receipt-h" tabIndex={-1} ref={head}><b>{done ? t("work-todo.done") : t("work-todo.skipped")}</b>{task.done_at && <span className="muted small"> · <Time v={task.done_at} /></span>}</p>
      {finished?.lines.map((l) => <p key={l} className="small">{l}</p>)}
      {finished?.collects && worker.down && <p className="small">{t("work-todo.post_worker_off")}</p>}
      <dl className="kv">
        {r.link && <div><dt>{t("work-todo.link")}</dt><dd className="pre-wrap">{safeUrl(r.link) ? <ExtLink href={r.link}>{r.link}</ExtLink> : r.link}</dd></div>}
        {r.note && <div><dt>{done ? t("work-todo.note") : t("work-todo.reason")}</dt><dd className="pre-wrap">{r.note}</dd></div>}
      </dl>
      {error && <SaveError text={error} />}
      {next?.post && POST_KINDS.includes(task.kind) && <p className="small warn-t">{t("work-todo.next_same_post")}</p>}
      <div className="row wrap sp-next">
        {next ? <button type="button" className="outline-btn" onClick={next.open}>{nextLabel(next)}</button>
          : <p className="small muted">{t("work-todo.nothing_next")}</p>}
        <button type="button" className="link small" onClick={onClose}>{t("common.close")}</button>
        <button type="button" className="link small" disabled={busy} onClick={onReopen}>{t("work-todo.reopen")}</button>
      </div>
    </section>
  );
}

const SCHEME = /^[a-z][a-z0-9+.-]*:/i;
/** Something typed without `https://` that still reads as an address: a dotted host, then a path or query. */
const BARE_ADDRESS = /^[^\s/?#@:]+\.[a-z]{2,}(?::\d+)?(?:[/?#]\S*)?$/i;

/**
 * The link as it will be sent: `https://` added to a bare address, null when it is no web address at all. `notPost`
 * is a link the to-do's channel would refuse as not a post's own page (`post_link`, the server's `post_link_problem`).
 */
function readLink(raw: string, shape: PostShape | null): { url: string | null; notPost: boolean } {
  const typed = raw.trim();
  const full = BARE_ADDRESS.test(typed) ? `https://${typed}` : SCHEME.test(typed) ? typed : null;
  let u: URL | null = null;
  try {
    u = full ? new URL(full) : null;
  } catch {
    /* not an address */
  }
  if (!u || (u.protocol !== "http:" && u.protocol !== "https:")) return { url: null, notPost: false };
  const address = u.hostname.replace(/\.$/, "") + u.pathname + u.search;
  const notPost = !!shape && !shape.patterns.some((p) => new RegExp(`^(?:${p})$`, "i").test(address));
  return { url: full, notPost };
}

interface PostShape {
  patterns: string[];
  example: string;
}

function Complete({ task, fresh, onResult }: { task: Row; fresh: Row | null; onResult: (row: Row, finished: Finished | null) => void }) {
  const uid = useId();
  const card = task.card ?? {};
  const person = PERSON_KINDS.includes(task.kind);
  const needs: string | null = task.result_needs ?? null;
  const needsNote = needs === "note";
  const isPost = task.kind === "post";
  // A post's link is the main path: the company reads its reactions from it. Without one the owner says why.
  const linkFirst = isPost && needs === "link_or_note";
  const current: string = fresh?.stage ?? card.person?.stage ?? "found";
  const forward: string[] = fresh?.next_stages ?? card.next_stages ?? [];
  const stages = [current, ...forward.filter((s) => s !== current)];
  const [link, setLink] = useState("");
  const [note, setNote] = useState("");
  const [stage, setStage] = useState("");
  const [quote, setQuote] = useState("");
  const [day, setDay] = useState("");
  const [why, setWhy] = useState("");
  const [heard, setHeard] = useState<Heard>(() => heardStart(fresh));
  const [heardError, setHeardError] = useState<string | null>(null);
  const [skipping, setSkipping] = useState(false);
  const [busy, setBusy] = useState<"done" | "skip" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checkLink, setCheckLink] = useState(false);
  // a post without a link asks for the reason only once the owner says there is no link: one question at a time
  const [withoutLink, setWithoutLink] = useState(false);
  // the business's channels for the "how did they hear about you?" pick; asked only for a person's to-do
  const choices = useLive<{ choices: HeardChoice[] }>(person && task.business_id ? `/businesses/${task.business_id}/tracking` : null).data?.choices ?? null;
  const heardNow = person && heardChanged(heard, fresh);
  const noteBox = useRef<HTMLTextAreaElement>(null);
  const linkBox = useRef<HTMLInputElement>(null);
  const whyBox = useRef<HTMLTextAreaElement>(null);
  const tomorrow = new Date(Date.now() + 86_400_000).toLocaleDateString("sv-SE");
  const dropped = stage === "dropped";
  const typed = !!link.trim();
  const parsed = readLink(link, isPost ? task.post_link ?? null : null);
  const badLink = typed && !parsed.url;
  const notPost = typed && parsed.notPost;
  const noLink = linkFirst && !typed;
  const ready = !busy && (!needsNote || note.trim()) && (!person || stage) && (!linkFirst || typed);
  const readyNoLink = !busy && noLink && !!note.trim();
  const askNote = !linkFirst || typed || withoutLink;

  useEffect(() => {
    if (skipping) whyBox.current?.focus();
  }, [skipping]);
  useEffect(() => {
    if (withoutLink) noteBox.current?.focus();
  }, [withoutLink]);

  async function finish(withLink: boolean) {
    if (withLink && (badLink || notPost)) {
      setCheckLink(true);
      linkBox.current?.focus();
      return;
    }
    setBusy("done");
    setError(null);
    setHeardError(null);
    const at = day && !dropped ? new Date(`${day}T09:00:00`) : null;
    try {
      const row = await post<Row>(`/owner-tasks/${task.id}/done`, person
        ? { stage, quote: quote.trim() || undefined, next_action_at: at?.toISOString(), ...(heardNow ? heardBody(heard) : {}) }
        : { link: (withLink && parsed.url) || undefined, note: note.trim() || undefined });
      onResult(row, await consequences(row, at));
    } catch (e) {
      // a refusal with a contact detail in their words: the quote is the only other free text, so with no quote it is the words'
      if (heardNow && heard.words.trim() && !quote.trim() && failureOf(e) === "rejected") setHeardError(saveError(e));
      else setError(saveError(e));
      setBusy(null);
    }
  }

  async function consequences(row: Row, at: Date | null): Promise<Finished> {
    const lines: string[] = [];
    if (person) {
      const name = card.person?.display_name || card.person?.handle || t("work-todo.this_person");
      lines.push(t(stage === current ? "work-todo.stayed" : "work-todo.moved", { name, stage: stageName(stage) }));
      if (quote.trim()) lines.push(t("work-todo.quote_saved"));
      if (heardNow) lines.push(t("business-tracking.heard.saved"));
      if (at) {
        let due = at.toISOString();
        try {
          const open = await api<{ items: Row[] }>(withBusiness("/owner-tasks?status=OPEN", row.business_id));
          due = open.items.find((x) => x.kind === "follow_up" && x.card?.person_id === card.person_id)?.due_at ?? due;
        } catch {
          /* the date the owner typed is still the date */
        }
        lines.push(t("work-todo.follow_up", { date: monthDay(due) }));
      }
    } else if (isPost) {
      const linked = !!row.result?.link;
      return { lines: [t(linked ? "work-todo.post_saved" : "work-todo.post_saved_nolink")], collects: linked };
    } else if (task.kind === "check") {
      lines.push(t("work-todo.check_saved"));
    } else {
      lines.push(t("work-todo.marked"));
    }
    return { lines };
  }

  async function skip() {
    setBusy("skip");
    setError(null);
    try {
      onResult(await post<Row>(`/owner-tasks/${task.id}/skip`, { note: why.trim() }), { lines: [t("work-todo.skip_saved")] });
    } catch (e) {
      setError(saveError(e));
      setBusy(null);
    }
  }

  const linkLabel = isPost ? "work-todo.link_post" : task.kind === "participate" ? "work-todo.link_reply" : "work-todo.link_result";
  const linkHelp = [isPost && `${uid}-link-help`, (badLink || notPost) && checkLink && `${uid}-link-bad`]
    .filter(Boolean).join(" ") || undefined;

  const form = (
    <form className="dp-complete" aria-label={t("work-todo.form")} aria-busy={!!busy} noValidate onSubmit={(e) => {
      e.preventDefault();
      if (ready && !skipping) finish(true);
    }}>
      <h3>{t(skipping ? "work-todo.skip_head" : "work-todo.form_head")}</h3>
      {skipping ? (
        <>
          <div className="field">
            <label htmlFor={`${uid}-why`}>{t("work-todo.skip_why")}<span className="muted">{t("work-todo.required")}</span></label>
            <textarea id={`${uid}-why`} ref={whyBox} rows={2} required value={why} onChange={(e) => setWhy(e.target.value)} />
          </div>
          <div className="row wrap">
            <button type="button" disabled={!why.trim() || !!busy} onClick={skip}>{busy === "skip" ? t("common.working") : t("work-todo.skip")}</button>
            <button type="button" className="link" disabled={!!busy} onClick={() => setSkipping(false)}>{t("common.cancel")}</button>
          </div>
        </>
      ) : person ? (
        <>
          <div className="field">
            <label htmlFor={`${uid}-stage`}>{t("work-todo.stage_ask")}</label>
            <select id={`${uid}-stage`} value={stage} onChange={(e) => setStage(e.target.value)}>
              <option value="" disabled>{t("work-todo.stage_pick")}</option>
              {stages.map((s) => <option key={s} value={s}>{s === current ? t("work-todo.stage_same", { stage: stageName(s) }) : stageName(s)}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor={`${uid}-quote`}>{t("work-todo.quote")} <span className="muted">{t("work-todo.quote_help")}</span></label>
            <textarea id={`${uid}-quote`} rows={2} maxLength={400} value={quote} onChange={(e) => setQuote(e.target.value)} />
          </div>
          <HeardField value={heard} onChange={setHeard} choices={choices} now={fresh} error={heardError} disabled={!!busy} />
          {!dropped && (
            <div className="field">
              <label htmlFor={`${uid}-day`}>{t("work-todo.day")} <span className="muted">{t("work-todo.day_help")}</span></label>
              <input id={`${uid}-day`} type="date" min={tomorrow} value={day} onChange={(e) => setDay(e.target.value)} />
            </div>
          )}
        </>
      ) : (
        <>
          <div className="field">
            <label htmlFor={`${uid}-link`}>
              {t(linkLabel)}
              {!isPost && <span className="muted">{t("work-todo.if_any")}</span>}
            </label>
            <input id={`${uid}-link`} ref={linkBox} type="url" inputMode="url" autoComplete="off" placeholder="https://" value={link}
              aria-invalid={(badLink || notPost) && checkLink ? true : undefined} aria-describedby={linkHelp}
              onBlur={() => setCheckLink(true)} onChange={(e) => {
                setLink(e.target.value);
                setCheckLink(false);
              }} />
            {isPost && <p id={`${uid}-link-help`} className="muted small">{t("work-todo.link_post_help")}</p>}
            {badLink && checkLink && <p id={`${uid}-link-bad`} className="todo-field-bad small" role="alert">{t("work-todo.link_bad")}</p>}
            {notPost && checkLink && (
              <p id={`${uid}-link-bad`} className="todo-field-bad small" role="alert">{t("work-todo.link_not_post", { example: task.post_link.example })}</p>
            )}
          </div>
          {askNote && <div className="field">
            <label htmlFor={`${uid}-note`}>
              {t(needsNote ? "work-todo.note_check" : noLink ? "work-todo.note_why" : "work-todo.note")}
              <span className="muted">{needsNote || noLink ? t("work-todo.required") : t("work-todo.if_any")}</span>
            </label>
            <textarea id={`${uid}-note`} ref={noteBox} rows={needsNote ? 10 : 2} maxLength={task.note_max} required={needsNote || noLink} value={note}
              onChange={(e) => setNote(e.target.value)} />
            {noLink && !note.trim() && (
              <p className="small"><button type="button" className="link" onClick={() => setNote(t("work-todo.phone_note"))}>{t("work-todo.phone_note")}</button></p>
            )}
          </div>}
        </>
      )}
      {error && <SaveError text={error} />}
      {!skipping && (
        <div className="row wrap sp-actions">
          <button type="submit" className="primary" disabled={!ready}>{busy === "done" ? t("common.working") : t("work-todo.finish")}</button>
          {noLink && !withoutLink && <button type="button" className="link" onClick={() => setWithoutLink(true)}>{t("work-todo.no_link_open")}</button>}
          {noLink && withoutLink && <button type="button" disabled={!readyNoLink} onClick={() => finish(false)}>{t("work-todo.finish_nolink")}</button>}
          <button type="button" className={linkFirst ? "link" : undefined} disabled={!!busy} onClick={() => setSkipping(true)}>{t("work-todo.skip")}</button>
        </div>
      )}
    </form>
  );
  // The post form is pinned under the panel so its link field and buttons show without scrolling past the card.
  return linkFirst ? <PanelFooter>{form}</PanelFooter> : form;
}

export function TaskPanel({ id, next, onClose, onChanged }: {
  id: number; next: NextItem | null; onClose: () => void; onChanged: () => void;
}) {
  const { data, error, failure, reload } = useLive<Row>(`/owner-tasks/${id}`, ["owner_tasks"]);
  useChatSubject({ kind: "owner_task", ref: String(id) }, "panel");
  return (
    <SidePanel item={`OT-${id}`} title={data?.title ?? t("term.todo")} onClose={onClose}>
      {!data ? (
        // a missing to-do (an old link, an archived business) says so and offers the list; retrying cannot help
        error && failure === "missing" ? (
          <div className="load-error" role="alert">
            <p className="load-error-t">{t("work-todo.panel.missing", { id: `OT-${id}` })}</p>
            <Link className="btn-ghost small" to="/my-tasks">{t("work-todo.panel.to_list")}</Link>
          </div>
        ) : error ? <p className="quiet-error" role="alert">{error} <button className="link" onClick={reload}>{t("common.retry")}</button></p> : <PanelSkeleton />
      ) : (
        <TaskBody key={data.id} task={data} next={next} onClose={onClose} onChanged={onChanged} />
      )}
    </SidePanel>
  );
}

function TaskBody({ task: loaded, next, onClose, onChanged }: { task: Row; next: NextItem | null; onClose: () => void; onChanged: () => void }) {
  const [row, setRow] = useState<Row | null>(null);
  const [finished, setFinished] = useState<Finished | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const task = row ?? loaded;
  const due = dueText(task.due_at);
  const { data: person } = useLive<Row>(
    PERSON_KINDS.includes(task.kind) && task.card?.person_id ? `/people/${task.card.person_id}` : null, ["people"]);
  async function reopen() {
    setBusy(true);
    setError(null);
    try {
      setRow(await post<Row>(`/owner-tasks/${task.id}/reopen`, {}));
      setFinished(null);
      onChanged();
    } catch (e) {
      setError(saveError(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="dp" onKeyDown={keepOnEscape}>
      <p className="dp-tags">
        <Badge value={lookup(`label.todoKind.${task.shown_kind ?? task.kind}`) ?? task.kind_label} tone="info" raw />
        {task.request_id && task.request_title && (
          <span className="small muted">{tx("work-todo.from_decision", { title: <ItemLink to={`/inbox?req=${task.request_id}`}>{task.request_title}</ItemLink> })}</span>
        )}
        {task.status === "OPEN" && due && <span className="muted small">{due}</span>}
      </p>
      {task.status !== "OPEN" && (
        <Result task={task} finished={finished} onReopen={reopen} busy={busy} error={error} next={next} onClose={onClose} />
      )}
      <Steps task={task} onTicked={(res) => {
        setRow(res);
        onChanged();
      }} />
      <CardView task={task} person={person} />
      {task.status === "OPEN" && (
        <Complete task={task} fresh={person} onResult={(res, fin) => {
          setRow(res);
          setFinished(fin);
          onChanged();
        }} />
      )}
    </div>
  );
}
