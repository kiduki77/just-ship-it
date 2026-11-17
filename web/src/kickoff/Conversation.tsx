import { ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Doc, DocRows } from "../components/attach";
import { Strength, STRENGTHS, strengthList } from "../components/strengths";
import { ActionButton, Badge, Markdown, Time } from "../components/ui";
import { PlainKey, t } from "../i18n";
import { agentName, turnKindLabel } from "../labels";
import { scrollBehavior } from "./motion";
import { Waiting } from "./Waiting";
import { BusinessInfo, DowngradeReason, isClosing, KickoffView, PatchNote, Stage, Turn } from "./types";

const NEAR_BOTTOM_PX = 120;

/** The answer to the strengths question, as the owner picked it in the console's language. */
function strengthsSaid(choice: string): string {
  if (choice === "unknown") return t("kickoff.strengths.skipped");
  if (choice === "none") return t("settings.about.area.none");
  return strengthList(choice.split(",").filter((a): a is Strength => (STRENGTHS as readonly string[]).includes(a)));
}

function ownerSaid(turn: Turn): string[] {
  if (turn.asks === "strengths") return turn.owner_choice ? [strengthsSaid(turn.owner_choice)] : [];
  const out: string[] = [];
  if (turn.owner_choice === "unknown") out.push(t("kickoff.talk.unknown"));
  else if (turn.owner_choice === "yes") out.push(t("kickoff.talk.yes"));
  else if (turn.owner_choice) out.push(turn.owner_choice);
  if (turn.owner_text) out.push(turn.owner_text);
  return out;
}

const RESTORED: Record<DowngradeReason, PlainKey> = {
  no_quote: "kickoff.talk.reason.no_quote",
  not_backing: "kickoff.talk.reason.not_backing",
  guess_reply: "kickoff.talk.reason.guess_reply",
  chip_elsewhere: "kickoff.talk.reason.chip_elsewhere",
  chip: "kickoff.talk.reason.chip",
};

/** What the company, not the CEO, did to a line the CEO wrote as said by the owner. Older notes carry no reason. */
function restored(n: PatchNote): string {
  return t(n.reason ? RESTORED[n.reason] : "kickoff.talk.reason.other");
}

/** The memo the owner pasted: a few lines until they ask for all of it, and no toggle when it already fits. */
function Memo({ text }: { text: string }) {
  const box = useRef<HTMLParagraphElement>(null);
  const [open, setOpen] = useState(false);
  const [long, setLong] = useState(false);
  useLayoutEffect(() => {
    const el = box.current;
    if (el && !open) setLong(el.scrollHeight > el.clientHeight + 1);
  }, [text, open]);
  return (
    <div className="memo">
      <p className="memo-l small muted">{t("kickoff.talk.memo")}</p>
      <p id="memo-text" ref={box} className={`pre-wrap memo-t ${open ? "" : "clamped"}`}>{text}</p>
      {(long || open) && (
        <button type="button" className="quiet small" aria-expanded={open} aria-controls="memo-text" onClick={() => setOpen(!open)}>
          {open ? t("kickoff.talk.collapse") : t("kickoff.talk.show_all")}
        </button>
      )}
    </div>
  );
}

function Opening({ business, material, docs }: { business: BusinessInfo | null; material: string | null | undefined; docs: Doc[] }) {
  return (
    <article id="turn-0" className="turn" tabIndex={-1}>
      <div className="msg owner">
        <div className="msg-h">
          <span className="strong">{t("kickoff.talk.owner")}</span>
          <span className="muted"> · {t("kickoff.talk.first_words")}</span>
        </div>
        {business ? (
          <p className="pre-wrap"><span className="strong">{business.name}</span>{business.one_liner && <> · {business.one_liner}</>}</p>
        ) : (
          <p className="muted">{t("kickoff.talk.no_business")}</p>
        )}
        {material && <Memo text={material} />}
        <DocRows docs={docs} />
      </div>
    </article>
  );
}

function TurnView({ turn, sectionTitle, jumped, docs }: { turn: Turn; sectionTitle?: string; jumped: boolean; docs: Doc[] }) {
  const said = ownerSaid(turn);
  const lowered = (turn.patch_notes ?? []).filter((n) => n.what === "downgraded" && n.text);
  const kind = isClosing(turn) ? t("kickoff.talk.final_check") : turnKindLabel(turn.kind);
  const asks = turn.asks === "strengths";
  return (
    <article id={asks ? "turn-strengths" : `turn-${turn.n}`} className={`turn ${jumped ? "jumped" : ""}`} tabIndex={-1}>
      <div className="msg ceo">
        <div className="msg-h">
          <span className="strong">{agentName("project_manager")}</span>
          {asks ? (
            <span className="muted"> · {t("kickoff.strengths.head")} · <Time v={turn.created_at} /></span>
          ) : (
            <span className="muted"> · {t("kickoff.talk.turn", { n: turn.n })} · {kind}{sectionTitle && !isClosing(turn) ? ` · ${sectionTitle}` : ""} · <Time v={turn.created_at} /></span>
          )}
        </div>
        <Markdown text={turn.ceo_text} />
        {lowered.length > 0 && (
          <ul className="notes small muted" aria-label={t("kickoff.talk.relabelled")}>
            {lowered.map((n, i) => (
              <li key={i}>{restored(n)} · {n.text}</li>
            ))}
          </ul>
        )}
      </div>
      {turn.answered_at && (said.length > 0 || docs.length > 0) && (
        <div className="msg owner">
          <div className="msg-h">
            <span className="strong">{t("kickoff.talk.owner")}</span>
            <span className="muted"> · <Time v={turn.answered_at} /></span>
          </div>
          {said.map((s, i) => (
            <p key={i} className={`pre-wrap ${i === 0 && turn.owner_choice === "unknown" ? "muted" : ""}`}>{s}</p>
          ))}
          <DocRows docs={docs} />
        </div>
      )}
    </article>
  );
}

const waitKey = (view: KickoffView) => `ship.kickoff.wait.${view.session.id}.${view.session.turns_used}`;

function readWait(view: KickoffView): string | null {
  try {
    return sessionStorage.getItem(waitKey(view));
  } catch {
    return null;
  }
}

/**
 * The moment this screen saw the CEO start, kept for the tab so a reload keeps counting from it. The view carries no
 * such time; a wait that started before the screen opened falls back to the last answer.
 */
function useWaitStart(view: KickoffView, thinking: boolean): string | null {
  const prev = useRef<boolean | null>(null);
  useEffect(() => {
    if (thinking && prev.current === false && !readWait(view)) {
      try {
        sessionStorage.setItem(waitKey(view), new Date().toISOString());
      } catch {
        /* private mode: the last answer's time stands in */
      }
    }
    prev.current = thinking;
  }, [thinking, view]);
  return thinking ? readWait(view) : null;
}

/** When the CEO's last question was answered, or the chat opened: what the CEO has been working on since. */
function thinkingSince(view: KickoffView): string | null {
  const answered = view.turns.map((x) => x.answered_at).filter((x): x is string => !!x).sort();
  if (answered.length) return answered[answered.length - 1];
  // an imported product is read first, and the CEO starts after that at a time the view does not carry
  return view.product ? null : view.session.created_at;
}

/** The CEO's last question, after the closing turn. It is not a turn: the answer box is in the dock below. */
function CustomerQuestion({ text }: { text: string }) {
  return (
    <article id="turn-customer" className="turn" tabIndex={-1}>
      <div className="msg ceo">
        <div className="msg-h">
          <span className="strong">{agentName("project_manager")}</span>
          <span className="muted"> · {t("kickoff.customer.head")}</span>
        </div>
        <p>{text}</p>
      </div>
    </article>
  );
}

function Tail({ view, stage, onResume }: { view: KickoffView; stage: Stage; onResume: () => Promise<unknown> }) {
  const started = useWaitStart(view, stage.kind === "thinking");
  if (view.product?.report_state === "reading" && stage.kind === "thinking")
    return (
      <Waiting title={t("kickoff.product.reading", { folder: view.product.folder })}
        downTitle={t("kickoff.product.reading_down", { folder: view.product.folder })} expect={t("kickoff.product.reading_next")}
        since={view.session.created_at} slowAfter={10 * 60} held={view.product.waiting} />
    );
  if (stage.kind === "thinking")
    return <Waiting title={t("kickoff.talk.thinking")} downTitle={t("kickoff.talk.thinking_down")} expect={t("kickoff.talk.thinking_time")} since={started ?? thinkingSince(view)} slowAfter={90} />;
  if (stage.kind === "usage_wait")
    return (
      <div className="tail" role="status">
        <span className="think" aria-hidden="true" />
        <span>
          <Badge value={t("kickoff.talk.usage_wait")} tone="warn" raw />{" "}
          <span className="strong">{stage.note}</span>
        </span>
      </div>
    );
  if (stage.kind === "paused")
    return (
      <div className="tail paused">
        <span>
          <Badge value={t("kickoff.talk.paused")} tone="warn" raw />{" "}
          <span className="strong">{stage.note ?? t("kickoff.talk.paused_note")}</span>
        </span>
        <ActionButton className="primary" run={onResume}>{t("kickoff.talk.resume")}</ActionButton>
      </div>
    );
  return null;
}

export function Conversation({ view, stage, business, jumped, onResume }: {
  view: KickoffView; stage: Stage; business: BusinessInfo | null; jumped: number | null; onResume: () => Promise<unknown>;
}) {
  const box = useRef<HTMLDivElement>(null);
  const near = useRef(true);
  const [behind, setBehind] = useState(false);
  const titles = new Map((view.brief?.sections ?? []).map((s) => [s.key, s.title]));
  const last = view.turns.length;
  const docs = view.documents ?? [];
  const docsAt = (n: number) => docs.filter((d) => d.after_turn === n);
  const toEnd = () => box.current?.scrollTo({ top: box.current.scrollHeight, behavior: scrollBehavior() });
  const toLatest = () => {
    toEnd();
    document.getElementById("kick-answer")?.focus({ preventScroll: true });
  };
  const track = () => {
    const el = box.current;
    if (!el) return;
    near.current = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;
    if (near.current) setBehind(false);
  };
  useEffect(() => {
    if (near.current) toEnd();
    else setBehind(stage.kind === "owner");
  }, [last, stage.kind, !!view.customer_question]);
  // the answer box grows (chips, an error, files) and the log shrinks: an owner reading the end stays at the end
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      if (near.current) el.scrollTop = el.scrollHeight;
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return (
    <div className="talk-w">
      <div id="kick-log" className="talk" ref={box} role="log" aria-label={t("kickoff.talk.log")} tabIndex={0} onScroll={track}>
        <Opening business={business} material={view.session.material} docs={docsAt(0)} />
        {view.turns.map((t) => (
          <TurnView key={t.n} turn={t} sectionTitle={t.section ? titles.get(t.section) : undefined} jumped={jumped === t.n && !t.asks}
            docs={t.answered_at && !t.asks ? docsAt(t.n) : []} />
        ))}
        <Tail view={view} stage={stage} onResume={onResume} />
        {view.customer_question && <CustomerQuestion text={view.customer_question.text} />}
      </div>
      {behind && <button type="button" className="newq" aria-label={t("kickoff.talk.to_new_question")} onClick={toLatest}>{t("kickoff.talk.new_question")}</button>}
    </div>
  );
}
