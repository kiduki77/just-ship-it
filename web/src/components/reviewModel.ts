/**
 * The merge card's review, as plain data (Walkthrough.tsx and MergeReview.tsx draw it): the checks the product agent
 * made, what to doubt before approving, the changed files by kind, and the decline note the owner's ticks write. The server sends what
 * the acceptance check recorded (engineering/merge_review.py); nothing here reads findings out of free text.
 */
import type { Checked, Walkthrough } from "../api";

export interface Need { name: string; what: string; without: string; required: boolean }
export interface UseSteps { folder: string; branch: string; steps: string[]; needs: Need[]; why: string | null }
export interface Scenario { given: string; when: string; then: string }
export interface Criterion { criterion: string; met: boolean; evidence: string; scenario: Scenario | null }
export type GapKind = "unchecked" | "built_partly" | "built_no" | "beyond";
export interface Gap { kind: GapKind; story: string | null; priority: "P1" | "P2" | "P3" | null; title: string; detail: string }
export interface Task { n: number; title: string; detail: string; done: boolean }
export interface Review {
  use: UseSteps | null;
  criteria: Criterion[];
  gaps: Gap[];
  findings: string[];
  items: { done: number; total: number; tasks: Task[] } | null;
  commits_ahead: number | null;
  /** The run of the user journeys on this build; null for a build accepted before builds recorded them. */
  walkthrough: Walkthrough | null;
  checked: Checked;
}

/** A check in a few words: what the owner does in a scenario, else the criterion itself. */
export const checkLabel = (c: Criterion) => c.scenario?.when ?? c.criterion;

export type Severity = "high" | "medium" | "low";
export type DoubtKind = "unmet" | GapKind | "finding" | "unseen" | "sample" | WalkDoubt;
/** A walkthrough step nobody could run (it needs something the sandbox never gets, or there was no sandbox), or one
 * the product agent says is wrong itself. No fix round can repair these, so they are doubts, not failed checks. */
export type WalkDoubt = "needs" | "no_docker" | "scenario";

/** One thing to doubt before approving. `detail` is the reason in the record's own words, shown under its kind's. */
export interface Doubt { id: string; kind: DoubtKind; severity: Severity; text: string; detail: string }

const P1_FIRST: Record<"built_no" | "built_partly" | "unchecked", [Severity, Severity]> = {
  built_no: ["high", "medium"], built_partly: ["medium", "low"], unchecked: ["medium", "low"],
};

function gapSeverity(g: Gap): Severity {
  if (g.kind === "beyond") return "medium";
  const [p1, other] = P1_FIRST[g.kind];
  return g.priority === "P1" ? p1 : other;
}

const ORDER: Record<Severity, number> = { high: 0, medium: 1, low: 2 };

/** The walkthrough steps worth a doubt, as the journeys list them: `detail` is the need a step lacked, or what the
 * product agent saw when it called the step wrong. */
function walkDoubts(review: Review): Doubt[] {
  const out: Doubt[] = [];
  for (const j of review.walkthrough?.journeys ?? []) {
    for (const s of j.steps) {
      const doubt = (kind: WalkDoubt, detail: string) => out.push({ id: `w${s.id}`, kind, severity: "medium", text: `${j.title}: ${s.do}`, detail });
      if (s.state === "not_run" && (s.not_run === "needs" || s.not_run === "no_docker")) doubt(s.not_run, s.need ?? "");
      else if (s.state === "differs" && s.fault === "scenario") doubt("scenario", s.looks_like);
    }
  }
  return out;
}

/**
 * Everything worth doubting, worst first: each unmet check, where the build and the spec disagree, the screens'
 * shortfalls, the walkthrough steps nobody could run or that are wrong themselves, the screens nobody saw, and
 * captures made on sample data.
 */
export function doubtsOf(review: Review, unseen: string[], sample: string | null): Doubt[] {
  const out: Doubt[] = [];
  review.criteria.forEach((c, i) => {
    if (!c.met) out.push({ id: `c${i}`, kind: "unmet", severity: "high", text: checkLabel(c), detail: c.evidence });
  });
  review.gaps.forEach((g, i) => out.push({ id: `g${i}`, kind: g.kind, severity: gapSeverity(g),
    text: g.kind === "beyond" ? g.detail : g.title, detail: g.kind === "beyond" ? "" : g.detail }));
  review.findings.forEach((f, i) => out.push({ id: `f${i}`, kind: "finding", severity: "medium", text: f, detail: "" }));
  out.push(...walkDoubts(review));
  unseen.forEach((u, i) => out.push({ id: `u${i}`, kind: "unseen", severity: "medium", text: u, detail: "" }));
  if (sample) out.push({ id: "s", kind: "sample", severity: "low", text: sample, detail: "" });
  return out.map((d, i) => ({ d, i })).sort((a, b) => ORDER[a.d.severity] - ORDER[b.d.severity] || a.i - b.i).map((x) => x.d);
}

/** The decline note the owner's checked doubts write: the lead line, then one line each. Empty when none is checked. */
export const noteOf = (lead: string, lines: string[]) => (lines.length ? [lead, ...lines.map((x) => `- ${x}`)].join("\n") : "");

/**
 * What the note becomes when the checked doubts change. The card keeps writing it while it is the text it last wrote
 * (or empty); once the owner edits it by hand, it is the owner's and stays as it is.
 */
export function syncNote(note: string, written: string | null, next: string): { note: string; written: string | null } {
  return note === (written ?? "") || !note.trim() ? { note: next, written: next } : { note, written };
}

export type FileKind = "source" | "tests" | "docs" | "config" | "other";
export interface ChangedFile { path: string; lines: string }
export interface FileGroup { kind: FileKind; files: ChangedFile[] }

// First match wins: a test's sample data is a test, and a requirements.txt is config, not a document.
const KINDS: [FileKind, RegExp][] = [
  ["tests", /(^|\/)(tests?|__tests__|spec|fixtures?)\/|\.(test|spec)\.[^/]+$|(^|\/)test_[^/]+$/i],
  ["config", /(^|\/)(\.github|\.vscode|\.devcontainer)\/|(^|\/)(package(-lock)?\.json|pnpm-lock\.yaml|yarn\.lock|tsconfig[^/]*\.json|pyproject\.toml|uv\.lock|requirements[^/]*\.txt|Dockerfile|docker-compose[^/]*|Makefile|\.gitignore|\.gitattributes|\.editorconfig|\.npmrc|\.env\.example)$|\.(ya?ml|toml|ini|cfg)$|(^|\/)[^/]+\.config\.[^/]+$/i],
  ["docs", /(^|\/)docs?\/|\.(md|mdx|rst|txt)$|(^|\/)(LICENSE|NOTICE|CHANGELOG)[^/]*$/i],
  ["source", /\.(ts|tsx|js|jsx|mjs|cjs|py|go|rs|rb|java|kt|swift|c|cc|cpp|h|cs|php|sh|ps1|css|scss|html|vue|svelte|sql)$/i],
];
const GROUPS: FileKind[] = ["source", "tests", "docs", "config", "other"];

export const kindOf = (path: string): FileKind => KINDS.find(([, re]) => re.test(path))?.[0] ?? "other";

/** `git diff --stat` lines ("path | 12 +++--") by kind, in a fixed order, without its closing count line. */
export function fileGroups(stat: string): FileGroup[] {
  const files = stat.split("\n").map((line) => /^\s*(.+?)\s+\|\s+(.+?)\s*$/.exec(line)).filter((m) => m !== null)
    .map((m) => ({ path: m[1], lines: m[2] }));
  return GROUPS.map((kind) => ({ kind, files: files.filter((f) => kindOf(f.path) === kind) })).filter((g) => g.files.length > 0);
}

/** The stat's own count of changed files, else the number of lines it lists (a stat cut short at its cap). */
export function filesChanged(stat: string, groups: FileGroup[]): number {
  const said = /^\s*(\d+) files? changed/m.exec(stat);
  return said ? Number(said[1]) : groups.reduce((n, g) => n + g.files.length, 0);
}
