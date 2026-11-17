/**
 * The console's words in the owner's language. Every string a screen shows comes from a catalog under `./areas`,
 * looked up with `t(key, vars)` (text) or `tx(key, vars)` (text with elements inside, like a bold number). Keys and
 * the values each message needs are checked by the compiler. See docs/I18N.md.
 *
 * The language is the company's setting on the server (Settings > Language); the last one seen is kept in this
 * browser so the first paint already uses it. Changing it re-renders the whole console from the root (main.tsx keys
 * the app by language), so `t()` is a plain function any helper may call. Never call it at module scope: a constant
 * built there would keep the language the page loaded with. Keep keys at module scope and call `t()` when rendering.
 */
import { Fragment, ReactElement, ReactNode, createElement, isValidElement, useSyncExternalStore } from "react";
import { catalog } from "./catalog";
import { PLACEHOLDER, particle, pick } from "./format";
import type { Lang, Plural } from "./types";

export type { Lang } from "./types";
export const LANGS: readonly Lang[] = ["en", "ko"];
export const DEFAULT_LANG: Lang = "en";
/** Each language's own name, the way a language menu shows it. */
export const LANG_NAMES: Record<Lang, string> = { en: "English", ko: "한국어" };

type Catalog = typeof catalog;
export type MessageKey = keyof Catalog;

type Strings<M> = M extends Plural ? M["one"] | M["other"] : M;
type Name<P> = P extends `${infer N}|${string}` ? N : P;
type Names<S> = S extends `${string}{${infer P}}${infer R}` ? Name<P> | Names<R> : never;
type VarsOf<K extends MessageKey> = Names<Strings<Catalog[K]["en"]> | Strings<Catalog[K]["ko"]>>;
type Args<K extends MessageKey, V> = [VarsOf<K>] extends [never] ? [] : [vars: { [P in VarsOf<K>]: V }];
/** A key whose message takes no values: the type for a table of keys a screen picks from at render time. */
export type PlainKey = { [K in MessageKey]: [VarsOf<K>] extends [never] ? K : never }[MessageKey];

const STORE = "ship.lang";
const isLang = (v: unknown): v is Lang => LANGS.includes(v as Lang);

function stored(): Lang {
  try {
    const v = localStorage.getItem(STORE);
    return isLang(v) ? v : DEFAULT_LANG;
  } catch {
    return DEFAULT_LANG;
  }
}

let current: Lang = stored();
const listeners = new Set<() => void>();
document.documentElement.lang = current;

export function language(): Lang {
  return current;
}

/** Switch the console's language now. The server setting is saved by the caller; this is what the screen shows. */
export function setLanguage(lang: unknown): void {
  if (!isLang(lang) || lang === current) return;
  current = lang;
  document.documentElement.lang = lang;
  try {
    localStorage.setItem(STORE, lang);
  } catch {
    /* private mode: the server setting still applies on the next load */
  }
  listeners.forEach((f) => f());
}

export function useLanguage(): Lang {
  return useSyncExternalStore((f) => (listeners.add(f), () => listeners.delete(f)), language);
}

/** The locale for Intl formatting (dates, numbers, relative times) in the current language. */
export function locale(): string {
  return current === "ko" ? "ko-KR" : "en-US";
}

function textOf(node: unknown): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (isValidElement(node)) return textOf((node as ReactElement<{ children?: unknown }>).props.children);
  return "";
}

function parts(key: string, vars: Record<string, unknown>): unknown[] {
  const template = pick((catalog as Record<string, Record<Lang, Plural | string>>)[key][current], textOf(vars.n));
  const out: unknown[] = [];
  let at = 0;
  for (const m of template.matchAll(PLACEHOLDER)) {
    out.push(template.slice(at, m.index));
    const value = vars[m[1]];
    out.push(value);
    if (m[2]) out.push(particle(textOf(value), m[2]));
    at = m.index! + m[0].length;
  }
  out.push(template.slice(at));
  return out.filter((p) => p !== "");
}

/** The message for `key` in the current language, with its values filled in. */
export function t<K extends MessageKey>(key: K, ...args: Args<K, string | number>): string {
  return parts(key, (args[0] ?? {}) as Record<string, unknown>).map(textOf).join("");
}

/** Like `t`, for a message whose values are elements: "<b>3</b> decisions are waiting". */
export function tx<K extends MessageKey>(key: K, ...args: Args<K, ReactNode>): ReactNode {
  return createElement(Fragment, null, ...parts(key, (args[0] ?? {}) as Record<string, unknown>)
    .map((p, i) => createElement(Fragment, { key: i }, p as ReactNode)));
}

const entries = catalog as Record<string, Record<Lang, Plural | string>>;

/** A plain word whose key is built from a code the server sent (`label.status.${code}`), or undefined when the
 * catalog has none. Only for messages without values. */
export function lookup(key: string): string | undefined {
  return lookupIn(current, key);
}

/** `lookup` in a given language, for words that must match the language of the text around them. */
export function lookupIn(lang: Lang, key: string): string | undefined {
  return key in entries ? pick(entries[key][lang], undefined) : undefined;
}

/** Every key under a prefix, for code that lists a group ("label.agent."). */
export function keys(prefix: string): string[] {
  return Object.keys(entries).filter((k) => k.startsWith(prefix));
}
