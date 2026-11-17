import { ReactNode, useEffect, useRef, useState } from "react";
import { t } from "../i18n";

/*
 * Small daily charts for the operate screens. Series colors come from status tokens (ok / bad / muted) or the
 * accent for a single series, never a generated palette. Text stays in text tokens; identity is never color alone
 * (legend + tooltip + table view).
 */

export type Series = { key: string; label: string; color: string };
export type Point = Record<string, any>;

const H = 132;
const PAD = { t: 10, r: 8, b: 22, l: 36 };

function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(480);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(200, Math.floor(e.contentRect.width))));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

const dayLabel = (d: string) => {
  const [, m, day] = d.split("-");
  return `${Number(m)}/${Number(day)}`;
};

/** A rounded-nice ceiling so the axis label reads as a round number. */
function niceMax(v: number) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

function Legend({ series }: { series: Series[] }) {
  if (series.length < 2) return null;
  return (
    <div className="legend" aria-hidden="true">
      {series.map((s) => (
        <span key={s.key} className="legend-i"><span className="legend-sw" style={{ background: s.color }} />{s.label}</span>
      ))}
    </div>
  );
}

function TableView({ data, series, fmt }: { data: Point[]; series: Series[]; fmt: (v: number) => string }) {
  return (
    <details className="chart-table">
      <summary className="small">{t("work.chart.table")}</summary>
      <table className="tbl">
        <thead><tr><th scope="col">{t("work.chart.date")}</th>{series.map((s) => <th key={s.key} scope="col">{s.label}</th>)}</tr></thead>
        <tbody>
          {data.map((d) => (
            <tr key={d.day}><td>{d.day}</td>{series.map((s) => <td key={s.key}>{d[s.key] == null ? "-" : fmt(Number(d[s.key]))}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

/** Stacked daily bars. A 2px surface gap separates segments; only the top segment gets the rounded end. */
/** `fmt` writes values in the tooltip and the table view; `axisFmt`, when given, writes the shorter axis label. */
export function BarChart({ data, series, fmt = (v) => String(v), axisFmt, label, empty }: {
  data: Point[]; series: Series[]; fmt?: (v: number) => string; axisFmt?: (v: number) => string; label: string; empty?: ReactNode;
}) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const totals = data.map((d) => series.reduce((a, s) => a + (Number(d[s.key]) || 0), 0));
  const max = niceMax(Math.max(...totals, 0));
  const iw = w - PAD.l - PAD.r, ih = H - PAD.t - PAD.b;
  const step = iw / data.length;
  const bw = Math.max(4, Math.min(28, step * 0.62));
  const y = (v: number) => PAD.t + ih - (v / max) * ih;
  const allZero = totals.every((t) => t === 0);
  const tick = (i: number) => i === 0 || i === data.length - 1 || i === Math.floor(data.length / 2);
  // axes over nothing read as a broken chart: a period with no records is one sentence
  if (allZero) return <div className="chart" ref={ref}><p className="chart-none">{empty ?? t("work.chart.empty")}</p></div>;
  return (
    <div className="chart" ref={ref}>
      <Legend series={series} />
      <div className="chart-plot">
        <svg width={w} height={H} role="img" aria-label={`${t("work.chart.aria", { label, n: data.length, max: fmt(Math.max(...totals, 0)) })} ${t("audit.chart.sum",
            { total: fmt(totals.reduce((a, v) => a + v, 0)), n: totals.filter((v) => v > 0).length })}`}
          onMouseLeave={() => setHover(null)}>
          <line x1={PAD.l} x2={w - PAD.r} y1={y(max)} y2={y(max)} className="grid" />
          <line x1={PAD.l} x2={w - PAD.r} y1={y(max / 2)} y2={y(max / 2)} className="grid" />
          <text x={PAD.l - 6} y={y(max) + 4} className="axis" textAnchor="end">{(axisFmt ?? fmt)(max)}</text>
          <text x={PAD.l - 6} y={y(0) + 4} className="axis" textAnchor="end">0</text>
          {data.map((d, i) => {
            const cx = PAD.l + step * i + step / 2;
            let acc = 0;
            const segs = series.map((s) => ({ s, v: Number(d[s.key]) || 0 })).filter((x) => x.v > 0);
            return (
              <g key={d.day} onMouseEnter={() => setHover(i)} className={hover === i ? "on" : ""}>
                <rect x={PAD.l + step * i} y={PAD.t} width={step} height={ih} className="hit" />
                {segs.map(({ s, v }, j) => {
                  const y0 = y(acc), y1 = y(acc + v);
                  acc += v;
                  const top = j === segs.length - 1;
                  const h = Math.max(1, y0 - y1 - (j > 0 ? 2 : 0));
                  const yy = y1;
                  const r = top ? Math.min(4, h, bw / 2) : 0;
                  const x0 = cx - bw / 2;
                  return (
                    <path key={s.key} fill={s.color}
                      d={`M${x0},${yy + h} V${yy + r} Q${x0},${yy} ${x0 + r},${yy} H${x0 + bw - r} Q${x0 + bw},${yy} ${x0 + bw},${yy + r} V${yy + h} Z`} />
                  );
                })}
                {tick(i) && <text x={cx} y={H - 6} className="axis" textAnchor="middle">{dayLabel(d.day)}</text>}
              </g>
            );
          })}
          <line x1={PAD.l} x2={w - PAD.r} y1={y(0)} y2={y(0)} className="base" />
        </svg>
        {hover !== null && (
          <div className="tip" style={{ left: Math.min(w - 150, Math.max(0, PAD.l + step * hover + step / 2 - 70)) }}>
            <div className="tip-h">{data[hover].day}</div>
            {series.map((s) => (
              <div key={s.key} className="tip-r">
                <span className="legend-sw" style={{ background: s.color }} />
                <span>{s.label}</span><b>{fmt(Number(data[hover][s.key]) || 0)}</b>
              </div>
            ))}
          </div>
        )}
      </div>
      <TableView data={data} series={series} fmt={fmt} />
    </div>
  );
}

/** One daily line (null = no data that day, drawn as a gap). Crosshair + tooltip on hover. */
export function LineChart({ data, k, label, fmt, max: fixedMax, color = "var(--accent)", empty }: {
  data: Point[]; k: string; label: string; fmt: (v: number) => string; max?: number; color?: string; empty?: ReactNode;
}) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const vals = data.map((d) => (d[k] == null ? null : Number(d[k])));
  const max = fixedMax ?? niceMax(Math.max(...vals.map((v) => v ?? 0), 0));
  const iw = w - PAD.l - PAD.r, ih = H - PAD.t - PAD.b;
  const step = iw / data.length;
  const x = (i: number) => PAD.l + step * i + step / 2;
  const y = (v: number) => PAD.t + ih - (v / max) * ih;
  const segs: string[] = [];
  let cur = "";
  vals.forEach((v, i) => {
    if (v == null) {
      if (cur) segs.push(cur);
      cur = "";
      return;
    }
    cur += `${cur ? "L" : "M"}${x(i)},${y(v)} `;
  });
  if (cur) segs.push(cur);
  const has = vals.some((v) => v != null);
  if (!has) return <div className="chart" ref={ref}><p className="chart-none">{empty ?? t("work.chart.empty")}</p></div>;
  return (
    <div className="chart" ref={ref}>
      <div className="chart-plot">
        <svg width={w} height={H} role="img" aria-label={label} onMouseLeave={() => setHover(null)}
          onMouseMove={(e) => {
            const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
            const i = Math.floor((e.clientX - r.left - PAD.l) / step);
            setHover(i >= 0 && i < data.length ? i : null);
          }}>
          <line x1={PAD.l} x2={w - PAD.r} y1={y(max)} y2={y(max)} className="grid" />
          <line x1={PAD.l} x2={w - PAD.r} y1={y(max / 2)} y2={y(max / 2)} className="grid" />
          <text x={PAD.l - 6} y={y(max) + 4} className="axis" textAnchor="end">{fmt(max)}</text>
          <text x={PAD.l - 6} y={y(0) + 4} className="axis" textAnchor="end">{fmt(0)}</text>
          {segs.map((d, i) => <path key={i} d={d} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />)}
          {vals.map((v, i) => v != null && segs.length && vals.filter((x) => x != null).length === 1
            ? <circle key={i} cx={x(i)} cy={y(v)} r={4} fill={color} /> : null)}
          {data.map((d, i) => (i === 0 || i === data.length - 1 || i === Math.floor(data.length / 2)) && (
            <text key={d.day} x={x(i)} y={H - 6} className="axis" textAnchor="middle">{dayLabel(d.day)}</text>
          ))}
          <line x1={PAD.l} x2={w - PAD.r} y1={y(0)} y2={y(0)} className="base" />
          {hover !== null && (
            <g>
              <line x1={x(hover)} x2={x(hover)} y1={PAD.t} y2={PAD.t + ih} className="cross" />
              {vals[hover] != null && <circle cx={x(hover)} cy={y(vals[hover]!)} r={5} fill={color} stroke="var(--panel)" strokeWidth={2} />}
            </g>
          )}
        </svg>
        {hover !== null && (
          <div className="tip" style={{ left: Math.min(w - 150, Math.max(0, x(hover) - 70)) }}>
            <div className="tip-h">{data[hover].day}</div>
            <div className="tip-r"><span>{label}</span><b>{vals[hover] == null ? t("work.chart.no_record") : fmt(vals[hover]!)}</b></div>
          </div>
        )}
      </div>
      <TableView data={data} series={[{ key: k, label, color }]} fmt={fmt} />
    </div>
  );
}

/** A horizontal share bar for lists ("cost by agent"): one value against the largest in the list. */
export function ShareBar({ value, max, label }: { value: number; max: number; label: string }) {
  return (
    <div className="share" role="img" aria-label={label}>
      <span style={{ width: `${max > 0 ? Math.max(2, (value / max) * 100) : 0}%` }} />
    </div>
  );
}
