import { useLive } from "../api";
import { LineChart } from "../components/charts";
import { Box, Chip, Hint, Section } from "../components/kit";
import { LoadError, Loading, num } from "../components/ui";
import { language, PlainKey, t, tx } from "../i18n";
import { CustomersLink } from "./Customers";
import { Step, Steps } from "./parts";
import { Completion, Goal, Numbers, StageNumber } from "./types";

const WHAT: Record<StageNumber["stage"], PlainKey> = {
  interest: "business.numbers.what.interest",
  first_value: "business.numbers.what.first_value",
  repeat: "business.numbers.what.repeat",
};

export const stageLabel = (stage: StageNumber["stage"]) => t(`business.stage.${stage}`);

const unit = (s: StageNumber) => t(s.stage === "interest" ? "business.numbers.unit.stars" : "business.numbers.unit.people");

/** The goal of the plan that counts this step, if one does. */
type CountedGoal = Goal & { number: number };
const goalOf = (c: Completion, stage: StageNumber["stage"]) =>
  c.goals.find((g): g is CountedGoal => g.measure === stage && g.number !== null);

const HANGUL = /\p{Script=Hangul}/u;
/** The plan's unit, or none when the plan was written in Korean and the console reads in English (a stray Korean unit
 * after an English sentence reads as a bug, the bare number does not). */
export const unitFor = (unit: string) => (language() === "en" && HANGUL.test(unit) ? "" : unit);

/** A count in the plan's own unit: "30 users", a Korean unit with no space; no unit when it is in the other language. */
const counted = (n: number, unit: string) => {
  const u = unitFor(unit);
  return `${num(n)}${/^[A-Za-z]/.test(u) ? " " : ""}${u}`;
};

const whyUnknown = (s: StageNumber) => s.why_unknown ?? t("business.numbers.no_reason");

/** Where a goal stands: the count against it, or that the owner checks it because the people list cannot count it. */
export function goalNow(g: Goal) {
  return g.current === null || g.number === null ? t("business.numbers.owner_checks")
    : tx("business.numbers.now", { current: <b className="num">{counted(g.current, g.unit)}</b>, target: counted(g.number, g.unit) });
}

/** Why the stars row is here when the CEO only guesses the buyers are developers, with the way to check the guess. */
function GuessNote() {
  return <span className="biz-mark">{t("business.numbers.stars_guess")} <CustomersLink>{t("business-profile.check")}</CustomersLink></span>;
}

/** The steps, left to right, with the value or the reason it is not known. */
export function stageSteps(numbers: Numbers): Step[] {
  return numbers.stages.map((s) => {
    const goal = goalOf(numbers.completion, s.stage);
    return {
      key: s.stage,
      label: stageLabel(s.stage),
      value: s.value === null ? <span className="muted">{t("business.numbers.unknown")}</span>
        : <>{num(s.value)}<small>{unit(s)}{goal ? ` / ${counted(goal.number, goal.unit)}` : ""}</small></>,
      note: <>{s.value === null ? whyUnknown(s) : t(WHAT[s.stage])}{s.guess && <GuessNote />}</>,
    };
  });
}

/** The done sentence, goal by goal in the plan's words, and where the business stands against each. */
export function CompletionLine({ c }: { c: Completion }) {
  return (
    <div className="biz-done">
      <p>
        <Chip tone={c.done ? "ok" : "warn"}>{t(c.done ? "business.numbers.met" : "business.numbers.not_yet")}</Chip>{" "}
        <span className="biz-done-k">{t("business.numbers.done_when")}</span>
      </p>
      {c.goals.map((g, i) => (
        <p key={`${i}-${g.text}`}>{g.text} · <span className="biz-done-now">{goalNow(g)}</span></p>
      ))}
      <p className="biz-done-now">{t(c.targets_from === "brief" ? "business.numbers.from_plan" : "business.numbers.from_default")}</p>
      {c.targets_from === "default" && (
        <p className="biz-done-now">{t("business.numbers.set_in_plan")}</p>
      )}
      {c.missing.map((m) => <p key={m} className="biz-done-now">{m}</p>)}
    </div>
  );
}

/** For the overview: the steps as one strip, then the sentence they are measured against. */
export function Ladder({ numbers }: { numbers: Numbers }) {
  return (
    <Box>
      <Steps steps={stageSteps(numbers)} label={t("term.first_users")} />
      <CompletionLine c={numbers.completion} />
    </Box>
  );
}

function StageBox({ s, goal }: { s: StageNumber; goal: CountedGoal | undefined }) {
  const points = s.series.map((p) => ({ day: p.day, v: p.value }));
  const name = stageLabel(s.stage);
  return (
    <Box className="biz-stage">
      <div className="chart-head">
        <h2>{name}</h2>
        <span className="biz-stage-v">
          {s.value === null ? <span className="muted">{t("business.numbers.unknown")}</span> : <>{num(s.value)}<small>{unit(s)}</small></>}
          {goal && <small>{t("business.numbers.goal_of", { target: counted(goal.number, goal.unit) })}</small>}
        </span>
      </div>
      <p className="biz-stage-what">{s.value === null ? whyUnknown(s) : t(WHAT[s.stage])}{s.guess && <GuessNote />}</p>
      {points.length >= 2 ? (
        <LineChart data={points} k="v" label={t("business.numbers.trend", { stage: name })} fmt={(v) => num(Math.round(v))} />
      ) : (
        <p className="biz-trend-none">{t("business.numbers.trend_none")}</p>
      )}
    </Box>
  );
}

/** One reading of `GET /businesses/{id}/metrics`: what the server counted in the window, never model-written. */
type Obs = { metric: string; name: string; value: number; basis: string; evidence_id: string };
type Observed = { by_stage: Record<string, Obs[]>; missing: string[]; gaps?: string[] };

/** Days the company keeps reading a finished post's score and comments (server REACTION_DAYS). */
const REACTION_DAYS = 14;

/** What the company read about people's interest over the last two weeks: post scores and comments, stars. Empty, it
 * says how a reading starts, since a post only gets read once its to-do is finished with the post's address. */
export function Reactions({ businessId }: { businessId: number }) {
  const { data, error, failure, reload } = useLive<Observed>(`/businesses/${businessId}/metrics?days=${REACTION_DAYS}`, ["metric_points"]);
  // the referrer lines moved to "Where people came from", so one number shows in one place; the gap notes carry no metric key, so they are still told by the word the server's name for the referrer starts with
  const referrer = t("business-tracking.referrer_name");
  const read = (data?.by_stage.interest ?? []).filter((o) => o.metric !== "github_referrer");
  const gaps = (data?.gaps ?? []).filter((g) => !g.startsWith(referrer));
  return (
    <Section title={t("business.reactions.title", { days: REACTION_DAYS })} className="biz-reactions">
      {!data ? (error ? <LoadError error={error} retry={reload} failure={failure} /> : <Loading />) : read.length ? (
        <ul className="biz-obs">
          {read.map((o) => (
            <li key={o.evidence_id}>
              <span className="biz-obs-n">{o.name}</span>
              <b className="num">{num(o.value)}</b>
              <span className="biz-obs-b">{o.basis}</span>
            </li>
          ))}
        </ul>
      ) : <p className="biz-none">{t("business.reactions.empty", { days: REACTION_DAYS })}</p>}
      {/* only the readings that failed: a stage with no reading yet is the Numbers cards' to say, from the People list */}
      {!!gaps.length && <p className="biz-obs-miss">{t("business.reactions.missing")} {gaps.join(" · ")}</p>}
    </Section>
  );
}

/** The steps side by side, so the owner reads them from left to right. Stars appear only for a software business, so
 * there are two or three. */
export function NumbersTab({ numbers }: { numbers: Numbers }) {
  const { completion } = numbers;
  const nothing = numbers.stages.every((s) => !s.value && s.series.length < 2);
  const stars = numbers.stages.some((s) => s.stage === "interest");
  return (
    <>
      <Box className="biz-sec-box"><CompletionLine c={completion} /></Box>
      {/* nothing counted yet: one empty state with each step's reason, not boxes that each say unknown */}
      {nothing ? (
        <Hint title={t("business.numbers.empty")} action={<div className="biz-none-steps"><Steps steps={stageSteps(numbers)} label={t("term.first_users")} /></div>}>
          {t(stars ? "business.numbers.empty_body_stars" : "business.numbers.empty_body")}
        </Hint>
      ) : (
        <div className={`cols cols-${numbers.stages.length} biz-stages`}>
          {numbers.stages.map((s) => <StageBox key={s.stage} s={s} goal={goalOf(completion, s.stage)} />)}
        </div>
      )}
    </>
  );
}
