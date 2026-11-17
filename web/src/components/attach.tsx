import { DragEvent, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { locale, t } from "../i18n";
import { Icon } from "./icons";
import { Box, Section } from "./kit";
import "./attach.css";

/** Mirrors `services/doc_extract.py` and `services/documents.py`, so the limits the owner reads are the ones the
 * server applies. */
export const ATTACH = { files: 5, mb: 20, pages: 50, chars: 30_000 } as const;
const ACCEPT = ".pdf,.docx,.pptx,.md,.markdown,.txt";

export type DocKind = "pdf" | "docx" | "pptx" | "md" | "txt";

/** A file the server kept, as `documents.view` sends it. */
export interface Doc {
  id: number;
  business_id: number | null;
  session_id: number | null;
  /** 0: with the owner's first words. n: while turn n was the CEO's latest. null: from the business page. */
  after_turn: number | null;
  name: string;
  kind: DocKind;
  size: number;
  pages: number | null;
  chars: number;
  cut: boolean;
  created_at: string;
  /** Removed by the owner: no longer read, still shown where it was sent. */
  removed?: boolean;
}

/** Where a plan line came from when it quotes a file. */
export interface Cite {
  id: number;
  name: string;
  page: number | null;
}

/** One row in the list: a file on its way, one the server kept, or one it refused with its reason. */
type Item =
  | { key: string; name: string; state: "reading" }
  | { key: string; name: string; state: "kept"; doc: Doc }
  | { key: string; name: string; state: "refused"; why: string };

const num = (n: number, digits = 0) => n.toLocaleString(locale(), { maximumFractionDigits: digits });

export function sizeText(bytes: number): string {
  return bytes >= 1024 * 1024 ? t("attach.mb", { n: num(bytes / (1024 * 1024), 1) }) : t("attach.kb", { n: num(Math.max(1, bytes / 1024)) });
}

/** "PDF · 12 pages · 1.2 MB": what the company has of the file. */
export function docMeta(d: Doc): string {
  const extent = d.pages === null ? t("attach.chars", { n: d.chars })
    : t(d.kind === "pptx" ? "attach.slides" : "attach.pages", { n: d.pages });
  return [t(`attach.kind.${d.kind}`), extent, sizeText(d.size)].join(" · ");
}

function cutText(d: Doc): string | null {
  if (!d.cut) return null;
  return d.pages !== null && d.pages > ATTACH.pages ? t("attach.cut_pages", { n: ATTACH.pages }) : t("attach.cut_chars", { n: num(ATTACH.chars) });
}

/** "plan.pdf, p. 3": the file and page a plan line quotes. */
export function citeText(c: Cite): string {
  if (c.page === null) return c.name;
  return t(/\.pptx$/i.test(c.name) ? "attach.cite.slide" : "attach.cite.page", { name: c.name, page: c.page });
}

/** The files of one form or one kickoff turn: upload as they arrive, remove on request. Without `businessId` the
 * files wait for the form that opens the business. */
export function useAttachments(businessId?: number, initial: Doc[] = []) {
  const [items, setItems] = useState<Item[]>(() => initial.map((doc) => ({ key: `d${doc.id}`, name: doc.name, state: "kept", doc })));
  const seq = useRef(0);
  const set = (key: string, next: Item | null) => setItems((xs) => next ? xs.map((x) => (x.key === key ? next : x)) : xs.filter((x) => x.key !== key));

  async function upload(file: File, key: string) {
    if (file.size > ATTACH.mb * 1024 * 1024) return set(key, { key, name: file.name, state: "refused", why: t("attach.too_big", { mb: ATTACH.mb }) });
    const query = new URLSearchParams({ name: file.name, ...(businessId ? { business_id: String(businessId) } : {}) });
    try {
      const doc = await api<Doc>(`/documents?${query}`, {
        method: "POST", body: file, headers: { "Content-Type": "application/octet-stream", "X-Ship": "1" },
      });
      set(key, { key, name: doc.name, state: "kept", doc });
    } catch (e: any) {
      set(key, { key, name: file.name, state: "refused", why: e.message ?? String(e) });
    }
  }

  function add(files: FileList | File[]) {
    const taken = items.filter((x) => x.state !== "refused").length;
    const room = Math.max(0, ATTACH.files - taken);
    const fresh = Array.from(files).map((file, i) => ({ file, key: `u${++seq.current}`, over: i >= room }));
    setItems((xs) => [...xs, ...fresh.map(({ file, key, over }): Item => over
      ? { key, name: file.name, state: "refused", why: t("attach.full", { n: ATTACH.files }) }
      : { key, name: file.name, state: "reading" })]);
    fresh.filter((f) => !f.over).forEach(({ file, key }) => upload(file, key));
  }

  async function remove(key: string) {
    const item = items.find((x) => x.key === key);
    if (item?.state === "kept") await api(`/documents/${item.doc.id}`, { method: "DELETE", headers: { "X-Ship": "1" } });
    set(key, null);
  }

  const kept = items.flatMap((x) => (x.state === "kept" ? [x.doc] : []));
  const keptRef = useRef(kept);
  keptRef.current = kept;
  /** Lets go of every kept file (a form left without submitting); the server only marks them removed. Safe to call twice. */
  function discard() {
    const docs = keptRef.current;
    keptRef.current = [];
    docs.forEach((doc) => api(`/documents/${doc.id}`, { method: "DELETE", headers: { "X-Ship": "1" } }).catch(() => {}));
  }
  return { items, add, remove, discard, kept, reading: items.some((x) => x.state === "reading"), full: kept.length >= ATTACH.files };
}

export type Attachments = ReturnType<typeof useAttachments>;

function Row({ item, onRemove }: { item: Item; onRemove: () => void }) {
  const note = item.state === "kept" ? cutText(item.doc) : null;
  return (
    <li className={`att-row ${item.state}`}>
      <Icon name="file" />
      <span className="att-main">
        <span className="att-name" title={item.name}>{item.name}</span>
        <span className="att-meta small" role={item.state === "refused" ? "alert" : undefined}>
          {item.state === "reading" && <span className="muted">{t("attach.reading")}</span>}
          {item.state === "kept" && <span className="muted">{docMeta(item.doc)}{note ? ` · ${note}` : ""}</span>}
          {item.state === "refused" && <span className="bad-t">{item.why}</span>}
        </span>
      </span>
      {item.state !== "reading" && (
        <button type="button" className="att-x" onClick={onRemove}
          aria-label={item.state === "refused" ? t("attach.dismiss") : t("attach.remove", { name: item.name })}>
          <Icon name="close" size={14} />
        </button>
      )}
    </li>
  );
}

/** The files as rows, each with what was read or why it was refused, and a remove button. */
export function AttachList({ att, label }: { att: Attachments; label?: string }) {
  if (!att.items.length) return null;
  return (
    <ul className="att-list" aria-label={label ?? t("attach.list")}>
      {att.items.map((x) => <Row key={x.key} item={x} onRemove={() => att.remove(x.key)} />)}
    </ul>
  );
}

/** Drag and drop of files onto an element: spread `on` on it, read `over` to show the target. */
export function useDrop(att: Attachments) {
  const [over, setOver] = useState(false);
  const has = (e: DragEvent) => Array.from(e.dataTransfer.types).includes("Files");
  return {
    over,
    on: {
      onDragOver: (e: DragEvent) => { if (has(e)) { e.preventDefault(); setOver(true); } },
      onDragLeave: (e: DragEvent) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setOver(false); },
      onDrop: (e: DragEvent) => { if (!has(e)) return; e.preventDefault(); setOver(false); if (!att.full) att.add(e.dataTransfer.files); },
    },
  };
}

function Picker({ att, id, children, className, describedBy }: {
  att: Attachments; id: string; children: string; className?: string; describedBy?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <input ref={input} type="file" multiple accept={ACCEPT} className="sr-only" tabIndex={-1} aria-hidden="true"
        onChange={(e) => { if (e.target.files) att.add(e.target.files); e.target.value = ""; }} />
      <button type="button" id={id} className={className} disabled={att.full} aria-describedby={describedBy} onClick={() => input.current?.click()}>
        <Icon name="file" size={14} />{children}
      </button>
    </>
  );
}

/** A quiet drop zone with a file button and the limits, then the rows. For a form field. */
export function DropZone({ att, id }: { att: Attachments; id: string }) {
  const drop = useDrop(att);
  const limits = t("attach.limits", { n: ATTACH.files, mb: ATTACH.mb, pages: ATTACH.pages, chars: num(ATTACH.chars) });
  return (
    <div className="att" {...drop.on}>
      <div className={`att-zone ${drop.over ? "over" : ""} ${att.full ? "full" : ""}`}>
        {att.full ? <span className="muted small">{t("attach.full", { n: ATTACH.files })}</span> : (
          <span className="att-zone-l">
            <span className="muted">{t("attach.drop")}</span>
            <Picker att={att} id={id} className="btn-ghost att-pick" describedBy={`${id}-limits`}>{t("attach.choose")}</Picker>
          </span>
        )}
        <span id={`${id}-limits`} className="muted small att-limits">{limits}</span>
      </div>
      <AttachList att={att} />
    </div>
  );
}

/** A quiet file button for a toolbar; the limits are its tooltip and its description. */
export function AttachButton({ att, id }: { att: Attachments; id: string }) {
  const limits = t("attach.limits", { n: ATTACH.files, mb: ATTACH.mb, pages: ATTACH.pages, chars: num(ATTACH.chars) });
  return (
    <span title={limits}>
      <Picker att={att} id={id} className="quiet att-add" describedBy={`${id}-limits`}>{t("attach.add")}</Picker>
      <span id={`${id}-limits`} className="sr-only">{limits}</span>
    </span>
  );
}

/** Files the owner already gave, read-only, as the chat shows them in the owner's message. */
export function DocRows({ docs }: { docs: Doc[] }) {
  if (!docs.length) return null;
  return (
    <ul className="att-list att-read" aria-label={t("attach.talk")}>
      {docs.map((d) => (
        <li key={d.id} className={`att-row ${d.removed ? "removed" : "kept"}`}>
          <Icon name="file" />
          <span className="att-main">
            <span className="att-name" title={d.name}>{d.name}</span>
            <span className="att-meta small muted">{d.removed ? t("attach.removed") : docMeta(d) + (cutText(d) ? ` · ${cutText(d)}` : "")}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

function Files({ businessId, initial }: { businessId: number; initial: Doc[] }) {
  const files = useAttachments(businessId, initial);
  return (
    <Section title={t("attach.business")} count={files.kept.length || undefined}>
      <Box>
        <p className="small muted att-lead">{t("attach.business_hint")}</p>
        <DropZone att={files} id="biz-files" />
      </Box>
    </Section>
  );
}

/** The business's files on its Plan tab: what agents may cite, and where to add or remove one. */
export function BusinessFiles({ businessId }: { businessId: number }) {
  const [docs, setDocs] = useState<Doc[] | null>(null);
  useEffect(() => {
    let live = true;
    api<Doc[]>(`/businesses/${businessId}/documents`).then((d) => live && setDocs(d), () => live && setDocs([]));
    return () => { live = false; };
  }, [businessId]);
  return docs ? <Files key={businessId} businessId={businessId} initial={docs} /> : null;
}
