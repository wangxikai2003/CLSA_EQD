import React, { useEffect, useMemo, useState } from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  Legend,
} from 'recharts';
import AssetUniverseSelector from '../common/AssetUniverseSelector';
import { CorrSpreadsResponse, fetchCorrSpreads } from '../../services/api_v1';

const selectClass =
  'px-2 py-1.5 text-xs font-bold border border-slate-200 rounded-md bg-slate-50 text-[#000066]';

type BoardRow = {
  label: string;
  r21: number | null;
  r63: number | null;
  r126: number | null;
  spread: number | null;
  pctl: number | null;
  z: number | null;
};

const MONITORS: { label: string; a: string; b: string }[] = [
  { label: 'SPX–NDX', a: 'SPX', b: 'NDX' },
  { label: 'HSI–HSTECH', a: 'HSI', b: 'HSTECH' },
  { label: 'CSI1000–HSTECH', a: 'CSI1000', b: 'HSTECH' },
  { label: 'SPX–SX5E', a: 'SPX', b: 'SX5E' },
];

const CorrelationSpreadPanel: React.FC = () => {
  const [mode, setMode] = useState<'term' | 'cross'>('term');
  const [s1, setS1] = useState('SPX');
  const [s2, setS2] = useState('NDX');
  const [s3, setS3] = useState('HSI');
  const [s4, setS4] = useState('HSTECH');
  const [longWin, setLongWin] = useState(126);
  const [data, setData] = useState<CorrSpreadsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [board, setBoard] = useState<BoardRow[]>([]);
  const [sourceHint] = useState<'realized' | 'crp'>('realized');

  useEffect(() => {
    const ac = new AbortController();
    setLoading(true);
    fetchCorrSpreads(
      {
        mode,
        symbol1: s1,
        symbol2: s2,
        symbol3: mode === 'cross' ? s3 : undefined,
        symbol4: mode === 'cross' ? s4 : undefined,
        short_win: 21,
        long_win: longWin,
      },
      { signal: ac.signal }
    )
      .then((d) => {
        if (!ac.signal.aborted) setData(d);
      })
      .catch(() => {})
      .finally(() => {
        if (!ac.signal.aborted) setLoading(false);
      });
    return () => ac.abort();
  }, [mode, s1, s2, s3, s4, longWin]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const rows: BoardRow[] = [];
      for (const m of MONITORS) {
        try {
          const term = await fetchCorrSpreads({
            mode: 'term',
            symbol1: m.a,
            symbol2: m.b,
            short_win: 21,
            long_win: 126,
          });
          const r21 = await fetchCorrSpreads({
            mode: 'term',
            symbol1: m.a,
            symbol2: m.b,
            short_win: 21,
            long_win: 21,
          });
          // last short/long from series tip
          const last = term.series[term.series.length - 1];
          rows.push({
            label: m.label,
            r21: last?.short ?? null,
            r63: null,
            r126: last?.long ?? null,
            spread: term.stats.last ?? null,
            pctl: term.stats.pctl ?? null,
            z: term.stats.z ?? null,
          });
          void r21;
        } catch {
          rows.push({
            label: m.label,
            r21: null,
            r63: null,
            r126: null,
            spread: null,
            pctl: null,
            z: null,
          });
        }
      }
      if (!cancelled) setBoard(rows);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const chart = useMemo(() => {
    if (!data?.series?.length) return [];
    const mean = data.stats.mean ?? 0;
    const sd = data.stats.std ?? 0;
    return data.series.map((p) => ({
      d: p.d,
      v: p.v,
      mean,
      p1: mean + sd,
      m1: mean - sd,
      p2: mean + 2 * sd,
      m2: mean - 2 * sd,
    }));
  }, [data]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex gap-1 bg-slate-100 p-1 rounded-lg">
          {(
            [
              ['term', 'Term Structure Spread'],
              ['cross', 'Cross-Pair Spread'],
            ] as const
          ).map(([id, lab]) => (
            <button
              key={id}
              onClick={() => setMode(id)}
              className={`px-3 py-1.5 text-xs font-bold rounded-md ${
                mode === id ? 'bg-white text-[#000066] shadow' : 'text-slate-500'
              }`}
            >
              {lab}
            </button>
          ))}
        </div>
        <select className={selectClass} value={longWin} onChange={(e) => setLongWin(Number(e.target.value))}>
          <option value={63}>Long = 63D</option>
          <option value={126}>Long = 126D</option>
        </select>
        <select className={selectClass} value={sourceHint} disabled title="Implied CRP reserved">
          <option value="realized">Realized</option>
          <option value="crp">Implied vs Realized (CRP) — soon</option>
        </select>
        {loading && <span className="text-[10px] text-slate-400">Loading…</span>}
      </div>

      <div className="flex flex-wrap gap-3 items-end bg-white border border-slate-200 rounded-lg p-3">
        <AssetUniverseSelector label="Pair1 A" value={s1} onChange={setS1} />
        <AssetUniverseSelector label="Pair1 B" value={s2} onChange={setS2} />
        {mode === 'cross' && (
          <>
            <AssetUniverseSelector label="Pair2 A" value={s3} onChange={setS3} />
            <AssetUniverseSelector label="Pair2 B" value={s4} onChange={setS4} />
          </>
        )}
      </div>

      {/* Scoreboard */}
      <div className="bg-white border border-slate-200 rounded-lg p-3 overflow-x-auto">
        <h3 className="text-xs font-bold text-[#000066] mb-2">Monitor Scoreboard (Term 126−21)</h3>
        <table className="w-full text-[10px]">
          <thead>
            <tr className="text-slate-500">
              <th className="text-left p-1">Pair</th>
              <th className="p-1">ρ 21D</th>
              <th className="p-1">ρ 126D</th>
              <th className="p-1">Spread</th>
              <th className="p-1">Z</th>
              <th className="p-1">PCTL</th>
            </tr>
          </thead>
          <tbody>
            {board.map((r) => {
              const hot = r.pctl != null && (r.pctl > 85 || r.pctl < 15);
              return (
                <tr key={r.label} className="border-t border-slate-100">
                  <td className="p-1 font-bold">{r.label}</td>
                  <td className="p-1 text-center font-mono">{r.r21?.toFixed(3) ?? '—'}</td>
                  <td className="p-1 text-center font-mono">{r.r126?.toFixed(3) ?? '—'}</td>
                  <td className="p-1 text-center font-mono">{r.spread?.toFixed(3) ?? '—'}</td>
                  <td className="p-1 text-center font-mono">{r.z?.toFixed(2) ?? '—'}</td>
                  <td className={`p-1 text-center font-mono ${hot ? 'text-[#E20000] font-bold' : ''}`}>
                    {r.pctl != null ? (
                      <span className="inline-flex items-center gap-1">
                        {r.pctl.toFixed(0)}%
                        <span className="inline-block h-1.5 w-12 bg-slate-100 rounded overflow-hidden">
                          <span
                            className={`block h-full ${hot ? 'bg-[#E20000]' : 'bg-[#000066]'}`}
                            style={{ width: `${Math.max(0, Math.min(100, r.pctl))}%` }}
                          />
                        </span>
                      </span>
                    ) : (
                      '—'
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="bg-white border border-slate-200 rounded-lg p-4">
        <h3 className="text-xs font-bold text-[#000066] mb-2">{data?.label || 'Spread'}</h3>
        <div className="h-[300px]">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chart}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="d" tick={{ fontSize: 9 }} minTickGap={40} />
              <YAxis tick={{ fontSize: 10 }} domain={['auto', 'auto']} />
              <Tooltip contentStyle={{ fontSize: 11 }} />
              <Legend wrapperStyle={{ fontSize: 10 }} />
              <Line type="monotone" dataKey="v" name="Spread" stroke="#000066" strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="mean" name="Mean" stroke="#94a3b8" strokeDasharray="4 4" dot={false} />
              <Line type="monotone" dataKey="p1" name="+1SD" stroke="#16a34a" strokeDasharray="2 2" dot={false} />
              <Line type="monotone" dataKey="m1" name="-1SD" stroke="#16a34a" strokeDasharray="2 2" dot={false} />
              <Line type="monotone" dataKey="p2" name="+2SD" stroke="#EAB308" strokeDasharray="2 2" dot={false} />
              <Line type="monotone" dataKey="m2" name="-2SD" stroke="#EAB308" strokeDasharray="2 2" dot={false} />
              <ReferenceLine y={0} stroke="#cbd5e1" />
            </LineChart>
          </ResponsiveContainer>
        </div>
        {data?.stats && (
          <div className="mt-2 text-[10px] font-mono text-slate-500">
            last={data.stats.last?.toFixed(4)} · z={data.stats.z?.toFixed(2)} · pctl={data.stats.pctl?.toFixed(1)}% ·
            range [{data.stats.low?.toFixed(3)}, {data.stats.high?.toFixed(3)}]
          </div>
        )}
      </div>
    </div>
  );
};

export default CorrelationSpreadPanel;
