import { marked } from "marked";

// `breaks`: agents write single newlines as line breaks; without it their lists collapse into one paragraph.
marked.setOptions({ gfm: true, breaks: true });
// CommonMark leaves "**[메뉴 이름]**과" as text: a closing "**" after punctuation must be followed by a space or
// punctuation, and Korean puts a particle straight after it. Bold runs to the next "**" on the line, not past one;
// anything else (false) goes to marked's own rule.
const STRONG = /^\*\*(?![\s*])((?:(?!\*\*)[^\n])*?[^\s*\\])\*\*(?=[\p{L}\p{N}])/u;

// The page has one h1, its title: agent headings sit one level down ("# Role" is an h2), inside the section that shows them.
marked.use({
  walkTokens(token) {
    if (token.type === "heading") token.depth = Math.min(6, token.depth + 1);
  },
});

// Only "~~x~~" strikes through. Agents write ranges with a single "~" ("T-46~T-49", "3~13%").
marked.use({
  tokenizer: {
    emStrong(src) {
      const m = STRONG.exec(src);
      return m ? { type: "strong", raw: m[0], text: m[1], tokens: this.lexer.inlineTokens(m[1]) } : false;
    },
    del(src) {
      const m = /^~~(?=[^\s~])([\s\S]*?[^\s~])~~(?!~)/.exec(src);
      return m ? { type: "del", raw: m[0], text: m[1], tokens: this.lexer.inlineTokens(m[1]) } : undefined;
    },
  },
});

// The images the console serves: a build's captures (api/console.py `dev_screen` and `dev_walk_shot`,
// engineering/try_it.py `SHOT_NAME`, `WALK_SHOT` and `_HEAD`). Each is a plain read that answers with a PNG.
const SERVED_IMAGE = /^\/api\/dev-jobs\/\d+\/(?:screens\/[A-Za-z0-9_-]+\.png|walk\/[0-9a-f]{7,40}\/JN\d+\.\d+\.png)$/;

/**
 * Whether agent Markdown may show the image at `src`, read from the page at `page`. An `<img>` makes the browser send
 * a GET with the owner's cookie, so any other address of the console (the event stream, an export) is not an image
 * source, and neither is another site (a tracking beacon).
 */
export function servedImage(src: string, page: string): boolean {
  try {
    const url = new URL(src, page);
    return url.origin === new URL(page).origin && !url.search && SERVED_IMAGE.test(url.pathname);
  } catch {
    return false;
  }
}

/** Agent Markdown as HTML, before the console names its codes and sanitizes it. */
export const markdownHtml = (text: string): string => marked.parse(text, { async: false }) as string;
