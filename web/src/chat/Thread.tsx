import { KeyboardEvent, RefObject, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, post, refreshLive, useLive } from "../api";
import { useWorker } from "../components/frame";
import { Icon } from "../components/icons";
import { Chip } from "../components/kit";
import { ItemLink } from "../components/panel";
import { Loading, Skeleton, Time, when } from "../components/ui";
import { lookup, t } from "../i18n";
import { agentName } from "../labels";
import {
  Agent, CHAT_TABLES, dept, Line, names, newAttempt, Page, readDraft, removed, Replying, Resolved, roleLine, Sent, TASK_TONE, TASK_WORD,
  TaskInfo, writeDraft, Who,
} from "./model";
import type { Asking } from "./Dock";
import { Subject } from "./subject";

/** A long paste folds to this many lines, with the rest one press away. */
const FOLD = 8;

function Text({ text }: { text: string }) {
  const [all, setAll] = useState(false);
  const lines = text.split("\n");
  const long = lines.length > FOLD;
  return (
    <>
      <p className="cm-text">{long && !all ? lines.slice(0, FOLD).join("\n") : text}</p>
      {long && (
        <button type="button" className="link small" aria-expanded={all} onClick={() => setAll(!all)}>
          {all ? t("chat.fewer") : t("chat.more_lines", { n: lines.length })}
        </button>
      )}
    </>
  );
}

function Bubble({ line }: { line: Line }) {
  const mine = line.from.id === "owner";
  return (
    <li className={`cm ${mine ? "is-me" : ""}`}>
      <p className="cm-meta">
        <b className="cm-who">{roleLine(line.from)}</b>
        {mine && line.to.id !== "owner" && <span className="muted">{t("chat.sent_to", { agent: agentName(line.to.id) })}</span>}
        <Time v={line.at} />
      </p>
      <div className="cm-body"><Text text={line.text ?? line.body} /></div>
      {line.recall && <Recalled recall={line.recall} />}
    </li>
  );
}

/** Under a recalled role's answer: it is rebuilt from records, what it rests on, and where the records stop. */
function Recalled({ recall }: { recall: NonNullable<Line["recall"]> }) {
  return (
    <p className="cm-recall">
      <span>{t("chat.recall.rebuilt")}</span>
      {recall.cites.length > 0 && <span className="mono">{t("chat.recall.cites", { ids: recall.cites.join(", ") })}</span>}
      {recall.in_progress && <span>{t("chat.recall.in_progress")}</span>}
    </p>
  );
}

/** A line the server wrote (a status, a handoff, a card filed): quiet, in the owner's language already. */
function Server({ line }: { line: Line }) {
  const card = line.decision?.request_id;
  return (
    <li className="cm-sys">
      <span>{line.text ?? line.body}</span>
      {card != null && <ItemLink to={`/inbox?req=${card}`} className="cm-link">{t("chat.open_decision", { ref: `REQ-${card}` })}</ItemLink>}
      <Time v={line.at} />
    </li>
  );
}

/** Work the owner asked for. Its state is read from the work itself, so it matches the Engineering screen. */
function TaskCard({ line, task }: { line: Line; task: TaskInfo }) {
  const [busy, setBusy] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);
  const s = task.state;
  const word = t(TASK_WORD[s.state]);
  const who = s.state === "done" ? t("chat.task.who_done", { who: names(s.who) })
    : s.state === "stopped" || s.state === "cancelled" ? null : t("chat.task.who", { who: names(s.who) });
  const cancel = async () => {
    setBusy(true);
    setRefused(null);
    try {
      await post(`/chat/tasks/${line.id}/cancel`, {});
      refreshLive(CHAT_TABLES);
    } catch (e) {
      setRefused(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <li className="cm cm-task">
      <p className="cm-meta"><b className="cm-who">{roleLine(line.from)}</b><Time v={line.at} /></p>
      <div className="ct">
        <div className="ct-head">
          <p className="ct-title">{task.title}</p>
          <Chip tone={TASK_TONE[s.state]} dot={s.state !== "running"}>
            {s.state === "running" && <span className="live-dot" aria-hidden="true" />}{word}
          </Chip>
        </div>
        {s.progress && <p className="small muted">{t("chat.task.progress", { done: s.progress.done, total: s.progress.total })}</p>}
        {who && <p className="small muted">{who}</p>}
        {s.state === "stopped" && <p className="small">{t("chat.task.stopped_note")}</p>}
        {task.branch && <p className="small mono ct-branch">{t("chat.subject.branch", { name: task.branch })}</p>}
        {(s.request_id != null || s.state === "received" || s.state === "budget") && (
          <div className="ct-act">
            {s.request_id != null && <ItemLink to={`/inbox?req=${s.request_id}`} className="cm-link">{t("chat.task.open_card", { ref: `REQ-${s.request_id}` })}</ItemLink>}
            {(s.state === "received" || s.state === "budget") && (
              <button type="button" className="small" disabled={busy} onClick={cancel}>{t("chat.task.cancel")}</button>
            )}
          </div>
        )}
        {refused && <p className="small ct-refused" role="alert">{refused}</p>}
      </div>
    </li>
  );
}

function Entry({ line }: { line: Line }) {
  if (line.kind === "task" && line.task) return <TaskCard line={line} task={line.task} />;
  if (line.kind === "text" || line.kind === "comment") return <Bubble line={line} />;
  return <Server line={line} />;
}

const future = (v: string | null) => !!v && new Date(v).getTime() > Date.now();

/** Who is on the way to answering, and why one waits: a pause, a usage cap, or just writing. */
function ReplyState({ replying }: { replying: Replying[] }) {
  return (
    <div className="cm-state" role="status">
      {replying.map((r) => {
        const agent = agentName(r.agent);
        if (r.waits_on) {
          return (
            <p key={r.agent}>
              {t("chat.paused", { agent: agentName(r.waits_on) })}{" "}
              <Link to={`/agents/${r.waits_on}`}>{t("chat.paused_link")}</Link>
            </p>
          );
        }
        if (r.wait_reason && future(r.run_after)) {
          const reason = lookup(`audit.queue.reason.${r.wait_reason}`) ?? r.wait_reason;
          return <p key={r.agent}>{t("chat.waiting_until", { reason, agent, until: when(r.run_after) })}</p>;
        }
        return <p key={r.agent}><span className="chat-dot" aria-hidden="true" />{t("chat.replying", { agent })}</p>;
      })}
    </div>
  );
}

/** The lines of one conversation, oldest first, and the state of the replies on their way. Shown, it is read. */
export function Messages({ threadId, agent, canWork }: { threadId: number | null; agent: string; canWork: boolean }) {
  const { data, error, failure, reload } = useLive<Page>(threadId ? `/chat/threads/${threadId}/messages?limit=50` : null, CHAT_TABLES);
  const page = data && data.thread.id === threadId ? data : null;
  const [older, setOlder] = useState<{ lines: Line[]; more: boolean } | null>(null);
  const [fetching, setFetching] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const anchor = useRef<number | null>(null);
  const lines = useMemo(() => [...(older?.lines ?? []), ...(page?.messages ?? [])], [older, page]);
  const more = older ? older.more : !!page?.more;
  const replying = page?.replying ?? [];

  const loadOlder = async () => {
    const first = lines[0]?.seq;
    if (!first || !threadId || fetching) return;
    setFetching(true);
    try {
      const res = await api<Page>(`/chat/threads/${threadId}/messages?before=${first}&limit=50`);
      const el = scroller.current;
      if (el) anchor.current = el.scrollHeight - el.scrollTop;
      stick.current = false;
      setOlder({ lines: [...res.messages, ...(older?.lines ?? [])], more: res.more });
    } catch {
      /* the button stays; the thread shown is not lost */
    } finally {
      setFetching(false);
    }
  };
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && anchor.current !== null) {
      el.scrollTop = el.scrollHeight - anchor.current;
      anchor.current = null;
    }
  }, [older]);
  useEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [page?.messages.length, replying.length]);

  const read = useRef(0);
  const thread = page?.thread;
  useEffect(() => {
    if (!thread || thread.last_seq <= thread.owner_read_seq || read.current >= thread.last_seq) return;
    read.current = thread.last_seq;
    post(`/chat/threads/${thread.id}/read`, { seq: thread.last_seq }).catch(() => {
      read.current = 0;
    });
  }, [thread]);

  const name = agentName(agent);
  return (
    <div className="chat-scroll" ref={scroller} onScroll={(e) => {
      const el = e.currentTarget;
      stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
    }}>
      {threadId && !page ? (error ? <Loading error={error} failure={failure} retry={reload} /> : <Skeleton rows={3} />) : (
        <>
          {page?.thread.recall && <p className="chat-recall small muted" role="note">{t("chat.recall.thread")}</p>}
          {more && <button type="button" className="link small chat-earlier" disabled={fetching} onClick={loadOlder}>{t("chat.earlier")}</button>}
          {lines.length === 0 ? (
            <p className="chat-empty">{t(canWork ? "chat.empty_work" : "chat.empty", { agent: name })}</p>
          ) : (
            <div role="log" aria-live="polite" aria-relevant="additions" aria-label={t("chat.log")}>
              <ul className="chat-log">{lines.map((l) => <Entry key={l.id} line={l} />)}</ul>
            </div>
          )}
          <ReplyState replying={replying} />
        </>
      )}
    </div>
  );
}

function AgentList({ id, options, active, onPick }: { id: string; options: Who[]; active: number; onPick: (a: Who) => void }) {
  useEffect(() => {
    document.getElementById(`${id}-${active}`)?.scrollIntoView({ block: "nearest" });
  }, [id, active]);
  if (options.length === 0) return <p className="chat-agents chat-agents-none small muted" role="status">{t("chat.agents_none")}</p>;
  return (
    <ul id={id} role="listbox" aria-label={t("chat.agents")} className="chat-agents">
      {options.map((a, i) => (
        <li key={a.id} id={`${id}-${i}`} role="option" aria-selected={i === active} className={i === active ? "on" : ""}
          onMouseDown={(e) => {
            e.preventDefault();
            onPick(a);
          }}>
          <span className="chat-agents-n">{agentName(a.id)}</span>
          <span className="small muted">{dept(a.department)}</span>
        </li>
      ))}
    </ul>
  );
}

/** "To: Engineer · Engineering", a select-only combobox over the team. */
function RecipientPicker({ agents, value, onPick }: { agents: Who[]; value: Agent; onPick: (a: Who) => void }) {
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const at = Math.max(0, agents.findIndex((a) => a.id === value.id));
  const show = () => {
    setActive(at);
    setOpen(true);
  };
  const choose = (a: Who) => {
    setOpen(false);
    onPick(a);
  };
  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    const last = agents.length - 1;
    if (e.key === "Escape" && open) {
      e.preventDefault();
      e.stopPropagation();
      setOpen(false);
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!open) show();
      else setActive((i) => (e.key === "ArrowDown" ? (i >= last ? 0 : i + 1) : i <= 0 ? last : i - 1));
    } else if (open && (e.key === "Home" || e.key === "End")) {
      e.preventDefault();
      setActive(e.key === "Home" ? 0 : last);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (!open) show();
      else if (agents[active]) choose(agents[active]);
    } else if (e.key === "Tab") setOpen(false);
  };
  return (
    <div className="chat-to">
      <button type="button" className="chat-to-btn small" role="combobox" aria-haspopup="listbox" aria-expanded={open}
        aria-controls={open ? listId : undefined} aria-activedescendant={open ? `${listId}-${active}` : undefined}
        aria-label={`${t("chat.to_pick")}: ${roleLine(value)}`} onKeyDown={onKeyDown} onBlur={() => setOpen(false)}
        onClick={(e) => {
          // a press of Enter or Space arrives as a click with no detail: the key handler already did it
          if (e.detail > 0) (open ? setOpen(false) : show());
        }}>
        <span className="muted">{t("chat.to")}:</span> {roleLine(value)}
        <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true"><path d="M4.5 6.5 8 10l3.5-3.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </button>
      {open && <AgentList id={listId} options={agents} active={active} onPick={choose} />}
    </div>
  );
}

const MENTION = /(^|\s)@([^\s@]*)$/;

/**
 * The owner's next line: To, the text, send. Enter sends, Shift+Enter adds a line, and Enter never sends while an input
 * method is composing (a Korean syllable half typed). `@` opens the team. A failed send keeps the text and says so; a
 * retry reuses the attempt's id, so a line that did arrive is not sent twice. Keyed by the conversation by its caller.
 */
export function Composer({ convKey, subject, conv, inputRef, asking, onAsked, onSent }: {
  convKey: string; subject: Subject; conv: Resolved; inputRef: RefObject<HTMLTextAreaElement | null>;
  /** A merge card step the owner asked about, which the next line carries until they remove it. */
  asking: Asking | null; onAsked: () => void; onSent: (r: Sent) => void;
}) {
  const worker = useWorker();
  const { data: agents } = useLive<Who[]>("/chat/agents", ["agents"]);
  const [text, setText] = useState(() => readDraft(convKey));
  const [to, setTo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [scrubbed, setScrubbed] = useState(0);
  const [caret, setCaret] = useState(0);
  const [dismissed, setDismissed] = useState<number | null>(null);
  const [active, setActive] = useState(0);
  const attempt = useRef<string | null>(null);
  const listId = useId();
  const team = agents ?? [];

  const mention = useMemo(() => {
    const m = MENTION.exec(text.slice(0, caret));
    return m ? { start: caret - m[2].length - 1, query: m[2].toLowerCase() } : null;
  }, [text, caret]);
  const open = !!mention && dismissed !== mention.start;
  const options = mention && open ? team.filter((a) => `${agentName(a.id)} ${a.id}`.toLowerCase().includes(mention.query)) : [];
  const at = Math.min(active, Math.max(0, options.length - 1));
  useEffect(() => setActive(0), [mention?.query]);

  const recipient: Agent = team.find((a) => a.id === (to ?? conv.default_agent.id)) ?? conv.default_agent;
  const edit = (v: string) => {
    setText(v);
    writeDraft(convKey, v);
    attempt.current = null;
  };
  // the draft opens the message; words the owner already wrote stay theirs
  useEffect(() => {
    if (asking && !readDraft(convKey).trim()) edit(t("chat.about.draft", { n: asking.chip.n }));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once for each step asked
  }, [asking]);
  const pick = (a: Who) => {
    if (!mention) return;
    const next = text.slice(0, mention.start) + text.slice(caret);
    edit(next);
    setTo(a.id);
    setCaret(mention.start);
    requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.setSelectionRange(mention.start, mention.start);
    });
  };

  const send = async () => {
    const body = text.trim();
    if (!body || busy) return;
    attempt.current ??= newAttempt();
    setBusy(true);
    setFailed(null);
    try {
      const sent = await post<Sent>("/chat/messages", {
        subject: subject.kind === "general" ? null : { kind: conv.subject.kind, ref: conv.subject.ref, business_id: conv.business_id },
        to_agent: to, body, client_id: attempt.current, ...(asking ? { about: asking.about } : {}),
      });
      attempt.current = null;
      onAsked();
      setText("");
      writeDraft(convKey, "");
      setScrubbed(removed(sent.scrubbed));
      onSent(sent);
      refreshLive(CHAT_TABLES);
    } catch (e) {
      setFailed(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    if (mention && open) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (options.length) setActive(e.key === "ArrowDown" ? (at + 1) % options.length : (at + options.length - 1) % options.length);
        return;
      }
      if ((e.key === "Enter" || e.key === "Tab") && options.length) {
        e.preventDefault();
        pick(options[at]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        setDismissed(mention.start);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  };
  const where = (e: { currentTarget: HTMLTextAreaElement }) => setCaret(e.currentTarget.selectionStart);
  const name = agentName(recipient.id);
  const note = worker.down ? t("chat.worker_off", { agent: name }) : worker.stopped ? t("chat.company_stopped") : null;

  return (
    <form className="chat-compose" onSubmit={(e) => {
      e.preventDefault();
      void send();
    }}>
      {failed && (
        <p className="chat-failed small" role="alert">
          <b>{t("chat.not_sent")}</b> {failed}{" "}
          <button type="button" className="link" disabled={busy} onClick={() => void send()}>{t("common.retry")}</button>
        </p>
      )}
      {scrubbed > 0 && <p className="chat-note small" role="status">{t("chat.scrubbed", { n: scrubbed })}</p>}
      {note && <p className="chat-note small muted">{note}</p>}
      {asking && (
        <p className="chat-step small">
          <span className="chat-step-t" title={asking.chip.do}>{t("chat.about.step", asking.chip)}</span>
          <button type="button" className="icon-btn" aria-label={t("chat.about.remove", { n: asking.chip.n })}
            title={t("chat.about.remove", { n: asking.chip.n })} onClick={onAsked}>
            <Icon name="close" />
          </button>
        </p>
      )}
      <RecipientPicker agents={team} value={recipient} onPick={(a) => setTo(a.id)} />
      <div className="chat-input">
        {open && <AgentList id={listId} options={options} active={at} onPick={pick} />}
        <textarea ref={inputRef} value={text} rows={Math.min(8, Math.max(2, text.split("\n").length))}
          placeholder={t("chat.placeholder", { agent: name })} aria-label={t("chat.placeholder", { agent: name })}
          role="combobox" aria-autocomplete="list" aria-haspopup="listbox" aria-expanded={open && options.length > 0}
          aria-controls={open && options.length > 0 ? listId : undefined}
          aria-activedescendant={open && options.length > 0 ? `${listId}-${at}` : undefined}
          onChange={(e) => {
            edit(e.target.value);
            where(e);
          }}
          onKeyDown={onKeyDown} onKeyUp={where} onClick={where} onSelect={where} />
      </div>
      <div className="chat-foot">
        <span className="small muted">{t("chat.hint")}</span>
        <button type="submit" className="primary small" disabled={busy || !text.trim()}>{t("chat.send")}</button>
      </div>
    </form>
  );
}
