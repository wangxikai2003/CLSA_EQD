import React, { useMemo, useState } from 'react';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { GreeksTermStructureResponse, GreeksTermSlice } from '../services/api_v1';

/** Time-slice color ramp: t=0 light blue → T deep purple/black */
const SLICE_COLORS = ['#7dd3fc', '#22c55e', '#f97316', '#ef4444', '#312e81'];

type Metric = 'delta' | 'gamma' | 'pv';

interface Props {
  data: GreeksTermStructureResponse | null;
  loading?: boolean;
  spot: number;
  className?: string;
}

/** Catmull-Rom → cubic Bezier samples for smooth display from sparse grid */
function catmullRomSmooth(
  xs: number[],
  ys: number[],
  samplesPerSeg = 8,
): { x: number; y: number }[] {
  const n = xs.length;
  if (n < 2) return xs.map((x, i) => ({ x, y: ys[i] }));
  const out: { x: number; y: number }[] = [];
  for (let i = 0; i < n - 1; i++) {
    const p0x = xs[Math.max(i - 1, 0)];
    const p0y = ys[Math.max(i - 1, 0)];
    const p1x = xs[i];
    const p1y = ys[i];
    const p2x = xs[i + 1];
    const p2y = ys[i + 1];
    const p3x = xs[Math.min(i + 2, n - 1)];
    const p3y = ys[Math.min(i + 2, n - 1)];
    for (let s = 0; s < samplesPerSeg; s++) {
      const t = s / samplesPerSeg;
      const t2 = t * t;
      const t3 = t2 * t;
      const x =
        0.5 *
        ((2 * p1x) +
          (-p0x + p2x) * t +
          (2 * p0x - 5 * p1x + 4 * p2x - p3x) * t2 +
          (-p0x + 3 * p1x - 3 * p2x + p3x) * t3);
      const y =
        0.5 *
        ((2 * p1y) +
          (-p0y + p2y) * t +
          (2 * p0y - 5 * p1y + 4 * p2y - p3y) * t2 +
          (-p0y + 3 * p1y - 3 * p2y + p3y) * t3);
      out.push({ x, y });
    }
  }
  out.push({ x: xs[n - 1], y: ys[n - 1] });
  return out;
}

function regimeSeries(
  slice: GreeksTermSlice,
  regime: 'before_ki' | 'after_ki',
  metric: Metric,
  S0: number,
): { m: number; v: number }[] {
  const c = slice[regime];
  const vals = c[metric];
  if (!vals || !c.valid_spots?.length) return [];
  const xs = c.valid_spots.map((s) => s / S0);
  const smoothed = catmullRomSmooth(xs, vals, 6);
  return smoothed.map((p) => ({ m: p.x, v: p.y }));
}

const GreeksTermStructureView: React.FC<Props> = ({ data, loading, spot, className }) => {
  const [metric, setMetric] = useState<Metric>('delta');
  const [showBefore, setShowBefore] = useState(true);
  const [showAfter, setShowAfter] = useState(true);
  const [enabledSlices, setEnabledSlices] = useState<Record<string, boolean>>({});

  // init enabled when data arrives
  React.useEffect(() => {
    if (!data) return;
    setEnabledSlices((prev) => {
      const next = { ...prev };
      let changed = false;
      for (const s of data.slices) {
        if (next[s.label] === undefined) {
          next[s.label] = true;
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [data]);

  const chartRows = useMemo(() => {
    if (!data) return [];
    const S0 = data.S0 || spot || 100;
    // Merge all series onto a common moneyness grid via nearest join of smoothed points
    const allM = new Set<number>();
    const seriesMap: Record<string, Map<number, number>> = {};

    data.slices.forEach((sl, idx) => {
      if (enabledSlices[sl.label] === false) return;
      const colorIdx = idx;
      if (showBefore) {
        const key = `b_${colorIdx}`;
        const pts = regimeSeries(sl, 'before_ki', metric, S0);
        const m = new Map<number, number>();
        pts.forEach((p) => {
          const mk = Math.round(p.m * 1000) / 1000;
          m.set(mk, p.v);
          allM.add(mk);
        });
        seriesMap[key] = m;
      }
      if (showAfter) {
        const key = `a_${colorIdx}`;
        const pts = regimeSeries(sl, 'after_ki', metric, S0);
        const m = new Map<number, number>();
        pts.forEach((p) => {
          const mk = Math.round(p.m * 1000) / 1000;
          m.set(mk, p.v);
          allM.add(mk);
        });
        seriesMap[key] = m;
      }
    });

    const sorted = Array.from(allM).sort((a, b) => a - b);
    return sorted.map((m) => {
      const row: Record<string, number | null> = { m };
      for (const [k, map] of Object.entries(seriesMap)) {
        row[k] = map.has(m) ? (map.get(m) as number) : null;
      }
      return row;
    });
  }, [data, enabledSlices, showBefore, showAfter, metric, spot]);

  const lines = useMemo(() => {
    if (!data) return [];
    const out: { key: string; name: string; color: string; dashed: boolean }[] = [];
    data.slices.forEach((sl, idx) => {
      if (enabledSlices[sl.label] === false) return;
      const color = SLICE_COLORS[idx % SLICE_COLORS.length];
      if (showBefore) {
        out.push({
          key: `b_${idx}`,
          name: `${sl.label} Before KI`,
          color,
          dashed: false,
        });
      }
      if (showAfter) {
        out.push({
          key: `a_${idx}`,
          name: `${sl.label} After KI`,
          color,
          dashed: true,
        });
      }
    });
    return out;
  }, [data, enabledSlices, showBefore, showAfter]);

  const kiM = data ? data.ki_level / (data.S0 || spot) : 0.75;
  const koM = data ? data.ko_level / (data.S0 || spot) : 1.03;
  const spotM = 1.0;

  if (!data && !loading) {
    return (
      <div className={`text-xs text-slate-400 p-4 border border-dashed border-slate-200 rounded ${className || ''}`}>
        Greeks term-structure not loaded yet.
      </div>
    );
  }

  return (
    <div className={`bg-white rounded border border-slate-100 p-3 space-y-3 ${className || ''}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-xs font-bold text-slate-500 uppercase tracking-wide">
          Greeks Term Structure
          {data?.elapsed_ms != null && (
            <span className="ml-2 font-mono text-[10px] text-slate-400 normal-case">
              {data.elapsed_ms.toFixed(0)} ms · {data.ki_monitoring_type} KI
            </span>
          )}
        </h4>
        <div className="flex gap-1">
          {(['delta', 'gamma', 'pv'] as Metric[]).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMetric(m)}
              className={`px-2 py-0.5 text-[10px] font-bold rounded border ${
                metric === m
                  ? 'bg-[#000066] text-white border-[#000066]'
                  : 'bg-white text-slate-500 border-slate-200'
              }`}
            >
              {m.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      <div className="flex flex-wrap gap-3 text-[10px] text-slate-600">
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={showBefore} onChange={(e) => setShowBefore(e.target.checked)} />
          Before KI (solid)
        </label>
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={showAfter} onChange={(e) => setShowAfter(e.target.checked)} />
          After KI (dashed)
        </label>
        {(data?.slices ?? []).map((s, idx) => (
          <label key={s.label} className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={enabledSlices[s.label] !== false}
              onChange={(e) =>
                setEnabledSlices((prev) => ({ ...prev, [s.label]: e.target.checked }))
              }
            />
            <span style={{ color: SLICE_COLORS[idx % SLICE_COLORS.length] }} className="font-bold">
              {s.label}
            </span>
          </label>
        ))}
      </div>

      <div className="h-[280px] w-full relative">
        {loading && (
          <div className="absolute inset-0 bg-white/60 z-10 flex items-center justify-center text-xs text-slate-500">
            Computing term structure…
          </div>
        )}
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartRows} margin={{ top: 8, right: 12, left: 0, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" opacity={0.4} />
            <XAxis
              dataKey="m"
              type="number"
              domain={['auto', 'auto']}
              tickFormatter={(v) => `${(Number(v) * 100).toFixed(0)}%`}
              tick={{ fontSize: 10 }}
              label={{ value: 'S / S₀', position: 'insideBottom', offset: -2, fontSize: 10 }}
            />
            <YAxis tick={{ fontSize: 10 }} width={48} />
            <Tooltip
              labelFormatter={(v) => `S/S₀ = ${(Number(v) * 100).toFixed(1)}%`}
              contentStyle={{ fontSize: 11 }}
            />
            <Legend wrapperStyle={{ fontSize: 10 }} />
            <ReferenceLine x={spotM} stroke="#94a3b8" strokeDasharray="2 2" />
            <ReferenceLine x={kiM} stroke="#ef4444" strokeDasharray="4 2" />
            <ReferenceLine x={koM} stroke="#16a34a" strokeDasharray="6 3" />
            {lines.map((ln) => (
              <Line
                key={ln.key}
                type="monotone"
                dataKey={ln.key}
                name={ln.name}
                stroke={ln.color}
                strokeWidth={1.6}
                strokeDasharray={ln.dashed ? '6 3' : undefined}
                dot={false}
                connectNulls
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};

export default GreeksTermStructureView;
