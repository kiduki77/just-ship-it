export type Lang = "en" | "ko";

/** An English message that counts something: `one` for n = 1, `other` for the rest. Korean needs no pair. */
export type Plural = { readonly one: string; readonly other: string };
export type Message = string | Plural;

/** One message in every language, side by side, so a key is never added in one language only. */
export type Entry = { readonly [L in Lang]: Message };

/** An area's catalog: every key starts with the area's name, so two areas can never define the same key. */
export type Area<P extends string> = { readonly [K in `${P}.${string}`]: Entry };
