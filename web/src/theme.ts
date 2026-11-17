import { useEffect, useState } from "react";

/** Light is the default. Dark, or following the OS, is the owner's choice, kept per browser. */
export type ThemePref = "light" | "dark" | "system";

const KEY = "ship.theme";
const subs = new Set<(p: ThemePref) => void>();
const media = typeof window !== "undefined" ? window.matchMedia("(prefers-color-scheme: dark)") : null;

export function readTheme(): ThemePref {
  try {
    const v = localStorage.getItem(KEY);
    return v === "dark" || v === "system" ? v : "light";
  } catch {
    return "light";
  }
}

export function applyTheme(pref: ThemePref = readTheme()) {
  const dark = pref === "dark" || (pref === "system" && !!media?.matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}

export function setTheme(pref: ThemePref) {
  try {
    localStorage.setItem(KEY, pref);
  } catch {
    /* private mode: the choice lasts until the page closes */
  }
  applyTheme(pref);
  subs.forEach((f) => f(pref));
}

media?.addEventListener("change", () => applyTheme());

export function useTheme(): [ThemePref, (p: ThemePref) => void] {
  const [pref, setPref] = useState(readTheme);
  useEffect(() => {
    subs.add(setPref);
    return () => void subs.delete(setPref);
  }, []);
  return [pref, setTheme];
}
