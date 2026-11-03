import React, { useEffect, useMemo, useState } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import { fetchSynthTenors, SynthTenorsResponse } from '../../services/api_v1';
import { SplitSquareHorizontal } from 'lucide-react';

const COLORS = ['#000066', '#EAB308', '#E20000', '#16a34a', '#7c3aed', '#0891b2'];
const TENORS = ['3M', '6M', '9M', '18M'] as const;
type Tenor = (typeof TENORS)[number];
type Product = 'HO' | 'IO' | 'MO';

interface SpreadDef {
  id: string;
  label: string;
  product: Product | 'CROSS';
  left: { product: Product; tenor: Tenor };
  right: { product: Product; tenor: Tenor };
}

function seriesMap(levels: SynthTenorsResponse['levels'], product: Product, tenor: Tenor) {
  const arr = levels?.[product]?.[tenor] ?? [];
  const m = new Map<string, number>();
  for (const p of arr) m.set(p.d, p.settle);
  return m;
}

function alignSpread(
  a: Map<string, number>,
  b: Map<string, number>
): { d: string; v: number }[] {
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

const SpreadView: React.FC = () => {
  const [data, setData] = useState<SynthTenorsResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Level chart: products × tenors
  const [levelProducts, setLevelProducts] = useState<Product[]>(['MO', 'IO']);
  const [levelTenors, setLevelTenors] = useState<Tenor[]>(['3M', '6M', '9M', '18M']);

  // Spread selectors
  const [spLeftProd, setSpLeftProd] = useState<Product>('MO');
  const [spLeftTenor, setSpLeftTenor] = useState<Tenor>('3M');
  const [spRightProd, setSpRightProd] = useState<Product>('MO');
  const [spRightTenor, setSpRightTenor] = useState<Tenor>('6M');

  // Saved spreads for SoS (max a few)
  const [savedSpreads, setSavedSpreads] = useState<SpreadDef[]>([
    {
      id: 'MO|3M-6M',
      label: 'MO 3M−6M',
      product: 'MO',
      left: { product: 'MO', tenor: '3M' },
      right: { product: 'MO', tenor: '6M' },
    },
    {
      id: 'MO|6M-9M',
      label: 'MO 6M−9M',
      product: 'MO',
      left: { product: 'MO', tenor: '6M' },
      right: { product: 'MO', tenor: '9M' },
    },
  ]);
  const [sosA, setSosA] = useState('MO|3M-6M');
  const [sosB, setSosB] = useState('MO|6M-9M');

  useEffect(() => {
    setLoading(true);
    fetchSynthTenors()
      .then((d) => {
        setData(d);
        setErr(null);
      })
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }, []);

  const currentSpreadDef = useMemo<SpreadDef>(() => {
    const cross = spLeftProd !== spRightProd;
    const id = cross
      ? `${spLeftProd}${spLeftTenor}-${spRightProd}${spRightTenor}`
      : `${spLeftProd}|${spLeftTenor}-${spRightTenor}`;
    const label = cross
      ? `${spLeftProd} ${spLeftTenor} − ${spRightProd} ${spRightTenor}`
      : `${spLeftProd} ${spLeftTenor}−${spRightTenor}`;
    return {
      id,
      label,
      product: cross ? 'CROSS' : spLeftProd,
      left: { product: spLeftProd, tenor: spLeftTenor },
      right: { product: spRightProd, tenor: spRightTenor },
    };
  }, [spLeftProd, spLeftTenor, spRightProd, spRightTenor]);

  const levelChartData = useMemo(() => {
    if (!data) return [];
    const dates = new Set<string>();
    for (const p of levelProducts) {
      for (const t of levelTenors) {
        for (const pt of data.levels[p]?.[t] ?? []) dates.add(pt.d);
      }
    }
    const sorted = [...dates].sort();
    const maps: Record<string, Map<string, number>> = {};
    for (const p of levelProducts) {
      for (const t of levelTenors) {
        maps[`${p}_${t}`] = seriesMap(data.levels, p, t);
      }
    }
    const rows = sorted.map((d) => {
      const row: Record<string, string | number | null> = { d };
      for (const key of Object.keys(maps)) {
        row[key] = maps[key].get(d) ?? null;
      }
      return row;
    });
    return downsample(rows, 450);
  }, [data, levelProducts, levelTenors]);

  const spreadSeries = useMemo(() => {
    if (!data) return [];
    const a = seriesMap(data.levels, currentSpreadDef.left.product, currentSpreadDef.left.tenor);
    const b = seriesMap(data.levels, currentSpreadDef.right.product, currentSpreadDef.right.tenor);
    return downsample(alignSpread(a, b), 450);
  }, [data, currentSpreadDef]);

  const resolveSpreadSeries = (def: SpreadDef) => {
    if (!data) return [];
    const a = seriesMap(data.levels, def.left.product, def.left.tenor);
    const b = seriesMap(data.levels, def.right.product, def.right.tenor);
    return alignSpread(a, b);
  };

  const sosSeries = useMemo(() => {
    const da = savedSpreads.find((s) => s.id === sosA);
    const db = savedSpreads.find((s) => s.id === sosB);
    if (!da || !db || !data) return [];
    const sa = new Map(resolveSpreadSeries(da).map((p) => [p.d, p.v]));
    const sb = new Map(resolveSpreadSeries(db).map((p) => [p.d, p.v]));
    return downsample(alignSpread(sa, sb), 450);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, savedSpreads, sosA, sosB]);

  const addCurrentSpread = () => {
    setSavedSpreads((prev) => {
      if (prev.some((s) => s.id === currentSpreadDef.id)) return prev;
      return [...prev, currentSpreadDef].slice(-12);
    });
  };

  const toggleProduct = (p: Product) => {
    setLevelProducts((prev) =>
      prev.includes(p) ? (prev.length === 1 ? prev : prev.filter((x) => x !== p)) : [...prev, p]
    );
  };

  const toggleTenor = (t: Tenor) => {
    setLevelTenors((prev) =>
      prev.includes(t) ? (prev.length === 1 ? prev : prev.filter((x) => x !== t)) : [...prev, t]
    );
  };

  const selectClass =
    'px-2 py-1.5 text-xs font-bold border border-slate-200 rounded-md bg-slate-50 text-[#000066] focus:outline-none focus:ring-2 focus:ring-[#000066]';

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold text-[#000066] flex items-center gap-2">
            <SplitSquareHorizontal size={18} className="text-[#EAB308]" />
            Futures Tenor Spread
          </h2>
          <p className="text-[11px] text-slate-500 max-w-3xl mt-1">
            用挂牌合约的<strong>实际剩余到期天数</strong>线性插值，合成常数期限 3M / 6M / 9M / 18M
            settle；再选两腿看 spread，或选两个 spread 看 spread-of-spread（同品种跨期或跨品种）。
            纵轴为指数点。
          </p>
        </div>
        {data && (
          <div className="text-[10px] font-mono text-slate-400 bg-white border border-slate-200 rounded px-2 py-1">
            days: {Object.entries(data.tenor_days).map(([k, v]) => `${k}=${v}`).join(' · ')}
          </div>
        )}
      </div>

      {loading && <div className="text-xs text-slate-500">Loading synth tenors…</div>}
      {err && <div className="text-xs text-red-600 bg-red-50 border border-red-100 rounded p-2">{err}</div>}

      {/* ---- Levels ---- */}
      <div className="bg-white rounded-lg border border-slate-200 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
          <h3 className="font-bold text-[#000066] text-sm">① 合成合约点位（3 / 6 / 9 / 18M）</h3>
          <div className="flex flex-wrap gap-2 items-center">
            <span className="text-[10px] text-slate-400 uppercase font-bold">Product</span>
            {(['HO', 'IO', 'MO'] as Product[]).map((p) => (
              <button
                key={p}
                onClick={() => toggleProduct(p)}
                className={`px-2 py-1 text-[10px] font-bold rounded border ${
                  levelProducts.includes(p)
                    ? 'bg-[#000066] text-white border-[#000066]'
                    : 'bg-white text-slate-500 border-slate-200'
                }`}
              >
                {p}
              </button>
            ))}
            <span className="text-[10px] text-slate-400 uppercase font-bold ml-2">Tenor</span>
            {TENORS.map((t) => (
              <button
                key={t}
                onClick={() => toggleTenor(t)}
                className={`px-2 py-1 text-[10px] font-bold rounded border ${
                  levelTenors.includes(t)
                    ? 'bg-[#FFF100] text-[#000066] border-[#EAB308]'
                    : 'bg-white text-slate-500 border-slate-200'
                }`}
              >
                {t}
              </button>
            ))}
          </div>
        </div>
        {data?.coverage && (
          <div className="flex flex-wrap gap-2 mb-2 text-[10px] text-slate-500">
            {Object.entries(data.coverage).map(([p, cov]) => (
              <span key={p} className="bg-slate-50 rounded px-2 py-0.5 border border-slate-100">
                {p} days: {TENORS.map((t) => `${t}=${(cov as any)[t] ?? 0}`).join(' / ')}
              </span>
            ))}
            <span className="text-amber-700 bg-amber-50 border border-amber-100 rounded px-2 py-0.5">
              9M 多为外推；18M 在中金所 4 合约挂牌下通常不可得
            </span>
          </div>
        )}
        <div className="h-[320px]">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={levelChartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="d" tick={{ fontSize: 9 }} minTickGap={40} />
              <YAxis tick={{ fontSize: 10 }} domain={['auto', 'auto']} />
              <Tooltip contentStyle={{ fontSize: 11 }} />
              <Legend wrapperStyle={{ fontSize: 10 }} />
              {levelProducts.flatMap((p, pi) =>
                levelTenors.map((t, ti) => (
                  <Line
                    key={`${p}_${t}`}
                    type="monotone"
                    dataKey={`${p}_${t}`}
                    name={`${p} ${t}`}
                    stroke={COLORS[(pi * 4 + ti) % COLORS.length]}
                    dot={false}
                    strokeWidth={1.6}
                    connectNulls
                  />
                ))
              )}
            </LineChart>
          </ResponsiveContainer>
        </div>
        <p className="text-[10px] text-slate-400 mt-2">{data?.method}</p>
      </div>

      {/* ---- Spread ---- */}
      <div className="bg-white rounded-lg border border-slate-200 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
          <h3 className="font-bold text-[#000066] text-sm">② Spread（选两个合成合约）</h3>
          <div className="flex flex-wrap items-center gap-2">
            <select className={selectClass} value={spLeftProd} onChange={(e) => setSpLeftProd(e.target.value as Product)}>
              {(['HO', 'IO', 'MO'] as Product[]).map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
            <select className={selectClass} value={spLeftTenor} onChange={(e) => setSpLeftTenor(e.target.value as Tenor)}>
              {TENORS.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
            <span className="text-xs font-bold text-slate-400">−</span>
            <select className={selectClass} value={spRightProd} onChange={(e) => setSpRightProd(e.target.value as Product)}>
              {(['HO', 'IO', 'MO'] as Product[]).map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
            <select className={selectClass} value={spRightTenor} onChange={(e) => setSpRightTenor(e.target.value as Tenor)}>
              {TENORS.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
            <button
              onClick={addCurrentSpread}
              className="px-3 py-1.5 text-[10px] font-bold rounded bg-[#000066] text-white hover:bg-[#000044]"
            >
              加入 SoS 候选
            </button>
          </div>
        </div>
        <div className="grid grid-cols-3 gap-2 mb-2 text-[10px]">
          <div className="bg-slate-50 rounded px-2 py-1 flex justify-between border border-slate-100">
            <span className="text-slate-500">label</span>
            <span className="font-mono font-bold text-[#000066]">{currentSpreadDef.label}</span>
          </div>
          <div className="bg-slate-50 rounded px-2 py-1 flex justify-between border border-slate-100">
            <span className="text-slate-500">last</span>
            <span className="font-mono font-bold text-[#000066]">
              {spreadSeries.length ? spreadSeries[spreadSeries.length - 1].v.toFixed(1) : '—'}
            </span>
          </div>
          <div className="bg-slate-50 rounded px-2 py-1 flex justify-between border border-slate-100">
            <span className="text-slate-500">n</span>
            <span className="font-mono font-bold text-[#000066]">{spreadSeries.length}</span>
          </div>
        </div>
        <div className="h-[280px]">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={spreadSeries}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="d" tick={{ fontSize: 9 }} minTickGap={40} />
              <YAxis tick={{ fontSize: 10 }} domain={['auto', 'auto']} label={{ value: 'points', angle: -90, position: 'insideLeft', fontSize: 10 }} />
              <Tooltip contentStyle={{ fontSize: 11 }} />
              <Legend wrapperStyle={{ fontSize: 10 }} />
              <Line type="monotone" dataKey="v" name={currentSpreadDef.label} stroke="#000066" dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="flex flex-wrap gap-1 mt-2">
          {savedSpreads.map((s) => (
            <span key={s.id} className="text-[10px] px-2 py-0.5 rounded-full border border-slate-200 bg-slate-50 text-slate-600">
              {s.label}
            </span>
          ))}
        </div>
      </div>

      {/* ---- Spread of spread ---- */}
      <div className="bg-white rounded-lg border border-slate-200 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
          <h3 className="font-bold text-[#000066] text-sm">③ Spread of Spread（选两个 spread）</h3>
          <div className="flex flex-wrap items-center gap-2">
            <select className={selectClass} value={sosA} onChange={(e) => setSosA(e.target.value)}>
              {savedSpreads.map((s) => (
                <option key={s.id} value={s.id}>{s.label}</option>
              ))}
            </select>
            <span className="text-xs font-bold text-slate-400">−</span>
            <select className={selectClass} value={sosB} onChange={(e) => setSosB(e.target.value)}>
              {savedSpreads.map((s) => (
                <option key={s.id} value={s.id}>{s.label}</option>
              ))}
            </select>
          </div>
        </div>
        <p className="text-[10px] text-slate-500 mb-2">
          先在 ② 配置任意同品种跨期或跨品种 spread，点「加入 SoS 候选」，再在此两两相减。
        </p>
        <div className="h-[280px]">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={sosSeries}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="d" tick={{ fontSize: 9 }} minTickGap={40} />
              <YAxis tick={{ fontSize: 10 }} domain={['auto', 'auto']} />
              <Tooltip contentStyle={{ fontSize: 11 }} />
              <Legend wrapperStyle={{ fontSize: 10 }} />
              <Line
                type="monotone"
                dataKey="v"
                name={`(${savedSpreads.find((s) => s.id === sosA)?.label ?? sosA}) − (${savedSpreads.find((s) => s.id === sosB)?.label ?? sosB})`}
                stroke="#E20000"
                dot={false}
                strokeWidth={2}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
};

export default SpreadView;
