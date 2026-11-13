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

type Metric = 'delta' | 'gamma' | 'pv';
export type DeskVariantId = 'baseline' | 'shift' | 'smooth' | 'desk';

const VARIANT_META: {
  id: DeskVariantId;
  short: string;
  color: string;
}[] = [
  { id: 'baseline', short: 'Hard · No Shift', color: '#0ea5e9' },
  { id: 'shift', short: 'Hard · After Shift', color: '#f97316' },
  { id: 'smooth', short: 'Smooth · No Shift', color: '#16a34a' },
  { id: 'desk', short: 'Desk (Shift+Smooth)', color: '#7c3aed' },
];

const SLICE_COLORS = ['#7dd3fc', '#22c55e', '#f97316', '#ef4444', '#312e81'];

interface Props {
  /** Primary / baseline response (backward compatible). */
  data: GreeksTermStructureResponse | null;
  /** Optional desk variants for Barrier Shift / Smoothing comparison. */
  variants?: Partial<Record<DeskVariantId, GreeksTermStructureResponse | null>>;
  loading?: boolean;
  spot: number;
  barrierShiftBps?: number;
  smoothingPct?: number;
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
        (2 * p1x +
          (-p0x + p2x) * t +
          (2 * p0x - 5 * p1x + 4 * p2x - p3x) * t2 +
          (-p0x + 3 * p1x - 3 * p2x + p3x) * t3);
      const y =
        0.5 *
        (2 * p1y +
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

function pickSlice(
  resp: GreeksTermStructureResponse | null | undefined,
  label: string | null,
): GreeksTermSlice | null {
  if (!resp?.slices?.length) return null;
  if (label) {
    const hit = resp.slices.find((s) => s.label === label);
    if (hit) return hit;
  }
  return resp.slices[0];
}

const GreeksTermStructureView: React.FC<Props> = ({
  data,
  variants,
  loading,
  spot,
  barrierShiftBps = 0,
  smoothingPct = 0,
  className,
}) => {
  const [metric, setMetric] = useState<Metric>('delta');
  const [showBefore, setShowBefore] = useState(true);
  const [showAfter, setShowAfter] = useState(true);
  const [enabledSlices, setEnabledSlices] = useState<Record<string, boolean>>({});
  const [enabledVariants, setEnabledVariants] = useState<Record<DeskVariantId, boolean>>({
    baseline: true,
    shift: true,
    smooth: true,
    desk: true,
  });
  const [activeSliceLabel, setActiveSliceLabel] = useState<string | null>(null);

  const hasVariants = useMemo(() => {
    if (!variants) return false;
    return VARIANT_META.some((v) => variants[v.id]?.slices?.length);
  }, [variants]);

  const primary = useMemo(() => {
    if (hasVariants) {
      return (
        variants?.baseline ||
        variants?.desk ||
        variants?.smooth ||
        variants?.shift ||
        data
      );
    }
    return data;
  }, [hasVariants, variants, data]);

  React.useEffect(() => {
    if (!primary) return;
    setEnabledSlices((prev) => {
      const next = { ...prev };
      let changed = false;
      for (const s of primary.slices) {
        if (next[s.label] === undefined) {
          next[s.label] = true;
          changed = true;
        }
      }
      return changed ? next : prev;
    });
    if (activeSliceLabel == null && primary.slices[0]) {
      setActiveSliceLabel(primary.slices[0].label);
    }
  }, [primary, activeSliceLabel]);

  const chartRows = useMemo(() => {
    if (!primary) return [];
    const S0 = primary.S0 || spot || 100;
    const allM = new Set<number>();
    const seriesMap: Record<string, Map<number, number>> = {};

    const pushPts = (key: string, pts: { m: number; v: number }[]) => {
      const m = new Map<number, number>();
      pts.forEach((p) => {
        const mk = Math.round(p.m * 1000) / 1000;
        m.set(mk, p.v);
        allM.add(mk);
      });
      seriesMap[key] = m;
    };

    if (hasVariants) {
      for (const v of VARIANT_META) {
        if (!enabledVariants[v.id]) continue;
        const resp = variants?.[v.id];
        const sl = pickSlice(resp, activeSliceLabel);
        if (!sl) continue;
        const S = resp?.S0 || S0;
        if (showBefore) pushPts(`vb_${v.id}`, regimeSeries(sl, 'before_ki', metric, S));
        if (showAfter) pushPts(`va_${v.id}`, regimeSeries(sl, 'after_ki', metric, S));
      }
    } else {
      primary.slices.forEach((sl, idx) => {
        if (enabledSlices[sl.label] === false) return;
        if (showBefore) pushPts(`b_${idx}`, regimeSeries(sl, 'before_ki', metric, S0));
        if (showAfter) pushPts(`a_${idx}`, regimeSeries(sl, 'after_ki', metric, S0));
      });
    }

    return Array.from(allM)
      .sort((a, b) => a - b)
      .map((m) => {
        const row: Record<string, number | null> = { m };
        for (const [k, map] of Object.entries(seriesMap)) {
          row[k] = map.has(m) ? (map.get(m) as number) : null;
        }
        return row;
      });
  }, [
    primary,
    hasVariants,
    variants,
    enabledVariants,
    enabledSlices,
    activeSliceLabel,
    showBefore,
    showAfter,
    metric,
    spot,
  ]);

  const lines = useMemo(() => {
    if (!primary) return [];
    const out: { key: string; name: string; color: string; dashed: boolean }[] = [];
    if (hasVariants) {
      for (const v of VARIANT_META) {
        if (!enabledVariants[v.id]) continue;
        if (!variants?.[v.id]?.slices?.length) continue;
        if (showBefore) {
          out.push({
            key: `vb_${v.id}`,
            name: `${v.short} · Before KI`,
            color: v.color,
            dashed: false,
          });
        }
        if (showAfter) {
          out.push({
            key: `va_${v.id}`,
            name: `${v.short} · After KI`,
            color: v.color,
            dashed: true,
          });
        }
      }
      return out;
    }
    primary.slices.forEach((sl, idx) => {
      if (enabledSlices[sl.label] === false) return;
      const color = SLICE_COLORS[idx % SLICE_COLORS.length];
      if (showBefore) {
        out.push({ key: `b_${idx}`, name: `${sl.label} Before KI`, color, dashed: false });
      }
      if (showAfter) {
        out.push({ key: `a_${idx}`, name: `${sl.label} After KI`, color, dashed: true });
      }
    });
    return out;
  }, [primary, hasVariants, variants, enabledVariants, enabledSlices, showBefore, showAfter]);

  const S0 = primary?.S0 || spot || 100;
  const kiRaw = primary ? primary.ki_level / S0 : 0.75;
  const koRaw = primary ? primary.ko_level / S0 : 1.03;
  const d = Math.abs(barrierShiftBps) / 10000;
  const kiShifted = kiRaw * (1 + d);
  const koShifted = koRaw * (1 - d);

  if (!primary && !loading) {
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
          <span className="ml-2 font-mono text-[10px] text-slate-400 normal-case">
            {hasVariants
              ? `desk compare · ${activeSliceLabel || '—'}`
              : primary?.elapsed_ms != null
                ? `${primary.elapsed_ms.toFixed(0)} ms · ${primary.ki_monitoring_type} KI`
                : ''}
          </span>
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
        {hasVariants && (primary?.slices ?? []).map((s) => (
          <label key={s.label} className="flex items-center gap-1">
            <input
              type="radio"
              name="gts-slice"
              checked={activeSliceLabel === s.label}
              onChange={() => setActiveSliceLabel(s.label)}
            />
            <span className="font-bold text-slate-700">{s.label}</span>
          </label>
        ))}
        {!hasVariants &&
          (primary?.slices ?? []).map((s, idx) => (
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

      {(hasVariants || barrierShiftBps > 0 || smoothingPct > 0) && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-[10px]">
          <div className="border border-slate-100 rounded px-2 py-1.5 space-y-1">
            <div className="font-bold text-slate-500 uppercase text-[9px]">
              Barrier Shift ({barrierShiftBps} bps) · KO↓ / KI↑
            </div>
            <div className="flex flex-wrap gap-x-3 gap-y-1">
              <label className="flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={enabledVariants.baseline}
                  disabled={!hasVariants}
                  onChange={(e) =>
                    setEnabledVariants((prev) => ({ ...prev, baseline: e.target.checked }))
                  }
                />
                <span style={{ color: '#0ea5e9' }} className="font-bold">
                  Before Barrier Shift (bps)
                </span>
              </label>
              <label className="flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={enabledVariants.shift}
                  disabled={!hasVariants}
                  onChange={(e) =>
                    setEnabledVariants((prev) => ({ ...prev, shift: e.target.checked }))
                  }
                />
                <span style={{ color: '#f97316' }} className="font-bold">
                  After Barrier Shift (bps)
                </span>
              </label>
            </div>
          </div>
          <div className="border border-slate-100 rounded px-2 py-1.5 space-y-1">
            <div className="font-bold text-slate-500 uppercase text-[9px]">
              Smoothing Spread ({smoothingPct.toFixed(1)}%)
            </div>
            <div className="flex flex-wrap gap-x-3 gap-y-1">
              <label className="flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={enabledVariants.smooth}
                  disabled={!hasVariants}
                  onChange={(e) =>
                    setEnabledVariants((prev) => ({ ...prev, smooth: e.target.checked }))
                  }
                />
                <span style={{ color: '#16a34a' }} className="font-bold">
                  After Smoothing Spread (%) · No Shift
                </span>
              </label>
              <label className="flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={enabledVariants.desk}
                  disabled={!hasVariants}
                  onChange={(e) =>
                    setEnabledVariants((prev) => ({ ...prev, desk: e.target.checked }))
                  }
                />
                <span style={{ color: '#7c3aed' }} className="font-bold">
                  After Smoothing + After Shift (Desk)
                </span>
              </label>
            </div>
            <div className="text-[9px] text-slate-400">
              Hard = before smoothing (gap risk). Smooth = continuous call-spread greeks.
            </div>
          </div>
        </div>
      )}

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
            <ReferenceLine x={1} stroke="#94a3b8" strokeDasharray="2 2" />
            <ReferenceLine x={kiRaw} stroke="#ef4444" strokeDasharray="4 2" />
            {barrierShiftBps > 0 && (
              <ReferenceLine x={kiShifted} stroke="#ef4444" strokeDasharray="2 2" strokeOpacity={0.45} />
            )}
            <ReferenceLine x={koRaw} stroke="#16a34a" strokeDasharray="6 3" />
            {barrierShiftBps > 0 && (
              <ReferenceLine x={koShifted} stroke="#16a34a" strokeDasharray="2 2" strokeOpacity={0.45} />
            )}
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
