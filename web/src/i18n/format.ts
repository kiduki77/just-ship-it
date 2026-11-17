import type { Message } from "./types";

/** `{name}` or `{name|을}`: a value, and the Korean particle that follows it. */
export const PLACEHOLDER = /\{(\w+)(?:\|([^{}]+))?\}/g;

// [with a final consonant, without one]. 으로/로 also takes 로 after ㄹ.
const PARTICLES: [string, string][] = [
  ["을", "를"], ["이", "가"], ["은", "는"], ["과", "와"], ["으로", "로"], ["이에요", "예요"], ["이었어요", "였어요"],
  ["아", "야"], ["이나", "나"], ["이랑", "랑"], ["이라고", "라고"], ["이라", "라"],
];
const PAIR = new Map(PARTICLES.flatMap((pair) => pair.map((form) => [form, pair] as const)));
// The final consonant of each digit read in Korean (영 일 이 삼 사 오 육 칠 팔 구): 0 none, 8 ㄹ, other a consonant.
const DIGIT_FINAL: Record<string, number> = { 0: 21, 1: 8, 2: 0, 3: 16, 4: 0, 5: 0, 6: 1, 7: 8, 8: 8, 9: 0 };
const RIEUL = 8;

function finalOf(word: string): number | null {
  for (let i = word.length - 1; i >= 0; i--) {
    const ch = word[i];
    const code = ch.charCodeAt(0);
    if (code >= 0xac00 && code <= 0xd7a3) return (code - 0xac00) % 28;
    if (ch in DIGIT_FINAL) return DIGIT_FINAL[ch];
    if (/\p{L}/u.test(ch)) return null;
  }
  return null;
}

/** The particle that fits after `word`; both forms ("을(를)") when its last sound cannot be told, as after a Latin letter. */
export function particle(word: string, form: string): string {
  const pair = PAIR.get(form);
  if (!pair) return form;
  const final = finalOf(word);
  if (final === null) return `${pair[0]}(${pair[1]})`;
  if (pair[0] === "으로") return final === 0 || final === RIEUL ? pair[1] : pair[0];
  return final ? pair[0] : pair[1];
}

/** The template a message uses for these values: a plural pair is chosen by `n`. */
export function pick(message: Message, n: unknown): string {
  if (typeof message === "string") return message;
  return Number(n) === 1 ? message.one : message.other;
}
