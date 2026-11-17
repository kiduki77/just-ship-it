import { Link } from "react-router-dom";
import { Lang, language, lookupIn } from "../i18n";

const HANGUL = /[\uac00-\ud7a3]/g;
const LATIN = /[A-Za-z]/g;
/** Fewer English letters than this, with no Korean, is a name or a word ("Slack", "TaskFlow"), not writing. */
const ENGLISH_FROM = 12;

/**
 * The language the text is written in, or null when it is too short to tell. A Korean syllable carries about two
 * English letters' worth, so Korean wins once its syllables reach half the Latin letters: "Slack 알림봇" is Korean.
 */
export function writtenIn(text: string): Lang | null {
  const hangul = text.match(HANGUL)?.length ?? 0;
  const latin = text.match(LATIN)?.length ?? 0;
  if (hangul >= 2 && hangul * 2 >= latin) return "ko";
  if (hangul === 0 && latin >= ENGLISH_FROM) return "en";
  return null;
}

/**
 * When the owner writes in a language other than the setting's, one quiet line in the language they write in: they
 * may keep writing so, the CEO replies in the setting's language, and where to change it. Nothing when they match.
 */
export function LanguageHint({ id, text }: { id: string; text: string }) {
  const setting = language();
  const typed = writtenIn(text);
  if (!typed || typed === setting) return null;
  return (
    <p id={id} className="small muted lang-hint" lang={typed}>
      {lookupIn(typed, `kickoff.lang.hint.${setting}`)}{" "}
      <Link className="link" to="/settings#set-language">{lookupIn(typed, "kickoff.lang.change")}</Link>
    </p>
  );
}
