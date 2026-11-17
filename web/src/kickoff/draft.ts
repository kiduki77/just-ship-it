const key = (sessionId: number) => `kickoff-draft:${sessionId}`;

interface Draft {
  n: number;
  text: string;
}

/** The unsent answer to turn `n`. Browser storage can be missing or full, so a failed read is an empty draft. */
export function loadDraft(sessionId: number, n: number): string {
  try {
    const d: Draft | null = JSON.parse(sessionStorage.getItem(key(sessionId)) ?? "null");
    return d && d.n === n && typeof d.text === "string" ? d.text : "";
  } catch {
    return "";
  }
}

export function saveDraft(sessionId: number, n: number, text: string): void {
  try {
    if (text) sessionStorage.setItem(key(sessionId), JSON.stringify({ n, text } satisfies Draft));
    else sessionStorage.removeItem(key(sessionId));
  } catch {
    /* the draft just won't survive a reload */
  }
}
