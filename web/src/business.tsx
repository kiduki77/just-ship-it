import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { Row, useLive } from "./api";
import { useShowTest } from "./components/ui";
import { t } from "./i18n";
import { Today, TODAY_TABLES } from "./today";

export interface Business {
  id: number;
  name: string;
  one_liner: string;
  project_ids: number[];
  cycle_id: number | null;
  bets_by_status: Record<string, number>;
  current_rung: Row | null;
  brief_version: number | null;
}

/** Said once after an item opened by its address switched the business, or could not. */
export interface SwitchNote {
  text: string;
  back: number | null;
}

interface Selection {
  businesses: Business[] | null;
  error: string | null;
  reload: () => void;
  current: Business | null;
  select: (id: number) => void;
  /** Show the business an item opened by its address belongs to, and say so. */
  /** False while the business is not in the list yet (just created): the list reloads and the caller tries again. */
  follow: (businessId: number, what: string) => boolean;
  note: SwitchNote | null;
  dismissNote: () => void;
  /** The owner's whole list for the business: the one source of every decision and to-do count on screen. */
  today: Today | null;
  todayError: string | null;
  reloadToday: () => void;
}

const STORE = "ship.business";
const readStored = (): number | null => {
  try {
    const n = Number(localStorage.getItem(STORE));
    return Number.isInteger(n) && n > 0 ? n : null;
  } catch {
    return null;
  }
};
const writeStored = (id: number) => {
  try {
    localStorage.setItem(STORE, String(id));
  } catch {
    /* private mode: the choice lasts until the page closes */
  }
};

/** A business's name as the owner reads it: the harness prefix of a test business is not a name. */
export const cleanName = (name: string) => name.replace(/^\[TEST\]\s*/, "");

const Ctx = createContext<Selection | null>(null);

/** The business where the most waits on the owner, read off one business's list and the others it counts
 * (`elsewhere`): decisions that hold up work first, then everything waiting. `own` wins a tie. */
function landingOf(own: number, today: Today): number {
  const decisions = today.items.filter((it) => it.kind === "decision");
  const weigh = (blocking: number, all: number) => blocking * 1000 + all;
  let best = { id: own, w: weigh(decisions.filter((it) => it.tier === "blocking").length, today.items.length) };
  for (const b of today.elsewhere ?? []) {
    const w = weigh(b.blocking ?? 0, b.decisions + b.todos + (b.turns ?? 0));
    if (w > best.w) best = { id: b.business_id, w };
  }
  return best.id;
}

/**
 * The business the console shows. A `?b=` in the address wins (links the server writes carry it), then a business an
 * item followed into for this tab, then the owner's own last pick, then the business where the most waits on the owner
 * (`landingOf`), read once as the console opens. Only the owner's pick is
 * kept across visits: an item opened by its address switches the business for this tab and says so, and the next
 * visit opens on the business the owner chose. The owner's whole today list for it is loaded once here, so the
 * sidebar, the top bar, the home and the to-do page count the same items.
 */
export function BusinessProvider({ children }: { children: ReactNode }) {
  const [showTest] = useShowTest();
  const { data: businesses, error, reload } = useLive<Business[]>(
    `/businesses${showTest ? "?include_test=true" : ""}`, ["businesses", "projects"]);
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { hash } = useLocation();
  // `?b=` changes in place and the hash stays, so a link to a section (`/settings#set-outgoing`) still lands on it
  const setParams = useCallback((next: URLSearchParams) => navigate({ search: `?${next}`, hash }, { replace: true }), [navigate, hash]);
  const [stored, setStored] = useState(readStored);
  const [followed, setFollowed] = useState<number | null>(null);
  const [note, setNote] = useState<SwitchNote | null>(null);
  const asked = Number(params.get("b"));
  // with no address, no item followed and no pick of the owner's, the console opens where the owner's work waits
  const [landed, setLanded] = useState<number | null>(null);
  const landing = landed === null && !asked && followed === null && !businesses?.some((b) => b.id === stored);
  const current = useMemo(
    () => businesses?.find((b) => b.id === asked) ?? businesses?.find((b) => b.id === followed)
      ?? businesses?.find((b) => b.id === stored) ?? businesses?.find((b) => b.id === landed) ?? businesses?.[0] ?? null,
    [businesses, asked, followed, stored, landed]);
  const { data: today, error: todayError, reload: reloadToday } = useLive<Today>(
    current ? `/businesses/${current.id}/today?all=true` : null, TODAY_TABLES);
  useEffect(() => {
    if (!landing || !current) return;
    if (today?.business_id === current.id) setLanded(landingOf(current.id, today));
    else if (todayError) setLanded(current.id);
  }, [landing, current, today, todayError]);

  const inAddress = useCallback((id: number) => {
    const next = new URLSearchParams(params);
    next.set("b", String(id));
    setParams(next);
  }, [params, setParams]);

  // the owner's own pick: kept for the next visit, and in the address so the tab can be shared or reopened on it
  const select = useCallback((id: number) => {
    writeStored(id);
    setStored(id);
    setFollowed(null);
    setNote(null);
    inAddress(id);
  }, [inAddress]);

  // an item's business: this tab follows it and says so, with the way back; nothing is kept for the next visit
  // A business missing from the list may be one created a moment ago: the list is read again once before the console
  // says it is archived or unknown.
  const rechecked = useRef<number | null>(null);
  const follow = useCallback((businessId: number, what: string) => {
    if (!current || businessId === current.id) return true;
    const to = businesses?.find((b) => b.id === businessId);
    if (!to) {
      if (rechecked.current !== businessId) {
        rechecked.current = businessId;
        reload();
        return false;
      }
      setNote({ text: t("shell.switch.elsewhere", { what }), back: null });
      return true;
    }
    setFollowed(to.id);
    inAddress(to.id);
    setNote({ text: t("shell.switch.moved", { name: cleanName(to.name), what }), back: current.id });
    return true;
  }, [businesses, current, inAddress, reload]);
  const dismissNote = useCallback(() => setNote(null), []);

  // a `?b=` that arrived by address counts as followed, so leaving the address keeps the tab on that business. Only a
  // change of the address counts: the owner's own pick changes `stored` a render before the router applies its `?b=`,
  // and the old `?b=` seen in that render must not be adopted.
  const seenAsked = useRef(0);
  useEffect(() => {
    if (!businesses || asked === seenAsked.current) return;
    seenAsked.current = asked;
    if (asked && businesses.some((b) => b.id === asked) && asked !== stored) setFollowed(asked);
  }, [asked, businesses, stored]);
  // a `?b=` for no business in the list (an old link, an archived or test business) says so and shows the fallback
  useEffect(() => {
    if (!businesses || !asked || businesses.some((b) => b.id === asked) || !current) return;
    if (rechecked.current !== asked) {
      rechecked.current = asked;
      reload();
      return;
    }
    setNote({ text: t("shell.switch.unknown", { id: asked, name: cleanName(current.name) }), back: null });
    const next = new URLSearchParams(params);
    next.set("b", String(current.id));
    setParams(next);
  }, [asked, businesses, current, params, setParams, reload]);
  // every address names its business: a sidebar link or a page's own link drops `?b=`, and without it a reload, a
  // copied link or the back button could open another business than the one on screen
  useEffect(() => {
    if (current && !landing && !params.has("b")) inAddress(current.id);
  }, [current, landing, params, inAddress]);

  const value = useMemo(
    () => ({ businesses, error, reload, current, select, follow, note, dismissNote,
      today: today?.business_id === current?.id ? today : null, todayError, reloadToday }),
    [businesses, error, reload, current, select, follow, note, dismissNote, today, todayError, reloadToday]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useBusinessSelection(): Selection {
  const v = useContext(Ctx);
  if (!v) throw new Error("BusinessProvider is missing");
  return v;
}

/** `path` for one business: the server keeps that business's rows and the company's own (services/scope.py). */
export const withBusiness = (path: string, id: number) => `${path}${path.includes("?") ? "&" : "?"}business_id=${id}`;

/**
 * The business of the item whose page this is (a project's page), which its lists show whatever the switcher says. `null`
 * is a company project. Left unset, the selected business decides.
 */
const ItemBusiness = createContext<number | null | undefined>(undefined);
export const ItemScope = ItemBusiness.Provider;

/** `path` for an item's own business: an id names it, and a company project (`null`) reads company-wide. */
export const forItem = (path: string, business: number | null) => (business === null ? path : withBusiness(path, business));

/**
 * Turns a list path into the selected business's, or on an item's page the item's business's. Null while the businesses
 * load and when there is none, so a business screen never lists every business's rows. Use it for a one-off fetch (load
 * more), `useBusinessLive` for a live list.
 */
export function useScoped(): (path: string) => string | null {
  const { current } = useBusinessSelection();
  const item = useContext(ItemBusiness);
  return useCallback((path: string) => {
    if (item !== undefined) return forItem(path, item);
    return current ? withBusiness(path, current.id) : null;
  }, [current, item]);
}

/** A business screen with nothing to show: no business exists and no item's page names one. It shows how to start one. */
export function useNoBusiness(): boolean {
  const { businesses, current } = useBusinessSelection();
  return useContext(ItemBusiness) === undefined && businesses !== null && current === null;
}

/**
 * A live list of the selected business. Every owner screen reads lists through this, so none shows another business.
 * With no business it never loads: check `useNoBusiness` first.
 */
export function useBusinessLive<T = Row>(path: string | null, tables: string[] = []) {
  const scoped = useScoped();
  const { businesses, error, reload } = useBusinessSelection();
  const live = useLive<T>(path === null ? null : scoped(path), tables);
  // the list of businesses failed: the screen says so instead of waiting for a request that never goes out
  if (path !== null && businesses === null && error) return { data: null, error, failure: "server" as const, reload };
  return live;
}

/** A list across every business, for a screen that says it shows the whole company. */
export function useCompanyLive<T = Row>(path: string | null, tables: string[] = []) {
  return useLive<T>(path, tables);
}

/**
 * An item opened by its address (a link, a notification, search) shows under its own business: once the item names its
 * business, the selection switches to it and a note names the item by its title. `item` only tells one item from
 * another. A company-wide item (`null`) leaves the selection alone.
 */
export function useFollowBusiness(businessId: number | null | undefined, item: string, title: string) {
  const { current, follow } = useBusinessSelection();
  const followed = useRef<string | null>(null);
  useEffect(() => {
    // once per item, so a later pick in the switcher is not undone while the item stays open; a business not in the
    // list yet (just created) is tried again when the list comes back
    if (businessId === undefined || !current || followed.current === item) return;
    if (businessId === null || follow(businessId, title)) followed.current = item;
  }, [businessId, current, follow, item, title]);
}

const NOTE_MS = 12000;

/** The note `follow` leaves, with a way back to the business the owner was in. It goes away on its own after a while,
 * like the other notices, but never while the pointer or focus is on it. */
export function SwitchNoteToast() {
  const { note, dismissNote, select, businesses } = useBusinessSelection();
  const [held, setHeld] = useState(false);
  useEffect(() => {
    if (!note || held) return;
    const id = window.setTimeout(dismissNote, NOTE_MS);
    return () => window.clearTimeout(id);
  }, [note, held, dismissNote]);
  useEffect(() => setHeld(false), [note]);
  if (!note) return null;
  const back = note.back !== null ? businesses?.find((b) => b.id === note.back) : undefined;
  return (
    <div className="toast warn" onMouseEnter={() => setHeld(true)} onMouseLeave={() => setHeld(false)}
      onFocus={() => setHeld(true)} onBlur={(e) => !e.currentTarget.contains(e.relatedTarget as Node) && setHeld(false)}>
      <span className="dot warn" aria-hidden="true" />
      <span>{note.text}</span>
      <span className="toast-a">
        {back && (
          <button className="link small" aria-label={t("shell.switch.back_to", { name: cleanName(back.name) })} onClick={() => select(back.id)}>
            {t("shell.switch.back")}
          </button>
        )}
        <button className="link small" aria-label={t("shell.toast.close")} onClick={dismissNote}>{t("common.close")}</button>
      </span>
    </div>
  );
}

