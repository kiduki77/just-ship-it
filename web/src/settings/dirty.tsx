import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useBlocker } from "react-router-dom";
import { ConfirmBox } from "../components/ui";
import { t } from "../i18n";

/**
 * Which settings sections hold edits that were not saved. Every section saves on its own, so a change in one is easy to
 * lose while the owner moves on to another; the index, the other save bars and the browser's leave prompt all say so.
 */
interface Dirty {
  /** Section id to its title, for the sections with unsaved edits. */
  sections: ReadonlyMap<string, string>;
  mark: (id: string, title: string) => void;
  clear: (id: string) => void;
}

const Ctx = createContext<Dirty>({ sections: new Map(), mark: () => {}, clear: () => {} });
const SectionCtx = createContext({ id: "", title: "" });

export const useDirty = () => useContext(Ctx);
export const useSectionId = () => useContext(SectionCtx).id;
export const SectionIdProvider = SectionCtx.Provider;

/**
 * A section is unsaved exactly while its inputs differ from what is saved: typing a value back to the saved one clears
 * the mark, and so does a save (the form remounts with the new saved values).
 */
export function useDirtyWhen(changed: boolean) {
  const { id, title } = useContext(SectionCtx);
  const { mark, clear } = useDirty();
  useEffect(() => {
    if (changed) mark(id, title);
    else clear(id);
  }, [changed, id, title, mark, clear]);
  useEffect(() => () => clear(id), [id, clear]);
}

export function DirtyProvider({ children }: { children: ReactNode }) {
  const [sections, setSections] = useState<ReadonlyMap<string, string>>(new Map());
  const mark = useCallback((id: string, title: string) => {
    setSections((prev) => (prev.get(id) === title ? prev : new Map(prev).set(id, title)));
  }, []);
  const clear = useCallback((id: string) => {
    setSections((prev) => {
      if (!prev.has(id)) return prev;
      const next = new Map(prev);
      next.delete(id);
      return next;
    });
  }, []);
  const value = useMemo(() => ({ sections, mark, clear }), [sections, mark, clear]);

  const dirty = sections.size > 0;
  // Leaving the settings screen by any way the router sees (a link, the palette, Back or Forward) waits on the owner.
  // A move inside the screen (its section tabs change only the hash) does not.
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname);
  useEffect(() => {
    if (!dirty) return;
    // closing the tab or reloading is the browser's own question
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  useEffect(() => {
    if (blocker.state === "blocked" && !dirty) blocker.proceed();
  }, [blocker, dirty]);

  const leave = () => {
    setSections(new Map());
    blocker.proceed?.();
  };

  return (
    <Ctx.Provider value={value}>
      {blocker.state === "blocked" && dirty && (
        <div className="set-leave" role="alertdialog" aria-label={t("settings.leave.title")}>
          <ConfirmBox confirmLabel={t("settings.leave.go")} cancelLabel={t("settings.leave.stay")} busy={false} error={null} tone="danger"
            onConfirm={leave} onCancel={() => blocker.reset?.()}>
            <p><b>{t("settings.leave.title")}</b></p>
            <p>{t("settings.leave.body", { sections: [...sections.values()].join(", ") })}</p>
          </ConfirmBox>
        </div>
      )}
      {children}
    </Ctx.Provider>
  );
}
