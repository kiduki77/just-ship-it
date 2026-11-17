import { useState } from "react";
import { Link } from "react-router-dom";
import { Row, useLive } from "../api";
import { Meter } from "../components/kit";
import { locale, t } from "../i18n";
import { useDirtyWhen } from "./dirty";
import { Fields, SaveBar, Section, pctText, usdText } from "./parts";
import { SettingsData, UsageView, UsageWindow } from "./types";
import { Saver, useSave } from "./useSave";

const SUBSCRIPTIONS = ["Claude Max 20x", "Claude Max 5x", "Claude Pro"];
const NUMBER = /^\d+(\.\d+)?$/;

const round1 = (n: number) => Math.round(n * 10) / 10;
const cents = (n: number) => Math.round(n * 100) / 100;

/** Empty text is null (no value), a plain positive number is itself, anything else is NaN. */
const parse = (text: string): number | null => (text.trim() === "" ? null : NUMBER.test(text.trim()) ? Number(text) : NaN);

const shareText = (cap: number | null, capacity: number | null) =>
  cap !== null && capacity ? String(round1((cap / capacity) * 100)) : "";

/** The cap in USD for a percent the owner typed: null for no cap, undefined when the text cannot be a cap. A percent
 *  the owner did not touch keeps the stored amount, so saving never nudges a cap by a rounding step. */
function capOf(text: string, saved: number | null, capacity: number | null, savedCapacity: number | null): number | null | undefined {
  const p = parse(text);
  if (p === null) return null;
  if (!capacity || Number.isNaN(p) || p <= 0) return undefined;
  const untouched = capacity === savedCapacity && text === shareText(saved, savedCapacity);
  return untouched && saved !== null ? saved : cents((capacity * p) / 100);
}

function minimumCap({ owner, background }: SettingsData["usage_needs"], reservePct: number) {
  const backgroundUsd = (background.usd_eq * 100) / (100 - reservePct);
  const [what, usd] = backgroundUsd >= owner.usd_eq ? [background.what, backgroundUsd] : [owner.what, owner.usd_eq];
  return { what, usd: Math.ceil(usd * 100 - 1e-9) / 100 };
}

function when(iso: string, tz: string): string {
  return new Intl.DateTimeFormat(locale(), { timeZone: tz, month: "long", day: "numeric", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false })
    .format(new Date(iso));
}

function Reading({ today, win, capacity, tz }: { today: boolean; win: UsageWindow; capacity: number | null; tz: string }) {
  const used = win.used_usd_eq + win.reserved_usd_eq;
  const next = t(today ? "settings.usage.day_resets" : "settings.usage.week_resets", { when: when(win.resets_at, tz) });
  const capText = (cap: string | null) => t("settings.usage.cap", { cap: cap ?? t("settings.usage.no_cap"), next });
  if (!capacity) {
    return (
      <li>
        <span className="strong">{t(today ? "settings.usage.day_amount" : "settings.usage.week_amount", { amount: usdText(used) })}</span>
        <span className="muted">{capText(win.cap_usd_eq === null ? null : usdText(win.cap_usd_eq))}</span>
      </li>
    );
  }
  const share = pctText(round1((used / capacity) * 100));
  const capShare = win.cap_usd_eq === null ? null : pctText(round1((win.cap_usd_eq / capacity) * 100));
  return (
    <li>
      <span className="strong">{t(today ? "settings.usage.day_share" : "settings.usage.week_share", { share })}</span>
      <span className="muted">{capText(capShare)}</span>
      {win.cap_usd_eq !== null && (
        <span className="set-meter">
          <Meter value={win.used_pct ?? 0} max={100} label={t(today ? "settings.usage.day_meter" : "settings.usage.week_meter")} />
          <span className="small muted num">{t("settings.usage.of_cap", { pct: pctText(round1(win.used_pct ?? 0)) })}</span>
        </span>
      )}
    </li>
  );
}

function UsageForm({ data, saver, lastWeek }: { data: SettingsData; saver: Saver; lastWeek: number | null }) {
  const u = data.effective.usage;
  const [subscription, setSubscription] = useState(u.subscription);
  const [capacityText, setCapacityText] = useState(u.capacity_week_usd_eq?.toString() ?? "");
  const [dayText, setDayText] = useState(shareText(u.day_usd_eq, u.capacity_week_usd_eq));
  const [weekText, setWeekText] = useState(shareText(u.week_usd_eq, u.capacity_week_usd_eq));
  const [reserveText, setReserveText] = useState(String(u.owner_reserve_pct));
  useDirtyWhen(subscription !== u.subscription || capacityText !== (u.capacity_week_usd_eq?.toString() ?? "")
    || dayText !== shareText(u.day_usd_eq, u.capacity_week_usd_eq) || weekText !== shareText(u.week_usd_eq, u.capacity_week_usd_eq)
    || reserveText !== String(u.owner_reserve_pct));

  const parsedCapacity = parse(capacityText);
  const capacityBad = parsedCapacity !== null && (Number.isNaN(parsedCapacity) || parsedCapacity <= 0);
  const capacity = capacityBad ? null : parsedCapacity;
  const reserve = parse(reserveText);
  const reserveBad = reserve === null || !Number.isInteger(reserve) || reserve > 50;
  const reserveNow = reserveBad ? u.owner_reserve_pct : (reserve as number);
  const minimum = minimumCap(data.usage_needs, reserveNow);

  const day = capOf(dayText, u.day_usd_eq, capacity, u.capacity_week_usd_eq);
  const week = capOf(weekText, u.week_usd_eq, capacity, u.capacity_week_usd_eq);
  const small = (cap: number | null | undefined) => typeof cap === "number" && cap < minimum.usd;
  const minShare = capacity ? Math.ceil((minimum.usd / capacity) * 1000) / 10 : null;
  const invalid = [capacityBad, reserveBad, day === undefined, week === undefined, small(day), small(week)].filter(Boolean).length;
  const backgroundLimit = 100 - reserveNow;
  const currentCaps = [u.day_usd_eq !== null && t("settings.usage.caps_day", { amount: usdText(u.day_usd_eq) }),
    u.week_usd_eq !== null && t("settings.usage.caps_week", { amount: usdText(u.week_usd_eq) })].filter(Boolean).join(", ");

  const sizeNote = (cap: number | null | undefined) =>
    small(cap) ? (
      <span className="small bad-t" role="alert">
        {t("settings.usage.too_small", {
          amount: usdText(minimum.usd), what: minimum.what,
          share: minShare !== null ? t("settings.usage.too_small_share", { share: pctText(minShare) }) : "",
        })}
      </span>
    ) : null;

  const amount = (cap: number | null | undefined) =>
    typeof cap === "number" ? <span className="small muted">{t("settings.usage.about", { amount: usdText(cap) })}</span> : null;

  const save = () =>
    saver.save({
      subscription: subscription.trim(),
      capacity_week_usd_eq: capacity,
      day_usd_eq: capacity ? day : u.day_usd_eq,
      week_usd_eq: capacity ? week : u.week_usd_eq,
      owner_reserve_pct: reserve,
    });

  return (
    <Fields saver={saver} label={t("settings.usage.form")} onSubmit={save}>
      <div className="set-grid">
        <div className="field">
          <label htmlFor="us-sub">{t("settings.usage.subscription")}</label>
          <input id="us-sub" list="us-subs" value={subscription} maxLength={80} autoComplete="off" onChange={(e) => setSubscription(e.target.value)} />
          <datalist id="us-subs">{SUBSCRIPTIONS.map((s) => <option key={s} value={s} />)}</datalist>
        </div>
        <div className="field">
          <label htmlFor="us-cap">{t("settings.usage.capacity")} <span className="muted">{t("settings.usage.capacity_hint")}</span></label>
          <div className="set-unit">
            <input id="us-cap" inputMode="decimal" value={capacityText} aria-invalid={capacityBad} aria-describedby="us-cap-h" onChange={(e) => setCapacityText(e.target.value)} />
            <span className="muted">{t("settings.usage.list_price")}</span>
          </div>
        </div>
      </div>
      <p id="us-cap-h" className="small muted set-help">
        {t("settings.usage.capacity_help")}
        {lastWeek !== null && t("settings.usage.last_week", { amount: usdText(lastWeek) })}
        {t("settings.usage.capacity_help_end")}
      </p>
      {capacityBad && <div className="error" role="alert">{t("settings.usage.capacity_bad")}</div>}
      <div className="set-grid">
        <div className="field">
          <label htmlFor="us-day">{t("settings.usage.day_cap")} <span className="muted">{t("settings.usage.cap_hint")}</span></label>
          <div className="set-unit">
            <input id="us-day" inputMode="decimal" value={dayText} disabled={!capacity} placeholder={t("settings.usage.cap_none")} aria-invalid={day === undefined || small(day)}
              onChange={(e) => setDayText(e.target.value)} />
            <span className="muted">%</span>
            {amount(day)}
          </div>
          {sizeNote(day)}
        </div>
        <div className="field">
          <label htmlFor="us-week">{t("settings.usage.week_cap")} <span className="muted">{t("settings.usage.cap_hint")}</span></label>
          <div className="set-unit">
            <input id="us-week" inputMode="decimal" value={weekText} disabled={!capacity} placeholder={t("settings.usage.cap_none")} aria-invalid={week === undefined || small(week)}
              onChange={(e) => setWeekText(e.target.value)} />
            <span className="muted">%</span>
            {amount(week)}
          </div>
          {sizeNote(week)}
        </div>
      </div>
      {!capacity && (
        <p className="small muted set-help">
          {t("settings.usage.no_capacity")}
          {currentCaps && t("settings.usage.caps_now", { caps: currentCaps })}
        </p>
      )}
      <div className="field set-narrow">
        <label htmlFor="us-res">{t("settings.usage.reserve")}</label>
        <div className="set-unit">
          <input id="us-res" inputMode="numeric" value={reserveText} aria-invalid={reserveBad} aria-describedby="us-res-h" onChange={(e) => setReserveText(e.target.value)} />
          <span className="muted">{t("settings.usage.reserve_unit")}</span>
        </div>
        <p id="us-res-h" className="small muted set-help">{t("settings.usage.reserve_help", { pct: backgroundLimit })}</p>
      </div>
      {reserveBad && <div className="error" role="alert">{t("settings.usage.reserve_bad")}</div>}
      <SaveBar saver={saver} invalid={invalid} changed={Object.keys(data.overrides.usage ?? {}).length > 0} />
    </Fields>
  );
}

/** The two ceilings on spending side by side, so the owner never mistakes one for the other (P1-11). */
function TwoLimits({ month }: { month: number | null }) {
  return (
    <dl className="set-limits">
      <div>
        <dt>{t("settings.usage.limits.cap")}</dt>
        <dd>{t("settings.usage.limits.cap_says")}</dd>
      </div>
      <div>
        <dt>{t("settings.usage.limits.month")}</dt>
        <dd>
          {month !== null ? t("settings.usage.limits.month_says", { amount: usdText(month) }) : t("settings.usage.limits.month_none")}{" "}
          <Link to="/audit/costs">{t("settings.usage.limits.month_link")}</Link>
        </dd>
      </div>
    </dl>
  );
}

export function Usage({ data, reload }: { data: SettingsData; reload: () => void }) {
  const saver = useSave("usage", reload);
  const { data: costs } = useLive<{ usage: UsageView; by_day: Row[] }>("/costs?days=7", ["activity_events", "jobs"]);
  const view = costs?.usage ?? null;
  const capacity = data.effective.usage.capacity_week_usd_eq;
  const lastWeek = costs ? costs.by_day.reduce((sum, d) => sum + (d.cost_usd as number), 0) : null;
  return (
    <Section id="set-usage" title={t("settings.section.usage")} lead={t("settings.usage.lead")}>
      <TwoLimits month={data.month_limit_usd ?? data.month?.limit_usd ?? null} />
      {view ? (
        <ul className="set-read plain" aria-label={t("settings.usage.now")}>
          <Reading today={false} win={view.week} capacity={capacity} tz={view.timezone} />
          <Reading today win={view.day} capacity={capacity} tz={view.timezone} />
        </ul>
      ) : (
        <p className="small muted">{t("settings.usage.loading")}</p>
      )}
      <UsageForm key={JSON.stringify(data.effective.usage)} data={data} saver={saver} lastWeek={lastWeek} />
    </Section>
  );
}
