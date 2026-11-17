import { ReactNode, createContext, useContext, useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { post, useLive } from "../api";
import { Box, Chip, Section, Tone } from "../components/kit";
import { CopyButton } from "../components/panel";
import { Skeleton, announce, landOn, num, shortDate } from "../components/ui";
import { locale, lookup, t } from "../i18n";
import { CustomersLink } from "./Customers";
import { PLACES_ANCHOR } from "./Places";
import { Clamp, Fail, useBizTabs, useDo } from "./parts";
import type { Kit, KitPart, KitText, KitToday } from "./types";
import "./first-kit.css";

/** True when every written text stands on a guess: the section then says so once, not under each text. */
const GuessSaidOnce = createContext(false);

export const KIT_ANCHOR = "first-ten-kit";
/** The tables whose changes move the kit: the job that writes it, the activity line it leaves, the people it is about. */
const TABLES = ["jobs", "activity_events", "people", "owner_tasks", "businesses"];
const POLL_MS = 4000;
const PROFILE_SLOTS = ["line", "short", "about"] as const;
const INVITE_SLOTS = ["known", "problem"] as const;
const STAGE_TONE: Record<string, Tone> = { found: "muted", contacted: "run", talked: "run", first_value: "ok", repeat: "ok", dropped: "muted" };
const web = (u: string | null | undefined) => (u && /^https?:\/\//.test(u) ? u : null);

// The server sends codes; a code this console has no words for shows as sent.
const stageText = (code: string) => lookup(`business.stage.${code}`) ?? code;
const sourceText = (code: string) => lookup(`business.source.${code}`) ?? code;
const platformText = (code: string) => lookup(`business.platform.${code}`) ?? code;

/** The name of a language in the console's own, so a message written in Korean for an English console says so. */
function languageName(code: string): string {
  try {
    return new Intl.DisplayNames(locale(), { type: "language" }).of(code) ?? code;
  } catch {
    return code;
  }
}

type Ask = (key: string, body: { parts: KitPart[]; person_id?: number }) => void;
type Ctl = { can: boolean; busy: boolean; ask: Ask; failed: (key: string) => string | null };

/** What stands under a text: how long it is, when it was written, and anything that weakens it. */
function Notes({ text, quotes }: { text: KitText; quotes?: boolean }) {
  const n = text.basis_view.quotes.length;
  const other = text.lang !== locale().slice(0, 2);
  const saidOnce = useContext(GuessSaidOnce);
  return (
    <>
      <p className="kt-meta">
        <span className="num">{t("business-kit.count", { n: num(text.chars), max: num(text.limit) })}</span>
        <span>{t("business-kit.made", { date: shortDate(text.made_at) })}</span>
        {other && <span>{t("business-kit.in_lang", { lang: languageName(text.lang) })}</span>}
      </p>
      {text.stale && <p className="kt-note kt-stale" role="note">{t("business-kit.stale")}</p>}
      {text.guess && !saidOnce && (
        <p className="kt-note kt-guess" role="note">{t("business-kit.guess")} <CustomersLink>{t("business-profile.check")}</CustomersLink></p>
      )}
      {quotes && (n > 0 ? (
        <details className="kt-from">
          <summary>{t("business-kit.from", { n })}</summary>
          <ul>{text.basis_view.quotes.map((q, i) => <li key={i}>“{q}”</li>)}</ul>
        </details>
      ) : <p className="kt-from-none">{t("business-kit.from_none")}</p>)}
    </>
  );
}

/** A refused text is shown as not written, with the reason in the owner's language and no body. */
function Refused({ text, children }: { text: KitText; children?: ReactNode }) {
  return (
    <div className="kt-refused" role="note">
      <p><b>{t("business-kit.refused")}</b>{text.refused_text}</p>
      {children}
    </div>
  );
}

function Written({ text, what, quotes }: { text: KitText; what: string; quotes?: boolean }) {
  if (text.body === null) return <Refused text={text} />;
  return (
    <>
      <div className="kt-textrow">
        <pre className="kt-text" lang={text.lang}>{text.body}</pre>
        <CopyButton text={text.body} what={what} />
      </div>
      {text.last_refused && (
        <p className="kt-note kt-failed" role="note">{t("business-kit.again_failed", { reason: text.last_refused.refused_text })}</p>
      )}
      <Notes text={text} quotes={quotes} />
    </>
  );
}

/** One person to reach today: why today, their latest words, the draft with Copy, and the move to contacted. */
function Row({ row, kit, ctl, reload }: { row: KitToday; kit: Kit; ctl: Ctl; reload: () => void }) {
  const p = row.person;
  const d = useDo();
  const name = p.display_name && p.display_name !== p.handle ? `${p.display_name} (${p.handle})` : p.handle;
  const found = web(p.where_found_url);
  const latest = p.quotes.length ? p.quotes[p.quotes.length - 1] : null;
  const key = `m${p.id}`;
  const draft = row.draft;
  const canReach = !!p.next_stages?.includes("contacted");
  const needsDraft = kit.prepared && !draft && row.no_draft_reason === "not_written";
  const reached = async () => {
    if (await d.run(() => post(`/people/${p.id}/stage`, { stage: "contacted" }))) {
      announce(t("business-kit.reached_done", { name, stage: stageText("contacted") }));
      reload();
    }
  };
  const write = (again: boolean) => (
    <button type="button" className="btn-ghost small" disabled={!ctl.can || ctl.busy}
      aria-label={again ? t("business-kit.rewrite_for", { what: t("business-kit.draft_for", { name }) }) : t("business-kit.write_draft_for", { name })}
      onClick={() => ctl.ask(key, { parts: ["message"], person_id: p.id })}>
      {t(again ? "business-kit.rewrite" : "business-kit.write_draft")}
    </button>
  );
  return (
    <li className="kt-person">
      <p className="kt-person-t">
        <Chip tone={STAGE_TONE[p.stage] ?? "muted"}>{stageText(p.stage)}</Chip>
        <span className="kt-name">{name}</span>
      </p>
      <p className="kt-why">
        {row.reason_text} · {sourceText(p.source)} · {platformText(p.platform)}
        {found && <> · <a href={found} target="_blank" rel="noopener noreferrer">{t("business.people.found_post")}</a></>}
      </p>
      {latest && <p className="kt-quote"><span className="k">{t("business.people.latest_quote")}</span> <Clamp text={`“${latest.text}”`} /></p>}
      {p.next_action && <p className="kt-next"><span className="k">{t("business.people.next")}</span> <Clamp text={p.next_action} /></p>}

      {draft ? (
        <div className="kt-draft">
          <p className="kt-label">{t("business-kit.draft")}</p>
          {draft.body === null ? (
            <Refused text={draft}>{write(true)}</Refused>
          ) : (
            <Written text={draft} what={t("business-kit.draft_for", { name })} quotes />
          )}
        </div>
      ) : row.no_draft_reason === "public_first" ? (
        <p className="kt-nodraft">{row.no_draft_text}</p>
      ) : needsDraft && <p className="kt-nodraft">{row.no_draft_text}</p>}
      <Fail text={ctl.failed(key)} />
      {(canReach || needsDraft || (draft && draft.body !== null)) && (
        <div className="kt-row-a">
          {canReach && (
            <button type="button" className="btn-ghost small" disabled={d.busy} aria-label={t("business-kit.reached_for", { name })} onClick={reached}>
              {t(d.busy ? "business-kit.reached_saving" : "business-kit.reached")}
            </button>
          )}
          {needsDraft && write(false)}
          {draft && draft.body !== null && write(true)}
        </div>
      )}
      <Fail text={d.error} />
    </li>
  );
}

/** No one to reach today: add someone, or ask the company to look. Both are the actions the People tab already has. */
function Nobody({ businessId, plan }: { businessId: number; plan: boolean }) {
  const d = useDo();
  const [said, setSaid] = useState<string | null>(null);
  const find = async () => {
    setSaid(null);
    let out: { queued: Record<string, boolean> } | null = null;
    if (await d.run(async () => { out = await post(`/businesses/${businessId}/people/find`, {}); })) {
      setSaid(t(Object.values(out!.queued).some(Boolean) ? "business-kit.find_queued" : "business-kit.find_today"));
    }
  };
  return (
    <div className="kt-nobody">
      <p className="kt-nobody-t">{t("business-kit.today_empty")}</p>
      <p className="kt-help">{t("business-kit.today_empty_body")}</p>
      <div className="row wrap">
        <button type="button" className="btn-ghost" onClick={() => landOn("p-handle")}>{t("business-kit.add_person")}</button>
        <button type="button" className="btn-ghost" disabled={d.busy || !plan} onClick={find}>{t(d.busy ? "business-kit.finding" : "business-kit.find")}</button>
      </div>
      <Fail text={d.error} />
      {said && <p className="kt-help" role="status">{said}</p>}
    </div>
  );
}

/** A group of texts of one part. Writing is by part, so one button writes all of its texts again. */
function Group({ id, part, title, help, texts, ctl, extra, slotName, children }: {
  id: string; part: "profile" | "invite"; title: string; help: string; texts: [string, KitText | null][]; ctl: Ctl;
  extra?: ReactNode; slotName: (slot: string) => string; children?: ReactNode;
}) {
  const any = texts.some(([, x]) => x);
  const needs = texts.some(([, x]) => x && (x.refused || x.stale));
  const key = `p-${part}`;
  const label = t(any ? "business-kit.rewrite" : "business-kit.write");
  return (
    <div className="kt-group" aria-labelledby={`${id}-h`} role="group">
      <div className="kt-group-h">
        <h3 id={`${id}-h`}>{title}</h3>
        <button type="button" className={needs ? "btn-primary small" : "btn-ghost small"} disabled={!ctl.can || ctl.busy}
          aria-label={t(any ? "business-kit.rewrite_for" : "business-kit.write_for", { what: title })}
          onClick={() => ctl.ask(key, { parts: [part] })}>{label}</button>
      </div>
      <p className="kt-help">{help}</p>
      <Fail text={ctl.failed(key)} />
      {extra}
      <ul className="kt-texts">
        {texts.map(([slot, x]) => (
          <li key={slot} className="kt-item">
            <h4>{slotName(slot)}</h4>
            {x ? <Written text={x} what={t("business-kit.slot_of", { part: title, slot: slotName(slot) })} />
              : <p className="kt-unwritten">{t("business-kit.unwritten")}</p>}
          </li>
        ))}
      </ul>
      {children}
    </div>
  );
}

function Body({ kit, businessId, ctl, reloadAll }: { kit: Kit; businessId: number; ctl: Ctl; reloadAll: () => void }) {
  const { href } = useBizTabs();
  const noPlace = kit.link.state === "no_place";
  const placeLink = <Link to={href("numbers", PLACES_ANCHOR)}>{t("business-kit.link_go")}</Link>;
  const written = [...kit.today.map((r) => r.draft), ...Object.values(kit.profile), ...Object.values(kit.invite)]
    .filter((x): x is KitText => !!x && x.body !== null);
  const allGuess = written.length > 1 && written.every((x) => x.guess);
  return (
    <GuessSaidOnce.Provider value={allGuess}>
      {allGuess && (
        <p className="kt-note kt-guess" role="note">{t("business-kit.guess_all")} <CustomersLink>{t("business-profile.check")}</CustomersLink></p>
      )}
      {kit.writing && <p className="kt-writing-note" role="status">{kit.job?.waiting_text ?? t("business-kit.writing_note")}</p>}
      {!kit.writing && <Fail text={kit.job?.error_text ?? null} />}
      {!kit.writing && !kit.can_write && (
        <p className="kt-note" role="note">{t("business-kit.cannot")} <Link to={href("brief")}>{t("business-kit.cannot_go")}</Link></p>
      )}
      {!kit.prepared && (
        <>
          <p className="kt-help">{t("business-kit.first_empty")}</p>
          {noPlace && <p className="kt-help">{t("business-kit.link_none")} {placeLink}</p>}
        </>
      )}

      <div className="kt-block">
        <h3>{t("business-kit.today")}</h3>
        {kit.today.length ? (
          <Box pad={false}>
            <ul className="kt-people">
              {kit.today.map((r) => <Row key={r.person.id} row={r} kit={kit} ctl={ctl} reload={reloadAll} />)}
            </ul>
          </Box>
        ) : <Box><Nobody businessId={businessId} plan={kit.can_write || kit.writing} /></Box>}
      </div>

      {kit.prepared && (
        <div className="kt-pair">
          <div className="kt-block">
            <Box>
              <Group id="kt-profile" part="profile" title={t("business-kit.profile")} help={t("business-kit.profile_help")} ctl={ctl}
                texts={PROFILE_SLOTS.map((s) => [s, kit.profile[s]])} slotName={(s) => lookup(`business-kit.slot.profile.${s}`) ?? s}
                extra={!kit.owner_said && <p className="kt-note" role="note">{t("business-kit.profile_product_only")}</p>} />
            </Box>
          </div>
          <div className="kt-block">
            <Box>
              <Group id="kt-invite" part="invite" title={t("business-kit.invite")} help={t("business-kit.invite_help")} ctl={ctl}
                texts={INVITE_SLOTS.map((s) => [s, kit.invite[s]])} slotName={(s) => lookup(`business-kit.slot.invite.${s}`) ?? s}>
                <p className="kt-link">
                  {noPlace ? <>{t("business-kit.link_none")} {placeLink}</>
                    : <>{t("business-kit.link_ready")} <span className="code">{kit.link.url}</span></>}
                </p>
              </Group>
            </Box>
          </div>
        </div>
      )}
    </GuessSaidOnce.Provider>
  );
}

/**
 * The First 10 kit at the top of the People tab: who to reach today with a draft for each, wording for the owner's
 * profile and for invites, each with Copy. It prepares and sends nothing. It is written on the owner's click and the
 * server says while it writes, so the texts already here stay readable; once ten people got value it folds to one line.
 */
export function FirstTenKit({ businessId, reloadPeople }: { businessId: number; reloadPeople: () => void }) {
  const { data, error, reload } = useLive<Kit>(`/businesses/${businessId}/kit`, TABLES);
  const { hash } = useLocation();
  const d = useDo();
  const [target, setTarget] = useState<string | null>(null);
  const [hold, setHold] = useState(false);
  const [open, setOpen] = useState<boolean | null>(null);
  const was = useRef<{ writing: boolean; at: string | null } | null>(null);

  // the request is queued once the server answers; until the next reading says `writing` the buttons stay shut
  useEffect(() => setHold(false), [data]);
  useEffect(() => {
    if (!data?.writing) return;
    const id = window.setInterval(reload, POLL_MS);
    return () => window.clearInterval(id);
  }, [data?.writing, reload]);
  // A write that ends is announced for what it did: failed, or updated only when it stored a text (`prepared_at` moved).
  useEffect(() => {
    if (!data) return;
    const before = was.current;
    if (before?.writing && !data.writing) {
      if (data.job?.error_text) announce(data.job.error_text, "bad");
      else if (data.prepared_at !== before.at) announce(t("business-kit.written"));
    }
    was.current = { writing: data.writing, at: data.writing && before ? before.at : data.prepared_at };
  }, [data]);

  const title = t("term.first_ten_kit");
  if (!data) {
    return (
      <Section id={KIT_ANCHOR} title={title} className="kt">
        {error ? (
          <p className="quiet-error" role="alert">{t("business-kit.load_failed")} <button type="button" className="link" onClick={reload}>{t("common.retry")}</button></p>
        ) : <Skeleton rows={3} />}
      </Section>
    );
  }

  const folded = data.milestone.have >= data.milestone.n;
  const shut = data.writing || hold || d.busy;
  const ctl: Ctl = {
    can: data.can_write, busy: shut,
    failed: (key) => (target === key ? d.error : null),
    ask: async (key, body) => {
      setTarget(key);
      if (await d.run(() => post(`/businesses/${businessId}/kit/prepare`, body))) setHold(true);
      reload();
    },
  };
  const reloadAll = () => {
    reload();
    reloadPeople();
  };
  const progress = t("business-kit.progress", { have: num(data.milestone.have), n: num(data.milestone.n) });
  const main = !data.prepared && !data.writing ? (
    <button type="button" className="btn-primary" disabled={!data.can_write || shut} onClick={() => ctl.ask("main", { parts: ["message", "profile", "invite"] })}>
      {t("business-kit.prepare")}
    </button>
  ) : data.writing || hold ? <span className="kt-writing" role="status">{t("business-kit.writing")}</span> : null;

  const body = (
    <>
      <Fail text={ctl.failed("main")} />
      {error && <p className="quiet-error" role="alert">{t("business-kit.stale_error")} <button type="button" className="link" onClick={reload}>{t("common.retry")}</button></p>}
      <Body kit={data} businessId={businessId} ctl={ctl} reloadAll={reloadAll} />
    </>
  );
  return (
    <Section id={KIT_ANCHOR} title={title} className="kt" actions={main}>
      {folded ? (
        <details className="kt-fold" open={open ?? hash === `#${KIT_ANCHOR}`} onToggle={(e) => setOpen(e.currentTarget.open)}>
          <summary><b className="num">{progress}</b> <span>{t("business-kit.folded_note")}</span></summary>
          <p className="kt-lead">{t("business-kit.lead")}</p>
          {body}
        </details>
      ) : (
        <>
          <p className="kt-lead"><b className="num">{progress}</b> <span>{t("business-kit.lead")}</span></p>
          {body}
        </>
      )}
    </Section>
  );
}
