import { FormEvent, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, post } from "../api";
import { cleanName, useBusinessSelection } from "../business";
import { DropZone, useAttachments } from "../components/attach";
import { Fix } from "../components/fix";
import { useWorker } from "../components/frame";
import { Box, PageHead, Section } from "../components/kit";
import { locale, PlainKey, t } from "../i18n";
import { KickoffView, MATERIAL_LIMIT, MAX_TURNS } from "../kickoff/types";
import "./misc.css";
import "./new-business.css";

const MATERIAL_COUNTER_FROM = 18000;

/** What the owner starts from. It picks the fields the form shows and the route it submits to. */
type Mode = "idea" | "product";
const MODES = ["idea", "product"] as const satisfies readonly Mode[];

const IMPORT_STEPS = ["copy", "read", "ask"] as const;
/** Where each thing the owner enters goes, so the form reads as the start of the plan, not a questionnaire. */
const USED_FOR: PlainKey[] = ["kickoff.new.used.name", "kickoff.new.used.material", "kickoff.new.used.strengths"];

function NextSteps({ importing }: { importing: boolean }) {
  return (
    <>
      <ol className="start-steps">
        {importing
          ? IMPORT_STEPS.map((s) => <li key={s}><b>{t(`kickoff.new.next.${s}.t`)}</b> {t(`kickoff.new.next.${s}.b`)}</li>)
          : USED_FOR.map((k) => <li key={k}>{t(k)}</li>)}
      </ol>
      <p className="nb-cycle"><b>{t("common.steps.go.t")}</b> {t("common.steps.go.b")}</p>
    </>
  );
}

const DRAFT_KEY = "ship.newBusiness.draft";
type Draft = { mode: Mode; source: string; name: string; oneLiner: string; material: string };
const EMPTY: Draft = { mode: "idea", source: "", name: "", oneLiner: "", material: "" };

function readDraft(): Draft | null {
  try {
    const raw = sessionStorage.getItem(DRAFT_KEY);
    return raw ? { ...EMPTY, ...JSON.parse(raw) } : null;
  } catch {
    return null;
  }
}

/**
 * The typed fields outlive leaving the form (this router has no leave prompt): they are kept for the tab and come back
 * on return. Files are not kept; the form says to add them again.
 */
function useDraft() {
  const [first] = useState(readDraft);
  const [d, setD] = useState<Draft>(first ?? EMPTY);
  const restored = !!first && Object.entries(first).some(([k, v]) => k !== "mode" && String(v).trim());
  useEffect(() => {
    try {
      if (Object.entries(d).some(([k, v]) => k !== "mode" && String(v).trim())) sessionStorage.setItem(DRAFT_KEY, JSON.stringify(d));
      else sessionStorage.removeItem(DRAFT_KEY);
    } catch {
      /* private mode: nothing is kept */
    }
  }, [d]);
  const set = <K extends keyof Draft>(k: K) => (v: Draft[K]) => setD((x) => ({ ...x, [k]: v }));
  const forget = () => {
    try {
      sessionStorage.removeItem(DRAFT_KEY);
    } catch {
      /* nothing kept */
    }
  };
  return { d, set, restored, forget };
}

const sameName = (a: string, b: string) => a.replace(/\s+/g, "").toLowerCase() === b.replace(/\s+/g, "").toLowerCase();

export default function NewBusiness() {
  const navigate = useNavigate();
  const { select, businesses } = useBusinessSelection();
  const worker = useWorker();
  const { d, set, restored, forget } = useDraft();
  const { mode, source, name, oneLiner, material } = d;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [srcError, setSrcError] = useState<string | null>(null);
  const srcRef = useRef<HTMLInputElement>(null);
  const files = useAttachments();
  const submitted = useRef(false);
  const over = material.length - MATERIAL_LIMIT;
  const importing = mode === "product";
  const missing = importing ? !source.trim() : !name.trim() || !oneLiner.trim();
  const ready = over <= 0 && !files.reading && !missing;
  const why = missing ? t(importing ? "kickoff.new.why.source" : "kickoff.new.why.fields") : files.reading ? t("kickoff.new.why.reading")
    : over > 0 ? t("kickoff.new.why.over") : null;
  const twin = name.trim() ? businesses?.find((b) => sameName(cleanName(b.name), name.trim())) : undefined;

  // Files uploaded for a business that was never started are let go (the server marks them removed, it keeps the row).
  const discardRef = useRef(files.discard);
  discardRef.current = files.discard;
  useEffect(() => () => {
    if (!submitted.current) discardRef.current();
  }, []);

  useEffect(() => {
    if (srcError) srcRef.current?.focus();
  }, [srcError]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    setSrcError(null);
    try {
      const documents = files.kept.map((doc) => doc.id);
      const v = importing
        ? await post<KickoffView>("/businesses/import", {
            source: source.trim(), name: name.trim() || undefined, one_liner: oneLiner.trim() || undefined, material: material.trim() || undefined,
            documents,
          })
        : await post<KickoffView>("/businesses", {
            name: name.trim(), one_liner: oneLiner.trim(), material: material.trim() || undefined, documents,
          });
      submitted.current = true;
      forget();
      select(v.session.business_id);
      navigate(`/kickoff/${v.session.id}`);
    } catch (err: any) {
      // the server refusing the folder is about the folder: it is said under that field, which takes the focus
      if (importing && err instanceof ApiError && err.kind === "rejected") setSrcError(err.message);
      else setError(err.message ?? String(err));
      setBusy(false);
    }
  }

  return (
    <div className="owner-page kui mx nb">
      <PageHead title={t("kickoff.new.title")} sub={t(importing ? "kickoff.new.sub_product" : "kickoff.new.sub", { n: MAX_TURNS })} />
      {worker.down && <p className="nb-worker small" role="status"><Fix text={t("kickoff.new.worker_off")} /></p>}
      <div className="mx-formwrap">
        <Section title={t(importing ? "kickoff.new.section_product" : "kickoff.new.section")}>
          <Box>
            <form className="form mx-form" onSubmit={submit} aria-busy={busy} noValidate>
              <div className="seg mx-mode" role="group" aria-label={t("kickoff.new.mode")}>
                {MODES.map((m) => (
                  <button key={m} type="button" className={mode === m ? "on" : ""} aria-pressed={mode === m} disabled={busy}
                    onClick={() => { set("mode")(m); setSrcError(null); }}>
                    {t(`kickoff.new.mode.${m}`)}
                  </button>
                ))}
              </div>
              {restored && <p className="small muted nb-restored" role="status">{t("kickoff.new.restored")}</p>}
              {importing && (
                <div className="field">
                  <label htmlFor="nb-src">{t("kickoff.new.source")}</label>
                  <input
                    id="nb-src"
                    ref={srcRef}
                    value={source}
                    maxLength={1000}
                    autoComplete="off"
                    spellCheck={false}
                    placeholder="C:/work/my-app"
                    required
                    aria-invalid={!!srcError}
                    aria-describedby={srcError ? "nb-src-e nb-src-h" : "nb-src-h"}
                    onChange={(e) => { set("source")(e.target.value); setSrcError(null); }}
                  />
                  {srcError && <p id="nb-src-e" className="small bad-t nb-field-err" role="alert">{srcError}</p>}
                  <div id="nb-src-h" className="small muted">
                    <p className="nb-hint">{t("kickoff.new.source_needs")}</p>
                    <ul className="nb-needs">
                      <li>{t("kickoff.new.source_need.path")}</li>
                      <li>{t("kickoff.new.source_need.top")}</li>
                      <li>{t("kickoff.new.source_need.commit")}</li>
                    </ul>
                    <p className="nb-hint">{t("kickoff.new.source_hint")}</p>
                  </div>
                </div>
              )}
              <div className="field">
                <label htmlFor="nb-name">{t("kickoff.new.name")}{importing && <> <span className="muted">{t("kickoff.new.name_default")}</span></>}</label>
                <input id="nb-name" value={name} maxLength={200} autoComplete="off" required={!importing} aria-describedby={twin ? "nb-name-twin" : undefined}
                  onChange={(e) => set("name")(e.target.value)} />
                {twin && (
                  <p id="nb-name-twin" className="small warn-t">
                    {t("kickoff.new.twin")} <Link to={`/business?b=${twin.id}`}>{t("kickoff.new.twin_open", { name: cleanName(twin.name) })}</Link>
                  </p>
                )}
              </div>
              <div className="field">
                <label htmlFor="nb-one">
                  {t("kickoff.new.one_liner")} <span className="muted">{importing ? t("kickoff.new.optional") : t("kickoff.new.one_liner_hint")}</span>
                </label>
                <input
                  id="nb-one"
                  value={oneLiner}
                  autoComplete="off"
                  required={!importing}
                  aria-describedby={importing ? "nb-one-h" : undefined}
                  onChange={(e) => set("oneLiner")(e.target.value)}
                />
                {importing && <span id="nb-one-h" className="small muted">{t("kickoff.new.one_liner_product")}</span>}
              </div>
              <div className="field">
                <label htmlFor="nb-mat">{t("kickoff.new.material")} <span className="muted">{t("kickoff.new.optional")}</span></label>
                <textarea
                  id="nb-mat"
                  rows={importing ? 4 : 5}
                  value={material}
                  aria-describedby="nb-mat-n"
                  aria-invalid={over > 0}
                  onChange={(e) => set("material")(e.target.value)}
                />
                <span id="nb-mat-n" className={`small ${over > 0 ? "bad-t" : "muted"}`} role="status">
                  {over > 0
                    ? t("kickoff.new.over", { n: over.toLocaleString(locale()), limit: MATERIAL_LIMIT.toLocaleString(locale()) })
                    : material.length >= MATERIAL_COUNTER_FROM
                      ? t("kickoff.count", { length: material.length.toLocaleString(locale()), limit: MATERIAL_LIMIT.toLocaleString(locale()) })
                      : t("kickoff.new.material_hint")}
                </span>
              </div>
              <div className="field">
                <label htmlFor="nb-files">{t("attach.field")} <span className="muted">{t("kickoff.new.optional")}</span></label>
                <DropZone att={files} id="nb-files" />
              </div>
              {error && <div className="error" role="alert">{error}</div>}
              <div className="row mx-form-foot">
                <button type="submit" className="btn-primary" disabled={!ready || busy} aria-describedby={why ? "nb-why" : undefined}>
                  {importing ? t(busy ? "kickoff.new.importing" : "kickoff.new.import") : t(busy ? "kickoff.new.opening" : "kickoff.new.start")}
                </button>
                <Link className="btn-ghost" to="/" onClick={() => { forget(); files.discard(); }}>{t("common.cancel")}</Link>
                {why && !busy && <span id="nb-why" className="small muted">{why}</span>}
                {busy && importing && <span className="small muted" role="status">{t("kickoff.new.importing_note")}</span>}
              </div>
            </form>
          </Box>
        </Section>
        <Section title={t(importing ? "common.firstrun.then" : "kickoff.new.used_title")} className="mx-aside">
          <Box><NextSteps importing={importing} /></Box>
        </Section>
      </div>
    </div>
  );
}
