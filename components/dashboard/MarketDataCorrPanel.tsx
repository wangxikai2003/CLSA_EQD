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
} from 'recharts';
import AssetUniverseSelector from '../common/AssetUniverseSelector';
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
const METRICS = [
  ['realized', 'Realized'],
  ['desk_mark', 'Desk Mark'],
  ['diff', 'Mark − Realized'],
] as const;

function heatColor(v: number | null, metric: string): string {
  if (v == null || Number.isNaN(v)) return '#f1f5f9';
  const x = metric === 'diff' ? Math.max(-0.3, Math.min(0.3, v)) / 0.3 : Math.max(-1, Math.min(1, v));
  // red-white-blue diverging
  if (x >= 0) {
    const t = x;
    const r = Math.round(255 - t * (255 - 0));
    const g = Math.round(255 - t * (255 - 0));
    const b = Math.round(255 - t * (255 - 102));
    return `rgb(${r},${g},${b})`;
  }
  const t = -x;
  const r = Math.round(255 - t * (255 - 226));
  const g = Math.round(255 - t * (255 - 0));
  const b = Math.round(255 - t * (255 - 0));
  return `rgb(${r},${g},${b})`;
}

const MarketDataCorrPanel: React.FC = () => {
  const [sym1, setSym1] = useState('SPX');
  const [sym2, setSym2] = useState('NDX');
  const [win, setWin] = useState<number>(63);
  const [useRules, setUseRules] = useState(true);
  const [loading, setLoading] = useState(false);
  const [pair, setPair] = useState<CorrPairwiseResponse | null>(null);
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

  useEffect(() => {
    fetchCorrUniverse().then(setUni).catch(() => setUni(null));
  }, []);

  useEffect(() => {
    const ac = new AbortController();
    setLoading(true);
    fetchCorrPairwise(
      { symbol1: sym1, symbol2: sym2, rolling_win: win, use_rules: useRules },
      { signal: ac.signal }
    )
      .then((d) => {
        if (!ac.signal.aborted) setPair(d);
      })
      .catch(() => {
        /* keep previous frame */
      })
      .finally(() => {
        if (!ac.signal.aborted) setLoading(false);
      });
    return () => ac.abort();
  }, [sym1, sym2, win, useRules]);

  useEffect(() => {
    if (matrixSyms.length < 2) return;
    const ac = new AbortController();
    setMatrixLoading(true);
    postCorrMatrix(
      { symbols: matrixSyms, rolling_win: win, metric, use_rules: useRules },
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
  }, [matrixSyms, win, metric, useRules]);

  const chartData = useMemo(() => {
    if (!pair) return [];
    const { bands } = pair;
    return pair.dates.map((d, i) => {
      const v = pair.rolling_corr[i];
      return {
        d,
        v,
        p10: bands.p10,
        p90: bands.p90,
        p50: bands.p50,
      };
    });
  }, [pair]);

  const applyPreset = (id: string) => {
    const p = uni?.presets.find((x) => x.id === id || x.label === id);
    if (p?.symbols?.length) setMatrixSyms(p.symbols.slice(0, 12));
  };

  return (
    <div className="space-y-4 relative">
      {(loading || matrixLoading) && (
        <div className="absolute right-2 top-0 text-[10px] text-slate-400 flex items-center gap-1 z-10">
          <span className="h-1.5 w-1.5 rounded-full bg-[#EAB308] animate-pulse" />
          Loading
        </div>
      )}

      {/* Pairwise */}
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
            <span className="text-[10px] font-bold text-slate-500 uppercase">Window</span>
            <div className="flex gap-1 bg-slate-100 p-1 rounded-lg">
              {WINS.map((w) => (
                <button
                  key={w}
                  onClick={() => setWin(w)}
                  className={`px-2 py-1 text-[10px] font-bold rounded-md ${
                    win === w ? 'bg-white text-[#000066] shadow' : 'text-slate-500'
                  }`}
                >
                  {w}D
                </button>
              ))}
            </div>
          </div>
          <label className="flex items-center gap-2 text-xs font-bold text-slate-600 cursor-pointer">
            <input type="checkbox" checked={useRules} onChange={(e) => setUseRules(e.target.checked)} />
            Desk Rules
          </label>
        </div>

        {pair && (
          <div className="mb-2 text-[10px] font-mono text-slate-600 bg-slate-50 border border-slate-100 rounded px-2 py-1">
            {pair.rule_desc}
            {pair.realized_last != null && (
              <span className="ml-3">Realized={pair.realized_last.toFixed(3)}</span>
            )}
            {pair.desk_mark != null && (
              <span className="ml-2 text-[#000066] font-bold">DeskMark={pair.desk_mark.toFixed(3)}</span>
            )}
          </div>
        )}

        <div className="h-[280px]">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="d" tick={{ fontSize: 9 }} minTickGap={40} />
              <YAxis domain={[-1, 1]} tick={{ fontSize: 10 }} />
              <Tooltip contentStyle={{ fontSize: 11 }} />
              <Area type="monotone" dataKey="p90" stroke="none" fill="#94a3b8" fillOpacity={0.15} />
              <Area type="monotone" dataKey="p10" stroke="none" fill="#ffffff" fillOpacity={1} />
              <Line type="monotone" dataKey="p50" stroke="#94a3b8" strokeDasharray="4 4" dot={false} name="Median" />
              <Line type="monotone" dataKey="v" stroke="#000066" strokeWidth={2} dot={false} name="Realized" />
              {pair?.desk_mark != null && (
                <ReferenceLine y={pair.desk_mark} stroke="#E20000" strokeDasharray="6 4" label="Desk Mark" />
              )}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Matrix */}
      <div className="bg-white border border-slate-200 rounded-lg p-4 overflow-x-auto">
        <div className="flex flex-wrap gap-2 items-center mb-3">
          <span className="text-xs font-bold text-[#000066]">Correlation Matrix</span>
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
        {matrix && (
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
                        title={diag ? '' : 'Click to load pairwise'}
                      >
                        {v == null ? '—' : v.toFixed(2)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
};

export default MarketDataCorrPanel;
