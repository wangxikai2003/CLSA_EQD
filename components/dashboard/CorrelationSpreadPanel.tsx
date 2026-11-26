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
import {
  CorrSpreadsResponse,
  ImpliedCrpLevelsResponse,
  ImpliedCrpMeta,
  ImpliedCrpMetric,
  ImpliedCrpSkewResponse,
  ImpliedCrpTermResponse,
  fetchCorrSpreads,
  fetchImpliedCrpLevels,
  fetchImpliedCrpMeta,
  fetchImpliedCrpSkew,
  fetchImpliedCrpTerm,
} from '../../services/api_v1';

const selectClass =
  'px-2 py-1.5 text-xs font-bold border border-slate-200 rounded-md bg-slate-50 text-[#000066]';

const METRIC_COLORS: Record<ImpliedCrpMetric, string> = {
  rho_imp: '#0891b2',
  rho_real: '#EAB308',
  crp: '#000066',
};
const METRIC_LABEL: Record<ImpliedCrpMetric, string> = {
  rho_imp: 'ρ_imp',
  rho_real: 'ρ_real',
  crp: 'CRP',
};
const LINE_PALETTE = ['#000066', '#EAB308', '#0891b2', '#E20000', '#16a34a', '#7c3aed', '#dc2626', '#0ea5e9'];

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

function toggleKeepOne<T>(prev: T[], item: T): T[] {
  if (prev.includes(item)) {
    if (prev.length === 1) return prev;
    return prev.filter((x) => x !== item);
  }
  return [...prev, item];
}

/** Match Python f"{m:g}" keys from implied_corr_service.levels */
function mnyKey(m: number): string {
  return Number(m).toString();
}

const CorrelationSpreadPanel: React.FC = () => {
  const [sourceHint, setSourceHint] = useState<'realized' | 'crp'>('crp');
  const [mode, setMode] = useState<'term' | 'cross'>('term');
  const [s1, setS1] = useState('SPX');
  const [s2, setS2] = useState('NDX');
  const [s3, setS3] = useState('HSI');
  const [s4, setS4] = useState('HSTECH');
  const [longWin, setLongWin] = useState(126);
  const [data, setData] = useState<CorrSpreadsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [board, setBoard] = useState<BoardRow[]>([]);

  // CRP / Vol-style analytics
  const [crpMeta, setCrpMeta] = useState<ImpliedCrpMeta | null>(null);
  const [levelFamilies, setLevelFamilies] = useState<ImpliedCrpMetric[]>(['rho_imp', 'rho_real', 'crp']);
  const [levelTenors, setLevelTenors] = useState<number[]>([30, 60]);
  const [levelMny, setLevelMny] = useState<number[]>([1.0]);
  const [variant, setVariant] = useState<'clean' | 'dirty'>('clean');
  const [levels, setLevels] = useState<ImpliedCrpLevelsResponse | null>(null);
  const [asof, setAsof] = useState('');
  const [skewTenor, setSkewTenor] = useState(30);
  const [termMny, setTermMny] = useState(1.0);
  const [skew, setSkew] = useState<ImpliedCrpSkewResponse | null>(null);
  const [term, setTerm] = useState<ImpliedCrpTermResponse | null>(null);

  useEffect(() => {
    fetchImpliedCrpMeta()
      .then((m) => {
        setCrpMeta(m);
        if (m.tenors?.length) {
          setLevelTenors(m.tenors.filter((t) => t === 30 || t === 60).length ? [30, 60].filter((t) => m.tenors.includes(t)) : m.tenors.slice(0, 2));
          setSkewTenor(m.tenors.includes(30) ? 30 : m.tenors[0]);
        }
        if (m.mnys?.length) {
          const atm = m.mnys.find((x) => Math.abs(x - 1) < 1e-9) ?? m.mnys[0];
          setLevelMny([atm]);
          setTermMny(atm);
        }
        if (m.dates?.length) setAsof(m.dates[m.dates.length - 1]);
        else if (m.history_end) setAsof(m.history_end);
      })
      .catch(() => setCrpMeta(null));
  }, []);

  useEffect(() => {
    if (sourceHint !== 'realized') return;
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
  }, [sourceHint, mode, s1, s2, s3, s4, longWin]);

  useEffect(() => {
    if (sourceHint !== 'realized') return;
    let cancelled = false;
    (async () => {
      const rows: BoardRow[] = [];
      for (const m of MONITORS) {
        try {
          const termRes = await fetchCorrSpreads({
            mode: 'term',
            symbol1: m.a,
            symbol2: m.b,
            short_win: 21,
            long_win: 126,
          });
          const last = termRes.series[termRes.series.length - 1];
          rows.push({
            label: m.label,
            r21: last?.short ?? null,
            r63: null,
            r126: last?.long ?? null,
            spread: termRes.stats.last ?? null,
            pctl: termRes.stats.pctl ?? null,
            z: termRes.stats.z ?? null,
          });
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
  }, [sourceHint]);

  useEffect(() => {
    if (sourceHint !== 'crp') return;
    const ac = new AbortController();
    setLoading(true);
    fetchImpliedCrpLevels(
      {
        variant,
        metrics: levelFamilies,
        tenors: levelTenors,
        mnys: levelMny,
      },
      { signal: ac.signal }
    )
      .then((d) => {
        if (!ac.signal.aborted) setLevels(d);
      })
      .catch(() => {})
      .finally(() => {
        if (!ac.signal.aborted) setLoading(false);
      });
    return () => ac.abort();
  }, [sourceHint, variant, levelFamilies, levelTenors, levelMny]);

  useEffect(() => {
    if (sourceHint !== 'crp' || !asof) return;
    const ac = new AbortController();
    Promise.all([
      fetchImpliedCrpSkew({ trade_date: asof, tenor_days: skewTenor, variant }, { signal: ac.signal }),
      fetchImpliedCrpTerm({ trade_date: asof, mny: termMny, variant }, { signal: ac.signal }),
    ])
      .then(([sk, tm]) => {
        if (ac.signal.aborted) return;
        setSkew(sk);
        setTerm(tm);
      })
      .catch(() => {});
    return () => ac.abort();
  }, [sourceHint, asof, skewTenor, termMny, variant]);

  const realizedChart = useMemo(() => {
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

  const levelSeriesKeys = useMemo(() => {
    const keys: { key: string; label: string; color: string }[] = [];
    for (const fam of levelFamilies) {
      for (const t of levelTenors) {
        for (const m of levelMny) {
          if (fam === 'rho_real' && Math.abs(m - levelMny[0]) > 1e-9) continue;
          const mk = mnyKey(m);
          const key = `${fam}_${t}_${mk}`;
          const label =
            fam === 'rho_real'
              ? `${METRIC_LABEL[fam]} ${t}D`
              : fam === 'crp'
                ? `CRP ${t}D @${Number(m).toFixed(2)}`
                : `${METRIC_LABEL[fam]} ${t}D @${Number(m).toFixed(2)}`;
          keys.push({ key, label, color: METRIC_COLORS[fam] });
        }
      }
    }
    if (keys.length > 3) {
      keys.forEach((k, idx) => {
        k.color = LINE_PALETTE[idx % LINE_PALETTE.length];
      });
    }
    return keys;
  }, [levelFamilies, levelTenors, levelMny]);

  const levelsChart = useMemo(() => {
    if (!levels) return [];
    const dateSet = new Set<string>();
    for (const fam of levelFamilies) {
      for (const t of levelTenors) {
        for (const m of levelMny) {
          const pts = levels.levels?.[fam]?.[String(t)]?.[mnyKey(m)] || [];
          for (const p of pts) dateSet.add(p.d);
        }
      }
    }
    const dates = [...dateSet].sort();
    return dates.map((d) => {
      const row: Record<string, string | number | null> = { d };
      for (const fam of levelFamilies) {
        for (const t of levelTenors) {
          for (const m of levelMny) {
            if (fam === 'rho_real' && Math.abs(m - levelMny[0]) > 1e-9) continue;
            const mk = mnyKey(m);
            const pts = levels.levels?.[fam]?.[String(t)]?.[mk] || [];
            const hit = pts.find((p) => p.d === d);
            row[`${fam}_${t}_${mk}`] = hit ? hit.v : null;
          }
        }
      }
      return row;
    });
  }, [levels, levelFamilies, levelTenors, levelMny]);

  const tenors = crpMeta?.tenors?.length ? crpMeta.tenors : [30, 60, 90];
  const mnys = crpMeta?.mnys?.length ? crpMeta.mnys : [0.95, 1.0, 1.05];
  const dates = crpMeta?.dates?.length ? crpMeta.dates : asof ? [asof] : [];

  return (
    <div className="space-y-4 relative">
      {loading && (
        <div className="absolute right-2 top-0 text-[10px] text-slate-400 flex items-center gap-1 z-10">
          <span className="h-1.5 w-1.5 rounded-full bg-[#EAB308] animate-pulse" />
          Loading
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <div className="flex gap-1 bg-slate-100 p-1 rounded-lg">
          {(
            [
              ['crp', 'Implied + Realized (CRP)'],
              ['realized', 'Realized Spreads'],
            ] as const
          ).map(([id, lab]) => (
            <button
              key={id}
              type="button"
              onClick={() => setSourceHint(id)}
              className={`px-3 py-1.5 text-xs font-bold rounded-md ${
                sourceHint === id ? 'bg-white text-[#000066] shadow' : 'text-slate-500'
              }`}
            >
              {lab}
            </button>
          ))}
        </div>
        {sourceHint === 'realized' && (
          <>
            <div className="flex gap-1 bg-slate-100 p-1 rounded-lg">
              {(
                [
                  ['term', 'Term Structure Spread'],
                  ['cross', 'Cross-Pair Spread'],
                ] as const
              ).map(([id, lab]) => (
                <button
                  key={id}
                  type="button"
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
          </>
        )}
        {sourceHint === 'crp' && (
          <select
            className={selectClass}
            value={variant}
            onChange={(e) => setVariant(e.target.value as 'clean' | 'dirty')}
          >
            <option value="clean">Clean</option>
            <option value="dirty">Dirty</option>
          </select>
        )}
      </div>

      {sourceHint === 'crp' ? (
        <>
          <div className="bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-[11px] text-slate-600">
            <span className="font-bold text-[#000066]">NDX basket CRP</span>
            {' · '}Top50 · NDXP · forward realized
            {crpMeta?.history_start && (
              <span className="ml-2 text-slate-400">
                history {crpMeta.history_start} → {crpMeta.history_end} ({crpMeta.n_dates} dates)
              </span>
            )}
          </div>

          {/* Levels — VolSpreadPanel style */}
          <div className="bg-white border border-slate-200 rounded-lg p-4 space-y-3">
            <div className="text-[11px] font-bold text-[#000066]">
              ① Levels（ρ_imp / ρ_real / CRP 可同时叠图）
            </div>
            <div className="flex flex-wrap gap-2 items-center">
              <span className="text-[10px] font-bold text-slate-500 uppercase">Family</span>
              {(['rho_imp', 'rho_real', 'crp'] as ImpliedCrpMetric[]).map((f) => (
                <button
                  key={f}
                  type="button"
                  onClick={() => setLevelFamilies((prev) => toggleKeepOne(prev, f))}
                  className={`px-2 py-1 text-[10px] font-bold rounded border ${
                    levelFamilies.includes(f)
                      ? f === 'rho_real'
                        ? 'bg-[#FFF100] text-[#000066] border-[#EAB308]'
                        : 'bg-[#000066] text-white border-[#000066]'
                      : 'bg-white text-slate-500 border-slate-200'
                  }`}
                >
                  {METRIC_LABEL[f]}
                </button>
              ))}
              <span className="text-[10px] font-bold text-slate-500 uppercase ml-2">Tenor</span>
              {tenors.map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => setLevelTenors((prev) => toggleKeepOne(prev, t))}
                  className={`px-2 py-1 text-[10px] font-bold rounded border ${
                    levelTenors.includes(t)
                      ? 'bg-[#000066] text-white border-[#000066]'
                      : 'bg-white text-slate-500 border-slate-200'
                  }`}
                >
                  {t}D
                </button>
              ))}
              {levelFamilies.some((f) => f !== 'rho_real') && (
                <>
                  <span className="text-[10px] font-bold text-slate-500 uppercase ml-2">Mny</span>
                  {mnys.map((m) => (
                    <button
                      key={m}
                      type="button"
                      onClick={() => setLevelMny((prev) => toggleKeepOne(prev, m))}
                      className={`px-2 py-1 text-[10px] font-bold rounded border ${
                        levelMny.some((x) => Math.abs(x - m) < 1e-9)
                          ? 'bg-[#E20000]/10 text-[#E20000] border-[#E20000]'
                          : 'bg-white text-slate-500 border-slate-200'
                      }`}
                    >
                      {m.toFixed(2)}
                    </button>
                  ))}
                </>
              )}
            </div>
            <div className="h-[280px]">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={levelsChart}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                  <XAxis dataKey="d" tick={{ fontSize: 9 }} minTickGap={40} />
                  <YAxis tick={{ fontSize: 10 }} domain={['auto', 'auto']} />
                  <Tooltip contentStyle={{ fontSize: 11 }} />
                  <Legend wrapperStyle={{ fontSize: 10 }} />
                  {levelSeriesKeys.map((s) => (
                    <Line
                      key={s.key}
                      type="monotone"
                      dataKey={s.key}
                      name={s.label}
                      stroke={s.color}
                      strokeWidth={s.key.startsWith('crp') ? 2.25 : 1.75}
                      strokeDasharray={s.key.startsWith('rho_real') ? '4 3' : undefined}
                      dot={false}
                      connectNulls
                    />
                  ))}
                  <ReferenceLine y={0} stroke="#cbd5e1" />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Skew + Term */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="bg-white border border-slate-200 rounded-lg p-4">
              <div className="flex flex-wrap gap-2 items-center mb-2">
                <span className="text-[11px] font-bold text-[#000066]">② Implied Corr Skew</span>
                <select className={selectClass} value={asof} onChange={(e) => setAsof(e.target.value)}>
                  {dates.map((d) => (
                    <option key={d} value={d}>
                      {d}
                    </option>
                  ))}
                </select>
                <div className="flex gap-1">
                  {tenors.map((t) => (
                    <button
                      key={t}
                      type="button"
                      onClick={() => setSkewTenor(t)}
                      className={`px-2 py-1 text-[10px] font-bold rounded border ${
                        skewTenor === t
                          ? 'bg-[#000066] text-white border-[#000066]'
                          : 'bg-white text-slate-500 border-slate-200'
                      }`}
                    >
                      {t}D
                    </button>
                  ))}
                </div>
              </div>
              <div className="text-[10px] text-slate-400 mb-2">{skew?.label}</div>
              <div className="h-[240px]">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={skew?.points || []}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    <XAxis dataKey="mny" tick={{ fontSize: 10 }} tickFormatter={(v) => Number(v).toFixed(2)} />
                    <YAxis tick={{ fontSize: 10 }} domain={['auto', 'auto']} />
                    <Tooltip contentStyle={{ fontSize: 11 }} />
                    <Legend wrapperStyle={{ fontSize: 10 }} />
                    <Line type="monotone" dataKey="rho_imp" name="ρ_imp" stroke="#0891b2" strokeWidth={2} dot={{ r: 3 }} />
                    <Line type="monotone" dataKey="crp" name="CRP" stroke="#000066" strokeWidth={2} dot={{ r: 3 }} />
                    <Line
                      type="monotone"
                      dataKey="rho_real"
                      name="ρ_real"
                      stroke="#EAB308"
                      strokeWidth={1.5}
                      strokeDasharray="4 3"
                      dot={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div className="bg-white border border-slate-200 rounded-lg p-4">
              <div className="flex flex-wrap gap-2 items-center mb-2">
                <span className="text-[11px] font-bold text-[#000066]">③ Term Structure</span>
                <select className={selectClass} value={asof} onChange={(e) => setAsof(e.target.value)}>
                  {dates.map((d) => (
                    <option key={d} value={d}>
                      {d}
                    </option>
                  ))}
                </select>
                <div className="flex gap-1">
                  {mnys.map((m) => (
                    <button
                      key={m}
                      type="button"
                      onClick={() => setTermMny(m)}
                      className={`px-2 py-1 text-[10px] font-bold rounded border ${
                        Math.abs(termMny - m) < 1e-9
                          ? 'bg-[#E20000]/10 text-[#E20000] border-[#E20000]'
                          : 'bg-white text-slate-500 border-slate-200'
                      }`}
                    >
                      {m.toFixed(2)}
                    </button>
                  ))}
                </div>
              </div>
              <div className="text-[10px] text-slate-400 mb-2">{term?.label}</div>
              <div className="h-[240px]">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={term?.points || []}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    <XAxis dataKey="tenor_days" tick={{ fontSize: 10 }} tickFormatter={(v) => `${v}D`} />
                    <YAxis tick={{ fontSize: 10 }} domain={['auto', 'auto']} />
                    <Tooltip contentStyle={{ fontSize: 11 }} />
                    <Legend wrapperStyle={{ fontSize: 10 }} />
                    <Line type="monotone" dataKey="rho_imp" name="ρ_imp" stroke="#0891b2" strokeWidth={2} dot={{ r: 3 }} />
                    <Line
                      type="monotone"
                      dataKey="rho_real"
                      name="ρ_real"
                      stroke="#EAB308"
                      strokeWidth={2}
                      strokeDasharray="4 3"
                      dot={{ r: 3 }}
                    />
                    <Line type="monotone" dataKey="crp" name="CRP" stroke="#000066" strokeWidth={2} dot={{ r: 3 }} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>
        </>
      ) : (
        <>
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
                <LineChart data={realizedChart}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                  <XAxis dataKey="d" tick={{ fontSize: 9 }} minTickGap={40} />
                  <YAxis tick={{ fontSize: 10 }} domain={['auto', 'auto']} />
                  <Tooltip contentStyle={{ fontSize: 11 }} />
                  <Legend wrapperStyle={{ fontSize: 10 }} />
                  <Line type="monotone" dataKey="v" name="Spread" stroke="#000066" strokeWidth={2} dot={false} connectNulls />
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
        </>
      )}
    </div>
  );
};

export default CorrelationSpreadPanel;
