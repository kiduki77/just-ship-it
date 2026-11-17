import { ReactNode } from "react";
import { Link } from "react-router-dom";
import { t } from "../i18n";
import { Icon } from "./icons";
import { ItemLink } from "./panel";
import { State } from "./WhereSits";
import { Counts, freesText, Outline, OutlineNode, TurnCard } from "../workPath";
import { refHref } from "./ui";
import "./outline.css";

/** What a row has, as icon and word: progress first, then what needs a look. Nothing is shown for a zero. */
function Facts({ c }: { c: Counts }) {
  return (
    <span className="ol-facts">
      <span className="ol-fact">{c.total ? t("path.outline.progress", { done: c.done, n: c.total }) : t("path.outline.no_tasks")}</span>
      {c.held > 0 && <span className="ol-fact"><Icon name="lock" size={12} />{t("path.outline.held", { n: c.held })}</span>}
      {c.failed > 0 && <span className="ol-fact ol-bad"><Icon name="alert" size={12} />{t("path.outline.failed", { n: c.failed })}</span>}
      {c.cards > 0 && <span className="ol-fact ol-warn"><Icon name="clock" size={12} />{t("path.outline.cards", { n: c.cards })}</span>}
      {c.todos > 0 && <span className="ol-fact ol-warn"><Icon name="clock" size={12} />{t("path.outline.todos", { n: c.todos })}</span>}
    </span>
  );
}

/** A decision waiting for the owner: what it is and what answering frees, which is the order's reason. */
export function CardRow({ card, current }: { card: TurnCard; current?: boolean }) {
  return (
    <li className="ol-item ol-leaf" aria-current={current ? "true" : undefined}>
      <ItemLink to={`/inbox?req=${card.id}`} className="ol-link">
        <Icon name="inbox" size={14} /><span className="ol-ref">{card.ref}</span><span className="ol-title">{card.title}</span>
      </ItemLink>
      <span className="ol-fact ol-frees"><Icon name="lock" size={12} />{freesText(card)}</span>
    </li>
  );
}

function ProjectRow({ n, here, focusId }: { n: OutlineNode; here: boolean; focusId: number | null }) {
  const nothing = !n.cards.length && !n.attention.length;
  return (
    <li className={`ol-item${nothing ? " ol-folded" : ""}`} aria-current={here ? "true" : undefined}>
      <div className="ol-row">
        <Icon name="projects" size={14} />
        <Link to={`/projects/${n.id}`} className="ol-link"><span className="ol-ref">{n.ref}</span><span className="ol-title">{n.title}</span></Link>
        {n.status && <State kind="project" state={n.status} />}
        <Facts c={n.counts} />
      </div>
      {n.experiment && <p className="ol-sub">{t("path.step.experiment", { title: n.experiment.title })}</p>}
      {!nothing && (
        <ul className="ol-list">
          {n.cards.map((c) => <CardRow key={c.id} card={c} current={c.id === focusId} />)}
          {n.attention.map((a) => (
            <li key={a.id} className="ol-item ol-leaf">
              <ItemLink to={refHref("T", a.id, n.id) ?? "#"} className="ol-link">
                <Icon name="tasks" size={14} /><span className="ol-ref">{a.ref}</span><span className="ol-title">{a.title}</span>
              </ItemLink>
              <State kind="task" state={a.status} />
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function Branch({ n, projectId, focusId }: { n: OutlineNode; projectId: number | null; focusId: number | null }) {
  if (n.kind === "project") return <ProjectRow n={n} here={n.id === projectId} focusId={focusId} />;
  const head = n.kind === "cycle" ? t("path.step.cycle", { n: n.number ?? 0 }) : n.title ?? "";
  return (
    <li className="ol-item">
      <div className="ol-row ol-group">
        <Icon name={n.kind === "cycle" ? "routines" : "board"} size={14} />
        <span className="ol-name">{head}</span>
        {n.status && <State kind={n.kind} state={n.status} />}
        <Facts c={n.counts} />
      </div>
      <ul className="ol-list">
        {n.children.map((c) => <Branch key={`${c.kind}${c.id}`} n={c} projectId={projectId} focusId={focusId} />)}
      </ul>
    </li>
  );
}

/**
 * The work as a nested list: the goal, cycle > stage > project, and under each project the decisions waiting for the
 * owner (in the one order) and its failed tasks. A project with nothing waiting is one row. It is the text version of
 * the path view, so it carries every fact the graph shows.
 */
export function OutlineTree({ outline, projectId = null, focusId = null, loose = [], children }: {
  outline: Outline; projectId?: number | null; focusId?: number | null;
  /** Decisions that belong to no project. */
  loose?: TurnCard[]; children?: ReactNode;
}) {
  if (!outline.children.length && !loose.length) return <p className="ol-empty">{t("path.outline.empty")}</p>;
  return (
    <div className="ol">
      {outline.goal && <p className="ol-goal"><Icon name="flag" size={14} />{t("path.step.goal", { text: outline.goal })}</p>}
      {children}
      {outline.children.length > 0 && (
        <ul className="ol-list ol-root" aria-label={t("path.outline.label")}>
          {outline.children.map((n) => <Branch key={`${n.kind}${n.id}`} n={n} projectId={projectId} focusId={focusId} />)}
        </ul>
      )}
      {loose.length > 0 && (
        <section className="ol-loose">
          <h3>{t("path.outline.company")}</h3>
          <ul className="ol-list">{loose.map((c) => <CardRow key={c.id} card={c} current={c.id === focusId} />)}</ul>
        </section>
      )}
    </div>
  );
}

/** The outline cut down to the branch that holds one project, for a screen that shows a single project. */
export function branchOf(outline: Outline, projectId: number): Outline {
  const cut = (n: OutlineNode): OutlineNode | null => {
    if (n.kind === "project") return n.id === projectId ? n : null;
    const kids = n.children.map(cut).filter((x): x is OutlineNode => !!x);
    return kids.length ? { ...n, children: kids } : null;
  };
  return { ...outline, children: outline.children.map(cut).filter((x): x is OutlineNode => !!x) };
}
