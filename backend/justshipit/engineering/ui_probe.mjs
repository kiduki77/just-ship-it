// The host's keyboard and forced-colors probe of a product's screens (engineering/ui_probe.py runs it).
// Node's standard library only: chromium is driven over --remote-debugging-pipe, so the engineer image needs nothing
// it does not already have. Usage:
//   node ui_probe.mjs --browser <chromium> --base http://127.0.0.1:4173 [--states scripts/ui-states.txt]
//                     [--budget 90] [--lang en-US]
// Prints one `[ui-probe] <state>\t<check>\t<finding>` line per finding and a closing `[ui-probe-done] ...` line.
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > 0 ? process.argv[i + 1] : fallback;
};
const BASE = arg("base", "http://127.0.0.1:4173").replace(/\/$/, "");
const BUDGET_MS = Number(arg("budget", "90")) * 1000;
const DEADLINE = Date.now() + BUDGET_MS;
const MAX_STATES = 24;
const MAX_TABS = 150;
const MAX_PRESSES = 30; // per state
// A press that would lose or send the user's data, or open the system's file or print dialogs, is never made.
const RISKY = /delete|remove|discard|erase|clear|reset|wipe|upload|download|export|import|file|print|log ?out|sign ?out|삭제|제거|지우|비우|버리|초기화|업로드|다운로드|내려받|내보내|가져오|불러오|파일|인쇄|로그아웃/i;
const PRESSABLE = new Set(["button", "togglebutton", "tab", "switch", "checkbox", "radio", "menuitem",
  "menuitemcheckbox", "menuitemradio", "DisclosureTriangle", "combobox"]);
const SPACE_ROLES = new Set(["checkbox", "radio", "switch"]);

const out = (state, check, text) => console.log(`[ui-probe] ${state}\t${check}\t${text.replace(/\s+/g, " ")}`);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const late = () => Date.now() > DEADLINE;
const describe = (s) => `${s.role || s.tag} "${s.name}" (${s.label})`;
// A list for a finding: repeats counted, and at most five names.
const some = (items) => {
  const counts = new Map();
  for (const x of items) counts.set(x, (counts.get(x) || 0) + 1);
  const named = [...counts].map(([x, n]) => (n > 1 ? `${x} x${n}` : x));
  return named.slice(0, 5).join(", ") + (named.length > 5 ? `, and ${named.length - 5} more` : "");
};

function states() {
  const list = [["first-open", "/"]];
  const file = arg("states");
  if (!file) return list;
  let text = "";
  try { text = readFileSync(file, "utf8"); } catch { return list; }
  for (const line of text.split(/\r?\n/)) {
    if (line.startsWith("#")) continue;
    const [name, path] = line.trim().split(/\s+/);
    if (name && /^[A-Za-z0-9_-]+$/.test(name) && path?.startsWith("/")) list.push([name, path]);
  }
  return list.slice(0, MAX_STATES + 1);
}

class Cdp {
  constructor(child) {
    this.id = 0;
    this.waiting = new Map();
    this.listeners = [];
    this.writer = child.stdio[3];
    let buf = "";
    child.stdio[4].on("data", (chunk) => {
      buf += chunk.toString("utf8");
      let end;
      while ((end = buf.indexOf("\0")) >= 0) {
        const msg = JSON.parse(buf.slice(0, end));
        buf = buf.slice(end + 1);
        if (msg.id && this.waiting.has(msg.id)) {
          const { ok, fail } = this.waiting.get(msg.id);
          this.waiting.delete(msg.id);
          msg.error ? fail(new Error(`${msg.error.message}`)) : ok(msg.result);
        } else if (msg.method) {
          for (const l of this.listeners) l(msg);
        }
      }
    });
  }
  send(method, params = {}, sessionId) {
    const id = ++this.id;
    this.writer.write(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }) + "\0");
    return new Promise((ok, fail) => {
      this.waiting.set(id, { ok, fail });
      setTimeout(() => this.waiting.has(id) && (this.waiting.delete(id), fail(new Error(`${method} timed out`))), 15000);
    });
  }
  on(fn) { this.listeners.push(fn); }
}

// Runs in the page before its own code: no file, save or print dialog can open, and no confirm() can block.
const GUARD = `(() => {
  const no = () => Promise.reject(new DOMException("blocked by the UI probe", "AbortError"));
  window.showOpenFilePicker = no; window.showSaveFilePicker = no; window.showDirectoryPicker = no;
  window.print = () => {}; window.__uiProbeIds = new WeakMap(); window.__uiProbeNext = 1;
})()`;

// In-page helpers, evaluated after each load.
const HELPERS = `(() => {
  const REGION = 'main,nav,aside,header,footer,form,dialog,[role=dialog],[role=alertdialog],[role=region],' +
    '[role=navigation],[role=main],[role=complementary],[role=banner],[role=contentinfo],[role=form],' +
    '[role=search],section[aria-label],section[aria-labelledby]';
  const LIVE = '[role=status],[role=alert],[role=log],[aria-live]';
  const idOf = (el) => {
    if (!el) return 0;
    let n = window.__uiProbeIds.get(el);
    if (!n) { n = window.__uiProbeNext++; window.__uiProbeIds.set(el, n); }
    return n;
  };
  const path = (el) => {
    const parts = [];
    for (let e = el; e && e.nodeType === 1 && e !== document.documentElement; e = e.parentElement) {
      const same = e.parentElement ? [...e.parentElement.children].filter((c) => c.tagName === e.tagName) : [e];
      parts.unshift(e.tagName.toLowerCase() + (same.length > 1 ? ':nth-of-type(' + (same.indexOf(e) + 1) + ')' : ''));
    }
    return parts.join('>');
  };
  const label = (el) => {
    if (!el || el.nodeType !== 1) return String(el);
    const cls = typeof el.className === 'string' && el.className.trim() ? '.' + el.className.trim().split(/\\s+/).slice(0, 2).join('.') : '';
    return el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + cls;
  };
  const region = (el) => {
    const r = el.closest(REGION);
    if (!r) return 'the page';
    const name = r.getAttribute('aria-label') || (r.getAttribute('aria-labelledby') &&
      document.getElementById(r.getAttribute('aria-labelledby'))?.textContent?.trim()) || '';
    return label(r) + (name ? ' "' + name.slice(0, 40) + '"' : '');
  };
  // The page, when it scrolls, and every element made to scroll, whether or not its content overflows yet.
  const scrollers = () => {
    const root = document.scrollingElement || document.documentElement;
    const all = root.scrollHeight > root.clientHeight + 4 ? [root] : [];
    for (const e of document.querySelectorAll('body *')) {
      const oy = getComputedStyle(e).overflowY;
      if (oy === 'auto' || oy === 'scroll' || oy === 'overlay') all.push(e);
    }
    return all;
  };
  // The border as drawn, one [width, style, colour] per side; a side with no style or no width draws nothing.
  const edge = (el) => {
    const s = getComputedStyle(el);
    return ['Top', 'Right', 'Bottom', 'Left'].map((side) => {
      const style = s['border' + side + 'Style'], width = parseFloat(s['border' + side + 'Width']) || 0;
      return style === 'none' || style === 'hidden' || !width ? [0, 'none', ''] : [width, style, s['border' + side + 'Color']];
    });
  };
  // A border shows focus when its width or style changes, or its colour on a side 2px or wider: in forced colours
  // the browser itself recolours a 1px button border on focus, which every product gets and no one sees.
  const edgeMoved = (was, now) => was.some((w, i) => w[0] !== now[i][0] || w[1] !== now[i][1] ||
    (w[2] !== now[i][2] && Math.max(w[0], now[i][0]) >= 2));
  const MARK = ['textDecorationLine', 'fontWeight', 'color', 'backgroundColor', 'fontStyle', 'outlineStyle'];
  const look = (el) => { const s = getComputedStyle(el); return JSON.stringify(edge(el)) + '|' + MARK.map((k) => s[k]).join('|'); };
  window.__uiProbe = {
    idOf, path, label, region, LIVE,
    focused() {
      const a = document.activeElement;
      if (!a || a === document.body || a === document.documentElement) return null;
      return { id: idOf(a), path: path(a), label: label(a), region: region(a), tag: a.tagName.toLowerCase(),
        type: a.getAttribute('type') || '' };
    },
    edges: new Map(),
    shadows: new Map(),
    // Each focusable element's look at rest: its box-shadow in normal colours, its border in forced colours.
    rest(what) {
      for (const e of document.querySelectorAll('a[href],button,input,select,textarea,summary,[tabindex],[contenteditable]'))
        what === 'shadow' ? this.shadows.set(idOf(e), getComputedStyle(e).boxShadow) : this.edges.set(idOf(e), edge(e));
    },
    focusShown() {
      const a = document.activeElement;
      const s = getComputedStyle(a);
      if (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0) return 'outline';
      const before = this.edges.get(idOf(a));
      return before && edgeMoved(before, edge(a)) ? 'border' : '';
    },
    shadowMoved() {
      const a = document.activeElement;
      return this.shadows.has(idOf(a)) && this.shadows.get(idOf(a)) !== getComputedStyle(a).boxShadow;
    },
    scrolled() {
      return scrollers().map((e) => ({ path: e === document.scrollingElement ? ':root' : path(e), label: label(e),
        max: e.scrollHeight - e.clientHeight, node: idOf(e) }));
    },
    scrollTo(spots) {
      for (const p of spots) {
        const e = p.path === ':root' ? document.scrollingElement : document.querySelector(p.path);
        if (e && p.max > 4 && e.scrollTop === 0) e.scrollTop = Math.max(1, Math.min(p.max, Math.round(p.max / 2), 240));
      }
    },
    // Each list's scroll, and whether it is still the node it was and holds focus.
    scrollTops(spots) {
      const a = document.activeElement;
      return spots.map((p) => {
        const e = p.path === ':root' ? document.scrollingElement : document.querySelector(p.path);
        return e ? { top: e.scrollTop, same: idOf(e) === p.node, focus: !!a && a !== document.body && e.contains(a) }
          : { top: -1 };
      });
    },
    lives() {
      return [...document.querySelectorAll(LIVE)].map((e) => ({ id: idOf(e), path: path(e), label: label(e),
        kind: e.getAttribute('role') || 'aria-live=' + e.getAttribute('aria-live') }));
    },
    alive(ids) {
      const now = new Set([...document.querySelectorAll(LIVE)].map(idOf));
      return ids.map((n) => now.has(n));
    },
    // Elements marked as selected or current, each paired with a sibling of the same kind that is not.
    marked() {
      const out = [];
      const sel = '[aria-selected=true],[aria-current]:not([aria-current=false]),[aria-pressed=true],[aria-checked=true]';
      for (const e of document.querySelectorAll(sel)) {
        const attr = ['aria-selected', 'aria-current', 'aria-pressed', 'aria-checked'].find((a) => e.hasAttribute(a));
        const peer = [...(e.parentElement?.parentElement?.querySelectorAll(e.tagName) || [])]
          .find((p) => p !== e && p.getAttribute(attr) !== e.getAttribute(attr));
        if (peer) out.push({ label: label(e), attr, text: (e.textContent || '').trim().slice(0, 40),
          same: look(e) === look(peer) });
      }
      return out.slice(0, 10);
    },
    byPath(p) { return document.querySelector(p); },
  };
  return true;
})()`;

async function main() {
  const browser = arg("browser");
  if (!browser) throw new Error("--browser is required");
  const profile = mkdtempSync(join(tmpdir(), "ui-probe-"));
  const child = spawn(browser, ["--headless=new", "--remote-debugging-pipe", "--no-sandbox", "--disable-gpu",
    "--disable-dev-shm-usage", "--no-first-run", "--no-default-browser-check", "--no-proxy-server",
    "--disable-extensions", "--hide-scrollbars", `--lang=${arg("lang", "en-US")}`, `--user-data-dir=${profile}`,
    "about:blank"], { stdio: ["ignore", "ignore", "ignore", "pipe", "pipe"] });
  child.on("error", (e) => { console.log(`[ui-probe-error] the browser did not start: ${e.message}`); process.exit(3); });
  const cdp = new Cdp(child);
  const kill = setTimeout(() => { console.log("[ui-probe-error] stopped: the probe ran past its time"); finish(4); },
    BUDGET_MS + 20000);
  const finish = (code) => {
    clearTimeout(kill);
    try { child.kill(); } catch {}
    setTimeout(() => { try { rmSync(profile, { recursive: true, force: true }); } catch {} process.exit(code); }, 300);
  };

  let probed = 0, presses = 0, skipped = [];
  try {
    await cdp.send("Browser.setDownloadBehavior", { behavior: "deny" }).catch(() => {});
    const { targetId } = await cdp.send("Target.createTarget", { url: "about:blank" });
    const { sessionId: sid } = await cdp.send("Target.attachToTarget", { targetId, flatten: true });
    const send = (m, p) => cdp.send(m, p, sid);
    let loaded = null, chooser = false;
    cdp.on((msg) => {
      if (msg.sessionId !== sid) return;
      if (msg.method === "Page.loadEventFired" && loaded) loaded();
      if (msg.method === "Page.fileChooserOpened") chooser = true;
      if (msg.method === "Page.javascriptDialogOpening")
        send("Page.handleJavaScriptDialog", { accept: false }).catch(() => {});
    });
    await send("Page.enable");
    await send("Runtime.enable");
    await send("Accessibility.enable");
    await send("Page.setInterceptFileChooserDialog", { enabled: true }).catch(() => {});
    await send("Page.addScriptToEvaluateOnNewDocument", { source: GUARD });
    // A laptop's viewport once the browser's own bars are drawn: a list that fits a big screen still scrolls here.
    await send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 720, deviceScaleFactor: 1, mobile: false });
    const origin = new URL(BASE).origin;

    const evaluate = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
      return r.result.value;
    };
    const forced = (on) => send("Emulation.setEmulatedMedia", { features: [{ name: "forced-colors", value: on ? "active" : "none" }] });
    const open = async (path) => {
      await send("Storage.clearDataForOrigin", { origin, storageTypes: "all" }).catch(() => {});
      const done = new Promise((r) => { loaded = r; setTimeout(r, 8000); });
      const nav = await send("Page.navigate", { url: BASE + path });
      if (nav.errorText) throw new Error(`it did not open: ${nav.errorText}`);
      await done;
      loaded = null;
      let last = -1;
      for (let i = 0; i < 20; i++) {  // settled: the page stops changing for 200 ms
        const size = await evaluate("document.body ? document.body.innerHTML.length : 0");
        if (size === last) break;
        last = size;
        await sleep(100);
      }
      await evaluate(HELPERS);
    };
    const key = async (name) => {
      const k = { Tab: ["Tab", 9, ""], Enter: ["Enter", 13, "\r"], " ": ["Space", 32, " "] }[name];
      await send("Input.dispatchKeyEvent", { type: "keyDown", key: name, code: k[0], windowsVirtualKeyCode: k[1],
        ...(k[2] ? { text: k[2] } : {}) });
      await send("Input.dispatchKeyEvent", { type: "keyUp", key: name, code: k[0], windowsVirtualKeyCode: k[1] });
    };
    const ax = async () => {
      const { result } = await send("Runtime.evaluate", { expression: "document.activeElement" });
      if (!result.objectId) return { role: "", name: "" };
      const { nodes } = await send("Accessibility.getPartialAXTree", { objectId: result.objectId, fetchRelatives: false });
      const n = nodes.find((x) => !x.ignored) || nodes[0] || {};
      return { role: n.role?.value || "", name: (n.name?.value || "").trim() };
    };

    for (const [state, path] of states()) {
      if (late()) { skipped.push(state); continue; }
      try {
        // (a) and (c): Tab through the page in forced colors, naming each stop and checking its focus shows.
        await forced(false);
        await open(path);
        await evaluate("window.__uiProbe.rest('shadow')");
        await forced(true);
        await evaluate("window.__uiProbe.rest('edge')");
        const stops = [];
        const seen = new Set();
        for (let i = 0; i < MAX_TABS && !late(); i++) {
          await key("Tab");
          const f = await evaluate("window.__uiProbe.focused()");
          if (!f) { if (stops.length) break; else continue; }
          if (seen.has(f.id)) break;
          seen.add(f.id);
          const { role, name } = await ax();
          const stop = { ...f, role, name, shown: await evaluate("window.__uiProbe.focusShown()") };
          if (!stop.shown) {
            await forced(false);
            stop.shadow = await evaluate("window.__uiProbe.shadowMoved()");
            await forced(true);
          }
          stops.push(stop);
        }
        const hidden = stops.filter((s) => !s.shown);
        if (hidden.length) {
          const shadow = hidden.some((s) => s.shadow);
          out(state, "forced-colors", `${hidden.length} of ${stops.length} focus stops show no focus in forced colors` +
            ` (no outline, no border change${shadow ? "; the focus ring is a box-shadow, which forced colors removes" : ""}): ` +
            some(hidden.map(describe)));
        }
        for (const m of await evaluate("window.__uiProbe.marked()"))
          if (m.same) out(state, "forced-colors", `the ${m.attr} item "${m.text}" (${m.label}) looks the same as the ` +
            "items that are not in forced colors: its mark is a colour only, which forced colors replaces");
        await forced(false);

        const groups = new Map();
        for (const s of stops) {
          if (s.role !== "button" && s.role !== "link") continue;
          if (!s.name) { out(state, "names", `a ${s.role} has no accessible name (${s.label} in ${s.region})`); continue; }
          const k = `${s.region}\u0000${s.role}\u0000${s.name}`;
          groups.set(k, [...(groups.get(k) || []), s]);
        }
        for (const [k, list] of groups) {
          if (list.length < 2) continue;
          const [region, role, name] = k.split("\u0000");
          out(state, "names", `${list.length} ${role}s share the accessible name "${name}" in ${region} ` +
            `(${list[0].label}); a screen reader user cannot tell which item each acts on`);
        }

        // (b) and (d): press each button with the keyboard on a fresh load, its lists scrolled part way.
        const lost = [], reset = new Map(), rebuilt = new Map(), recreated = new Map();
        let pressed = 0;
        for (const s of stops) {
          if (late() || pressed >= MAX_PRESSES) break;
          if (!PRESSABLE.has(s.role) && s.tag !== "button" && s.tag !== "summary") continue;
          if ((s.tag === "input" && s.type === "file") || RISKY.test(s.name) || RISKY.test(s.label)) continue;
          await open(path);
          const ok = await evaluate(`(() => { const e = window.__uiProbe.byPath(${JSON.stringify(s.path)});
            if (!e) return false; e.focus(); return document.activeElement === e; })()`);
          if (!ok) continue;
          const spots = await evaluate("window.__uiProbe.scrolled()");
          await evaluate(`window.__uiProbe.scrollTo(${JSON.stringify(spots)})`);
          const before = await evaluate(`window.__uiProbe.scrollTops(${JSON.stringify(spots)})`);
          const lives = await evaluate("window.__uiProbe.lives()");
          chooser = false;
          await evaluate("window.__uiProbeMark = 1");
          await key(SPACE_ROLES.has(s.role) ? " " : "Enter");
          await sleep(400);
          pressed++, presses++;
          if (chooser) continue;
          const same = await evaluate("window.__uiProbeMark === 1").catch(() => false);
          if (!same) continue;  // it opened another page, which its own line in ui-states.txt probes
          if (!(await evaluate("window.__uiProbe.focused()"))) lost.push(describe(s));
          const after = await evaluate(`window.__uiProbe.scrollTops(${JSON.stringify(spots)})`);
          spots.forEach((p, i) => {
            // A list that kept its node and scrolled to show where focus went followed the user. One rebuilt at
            // the top, or one that jumped back with focus elsewhere, lost the user's place.
            const was = before[i].top, now = after[i];
            if (was > 0 && now.top === 0 && !(now.same && now.focus))
              reset.set(p.label, [...(reset.get(p.label) || []), describe(s)]);
            else if (p.max <= 4 && now.top === 0 && now.same === false)
              rebuilt.set(p.label, [...(rebuilt.get(p.label) || []), describe(s)]);
          });
          const alive = await evaluate(`window.__uiProbe.alive(${JSON.stringify(lives.map((l) => l.id))})`);
          const old = new Set(lives.map((l) => l.id));
          const born = (await evaluate("window.__uiProbe.lives()")).filter((n) => !old.has(n.id));
          lives.forEach((l, i) => {
            const k = `${l.kind} node (${l.label})`;
            if (!alive[i] && born.some((n) => n.path === l.path || n.kind === l.kind))
              recreated.set(k, [...(recreated.get(k) || []), describe(s)]);
          });
        }
        if (lost.length) out(state, "focus", `focus fell to the page body after pressing ${some(lost)}; keyboard and ` +
          "screen reader users lose their place");
        for (const [what, by] of reset)
          out(state, "scroll", `${what} scrolled back to the top after pressing ${some(by)}`);
        const idle = [...rebuilt.keys()].filter((what) => !reset.has(what));
        if (idle.length)
          out(state, "scroll", `${some(idle)} ${idle.length > 1 ? "are" : "is"} made to scroll but rebuilt as new ` +
            `nodes after pressing ${some(idle.flatMap((what) => rebuilt.get(what)).filter((x, i, all) => all.indexOf(x) === i))}; ` +
            "the content does not overflow here, but once it does, each press will send it back to the top");
        for (const [what, by] of recreated)
          out(state, "live-region", `the ${what} was replaced by a new node, not updated, after pressing ${some(by)}; ` +
            "a screen reader can miss what it says");
        probed++;
      } catch (e) {
        out(state, "error", `could not be probed: ${e.message}`);
      }
    }
  } catch (e) {
    console.log(`[ui-probe-error] ${e.message}`);
    return finish(2);
  }
  console.log(`[ui-probe-done] states=${probed} presses=${presses}` +
    (skipped.length ? ` stopped-at-cap=${skipped.join(",")}` : ""));
  finish(0);
}

main();
