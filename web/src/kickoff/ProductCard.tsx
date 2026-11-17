import { ReactNode } from "react";
import { Chip, Tone } from "../components/kit";
import { ActionButton } from "../components/ui";
import { PlainKey, t } from "../i18n";
import "./product-card.css";
import { ClaimVerdict, FeatureState, ProductReport, ProductView, TestsState } from "./types";

/*
 * What the CPO found when it read the code of a product the owner brought. It is the CPO's reading, never a verified
 * fact, so the source and that caveat stay in view while the detail folds away.
 */

const FEATURE: Record<FeatureState, { tone: Tone; word: PlainKey }> = {
  works: { tone: "ok", word: "kickoff.product.feature.works" },
  partial: { tone: "warn", word: "kickoff.product.feature.partial" },
  missing: { tone: "muted", word: "kickoff.product.feature.missing" },
};

const TESTS: Record<TestsState, { tone: Tone; word: PlainKey }> = {
  strong: { tone: "ok", word: "kickoff.product.tests.strong" },
  some: { tone: "muted", word: "kickoff.product.tests.some" },
  few: { tone: "warn", word: "kickoff.product.tests.few" },
  none: { tone: "warn", word: "kickoff.product.tests.none" },
};

const CLAIM: Record<ClaimVerdict, { tone: Tone; word: PlainKey }> = {
  holds: { tone: "ok", word: "kickoff.product.claim.holds" },
  partly: { tone: "warn", word: "kickoff.product.claim.partly" },
  not_found: { tone: "muted", word: "kickoff.product.claim.not_found" },
};

function Group({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
  return (
    <section className="pc-g">
      <h3 className="pc-g-h">{title}{count != null && <span className="pc-g-n">{count}</span>}</h3>
      {children}
    </section>
  );
}

/** One row of a list: the text first, the file it rests on under it. */
function Row({ lead, children, evidence }: { lead?: ReactNode; children: ReactNode; evidence?: string }) {
  return (
    <li className="pc-r">
      <span>{lead}{children}</span>
      {evidence && <span className="pc-ev mono muted">{evidence}</span>}
    </li>
  );
}

function Details({ report }: { report: ProductReport }) {
  const { features, stack, tests, risks, unfinished, readme_vs_reality: claims } = report;
  return (
    <details className="pc-more">
      <summary>{t("kickoff.product.details")}</summary>
      <div className="pc-detail" role="region" aria-label={t("kickoff.product.details")} tabIndex={0}>
        <Group title={t("kickoff.product.does")}><p className="pc-p">{report.what_it_does}</p></Group>
        {features.length > 0 && (
          <Group title={t("kickoff.product.features")} count={features.length}>
            <ul className="pc-list">
              {features.map((f, i) => (
                <Row key={i} evidence={f.evidence} lead={<Chip tone={FEATURE[f.state].tone}>{t(FEATURE[f.state].word)}</Chip>}>{f.name}</Row>
              ))}
            </ul>
          </Group>
        )}
        {stack.length > 0 && (
          <Group title={t("kickoff.product.stack")}>
            <ul className="pc-tags">{stack.map((s, i) => <li key={i} className="tag">{s}</li>)}</ul>
          </Group>
        )}
        <Group title={t("kickoff.product.tests")}>
          <ul className="pc-list">
            <Row evidence={tests.evidence} lead={<Chip tone={TESTS[tests.state].tone}>{t(TESTS[tests.state].word)}</Chip>}>{tests.summary}</Row>
          </ul>
          <p className="pc-note small muted">{t("kickoff.product.tests.note")}</p>
        </Group>
        {risks.length > 0 && (
          <Group title={t("kickoff.product.risks")} count={risks.length}>
            <ul className="pc-list">{risks.map((r, i) => <Row key={i} evidence={r.evidence}>{r.text}</Row>)}</ul>
          </Group>
        )}
        {unfinished.length > 0 && (
          <Group title={t("kickoff.product.unfinished")} count={unfinished.length}>
            <ul className="pc-list">{unfinished.map((u, i) => <Row key={i} evidence={u.evidence}>{u.text}</Row>)}</ul>
          </Group>
        )}
        {claims.length > 0 && (
          <Group title={t("kickoff.product.readme")} count={claims.length}>
            <ul className="pc-list">
              {claims.map((c, i) => (
                <Row key={i} evidence={c.evidence} lead={<Chip tone={CLAIM[c.verdict].tone}>{t(CLAIM[c.verdict].word)}</Chip>}>
                  <span className="strong">{c.claim}</span>
                  <span className="pc-reality muted">{t("kickoff.product.reality")}: {c.reality}</span>
                </Row>
              ))}
            </ul>
          </Group>
        )}
      </div>
    </details>
  );
}

/** One card per report state. A state with no report says what is happening and nothing more. */
export function ProductCard({ product, onReadAgain, inTalk }: {
  product: ProductView; onReadAgain: () => Promise<unknown>; inTalk: boolean;
}) {
  const title = <h2 className="pc-t">{t("kickoff.product.title")}</h2>;
  switch (product.report_state) {
    case "reading":
      // before the first question the conversation shows the reading, with its clock; a reading asked for again
      // while the owner answers questions is shown here, since the conversation is busy with those
      if (inTalk) return null;
      return (
        <section className="pc" aria-label={t("kickoff.product.title")}>
          <div className="pc-line" role="status">
            <span className="live-dot" aria-hidden="true" />
            <span>
              <span className="strong">{t("kickoff.product.reading", { folder: product.folder })}</span>
              <span className="muted">{t("kickoff.product.reading_again_next")}</span>
            </span>
          </div>
          {product.waiting && <p className="small bad-t" role="alert">{product.waiting}</p>}
        </section>
      );
    case "failed":
      return (
        <section className="pc" aria-label={t("kickoff.product.title")}>
          <p className="pc-line">{t("kickoff.product.failed")}</p>
          <p className="pc-from small muted">{t("kickoff.product.copy", { folder: product.folder })}</p>
          <ActionButton className="primary" run={onReadAgain}>{t("kickoff.product.read_again")}</ActionButton>
        </section>
      );
    case "ready": {
      const { report } = product;
      return (
        <section className="pc" aria-label={t("kickoff.product.title")}>
          {title}
          <p className="pc-lead">{report.summary}</p>
          <p className="pc-p">{report.for_owner}</p>
          <p className="pc-from small muted">{t("kickoff.product.from", { folder: product.folder, branch: product.base_branch, commit: product.commit })}</p>
          <Details report={report} />
        </section>
      );
    }
  }
}
