import { createContext, ReactNode, useContext, useEffect, useId, useMemo, useRef, useState, useCallback } from "react";
import { useLocation } from "react-router-dom";
import { subjectForPath } from "./routes";

/** The screens a conversation can be about. Mirrors `chat_tags.PAGES` on the server (tests/test_chat_routes.py checks it). */
export const PAGES = [
  "home", "my_tasks", "business", "new_business", "kickoff", "inbox", "tasks", "projects", "agents", "audit",
  "crews", "engineering", "skills", "routines", "settings",
] as const;
export type PageRef = (typeof PAGES)[number];

/** What a conversation is about: the server's subject kinds (`chat_tags.TagKind`). General needs no ref, a page's ref is one of PAGES. */
export type Subject =
  | { kind: "general" }
  | { kind: "page"; ref: PageRef }
  | { kind: "business" | "project" | "task" | "request" | "owner_task" | "plan" | "spec" | "dev_job"; ref: string };

export const GENERAL: Subject = { kind: "general" };

export const subjectKey = (s: Subject): string => (s.kind === "general" ? "general" : `${s.kind}:${s.ref}`);

/** A subject as the server wrote it back (`{kind, ref}`), typed. */
export const asSubject = (s: { kind: string; ref: string }): Subject => (s.kind === "general" ? GENERAL : (s as Subject));

/** "page" is what the screen is about; "panel" is what is open over it, and wins. */
export type Layer = "page" | "panel";

interface Entry {
  id: string;
  layer: Layer;
  subject: Subject;
}

interface SubjectCtx {
  current: Subject;
  set: (id: string, layer: Layer, subject: Subject | null) => void;
}

const Ctx = createContext<SubjectCtx | null>(null);

/**
 * The subject of whatever the owner is looking at. A screen that says nothing has its route's default (routes.ts);
 * one that registers a subject (`useChatSubject`) replaces it. A stack: a panel beats the page, the newest in a
 * layer wins, and a registration ends when its component unmounts.
 */
export function ChatSubjectProvider({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  const [entries, setEntries] = useState<Entry[]>([]);
  const set = useCallback((id: string, layer: Layer, subject: Subject | null) => setEntries((cur) => {
    const at = cur.findIndex((e) => e.id === id);
    if (!subject) return at < 0 ? cur : cur.filter((e) => e.id !== id);
    if (at < 0) return [...cur, { id, layer, subject }];
    if (cur[at].layer === layer && subjectKey(cur[at].subject) === subjectKey(subject)) return cur;
    return cur.map((e, i) => (i === at ? { id, layer, subject } : e));
  }), []);
  const current = useMemo(() => {
    const newest = (layer: Layer) => [...entries].reverse().find((e) => e.layer === layer);
    return (newest("panel") ?? newest("page"))?.subject ?? subjectForPath(pathname);
  }, [entries, pathname]);
  const value = useMemo(() => ({ current, set }), [current, set]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

function useCtx(): SubjectCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("ChatSubjectProvider is missing");
  return v;
}

/** The subject the chat dock is about right now. */
export function useCurrentSubject(): Subject {
  return useCtx().current;
}

/** Say what this screen, or this panel, is about. `null` says nothing, for a screen that has not loaded its item yet. */
export function useChatSubject(subject: Subject | null, layer: Layer): void {
  const { set } = useCtx();
  const id = useId();
  const latest = useRef(subject);
  latest.current = subject;
  const key = subject ? subjectKey(subject) : "";
  useEffect(() => {
    set(id, layer, latest.current);
    return () => set(id, layer, null);
  }, [set, id, layer, key]);
}

/** `useChatSubject` for a component that has early returns before it knows its subject: render this beside the content. */
export function AboutSubject({ subject, layer }: { subject: Subject | null; layer: Layer }) {
  useChatSubject(subject, layer);
  return null;
}
