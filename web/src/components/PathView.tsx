import { memo, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import {
  BaseEdge, Controls, EdgeLabelRenderer, EdgeProps, getSmoothStepPath, Handle, NodeProps, Position, ReactFlow, ReactFlowProvider,
  useNodesInitialized, useReactFlow,
} from "@xyflow/react";
import { useNavigate, useSearchParams } from "react-router-dom";
import "@xyflow/react/dist/base.css";
import { qs, useLive } from "../api";
import { t } from "../i18n";
import { Icon } from "./icons";
import { branchOf, OutlineTree } from "./Outline";
import { usePanel } from "./panel";
import { StepKind } from "./pathContext";
import { Dir, EdgeData, FlowEdge, flowOf, FlowNode, layoutOf, NodeData } from "./pathGraph";
import { QuietError } from "./today";
import { refHref, Skeleton } from "./ui";
import { stateView, Tone, TONE_ICON } from "./WhereSits";
import { freesText, PathNode, PathView, YourTurn } from "../workPath";
import "./path-view.css";

const TABLES = ["agent_requests", "tasks", "projects", "owner_tasks", "cycles", "cycle_stages", "bets", "request_links"];

function useMedia(query: string): boolean {
  return useSyncExternalStore(
    (cb) => {
      const m = window.matchMedia(query);
      m.addEventListener("change", cb);
      return () => m.removeEventListener("change", cb);
    },
    () => window.matchMedia(query).matches,
  );
}

/** The width the path has to draw in. The decision's panel can take half the window, so the window's width says little. */
function useWidth(): [(el: HTMLElement | null) => void, number | null] {
  const [width, setWidth] = useState<number | null>(null);
  const watch = useRef<ResizeObserver | null>(null);
  const ref = useCallback((el: HTMLElement | null) => {
    watch.current?.disconnect();
    watch.current = null;
    if (!el) return;
    setWidth(el.clientWidth);
    watch.current = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    watch.current.observe(el);
  }, []);
  return [ref, width];
}

// Below this the boxes' words are too small to read, so a fit stops here and the owner pans to the rest.
const READABLE = 0.85;
// A left-to-right path needs room for three columns of boxes at a readable size.
const LR_FROM = 900;

type Tag = { tone: Tone; word: string };

/** A box's status as an icon and a word: a decision waits for the owner, a to-do is theirs, the rest is the record's own status. */
function tagOf(n: PathNode): Tag | null {
  if (n.kind === "decision" && n.state === "PENDING") return { tone: "warn", word: t("path.state.pending") };
  if (n.kind === "todo") return { tone: "warn", word: t("path.state.pending") };
  if (!n.state || ["group", "origin", "project_manager", "goal"].includes(n.kind)) return null;
  return stateView(n.kind as StepKind, n.state);
}

const ICON: Record<string, string> = {
  goal: "flag", cycle: "routines", stage: "board", experiment: "idea", project: "projects", task: "tasks", decision: "inbox",
  project_manager: "agents", todo: "list", origin: "bolt", group: "plus",
};

const StateTag = ({ tag }: { tag: Tag }) => (
  <span className={`ws-state ws-${tag.tone}`}><Icon name={TONE_ICON[tag.tone]} size={12} />{tag.word}</span>
);

const PathBox = memo(function PathBox({ data }: NodeProps<FlowNode>) {
  const { n, text, title, dir } = data as NodeData;
  const tag = tagOf(n);
  const unknown = n.known === "unknown";
  const frees = n.kind === "decision" && n.waiting
    ? freesText({ scope: n.scope ?? "none", effective: n.scope ?? "none", frees: n.frees ?? 0, behind: n.behind ?? 0, nothing_live: false })
    : null;
  const cls = ["pv-node", `pv-k-${n.kind}`, n.focus && "pv-focus", n.chain && "pv-chain", n.waiting && "pv-waiting", unknown && "pv-unknown"]
    .filter(Boolean).join(" ");
  return (
    <div className={cls} aria-hidden="true" title={text}>
      <Handle type="target" position={dir === "LR" ? Position.Left : Position.Top} isConnectable={false} className="pv-h" />
      <div className="pv-top">
        <Icon name={ICON[n.kind] ?? "file"} size={14} />
        {n.ref && n.kind !== "project" && <span className="pv-ref">{n.ref}</span>}
        {tag && <StateTag tag={tag} />}
        {n.held && <span className="ws-state ws-warn"><Icon name="lock" size={12} />{t("path.state.held")}</span>}
      </div>
      <p className="pv-title">{title}</p>
      {frees && <p className="pv-frees"><Icon name="lock" size={12} />{frees}</p>}
      <Handle type="source" position={dir === "LR" ? Position.Right : Position.Bottom} isConnectable={false} className="pv-h" />
    </div>
  );
});

const PathLine = memo(function PathLine(p: EdgeProps<FlowEdge>) {
  const [path, x, y] = getSmoothStepPath({
    sourceX: p.sourceX, sourceY: p.sourceY, targetX: p.targetX, targetY: p.targetY,
    sourcePosition: p.sourcePosition, targetPosition: p.targetPosition, borderRadius: 10,
  });
  const d = p.data as EdgeData | undefined;
  return (
    <>
      <BaseEdge id={p.id} path={path} markerEnd={p.markerEnd} interactionWidth={p.interactionWidth} />
      {d?.label && (
        <EdgeLabelRenderer>
          <span className="pv-elabel" aria-hidden="true" style={{ transform: `translate(-50%, -50%) translate(${x}px, ${y}px)` }}>
            {d.lock && <Icon name="lock" size={11} />}{d.label}
          </span>
        </EdgeLabelRenderer>
      )}
    </>
  );
});

const nodeTypes = { pv: PathBox };
const edgeTypes = { pv: PathLine };

/** Where each box's own screen is: Enter, or a click, goes there. A group unfolds instead, and a source that is not a record goes nowhere. */
function useOpen(projectId: number) {
  const navigate = useNavigate();
  const panel = usePanel();
  return useCallback((n: PathNode) => {
    const id = n.ref_id;
    if (n.kind === "decision" && id) panel.open({ key: "req", id });
    else if (n.kind === "todo" && id) panel.open({ key: "task", id });
    else if (n.kind === "task" && id) navigate(refHref("T", id, projectId)!);
    else if (n.kind === "project" && id) navigate(`/projects/${id}`);
    else if (n.kind === "project_manager") navigate(`/projects/${projectId}/decisions`);
    else if (n.kind === "goal") navigate("/business?tab=brief");
    else if (n.kind === "experiment") navigate(`/business?tab=bets${n.ref ? `#${n.ref.toLowerCase()}` : ""}`);
    else if (n.kind === "cycle" || n.kind === "stage") navigate("/business?tab=bets");
  }, [navigate, panel, projectId]);
}

const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const boxEl = (root: HTMLElement | null, id: string): HTMLElement | null =>
  Array.from(root?.querySelectorAll<HTMLElement>(".react-flow__node") ?? []).find((el) => el.dataset.id === id) ?? null;

function Canvas({ view, dir, projectId, onUnfold }: { view: PathView; dir: Dir; projectId: number; onUnfold: (id: string) => void }) {
  const rf = useReactFlow();
  const ready = useNodesInitialized();
  const open = useOpen(projectId);
  const root = useRef<HTMLDivElement>(null);
  const keep = useRef(new Map<string, { sig: string; node: FlowNode }>());
  const [shown, setShown] = useState(false);
  const [inited, setInited] = useState(false);
  const fitted = useRef<string | null>(null);
  const focused = useRef<string | null>(null);
  const pending = useRef<string[] | null>(null);
  const byId = useMemo(() => new Map(view.nodes.map((n) => [n.id, n])), [view.nodes]);

  // layout runs when the graph's revision, the opened groups or the direction change, and not on a live refresh
  const shape = `${view.rev}|${view.unfolded.join(",")}|${view.nodes.map((n) => n.id).join(",")}|${dir}`;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const pos = useMemo(() => layoutOf(view, dir), [shape]);
  const flow = useMemo(() => {
    const out = flowOf(view, pos, dir, (n) => tagOf(n)?.word ?? null, keep.current);
    for (const id of keep.current.keys()) if (!byId.has(id)) keep.current.delete(id);
    return out;
  }, [view, pos, dir, byId]);

  const focusId = view.focus && byId.has(view.focus) ? view.focus : null;
  // the view opens on the decision it was asked for (its neighbours in sight) and never fits again on a live refresh
  useEffect(() => {
    if (!ready || !inited) return;
    const key = `${projectId}|${view.focus ?? ""}|${dir}`;
    if (fitted.current === key) return;
    const first = fitted.current === null;
    fitted.current = key;
    const near = focusId
      ? [focusId, ...view.edges.flatMap((e) => (e.source === focusId ? [e.target] : e.target === focusId ? [e.source] : []))]
      : null;
    const duration = first || reducedMotion() ? 0 : 250;
    const done = near
      ? rf.fitView({ nodes: near.map((id) => ({ id })), padding: 0.25, minZoom: READABLE, maxZoom: 1.2, duration })
      : rf.fitView({ padding: 0.15, maxZoom: 1.1, duration });
    Promise.resolve(done).then(() => {
      setShown(true);
      const arrived = `${projectId}|${view.focus ?? ""}`;
      if (focusId && focused.current !== arrived) {
        focused.current = arrived;
        window.requestAnimationFrame(() => boxEl(root.current, focusId)?.focus({ preventScroll: true }));
      }
    });
  }, [ready, inited, projectId, view.focus, focusId, dir, view.edges, rf]);

  // an unfolded group's first box takes the focus its group had, and the view moves to what opened. React Flow draws its
  // boxes a frame after the props change, so the box is waited for.
  useEffect(() => {
    const members = pending.current?.filter((id) => byId.has(id));
    if (!members?.length) return;
    pending.current = null;
    let tries = 20;
    const tick = () => {
      const el = boxEl(root.current, members[0]);
      if (!el) {
        if (tries-- > 0) window.requestAnimationFrame(tick);
        return;
      }
      el.focus({ preventScroll: true });
      void rf.fitView({ nodes: members.slice(0, 6).map((id) => ({ id })), padding: 0.3, minZoom: READABLE, maxZoom: 1.1, duration: reducedMotion() ? 0 : 200 });
    };
    tick();
  }, [byId, rf]);

  const act = (id: string) => {
    const n = byId.get(id);
    if (!n) return;
    if (n.kind === "group") {
      pending.current = n.members;
      onUnfold(n.id);
    } else open(n);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      const back = document.querySelector<HTMLElement>("[data-show-on-path]") ?? document.querySelector<HTMLElement>(".pv-mode button[aria-pressed='true']");
      if (back) {
        e.preventDefault();
        back.focus();
      }
      return;
    }
    const el = (e.target as HTMLElement).closest<HTMLElement>(".react-flow__node");
    if (!el || e.target !== el || !el.dataset.id) return;
    if (e.key === "Enter") {
      e.preventDefault();
      act(el.dataset.id);
    } else if (e.key === " ") {
      e.preventDefault();
      if (byId.get(el.dataset.id)?.kind === "group") act(el.dataset.id);
    }
  };

  const flowLabels = useMemo(() => ({
    "node.a11yDescription.default": t("path.flow.node_desc"),
    "node.a11yDescription.keyboardDisabled": t("path.flow.node_desc_keys"),
    "node.a11yDescription.ariaLiveMessage": ({ direction, x, y }: { direction: string; x: number; y: number }) =>
      t("path.flow.moved", { direction, x: Math.round(x), y: Math.round(y) }),
    "edge.a11yDescription.default": t("path.flow.edge_desc"),
    "controls.ariaLabel": t("path.flow.controls"),
    "controls.zoomIn.ariaLabel": t("path.flow.zoom_in"),
    "controls.zoomOut.ariaLabel": t("path.flow.zoom_out"),
    "controls.fitView.ariaLabel": t("path.flow.fit"),
    "controls.interactive.ariaLabel": t("path.flow.lock"),
    "minimap.ariaLabel": t("path.flow.minimap"),
    "handle.ariaLabel": t("path.flow.handle"),
  }), []);

  return (
    <div className={`pv-canvas${shown ? " pv-shown" : ""}`} ref={root} onKeyDown={onKeyDown} role="group" aria-label={t("path.view.canvas")}>
      <ReactFlow
        nodes={flow.nodes} edges={flow.edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} ariaLabelConfig={flowLabels}
        nodesDraggable={false} nodesConnectable={false} elementsSelectable={false} nodesFocusable edgesFocusable autoPanOnNodeFocus
        zoomOnScroll={false} preventScrolling={false} minZoom={0.3} maxZoom={1.5}
        onInit={() => setInited(true)} onNodeClick={(_, node) => act(node.id)} attributionPosition="bottom-right">
        <Controls showInteractive={false} position="bottom-left" />
      </ReactFlow>
    </div>
  );
}

const LEGEND: [string, string][] = [
  ["depends_on", "path.edge.depends_on"], ["holds", "path.edge.holds"], ["raised_by", "path.edge.raised_by"], ["followed", "path.edge.followed"],
];

function Legend() {
  return (
    <ul className="pv-legend" aria-label={t("path.view.legend")}>
      {LEGEND.map(([kind, key]) => (
        <li key={kind}>
          <svg width="34" height="8" viewBox="0 0 34 8" aria-hidden="true"><line className={`pv-e-${kind}`} x1="1" y1="4" x2="33" y2="4" /></svg>
          {kind === "holds" && <Icon name="lock" size={11} />}
          {t(key as "path.edge.holds")}
        </li>
      ))}
    </ul>
  );
}

const PLAN_KINDS = new Set(["task", "decision", "todo", "project_manager", "group"]);

/**
 * One project's path: where each decision sits and what moves when it is answered. The graph is the picture, the outline
 * is the same facts as a list, and the outline stands in whenever the graph can't (too big, loading, failed, a small window).
 */
export function PathTab({ projectId, businessId }: { projectId: number; businessId: number | null }) {
  const [params] = useSearchParams();
  const focus = params.get("focus");
  const focusNo = Number(focus?.replace(/\D/g, "")) || null;
  const [mode, setMode] = useState<"graph" | "list">("graph");
  const [narrow, setNarrow] = useState(false);
  const [unfold, setUnfold] = useState<string[]>([]);
  // a group asked to open again has no room left under the box limit
  const [full, setFull] = useState(false);
  const scope = `${projectId}|${focus ?? ""}`;
  const [seen, setSeen] = useState(scope);
  if (seen !== scope) {
    setSeen(scope);
    setNarrow(false);
    setUnfold([]);
    setFull(false);
  }
  const [sized, width] = useWidth();
  const tiny = useMedia("(max-width: 639px)");

  const url = `/projects/${projectId}/path${qs({ focus, neighbourhood: narrow || null, unfold: unfold.join(",") || null })}`;
  const { data, error, reload } = useLive<PathView>(url, TABLES);
  // a change of folding or narrowing keeps the last picture on screen until the next one lands
  const last = useRef<PathView | null>(null);
  if (data) last.current = data;
  const view = data ?? (last.current?.project_id === projectId ? last.current : null);
  const loading = !data;

  const turn = useLive<YourTurn>(!view && businessId != null ? `/businesses/${businessId}/your-turn` : null, TABLES).data;
  const fallback = turn ? branchOf(turn.outline, projectId) : null;

  if (!view) {
    return (
      <div className="pv">
        {error
          ? <QuietError text={t("path.view.error")} retry={reload} />
          : <div role="status" aria-live="polite"><p className="pv-note">{t("path.view.loading")}</p><Skeleton rows={2} /></div>}
        {fallback && <div className="pv-outline"><OutlineTree outline={fallback} projectId={projectId} focusId={focusNo} /></div>}
      </div>
    );
  }

  const planned = view.nodes.some((n) => PLAN_KINDS.has(n.kind));
  const asList = mode === "list" || view.too_big || tiny;
  return (
    <div className="pv" aria-busy={loading || undefined} ref={sized}>
      {error && <QuietError text={t("path.view.error")} retry={reload} />}
      <div className="pv-bar">
        {!view.too_big && !tiny && (
          <div className="seg pv-mode" role="group" aria-label={t("path.mode.label")}>
            <button type="button" aria-pressed={mode === "graph"} className={mode === "graph" ? "on" : ""} onClick={() => setMode("graph")}>
              <Icon name="path" />{t("path.mode.graph")}
            </button>
            <button type="button" aria-pressed={mode === "list"} className={mode === "list" ? "on" : ""} onClick={() => setMode("list")}>
              <Icon name="list" />{t("common.view_list")}
            </button>
          </div>
        )}
        {!asList && view.shown < view.total && <span className="pv-count small muted">{t("path.view.count", { shown: view.shown, total: view.total })}</span>}
        {!asList && view.unfolded.length > 0 && <button type="button" className="link small" onClick={() => setUnfold([])}>{t("path.view.fold")}</button>}
        {view.neighbourhood && (
          <span className="pv-note small" role="status">
            {t("path.view.narrowed")} <button type="button" className="link" onClick={() => setNarrow(false)}>{t("path.view.widen")}</button>
          </span>
        )}
      </div>

      {view.too_big && (
        <p className="pv-note" role="status">
          {t("path.view.too_big")}{" "}
          {view.can_narrow && <button type="button" className="link" onClick={() => setNarrow(true)}>{t("path.view.narrow")}</button>}
        </p>
      )}
      {full && !asList && (
        <p className="pv-note small" role="status">
          {t("path.view.full")} <button type="button" className="link" onClick={() => setMode("list")}>{t("path.view.full_list")}</button>
        </p>
      )}
      {tiny && !view.too_big && <p className="pv-note small muted">{t("path.view.small_screen")}</p>}
      {view.focus && !view.too_big && !view.nodes.some((n) => n.id === view.focus) && <p className="pv-note small muted">{t("path.view.focus_missing")}</p>}

      {asList || !planned ? (
        <div className="pv-outline">
          {!planned && !view.too_big && <p className="pv-note">{t("path.view.empty")}</p>}
          <OutlineTree outline={view.outline} projectId={projectId} focusId={focusNo} />
        </div>
      ) : (
        <>
          <ReactFlowProvider>
            <Canvas view={view} dir={(width ?? 0) >= LR_FROM ? "LR" : "TB"} projectId={projectId} onUnfold={(id) => (unfold.includes(id) ? setFull(true) : setUnfold((u) => [...u, id]))} />
          </ReactFlowProvider>
          <Legend />
          <p className="pv-help small muted">{t("path.view.help")}</p>
        </>
      )}
    </div>
  );
}
