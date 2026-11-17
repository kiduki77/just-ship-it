import { CSSProperties, useEffect, useState } from "react";
import { api, post, useLive } from "../api";
import { Section } from "../components/kit";
import { PlainKey, t } from "../i18n";
import linearShot from "../assets/looks/linear.webp";
import stripeShot from "../assets/looks/stripe.webp";
import tossShot from "../assets/looks/toss.webp";
import { Fail, Loaded, useDo } from "./parts";
import "./look.css";

/** The server's `contracts/look.py`: a pack, one main colour, and the nine accent roles derived from that colour. */
type PackSlug = "linear" | "toss" | "stripe";
type StylePack = { slug: PackSlug; name: string; pitch: string; main: string };
type Role = "primary" | "primary-hover" | "primary-press" | "on-primary" | "primary-text" | "primary-wash"
  | "primary-wash-strong" | "primary-border" | "highlight";
type Look = { pack: PackSlug; main: string; roles: Record<Role, string> };
type BusinessLook = Look & { picked: boolean };

const SHOT: Record<PackSlug, string> = { linear: linearShot, toss: tossShot, stripe: stripeShot };

const SWATCHES: { hex: string; name: PlainKey }[] = [
  { hex: "#3182f6", name: "business-look.swatch.blue" },
  { hex: "#5e6ad2", name: "business-look.swatch.indigo" },
  { hex: "#533afd", name: "business-look.swatch.violet" },
  { hex: "#0e9384", name: "business-look.swatch.teal" },
  { hex: "#16a34a", name: "business-look.swatch.green" },
  { hex: "#f5a524", name: "business-look.swatch.amber" },
  { hex: "#f2622e", name: "business-look.swatch.orange" },
  { hex: "#e5484d", name: "business-look.swatch.rose" },
];

/** `#rrggbb` in lowercase from `#RGB`, `#RRGGBB` or either without the `#`, as the server's `hex_color` reads it. */
function hexColor(value: string): string | null {
  const m = /^#?([0-9a-f]{6}|[0-9a-f]{3})$/i.exec(value.trim());
  if (!m) return null;
  const digits = m[1].toLowerCase();
  return `#${digits.length === 3 ? [...digits].map((c) => c + c).join("") : digits}`;
}

/** The roles `main` derives into for `pack`, asked of the server a moment after the owner stops changing them. */
function useDerived(pack: PackSlug, main: string | null, first: Look["roles"]) {
  const [roles, setRoles] = useState(first);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!main) return;
    const ctl = new AbortController();
    const timer = window.setTimeout(() => {
      api<Look>(`/styles/${pack}/derive?main=${encodeURIComponent(main)}`, { signal: ctl.signal })
        .then((look) => { setRoles(look.roles); setError(null); })
        .catch((e) => { if (e?.name !== "AbortError") setError(String(e.message ?? e)); });
    }, 150);
    return () => { window.clearTimeout(timer); ctl.abort(); };
  }, [pack, main]);
  return { roles, error };
}

/** A small product screen drawn in the pack's shape with the derived roles, so the owner sees the colour at work. */
function Preview({ pack, roles }: { pack: PackSlug; roles: Look["roles"] }) {
  const vars = Object.fromEntries(Object.entries(roles).map(([role, hex]) => [`--lm-${role}`, hex])) as CSSProperties;
  return (
    <div className="lm" data-pack={pack} style={vars} aria-hidden="true">
      <div className="lm-head">
        <span className="lm-title">{t("business-look.mock.title")}</span>
        <span className="lm-badge">{t("business-look.mock.badge")}</span>
        <span className="lm-link">{t("business-look.mock.link")}</span>
      </div>
      <ul className="lm-rows">
        {[1042, 1043, 1044].map((n) => (
          <li key={n} className={n === 1043 ? "on" : undefined}>
            <span>{t("business-look.mock.order", { n })}</span>
            <span className="lm-meta">{t(n === 1044 ? "business-look.mock.packing" : "business-look.mock.paid")}</span>
          </li>
        ))}
      </ul>
      <div className="lm-foot"><span className="lm-btn">{t("business-look.mock.button")}</span></div>
    </div>
  );
}

function LookForm({ businessId, packs, initial }: { businessId: number; packs: StylePack[]; initial: BusinessLook }) {
  const [saved, setSaved] = useState(initial);
  const [pack, setPack] = useState(initial.pack);
  const [text, setText] = useState(initial.main);
  const [justSaved, setJustSaved] = useState(false);
  const main = hexColor(text);
  const preview = useDerived(pack, main, initial.roles);
  const { busy, error, run } = useDo();
  const changed = !saved.picked || pack !== saved.pack || main !== saved.main;
  const nameOf = (slug: PackSlug) => packs.find((p) => p.slug === slug)?.name ?? slug;

  const edit = (next: () => void) => { next(); setJustSaved(false); };
  const pickPack = (next: StylePack) => edit(() => {
    // a colour the owner never touched is the old pack's own; it follows the pack they switch to
    if (main === hexColor(packs.find((p) => p.slug === pack)?.main ?? "")) setText(hexColor(next.main) ?? next.main);
    setPack(next.slug);
  });
  const save = () => run(async () => {
    setSaved(await post<BusinessLook>(`/businesses/${businessId}/look`, { pack, main }));
    setJustSaved(true);
  });

  return (
    <div className="look">
      <p className="biz-lead look-lead">{t("business-look.lead")}</p>
      {!saved.picked && <p className="look-default">{t("business-look.default", { pack: nameOf(saved.pack) })}</p>}

      <Section title={t("business-look.style")}>
        <div className="look-packs" role="group" aria-label={t("business-look.styles_label")}>
          {packs.map((p) => (
            <button key={p.slug} type="button" aria-pressed={p.slug === pack} className="look-pack" onClick={() => pickPack(p)}>
              <img src={SHOT[p.slug]} alt={t("business-look.shot", { pack: p.name })} width={640} height={400} loading="lazy" />
              <span className="look-pack-b">
                <span className="look-pack-n"><span className="look-radio" aria-hidden="true" />{p.name}</span>
                <span className="look-pack-p">{p.pitch}</span>
              </span>
            </button>
          ))}
        </div>
      </Section>

      <Section title={t("business-look.color")}>
        <div className="look-color">
          <div className="look-pick">
            <p className="biz-note-s">{t("business-look.color_hint")}</p>
            <div className="look-swatches" role="group" aria-label={t("business-look.swatches")}>
              {SWATCHES.map((s) => (
                <button key={s.hex} type="button" className="look-swatch" style={{ background: s.hex }} title={`${t(s.name)} ${s.hex}`}
                  aria-label={t(s.name)} aria-pressed={main === s.hex} onClick={() => edit(() => setText(s.hex))} />
              ))}
            </div>
            <div className="field look-hex">
              <label htmlFor="look-hex">{t("business-look.custom")}</label>
              <span className="look-hex-in">
                <span className="look-hex-chip" style={{ background: main ?? "transparent" }} aria-hidden="true" />
                <input id="look-hex" value={text} spellCheck={false} autoComplete="off" maxLength={7}
                  aria-invalid={!main} aria-describedby={main ? undefined : "look-hex-bad"}
                  onChange={(e) => edit(() => setText(e.target.value))} />
              </span>
              {!main && <p id="look-hex-bad" className="biz-fail">{t("business-look.bad_hex")}</p>}
            </div>
          </div>
          <div className="look-preview">
            <p className="look-preview-h">{t("business-look.preview")} <span>{t("business-look.preview_note")}</span></p>
            <Preview pack={pack} roles={preview.roles} />
            {preview.error && main && <p className="biz-fail" role="alert">{t("business-look.preview_failed", { reason: preview.error })}</p>}
          </div>
        </div>
      </Section>

      <div className="look-save">
        <button type="button" className="btn-primary" disabled={!main || !changed || busy} onClick={save}>
          {t(busy ? "business-look.saving" : "business-look.save")}
        </button>
        <p className="biz-note-s" role="status">{justSaved ? t("business-look.saved") : t("business-look.only_new")}</p>
      </div>
      <Fail text={error} />
    </div>
  );
}

/** The Look tab: the style pack and main colour this business's new products are built from. */
export function LookTab({ businessId }: { businessId: number }) {
  const packs = useLive<StylePack[]>("/styles");
  const look = useLive<BusinessLook>(`/businesses/${businessId}/look`);
  return (
    <Loaded ready={!!packs.data && !!look.data} error={packs.error ?? look.error} retry={() => { packs.reload(); look.reload(); }}>
      {packs.data && look.data && <LookForm businessId={businessId} packs={packs.data} initial={look.data} />}
    </Loaded>
  );
}
