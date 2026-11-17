import { KeyboardEvent, useCallback, useState } from "react";

/**
 * Escape inside a field leaves the field instead of closing the side panel around it, so a half-written note or
 * answer is never lost to a reflex key. Focus goes to the panel's heading; a second Escape closes the panel.
 */
export function keepOnEscape(e: KeyboardEvent) {
  if (e.key !== "Escape" || e.defaultPrevented) return;
  const el = e.target as HTMLElement;
  if (!el.matches("input, textarea, select")) return;
  e.preventDefault();
  const heading = el.closest(".side-panel")?.querySelector<HTMLElement>("#sp-title");
  if (heading) heading.focus();
  else el.blur();
}

function read<T>(key: string | null, initial: T): T {
  if (!key) return initial;
  try {
    const raw = sessionStorage.getItem(key);
    return raw === null ? initial : { ...initial, ...JSON.parse(raw) };
  } catch {
    return initial;
  }
}

/**
 * What the owner typed into a panel, kept for this tab under `key` until `clear()`, so closing the panel or opening
 * another card and coming back finds it again. A null key keeps nothing. Values are merged over `initial`, so a field
 * added later starts from its default.
 */
export function useDraft<T extends Record<string, unknown>>(key: string | null, initial: T): [T, (patch: Partial<T>) => void, () => void] {
  const [value, setValue] = useState<T>(() => read(key, initial));
  const set = useCallback((patch: Partial<T>) => {
    setValue((prev) => {
      const next = { ...prev, ...patch };
      if (key) {
        try {
          sessionStorage.setItem(key, JSON.stringify(next));
        } catch {
          /* storage off: the draft lives as long as the panel */
        }
      }
      return next;
    });
  }, [key]);
  const clear = useCallback(() => {
    if (!key) return;
    try {
      sessionStorage.removeItem(key);
    } catch {
      /* nothing kept */
    }
  }, [key]);
  return [value, set, clear];
}
