import { FormEvent, useId, useRef, useState } from "react";
import { api } from "../api";
import { Icon } from "../components/icons";
import { CopyButton } from "../components/panel";
import { settleFocus } from "../components/ui";
import { lookup, t } from "../i18n";
import { Fail, useDo } from "./parts";
import type { Door, Tracking } from "./types";

const KINDS = ["site", "store", "map", "package"] as const;
const MAX_PLACES = 8;
const MAX_URL = 500;
export const PLACES_ANCHOR = "places";

const kindName = (kind: string) => lookup(`business-tracking.door.${kind}`) ?? kind;
const web = (u: string) => (/^https?:\/\//.test(u) ? u : null);

/** `""` is the kind's standard tag name, `none` a place that keeps no tag; anything else is the query name itself. */
type Draft = { id: number; kind: string; url: string; param: string };
const PARAMS = ["ref", "utm_source"] as const;

const toDraft = (d: Door, id: number): Draft => ({ id, kind: d.kind, url: d.url, param: d.param ?? "none" });
const toBody = (d: Draft) => ({ kind: d.kind, url: d.url.trim(), ...(d.param === "" ? {} : { param: d.param === "none" ? null : d.param }) });

/** The editor: every stored place at once, saved as one list. The repository is not in it, since it is not stored. */
function PlacesForm({ stored, close, saved, businessId }: { stored: Door[]; close: () => void; saved: () => void; businessId: number }) {
  const uid = useId();
  const next = useRef(stored.length);
  const blank = (): Draft => ({ id: next.current++, kind: "site", url: "", param: "" });
  const [rows, setRows] = useState<Draft[]>(() => (stored.length ? stored.map(toDraft) : [blank()]));
  const d = useDo();
  const set = (id: number, patch: Partial<Draft>) => setRows(rows.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (d.busy) return;
    const filled = rows.filter((r) => r.url.trim());
    if (await d.run(() => api(`/businesses/${businessId}/doors`, { method: "PUT", body: JSON.stringify({ doors: filled.map(toBody) }) }))) saved();
  };
  return (
    <form className="tk-places-form" onSubmit={submit} aria-busy={d.busy} aria-label={t("business-tracking.places.title")}
      onKeyDown={(e) => e.key === "Escape" && !d.busy && (e.stopPropagation(), close())}>
      {rows.map((r, i) => (
        <fieldset key={r.id} className="tk-place-row">
          <legend>{t("business-tracking.places.group", { n: i + 1 })}</legend>
          <div className="field">
            <label htmlFor={`${uid}-${r.id}-k`}>{t("business-tracking.places.kind")}</label>
            <select id={`${uid}-${r.id}-k`} value={r.kind} autoFocus={i === 0} onChange={(e) => set(r.id, { kind: e.target.value })}>
              {KINDS.map((k) => <option key={k} value={k}>{kindName(k)}</option>)}
            </select>
          </div>
          <div className="field tk-url">
            <label htmlFor={`${uid}-${r.id}-u`}>{t("business-tracking.places.url")}</label>
            <input id={`${uid}-${r.id}-u`} type="url" inputMode="url" autoComplete="off" placeholder="https://" maxLength={MAX_URL} value={r.url}
              onChange={(e) => set(r.id, { url: e.target.value })} />
          </div>
          <div className="field">
            <label htmlFor={`${uid}-${r.id}-p`}>{t("business-tracking.places.param_label")}</label>
            <select id={`${uid}-${r.id}-p`} value={r.param} aria-describedby={`${uid}-ph`} onChange={(e) => set(r.id, { param: e.target.value })}>
              <option value="">{t("business-tracking.places.param_std")}</option>
              {PARAMS.map((p) => <option key={p} value={p}>{p}</option>)}
              <option value="none">{t("business-tracking.places.param_none")}</option>
            </select>
          </div>
          <button type="button" className="link small tk-remove" aria-label={t("business-tracking.places.remove", { n: i + 1 })}
            onClick={() => setRows(rows.filter((x) => x.id !== r.id))}>{t("business-tracking.places.remove_short")}</button>
        </fieldset>
      ))}
      <p id={`${uid}-ph`} className="tk-help">{t("business-tracking.places.param_help")}</p>
      {rows.length < MAX_PLACES && (
        <button type="button" className="btn-ghost small tk-add" onClick={() => setRows([...rows, blank()])}><Icon name="plus" size={14} />{t("business-tracking.places.add")}</button>
      )}
      <Fail text={d.error} />
      <div className="row wrap">
        <button className="btn-primary" type="submit" disabled={d.busy}>{t(d.busy ? "business-tracking.places.saving" : "business-tracking.places.save")}</button>
        <button type="button" className="btn-ghost" disabled={d.busy} onClick={close}>{t("common.cancel")}</button>
      </div>
    </form>
  );
}

/** Your places: where the post cards link to. The repository is read from the business and cannot be edited here. */
export function Places({ businessId, data, reload }: { businessId: number; data: Tracking; reload: () => void }) {
  const [editing, setEditing] = useState(false);
  const edit = useRef<HTMLButtonElement>(null);
  const offer = useDo();
  const stored = data.doors.filter((x) => !x.derived);
  const close = () => {
    setEditing(false);
    settleFocus(edit.current);
  };
  const useOffer = async () => {
    const o = data.homepage_offer;
    if (!o) return;
    if (await offer.run(() => api(`/businesses/${businessId}/doors`, {
      method: "PUT", body: JSON.stringify({ doors: [...stored.map((x) => ({ kind: x.kind, url: x.url, param: x.param })), { kind: "site", url: o.url }] }),
    }))) reload();
  };
  return (
    <div className="tk-places" id={PLACES_ANCHOR}>
      <h3>{t("business-tracking.places.title")}</h3>
      <p className="tk-help">{t("business-tracking.places.lead")}</p>
      {!data.doors.length && <p className="tk-help">{t("business-tracking.places.none")}</p>}
      {!!data.doors.length && (
        <ul className="tk-door-list">
          {data.doors.map((x) => (
            <li key={`${x.kind}-${x.url}`}>
              <p className="tk-door-h"><b>{kindName(x.kind)}</b>
                {web(x.url) ? <a href={web(x.url)!} target="_blank" rel="noopener noreferrer" className="tk-url-t">{x.url}</a> : <span className="tk-url-t">{x.url}</span>}
              </p>
              <p className="tk-sub">
                {x.derived ? t("business-tracking.places.derived")
                  : x.param ? t("business-tracking.places.param", { param: x.param }) : t("business-tracking.places.no_param")}
              </p>
            </li>
          ))}
        </ul>
      )}
      {data.homepage_offer && !editing && (
        <div className="tk-offer">
          <p>{t("business-tracking.places.offer", { host: data.homepage_offer.host })}</p>
          <button type="button" className="btn-ghost small" disabled={offer.busy} onClick={useOffer}>{t("business-tracking.places.offer_use", { host: data.homepage_offer.host })}</button>
          <Fail text={offer.error} />
        </div>
      )}
      {editing ? (
        <PlacesForm stored={stored} businessId={businessId} close={close} saved={() => { reload(); close(); }} />
      ) : (
        <button ref={edit} type="button" className="btn-ghost small" onClick={() => setEditing(true)}>
          {t(stored.length ? "business-tracking.places.edit" : "business-tracking.places.add")}
        </button>
      )}
    </div>
  );
}

/** The question for the owner's own sign-up form, in the owner's language, and the answers to offer: both copyable. Folded once there is any answer. */
export function SignupAsk({ signup, answered }: { signup: Tracking["signup"]; answered: boolean }) {
  return (
    <details className="tk-ask" open={!answered}>
      <summary>{signup.title}</summary>
      <p className="tk-help">{t("business-tracking.signup.help")}</p>
      <div className="tk-ask-row">
        <p className="tk-ask-k">{t("business-tracking.signup.question")}</p>
        <p className="tk-ask-v">{signup.question}</p>
        <CopyButton text={signup.question} what={t("business-tracking.signup.copy_question")} />
      </div>
      <div className="tk-ask-row">
        <p className="tk-ask-k">{t("business-tracking.signup.options")}</p>
        <ul className="tk-ask-v">{signup.options.map((o) => <li key={o}>{o}</li>)}</ul>
        <CopyButton text={signup.options.join("\n")} what={t("business-tracking.signup.copy_options")} />
      </div>
      <p className="tk-help">{signup.note}</p>
    </details>
  );
}
