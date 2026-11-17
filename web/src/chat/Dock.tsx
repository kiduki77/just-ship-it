import { createContext, KeyboardEvent, ReactNode, RefObject, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { qs, useLive } from "../api";
import { useBusinessSelection } from "../business";
import { Icon } from "../components/icons";
import { Chip } from "../components/kit";
import { Loading, Skeleton, Time } from "../components/ui";
import { t } from "../i18n";
import { agentName } from "../labels";
import { chipsOf, Resolved, Sent, TASK_TONE, TASK_WORD, taskState, ThreadRow, useMedia, useResolved } from "./model";
import { asSubject, GENERAL, Subject, subjectKey, useCurrentSubject } from "./subject";
import { Composer, Messages } from "./Thread";
import "./chat.css";

interface DockState {
  open: boolean;
  toggle: () => void;
  close: () => void;
  /** The conversation the owner chose from the list, until they go back to the screen's own. */
  picked: Subject | null;
  pick: (s: Subject | null) => void;
  /** The merge card step the owner asked about, waiting for their next message in its conversation. */
  asking: Asking | null;
  setAsking: (a: Asking | null) => void;
  threads: ThreadRow[] | null;
  threadsError: string | null;
  reloadThreads: () => void;
  /** Conversations with a reply the owner has not read. */
  unread: number;
  button: RefObject<HTMLButtonElement | null>;
}

/** A merge card's walkthrough step, sent with the owner's next message (`POST /api/chat/messages` `about`). */
export interface StepAbout { kind: "walkthrough_step"; step: string; head: string }

/** A step the owner asked about: `about` goes with the next message sent in the conversation `key`, and `chip` names it. */
export interface Asking { key: string; about: StepAbout; chip: { n: number; do: string } }

const Ctx = createContext<DockState | null>(null);

/** Whether the dock is open and which conversation it shows live in the shell, so they survive going to another screen. */
export function DockProvider({ children }: { children: ReactNode }) {
  const { businesses, current } = useBusinessSelection();
  const [open, setOpen] = useState(false);
  const [picked, pick] = useState<Subject | null>(null);
  const [asking, setAsking] = useState<Asking | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const path = businesses === null ? null : `/chat/threads${qs({ business_id: current?.id })}`;
  const { data, error, reload } = useLive<ThreadRow[]>(path, ["agent_messages", "chat_threads"]);
  const was = useRef(false);
  useEffect(() => {
    // the dock has left the page by now, so the page behind it is no longer held back
    if (was.current && !open) button.current?.focus();
    was.current = open;
  }, [open]);
  const toggle = useCallback(() => setOpen((o) => !o), []);
  const close = useCallback(() => setOpen(false), []);
  const value = useMemo<DockState>(() => ({
    open, toggle, close, picked, pick, asking, setAsking, threads: data, threadsError: error, reloadThreads: reload,
    unread: data?.filter((x) => x.unread).length ?? 0, button,
  }), [open, toggle, close, picked, asking, data, error, reload]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

function useDock(): DockState {
  const v = useContext(Ctx);
  if (!v) throw new Error("DockProvider is missing");
  return v;
}

/** Opens the dock on `subject` with `about` attached to the next message; `chip` is how the composer names it. */
export function useAsk(): (subject: Subject, about: StepAbout, chip: { n: number; do: string }) => void {
  const { pick, setAsking, open, toggle } = useDock();
  return useCallback((subject: Subject, about: StepAbout, chip: { n: number; do: string }) => {
    pick(subject);
    setAsking({ key: subjectKey(subject), about, chip });
    if (!open) toggle();
  }, [pick, setAsking, open, toggle]);
}

/** The Team button of the top bar: opens the dock, and counts the conversations with a new reply. */
export function TeamButton() {
  const { open, toggle, unread, button } = useDock();
  const name = t("chat.launcher");
  return (
    <button ref={button} type="button" className="icon-btn team-btn" aria-expanded={open} aria-controls="team-dock" aria-haspopup="dialog"
      aria-label={unread > 0 ? `${name}, ${t("chat.unread", { n: unread })}` : name} onClick={toggle}>
      <Icon name="chat" />
      <span className="team-btn-t">{name}</span>
      {unread > 0 && <span className="team-btn-n" aria-hidden="true">{unread}</span>}
    </button>
  );
}

function Chips({ facts }: { facts: Resolved["facts"] }) {
  const chips = chipsOf(facts);
  if (!chips.length) return null;
  return (
    <ul className="chat-chips" aria-label={t("chat.about")}>
      {chips.map((c) => <li key={c.key} className="chat-chip" title={c.title}>{c.text}</li>)}
    </ul>
  );
}

/** The conversations: General first, then by the last line. General is always there, written or not. */
function Conversations({ onPick }: { onPick: (s: Subject) => void }) {
  const { threads, threadsError, reloadThreads } = useDock();
  if (!threads) return <div className="chat-scroll">{threadsError ? <Loading error={threadsError} retry={reloadThreads} /> : <Skeleton rows={4} />}</div>;
  const rows = threads.some((x) => x.subject.kind === "general") ? threads : [null, ...threads];
  return (
    <div className="chat-scroll">
      <ul className="chat-list" aria-label={t("chat.list.label")}>
        {rows.map((th) => {
          const subject = th ? asSubject(th.subject) : GENERAL;
          const title = th ? chipsOf(th.facts).slice(0, 2).map((c) => c.text).join(" · ") : t("chat.general");
          const state = th?.open_tasks.map(taskState).find((x) => x !== null) ?? null;
          const last = th?.last;
          return (
            <li key={th?.id ?? "general"}>
              <button type="button" className="cl-row" onClick={() => onPick(subject)}>
                <span className="cl-top">
                  <span className="cl-title">{title}</span>
                  {th?.last_message_at && <span className="small muted"><Time v={th.last_message_at} /></span>}
                </span>
                <span className="cl-bot">
                  <span className="cl-last small muted">
                    {last ? [last.from.id === "system" ? "" : agentName(last.from.id), last.text].filter(Boolean).join(": ") : t("chat.list.none")}
                  </span>
                  {state && <Chip tone={TASK_TONE[state]}>{t(TASK_WORD[state])}</Chip>}
                  {th?.unread && <span className="cl-dot" role="img" aria-label={t("chat.list.unread")} />}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** The dock itself, rendered once by the frame. Closed, it renders nothing and asks the server nothing. */
export function Dock() {
  const { open } = useDock();
  return open ? <DockBody /> : null;
}

const FOCUSABLE = 'a[href], button:not(:disabled), textarea, [tabindex]:not([tabindex="-1"])';

function DockBody() {
  const { close, picked, pick, asking, setAsking } = useDock();
  const screen = useCurrentSubject();
  const { current: business } = useBusinessSelection();
  const businessId = business?.id ?? null;
  const modal = useMedia("(max-width: 900px)");
  const [view, setView] = useState<"thread" | "list">("thread");
  const dock = useRef<HTMLElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  const wanted = picked ?? screen;
  const first = useResolved(wanted, businessId, true);
  const missing = first.failure === "missing" && wanted.kind !== "general";
  const general = useResolved(GENERAL, businessId, missing);
  const conv = missing ? general : first;
  const shown = missing ? GENERAL : wanted;
  const convKey = subjectKey(shown);
  const [sent, setSent] = useState<{ key: string; id: number } | null>(null);
  const threadId = conv.data?.thread_id ?? (sent?.key === convKey ? sent.id : null);

  const focused = useRef(false);
  useEffect(() => {
    if (view !== "thread") focused.current = false;
    else if (conv.data && !focused.current) {
      focused.current = true;
      input.current?.focus({ preventScroll: true });
    }
  }, [view, conv.data]);

  useEffect(() => {
    if (modal) return;
    document.body.classList.add("has-chat");
    return () => document.body.classList.remove("has-chat");
  }, [modal]);
  // a full-screen sheet holds the page still, the way the menu drawer does
  useEffect(() => {
    if (!modal) return;
    const held = Array.from(document.querySelectorAll<HTMLElement>(".shell, .side-panel"));
    held.forEach((el) => (el.inert = true));
    return () => held.forEach((el) => (el.inert = false));
  }, [modal]);

  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    // an open list of names took Escape already (preventDefault); this one is for the dock, and keeps the side panel open
    if (e.key === "Escape" && !e.defaultPrevented && !e.nativeEvent.isComposing) {
      e.preventDefault();
      close();
    } else if ((e.key === "j" || e.key === "k") && !e.ctrlKey && !e.metaKey && !e.altKey) {
      // the page's j and k walk its rows: not while the owner is in the dock
      e.stopPropagation();
    } else if (e.key === "Tab" && modal && dock.current) {
      const nodes = Array.from(dock.current.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((n) => n.offsetParent !== null);
      const [head, tail] = [nodes[0], nodes[nodes.length - 1]];
      if (head && e.shiftKey && document.activeElement === head) {
        e.preventDefault();
        tail.focus();
      } else if (head && !e.shiftKey && document.activeElement === tail) {
        e.preventDefault();
        head.focus();
      }
    }
  };

  const onPick = (s: Subject) => {
    pick(subjectKey(s) === subjectKey(screen) ? null : s);
    setView("thread");
  };
  const listed = view === "list";
  return (
    <>
      {modal && <div className="chat-scrim" onClick={close} aria-hidden="true" />}
      <aside id="team-dock" ref={dock} className={`chat-dock ${modal ? "is-sheet" : ""}`} role="dialog" aria-modal={modal}
        aria-labelledby="chat-title" onKeyDown={onKeyDown}
        onClick={(e) => {
          // on a full-screen sheet a card the owner opens from here must not stay hidden behind it
          if (modal && (e.target as HTMLElement).closest("a[href]")) close();
        }}>
        <header className="chat-head">
          <div className="chat-head-main">
            <h2 id="chat-title">{t("term.team_chat")}</h2>
            {!listed && conv.data && <Chips facts={conv.data.facts} />}
          </div>
          <div className="chat-head-a">
            <button type="button" className="small" onClick={() => setView(listed ? "thread" : "list")}>
              {listed ? t("chat.back") : t("chat.all")}
            </button>
            <button type="button" className="icon-btn" aria-label={t("chat.close")} title={t("chat.close")} onClick={close}>
              <Icon name="close" />
            </button>
          </div>
        </header>
        {listed ? <Conversations onPick={onPick} /> : (
          <>
            {(picked || missing || shown.kind !== "general") && (
              <p className="chat-switch small">
                {missing && <span className="muted">{t("chat.fallback")}</span>}
                {picked && subjectKey(picked) !== subjectKey(screen) && (
                  <button type="button" className="link small" onClick={() => pick(null)}>{t("chat.to_screen")}</button>
                )}
                {shown.kind !== "general" && (
                  <button type="button" className="link small" onClick={() => pick(GENERAL)}>{t("chat.to_general")}</button>
                )}
              </p>
            )}
            {conv.data ? (
              <>
                <Messages key={`${convKey}:${threadId}`} threadId={threadId} agent={conv.data.default_agent.id}
                  canWork={conv.data.work.length > 0} />
                <Composer key={convKey} convKey={convKey} subject={shown} conv={conv.data} inputRef={input}
                  asking={asking?.key === convKey ? asking : null} onAsked={() => setAsking(null)}
                  onSent={(r: Sent) => setSent({ key: convKey, id: r.thread_id })} />
              </>
            ) : <div className="chat-scroll">{conv.error ? <Loading error={conv.error} failure={conv.failure} retry={conv.reload} /> : <Skeleton rows={3} />}</div>}
          </>
        )}
      </aside>
    </>
  );
}
