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
import {
  buildExpiryPayoffGrid,
  fdGreeksFromPayoff,
  type ExpiryPayoffParams,
  type ExpiryGridPoint,
} from '../utils/exoticExpiryPayoff';

type Metric = 'payoff' | 'delta' | 'gamma';

interface Props {
  params: ExpiryPayoffParams;
  spot: number;
  notional: number;
  viewRange: number;
  dailyKi?: boolean;
  className?: string;
}

/** Four desk variants: Shift × Smooth cross. */
const VARIANT_META = [
  {
    id: 'baseline',
    label: 'Before Shift · Before Smooth',
    short: 'Hard · No Shift',
    beforeKey: 'before_ki_baseline',
    afterKey: 'after_ki_baseline',
    color: '#0ea5e9',
    hard: true,
  },
  {
    id: 'shift',
    label: 'After Shift · Before Smooth',
    short: 'Hard · After Shift',
    beforeKey: 'before_ki_shift',
    afterKey: 'after_ki_shift',
    color: '#f97316',
    hard: true,
  },
  {
    id: 'smooth',
    label: 'Before Shift · After Smooth',
    short: 'Smooth · No Shift',
    beforeKey: 'before_ki_smooth',
    afterKey: 'after_ki_smooth',
    color: '#16a34a',
    hard: false,
  },
  {
    id: 'desk',
    label: 'After Shift · After Smooth',
    short: 'Desk (Shift+Smooth)',
    beforeKey: 'before_ki_desk',
    afterKey: 'after_ki_desk',
    color: '#7c3aed',
    hard: false,
  },
] as const;

function catmullRom(
  xs: number[],
  ys: number[],
  samplesPerSeg = 6,
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

/** Keep hard digital jumps: insert null between large gaps so Line does not bridge. */
function withHardGaps(
  xs: number[],
  ys: number[],
  jumpRel = 0.08,
): { m: number; v: number | null }[] {
  const out: { m: number; v: number | null }[] = [];
  for (let i = 0; i < xs.length; i++) {
    if (i > 0) {
      const dy = Math.abs(ys[i] - ys[i - 1]);
      const scale = Math.max(Math.abs(ys[i]), Math.abs(ys[i - 1]), 1e-9);
      if (dy / scale > jumpRel) {
        const mid = (xs[i - 1] + xs[i]) / 2;
        out.push({ m: Math.round(mid * 10000) / 10000, v: null });
      }
    }
    out.push({ m: Math.round(xs[i] * 1000) / 1000, v: ys[i] });
  }
  return out;
}

const ExoticDeskCharts: React.FC<Props> = ({
  params,
  spot,
  notional,
  viewRange,
  dailyKi = true,
  className,
}) => {
  const [metric, setMetric] = useState<Metric>('payoff');
  const [showBeforeKi, setShowBeforeKi] = useState(true);
  const [showAfterKi, setShowAfterKi] = useState(true);
  const [enabled, setEnabled] = useState<Record<string, boolean>>({
    baseline: true,
    shift: true,
    smooth: true,
    desk: true,
  });

  const shiftBps = params.barrierShiftBps ?? 0;
  const smoothPct = (params.smoothingWidth ?? 0) * 100;

  const grid = useMemo(() => {
    const lo = Math.max(0.4, 1 - viewRange * 1.6);
    const hi = 1 + viewRange * 1.6;
    return buildExpiryPayoffGrid(params, { mLo: lo, mHi: hi, steps: 161, dailyKi });
  }, [params, viewRange, dailyKi]);

  const chartRows = useMemo(() => {
    const scale = notional;
    const seriesMaps: Record<string, Map<number, number | null>> = {};
    const allM = new Set<number>();

    const push = (key: string, gKey: keyof ExpiryGridPoint, hard: boolean) => {
      let pts: { m: number; v: number | null }[];

      if (metric === 'payoff') {
        const xs: number[] = [];
        const ys: number[] = [];
        for (const g of grid) {
          const v = Number(g[gKey]);
          if (!Number.isFinite(v)) continue;
          xs.push(g.m);
          ys.push(v * scale);
        }
        if (hard) {
          pts = withHardGaps(xs, ys, 0.05);
        } else {
          pts = catmullRom(xs, ys, 8).map((p) => ({
            m: Math.round(p.x * 1000) / 1000,
            v: p.y,
          }));
        }
        if (String(gKey).startsWith('before_ki') && dailyKi) {
          pts = pts.map((p) =>
            p.v != null && p.m <= params.ki + 1e-9 ? { ...p, v: null } : p,
          );
        }
      } else {
        const greeks = fdGreeksFromPayoff(grid, gKey, spot);
        const xs: number[] = [];
        const ys: number[] = [];
        for (const r of greeks) {
          const v = metric === 'delta' ? r.delta : r.gamma;
          if (!Number.isFinite(v)) continue;
          xs.push(r.m);
          ys.push(v * scale);
        }
        if (hard) {
          pts = withHardGaps(xs, ys, metric === 'gamma' ? 0.25 : 0.12);
        } else {
          pts = catmullRom(xs, ys, 6).map((p) => ({
            m: Math.round(p.x * 1000) / 1000,
            v: p.y,
          }));
        }
      }

      const map = new Map<number, number | null>();
      pts.forEach((p) => {
        map.set(p.m, p.v);
        allM.add(p.m);
      });
      seriesMaps[key] = map;
    };

    for (const v of VARIANT_META) {
      if (!enabled[v.id]) continue;
      if (showBeforeKi) push(`b_${v.id}`, v.beforeKey as keyof ExpiryGridPoint, v.hard);
      if (showAfterKi) push(`a_${v.id}`, v.afterKey as keyof ExpiryGridPoint, v.hard);
    }

    return Array.from(allM)
      .sort((a, b) => a - b)
      .map((m) => {
        const row: Record<string, number | null> = { m };
        for (const [k, map] of Object.entries(seriesMaps)) {
          row[k] = map.has(m) ? (map.get(m) as number | null) : null;
        }
        return row;
      });
  }, [grid, metric, enabled, showBeforeKi, showAfterKi, notional, spot, dailyKi, params.ki]);

  const lines = useMemo(() => {
    const out: { key: string; name: string; color: string; dashed: boolean; hard: boolean }[] = [];
    for (const v of VARIANT_META) {
      if (!enabled[v.id]) continue;
      if (showBeforeKi) {
        out.push({
          key: `b_${v.id}`,
          name: `${v.short} · Before KI`,
          color: v.color,
          dashed: false,
          hard: v.hard,
        });
      }
      if (showAfterKi) {
        out.push({
          key: `a_${v.id}`,
          name: `${v.short} · After KI`,
          color: v.color,
          dashed: true,
          hard: v.hard,
        });
      }
    }
    return out;
  }, [enabled, showBeforeKi, showAfterKi]);

  const koM = useMemo(() => {
    const d = Math.abs(shiftBps) / 10000;
    return { raw: params.ko, shifted: params.ko * (1 - d) };
  }, [params.ko, shiftBps]);
  const kiM = useMemo(() => {
    const d = Math.abs(shiftBps) / 10000;
    return { raw: params.ki, shifted: params.ki * (1 + d) };
  }, [params.ki, shiftBps]);

  const yFmt = (v: number) => {
    if (!Number.isFinite(v)) return '—';
    if (metric === 'payoff') return `${((v / notional) * 100).toFixed(1)}%`;
    if (metric === 'delta') return v.toFixed(2);
    return v.toExponential(1);
  };

  return (
    <div className={`bg-white rounded border border-slate-100 p-3 space-y-3 ${className || ''}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-xs font-bold text-slate-500 uppercase tracking-wide">
          {metric === 'payoff'
            ? 'Payoff Profile @ Expiry'
            : metric === 'delta'
              ? 'Delta @ Expiry (Desk)'
              : 'Gamma @ Expiry (Desk)'}
          <span className="ml-2 font-mono text-[10px] text-slate-400 normal-case">
            t = T · solid = Before KI · dashed = After KI · Shift / Smooth
          </span>
        </h4>
        <div className="flex gap-1">
          {(['payoff', 'delta', 'gamma'] as Metric[]).map((m) => (
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
              {m === 'payoff' ? 'PAYOFF @ T' : m.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      <div className="flex flex-wrap gap-3 text-[10px] text-slate-600">
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={showBeforeKi} onChange={(e) => setShowBeforeKi(e.target.checked)} />
          Before KI (solid)
        </label>
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={showAfterKi} onChange={(e) => setShowAfterKi(e.target.checked)} />
          After KI (dashed)
        </label>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-[10px]">
        <div className="border border-slate-100 rounded px-2 py-1.5 space-y-1">
          <div className="font-bold text-slate-500 uppercase text-[9px]">
            Barrier Shift ({shiftBps} bps) · KO↓ / KI↑
          </div>
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            <label className="flex items-center gap-1">
              <input
                type="checkbox"
                checked={enabled.baseline !== false}
                onChange={(e) => setEnabled((prev) => ({ ...prev, baseline: e.target.checked }))}
              />
              <span style={{ color: '#0ea5e9' }} className="font-bold">
                Before Barrier Shift (bps)
              </span>
            </label>
            <label className="flex items-center gap-1">
              <input
                type="checkbox"
                checked={enabled.shift !== false}
                onChange={(e) => setEnabled((prev) => ({ ...prev, shift: e.target.checked }))}
              />
              <span style={{ color: '#f97316' }} className="font-bold">
                After Barrier Shift (bps)
              </span>
            </label>
          </div>
          <div className="text-[9px] text-slate-400">
            Hard barriers (gap). Blue = contract levels · Orange = shifted KO′/KI′.
          </div>
        </div>
        <div className="border border-slate-100 rounded px-2 py-1.5 space-y-1">
          <div className="font-bold text-slate-500 uppercase text-[9px]">
            Smoothing Spread ({smoothPct.toFixed(1)}%)
          </div>
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            <label className="flex items-center gap-1">
              <input
                type="checkbox"
                checked={enabled.smooth !== false}
                onChange={(e) => setEnabled((prev) => ({ ...prev, smooth: e.target.checked }))}
              />
              <span style={{ color: '#16a34a' }} className="font-bold">
                After Smoothing Spread (%) · No Shift
              </span>
            </label>
            <label className="flex items-center gap-1">
              <input
                type="checkbox"
                checked={enabled.desk !== false}
                onChange={(e) => setEnabled((prev) => ({ ...prev, desk: e.target.checked }))}
              />
              <span style={{ color: '#7c3aed' }} className="font-bold">
                After Smoothing + After Shift (Desk)
              </span>
            </label>
          </div>
          <div className="text-[9px] text-slate-400">
            Continuous call-spread ramp (no digital gap). Compare vs hard Before Smoothing curves above.
          </div>
        </div>
      </div>

      <div className="text-[9px] text-slate-400 leading-relaxed">
        Expiry analytical payoff (not t=0 PV). Hard = digital barrier (gap at KO/KI). Smooth =
        call-spread ramp of width ±{smoothPct.toFixed(1)}%. Shift moves KO by −{shiftBps} bps and KI
        by +{shiftBps} bps. Toggle PAYOFF / DELTA / GAMMA to see desk-parameter impact on risk.
      </div>

      <div className="h-[340px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartRows} margin={{ top: 8, right: 12, left: 0, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" opacity={0.4} />
            <XAxis
              dataKey="m"
              type="number"
              domain={['auto', 'auto']}
              tickFormatter={(v) => `${(Number(v) * 100).toFixed(0)}%`}
              tick={{ fontSize: 10 }}
              label={{ value: 'S / S₀ @ T', position: 'insideBottom', offset: -2, fontSize: 10 }}
            />
            <YAxis tick={{ fontSize: 10 }} width={52} tickFormatter={yFmt} />
            <Tooltip
              labelFormatter={(v) => `S/S₀ = ${(Number(v) * 100).toFixed(1)}%`}
              formatter={(val: number) => yFmt(val)}
              contentStyle={{ fontSize: 11 }}
            />
            <Legend wrapperStyle={{ fontSize: 9 }} />
            <ReferenceLine x={1} stroke="#94a3b8" strokeDasharray="2 2" />
            <ReferenceLine x={kiM.raw} stroke="#ef4444" strokeDasharray="4 2" label={{ value: 'KI', fontSize: 9, fill: '#ef4444' }} />
            {shiftBps > 0 && (
              <ReferenceLine
                x={kiM.shifted}
                stroke="#ef4444"
                strokeDasharray="2 2"
                strokeOpacity={0.45}
                label={{ value: 'KI′', fontSize: 9, fill: '#ef4444' }}
              />
            )}
            <ReferenceLine x={koM.raw} stroke="#16a34a" strokeDasharray="6 3" label={{ value: 'KO', fontSize: 9, fill: '#16a34a' }} />
            {shiftBps > 0 && (
              <ReferenceLine
                x={koM.shifted}
                stroke="#16a34a"
                strokeDasharray="2 2"
                strokeOpacity={0.45}
                label={{ value: 'KO′', fontSize: 9, fill: '#16a34a' }}
              />
            )}
            {lines.map((ln) => (
              <Line
                key={ln.key}
                type={ln.hard ? 'linear' : 'monotone'}
                dataKey={ln.key}
                name={ln.name}
                stroke={ln.color}
                strokeWidth={1.7}
                strokeDasharray={ln.dashed ? '6 3' : undefined}
                dot={false}
                connectNulls={false}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};

export default ExoticDeskCharts;
