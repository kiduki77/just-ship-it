import dagre from "@dagrejs/dagre";
import { Edge, MarkerType, Node, Position } from "@xyflow/react";
import { t } from "../i18n";
import { KNOWN, say, worded } from "./pathContext";
import { PathEdge, PathNode, PathView } from "../workPath";

export type Dir = "LR" | "TB";

/** The decision, task and to-do boxes read as "Task T-41: title"; the rest are the server's own sentence. */
const WITH_REF = new Set(["task", "decision", "todo"]);

export function textOf(n: PathNode): string {
  if (n.known === "unknown") return t("path.unknown");
  if (n.kind === "group") return worded(n.label_key, n.vars);
  return WITH_REF.has(n.kind) ? worded(n.label_key, { ...n.vars, ref: n.ref ?? "" }) : worded(n.label_key, n.vars);
}

/** The line a box shows under its code: a task's, decision's or to-do's own title, or the step's words. */
export const titleOf = (n: PathNode): string => (WITH_REF.has(n.kind) && n.known !== "unknown" ? String(n.vars.title ?? "") : textOf(n));

const short = (n: PathNode) => n.ref ?? textOf(n);

export function edgeText(e: PathEdge): string {
  const effect = e.effect ? `path.edge.followed_${e.effect}` : null;
  if (e.kind === "followed" && effect && KNOWN.has(effect)) return say(effect);
  return worded(e.label_key);
}

export interface NodeData extends Record<string, unknown> {
  n: PathNode;
  text: string;
  title: string;
  dir: Dir;
}
export type FlowNode = Node<NodeData, "pv">;
export interface EdgeData extends Record<string, unknown> { label: string | null; lock: boolean }
export type FlowEdge = Edge<EdgeData, "pv">;

const WIDTH = 248;
const SPINE = new Set(["goal", "cycle", "stage", "experiment", "project"]);
/** A box has a fixed size, the one the layout reserves, so a long title in either language is clamped and never overlaps. */
const heightOf = (n: PathNode) => (n.kind === "group" ? 52 : SPINE.has(n.kind) ? 68 : n.kind === "decision" ? 112 : 80);

/** Dagre's positions (top-left of each box). It runs only when the graph's revision, the unfolded groups or the direction change. */
export function layoutOf(view: Pick<PathView, "nodes" | "edges">, dir: Dir): Map<string, { x: number; y: number }> {
  const g = new dagre.graphlib.Graph();
  g.setGraph(dir === "LR" ? { rankdir: "LR", nodesep: 28, ranksep: 170, marginx: 16, marginy: 16 } : { rankdir: "TB", nodesep: 28, ranksep: 78, marginx: 16, marginy: 16 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of view.nodes) g.setNode(n.id, { width: WIDTH, height: heightOf(n) });
  for (const e of view.edges) if (g.hasNode(e.source) && g.hasNode(e.target)) g.setEdge(e.source, e.target);
  dagre.layout(g);
  const out = new Map<string, { x: number; y: number }>();
  for (const n of view.nodes) {
    const p = g.node(n.id);
    out.set(n.id, { x: p.x - WIDTH / 2, y: p.y - heightOf(n) / 2 });
  }
  return out;
}

/** The words a screen reader reads for a box, in the owner's language. */
export function nodeLabel(n: PathNode, stateWord: string | null): string {
  const parts = [textOf(n)];
  if (stateWord) parts.push(stateWord);
  if (n.held) parts.push(t("path.a11y.held"));
  if (n.focus) parts.push(t("path.a11y.focus"));
  parts.push(t(n.kind === "group" ? "path.a11y.fold" : "path.a11y.open"));
  return parts.join(". ");
}

const MARKER = { type: MarkerType.ArrowClosed, width: 14, height: 14, color: "var(--text-3)" } as const;

/**
 * React Flow's nodes and edges for a view. A box whose facts did not change keeps its object, so the memoised box does not
 * render again when a live update only moved other things.
 */
export function flowOf(
  view: PathView, pos: Map<string, { x: number; y: number }>, dir: Dir, stateWord: (n: PathNode) => string | null,
  keep: Map<string, { sig: string; node: FlowNode }>,
): { nodes: FlowNode[]; edges: FlowEdge[] } {
  const byId = new Map(view.nodes.map((n) => [n.id, n]));
  const nodes = view.nodes.map((n): FlowNode => {
    const at = pos.get(n.id) ?? { x: 0, y: 0 };
    const sig = JSON.stringify([n, at, dir]);
    const had = keep.get(n.id);
    if (had?.sig === sig) return had.node;
    const text = textOf(n);
    const node: FlowNode = {
      id: n.id, type: "pv", position: at, width: WIDTH, height: heightOf(n), measured: { width: WIDTH, height: heightOf(n) }, data: { n, text, title: titleOf(n), dir },
      sourcePosition: dir === "LR" ? Position.Right : Position.Bottom, targetPosition: dir === "LR" ? Position.Left : Position.Top,
      ariaLabel: nodeLabel(n, stateWord(n)), domAttributes: { "aria-roledescription": t("path.a11y.node_kind") },
      draggable: false, selectable: false, connectable: false,
    };
    keep.set(n.id, { sig, node });
    return node;
  });
  const edges = view.edges.filter((e) => byId.has(e.source) && byId.has(e.target)).map((e): FlowEdge => {
    const label = edgeText(e);
    return {
      id: e.id, type: "pv", source: e.source, target: e.target, className: `pv-e pv-e-${e.kind}${e.known === "unknown" ? " pv-e-unknown" : ""}`,
      data: { label: e.kind === "contains" ? null : label, lock: e.kind === "holds" },
      markerEnd: e.kind === "contains" ? undefined : MARKER, interactionWidth: 12,
      ariaLabel: t("path.a11y.edge", { from: short(byId.get(e.source)!), edge: label, to: short(byId.get(e.target)!) }),
      domAttributes: { "aria-roledescription": t("path.a11y.edge_kind") }, selectable: false, focusable: true,
    };
  });
  return { nodes, edges };
}
