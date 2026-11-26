import React, { useEffect, useMemo, useState } from 'react';
import Plot from 'react-plotly.js';
import {
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  Legend,
  LineChart,
  BarChart,
  Bar,
  Cell,
} from 'recharts';
import {
  ImpliedCrpBasket,
  ImpliedCrpLevelsResponse,
  ImpliedCrpMeta,
  ImpliedCrpMetric,
  ImpliedCrpSkewResponse,
  ImpliedCrpSurfaceResponse,
  ImpliedCrpTermResponse,
  fetchImpliedCrpLevels,
  fetchImpliedCrpMeta,
  fetchImpliedCrpSkew,
  fetchImpliedCrpSurface,
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
const BASKETS: ImpliedCrpBasket[] = ['NDX', 'SPX', 'HSI'];

function toggleKeepOne<T>(prev: T[], item: T): T[] {
  if (prev.includes(item)) {
    if (prev.length === 1) return prev;
    return prev.filter((x) => x !== item);
  }
  return [...prev, item];
}

function mnyKey(m: number): string {
  return Number(m).toString();
}

function percentile(sorted: number[], x: number): number {
  if (!sorted.length) return NaN;
  let lo = 0;
  for (const v of sorted) if (v <= x) lo += 1;
  return (100 * lo) / sorted.length;
}

function histBins(values: number[], nBins = 24) {
  if (!values.length) return [] as { x0: number; x1: number; mid: number; count: number; label: string }[];
  const mn = Math.min(...values);
  const mx = Math.max(...values);
  const width = mx === mn ? 1e-6 : (mx - mn) / nBins;
  const bins = Array.from({ length: nBins }, (_, i) => ({
    x0: mn + i * width,
    x1: mn + (i + 1) * width,
    mid: mn + (i + 0.5) * width,
    count: 0,
    label: '',
  }));
  for (const v of values) {
    let idx = Math.floor((v - mn) / width);
    if (idx >= nBins) idx = nBins - 1;
    if (idx < 0) idx = 0;
    bins[idx].count += 1;
  }
  bins.forEach((b) => {
    b.label = `${(b.x0 * 100).toFixed(1)}`;
  });
  return bins;
}

const PercentilePanel: React.FC<{ values: number[]; title?: string }> = ({ values, title = 'CRP hist. %' }) => {
  const last = values.length ? values[values.length - 1] : NaN;
  const sorted = useMemo(() => [...values].sort((a, b) => a - b), [values]);
  const pct = Number.isFinite(last) ? percentile(sorted, last) : NaN;
  const bins = useMemo(() => histBins(values), [values]);
  const activeIdx = useMemo(() => {
    if (!bins.length || !Number.isFinite(last)) return -1;
    return bins.findIndex((b) => last >= b.x0 && last <= b.x1 + 1e-12);
  }, [bins, last]);

  return (
    <div className="w-full lg:w-[200px] shrink-0 bg-slate-50 border border-slate-200 rounded-lg p-3 flex flex-col gap-2">
      <div className="text-[10px] uppercase tracking-wide text-slate-500 font-bold">{title}</div>
      <div className="text-2xl font-bold text-[#000066] tabular-nums">
        {Number.isFinite(pct) ? `${pct.toFixed(1)}%` : '—'}
      </div>
      <div className="text-[10px] text-slate-500 font-mono">
        last {Number.isFinite(last) ? last.toFixed(3) : '—'} · n={values.length}
      </div>
      <div className="h-[140px] mt-1">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={bins} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
            <XAxis dataKey="label" hide />
            <YAxis tick={{ fontSize: 8 }} width={28} />
            <Bar dataKey="count" radius={[2, 2, 0, 0]}>
              {bins.map((_, i) => (
                <Cell key={i} fill={i === activeIdx ? '#E20000' : '#000066'} fillOpacity={i === activeIdx ? 1 : 0.35} />
              ))}
            </Bar>
            {Number.isFinite(last) && <ReferenceLine x={bins[activeIdx]?.label} stroke="#EAB308" />}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};

const ImpliedCorrAnalytics: React.FC = () => {
  const [basket, setBasket] = useState<ImpliedCrpBasket>('NDX');
  const [crpMeta, setCrpMeta] = useState<ImpliedCrpMeta | null>(null);
  const [levelFamilies, setLevelFamilies] = useState<ImpliedCrpMetric[]>(['rho_imp', 'rho_real']);
  const [levelTenors, setLevelTenors] = useState<number[]>([30, 60, 90]);
  const [levelMny, setLevelMny] = useState<number[]>([1.0]);
  const [variant, setVariant] = useState<'clean' | 'dirty'>('clean');
  const [levels, setLevels] = useState<ImpliedCrpLevelsResponse | null>(null);
  const [levelsLoading, setLevelsLoading] = useState(false);
  const [asof, setAsof] = useState('');
  const [skewTenor, setSkewTenor] = useState(30);
  const [termMny, setTermMny] = useState(1.0);
  const [surfaceMetric, setSurfaceMetric] = useState<ImpliedCrpMetric>('rho_imp');
  const [skew, setSkew] = useState<ImpliedCrpSkewResponse | null>(null);
  const [term, setTerm] = useState<ImpliedCrpTermResponse | null>(null);
  const [surface, setSurface] = useState<ImpliedCrpSurfaceResponse | null>(null);
  const [empty, setEmpty] = useState<string | null>(null);

  useEffect(() => {
    const ac = new AbortController();
    setEmpty(null);
    fetchImpliedCrpMeta({ basket }, { signal: ac.signal })
      .then((m) => {
        if (ac.signal.aborted) return;
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
        if (!m.n_dates) setEmpty(`No implied CRP panel for ${basket}`);
      })
      .catch((e) => {
        if (!ac.signal.aborted) setEmpty(String(e?.message || e));
      });
    return () => ac.abort();
  }, [basket]);

  useEffect(() => {
    if (empty) return;
    const ac = new AbortController();
    setLevelsLoading(true);
    fetchImpliedCrpLevels(
      { variant, metrics: levelFamilies, tenors: levelTenors, mnys: levelMny, basket },
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
  }, [basket, variant, levelFamilies, levelTenors, levelMny, empty]);

  useEffect(() => {
    if (!asof || empty) return;
    const ac = new AbortController();
    Promise.all([
      fetchImpliedCrpSkew({ trade_date: asof, tenor_days: skewTenor, variant, basket }, { signal: ac.signal }),
      fetchImpliedCrpTerm({ trade_date: asof, mny: termMny, variant, basket }, { signal: ac.signal }),
      fetchImpliedCrpSurface(
        { trade_date: asof, variant, metric: surfaceMetric, basket },
        { signal: ac.signal }
      ),
    ])
      .then(([sk, tm, sf]) => {
        if (ac.signal.aborted) return;
        setSkew(sk);
        setTerm(tm);
        setSurface(sf);
      })
      .catch(() => {});
    return () => ac.abort();
  }, [asof, skewTenor, termMny, variant, basket, surfaceMetric, empty]);

  const levelSeriesKeys = useMemo(() => {
    const keys: { key: string; label: string; color: string }[] = [];
    for (const fam of levelFamilies) {
      for (const t of levelTenors) {
        for (const m of levelMny) {
          if (fam === 'rho_real' && Math.abs(m - levelMny[0]) > 1e-9) continue;
          const mk = mnyKey(m);
          keys.push({
            key: `${fam}_${t}_${mk}`,
            label:
              fam === 'rho_real'
                ? `${METRIC_LABEL[fam]} ${t}D`
                : `${METRIC_LABEL[fam]} ${t}D @${Number(m).toFixed(2)}`,
            color: METRIC_COLORS[fam],
          });
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

  const crpValues = useMemo(() => {
    const t = levelTenors[0] ?? 30;
    const m = levelMny[0] ?? 1;
    const pts = levels?.levels?.crp?.[String(t)]?.[mnyKey(m)] || levels?.levels?.rho_imp?.[String(t)]?.[mnyKey(m)] || [];
    // prefer CRP series if family includes crp; else derive from levels when available
    const crpPts = levels?.levels?.crp?.[String(t)]?.[mnyKey(m)];
    if (crpPts?.length) return crpPts.map((p) => p.v);
    return pts.map((p) => p.v);
  }, [levels, levelTenors, levelMny]);

  const tenors = crpMeta?.tenors?.length ? crpMeta.tenors : [30, 60, 90];
  const mnys = crpMeta?.mnys?.length ? crpMeta.mnys : [0.95, 1.0, 1.05];
  const dates = crpMeta?.dates?.length ? crpMeta.dates : asof ? [asof] : [];
  const basketNote = crpMeta?.note || '';
  const isBootstrap = /bootstrap/i.test(basketNote);

  return (
    <div className="space-y-4 relative">
      {levelsLoading && (
        <div className="absolute right-2 top-0 text-[10px] text-slate-400 flex items-center gap-1 z-10">
          <span className="h-1.5 w-1.5 rounded-full bg-[#EAB308] animate-pulse" />
          Loading
        </div>
      )}

      <div className="flex flex-wrap gap-2 items-center">
        <span className="text-[10px] font-bold text-slate-500 uppercase">Basket</span>
        {BASKETS.map((b) => {
          const info = crpMeta?.baskets?.find((x) => x.basket === b);
          const avail = basket === b ? true : info?.available !== false;
          return (
            <button
              key={b}
              type="button"
              disabled={!avail && b !== basket}
              onClick={() => setBasket(b)}
              className={`px-2 py-1 text-[10px] font-bold rounded border ${
                basket === b
                  ? 'bg-[#000066] text-white border-[#000066]'
                  : avail
                    ? 'bg-white text-slate-500 border-slate-200'
                    : 'bg-slate-100 text-slate-300 border-slate-100 cursor-not-allowed'
              }`}
            >
              {b}
            </button>
          );
        })}
        <select
          className={`${selectClass} ml-auto`}
          value={variant}
          onChange={(e) => setVariant(e.target.value as 'clean' | 'dirty')}
        >
          <option value="clean">Clean</option>
          <option value="dirty">Dirty</option>
        </select>
      </div>

      {empty ? (
        <div className="bg-slate-50 border border-slate-200 rounded-lg px-3 py-6 text-center text-sm text-slate-400">
          {empty}
        </div>
      ) : (
        <>
          <div className="bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-[11px] text-slate-600">
            <span className="font-bold text-[#000066]">{basket} implied / realized / CRP</span>
            {crpMeta?.history_start && (
              <span className="ml-2 text-slate-400">
                {crpMeta.history_start} → {crpMeta.history_end} · {crpMeta.n_dates} dates
              </span>
            )}
            {isBootstrap && (
              <span className="ml-2 text-[#E20000] font-bold">bootstrap panel — replace via CMDS</span>
            )}
          </div>

          {/* Levels + percentile */}
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
            <div className="flex flex-col lg:flex-row gap-3">
              <div className="h-[280px] flex-1 min-w-0">
                {levelsChart.length ? (
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
                ) : (
                  <div className="h-full flex items-center justify-center text-slate-400 text-sm">No levels</div>
                )}
              </div>
              <PercentilePanel values={crpValues} title="Primary hist. %" />
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
                {(skew?.points || []).length ? (
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
                ) : (
                  <div className="h-full flex items-center justify-center text-slate-400 text-sm">No skew</div>
                )}
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
                {(term?.points || []).length ? (
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
                ) : (
                  <div className="h-full flex items-center justify-center text-slate-400 text-sm">No term</div>
                )}
              </div>
            </div>
          </div>

          {/* Surface */}
          <div className="bg-white border border-slate-200 rounded-lg p-4">
            <div className="flex flex-wrap gap-2 items-center mb-2">
              <span className="text-[11px] font-bold text-[#000066]">④ Implied Corr Surface</span>
              <select className={selectClass} value={asof} onChange={(e) => setAsof(e.target.value)}>
                {dates.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
              <div className="flex gap-1">
                {(['rho_imp', 'crp', 'rho_real'] as ImpliedCrpMetric[]).map((m) => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => setSurfaceMetric(m)}
                    className={`px-2 py-1 text-[10px] font-bold rounded border ${
                      surfaceMetric === m
                        ? 'bg-[#000066] text-white border-[#000066]'
                        : 'bg-white text-slate-500 border-slate-200'
                    }`}
                  >
                    {METRIC_LABEL[m]}
                  </button>
                ))}
              </div>
            </div>
            <div className="text-[10px] text-slate-400 mb-2">{surface?.label}</div>
            <div className="h-[320px]">
              {surface?.z?.length ? (
                <Plot
                  data={[
                    {
                      type: 'surface',
                      x: surface.mnys,
                      y: surface.tenors,
                      z: surface.z,
                      colorscale: [
                        [0, '#FEFCE8'],
                        [0.5, '#EAB308'],
                        [1, '#000066'],
                      ],
                      showscale: true,
                      hovertemplate: 'mny=%{x:.2f}<br>tenor=%{y}D<br>z=%{z:.3f}<extra></extra>',
                    },
                  ]}
                  layout={{
                    autosize: true,
                    margin: { l: 0, r: 0, b: 0, t: 10 },
                    scene: {
                      xaxis: { title: 'Moneyness' },
                      yaxis: { title: 'Tenor (D)' },
                      zaxis: { title: METRIC_LABEL[surfaceMetric] },
                    },
                    paper_bgcolor: 'rgba(0,0,0,0)',
                  }}
                  style={{ width: '100%', height: '100%' }}
                  useResizeHandler
                  config={{ displayModeBar: false }}
                  onClick={(ev: any) => {
                    const pt = ev?.points?.[0];
                    if (!pt) return;
                    if (typeof pt.x === 'number') setTermMny(pt.x);
                    if (typeof pt.y === 'number') setSkewTenor(pt.y);
                  }}
                />
              ) : (
                <div className="h-full flex items-center justify-center text-slate-400 text-sm">No surface matrix</div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
};

export default ImpliedCorrAnalytics;
