import { t } from "../i18n";
import { Chip } from "./kit";
import { ItemLink } from "./panel";
import { shortDate } from "./ui";
import "./rules.css";

export const RULES_ANCHOR = "places-rules";

/** The list of places and their rules, on the business's Numbers tab. */
export const rulesHref = (businessId: number) => `/business?b=${businessId}&tab=numbers#${RULES_ANCHOR}`;

/** A date with no time ("2026-11-16") as the day it names: `new Date("2026-11-16")` is midnight UTC, a day early west of it. */
export const dayOf = (iso: string) => {
  const [y, m, d] = iso.split("-").map(Number);
  return y && m && d ? shortDate(new Date(y, m - 1, d)) : iso;
};

/**
 * What a post card says about its place's rules, as the server read them today: checked (with the day), unchecked (with
 * why), or the owner's own place. A card from before the field says nothing. The state is words, so colour never carries it.
 */
export function RulesMark({ card, businessId }: { card: Record<string, any>; businessId: number | null }) {
  const state = card.rules_state;
  if (state === "own") return <p className="rules-own muted small">{t("business-rules.card.own")}</p>;
  if (state === "checked") {
    return (
      <div className="rules-mark">
        <p className="small">
          <Chip tone="ok">{card.rules_checked_on ? t("business-rules.checked_dated", { date: dayOf(card.rules_checked_on) }) : t("term.rules_checked")}</Chip>
        </p>
        {card.rules_written_before && <p className="small">{card.rules_written_before}</p>}
      </div>
    );
  }
  if (state !== "unchecked") return null;
  return (
    <div className="rules-mark">
      <p className="small">
        <Chip tone="warn">{t("term.rules_unchecked")}</Chip>
        {card.rules_unchecked_text && <span className="rules-why">{card.rules_unchecked_text}</span>}
        {businessId !== null && <> <ItemLink to={rulesHref(businessId)}>{t("business-rules.card.see")}</ItemLink></>}
      </p>
      {card.rules_written_before && <p className="muted small">{card.rules_written_before}</p>}
    </div>
  );
}
