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
  LineChart,
} from 'recharts';
import AssetUniverseSelector from '../common/AssetUniverseSelector';
import {
  CorrMatrixResponse,
  CorrPairwiseResponse,
  CorrUniverseResponse,
  ImpliedCrpLevelsResponse,
  ImpliedCrpMeta,
  ImpliedCrpMetric,
  ImpliedCrpSkewResponse,
  ImpliedCrpTermResponse,
  fetchCorrPairwise,
  fetchCorrUniverse,
  fetchImpliedCrpLevels,
  fetchImpliedCrpMeta,
  fetchImpliedCrpSkew,
  fetchImpliedCrpTerm,
  postCorrMatrix,
} from '../../services/api_v1';
import { ArrowLeftRight } from 'lucide-react';

const WINS = [21, 63, 126] as const;
const WIN_COLORS: Record<number, string> = {
  21: '#EAB308',
  63: '#000066',
  126: '#0891b2',
};
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

const METRICS = [
  ['realized', 'Realized'],
  ['desk_mark', 'Desk Mark'],
  ['diff', 'Mark − Realized'],
] as const;

const selectClass =
  'px-2 py-1.5 text-xs font-bold border border-slate-200 rounded-md bg-slate-50 text-[#000066]';

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

/** Match Python f"{m:g}" keys from implied_corr_service.levels */
function mnyKey(m: number): string {
  return Number(m).toString();
}

const MarketDataCorrPanel: React.FC = () => {
  const [view, setView] = useState<'realized' | 'analytics'>('realized');
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

  // Implied analytics (Vol-style)
  const [crpMeta, setCrpMeta] = useState<ImpliedCrpMeta | null>(null);
  const [levelFamilies, setLevelFamilies] = useState<ImpliedCrpMetric[]>(['rho_imp', 'rho_real']);
  const [levelTenors, setLevelTenors] = useState<number[]>([30, 60, 90]);
  const [levelMny, setLevelMny] = useState<number[]>([1.0]);
  const [variant, setVariant] = useState<'clean' | 'dirty'>('clean');
  const [levels, setLevels] = useState<ImpliedCrpLevelsResponse | null>(null);
  const [levelsLoading, setLevelsLoading] = useState(false);
  const [asof, setAsof] = useState<string>('');
  const [skewTenor, setSkewTenor] = useState(30);
  const [termMny, setTermMny] = useState(1.0);
  const [skew, setSkew] = useState<ImpliedCrpSkewResponse | null>(null);
  const [term, setTerm] = useState<ImpliedCrpTermResponse | null>(null);

  const focusWin = primaryWin(wins);

  useEffect(() => {
    fetchCorrUniverse().then(setUni).catch(() => setUni(null));
    fetchImpliedCrpMeta()
      .then((m) => {
        setCrpMeta(m);
        if (m.tenors?.length) {
          setLevelTenors(m.tenors);
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

  useEffect(() => {
    if (view !== 'analytics') return;
    const ac = new AbortController();
    setLevelsLoading(true);
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
        if (!ac.signal.aborted) setLevelsLoading(false);
      });
    return () => ac.abort();
  }, [view, variant, levelFamilies, levelTenors, levelMny]);

  useEffect(() => {
    if (view !== 'analytics' || !asof) return;
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
  }, [view, asof, skewTenor, termMny, variant]);

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

  const levelSeriesKeys = useMemo(() => {
    const keys: { key: string; label: string; color: string }[] = [];
    let i = 0;
    for (const fam of levelFamilies) {
      for (const t of levelTenors) {
        for (const m of levelMny) {
          // rho_real identical across mny — only plot once per tenor
          if (fam === 'rho_real' && Math.abs(m - levelMny[0]) > 1e-9) continue;
          const key = `${fam}_${t}_${mnyKey(m)}`;
          const label =
            fam === 'rho_real'
              ? `${METRIC_LABEL[fam]} ${t}D`
              : `${METRIC_LABEL[fam]} ${t}D @${m.toFixed(2)}`;
          keys.push({
            key,
            label,
            color: fam === 'rho_imp' || fam === 'rho_real' || fam === 'crp' ? METRIC_COLORS[fam] : LINE_PALETTE[i % LINE_PALETTE.length],
          });
          i += 1;
        }
      }
    }
    // diversify colors when many lines
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
            const pts = levels.levels?.[fam]?.[String(t)]?.[mnyKey(m)] || [];
            const hit = pts.find((p) => p.d === d);
            row[`${fam}_${t}_${mnyKey(m)}`] = hit ? hit.v : null;
          }
        }
      }
      return row;
    });
  }, [levels, levelFamilies, levelTenors, levelMny]);

  const toggleWin = (w: number) => setWins((prev) => toggleKeepOne(prev, w));

  const applyPreset = (id: string) => {
    const p = uni?.presets.find((x) => x.id === id || x.label === id);
    if (p?.symbols?.length) setMatrixSyms(p.symbols.slice(0, 12));
  };

  const tenors = crpMeta?.tenors?.length ? crpMeta.tenors : [30, 60, 90];
  const mnys = crpMeta?.mnys?.length ? crpMeta.mnys : [0.95, 1.0, 1.05];
  const dates = crpMeta?.dates?.length
    ? crpMeta.dates
    : asof
      ? [asof]
      : [];
  const busy = view === 'realized' ? loading || matrixLoading : levelsLoading;

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
            ['realized', 'Pairwise Realized'],
            ['analytics', 'Implied + Realized Analytics'],
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
        <>
          {/* Levels — VolSpreadPanel style multi-select */}
          <div className="bg-white border border-slate-200 rounded-lg p-4 space-y-3">
            <div className="text-[11px] font-bold text-[#000066]">
              ① Levels（可同时勾选 ρ_imp + ρ_real，多期限 / moneyness）
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
              <select
                className={`${selectClass} ml-auto`}
                value={variant}
                onChange={(e) => setVariant(e.target.value as 'clean' | 'dirty')}
              >
                <option value="clean">Clean</option>
                <option value="dirty">Dirty</option>
              </select>
            </div>
            <div className="text-[10px] text-slate-400">
              NDX Top50 · NDXP · forward realized · history {crpMeta?.history_start} → {crpMeta?.history_end}
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

          {/* Skew + Term — VolAnalytics style */}
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
                      <Line type="monotone" dataKey="p50" stroke="#94a3b8" strokeDasharray="4 4" dot={false} name={`Median@${focusWin}D`} />
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
        </>
      )}
    </div>
  );
};

export default MarketDataCorrPanel;
