import { Fragment, ReactNode } from "react";

/**
 * A fix names its command between backticks; show the command as code the owner can copy. Punctuation right after the
 * command stays on its line, so a wrapped sentence never starts with a lone period.
 */
export function Fix({ text }: { text: string }) {
  const parts = text.split("`");
  const out: ReactNode[] = [];
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 0) {
      out.push(<Fragment key={i}>{parts[i]}</Fragment>);
      continue;
    }
    const code = <code className="mono fix-code">{parts[i]}</code>;
    const punct = (parts[i + 1] ?? "").match(/^[.,;:)!?。]+/)?.[0];
    if (!punct) {
      out.push(<Fragment key={i}>{code}</Fragment>);
      continue;
    }
    out.push(<span key={i} className="fix-glue">{code}{punct}</span>);
    parts[i + 1] = parts[i + 1].slice(punct.length);
  }
  return <>{out}</>;
}
