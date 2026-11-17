/**
 * Display words for the codes the server sends (statuses, agents, activity verbs), from the `label` catalog
 * (i18n/areas/label.ts). The raw code stays available as a tooltip, so the owner can still match what they see to
 * logs and API output. An unknown code is shown as it is.
 */
import { keys, lookup, lookupIn, t } from "./i18n";

const word = (group: string, code: unknown) => lookup(`label.${group}.${String(code ?? "")}`);

/** The label for a status or other system code; unknown codes are shown as-is. */
export function label(code: unknown): string {
  const k = String(code ?? "");
  return word("status", k) ?? k;
}

export const verdictLabel = (task: { verdict?: unknown; outcome?: unknown }) => task.verdict !== "unchecked" ? label(task.verdict)
  : task.outcome === "partial" ? `${t("work-tasks.verdict.unchecked")} · ${label("partial")}` : t("work-tasks.verdict.unchecked");

/** The id rows written before the Project Manager was renamed still hold (backend `agents/roles.py`), alone or as
 * the start of a stored responder such as `<id> (decision rule)`. */
const FORMER_PROJECT_MANAGER = "ceo";

/** An agent id read from a row, as it is called today. */
export function currentAgent(id: unknown): string {
  const k = String(id ?? "");
  return k === FORMER_PROJECT_MANAGER || k.startsWith(`${FORMER_PROJECT_MANAGER} (`)
    ? "project_manager" + k.slice(FORMER_PROJECT_MANAGER.length) : k;
}

/**
 * The one name for each agent. The sidebar, breadcrumbs, page titles, tables and the feed all read it, so an agent
 * never goes by two names on two screens. The server's own title stays a subtitle.
 */
export function agentName(id: unknown): string {
  const k = currentAgent(id);
  return word("agent", k) ?? spokenId(k);
}

/** An id the catalog doesn't know (a plan can name any role), said as a name: "qa" as "QA", "growth_lead" as "Growth lead". */
function spokenId(k: string): string {
  if (!/^[a-z][a-z0-9_-]*$/.test(k)) return k;
  if (k.length <= 3) return k.toUpperCase();
  const words = k.replace(/[_-]+/g, " ");
  return words[0].toUpperCase() + words.slice(1);
}

/** An agent row's display name: the catalog's name, or the server's title for an agent the catalog does not know. */
export const agentTitle = (a: { id?: unknown; title?: unknown }): string =>
  word("agent", currentAgent(a.id)) ?? String(a.title ?? a.id ?? "");

/** Label for an owner decision value (`approve` also names a policy mode, so it is keyed separately). */
export function decisionLabel(code: unknown): string {
  return code === "approve" ? t("label.status.approve_decision") : label(code);
}

/** Where a brief line came from. Not the evidence vocabulary: these four say who stood behind the line. */
export const provenanceLabel = (v: unknown) => word("provenance", v) ?? String(v ?? "");
export const turnKindLabel = (v: unknown) => word("turnKind", v) ?? String(v ?? "");

/**
 * Korean agent prose with the system's English words in the owner's: a verdict ("CONDITIONAL-GO"), an agent id or a
 * review word standing next to Korean ("research와 risk는", "(담당 research)"), and a code glossed after its Korean
 * word ("이견(contested)"). The words are Korean whatever the console's language, since the prose around them is.
 * A word inside English text, a path or a link is left alone, and so is every line with no Korean in it: one Korean
 * quote in an English document glossed "Review: risk → unmet" into "리스크·감사" on an English screen.
 */
const HANGUL = "\\uac00-\\ud7a3";
const PROSE_KEYS: Record<string, string> = {
  ...Object.fromEntries(keys("label.agent.").map((k) => [k.slice("label.agent.".length), k])),
  contested: "label.prose.contested", uncontested: "label.prose.uncontested", rework: "label.status.rework",
  escalate: "label.status.escalate", unreviewed: "label.status.unreviewed",
};
const PROSE_CODES = Object.keys(PROSE_KEYS).filter((k) => /^[a-z_]+$/.test(k) && !["owner", "system", "policy"].includes(k))
  .sort((a, b) => b.length - a.length).join("|");
const GLOSSED = new RegExp(`([${HANGUL}])\\((?:${PROSE_CODES})\\)`, "g");
const NEXT_TO_KOREAN = new RegExp(`(^|[\\s(,·${HANGUL}])(${PROSE_CODES})(?=[${HANGUL}),·]|\\s+[${HANGUL}→(]|$)`, "gm");
const VERDICT = /(?<![A-Za-z0-9_./-])(CONDITIONAL[-_]GO|NO[-_]GO)(?![A-Za-z0-9_-])/g;
const IS_KOREAN = new RegExp(`[${HANGUL}]`);

export function prose(text: string): string {
  if (!IS_KOREAN.test(text)) return text;
  return text.split("\n").map((line) => (IS_KOREAN.test(line) ? koreanLine(line) : line)).join("\n");
}

function koreanLine(text: string): string {
  return text
    .replace(VERDICT, (m) => lookupIn("ko", m.startsWith("NO") ? "label.verdict.no_go" : "label.verdict.conditional_go") ?? m)
    .replace(GLOSSED, "$1")
    .replace(NEXT_TO_KOREAN, (_m, before: string, code: string) => before + (lookupIn("ko", PROSE_KEYS[code]) ?? code));
}

/** Activity-log vocabulary: what happened, to what. An unknown verb or entity is an internal code: the caller shows
 * nothing. */
export const verbLabel = (v: unknown) => word("verb", v) ?? "";
export const entityLabel = (v: unknown) => word("entity", v) ?? "";
/** Documents agents write, by kind. */
export const artifactKindLabel = (v: unknown) => word("artifactKind", v) ?? t("label.artifactKind.default");
export const purposeLabel = (v: unknown) => word("purpose", v) ?? String(v ?? "");
export const agentStateLabel = (v: unknown) => word("agentState", v) ?? String(v ?? "");
/** A background job by its kind ("Run project", "Slack notice"); an unknown kind is shown as it is. */
export const jobLabel = (kind: string) => lookup(`company.job.${kind}`) ?? kind;
/** What each routine does, in one owner sentence. Its kind code stays a tooltip. */
export const routineLabel = (kind: unknown) => word("routine", kind) ?? t("label.routine.default");

/** Known engine errors in owner words; anything else is shown as written (the raw text stays a tooltip). */
const ERRORS: [RegExp, (m: RegExpMatchArray) => string][] = [
  [/task budget ([\d.]+) USD-eq exhausted/i, (m) => t("label.error.task_budget", { amount: `$${Number(m[1])}` })],
  [/project budget ([\d.]+) USD-eq would be exceeded/i, (m) => t("label.error.project_budget", { amount: `$${Number(m[1])}` })],
  [/month(ly)? (company )?budget .*exceeded|month cap/i, () => t("label.error.month_budget")],
];
export function errorText(raw: unknown): string {
  const text = String(raw ?? "");
  for (const [re, say] of ERRORS) {
    const m = text.match(re);
    if (m) return say(m);
  }
  return text;
}
