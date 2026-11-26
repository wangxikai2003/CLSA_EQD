import React, { useEffect, useMemo, useState } from 'react';
import {
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  Area,
  ComposedChart,
  Legend,
} from 'recharts';
import AssetUniverseSelector from '../common/AssetUniverseSelector';
import ImpliedCorrAnalytics from './ImpliedCorrAnalytics';
import {
  CorrMatrixResponse,
  CorrPairwiseResponse,
  CorrUniverseResponse,
  fetchCorrPairwise,
  fetchCorrUniverse,
  postCorrMatrix,
} from '../../services/api_v1';
import { ArrowLeftRight } from 'lucide-react';

const WINS = [21, 63, 126] as const;
const WIN_COLORS: Record<number, string> = {
  21: '#EAB308',
  63: '#000066',
  126: '#0891b2',
};

const METRICS = [
  ['realized', 'Realized'],
  ['desk_mark', 'Desk Mark'],
  ['diff', 'Mark − Realized'],
] as const;

function heatColor(v: number | null, metric: string): string {
  if (v == null || Number.isNaN(v)) return '#f1f5f9';
  const x = metric === 'diff' ? Math.max(-0.3, Math.min(0.3, v)) / 0.3 : Math.max(-1, Math.min(1, v));
  if (x >= 0) {
    const t = x;
    return `rgb(${Math.round(255 - t * 255)},${Math.round(255 - t * 255)},${Math.round(255 - t * (255 - 102))})`;
  }
  const t = -x;
  return `rgb(${Math.round(255 - t * (255 - 226))},${Math.round(255 - t * 255)},${Math.round(255 - t * 255)})`;
}

function primaryWin(wins: number[]): number {
  if (wins.includes(63)) return 63;
  return [...wins].sort((a, b) => b - a)[0] ?? 63;
}

function toggleKeepOne<T>(prev: T[], item: T): T[] {
  if (prev.includes(item)) {
    if (prev.length === 1) return prev;
    return prev.filter((x) => x !== item);
  }
  return [...prev, item];
}

const MarketDataCorrPanel: React.FC = () => {
  const [view, setView] = useState<'realized' | 'analytics'>('analytics');
  const [sym1, setSym1] = useState('SPX');
  const [sym2, setSym2] = useState('NDX');
  const [wins, setWins] = useState<number[]>([21, 63, 126]);
  const [useRules, setUseRules] = useState(true);
  const [loading, setLoading] = useState(false);
  const [pairByWin, setPairByWin] = useState<Record<number, CorrPairwiseResponse>>({});
  const [uni, setUni] = useState<CorrUniverseResponse | null>(null);
  const [matrixSyms, setMatrixSyms] = useState<string[]>([
    'SPX',
    'NDX',
    'SX5E',
    'NKY',
    'HSI',
    'HSTECH',
    'SHSZ300',
  ]);
  const [metric, setMetric] = useState<'realized' | 'desk_mark' | 'diff'>('realized');
  const [matrix, setMatrix] = useState<CorrMatrixResponse | null>(null);
  const [matrixLoading, setMatrixLoading] = useState(false);

  const focusWin = primaryWin(wins);

  useEffect(() => {
    fetchCorrUniverse().then(setUni).catch(() => setUni(null));
  }, []);

  useEffect(() => {
    if (view !== 'realized' || !wins.length) return;
    const ac = new AbortController();
    setLoading(true);
    Promise.all(
      wins.map((w) =>
        fetchCorrPairwise(
          { symbol1: sym1, symbol2: sym2, rolling_win: w, use_rules: useRules },
          { signal: ac.signal }
        ).then((d) => [w, d] as const)
      )
    )
      .then((rows) => {
        if (ac.signal.aborted) return;
        const next: Record<number, CorrPairwiseResponse> = {};
        for (const [w, d] of rows) next[w] = d;
        setPairByWin(next);
      })
      .catch(() => {})
      .finally(() => {
        if (!ac.signal.aborted) setLoading(false);
      });
    return () => ac.abort();
  }, [view, sym1, sym2, wins, useRules]);

  useEffect(() => {
    if (view !== 'realized' || matrixSyms.length < 2) return;
    const ac = new AbortController();
    setMatrixLoading(true);
    postCorrMatrix(
      { symbols: matrixSyms, rolling_win: focusWin, metric, use_rules: useRules },
      { signal: ac.signal }
    )
      .then((d) => {
        if (!ac.signal.aborted) setMatrix(d);
      })
      .catch(() => {})
      .finally(() => {
        if (!ac.signal.aborted) setMatrixLoading(false);
      });
    return () => ac.abort();
  }, [view, matrixSyms, focusWin, metric, useRules]);

  const focusPair = pairByWin[focusWin] ?? null;

  const pairwiseChart = useMemo(() => {
    const dateSet = new Set<string>();
    for (const w of wins) {
      const p = pairByWin[w];
      if (!p) continue;
      for (const d of p.dates) dateSet.add(d);
    }
    const dates = [...dateSet].sort();
    if (!dates.length) return [];
    const maps: Record<number, Map<string, number | null>> = {};
    for (const w of wins) {
      const p = pairByWin[w];
      maps[w] = new Map();
      if (p) p.dates.forEach((d, i) => maps[w].set(d, p.rolling_corr[i]));
    }
    const bands = focusPair?.bands;
    return dates.map((d) => {
      const row: Record<string, string | number | null> = { d };
      for (const w of wins) row[`v${w}`] = maps[w].has(d) ? maps[w].get(d)! : null;
      if (bands) {
        row.p10 = bands.p10;
        row.p90 = bands.p90;
        row.p50 = bands.p50;
      }
      return row;
    });
  }, [wins, pairByWin, focusPair]);

  const toggleWin = (w: number) => setWins((prev) => toggleKeepOne(prev, w));

  const applyPreset = (id: string) => {
    const p = uni?.presets.find((x) => x.id === id || x.label === id);
    if (p?.symbols?.length) setMatrixSyms(p.symbols.slice(0, 12));
  };

  const busy = view === 'realized' && (loading || matrixLoading);

  return (
    <div className="space-y-4 relative">
      {busy && (
        <div className="absolute right-2 top-0 text-[10px] text-slate-400 flex items-center gap-1 z-10">
          <span className="h-1.5 w-1.5 rounded-full bg-[#EAB308] animate-pulse" />
          Loading
        </div>
      )}

      <div className="flex gap-1 bg-slate-100 p-1 rounded-lg w-fit">
        {(
          [
            ['analytics', 'Implied + Realized Analytics'],
            ['realized', 'Pairwise Realized'],
          ] as const
        ).map(([id, lab]) => (
          <button
            key={id}
            type="button"
            onClick={() => setView(id)}
            className={`px-3 py-1.5 text-xs font-bold rounded-md ${
              view === id ? 'bg-white text-[#000066] shadow' : 'text-slate-500'
            }`}
          >
            {lab}
          </button>
        ))}
      </div>

      {view === 'analytics' ? (
        <ImpliedCorrAnalytics />
      ) : (
        <>
          <div className="bg-white border border-slate-200 rounded-lg p-4">
            <div className="flex flex-wrap gap-3 items-end mb-3">
              <AssetUniverseSelector label="Leg 1" value={sym1} onChange={setSym1} requiredCapability="pairwise_corr" />
              <button
                type="button"
                className="p-2 rounded border border-slate-200 hover:bg-slate-50"
                title="Swap"
                onClick={() => {
                  setSym1(sym2);
                  setSym2(sym1);
                }}
              >
                <ArrowLeftRight size={14} />
              </button>
              <AssetUniverseSelector label="Leg 2" value={sym2} onChange={setSym2} requiredCapability="pairwise_corr" />
              <div className="flex flex-col gap-1">
                <span className="text-[10px] font-bold text-slate-500 uppercase">
                  Window <span className="font-normal normal-case text-slate-400">(multi)</span>
                </span>
                <div className="flex gap-1 bg-slate-100 p-1 rounded-lg">
                  {WINS.map((w) => {
                    const on = wins.includes(w);
                    return (
                      <button
                        key={w}
                        onClick={() => toggleWin(w)}
                        className={`px-2 py-1 text-[10px] font-bold rounded-md border ${
                          on ? 'bg-white shadow border-transparent' : 'text-slate-400 border-transparent'
                        }`}
                        style={on ? { color: WIN_COLORS[w] } : undefined}
                      >
                        {w}D
                      </button>
                    );
                  })}
                </div>
              </div>
              <label className="flex items-center gap-2 text-xs font-bold text-slate-600 cursor-pointer">
                <input type="checkbox" checked={useRules} onChange={(e) => setUseRules(e.target.checked)} />
                Desk Rules
              </label>
            </div>

            {focusPair && (
              <div className="mb-2 text-[10px] font-mono text-slate-600 bg-slate-50 border border-slate-100 rounded px-2 py-1">
                <span className="text-slate-400 mr-2">bands@{focusWin}D</span>
                {focusPair.rule_desc}
                {wins.map((w) => {
                  const p = pairByWin[w];
                  if (!p || p.realized_last == null) return null;
                  return (
                    <span key={w} className="ml-3" style={{ color: WIN_COLORS[w] }}>
                      ρ{w}={p.realized_last.toFixed(3)}
                    </span>
                  );
                })}
                {focusPair.desk_mark != null && (
                  <span className="ml-2 text-[#E20000] font-bold">
                    DeskMark@{focusWin}D={focusPair.desk_mark.toFixed(3)}
                  </span>
                )}
              </div>
            )}

            <div className="h-[280px]">
              {pairwiseChart.length ? (
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={pairwiseChart}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    <XAxis dataKey="d" tick={{ fontSize: 9 }} minTickGap={40} />
                    <YAxis domain={[-1, 1]} tick={{ fontSize: 10 }} />
                    <Tooltip contentStyle={{ fontSize: 11 }} />
                    <Legend wrapperStyle={{ fontSize: 10 }} />
                    {focusPair && (
                      <>
                        <Area type="monotone" dataKey="p90" stroke="none" fill="#94a3b8" fillOpacity={0.15} name="p90" />
                        <Area type="monotone" dataKey="p10" stroke="none" fill="#ffffff" fillOpacity={1} name="p10" />
                        <Line
                          type="monotone"
                          dataKey="p50"
                          stroke="#94a3b8"
                          strokeDasharray="4 4"
                          dot={false}
                          name={`Median@${focusWin}D`}
                        />
                      </>
                    )}
                    {wins.map((w) => (
                      <Line
                        key={w}
                        type="monotone"
                        dataKey={`v${w}`}
                        stroke={WIN_COLORS[w]}
                        strokeWidth={w === focusWin ? 2.25 : 1.5}
                        dot={false}
                        connectNulls
                        name={`${w}D`}
                      />
                    ))}
                    {focusPair?.desk_mark != null && (
                      <ReferenceLine y={focusPair.desk_mark} stroke="#E20000" strokeDasharray="6 4" label={`Desk@${focusWin}D`} />
                    )}
                  </ComposedChart>
                </ResponsiveContainer>
              ) : (
                <div className="h-full flex items-center justify-center text-slate-400 text-sm">No pairwise series</div>
              )}
            </div>
          </div>

          <div className="bg-white border border-slate-200 rounded-lg p-4 overflow-x-auto">
            <div className="flex flex-wrap gap-2 items-center mb-3">
              <span className="text-xs font-bold text-[#000066]">Correlation Matrix</span>
              <span className="text-[10px] text-slate-400">window={focusWin}D</span>
              {(uni?.presets || []).map((p) => (
                <button
                  key={p.id}
                  onClick={() => applyPreset(p.id)}
                  className="px-2 py-1 text-[10px] font-bold rounded border border-slate-200 hover:bg-slate-50"
                >
                  {p.label}
                </button>
              ))}
              <div className="flex gap-1 ml-auto">
                {METRICS.map(([id, lab]) => (
                  <button
                    key={id}
                    onClick={() => setMetric(id)}
                    className={`px-2 py-1 text-[10px] font-bold rounded ${
                      metric === id ? 'bg-[#000066] text-white' : 'bg-slate-100 text-slate-600'
                    }`}
                  >
                    {lab}
                  </button>
                ))}
              </div>
            </div>
            {matrix ? (
              <table className="text-[10px] border-collapse">
                <thead>
                  <tr>
                    <th className="p-1" />
                    {matrix.symbols.map((s) => (
                      <th key={s} className="p-1 font-bold text-slate-500 whitespace-nowrap">
                        {s}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {matrix.symbols.map((ri, i) => (
                    <tr key={ri}>
                      <td className="p-1 font-bold text-slate-500 whitespace-nowrap">{ri}</td>
                      {matrix.symbols.map((cj, j) => {
                        const v = matrix.matrix[i][j];
                        const diag = i === j;
                        return (
                          <td
                            key={`${ri}-${cj}`}
                            className={`p-1 text-center font-mono cursor-pointer ${diag ? 'opacity-40' : ''}`}
                            style={{ background: heatColor(v, metric), minWidth: 44 }}
                            onClick={() => {
                              if (!diag) {
                                setSym1(ri);
                                setSym2(cj);
                              }
                            }}
                          >
                            {v == null ? '—' : v.toFixed(2)}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="text-slate-400 text-sm py-6 text-center">No matrix</div>
            )}
          </div>
        </>
      )}
    </div>
  );
};

export default MarketDataCorrPanel;
