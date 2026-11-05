import React, { useEffect, useMemo, useState } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  BarChart, Bar, ReferenceLine, Cell,
} from 'recharts';
import {
  fetchVolSpreads,
  VolSpreadsResponse,
  VolTenorKey,
  VolMoneyness,
  VolFamily,
} from '../../services/api_v1';

const COLORS = ['#000066', '#EAB308', '#E20000', '#16a34a', '#7c3aed', '#0891b2', '#ea580c', '#0d9488'];
const TENORS: VolTenorKey[] = ['1M', '3M', '6M', '12M'];
const MNY: VolMoneyness[] = ['ATM', 'OTM', 'ITM'];

const selectClass =
  'px-2 py-1.5 text-xs font-bold border border-slate-200 rounded-md bg-slate-50 text-[#000066] focus:outline-none focus:ring-2 focus:ring-[#000066]';

export type VolAssetFilter = 'all' | 'equity' | 'commodity';

type VolLeg = {
  product: string;
  family: VolFamily;
  tenor: VolTenorKey;
  moneyness: VolMoneyness;
};

function downsample<T>(arr: T[], maxPts = 450): T[] {
  if (arr.length <= maxPts) return arr;
  const step = Math.ceil(arr.length / maxPts);
  const out = arr.filter((_, i) => i % step === 0);
  if (out[out.length - 1] !== arr[arr.length - 1]) out.push(arr[arr.length - 1]);
  return out;
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

function legKey(l: VolLeg) {
  return l.family === 'RV'
    ? `${l.product} RV ${l.tenor}`
    : `${l.product} IV ${l.tenor} ${l.moneyness}`;
}

function getSeries(data: VolSpreadsResponse | null, leg: VolLeg): Map<string, number> {
  const m = new Map<string, number>();
  if (!data) return m;
  const block = data.levels[leg.product];
  if (!block) return m;
  const arr =
    leg.family === 'RV'
      ? block.RV?.[leg.tenor] ?? []
      : block.IV?.[leg.tenor]?.[leg.moneyness] ?? [];
  for (const p of arr) m.set(p.d, p.v);
  return m;
}

function alignDiff(a: Map<string, number>, b: Map<string, number>) {
  const out: { d: string; v: number }[] = [];
  for (const [d, va] of a) {
    const vb = b.get(d);
    if (vb == null) continue;
    out.push({ d, v: va - vb });
  }
  return out;
}

function toggleKeepOne<T>(prev: T[], item: T): T[] {
  if (prev.includes(item)) return prev.length === 1 ? prev : prev.filter((x) => x !== item);
  return [...prev, item];
}

const PercentilePanel: React.FC<{ values: number[] }> = ({ values }) => {
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
      <div className="text-[10px] uppercase tracking-wide text-slate-500 font-bold">Hist. percentile</div>
      <div className="text-2xl font-bold text-[#000066] tabular-nums">
        {Number.isFinite(pct) ? `${pct.toFixed(1)}%` : '—'}
      </div>
      <div className="text-[10px] text-slate-500 font-mono">
        last {Number.isFinite(last) ? `${(last * 100).toFixed(2)}%` : '—'} · n={values.length}
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

const LegPick: React.FC<{
  label: string;
  products: string[];
  leg: VolLeg;
  onChange: (l: VolLeg) => void;
}> = ({ label, products, leg, onChange }) => (
  <div className="flex flex-wrap items-center gap-2">
    <span className="text-[10px] font-bold text-slate-400 uppercase w-14">{label}</span>
    <select className={selectClass} value={leg.product} onChange={(e) => onChange({ ...leg, product: e.target.value })}>
      {products.map((p) => (
        <option key={p} value={p}>{p}</option>
      ))}
    </select>
    <select className={selectClass} value={leg.family} onChange={(e) => onChange({ ...leg, family: e.target.value as VolFamily })}>
      <option value="RV">RV</option>
      <option value="IV">IV</option>
    </select>
    <select className={selectClass} value={leg.tenor} onChange={(e) => onChange({ ...leg, tenor: e.target.value as VolTenorKey })}>
      {TENORS.map((t) => (
        <option key={t} value={t}>{t}</option>
      ))}
    </select>
    {leg.family === 'IV' && (
      <select className={selectClass} value={leg.moneyness} onChange={(e) => onChange({ ...leg, moneyness: e.target.value as VolMoneyness })}>
        {MNY.map((m) => (
          <option key={m} value={m}>{m}</option>
        ))}
      </select>
    )}
  </div>
);

const TITLE: Record<VolAssetFilter, string> = {
  all: 'All Vol Spread',
  equity: 'Equity Vol Spread',
  commodity: 'Commodity Vol Spread',
};

const VolSpreadPanel: React.FC<{ assetClass: VolAssetFilter }> = ({ assetClass }) => {
  const [data, setData] = useState<VolSpreadsResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadStatus, setLoadStatus] = useState('加载 vol 序列…');

  const [levelProds, setLevelProds] = useState<string[]>([]);
  // multi-select: RV and/or IV, and multiple tenors
  const [levelFamilies, setLevelFamilies] = useState<VolFamily[]>(['RV', 'IV']);
  const [levelTenors, setLevelTenors] = useState<VolTenorKey[]>(['1M', '3M']);
  const [levelMny, setLevelMny] = useState<VolMoneyness[]>(['ATM']);

  const [s1L, setS1L] = useState<VolLeg>({ product: '', family: 'IV', tenor: '1M', moneyness: 'ATM' });
  const [s1R, setS1R] = useState<VolLeg>({ product: '', family: 'IV', tenor: '1M', moneyness: 'OTM' });
  const [s2L, setS2L] = useState<VolLeg>({ product: '', family: 'RV', tenor: '1M', moneyness: 'ATM' });
  const [s2R, setS2R] = useState<VolLeg>({ product: '', family: 'IV', tenor: '1M', moneyness: 'ATM' });

  useEffect(() => {
    const ac = new AbortController();
    setLoading(true);
    setErr(null);
    setLoadStatus('加载 vol 序列…');
    fetchVolSpreads(assetClass, false, {
      signal: ac.signal,
      onRetry: ({ attempt, maxAttempts, delayMs }) => {
        setLoadStatus(
          `服务冷启动/繁忙，继续等待（${attempt}/${maxAttempts}），约 ${Math.ceil(delayMs / 1000)}s 后重试…`
        );
      },
    })
      .then((d) => {
        if (ac.signal.aborted) return;
        setData(d);
        setErr(null);
        const prods = d.products.map((p) => p.product);
        const prefer =
          assetClass === 'all'
            ? ['IM', 'IF', 'IH'].filter((p) => prods.includes(p)).slice(0, 2)
            : prods.slice(0, Math.min(2, prods.length));
        setLevelProds(prefer.length ? prefer : prods.slice(0, 2));
        setLevelFamilies(['RV', 'IV']);
        setLevelTenors(['1M']);
        setLevelMny(['ATM']);
        const a = prefer[0] ?? prods[0] ?? '';
        const b = prefer[1] ?? a;
        setS1L({ product: a, family: 'IV', tenor: '1M', moneyness: 'ATM' });
        setS1R({ product: a, family: 'IV', tenor: '1M', moneyness: 'OTM' });
        setS2L({ product: b || a, family: 'RV', tenor: '1M', moneyness: 'ATM' });
        setS2R({ product: b || a, family: 'IV', tenor: '1M', moneyness: 'ATM' });
      })
      .catch((e) => {
        if ((e as Error)?.name === 'AbortError' || ac.signal.aborted) return;
        setErr('Vol 数据仍在准备中，请稍后切换 All/Equity/Commodity 再试。');
      })
      .finally(() => {
        if (!ac.signal.aborted) setLoading(false);
      });
    return () => ac.abort();
  }, [assetClass]);

  const products = data?.products.map((p) => p.product) ?? [];
  const showIvMny = levelFamilies.includes('IV');

  const buildSpread = (L: VolLeg, R: VolLeg) =>
    downsample(alignDiff(getSeries(data, L), getSeries(data, R)), 450);

  const spread1 = useMemo(() => buildSpread(s1L, s1R), [data, s1L, s1R]);
  const spread2 = useMemo(() => buildSpread(s2L, s2R), [data, s2L, s2R]);
  const sos = useMemo(() => {
    const a = new Map(spread1.map((p) => [p.d, p.v]));
    const b = new Map(spread2.map((p) => [p.d, p.v]));
    return downsample(alignDiff(a, b), 450);
  }, [spread1, spread2]);

  const levelChart = useMemo(() => {
    if (!data) return [];
    const dates = new Set<string>();
    const maps: Record<string, Map<string, number>> = {};
    for (const p of levelProds) {
      for (const fam of levelFamilies) {
        for (const ten of levelTenors) {
          if (fam === 'RV') {
            const key = `${p}_RV_${ten}`;
            maps[key] = getSeries(data, { product: p, family: 'RV', tenor: ten, moneyness: 'ATM' });
            for (const d of maps[key].keys()) dates.add(d);
          } else {
            for (const m of levelMny) {
              const key = `${p}_IV_${ten}_${m}`;
              maps[key] = getSeries(data, { product: p, family: 'IV', tenor: ten, moneyness: m });
              for (const d of maps[key].keys()) dates.add(d);
            }
          }
        }
      }
    }
    return downsample(
      [...dates].sort().map((d) => {
        const row: Record<string, string | number | null> = { d };
        for (const k of Object.keys(maps)) {
          const v = maps[k].get(d);
          row[k] = v == null ? null : v * 100;
        }
        return row;
      }),
      450
    );
  }, [data, levelProds, levelFamilies, levelTenors, levelMny]);

  const SpreadBlock: React.FC<{ title: string; series: { d: string; v: number }[]; name: string; stroke: string }> = ({
    title,
    series,
    name,
    stroke,
  }) => {
    const chart = series.map((p) => ({ d: p.d, v: p.v * 100 }));
    return (
      <div className="bg-white rounded-lg border border-slate-200 p-4">
        <h4 className="font-bold text-[#000066] text-sm mb-2">{title}</h4>
        <div className="flex flex-col lg:flex-row gap-3">
          <div className="flex-1 h-[240px] min-w-0">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chart}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="d" tick={{ fontSize: 9 }} minTickGap={40} />
                <YAxis tick={{ fontSize: 10 }} domain={['auto', 'auto']} unit="%" />
                <Tooltip contentStyle={{ fontSize: 11 }} formatter={(v: any) => [`${Number(v).toFixed(2)}%`, name]} />
                <Legend wrapperStyle={{ fontSize: 10 }} />
                <Line type="monotone" dataKey="v" name={name} stroke={stroke} dot={false} strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <PercentilePanel values={series.map((x) => x.v)} />
        </div>
      </div>
    );
  };

  return (
    <div className="space-y-3 border border-slate-200 rounded-lg p-4 bg-white">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-bold text-[#000066] text-sm">{TITLE[assetClass]}</h3>
        <span className="text-[10px] text-slate-400 font-mono">{data?.unit}</span>
      </div>
      {loading && (
        <div className="text-xs text-slate-500 flex items-center gap-2">
          <span className="inline-block h-2 w-2 rounded-full bg-[#EAB308] animate-pulse" />
          {loadStatus}
        </div>
      )}
      {err && !loading && (
        <div className="text-xs text-amber-800 bg-amber-50 border border-amber-100 rounded p-2">{err}</div>
      )}
      {!loading && !err && products.length === 0 && (
        <div className="text-[11px] text-amber-800 bg-amber-50 border border-amber-100 rounded px-3 py-2">
          无 vol 序列（需 data/vol_spread_levels.parquet）。
        </div>
      )}

      {data && products.length > 0 && (
        <>
          <div className="bg-slate-50/80 border border-slate-100 rounded-lg p-3 space-y-2">
            <div className="text-[11px] font-bold text-[#000066]">① Levels（可多选 RV+IV、多期限）</div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[10px] font-bold text-slate-400 uppercase">Product</span>
              {products.map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => setLevelProds((prev) => toggleKeepOne(prev, p))}
                  className={`px-2 py-1 text-[10px] font-bold rounded border ${
                    levelProds.includes(p)
                      ? 'bg-[#000066] text-white border-[#000066]'
                      : 'bg-white text-slate-500 border-slate-200'
                  }`}
                >
                  {p}
                  {data.products.find((x) => x.product === p)?.asset_class === 'commodity' ? ' ·C' : ''}
                </button>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[10px] font-bold text-slate-400 uppercase">Family</span>
              {(['RV', 'IV'] as VolFamily[]).map((f) => (
                <button
                  key={f}
                  type="button"
                  onClick={() => setLevelFamilies((prev) => toggleKeepOne(prev, f))}
                  className={`px-2 py-1 text-[10px] font-bold rounded border ${
                    levelFamilies.includes(f)
                      ? 'bg-[#FFF100] text-[#000066] border-[#EAB308]'
                      : 'bg-white text-slate-500 border-slate-200'
                  }`}
                >
                  {f}
                </button>
              ))}
              <span className="mx-1 text-slate-300">|</span>
              <span className="text-[10px] font-bold text-slate-400 uppercase">Tenor</span>
              {TENORS.map((t) => (
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
                  {t}
                </button>
              ))}
              {showIvMny && (
                <>
                  <span className="mx-1 text-slate-300">|</span>
                  <span className="text-[10px] font-bold text-slate-400 uppercase">IV mny</span>
                  {MNY.map((m) => (
                    <button
                      key={m}
                      type="button"
                      onClick={() => setLevelMny((prev) => toggleKeepOne(prev, m))}
                      className={`px-2 py-1 text-[10px] font-bold rounded border ${
                        levelMny.includes(m)
                          ? 'bg-[#E20000]/10 text-[#E20000] border-[#E20000]/40'
                          : 'bg-white text-slate-500 border-slate-200'
                      }`}
                    >
                      {m}
                    </button>
                  ))}
                </>
              )}
            </div>
            <div className="h-[280px]">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={levelChart}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                  <XAxis dataKey="d" tick={{ fontSize: 9 }} minTickGap={40} />
                  <YAxis tick={{ fontSize: 10 }} domain={['auto', 'auto']} unit="%" />
                  <Tooltip contentStyle={{ fontSize: 11 }} />
                  <Legend wrapperStyle={{ fontSize: 10 }} />
                  {Object.keys(levelChart[0] ?? {})
                    .filter((k) => k !== 'd')
                    .map((k, i) => (
                      <Line
                        key={k}
                        type="monotone"
                        dataKey={k}
                        name={k.replace(/_/g, ' ')}
                        stroke={COLORS[i % COLORS.length]}
                        dot={false}
                        strokeWidth={1.5}
                        connectNulls
                      />
                    ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="grid md:grid-cols-2 gap-3">
            <div className="space-y-2 border border-slate-100 rounded-lg p-3 bg-slate-50/50">
              <div className="text-[11px] font-bold text-[#000066]">Spread1</div>
              <LegPick label="Left" products={products} leg={s1L} onChange={setS1L} />
              <LegPick label="Right" products={products} leg={s1R} onChange={setS1R} />
              <div className="text-[10px] font-mono text-slate-500">
                {legKey(s1L)} − {legKey(s1R)}
              </div>
            </div>
            <div className="space-y-2 border border-slate-100 rounded-lg p-3 bg-slate-50/50">
              <div className="text-[11px] font-bold text-[#000066]">Spread2</div>
              <LegPick label="Left" products={products} leg={s2L} onChange={setS2L} />
              <LegPick label="Right" products={products} leg={s2R} onChange={setS2R} />
              <div className="text-[10px] font-mono text-slate-500">
                {legKey(s2L)} − {legKey(s2R)}
              </div>
            </div>
          </div>

          <SpreadBlock title="Spread1" series={spread1} name={`${legKey(s1L)} − ${legKey(s1R)}`} stroke="#000066" />
          <SpreadBlock title="Spread2" series={spread2} name={`${legKey(s2L)} − ${legKey(s2R)}`} stroke="#EAB308" />
          <SpreadBlock title="Spread of Spread = Spread1 − Spread2" series={sos} name="SoS" stroke="#E20000" />
          <p className="text-[10px] text-slate-400">{data.method}</p>
        </>
      )}
    </div>
  );
};

export default VolSpreadPanel;
