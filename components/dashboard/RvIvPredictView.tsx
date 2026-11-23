import React, { useEffect, useMemo, useState } from 'react';
import {
  Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  Area, ComposedChart,
} from 'recharts';
import { ProductCode } from '../../services/api';
import { fetchRvIvPredict, RvIvPredictResponse } from '../../services/api_v1';

interface Props {
  product?: ProductCode;
}

type Mode = 'rv' | 'bench';
type Source = 'futures' | 'spot';

const fmt = (v: number | null | undefined, d = 1) =>
  v == null || !Number.isFinite(v) ? '—' : v.toFixed(d);

const QuoteCard: React.FC<{ label: string; value: number | null | undefined; hint?: string; tone?: 'bid' | 'mid' | 'offer' | 'iv' }> = ({
  label, value, hint, tone = 'mid',
}) => {
  const color =
    tone === 'bid' ? 'text-emerald-700' :
    tone === 'offer' ? 'text-rose-700' :
    tone === 'iv' ? 'text-amber-700' :
    'text-[#000066]';
  return (
    <div className="bg-white border border-slate-200 rounded-lg px-3 py-2.5 min-w-[110px]">
      <div className="text-[10px] uppercase tracking-wide text-slate-500 font-bold">{label}</div>
      <div className={`text-xl font-bold tabular-nums ${color}`}>{fmt(value)}</div>
      {hint ? <div className="text-[10px] text-slate-400 mt-0.5">{hint}</div> : null}
    </div>
  );
};

const RvIvPredictView: React.FC<Props> = ({ product = 'IO' }) => {
  const [mode, setMode] = useState<Mode>('rv');
  const [source, setSource] = useState<Source>('futures');
  const [benchmark, setBenchmark] = useState<ProductCode>('IO');
  const [data, setData] = useState<RvIvPredictResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const ac = new AbortController();
    setLoading(true);
    setErr(null);
    fetchRvIvPredict(
      {
        product,
        mode,
        source,
        benchmark: mode === 'bench' ? benchmark : undefined,
      },
      ac.signal
    )
      .then((d) => {
        if (!ac.signal.aborted) setData(d);
      })
      .catch((e) => {
        if ((e as Error)?.name === 'AbortError' || ac.signal.aborted) return;
        setErr(String(e));
        setData(null);
      })
      .finally(() => {
        if (!ac.signal.aborted) setLoading(false);
      });
    return () => ac.abort();
  }, [product, mode, source, benchmark]);

  const quotes = data?.quotes ?? {};
  const current = data?.current ?? {};

  const chartRows = useMemo(() => {
    const rows = data?.series ?? [];
    return rows.map((p) => ({
      d: p.d.slice(5),
      rv21: p.rv_21,
      rv63: p.rv_63,
      rv252: p.rv_252,
      mid: p.mid,
      bid: p.bid,
      offer: p.offer,
    }));
  }, [data]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 className="text-sm font-bold text-[#000066]">RV / IV Predict</h3>
          <p className="text-[11px] text-slate-500 max-w-2xl">
            移植自 exo_rv_iv：用已实现波动率（或对基准指数的滚动回归）估计 Mid / Bid / Offer，
            并与当前 ATM IV 对比。数据源为本地近月期货收盘价或指数现货。
          </p>
        </div>
        <div className="flex flex-wrap gap-2 items-center">
          <div className="flex gap-1 bg-slate-100 p-1 rounded-lg">
            {([
              ['rv', 'From RV'],
              ['bench', 'From Benchmark'],
            ] as const).map(([id, label]) => (
              <button
                key={id}
                onClick={() => setMode(id)}
                className={`px-2.5 py-1 text-[11px] font-bold rounded-md ${
                  mode === id ? 'bg-white text-[#000066] shadow' : 'text-slate-500'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="flex gap-1 bg-slate-100 p-1 rounded-lg">
            {([
              ['futures', 'Futures'],
              ['spot', 'Spot'],
            ] as const).map(([id, label]) => (
              <button
                key={id}
                onClick={() => setSource(id)}
                className={`px-2.5 py-1 text-[11px] font-bold rounded-md ${
                  source === id ? 'bg-white text-[#000066] shadow' : 'text-slate-500'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          {mode === 'bench' && (
            <select
              value={benchmark}
              onChange={(e) => setBenchmark(e.target.value as ProductCode)}
              className="px-2 py-1.5 text-xs font-bold border border-slate-200 rounded-md bg-slate-50 text-[#000066]"
            >
              {(['IO', 'HO', 'MO'] as ProductCode[]).map((p) => (
                <option key={p} value={p}>Bench {p}</option>
              ))}
            </select>
          )}
        </div>
      </div>

      {loading && (
        <div className="text-xs text-slate-400 py-8 text-center">Computing RV / IV predict…</div>
      )}
      {err && (
        <div className="text-xs text-rose-600 bg-rose-50 border border-rose-100 rounded-lg px-3 py-2">{err}</div>
      )}

      {!loading && data && (
        <>
          <div className="flex flex-wrap gap-2">
            <QuoteCard label="Bid 3M" value={quotes.Bid_3M ?? quotes.Bid_63} tone="bid" hint="25th Dist" />
            <QuoteCard label="Mid 3M" value={quotes.Mid_3M ?? quotes.Mid_63} tone="mid" hint={mode === 'rv' ? 'RV 252' : 'IV_bm / β'} />
            <QuoteCard label="Offer 3M" value={quotes.Offer_3M ?? quotes.Ask_63} tone="offer" hint="75th Dist" />
            <QuoteCard label="ATM IV" value={current.atm_iv} tone="iv" hint="surface now" />
            <QuoteCard label="ATM − Mid" value={quotes.atm_vs_mid} tone="iv" hint=">0 rich" />
            <QuoteCard label="Mid−Bid" value={quotes['Mid-Bid_3M']} hint="half-spread" />
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px]">
            {[
              ['RV 1M', current.rv_21],
              ['RV 2M', current.rv_42],
              ['RV 3M', current.rv_63],
              ['RV 1Y', current.rv_252],
            ].map(([lab, val]) => (
              <div key={lab as string} className="bg-slate-50 border border-slate-200 rounded-md px-2.5 py-1.5">
                <span className="text-slate-500 font-bold">{lab}</span>
                <span className="float-right font-mono text-[#000066]">{fmt(val as number)}</span>
              </div>
            ))}
          </div>

          <div className="bg-white border border-slate-200 rounded-lg p-3">
            <div className="flex items-center justify-between mb-2">
              <div className="text-xs font-bold text-[#000066]">
                {mode === 'rv' ? 'RV term + Mid/Bid/Offer band' : 'RV + mapped Mid/Bid/Offer'}
              </div>
              <div className="text-[10px] text-slate-400 font-mono">
                {data.source} · asof {data.asof || '—'} · n={data.sample_length}
              </div>
            </div>
            <div className="h-[320px]">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={chartRows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                  <XAxis dataKey="d" tick={{ fontSize: 10 }} minTickGap={24} />
                  <YAxis tick={{ fontSize: 10 }} domain={['auto', 'auto']} width={40} />
                  <Tooltip
                    contentStyle={{ fontSize: 11 }}
                    formatter={(v: number) => (v == null ? '—' : Number(v).toFixed(2))}
                  />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  {chartRows.some((r) => r.bid != null && r.offer != null) && (
                    <Area
                      type="monotone"
                      dataKey="offer"
                      stroke="none"
                      fill="#fecaca"
                      fillOpacity={0.35}
                      name="Offer"
                      connectNulls
                    />
                  )}
                  {chartRows.some((r) => r.bid != null) && (
                    <Area
                      type="monotone"
                      dataKey="bid"
                      stroke="none"
                      fill="#ffffff"
                      fillOpacity={1}
                      name="Bid floor"
                      legendType="none"
                      connectNulls
                    />
                  )}
                  <Line type="monotone" dataKey="rv21" name="RV 1M" stroke="#94a3b8" dot={false} strokeWidth={1} connectNulls />
                  <Line type="monotone" dataKey="rv63" name="RV 3M" stroke="#64748b" dot={false} strokeWidth={1.2} connectNulls />
                  <Line type="monotone" dataKey="rv252" name="RV 1Y" stroke="#0ea5e9" dot={false} strokeWidth={1.5} connectNulls />
                  <Line type="monotone" dataKey="mid" name="Mid" stroke="#000066" dot={false} strokeWidth={2} connectNulls />
                  <Line type="monotone" dataKey="bid" name="Bid" stroke="#059669" dot={false} strokeWidth={1.2} strokeDasharray="4 3" connectNulls />
                  <Line type="monotone" dataKey="offer" name="Offer" stroke="#e11d48" dot={false} strokeWidth={1.2} strokeDasharray="4 3" connectNulls />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="text-[10px] text-slate-400 leading-relaxed">
            {data.method}
            {data.benchmark ? ` · bench=${data.benchmark.product} (bid-to-mid ${data.benchmark.bid_to_mid})` : null}
            {' · '}range {data.first_date} → {data.end_date}
          </div>
        </>
      )}
    </div>
  );
};

export default RvIvPredictView;
