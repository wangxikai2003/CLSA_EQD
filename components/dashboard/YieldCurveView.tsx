import React, { useEffect, useMemo, useState } from 'react';
import { fetchYieldCurve } from '../../services/api_v1';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  BarChart, Bar, Cell,
} from 'recharts';

const COLORS: Record<string, string> = {
  Current: '#000066',
  '1W': '#EAB308',
  '1M': '#E20000',
  '1Y': '#16a34a',
};

type MarketId = 'CN' | 'US';

const MARKET_META: Record<MarketId, { label: string; hint: string }> = {
  CN: { label: '中国 CN', hint: 'SHIBOR + 国债 10Y' },
  US: { label: '美国 US', hint: 'SOFR term structure' },
};

interface Props {
  asof?: string;
}

const YieldCurveView: React.FC<Props> = ({ asof }) => {
  const [market, setMarket] = useState<MarketId>('CN');
  const [histOn, setHistOn] = useState({ '1W': true, '1M': true, '1Y': true });
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const historyParam = useMemo(() => {
    const parts = Object.entries(histOn).filter(([, on]) => on).map(([k]) => k);
    return parts.join(',') || '1W';
  }, [histOn]);

  useEffect(() => {
    const ac = new AbortController();
    setLoading(true);
    setErr(null);
    fetchYieldCurve(
      { market, history: historyParam, asof: asof || undefined },
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
  }, [market, asof, historyParam]);

  /** Term structure: one row per tenor, columns = Current / 1W / 1M / 1Y */
  const curveRows = useMemo(() => {
    const cur = data?.curves?.Current as any[] | undefined;
    if (!cur?.length) return [];
    return cur.map((p) => {
      const row: Record<string, string | number> = {
        tenor: p.tenor_years,
        label: p.label || p.code,
        code: p.code,
      };
      for (const [name, pts] of Object.entries(data.curves || {}) as [string, any[]][]) {
        const hit =
          pts.find((q) => q.code === p.code) ||
          pts.find((q) => Math.abs(q.tenor_years - p.tenor_years) < 1e-9);
        if (hit) row[name] = hit.rate_pct;
      }
      return row;
    });
  }, [data]);

  const spreadRows = useMemo(() => {
    if (!data?.spreads) return [];
    return Object.entries(data.spreads).map(([k, v]) => ({
      name: k,
      bp: Number(v),
    }));
  }, [data]);

  const seriesNames = useMemo(() => {
    if (!data?.curves) return [] as string[];
    const order = ['Current', '1W', '1M', '1Y'];
    return order.filter((k) => Array.isArray(data.curves[k]) && data.curves[k].length);
  }, [data]);

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-lg border border-slate-200 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
          <div>
            <h3 className="font-bold text-[#000066] text-sm">Yield Curve Hub</h3>
            <p className="text-[10px] text-slate-400">
              {MARKET_META[market].hint} · asof {data?.asof ?? asof ?? '—'}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="inline-flex rounded border border-slate-200 overflow-hidden text-[10px] font-bold">
              {(['CN', 'US'] as MarketId[]).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMarket(m)}
                  className={`px-2.5 py-1 transition ${
                    market === m ? 'bg-[#000066] text-white' : 'bg-white text-slate-500 hover:bg-slate-50'
                  }`}
                >
                  {MARKET_META[m].label}
                </button>
              ))}
            </div>
            <div className="flex gap-1">
              {(['1W', '1M', '1Y'] as const).map((h) => (
                <button
                  key={h}
                  type="button"
                  onClick={() => setHistOn((prev) => ({ ...prev, [h]: !prev[h] }))}
                  className={`px-2 py-0.5 text-[10px] font-bold rounded border ${
                    histOn[h]
                      ? 'bg-slate-800 text-white border-slate-800'
                      : 'bg-white text-slate-400 border-slate-200'
                  }`}
                >
                  {h}
                </button>
              ))}
            </div>
          </div>
        </div>

        {err && <div className="text-xs text-red-600 mb-2 break-all">{err}</div>}
        {loading && !data && (
          <div className="h-[320px] flex items-center justify-center text-slate-400 text-sm">加载利率曲线…</div>
        )}

        {!loading && !err && curveRows.length === 0 && (
          <div className="h-[320px] flex items-center justify-center text-slate-400 text-sm">
            无可用利率点（检查 rates_eod / asof）
          </div>
        )}

        {curveRows.length > 0 && (
          <div className="h-[340px]">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={curveRows} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis
                  dataKey="tenor"
                  type="number"
                  domain={['dataMin', 'dataMax']}
                  tick={{ fontSize: 10 }}
                  tickFormatter={(v) => {
                    const n = Number(v);
                    if (n < 0.05) return 'ON';
                    if (n < 1) return `${Math.round(n * 12)}M`;
                    return `${n}Y`;
                  }}
                  label={{ value: 'Tenor', fontSize: 10, position: 'insideBottom', offset: -2 }}
                />
                <YAxis
                  tick={{ fontSize: 10 }}
                  unit="%"
                  domain={['auto', 'auto']}
                  width={42}
                  tickFormatter={(v) => Number(v).toFixed(2)}
                />
                <Tooltip
                  content={({ active, payload }) => {
                    if (!active || !payload?.length) return null;
                    const row = payload[0].payload;
                    return (
                      <div className="bg-white border border-slate-200 rounded px-2 py-1 text-[10px] font-mono shadow">
                        <div className="font-bold mb-0.5">{row.label} · {row.code}</div>
                        {payload.map((p) => (
                          <div key={String(p.dataKey)} style={{ color: p.color as string }}>
                            {p.name}: {Number(p.value).toFixed(3)}%
                          </div>
                        ))}
                      </div>
                    );
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 10 }} />
                {seriesNames.map((name) => (
                  <Line
                    key={name}
                    type="monotone"
                    dataKey={name}
                    name={name}
                    stroke={COLORS[name] || '#64748b'}
                    strokeWidth={name === 'Current' ? 2.5 : 1.5}
                    strokeDasharray={name === 'Current' ? undefined : '4 3'}
                    dot={name === 'Current' ? { r: 3 } : false}
                    connectNulls
                    isAnimationActive={false}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}

        {curveRows.length > 0 && (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-[10px] font-mono">
              <thead>
                <tr className="text-slate-400 text-left border-b border-slate-100">
                  <th className="py-1 pr-2">Tenor</th>
                  <th className="py-1 pr-2">Code</th>
                  {seriesNames.map((n) => (
                    <th key={n} className="py-1 pr-2 text-right">{n}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {curveRows.map((r) => (
                  <tr key={String(r.code)} className="border-b border-slate-50">
                    <td className="py-1 pr-2 font-bold text-[#000066]">{r.label}</td>
                    <td className="py-1 pr-2 text-slate-400">{r.code}</td>
                    {seriesNames.map((n) => (
                      <td key={n} className="py-1 pr-2 text-right">
                        {r[n] != null ? Number(r[n]).toFixed(3) : '—'}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {spreadRows.length > 0 && (
        <div className="bg-white rounded-lg border border-slate-200 p-4">
          <h3 className="font-bold text-[#000066] text-sm mb-2">Curve Spreads (bp)</h3>
          <div className="h-[200px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={spreadRows} margin={{ top: 8, right: 12, left: 0, bottom: 4 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="name" tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 10 }} unit="bp" width={48} />
                <Tooltip
                  formatter={(v: number) => [`${Number(v).toFixed(1)} bp`, 'spread']}
                  contentStyle={{ fontSize: 11 }}
                />
                <Bar dataKey="bp" name="bp" radius={[3, 3, 0, 0]}>
                  {spreadRows.map((r) => (
                    <Cell key={r.name} fill={r.bp >= 0 ? '#000066' : '#E20000'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-2 grid grid-cols-2 sm:grid-cols-3 gap-2 text-[10px]">
            {spreadRows.map((r) => (
              <div key={r.name} className="bg-slate-50 rounded px-2 py-1 flex justify-between">
                <span className="text-slate-500">{r.name}</span>
                <span className={`font-mono font-bold ${r.bp >= 0 ? 'text-[#000066]' : 'text-[#E20000]'}`}>
                  {r.bp.toFixed(1)} bp
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

export default YieldCurveView;
