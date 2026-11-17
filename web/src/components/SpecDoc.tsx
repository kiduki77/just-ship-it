import { PlainKey, t } from "../i18n";
import { Markdown } from "./ui";

/** A section is one `##` block; its `###` blocks are the sub-blocks the owner can open one by one. */
interface Block { title: string; body: string }
interface Section extends Block { subs: Block[] }

/** Headings the spec writer emits in English whatever the owner's language is. */
const HEADINGS: Record<string, PlainKey> = {
  "user stories": "decide.spec.h.stories",
  "assumptions": "decide.spec.h.assumptions",
  "the owner's data": "decide.spec.h.owner_data",
};
const heading = (h: string) => {
  const key = HEADINGS[h.trim().toLowerCase()];
  return key ? t(key) : h.trim();
};

// a section longer than this starts folded, so the short ones the owner reads first stay open
const OPEN_BELOW = 1500;
const SUB_OPEN_BELOW = 400;

function split(text: string): { title: string; meta: string; sections: Section[] } {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  let title = "";
  const metaLines: string[] = [];
  const sections: Section[] = [];
  let fence = false;
  let sec: Section | null = null;
  let sub: Block | null = null;
  for (const line of lines) {
    if (/^```/.test(line)) fence = !fence;
    const h2 = !fence && /^## (.+)/.exec(line);
    const h3 = !fence && /^### (.+)/.exec(line);
    const h1 = !fence && !sec && /^# (.+)/.exec(line);
    if (h1) { title = h1[1]; continue; }
    if (h2) { sec = { title: h2[1], body: "", subs: [] }; sections.push(sec); sub = null; continue; }
    if (h3 && sec) { sub = { title: h3[1], body: "" }; sec.subs.push(sub); continue; }
    if (sub) sub.body += `${line}\n`;
    else if (sec) sec.body += `${line}\n`;
    else if (line.trim()) metaLines.push(line.trim());
  }
  return { title, meta: metaLines.join(" "), sections };
}

/** The product language line the writer puts under the title, said in the owner's words. */
const metaLine = (meta: string) => {
  const m = /^Product language:\s*(\S+)/i.exec(meta);
  return m ? t("decide.spec.lang", { lang: m[1] }) : meta;
};

/**
 * The spec the owner approves, read in parts: the sections that are short stay open, a long one (the stories and their
 * test scenarios) is folded with its stories listed by title. The text itself is the spec writer's, untouched.
 */
export function SpecDoc({ text, projectId }: { text: string; projectId: number | null }) {
  const doc = split(text);
  if (!doc.sections.length) return <Markdown text={text} projectId={projectId} readable spec />;
  return (
    <div className="spec-doc">
      {doc.title && <h2 className="spec-title">{doc.title}</h2>}
      {doc.meta && <p className="small muted">{metaLine(doc.meta)}</p>}
      {doc.sections.map((s, i) => {
        const size = s.body.length + s.subs.reduce((n, b) => n + b.body.length, 0);
        return (
          <details key={i} className="spec-sec" open={size < OPEN_BELOW || undefined}>
            <summary>{heading(s.title)}{s.subs.length > 1 && <span className="small muted num"> {s.subs.length}</span>}</summary>
            {s.body.trim() && <Markdown text={s.body} projectId={projectId} readable spec />}
            {s.subs.map((b, k) => (
              <details key={k} className="spec-sub" open={b.body.length < SUB_OPEN_BELOW || undefined}>
                <summary>{heading(b.title)}</summary>
                <Markdown text={b.body} projectId={projectId} readable spec />
              </details>
            ))}
          </details>
        );
      })}
    </div>
  );
}
