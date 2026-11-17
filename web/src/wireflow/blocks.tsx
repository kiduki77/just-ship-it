import type { CSSProperties, ReactNode } from "react";
import type { Block, BlockKind } from "./types";

/** Block text is the product's own words, drawn as written. Every kind has a bounded drawn height (the stylesheet
 * clamps lines and rows), so a frame's height comes from its blocks and never from the length of a string. */
export const BLOCKS: Record<BlockKind, (b: Block) => ReactNode> = {
  nav: (b) => (
    <span className="wf-nav">
      <b>{b.text}</b>
      {b.items.map((x, i) => <span key={i}>{x}</span>)}
    </span>
  ),
  title: (b) => <span className="wf-title">{b.text}</span>,
  text: (b) => <span className="wf-text">{b.text}</span>,
  notice: (b) => <span className="wf-notice">{b.text}</span>,
  actions: (b) => (
    <span className="wf-actions">
      {b.items.map((x, i) => <span key={i} className={i === 0 ? "wf-btn wf-btn-main" : "wf-btn"}>{x}</span>)}
    </span>
  ),
  field: (b) => (
    <span className="wf-field">
      <span className="wf-label">{b.text}</span>
      <span className="wf-input">{b.items.map((x, i) => <span key={i}>{x}</span>)}</span>
    </span>
  ),
  upload: (b) => <span className="wf-upload">{b.text}</span>,
  list: (b) => (
    <span className="wf-list">
      {b.text && <span className="wf-label">{b.text}</span>}
      {b.items.map((x, i) => <span key={i} className={i < 4 ? "wf-li" : "wf-li wf-more"}>{x}</span>)}
    </span>
  ),
  table: (b) => {
    const cols = Math.max(1, Math.min(b.items.length, 6));
    return (
      <span className="wf-table" style={{ "--cols": cols } as CSSProperties}>
        {b.text && <span className="wf-label">{b.text}</span>}
        <span className="wf-grid">
          {b.items.slice(0, cols).map((x, i) => <span key={i} className="wf-th">{x}</span>)}
          {Array.from({ length: cols * 3 }, (_, i) => <span key={`r${i}`} className="wf-td" />)}
        </span>
      </span>
    );
  },
  chips: (b) => (
    <span className="wf-chips">
      {b.items.map((x, i) => <span key={i} className="wf-chip">{x}</span>)}
    </span>
  ),
  media: (b) => (
    <span className="wf-media">
      <span className="wf-cross" />
      {b.text && <span className="wf-label">{b.text}</span>}
    </span>
  ),
};

export function Blocks({ blocks }: { blocks: Block[] }) {
  return <>{blocks.map((b, i) => <span key={i} className="wf-block">{(BLOCKS[b.kind] ?? BLOCKS.text)(b)}</span>)}</>;
}
