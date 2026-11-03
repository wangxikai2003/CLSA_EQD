import React, { useEffect, useMemo, useState } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  BarChart, Bar, ReferenceLine, Cell,
} from 'recharts';
import { fetchSynthTenors, SynthTenorsResponse } from '../../services/api_v1';
import { SplitSquareHorizontal } from 'lucide-react';

const COLORS = ['#000066', '#EAB308', '#E20000', '#16a34a', '#7c3aed', '#0891b2'];
const FUT_TENORS = ['3M', '6M', '9M', '18M'] as const;
type FutTenor = (typeof FUT_TENORS)[number];
type LegKind = FutTenor | 'SPOT';

type ProductMeta = SynthTenorsResponse['products'][number];

interface LegSel {
  product: string;
  kind: LegKind;
}

function seriesMap(levels: SynthTenorsResponse['levels'], product: string, kind: LegKind) {
  const arr = levels?.[product]?.[kind] ?? [];
  const m = new Map<string, number>();
  for (const p of arr) m.set(p.d, p.settle);
  return m;
}

function alignDiff(a: Map<string, number>, b: Map<string, number>): { d: string; v: number }[] {
  const out: { d: string; v: number }[] = [];
  for (const [d, va] of a) {
    const vb = b.get(d);
    if (vb == null) continue;
    out.push({ d, v: va - vb });
  }
  return out;
}

function downsample<T>(arr: T[], maxPts = 500): T[] {
  if (arr.length <= maxPts) return arr;
  const step = Math.ceil(arr.length / maxPts);
  const out = arr.filter((_, i) => i % step === 0);
  if (out[out.length - 1] !== arr[arr.length - 1]) out.push(arr[arr.length - 1]);
  return out;
}

function percentile(sorted: number[], x: number): number {
  if (!sorted.length) return NaN;
  let lo = 0;
  for (const v of sorted) {
    if (v <= x) lo += 1;
  }
  return (100 * lo) / sorted.length;
}

function histBins(values: number[], nBins = 24) {
  if (!values.length) return [] as { x0: number; x1: number; mid: number; count: number; label: string }[];
  const mn = Math.min(...values);
  const mx = Math.max(...values);
  const width = mx === mn ? 1 : (mx - mn) / nBins;
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
    b.label = `${b.x0.toFixed(0)}`;
  });
  return bins;
}

function legLabel(leg: LegSel) {
  return `${leg.product} ${leg.kind}`;
}

function spreadLabel(a: LegSel, b: LegSel) {
  return `${legLabel(a)} − ${legLabel(b)}`;
}

const selectClass =
  'px-2 py-1.5 text-xs font-bold border border-slate-200 rounded-md bg-slate-50 text-[#000066] focus:outline-none focus:ring-2 focus:ring-[#000066]';

const PercentilePanel: React.FC<{ values: number[]; title?: string }> = ({ values, title }) => {
  const last = values.length ? values[values.length - 1] : NaN;
  const sorted = useMemo(() => [...values].sort((a, b) => a - b), [values]);
  const pct = Number.isFinite(last) ? percentile(sorted, last) : NaN;
  const bins = useMemo(() => histBins(values), [values]);
  const activeIdx = useMemo(() => {
    if (!bins.length || !Number.isFinite(last)) return -1;
    return bins.findIndex((b) => last >= b.x0 && last <= b.x1 + 1e-9);
  }, [bins, last]);

  return (
    <div className="w-full lg:w-[220px] shrink-0 bg-slate-50 border border-slate-200 rounded-lg p-3 flex flex-col gap-2">
      <div className="text-[10px] uppercase tracking-wide text-slate-500 font-bold">{title ?? 'Hist. percentile'}</div>
      <div className="text-2xl font-bold text-[#000066] tabular-nums">
        {Number.isFinite(pct) ? `${pct.toFixed(1)}%` : '—'}
      </div>
      <div className="text-[10px] text-slate-500 font-mono">
        last {Number.isFinite(last) ? last.toFixed(1) : '—'} · n={values.length}
      </div>
      <div className="h-[160px] mt-1">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={bins} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
            <XAxis dataKey="label" hide />
            <YAxis tick={{ fontSize: 8 }} width={28} />
            <Tooltip
              contentStyle={{ fontSize: 10 }}
              formatter={(v: any) => [v, 'count']}
              labelFormatter={(_, payload: any) => {
                const b = payload?.[0]?.payload;
                return b ? `[${b.x0.toFixed(1)}, ${b.x1.toFixed(1)}]` : '';
              }}
            />
            <Bar dataKey="count" radius={[2, 2, 0, 0]}>
              {bins.map((_, i) => (
                <Cell key={i} fill={i === activeIdx ? '#E20000' : '#000066'} fillOpacity={i === activeIdx ? 1 : 0.35} />
              ))}
            </Bar>
            {Number.isFinite(last) && <ReferenceLine x={bins[activeIdx]?.label} stroke="#EAB308" />}
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="text-[9px] text-slate-400 leading-snug">红柱=当前点所在分箱；分位=历史≤当前的占比</div>
    </div>
  );
};

const LegPickers: React.FC<{
  label: string;
  products: ProductMeta[];
  leg: LegSel;
  onChange: (l: LegSel) => void;
}> = ({ label, products, leg, onChange }) => {
  const meta = products.find((p) => p.product === leg.product);
  const kinds: LegKind[] = [...FUT_TENORS, ...(meta?.has_spot ? (['SPOT'] as LegKind[]) : [])];
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-[10px] font-bold text-slate-400 uppercase w-14">{label}</span>
      <select
        className={selectClass}
        value={leg.product}
        onChange={(e) => {
          const p = e.target.value;
          const m = products.find((x) => x.product === p);
          const kind = leg.kind === 'SPOT' && !m?.has_spot ? '3M' : leg.kind;
          onChange({ product: p, kind });
        }}
      >
        {products.map((p) => (
          <option key={p.product} value={p.product}>
            {p.product} · {p.bbg ?? p.wind_root ?? p.product} · {p.name}
            {p.asset_class === 'commodity' ? ' ·CMD' : ''}
          </option>
        ))}
      </select>
      <select
        className={selectClass}
        value={leg.kind}
        onChange={(e) => onChange({ ...leg, kind: e.target.value as LegKind })}
      >
        {kinds.map((k) => (
          <option key={k} value={k}>{k === 'SPOT' ? 'SPOT 现货' : k}</option>
        ))}
      </select>
    </div>
  );
};

const SpreadView: React.FC = () => {
  const [data, setData] = useState<SynthTenorsResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [assetFilter, setAssetFilter] = useState<'all' | 'equity' | 'commodity'>('all');

  const [levelProducts, setLevelProducts] = useState<string[]>(['IM', 'IF']);
  const [levelKinds, setLevelKinds] = useState<LegKind[]>(['SPOT', '3M', '6M']);

  // Spread 1 & 2 — SoS = S1 − S2 automatically (product ids = Wind futures roots)
  const [s1L, setS1L] = useState<LegSel>({ product: 'IM', kind: '3M' });
  const [s1R, setS1R] = useState<LegSel>({ product: 'IM', kind: '6M' });
  const [s2L, setS2L] = useState<LegSel>({ product: 'IM', kind: 'SPOT' });
  const [s2R, setS2R] = useState<LegSel>({ product: 'IM', kind: '3M' });

  useEffect(() => {
    setLoading(true);
    fetchSynthTenors(false, assetFilter)
      .then((d) => {
        setData(d);
        setErr(null);
        const prods = d.products.map((p) => p.product);
        setLevelProducts((prev) => {
          const keep = prev.filter((p) => prods.includes(p));
          return keep.length ? keep : prods.slice(0, 2);
        });
        const prefer = d.products.find((p) => p.product === 'IM') ?? d.products[0];
        const first = prefer?.product ?? 'IM';
        const hasSpot = !!prefer?.has_spot;
        setS1L({ product: first, kind: '3M' });
        setS1R({ product: first, kind: '6M' });
        setS2L({ product: first, kind: hasSpot ? 'SPOT' : '3M' });
        setS2R({ product: first, kind: hasSpot ? '3M' : '6M' });
      })
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }, [assetFilter]);

  const products = data?.products ?? [];

  const buildSpread = (L: LegSel, R: LegSel) => {
    if (!data) return [] as { d: string; v: number }[];
    return alignDiff(seriesMap(data.levels, L.product, L.kind), seriesMap(data.levels, R.product, R.kind));
  };

  const spread1 = useMemo(() => downsample(buildSpread(s1L, s1R), 450), [data, s1L, s1R]);
  const spread2 = useMemo(() => downsample(buildSpread(s2L, s2R), 450), [data, s2L, s2R]);
  const sos = useMemo(() => {
    const a = new Map(spread1.map((p) => [p.d, p.v]));
    const b = new Map(spread2.map((p) => [p.d, p.v]));
    return downsample(alignDiff(a, b), 450);
  }, [spread1, spread2]);

  const levelChartData = useMemo(() => {
    if (!data) return [];
    const dates = new Set<string>();
    for (const p of levelProducts) {
      for (const k of levelKinds) {
        for (const pt of data.levels[p]?.[k] ?? []) dates.add(pt.d);
      }
    }
    const maps: Record<string, Map<string, number>> = {};
    for (const p of levelProducts) {
      for (const k of levelKinds) {
        maps[`${p}_${k}`] = seriesMap(data.levels, p, k);
      }
    }
    return downsample(
      [...dates].sort().map((d) => {
        const row: Record<string, string | number | null> = { d };
        for (const key of Object.keys(maps)) row[key] = maps[key].get(d) ?? null;
        return row;
      }),
      450
    );
  }, [data, levelProducts, levelKinds]);

  const toggleProd = (p: string) => {
    setLevelProducts((prev) =>
      prev.includes(p) ? (prev.length === 1 ? prev : prev.filter((x) => x !== p)) : [...prev, p]
    );
  };
  const toggleKind = (k: LegKind) => {
    setLevelKinds((prev) =>
      prev.includes(k) ? (prev.length === 1 ? prev : prev.filter((x) => x !== k)) : [...prev, k]
    );
  };

  const SpreadBlock: React.FC<{
    title: string;
    series: { d: string; v: number }[];
    name: string;
    stroke: string;
  }> = ({ title, series, name, stroke }) => (
    <div className="bg-white rounded-lg border border-slate-200 p-4">
      <h3 className="font-bold text-[#000066] text-sm mb-2">{title}</h3>
      <div className="flex flex-col lg:flex-row gap-3">
        <div className="flex-1 h-[260px] min-w-0">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={series}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="d" tick={{ fontSize: 9 }} minTickGap={40} />
              <YAxis tick={{ fontSize: 10 }} domain={['auto', 'auto']} />
              <Tooltip contentStyle={{ fontSize: 11 }} />
              <Legend wrapperStyle={{ fontSize: 10 }} />
              <Line type="monotone" dataKey="v" name={name} stroke={stroke} dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <PercentilePanel values={series.map((x) => x.v)} />
      </div>
    </div>
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold text-[#000066] flex items-center gap-2">
            <SplitSquareHorizontal size={18} className="text-[#EAB308]" />
            Tenor / Basis Spread
          </h2>
          <p className="text-[11px] text-slate-500 max-w-3xl mt-1">
            合成期限用挂牌合约<strong>实际 DTM</strong>配比；股指可加 <strong>SPOT 现货</strong>做期现。
            Spread1 / Spread2 任选两腿；Spread-of-spread 自动为 Spread1 − Spread2。
            品种代码为 <strong>Wind 期货根</strong>（IH/IF/IM，非 HO/IO/MO）+ Bloomberg generic（如 IMc1 Index）。
          </p>
        </div>
        <div className="flex gap-1 bg-slate-100 p-1 rounded-lg">
          {([
            ['all', 'All'],
            ['equity', 'Equity'],
            ['commodity', 'Commodity'],
          ] as const).map(([id, lab]) => (
            <button
              key={id}
              onClick={() => setAssetFilter(id)}
              className={`px-3 py-1.5 text-xs font-bold rounded-md ${
                assetFilter === id ? 'bg-white text-[#000066] shadow' : 'text-slate-500'
              }`}
            >
              {lab}
            </button>
          ))}
        </div>
      </div>

      {loading && <div className="text-xs text-slate-500">Loading…</div>}
      {err && <div className="text-xs text-red-600 bg-red-50 border border-red-100 rounded p-2">{err}</div>}
      {data && !data.commodity_enabled && assetFilter !== 'equity' && (
        <div className="text-[11px] text-amber-800 bg-amber-50 border border-amber-100 rounded px-3 py-2">
          商品期货库未启用（需 data/commodity_fut.sqlite）。商品现货尚未落地。
        </div>
      )}
      {data?.code_convention && (
        <div className="text-[10px] text-slate-400 font-mono">{data.code_convention}</div>
      )}

      {/* Levels */}
      <div className="bg-white rounded-lg border border-slate-200 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
          <h3 className="font-bold text-[#000066] text-sm">① 价格水平（合成期限 / 现货）</h3>
          <div className="flex flex-wrap gap-1 items-center">
            {products.map((p) => (
              <button
                key={p.product}
                onClick={() => toggleProd(p.product)}
                className={`px-2 py-1 text-[10px] font-bold rounded border ${
                  levelProducts.includes(p.product)
                    ? 'bg-[#000066] text-white border-[#000066]'
                    : 'bg-white text-slate-500 border-slate-200'
                }`}
              >
                {p.product}
              </button>
            ))}
            <span className="mx-1 text-slate-300">|</span>
            {([...FUT_TENORS, 'SPOT'] as LegKind[]).map((k) => (
              <button
                key={k}
                onClick={() => toggleKind(k)}
                className={`px-2 py-1 text-[10px] font-bold rounded border ${
                  levelKinds.includes(k)
                    ? 'bg-[#FFF100] text-[#000066] border-[#EAB308]'
                    : 'bg-white text-slate-500 border-slate-200'
                }`}
              >
                {k}
              </button>
            ))}
          </div>
        </div>
        <div className="h-[300px]">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={levelChartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="d" tick={{ fontSize: 9 }} minTickGap={40} />
              <YAxis tick={{ fontSize: 10 }} domain={['auto', 'auto']} />
              <Tooltip contentStyle={{ fontSize: 11 }} />
              <Legend wrapperStyle={{ fontSize: 10 }} />
              {levelProducts.flatMap((p, pi) =>
                levelKinds.map((k, ki) => (
                  <Line
                    key={`${p}_${k}`}
                    type="monotone"
                    dataKey={`${p}_${k}`}
                    name={`${p} ${k}`}
                    stroke={COLORS[(pi * 3 + ki) % COLORS.length]}
                    dot={false}
                    strokeWidth={1.5}
                    connectNulls
                  />
                ))
              )}
            </LineChart>
          </ResponsiveContainer>
        </div>
        <p className="text-[10px] text-slate-400 mt-2">{data?.method} · {data?.spot_source}</p>
      </div>

      {/* Spread1 / Spread2 controls */}
      <div className="bg-white rounded-lg border border-slate-200 p-4 space-y-3">
        <h3 className="font-bold text-[#000066] text-sm">② Spread1 & Spread2（SoS = Spread1 − Spread2）</h3>
        <div className="grid md:grid-cols-2 gap-4">
          <div className="space-y-2 border border-slate-100 rounded-lg p-3 bg-slate-50/50">
            <div className="text-[11px] font-bold text-[#000066]">Spread1</div>
            <LegPickers label="Left" products={products} leg={s1L} onChange={setS1L} />
            <LegPickers label="Right" products={products} leg={s1R} onChange={setS1R} />
            <div className="text-[10px] font-mono text-slate-500">{spreadLabel(s1L, s1R)}</div>
          </div>
          <div className="space-y-2 border border-slate-100 rounded-lg p-3 bg-slate-50/50">
            <div className="text-[11px] font-bold text-[#000066]">Spread2</div>
            <LegPickers label="Left" products={products} leg={s2L} onChange={setS2L} />
            <LegPickers label="Right" products={products} leg={s2R} onChange={setS2R} />
            <div className="text-[10px] font-mono text-slate-500">{spreadLabel(s2L, s2R)}</div>
          </div>
        </div>
      </div>

      <SpreadBlock title="Spread1" series={spread1} name={spreadLabel(s1L, s1R)} stroke="#000066" />
      <SpreadBlock title="Spread2" series={spread2} name={spreadLabel(s2L, s2R)} stroke="#EAB308" />
      <SpreadBlock
        title="③ Spread of Spread = Spread1 − Spread2"
        series={sos}
        name={`(${spreadLabel(s1L, s1R)}) − (${spreadLabel(s2L, s2R)})`}
        stroke="#E20000"
      />
    </div>
  );
};

export default SpreadView;
