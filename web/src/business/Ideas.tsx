import { FormEvent, ReactNode, useEffect, useRef, useState } from "react";
import { ExtLink, ItemLink } from "../components/panel";
import { Link, useSearchParams } from "react-router-dom";
import { post } from "../api";
import { useWorker } from "../components/frame";
import { Board, BoardColumn, Box, Chip, Hint, Section, Tone, useView, ViewSwitch } from "../components/kit";
import { ago, announce, refHref } from "../components/ui";
import { locale, lookup, PlainKey, t } from "../i18n";
import { ConfirmBox, Fail, Loaded, TabKey, useDo } from "./parts";
import { Idea, Ideas } from "./types";

const TEXT_MAX = 4000;
const LINKS_MAX = 5;
/** What the owner may turn an idea into, in the order the menu lists them. */
const CHOICES = ["apply", "bet_candidate", "merge", "reshape", "brief_change", "park", "discard"];
/** A disposition's name; discard_proposed is the CEO's, never the owner's choice. */
const dispositionName = (code: string | null | undefined) => (code ? lookup(`kickoff.ideas.choice.${code}`) : undefined);
/** On hold and discard apply the moment the owner picks them; every other choice goes back to the CEO to carry out. */
const appliesNow = (code: string) => code === "park" || code === "discard";
const TONE: Record<string, Tone> = {
  apply: "ok", bet_candidate: "run", merge: "run", reshape: "run", brief_change: "warn", park: "muted", discard_proposed: "warn", discard: "muted",
};

type Href = (t: TabKey, hash?: string) => string;

const host = (l: string) => {
  try {
    return new URL(l).hostname;
  } catch {
    return l;
  }
};

function Dispo({ i }: { i: Idea }) {
  if (i.status === "FAILED") return <Chip tone="bad">{t("kickoff.ideas.status.FAILED")}</Chip>;
  if (i.status !== "TRIAGED" || !i.disposition) return <Chip tone="run">{lookup(`kickoff.ideas.status.${i.status}`) ?? i.status}</Chip>;
  if (i.needs_owner) return <Chip tone="warn">{t("kickoff.ideas.needs_owner")}</Chip>;
  return <Chip tone={TONE[i.disposition] ?? "muted"}>{dispositionName(i.disposition) ?? i.disposition}</Chip>;
}

function Connections({ i, href }: { i: Idea; href: Href }) {
  const made = i.connections.filter((c) => c.kind !== "brief_line");
  const lines = i.connections.filter((c) => c.kind === "brief_line").length;
  if (!made.length && !lines) return null;
  return (
    <p className="biz-sub">
      <span className="k">{t("kickoff.ideas.linked")}</span>{" "}
      {made.map((c) => {
        const to = c.kind === "bet" ? href("bets", `bet-${c.id}`) : c.kind === "task" ? refHref("T", c.id) : c.kind === "request" ? refHref("REQ", c.id) : null;
        const text = c.merged ? t("kickoff.ideas.merged_into", { label: c.label }) : c.label;
        return <span key={`${c.kind}${c.id}`} className="biz-conn">{to ? <ItemLink to={to}>{text}</ItemLink> : text}</span>;
      })}
      {!!lines && <span className="biz-conn"><Link to={href("brief")}>{t("kickoff.ideas.plan_lines", { n: lines })}</Link></span>}
    </p>
  );
}

function Override({ i, done, cancel }: { i: Idea; done: () => void; cancel: () => void }) {
  const d = useDo();
  const worker = useWorker();
  const options = CHOICES.filter((k) => k !== i.disposition);
  const [to, setTo] = useState(options.includes("bet_candidate") ? "bet_candidate" : options[0]);
  const [note, setNote] = useState("");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (await d.run(() => post(`/ideas/${i.id}/override`, { disposition: to, note: note.trim() || null }))) {
      if (worker.down && !appliesNow(to)) announce(t("kickoff.ideas.worker_off"));
      done();
    }
  };
  return (
    <form className="confirm-box" onSubmit={submit} aria-label={t("kickoff.ideas.override")}>
      <div className="field"><label htmlFor={`ov-${i.id}`}>{t("kickoff.ideas.override_q")}</label>
        <select id={`ov-${i.id}`} value={to} onChange={(e) => setTo(e.target.value)}>
          {options.map((k) => <option key={k} value={k}>{t(appliesNow(k) ? "kickoff.ideas.opt_now" : "kickoff.ideas.opt_ceo", { name: dispositionName(k) ?? k })}</option>)}
        </select></div>
      <p className="biz-note-s">
        {t(appliesNow(to) ? "kickoff.ideas.override_now" : "kickoff.ideas.override_ceo")}
      </p>
      <div className="field"><label htmlFor={`ovn-${i.id}`}>{t("kickoff.ideas.why")} <span className="muted">{t("kickoff.ideas.why_hint")}</span></label>
        <textarea id={`ovn-${i.id}`} rows={2} maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} /></div>
      <Fail text={d.error} />
      <div className="row wrap">
        <button className="btn-primary" type="submit" disabled={d.busy}>{d.busy ? t("kickoff.ideas.changing") : t("kickoff.ideas.change")}</button>
        <button className="btn-ghost" type="button" disabled={d.busy} onClick={cancel}>{t("common.cancel")}</button>
      </div>
    </form>
  );
}

function ConfirmDiscard({ i, done, cancel }: { i: Idea; done: () => void; cancel: () => void }) {
  const d = useDo();
  const [note, setNote] = useState("");
  const go = async () => {
    if (await d.run(() => post(`/ideas/${i.id}/discard`, { note: note.trim() || null }))) done();
  };
  return (
    <ConfirmBox confirmLabel={t("kickoff.ideas.discard")} tone="danger" busy={d.busy} error={d.error} onConfirm={go} onCancel={cancel}>
      <p>{t("kickoff.ideas.discard_lead")}</p>
      <div className="field"><label htmlFor={`dn-${i.id}`}>{t("kickoff.ideas.note")} <span className="muted">{t("kickoff.ideas.optional")}</span></label>
        <input id={`dn-${i.id}`} maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} /></div>
    </ConfirmBox>
  );
}

/** One idea with what the CEO did with it, why, what it connected to, and the owner's moves. Works as a row and as a card. */
function IdeaItem({ i, reload, href, as: Tag = "li", className = "" }: {
  i: Idea; reload: () => void; href: Href; as?: "li" | "div"; className?: string;
}) {
  const [mode, setMode] = useState<"discard" | "override" | null>(null);
  const [open, setOpen] = useState(false);
  const retry = useDo();
  const worker = useWorker();
  const long = i.text.length > 180 || i.text.includes("\n");
  const ov = i.owner_override;
  const closed = () => {
    setMode(null);
    reload();
  };
  const actions: ReactNode = !mode && (
    <>
      {i.status === "FAILED" && (
        <button className="btn-ghost" disabled={retry.busy} onClick={() => retry.run(() => post(`/ideas/${i.id}/retry`, {})).then((ok) => {
          if (!ok) return;
          if (worker.down) announce(t("kickoff.ideas.worker_off"));
          reload();
        })}>
          {retry.busy ? t("kickoff.ideas.retrying") : t("common.retry")}
        </button>
      )}
      {i.needs_owner && <button className="btn-primary" onClick={() => setMode("discard")}>{t("kickoff.ideas.confirm_discard")}</button>}
      {i.status === "TRIAGED" && <button className="btn-ghost" onClick={() => setMode("override")}>{t(i.needs_owner ? "kickoff.ideas.other_choice" : "kickoff.ideas.change_choice")}</button>}
    </>
  );
  const hasActions = i.status === "FAILED" || i.needs_owner || i.status === "TRIAGED";
  return (
    <Tag className={`biz-r ${className}`} id={`idea-${i.id}`}>
      <div className="biz-r-h">
        <p className="biz-r-t"><Dispo i={i} /><span className="biz-sub-i">{ago(i.created_at)}</span></p>
      </div>
      <p className={`biz-idea ${open ? "" : "clamp3"}`}>{i.text}</p>
      {long && <button className="link small biz-more" aria-expanded={open} onClick={() => setOpen(!open)}>{t(open ? "kickoff.ideas.less" : "kickoff.ideas.more")}</button>}
      {i.status === "TRIAGED" && i.reason && <p className="biz-sub"><span className="k">{t("kickoff.ideas.reason")}</span> {i.reason}</p>}
      {(i.status !== "TRIAGED" || !i.reason) && i.note && <p className="biz-sub">{i.note}</p>}
      {ov && i.status === "TRIAGED" && (
        <p className="biz-sub"><span className="k">{t("kickoff.ideas.changed_by_owner")}</span> {dispositionName(ov.from) ?? t("kickoff.ideas.before_triage")} → {dispositionName(ov.to) ?? ov.to}{ov.note ? ` · ${ov.note}` : ""}</p>
      )}
      <Connections i={i} href={href} />
      {!!i.links.length && (
        <p className="biz-sub">
          {i.links.map((l) => <span key={l} className="biz-conn"><ExtLink href={l}>{t("kickoff.ideas.external", { host: host(l) })}</ExtLink></span>)}
        </p>
      )}
      <Fail text={retry.error} />
      {hasActions && !mode && <div className="biz-acts">{actions}</div>}
      {mode === "discard" && <ConfirmDiscard i={i} done={closed} cancel={() => setMode(null)} />}
      {mode === "override" && <Override i={i} done={closed} cancel={() => setMode(null)} />}
    </Tag>
  );
}

function Post({ businessId, done }: { businessId: number; done: () => void }) {
  const d = useDo();
  const worker = useWorker();
  const [params] = useSearchParams();
  const ask = params.get("ask");
  const box = useRef<HTMLTextAreaElement>(null);
  const [text, setText] = useState(ask ? t("kickoff.ideas.ask_prefill", { section: ask }) : "");
  // arriving from the plan's "Ask to change": the change is started, the cursor waits at its end
  useEffect(() => {
    if (!ask || !box.current) return;
    box.current.focus();
    box.current.setSelectionRange(box.current.value.length, box.current.value.length);
  }, [ask]);
  const [links, setLinks] = useState("");
  const [sent, setSent] = useState(false);
  const list = links.split("\n").map((l) => l.trim()).filter(Boolean);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSent(false);
    if (await d.run(() => post(`/businesses/${businessId}/ideas`, { text, links: list.length ? list : null }))) {
      setText("");
      setLinks("");
      setSent(true);
      done();
    }
  };
  return (
    <form className="biz-form" onSubmit={submit} aria-label={t("kickoff.ideas.post")}>
      <div className="field"><label htmlFor="idea-text">{t("kickoff.ideas.text")}</label>
        <textarea ref={box} id="idea-text" rows={4} maxLength={TEXT_MAX} required value={text} onChange={(e) => { setText(e.target.value); setSent(false); }}
          placeholder={t("kickoff.ideas.text_ph")} /></div>
      <div className="field"><label htmlFor="idea-links">{t("kickoff.ideas.links")} <span className="muted">{t("kickoff.ideas.links_hint", { n: LINKS_MAX })}</span></label>
        <textarea id="idea-links" rows={2} value={links} onChange={(e) => setLinks(e.target.value)} /></div>
      <Fail text={d.error ?? (list.length > LINKS_MAX ? t("kickoff.ideas.links_over", { n: LINKS_MAX }) : null)} />
      <div className="row wrap">
        <button className="btn-primary" type="submit" disabled={d.busy || !text.trim() || list.length > LINKS_MAX}>{d.busy ? t("kickoff.ideas.posting") : t("kickoff.ideas.submit")}</button>
        <span className="muted small num">{t("kickoff.ideas.count", { length: text.length.toLocaleString(locale()), max: TEXT_MAX.toLocaleString(locale()) })}</span>
        {sent && <span className="act-msg ok" role="status">{t("kickoff.ideas.sent")}{worker.down ? ` ${t("kickoff.ideas.worker_off")}` : ""}</span>}
      </div>
    </form>
  );
}

const rank = (i: Idea) => (i.needs_owner ? 0 : i.status === "FAILED" ? 1 : i.status === "TRIAGED" ? 3 : 2);

/** The four piles an idea sits in, in the order the owner should look at them. */
const PILES: { key: string; label: PlainKey; tone: Tone; empty: PlainKey; has: (i: Idea) => boolean }[] = [
  { key: "owner", label: "kickoff.ideas.pile.owner", tone: "warn", empty: "kickoff.ideas.pile.owner_empty", has: (i) => i.needs_owner },
  { key: "waiting", label: "kickoff.ideas.pile.waiting", tone: "run", empty: "kickoff.ideas.pile.waiting_empty",
    has: (i) => i.status === "NEW" || i.status === "TRIAGING" },
  { key: "failed", label: "kickoff.ideas.pile.failed", tone: "bad", empty: "kickoff.ideas.pile.failed_empty", has: (i) => i.status === "FAILED" },
  { key: "done", label: "kickoff.ideas.pile.done", tone: "ok", empty: "kickoff.ideas.pile.done_empty",
    has: (i) => i.status === "TRIAGED" && !i.needs_owner },
];

function Counts({ c }: { c: Ideas["counts"] }) {
  const rows: [PlainKey, number, Tone?][] = [
    ["kickoff.ideas.pile.waiting", c.waiting], ["kickoff.ideas.counts.discard", c.discard_proposed, c.discard_proposed ? "warn" : undefined],
    ["kickoff.ideas.pile.failed", c.failed, c.failed ? "bad" : undefined], ["kickoff.ideas.counts.week", c.this_week],
    ["kickoff.ideas.counts.total", c.total],
  ];
  return (
    <Box pad={false}>
      <ul className="ilist">
        {rows.map(([l, n, tone]) => (
          <li key={l}><span className="grow">{t(l)}</span><span className={`end biz-end-v ${tone ? `biz-t-${tone}` : ""}`}>{t("kickoff.ideas.counts.n", { n })}</span></li>
        ))}
      </ul>
    </Box>
  );
}

/** The idea form never depends on the list: a list that failed to load still leaves the owner a place to write. */
export function IdeasTab({ businessId, live, href }: {
  businessId: number; live: { data: Ideas | null; error: string | null; reload: () => void }; href: Href;
}) {
  const { data, error, reload } = live;
  return (
    <>
      {!!data?.counts.discard_proposed && (
        <p className="biz-callout" role="status">{t("kickoff.ideas.callout", { n: data.counts.discard_proposed })}</p>
      )}
      <div className="cols cols-main">
        <Section title={t("kickoff.ideas.post")}><Box><Post businessId={businessId} done={reload} /></Box></Section>
        <Section title={t("kickoff.ideas.status_section")}>
          <Loaded ready={!!data} error={error} retry={reload} rows={1}>{data && <Counts c={data.counts} />}</Loaded>
        </Section>
      </div>
      <Loaded ready={!!data} error={error} retry={reload}>{data && <IdeaList data={data} reload={reload} href={href} />}</Loaded>
    </>
  );
}

function IdeaList({ data, reload, href }: { data: Ideas; reload: () => void; href: Href }) {
  const [view, setView] = useView("business.ideas", "list");
  const [shown, setShown] = useState(12);
  const items = [...data.ideas].sort((a, b) => rank(a) - rank(b) || b.id - a.id);
  const columns: BoardColumn[] = PILES.map((p) => ({
    key: p.key, label: t(p.label), tone: p.tone, empty: t(p.empty),
    items: items.filter(p.has).map((i) => <IdeaItem key={i.id} i={i} reload={reload} href={href} as="div" className="bcard biz-icard" />),
  }));
  return (
    <>
      <Section title={t("kickoff.ideas.list")} count={items.length}
        actions={items.length ? <ViewSwitch value={view} onChange={setView} /> : undefined}>
        {!items.length ? (
          <Hint title={t("kickoff.ideas.empty_title")}>{t("kickoff.ideas.empty_body")}</Hint>
        ) : view === "board" ? (
          <Board columns={columns} label={t("kickoff.ideas.board")} />
        ) : (
          <Box pad={false}>
            <ul className="biz-rows">{items.slice(0, shown).map((i) => <IdeaItem key={i.id} i={i} reload={reload} href={href} />)}</ul>
            {items.length > shown && (
              <div className="box-foot"><button className="link" onClick={() => setShown(shown + 12)}>{t("common.more", { n: items.length - shown })}</button></div>
            )}
          </Box>
        )}
      </Section>
    </>
  );
}
