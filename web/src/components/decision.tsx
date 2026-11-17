import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { failureOf, JourneysMissing, post, Row, SpecJourney, useLive } from "../api";
import { useBusinessLive, useBusinessSelection } from "../business";
import { useChatSubject } from "../chat/subject";
import { LANGS, lookupIn, PlainKey, t, tx } from "../i18n";
import { agentName, decisionLabel, label } from "../labels";
import { EvidenceSummary } from "./evidence";
import { useWorker } from "./frame";
import { HandoutSection } from "./handout";
import { MergeReview } from "./MergeReview";
import { deep } from "./strengths";
import { CopyButton, PanelFooter, PanelLink, PanelSkeleton, safeUrl, SidePanel } from "./panel";
import { RulesMark } from "./rules";
import { keepOnEscape, useDraft } from "./typing";
import { Badge, Folded, LoadError, Markdown, RefLink, rid, Time, toneOf, usd, when } from "./ui";
import { SpecDoc } from "./SpecDoc";
import { WhereSits } from "./WhereSits";
import { Walkthrough } from "./Walkthrough";
import { screensUnread, SpecScreens } from "../wireflow/Wireflow";
import "./decision.css";

// Requests the owner answers in words; the backend records them as ANSWERED instead of granting an action.
const isQuestion = (r: Row) => ["QUESTION", "ESCALATION", "CLARIFICATION"].includes(r.type);
// The product plan's approval card (engineering/plans.py).
const PLAN = "dev.plan_approve";

type Decision = "approve" | "reject" | "defer" | "answer";
/** One option the owner can pick, as the server reads it. `verdict` is null only for an untyped option from before
 * options carried their decision. */
interface Choice {
  /** The option as the server stored it, which `respond` matches. Labels are scrubbed for reading and can repeat. */
  key: string;
  label: string;
  summary: string | null;
  risks: string[];
  evidence_ids: string[];
  recommended: boolean;
  verdict: Decision | null;
  /** The plan line as it reads once this option is picked, when the card amends one. */
  plan_line: string | null;
}
interface Undo { level: "easy" | "hard" | "none"; says: string; how: string | null; by: "server" | "agent" }
/** The request as a report, built by the server (`services/requests.report`). */
interface Report {
  headline: string;
  owner_does: string | null;
  why: string | null;
  on_approve: string | null;
  undo: Undo | null;
  typed_confirm: boolean;
  /** Why the number is typed: the policy's strong approval, or money on an action the company has no rule for. */
  typed_confirm_why: "strong" | "unknown_spend" | null;
  choices: Choice[];
  post: Row | null;
  handout?: Row | null;
  /** Who settled it and why, for a request the owner did not decide (a withdrawal, a policy approval). */
  closed?: string | null;
  post_edit: { title: string | null; body: string } | null;
  revises: string | null;
  /** A held or declined card: whether the owner can reopen it, and the server's reason when not. */
  reopen?: { allowed: boolean; why: string | null } | null;
  /** A waiting card: whether a hold or a decline of it could be reopened later. */
  reopen_later?: { defer: boolean; reject: boolean } | null;
}

const CUSTOM = "\u0000custom";
const BUTTON: Record<Decision, PlainKey> = {
  approve: "decide.button.approve", answer: "decide.button.answer", defer: "decide.button.defer", reject: "decide.button.reject",
};
const VERDICT_TONE: Record<Decision, string> = { approve: "ok", answer: "ok", defer: "warn", reject: "bad" };

const reportOf = (r: Row): Report => r.report;

/**
 * Why Approve needs the decision's number typed first, as the server decides it and `respond` enforces it
 * (`requests.typed_confirm_why`): the policy's strong approval, or money going out on an action the company has no rule
 * for (an unknown kind collapsed to `*.other`).
 */
export function strongConfirm(r: Row): "server" | "unknown_spend" | null {
  if (!r.report?.typed_confirm) return null;
  return r.report.typed_confirm_why === "unknown_spend" ? "unknown_spend" : "server";
}

const RISK_TONE: Record<string, string> = { medium: "warn", high: "bad", critical: "bad" };

const EMAIL = "external.email_send";
type EmailMissing = "no_payload" | "no_recipient" | "no_subject" | "no_body";
const EMAIL_MISSING: Record<EmailMissing, PlainKey> = {
  no_payload: "decide.email.missing.no_payload", no_recipient: "decide.email.missing.no_recipient",
  no_subject: "decide.email.missing.no_subject", no_body: "decide.email.missing.no_body",
};
/** An email card the panel cannot show in full: what is missing, or null when recipient, subject and body all read. */
function emailMissing(r: Row): EmailMissing | null {
  if (r.action_kind !== EMAIL) return null;
  return r.email_preview_missing ?? (r.email_preview ? null : "no_payload");
}


/** What the owner's decision set in motion, in the order the backend does it. */
function consequences(r: Row): PlainKey[] {
  const out: PlainKey[] = [];
  const resp = r.response ?? {};
  if (r.status === "APPROVED" || r.status === "ANSWERED") {
    if (r.action_kind === "dev.spec_approve") out.push("decide.after.spec");
    else if (r.action_kind === PLAN) out.push("decide.after.plan");
    else if (r.action_kind === "dev.merge") out.push("decide.after.merge");
    else if (r.type === "DEVELOPMENT") out.push("decide.after.dev");
    if (resp.post_edit) out.push("decide.after.post_edit");
    if (r.blocking) out.push("decide.after.unblocked");
  }
  // A merge turned down is a fix on the same branch, whose instructions are the note (engineering/revise.py).
  const fix = r.status === "REJECTED" && r.action_kind === "dev.merge";
  if (fix) out.push(!resp.note ? "decide.after.merge_ask" : r.fix_budget ? "decide.after.merge_fix_budget" : "decide.after.merge_fix");
  else if (r.status === "REJECTED" && r.action_kind === PLAN && resp.chosen_option
    && resp.chosen_option === r.action_params?.changes_option) out.push("decide.after.plan_changes");
  // Notes the owner dropped are no longer rewritten from: the card then reads as the plain decline it became.
  else if (r.status === "REJECTED") out.push(resp.screen_notes?.length && !resp.notes_dropped ? "decide.wf.sent_back" : "decide.after.rejected");
  if (r.status === "DEFERRED") out.push("decide.after.deferred");
  if ((resp.note || resp.answer) && !fix) out.push("decide.after.noted");
  return out;
}

const ORIGIN: Record<string, PlainKey> = { owner: "decide.rule.owner", brief: "decide.rule.brief", stock: "decide.rule.stock" };

export function ruleCite(r: Row): string | null {
  const rule = r.response?.door_rule;
  if (!rule?.action) return null;
  const origin = ORIGIN[rule.origin];
  return origin ? t("decide.rule.cite_from", { action: rule.action, origin: t(origin) }) : t("decide.rule.cite", { action: rule.action });
}

/** The owner's own words, as they decided them; never a JSON dump. */
function OwnerDecision({ r }: { r: Row }) {
  const x = r.response ?? {};
  if (x.decision === "merged") return <p className="small">{t("decide.owner.merged", { into: x.into })}{x.note && <>: {x.note}</>}</p>;
  // Nobody answered: the card's rule closed it or went on with its default, which is never the owner's answer.
  if (x.by === "expiry" && reportOf(r).closed) return <p className="small">{reportOf(r).closed}</p>;
  if (x.decision === "withdrawn")
    return <p className="small">{reportOf(r).closed
      ?? <>{t("decide.owner.withdrawn", { who: agentName(x.by) })}{x.note && <>: {x.note}</>}</>}</p>;
  const rows: [PlainKey, string | undefined][] = [
    ["decide.owner.decision", decisionLabel(x.decision)],
    ["decide.owner.chosen", x.chosen_option],
    ["decide.owner.answer", x.answer],
    ["decide.owner.note", x.note],
  ];
  return (
    <dl className="kv">
      {rows.filter(([, v]) => v).map(([k, v]) => (
        <div key={k}><dt>{t(k)}</dt><dd className="pre-wrap">{v}</dd></div>
      ))}
      {(x.responded_by || r.resolved_at) && (
        <div><dt>{t("decide.owner.responded")}</dt><dd>{[x.responded_by && agentName(x.responded_by), when(x.responded_at ?? r.resolved_at)].filter(Boolean).join(" · ")}</dd></div>
      )}
    </dl>
  );
}

function Prose({ text, projectId }: { text: string; projectId: number | null }) {
  return <div className="prose"><Markdown text={text} projectId={projectId} /></div>;
}

/** How hard approving is to take back, and when only the asking agent's card says so, whose judgement that is. */
function undoText(u: Undo, by: unknown): string {
  return `${u.says}.${u.how ? ` ${u.how}.` : ""}${u.by === "agent" ? t("decide.undo.agent", { who: agentName(by) }) : ""}`;
}

/**
 * What declining does, in the words the result will use (`consequences`). A declined card is closed for good: the
 * server takes no second decision on it, so the sentence says so, except for a merge, whose decline is a fix the
 * owner is asked about again.
 */
function ifDecline(r: Row, note: string): string {
  if (r.action_kind === "dev.merge") {
    return t(!note.trim() ? "decide.after.merge_ask" : r.fix_budget ? "decide.after.merge_fix_budget" : "decide.after.merge_fix");
  }
  return `${t("decide.after.rejected")} ${t(reportOf(r).reopen_later?.reject ? "decide.if_decline.reopen" : "decide.if_decline.final")}`;
}

/** The conclusion, what the owner does, why, and what approving and declining do: read before anything else. */
function ReportHead({ r, note }: { r: Row; note: string }) {
  const rep = reportOf(r);
  const rows: [PlainKey, string][] = [];
  if (rep.owner_does) rows.push(["decide.report.owner_does", rep.owner_does]);
  if (rep.why) rows.push(["decide.report.why", rep.why]);
  if (rep.revises) rows.push(["decide.report.revises", rep.revises]);
  const onApprove: PlainKey = r.type === "QUESTION" ? "decide.report.on_answer" : "decide.report.on_approve";
  // A channel that takes only the owner's own words gets no drafted post, whatever the generic sentence promises.
  if (rep.on_approve) rows.push([onApprove, rep.post?.mode === "outline" ? t("decide.post.outline_on_approve") : rep.on_approve]);
  if (r.status === "PENDING") rows.push(["decide.report.on_decline", ifDecline(r, note)]);
  return (
    <>
      {!r.title.includes(rep.headline.replace(/…$/, "")) && <p className="dp-q">{rep.headline}</p>}
      {rows.length > 0 && (
        <dl className="dp-report">
          {rows.map(([k, v]) => (
            <div key={k}>
              <dt>{t(k)}</dt>
              <dd>
                {v}
                {k === onApprove && rep.undo && <span className={`dp-undo dp-undo-${rep.undo.level}`}> {undoText(rep.undo, r.from_agent)}</span>}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </>
  );
}

interface PostDraft { title: string; body: string }

/** The post the owner is asked to put up. Open, a draft channel's title and body are edited in place, and what the
 * owner leaves there is what the owner task will copy. Closed, the text as approved. */
function PostSection({ r, draft, setDraft, dupes }: {
  r: Row; draft: PostDraft | null; setDraft: ((d: PostDraft) => void) | null; dupes: Row[];
}) {
  const uid = useId();
  const selected = useBusinessSelection().current?.id ?? null;
  const businessId: number | null = r.business_id ?? selected;
  const card = reportOf(r).post;
  if (!card) return null;
  const what = card.what ?? {};
  const edited = reportOf(r).post_edit;
  const original = { title: what.title ?? "", body: what.body ?? "" };
  const changed = !!draft && (draft.title !== original.title || draft.body !== original.body);
  const where = safeUrl(card.where_url);
  return (
    <section className="dp-sec dp-post" aria-labelledby={`${uid}-h`}>
      <div className="dp-post-h">
        <h3 id={`${uid}-h`}>{t("decide.post.title")}</h3>
        <span className="muted small">{[card.channel_name, card.when?.window].filter(Boolean).join(" · ")}</span>
      </div>
      <RulesMark card={card} businessId={businessId} />
      {r.status === "PENDING" && dupes.length > 0 && (
        <p className="dp-warn" role="note">
          {tx("decide.post.duplicate", { channel: card.channel_name ?? card.channel, todo: (
            <PanelLink to={`/my-tasks?ot=${dupes[0].id}`} target={{ key: "task", id: dupes[0].id }}>{dupes[0].title}</PanelLink>) })}
        </p>
      )}
      {card.mode === "outline" ? (
        <>
          <p className="dp-notice">{t("decide.post.outline")}</p>
          {what.facts?.length > 0 && <ul className="dp-list">{what.facts.map((f: string) => <li key={f}>{f}</li>)}</ul>}
        </>
      ) : draft && setDraft ? (
        <>
          <div className="field">
            <label htmlFor={`${uid}-t`}>{t("decide.post.field_title")}</label>
            <input id={`${uid}-t`} value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
          </div>
          <div className="field">
            <label htmlFor={`${uid}-b`}>{t("decide.post.field_body")}</label>
            <textarea id={`${uid}-b`} className="dp-post-body" rows={10} value={draft.body}
              onChange={(e) => setDraft({ ...draft, body: e.target.value })} />
          </div>
          <p className="muted small dp-post-note">
            {changed ? t("decide.post.edited_note") : t("decide.post.edit_hint")}
            {changed && <> <button type="button" className="link small" onClick={() => setDraft(original)}>{t("decide.post.reset")}</button></>}
          </p>
        </>
      ) : (
        <>
          <div className="dp-draft-h">
            <p className="dp-draft-title">{edited?.title ?? original.title}</p>
            <CopyButton text={edited?.body ?? original.body} what={t("decide.post.field_body")} />
          </div>
          <pre className="dp-draft">{edited?.body ?? original.body}</pre>
          {edited && <p className="muted small">{t("decide.post.edited_by_owner")}</p>}
        </>
      )}
      {(where || card.rules_summary || what.checklist?.length > 0) && (
        <details className="dp-fold dp-fold-in" open={deep(r.fit, "marketing")}>
          <summary>{t("decide.post.where")}</summary>
          {where && <p className="dp-url" title={where}>{where}</p>}
          {card.rules_state !== "own" && card.rules_summary && <p className="dp-rules"><span className="muted small">{t("decide.post.rules")}</span><br />{card.rules_summary}</p>}
          {what.checklist?.length > 0 && <ul className="dp-list">{what.checklist.map((c: string) => <li key={c}>{c}</li>)}</ul>}
        </details>
      )}
    </section>
  );
}

/** What the spec did with each thing the owner said or approved (engineering/owner_trace.py). What it left out
 * comes first, open; what it kept is one fold away. `source` and `requirement_ids` are the spec writer's ids, which
 * the owner has no use for; `from` says where the item came from in the owner's words (rows from before it: none). */
interface TraceRow { source: string; from?: string; says: string; disposition: "included" | "dropped" | "not_product" | "missed";
  requirement_ids: string[]; reason: string }

function OwnerTrace({ r }: { r: Row }) {
  const rows: TraceRow[] = r.action_params?.owner_trace ?? [];
  if (r.action_kind !== "dev.spec_approve" || !rows.length) return null;
  const left = rows.filter((x) => x.disposition === "dropped" || x.disposition === "missed");
  const kept = rows.filter((x) => x.disposition === "included");
  if (!kept.length && !left.length) return null;
  return (
    <section className="dp-sec dp-trace" aria-label={t("decide.trace.label")}>
      <h3>{t("decide.trace.title", { n: kept.length + left.length, kept: kept.length })}</h3>
      {left.length > 0 && (
        <ul className="dp-list dp-trace-left">
          {left.map((x) => (
            <li key={`${x.source}-${x.says}`}>
              <b>{x.disposition === "dropped" ? t("decide.trace.dropped") : t("decide.trace.missed")}</b>
              <div>
                <p>{x.says}</p>
                {x.reason && <p className="muted small">{x.reason}</p>}
                {x.from && <p className="muted small">{x.from}</p>}
              </div>
            </li>
          ))}
        </ul>
      )}
      {left.length > 0 && <p className="muted small">{t("decide.trace.fix")}</p>}
      {kept.length > 0 && (
        <details className="dp-fold dp-fold-in">
          <summary>{t("decide.trace.kept", { n: kept.length })}</summary>
          <ul className="dp-list">
            {kept.map((x) => (
              <li key={`${x.source}-${x.says}`}>{x.says}{x.from && <span className="muted small"> · {x.from}</span>}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

/** What the spec gives users and what the owner settled for it, read before the trace and the spec itself
 * (engineering/specs.py: `stories` in priority order, `rules` the product's rules approving sets, `clarified` the
 * owner's answers to the spec's questions). */
interface StoryRow { id: string; priority: "P1" | "P2" | "P3"; says: string }
const JOURNEYS_MISSING: Record<JourneysMissing, PlainKey> = {
  none: "decide.spec.journeys_missing.none", too_many: "decide.spec.journeys_missing.too_many",
  steps: "decide.spec.journeys_missing.steps", see: "decide.spec.journeys_missing.see", test: "decide.spec.journeys_missing.test",
};
interface RuleRow { id: string; rule: string; from: string }
interface Clarified { kind?: "clarify" | "amend"; question: string; answer: string; assumed: boolean; note?: string }

function SpecSummary({ r }: { r: Row }) {
  // a story the wireflow already walks is not listed twice
  const walked = new Set<string>(r.screens?.mode === "plan" ? r.screens.rows.map((x: { story: string }) => x.story) : []);
  const told: StoryRow[] = (r.action_params?.stories ?? []).filter((x: StoryRow) => !walked.has(x.id));
  const rules: RuleRow[] = r.action_params?.rules ?? [];
  const clarified: Clarified[] = r.action_params?.clarified ?? [];
  const journeys: SpecJourney[] = r.action_params?.journeys ?? [];
  // the warning is for the owner who has yet to decide; a decided card shows the journeys it was decided with
  const missing: JourneysMissing | null = r.status === "PENDING" ? r.action_params?.journeys_missing ?? null : null;
  if (r.action_kind !== "dev.spec_approve" || (!told.length && !rules.length && !clarified.length && !journeys.length && !missing)) return null;
  return (
    <>
      {told.length > 0 && (
        <section className="dp-sec" aria-label={t("decide.stories.title")}>
          <h3>{t("decide.stories.title")}</h3>
          <ol className="dp-stories">
            {told.map((x) => (
              <li key={x.id}>
                <Badge value={x.priority} tone={x.priority === "P1" ? "info" : "muted"} raw />
                <span>{x.says}</span>
              </li>
            ))}
          </ol>
          {(!walked.size || told.some((x) => x.priority === "P1")) && <p className="muted small">{t("decide.stories.p1")}</p>}
        </section>
      )}
      {(journeys.length > 0 || missing) && (
        <section className="dp-sec" aria-label={t("decide.spec.journeys")}>
          <h3>{t("decide.spec.journeys")}</h3>
          {missing && (
            <p className="dp-warn" role="note">{t(JOURNEYS_MISSING[missing])} {t("decide.spec.journeys_missing.owner")}</p>
          )}
          {journeys.length > 0 && (
            <ol className="dp-stories dp-journeys">
              {journeys.map((x) => (
                <li key={x.id}>
                  <Badge value={x.priority} tone={x.priority === "P1" ? "info" : "muted"} raw />
                  <span>
                    <b>{x.title}</b> <span className="muted">· {x.persona}</span>
                    <span className="dp-journey-s muted small">{t("decide.wt.success", { success: x.success })}</span>
                  </span>
                </li>
              ))}
            </ol>
          )}
        </section>
      )}
      {rules.length > 0 && (
        <section className="dp-sec" aria-label={t("decide.rules.title")}>
          <h3>{t("decide.rules.title")}</h3>
          <ul className="dp-rules">
            {rules.map((x) => (
              <li key={x.id}>{x.rule} <span className="muted small">({x.from})</span></li>
            ))}
          </ul>
          <p className="muted small">{t("decide.rules.yours")}</p>
        </section>
      )}
      {clarified.length > 0 && (
        <section className="dp-sec" aria-label={t("decide.clarified.title")}>
          <h3>{t("decide.clarified.title")}</h3>
          <dl className="dp-answers">
            {clarified.map((x) => (
              <div key={x.question}>
                <dt>{x.question}</dt>
                <dd>
                  <b>{x.answer}</b>
                  {x.assumed && <> <Badge value={t("decide.clarified.assumed")} tone="warn" raw />
                    <span className="muted small"> {t(x.kind === "amend" ? "decide.clarified.kept_why" : "decide.clarified.assumed_why")}</span></>}
                  {x.note && <div className="muted small">{t("decide.clarified.note", { note: x.note })}</div>}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      )}
    </>
  );
}

/** Why a spec card's text is not on screen: the panel has not read it yet, or the server could not (`card_spec`). */
function specUnread(r: Row): "loading" | "failed" | null {
  if (r.action_kind !== "dev.spec_approve" || r.spec?.text) return null;
  return r.spec === undefined ? "loading" : "failed";
}

/** The spec this card asks to approve, as approving pins it (the card's commit), one fold below the conclusion. */
function SpecText({ r, onRetry }: { r: Row; onRetry: (() => void) | null }) {
  const unread = specUnread(r);
  if (unread) {
    return r.status === "PENDING" ? (
      <p className="dp-warn" role="note">
        {t(unread === "loading" ? "decide.spec.loading" : "decide.spec.missing")}
        {unread === "failed" && onRetry && <> <button type="button" className="link" onClick={onRetry}>{t("common.retry")}</button></>}
      </p>
    ) : null;
  }
  if (!r.spec?.text) return null;
  return (
    <details className="dp-fold prose dp-spec" open={deep(r.fit, "software")}>
      <summary>{t("decide.spec.read")}</summary>
      <SpecDoc text={r.spec.text} projectId={r.project_id ?? null} />
    </details>
  );
}

/** Why a plan card's text is not on screen: the panel has not read it yet, or the server could not (`card_plan`). */
function planUnread(r: Row): "loading" | "failed" | null {
  if (r.action_kind !== PLAN || r.plan_doc?.text) return null;
  return r.plan_doc === undefined ? "loading" : "failed";
}

/** The product plan this card asks to approve, at the card's commit. It is the decision, so it reads open. While its
 * text is missing, the line under the note says why Approve is off; here is only the way to load it again. */
function PlanText({ r, onRetry }: { r: Row; onRetry: (() => void) | null }) {
  const unread = planUnread(r);
  if (unread) {
    return r.status === "PENDING" && unread === "failed" && onRetry ? (
      <p><button type="button" className="link" onClick={onRetry}>{t("common.retry")}</button></p>
    ) : null;
  }
  if (!r.plan_doc?.text) return null;
  return (
    <details className="dp-fold prose dp-spec" open={r.status === "PENDING"}>
      <summary>{t("decide.plan.read")}</summary>
      <Markdown text={r.plan_doc.text} projectId={r.project_id ?? null} />
    </details>
  );
}

interface SpecPlanRow { request_id: number | null; plan_key: string | null; commit_sha: string | null; title: string | null;
  stale: boolean; legacy: boolean }

/** The product plan a spec card's spec follows, one link away, and a word when it changed since or there is none. */
function SpecPlan({ r }: { r: Row }) {
  const p: SpecPlanRow | null | undefined = r.plan;
  if (r.action_kind !== "dev.spec_approve" || !p) return null;
  if (p.legacy) return <p className="muted small">{t("decide.plan.legacy")}</p>;
  return (
    <>
      <p className="small dp-from">
        <span className="muted">{t("term.product_plan")}</span>{" "}
        {p.request_id ? <PanelLink to={`/inbox?req=${p.request_id}`} target={{ key: "req", id: p.request_id }}>{p.title}</PanelLink> : p.title}
      </p>
      {p.stale && <p className="dp-warn" role="note">{t("decide.plan.stale")}</p>}
    </>
  );
}

/** The email approving sends, word for word: who gets it, the subject and the whole body, before the decision.
 * When a part cannot be read, the panel says which and Approve stays off (the gateway would not send it either). */
function EmailSection({ r }: { r: Row }) {
  const uid = useId();
  if (r.action_kind !== EMAIL) return null;
  const mail: { to: string[]; subject: string; body: string } | null = r.email_preview ?? null;
  const missing = emailMissing(r);
  return (
    <section className="dp-sec dp-email" aria-labelledby={`${uid}-h`}>
      <h3 id={`${uid}-h`}>{t("decide.email.title")}</h3>
      {missing && r.status === "PENDING" && <p className="dp-warn" role="alert">{t(EMAIL_MISSING[missing])}</p>}
      {mail && (
        <>
          <dl className="kv dp-email-h">
            <div><dt>{t("decide.email.to")}</dt><dd>{mail.to.length ? mail.to.join(", ") : <span className="muted">{t("decide.email.none")}</span>}</dd></div>
            <div><dt>{t("decide.email.subject")}</dt><dd>{mail.subject || <span className="muted">{t("decide.email.none")}</span>}</dd></div>
          </dl>
          {mail.body ? <pre className="dp-draft dp-email-body">{mail.body}</pre> : <p className="muted small">{t("decide.email.no_body")}</p>}
        </>
      )}
    </section>
  );
}

function Metrics({ r }: { r: Row }) {
  const metrics: Row[] = r.card?.metrics ?? [];
  if (!metrics.length) return null;
  const pid: number | null = r.project_id ?? null;
  return (
    <>
      <h3>{t("decide.metrics")}</h3>
      <dl className="facts dp-facts">
        {metrics.map((m, i) => (
          <div key={`${m.label}-${i}`}>
            <dt>{m.label}</dt>
            <dd><strong>{m.value}</strong>{m.basis && <span className="muted small dp-basis"><Markdown text={m.basis} projectId={pid} /></span>}</dd>
          </div>
        ))}
      </dl>
    </>
  );
}

const citedIn = (...texts: (string | null | undefined)[]) => {
  const seen = new Set<string>();
  for (const t of texts) for (const m of (t ?? "").matchAll(/(?<![\w-])EV-(\d+)(?!\d)/g)) seen.add(m[1]);
  return [...seen];
};

/** Everything behind the report: the CEO's reasoning, the numbers, each option's evidence, and the request itself. */
function Detail({ r, shown }: { r: Row; shown: string | null }) {
  const pid: number | null = r.project_id ?? null;
  const c = r.card;
  // the recommended option's evidence is already above the choices; the fold keeps the others
  const withEvidence = reportOf(r).choices.filter((o) => o.evidence_ids.length > 0 && o.key !== shown);
  const cited = citedIn(r.summary, r.rationale);
  return (
    <details className="dp-fold" open={deep(r.fit)}>
      <summary>{t("common.details")}</summary>
      {c?.recommendation && <><h3>{t("decide.detail.recommendation")}</h3><Prose text={c.recommendation} projectId={pid} /></>}
      <Metrics r={r} />
      {withEvidence.map((o) => (
        <EvidenceSummary key={o.label} ids={o.evidence_ids} projectId={pid} title={t("decide.detail.option_evidence", { option: o.label })} />
      ))}
      <h3>{t("decide.detail.request")}</h3>
      <Folded text={r.summary} projectId={pid} readable />
      {cited.length > 0 && <EvidenceSummary ids={cited} projectId={pid} title={t("decide.detail.request_evidence")} />}
      {r.rationale && <><h3>{t("decide.detail.rationale")}</h3><Folded text={r.rationale} projectId={pid} limit={600} readable /></>}
    </details>
  );
}

/**
 * The owner's open to-dos (the list My to-dos shows), read once per panel for the post card and the receipt. The
 * receipt reloads it right after the decision, so the to-do the decision added shows without waiting for a live event.
 */
function useOpenTodos(wanted: boolean): { items: Row[]; reload: () => void } {
  const { data, reload } = useBusinessLive<Row>(wanted ? "/owner-tasks" : null, ["owner_tasks"]);
  return { items: (data?.items ?? []).filter((x: Row) => x.status === "OPEN"), reload };
}

/**
 * What the decision set in motion, said once it is made: the result, the owner's words, what started (or why it has
 * not started yet, when the worker is off), the to-dos it added, and the way to the next item. Focus lands on the next
 * step after a decision, so the keyboard never falls back to the top of the page.
 */
function Receipt({ r, next, onNext, onClose, fresh, created, promised, onReopened }: {
  r: Row; next: NextItem | null; onNext: () => void; onClose: () => void; fresh: boolean; created: Row[]; promised: string | null;
  onReopened: (row: Row) => void;
}) {
  const projectManager = !!r.by_project_manager;
  const reopen = reportOf(r).reopen ?? null;
  const [reopening, setReopening] = useState(false);
  const [reopenError, setReopenError] = useState<string | null>(null);
  async function doReopen() {
    setReopening(true);
    setReopenError(null);
    try {
      onReopened(await post<Row>(`/requests/${r.id}/reopen`, {}));
    } catch (e: any) {
      const kind = failureOf(e);
      setReopenError(kind === "offline" ? t("decide.failed_offline") : t("decide.reopen.failed", { error: e.message }));
      setReopening(false);
    }
  }
  const cite = ruleCite(r);
  const worker = useWorker();
  const box = useRef<HTMLDivElement>(null);
  const go = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    box.current?.scrollIntoView({ block: "nearest" });
    if (fresh) go.current?.focus();
  }, [fresh]);
  const said = projectManager ? [] : consequences(r);
  const approved = r.status === "APPROVED" || r.status === "ANSWERED";
  // the server's own sentence for what approving does, when no line above already says it. The server writes it only
  // while the card waits, so the receipt repeats the one the panel showed right before the decision.
  const did = approved && !projectManager && !said.some((c) => AFTER_APPROVE.includes(c)) ? promised ?? reportOf(r).on_approve : null;
  return (
    <div className={`receipt ${toneOf(r.status)}`} ref={box}>
      <div className="receipt-head">
        <p className="receipt-h" role="status"><b>{label(r.status)}</b>{projectManager && <span className="muted small">{t("decide.receipt.by_ceo")}</span>}</p>
        {r.status === "ANSWERED" && !projectManager && r.project_id && (
          <Link className="small receipt-fix" to={`/projects/${r.project_id}`} title={t("decide.receipt.correct_hint")}>{t("decide.receipt.correct")}</Link>
        )}
      </div>
      <OwnerDecision r={r} />
      {cite && <p className="small muted">{cite}</p>}
      {(did || said.length > 0 || created.length > 0) && (
        <ul className="small receipt-list">
          {did && <li>{t(r.status === "ANSWERED" ? "decide.receipt.did_answer" : "decide.receipt.did", { what: did })}{r.estimated_cost_usd ? t("decide.receipt.cost", { cost: usd(r.estimated_cost_usd) }) : ""}</li>}
          {said.map((c) => (
            <li key={c}>{c === "decide.after.unblocked" && worker.down ? t("decide.after.unblocked_waits") : t(c)}</li>
          ))}
          {created.map((x) => (
            <li key={x.id}>{tx(x.kind === "spend" ? "decide.receipt.todo_spend" : "decide.receipt.todo", { todo: (
              <PanelLink to={`/my-tasks?ot=${x.id}`} target={{ key: "task", id: x.id }}>{x.title}</PanelLink>) })}</li>
          ))}
        </ul>
      )}
      {reopen && (reopen.allowed ? (
        <div className="row wrap receipt-reopen">
          <button type="button" className="btn-ghost small" disabled={reopening} onClick={doReopen}
            aria-describedby={`receipt-reopen-${r.id}`}>{reopening ? t("common.working") : t("decide.reopen.button")}</button>
          <span id={`receipt-reopen-${r.id}`} className="small muted">{t("decide.reopen.hint")}</span>
        </div>
      ) : reopen.why && <p className="small muted">{reopen.why}</p>)}
      {reopenError && <p className="error" role="alert">{reopenError}</p>}
      <div className="row wrap sp-next">
        {next ? (
          <button ref={go} type="button" className="outline-btn" onClick={onNext}>{nextLabel(next)}</button>
        ) : (
          <p className="small muted">{t("decide.receipt.none_left")}</p>
        )}
        <button ref={next ? undefined : go} type="button" className="btn-ghost receipt-close" onClick={onClose}>{t("common.close")}</button>
      </div>
    </div>
  );
}

const AFTER_APPROVE: PlainKey[] = ["decide.after.spec", "decide.after.merge", "decide.after.dev", "decide.after.post_edit"];

/** The next item the receipt offers: a decision or a to-do, and whether that to-do is a post (put up by the owner). */
export interface NextItem {
  title: string;
  kind: "decision" | "todo";
  post: boolean;
  open: () => void;
}

/** "Next decision: …" or "Next to-do: …", so the owner knows what the button opens before pressing it. */
export const nextLabel = (next: NextItem) =>
  t(next.kind === "decision" ? "decide.receipt.next_decision" : "decide.receipt.next_todo", { title: next.title });

/** An option that is a bare decision word, as cards before options were typed wrote them: the buttons already say it.
 * An agent's label keeps the language it was written in, so the words match in every language. */
const DECISION_WORDS: PlainKey[] = ["decide.word.approve", "decide.word.hold", "decide.word.defer", "decide.word.decline",
  "decide.word.reject", "decide.word.answer"];
const BARE = new Set(DECISION_WORDS.flatMap((k) => LANGS.map((l) => lookupIn(l, k)?.toLowerCase())));
const bareWord = (c: Choice) => BARE.has(c.label.trim().toLowerCase());

/** What the owner has typed into an open card, kept for this tab per card and revision until it is decided. */
interface Typed { [k: string]: unknown; answer: string; note: string; option: string; post: PostDraft | null;
  /** Notes on single planned screens of a spec card, by screen id. They go with a decline and send the spec back. */
  screens: Record<string, string> }

function useDecision(r: Row, onDecided: (row: Row) => void, onRefused: () => void) {
  const uid = useId();
  const rep = reportOf(r);
  // A bare hold or decline is dropped even though the server reads it as that decision: the hold and decline buttons
  // show whenever no option is one. A bare approve or answer stays, since a no picked by default would leave no button for it.
  const choices = rep.choices.filter((c) => !bareWord(c) || c.verdict === "approve" || c.verdict === "answer");
  // The recommendation outlives that cleanup: a dropped bare "Defer" still names the decision the CEO recommends,
  // which the buttons then lead with.
  const recommended = rep.choices.find((c) => c.recommended) ?? null;
  const recDropped = !!recommended && !choices.includes(recommended);
  // A lone option that is a yes, or says nothing, is what approving does, not a pick: it reads as a line above the
  // buttons, never as a radio beside them, so the panel has one set of controls.
  const lone = choices.length === 1 ? choices[0] : null;
  const radios = choices.length > 1 || (!!lone && (isQuestion(r) || (lone.verdict !== null && lone.verdict !== "approve")));
  const question = isQuestion(r);
  const what = rep.post?.mode === "draft" ? rep.post.what ?? {} : null;
  const firstOption = (choices.find((c) => c.recommended) ?? choices[0])?.key ?? (question ? CUSTOM : "");
  const [typed, setTyped, clearTyped] = useDraft<Typed>(r.status === "PENDING" ? `ship.draft.req.${r.id}.${r.revision}` : null, {
    answer: "", note: "", option: firstOption, post: what ? { title: what.title ?? "", body: what.body ?? "" } : null,
    screens: {},
  });
  const screenNotes = Object.entries(typed.screens).map(([screen, text]) => ({ screen, note: text.trim() })).filter((x) => x.note);
  // A note on a screen sends the spec back. Approving would build the draft as it is and a hold decides nothing, so
  // both are off until the owner sends the notes or clears them (the server refuses them too).
  const noted = screenNotes.length;
  const { answer, note, post: draft } = typed;
  // a kept pick the card no longer offers falls back to the default rather than to a guessed decision
  const option = typed.option === CUSTOM || choices.some((c) => c.key === typed.option) ? typed.option : firstOption;
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState<Decision | null>(null);
  const [error, setError] = useState<string | null>(null);
  const chosen = choices.find((c) => c.key === option) ?? null;
  // The decision the picked option is. Null for a legacy option whose words do not say: the owner then picks the
  // decision with plain buttons, and approval is never guessed from free text.
  const verdict: Decision | null = chosen ? chosen.verdict
    : option === CUSTOM ? "answer" : question ? "answer" : "approve";
  const edited = !!draft && !!what && (draft.title !== (what.title ?? "") || draft.body !== (what.body ?? ""));
  const strong = strongConfirm(r);
  const typedConfirm = strong !== null;
  const confirmed = confirm.trim().toUpperCase() === rid(r.id);
  const mailMissing = emailMissing(r);
  // the owner can't approve a spec they haven't read (owner decision, 2026-11-09): no override
  const specMissing = specUnread(r) !== null;
  const planMissing = planUnread(r) !== null;
  const screensMissing = screensUnread(r) !== null;
  // A plan card's "request changes" sends the note as the changes, so it waits for one (engineering/plans.send_back).
  const asksChanges = r.action_kind === PLAN && !!chosen && chosen.key === r.action_params?.changes_option;
  // Free text is only sent while its field is on screen; a hidden draft must never override the visible choice.
  const answerText = question && (option === CUSTOM || choices.length === 0) ? answer.trim() : "";
  const blocked = (d: Decision) => !!busy || (typedConfirm && d === "approve" && !confirmed)
    || (noted > 0 && (d === "approve" || d === "defer")) || (d === "reject" && asksChanges && !note.trim())
    || (d === "approve" && (!!mailMissing || specMissing || planMissing || screensMissing))
    || (d === "answer" && !chosen && !answerText) || (d === "approve" && edited && !draft?.body.trim());

  async function act(decision: Decision) {
    setBusy(decision);
    setError(null);
    try {
      const row = await post<Row>(`/requests/${r.id}/respond`, {
        decision, answer: answerText || undefined, note: note || undefined,
        chosen_option: chosen && (chosen.verdict ? chosen.verdict === decision : decision === "approve" || decision === "answer")
          ? chosen.key : undefined,
        confirm_text: confirm || undefined,
        post_edit: decision === "approve" && edited && draft ? { title: draft.title, body: draft.body } : undefined,
        // The revision on screen: a card revised since is refused, so the owner never decides one they did not read.
        revision: r.revision,
        screen_notes: decision === "reject" && noted ? screenNotes : undefined,
      });
      clearTyped();
      onDecided(row);
    } catch (e: any) {
      const kind = failureOf(e);
      setError(kind === "offline" ? t("decide.failed_offline") : kind === "missing" ? t("decide.failed_missing")
        : t("decide.failed", { error: e.message }));
      setBusy(null);
      onRefused();
    }
  }
  return { uid, choices, radios, question, answer, setAnswer: (v: string) => setTyped({ answer: v }), note,
    setNote: (v: string) => setTyped({ note: v }), confirm, setConfirm, option, setOption: (v: string) => setTyped({ option: v }),
    draft, setDraft: (v: PostDraft) => setTyped({ post: v }), edited, busy, error, typedConfirm, strong, chosen, verdict,
    typed: answerText, blocked, act, recommended, recDropped, mailMissing, specMissing, planMissing, screensMissing, noted, asksChanges,
    screens: typed.screens, clearScreens: () => setTyped({ screens: {} }),
    setScreen: (screen: string, text: string) => setTyped({ screens: { ...typed.screens, [screen]: text } }) };
}

type DecisionState = ReturnType<typeof useDecision>;

/** The CEO's pick, always on screen while the card is open: the option (or the decision a bare option names) and how
 * much evidence stands behind it, so a recommendation with nothing behind it reads differently from a checked one. */
function Recommendation({ r, d }: { r: Row; d: DecisionState }) {
  const rec = d.recommended;
  if (!rec) return null;
  const said = rec.verdict && bareWord(rec) ? decisionLabel(rec.verdict) : rec.label;
  const n = rec.evidence_ids.length;
  return (
    <section className="dp-rec" aria-label={t("decide.rec.label")}>
      <p>
        <span className="dp-rec-k">{t("decide.rec.title")}</span> <b>{said}</b>
        {rec.verdict && !bareWord(rec) && rec.label !== decisionLabel(rec.verdict) && <span className="muted small"> · {decisionLabel(rec.verdict)}</span>}
        {n === 0 && <span className="dp-rec-none"> · {t("decide.rec.no_evidence")}</span>}
      </p>
      {n > 0 && <EvidenceSummary ids={rec.evidence_ids} projectId={r.project_id ?? null} title={t("decide.rec.evidence")} />}
    </section>
  );
}

/** The one set of choices. Each option carries the decision it is, so picking it is the decision. */
function Choices({ r, d }: { r: Row; d: DecisionState }) {
  if (!d.choices.length && !d.question) return null;
  const pid: number | null = r.project_id ?? null;
  // when every option is the same decision (all answers), a decision badge on each says nothing
  const oneVerdict = new Set(d.choices.map((c) => c.verdict)).size <= 1;
  return (
    <section className="dp-sec">
      {!d.radios && d.chosen && !bareWord(d.chosen) && (
        <p className="dp-choice-one"><span className="muted small">{t("decide.choice.on_approve")}</span> <b>{d.chosen.label}</b>
          {d.chosen.summary && <span className="dp-choice-s"><Markdown text={d.chosen.summary} projectId={pid} /></span>}</p>
      )}
      {d.radios && (
        <fieldset className="choices">
          <legend>{d.question ? t("decide.choice.pick_answer") : t("decide.choice.pick")}</legend>
          {d.choices.map((o) => (
            <label key={o.key} className={`choice dp-choice ${d.option === o.key ? "on" : ""}`}>
              <input type="radio" name={`${d.uid}-opt`} checked={d.option === o.key} onChange={() => d.setOption(o.key)} />
              <span className="choice-t">
                <span className="dp-choice-l">{o.label}</span>
                {o.summary && <span className="dp-choice-s"><Markdown text={o.summary} projectId={pid} /></span>}
                {o.risks.length > 0 && <span className="dp-choice-r">{t("decide.choice.risks", { risks: o.risks.join(" · ") })}</span>}
                {o.plan_line && <span className="dp-choice-s">{t("decide.choice.plan_line", { line: o.plan_line })}</span>}
              </span>
              <span className="row tight dp-choice-tags">
                {o.recommended && <Badge value={t("decide.choice.recommended")} tone="info" raw />}
                {!oneVerdict && o.verdict && o.label !== decisionLabel(o.verdict) && <Badge value={decisionLabel(o.verdict)} tone={VERDICT_TONE[o.verdict]} raw />}
              </span>
            </label>
          ))}
          {d.question && (
            <label className={`choice ${d.option === CUSTOM ? "on" : ""}`}>
              <input type="radio" name={`${d.uid}-opt`} checked={d.option === CUSTOM} onChange={() => d.setOption(CUSTOM)} />
              <span className="choice-t">{t("decide.choice.write_own")}</span>
              <span />
            </label>
          )}
        </fieldset>
      )}
      {d.question && (d.option === CUSTOM || d.choices.length === 0) && (
        <div className="field">
          <label htmlFor={`${d.uid}-answer`}>{t("decide.choice.answer")}</label>
          <textarea id={`${d.uid}-answer`} rows={3} value={d.answer} onChange={(e) => d.setAnswer(e.target.value)} />
        </div>
      )}
    </section>
  );
}

// Decline and Hold close the card, and reopening it is not always possible, so their first click arms them; Approve and
// Answer act at once.
const ARMED: Partial<Record<Decision, PlainKey>> = { reject: "decide.arm.reject", defer: "decide.arm.defer" };

function Actions({ r, d }: { r: Row; d: DecisionState }) {
  const { verdict, busy, blocked, act, choices, recommended, recDropped } = d;
  const busyOr = (x: Decision, text: string) => (busy === x ? t("common.working") : text);
  const [armed, setArmed] = useState<Decision | null>(null);
  const pressed = useRef<Decision | null>(null);
  const buttonsRef = useRef<Partial<Record<Decision, HTMLButtonElement | null>>>({});
  const safe = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const armedOnce = useRef(false);
  useEffect(() => {
    if (armed) {
      armedOnce.current = true;
      safe.current?.focus();
    } else if (armedOnce.current && pressed.current) buttonsRef.current[pressed.current]?.focus();
  }, [armed]);
  // a refused decision leaves focus on what the owner pressed, not on the page's start
  useEffect(() => {
    if (!d.error || busy) return;
    (armed ? confirmRef.current : pressed.current && buttonsRef.current[pressed.current])?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when a decision comes back refused, not on arming
  }, [d.error, busy]);
  const press = (x: Decision) => {
    pressed.current = x;
    if (ARMED[x]) setArmed(x);
    else act(x);
  };
  const disarm = () => setArmed(null);
  const offered = new Set(choices.map((c) => c.verdict));
  // The picked option's decision first; hold and decline beside it only when no option already is one.
  let buttons: Decision[] = verdict
    ? [verdict, ...(["defer", "reject"] as Decision[]).filter((x) => x !== verdict && !offered.has(x))]
    : [d.question ? "answer" : "approve", "defer", "reject"];
  // A recommendation that is only a decision word (a bare "Defer") has no option to carry it, so its button leads.
  const recButton = recDropped && recommended?.verdict && buttons.includes(recommended.verdict) ? recommended.verdict : null;
  if (recButton) buttons = [recButton, ...buttons.filter((x) => x !== recButton)];
  // With a note on a screen the decline is what sends the notes: it leads, and reads as that.
  const sendsBack = d.noted > 0 && buttons.includes("reject");
  if (sendsBack) buttons = ["reject", ...buttons.filter((x) => x !== "reject")];
  // Asking for changes on a plan card is a decline that sends the note: it reads as that, never as a plain no.
  const changes = d.asksChanges && buttons.includes("reject");
  const sends = sendsBack || changes;
  const name = (x: Decision) => (x === "reject" && sendsBack ? t("decide.wf.send_back", { n: d.noted })
    : x === "reject" && changes ? t("decide.plan.send_changes")
    : t(x === "approve" && d.edited ? "decide.button.approve_edited" : BUTTON[x]));
  const fixes = r.action_kind === "dev.merge";
  const approveShown = buttons.includes("approve");
  // the typed number is the one thing keeping Approve grey, so the button's own neighbourhood says so
  const waitsOnConfirm = d.typedConfirm && approveShown && d.confirm.trim().toUpperCase() !== rid(r.id);
  // why Approve is grey, read with the button
  const offWhy = [waitsOnConfirm && `${d.uid}-why-confirm`, d.mailMissing && approveShown && `${d.uid}-why-mail`,
    d.specMissing && approveShown && `${d.uid}-why-spec`, d.planMissing && approveShown && `${d.uid}-why-plan`,
    d.screensMissing && approveShown && `${d.uid}-why-screens`,
    sendsBack && `${d.uid}-why-notes`].filter(Boolean).join(" ") || undefined;
  const armWhat = armed === "reject" && sendsBack ? t("decide.wf.send_back_what")
    : armed === "reject" && changes ? t("decide.after.plan_changes") : armed === "reject" ? ifDecline(r, d.note)
    : armed === "defer" ? t(reportOf(r).reopen_later?.defer ? "decide.arm.defer_what_reopen" : "decide.arm.defer_what") : "";
  return (
    <div className="sp-form" aria-busy={!!busy}>
      <span className="sr-only" role="status">{busy ? t("common.working") : ""}</span>
      {d.typedConfirm && approveShown && (
        <div className="field">
          <label htmlFor={`${d.uid}-confirm`}>{d.strong === "unknown_spend"
            ? tx("decide.act.confirm_unknown_spend", { cost: <b>{usd(r.estimated_cost_usd)}</b>, id: <b className="mono">{rid(r.id)}</b> })
            : tx(fixes ? "decide.act.confirm_merge" : "decide.act.confirm", { id: <b className="mono">{rid(r.id)}</b> })}</label>
          <input id={`${d.uid}-confirm`} autoComplete="off" value={d.confirm} onChange={(e) => d.setConfirm(e.target.value)} />
        </div>
      )}
      <div className="field">
        <label htmlFor={`${d.uid}-note`}>
          {t(fixes ? "decide.act.note_fix" : "decide.act.note")}<span className="muted">{t(fixes ? "decide.act.note_fix_hint" : "decide.act.note_hint")}</span>
        </label>
        <textarea id={`${d.uid}-note`} rows={2} value={d.note} onChange={(e) => d.setNote(e.target.value)} />
      </div>
      {d.error && <div className="error" role="alert">{d.error}</div>}
      {waitsOnConfirm && <p id={`${d.uid}-why-confirm`} className="muted small">{t("decide.act.confirm_waiting")}</p>}
      {d.mailMissing && approveShown && <p id={`${d.uid}-why-mail`} className="muted small">{t("decide.email.approve_off")}</p>}
      {d.specMissing && approveShown && <p id={`${d.uid}-why-spec`} className="muted small">{t("decide.spec.approve_off")}</p>}
      {d.planMissing && approveShown && <p id={`${d.uid}-why-plan`} className="muted small">{t("decide.plan.approve_off")}</p>}
      {d.screensMissing && approveShown && <p id={`${d.uid}-why-screens`} className="muted small">{t("decide.wf.approve_off")}</p>}
      {changes && !armed && !d.note.trim() && <p id={`${d.uid}-why-changes`} className="muted small">{t("decide.plan.changes_note")}</p>}
      {sendsBack && !armed && (
        <p id={`${d.uid}-why-notes`} className="muted small">
          {t("decide.wf.notes_off")} <button type="button" className="link" disabled={!!busy} onClick={d.clearScreens}>{t("decide.wf.notes_clear")}</button>
        </p>
      )}
      {armed ? (
        <div className="act-armed dp-armed" role="group" aria-labelledby={`${d.uid}-arm`}
          onKeyDown={(e) => { if (e.key === "Escape" && !busy) { e.preventDefault(); e.stopPropagation(); disarm(); } }}>
          <p id={`${d.uid}-arm`} className="act-what">{armWhat}</p>
          <div className="row wrap sp-actions">
            <button ref={safe} type="button" className="btn-ghost" disabled={!!busy} onClick={disarm}>{t("common.keep")}</button>
            <button ref={confirmRef} type="button" className={armed === "reject" && !sends ? "danger-solid" : "primary"} disabled={blocked(armed)}
              onClick={() => act(armed)}>{busyOr(armed, armed === "reject" && changes ? t("decide.plan.send_changes")
                : t(armed === "reject" && sendsBack ? "decide.wf.send_back_arm" : ARMED[armed]!))}</button>
          </div>
        </div>
      ) : (
        <div className="row wrap sp-actions">
          {buttons.map((x, i) => (
            <button key={x} ref={(el) => { buttonsRef.current[x] = el; }} type="button" disabled={blocked(x)} onClick={() => press(x)}
              aria-describedby={x === "approve" && blocked(x) ? offWhy : x === "defer" && sendsBack ? `${d.uid}-why-notes`
                : x === "reject" && changes && blocked(x) ? `${d.uid}-why-changes` : undefined}
              className={i === 0 ? (x === "reject" && !sends ? "danger-solid" : "primary") : x === "reject" ? "danger" : ""}>
              {busyOr(x, name(x))}
              {x === recButton && <span className="dp-rec-mark">{t("decide.choice.recommended")}</span>}
            </button>
          ))}
        </div>
      )}
      {buttons.includes("defer") && !armed && <p className="muted small">{t("decide.act.defer_hint")}</p>}
    </div>
  );
}

/** What the decision is about when that is not this panel's subject alone: the build a question comes from, the skill
 * a turn-on asks for. The skill opens on the skills page with this panel still open over it. */
function About({ r }: { r: Row }) {
  const p = r.action_params ?? {};
  if (p.dev_job_id) {
    return <p className="small dp-from">{tx("decide.about.build", { build: <RefLink code={`J-${p.dev_job_id}`} projectId={r.project_id} /> })}</p>;
  }
  if (r.type === "SKILL" && p.skill_id) {
    return <p className="small dp-from"><Link to={`/skills?id=${p.skill_id}&req=${r.id}`}>{t("decide.about.skill")}</Link></p>;
  }
  return null;
}

function Body({ r, d, dupes, onRetry }: { r: Row; d: DecisionState | null; dupes: Row[]; onRetry: (() => void) | null }) {
  const open = r.status === "PENDING";
  const projectLink = r.project_id ? <RefLink code={`P-${r.project_id}`} /> : t("common.whole_company");
  const risk = RISK_TONE[r.risk_level];
  const tags = [
    <span key="id" className="mono small dp-id">{rid(r.id)}</span>,
    open && r.blocking && <Badge key="b" value={t("decide.tag.blocking")} tone="warn" raw />,
    open && risk && <Badge key="r" value={label(r.risk_level)} tone={risk} raw />,
    open && strongConfirm(r) && <Badge key="s" value={t("label.status.strong_approve")} tone="info" raw
      title={t(strongConfirm(r) === "unknown_spend" ? "decide.extra_check.why_spend" : "decide.extra_check.why")} />,
    r.type !== "APPROVAL" && <Badge key="t" value={r.type} tone="info" />,
  ].filter(Boolean);
  return (
    <>
      <p className="muted small dp-from">
        {t("decide.body.from", { agent: agentName(r.from_agent) })} · {projectLink} · <Time v={r.created_at} />
        {open && r.expires_at && tx("decide.body.expires", { when: <Time v={r.expires_at} /> })}
        {r.estimated_cost_usd != null && t("decide.body.cost", { cost: `$${r.estimated_cost_usd}` })}
      </p>
      <About r={r} />
      <p className="dp-tags">{tags}</p>
      <WhereSits r={r} onRetry={onRetry} />
      <ReportHead r={r} note={d?.note ?? ""} />
      {open && d && <Recommendation r={r} d={d} />}
      {open && r.dev_budget && (
        <p className="dp-warn" role="note">
          <b>{t("decide.body.budget_short")}</b>
          {t("decide.body.budget_need", { need: `$${r.dev_budget.need_usd}`, left: `$${r.dev_budget.left_usd}` })}
          {tx("decide.body.budget_raise", { link: <Link to={`/projects/${r.project_id}/settings#budget`}>
            {t("decide.body.budget_link", { amount: `$${Math.ceil(r.dev_budget.short_usd)}` })}</Link> })}
          {t("decide.body.budget_else")}
        </p>
      )}
      {open && r.fix_budget && (
        <p className="dp-warn" role="note">
          {tx("decide.fix.budget", { need: `$${r.fix_budget.need_usd}`, left: `$${Number(r.fix_budget.left_usd).toFixed(2)}`,
            link: <Link to={`/projects/${r.project_id}/settings#budget`}>{t("decide.fix.budget_link")}</Link> })}
        </p>
      )}
      <EmailSection r={r} />
      <Walkthrough r={r} asking={open} />
      <MergeReview r={r} note={open && d ? d.note : null} onNote={open && d ? d.setNote : null} />
      <SpecScreens r={r} stories={r.action_params?.stories ?? []} onRetry={onRetry}
        notes={open && d ? d.screens : null} onNote={open && d ? d.setScreen : null} />
      <SpecSummary r={r} />
      <OwnerTrace r={r} />
      <SpecPlan r={r} />
      <SpecText r={r} onRetry={onRetry} />
      <PlanText r={r} onRetry={onRetry} />
      <PostSection r={r} draft={d?.draft ?? null} setDraft={d?.setDraft ?? null} dupes={dupes} />
      <HandoutSection handout={reportOf(r).handout} />
      {open && d && <Choices r={r} d={d} />}
      <Detail r={r} shown={open && d?.recommended ? d.recommended.key : null} />
    </>
  );
}

export function DecisionPanel({ id, next, onClose, onChanged }: {
  id: number; next: NextItem | null; onClose: () => void; onChanged: () => void;
}) {
  const { data, error, failure, reload } = useLive<Row>(`/requests/${id}`, ["agent_requests"]);
  // a merge card is about its build: the server turns this subject into that conversation
  useChatSubject({ kind: "request", ref: String(id) }, "panel");
  // The revision the owner first read here. A later one remounts the decision with nothing picked or typed carried
  // over, and says why, so a choice made on the old text is never sent for the new one.
  const [firstRead, setFirstRead] = useState<{ id: number; revision: number } | null>(null);
  useEffect(() => {
    if (data && firstRead?.id !== data.id) setFirstRead({ id: data.id, revision: data.revision });
  }, [data, firstRead]);
  const revised = !!data && !!firstRead && firstRead.id === data.id && data.revision !== firstRead.revision
    && data.status === "PENDING";
  return (
    <SidePanel item={rid(id)} title={data?.title ?? t("decide.panel.title")} onClose={onClose}>
      {!data ? (
        error ? (
          <div className="dp">
            {failure === "missing" ? (
              <div className="load-error" role="alert">
                <p className="load-error-t">{t("decide.panel.missing", { id: rid(id) })}</p>
                <Link className="btn-ghost small" to="/inbox">{t("decide.panel.to_list")}</Link>
              </div>
            ) : <LoadError error={error} retry={reload} failure={failure} />}
          </div>
        ) : <PanelSkeleton />
      ) : (
        <DecisionBody key={`${data.id}:${data.revision}`} r={data} revised={revised} next={next} onClose={onClose}
          onChanged={onChanged} onRefused={reload} />
      )}
    </SidePanel>
  );
}

type BodyProps = { r: Row; revised: boolean; next: NextItem | null; onClose: () => void; onChanged: () => void; onRefused: () => void };

/** A reopened card starts a new round: nothing picked, typed or pressed for the decision it undid carries over. The
 * server's reopened row stands in until the live one catches up. */
function DecisionBody(props: BodyProps) {
  const [round, setRound] = useState<{ n: number; row: Row | null }>({ n: 0, row: null });
  useEffect(() => setRound((x) => (x.row ? { ...x, row: null } : x)), [props.r]);
  return <DecisionRound key={round.n} {...props} r={round.row ?? props.r} reopened={round.n > 0}
    onReopened={(row) => {
      setRound((x) => ({ n: x.n + 1, row }));
      props.onRefused(); // refetch the card in full: the reopen reply leaves out the spec text a spec card shows
      props.onChanged();
    }} />;
}

function DecisionRound({ r, revised, next, onClose, onChanged, onRefused, reopened, onReopened }: BodyProps & {
  reopened: boolean; onReopened: (row: Row) => void;
}) {
  const [decided, setDecided] = useState<Row | null>(null);
  const top = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (reopened) top.current?.focus();
  }, [reopened]);
  // what the panel said approving does, kept from the moment of the decision: the server stops writing it once decided
  const [promised, setPromised] = useState<string | null>(null);
  // The reply to a decision leaves out what only the detail fetch brings (the spec's text, its screens), so the live
  // card takes over once it shows the same decision.
  const row = decided && !(r.id === decided.id && r.status === decided.status) ? decided : r;
  const open = row.status === "PENDING";
  const card = reportOf(row).post;
  const { items: todos, reload: reloadTodos } = useOpenTodos(!open || !!card);
  const d = useDecision(row, (res) => {
    setPromised(reportOf(row).on_approve ?? null);
    setDecided(res);
    reloadTodos();
    onChanged();
  }, onRefused);
  const dupes = card ? todos.filter((x) => x.request_id !== row.id && x.kind === "post" && x.card?.channel && x.card.channel === card.channel) : [];
  const created = todos.filter((x) => x.request_id === row.id);
  return (
    <div className="dp" onKeyDown={keepOnEscape} ref={top} tabIndex={reopened ? -1 : undefined}>
      {open && revised && <p className="dp-warn" role="alert"><b>{t("decide.revised")}</b></p>}
      {open && reopened && <p className="small" role="status">{t("decide.reopen.done")}</p>}
      {!open && <Receipt r={row} next={next} onNext={() => next?.open()} onClose={onClose} fresh={!!decided} created={created}
        promised={decided ? promised : null} onReopened={onReopened} />}
      <Body r={row} d={open ? d : null} dupes={dupes} onRetry={onRefused} />
      {open && <PanelFooter><Actions r={row} d={d} /></PanelFooter>}
    </div>
  );
}
