import React, { useEffect, useMemo, useState } from 'react';
import Plot from 'react-plotly.js';
import {
  Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  Area, ComposedChart, Scatter,
} from 'recharts';
import { fetchSurface, ProductCode } from '../../services/api';
import { calibrateJwSvi, fetchVolCone } from '../../services/api_v1';

interface Props {
  product?: ProductCode;
  tradeDate?: string;
}

const TENOR_PRESETS = [
  { id: '1M', days: 30, label: '1M (30D)' },
  { id: '3M', days: 90, label: '3M (90D)' },
  { id: '6M', days: 180, label: '6M (180D)' },
  { id: '1Y', days: 365, label: '1Y (365D)' },
  { id: '2Y', days: 730, label: '2Y (730D)' },
];

/** Cross-section of the vol surface along maturity: ATM / OTM put-wing / ITM (high strike). */
type TermMoneyness = 'ATM' | 'OTM' | 'ITM';

const TERM_SLICES: {
  id: TermMoneyness;
  label: string;
  hint: string;
  /** representative K/F for JW-SVI fit line */
  mny: number;
  /** market-point filter on K/F */
  inBucket: (m: number) => boolean;
}[] = [
  { id: 'ATM', label: 'ATM', hint: 'K/F ≈ 100%', mny: 1.0, inBucket: (m) => m >= 0.98 && m <= 1.02 },
  { id: 'OTM', label: 'OTM', hint: 'K/F ≤ 95% (put wing)', mny: 0.90, inBucket: (m) => m <= 0.95 },
  { id: 'ITM', label: 'ITM', hint: 'K/F ≥ 105% (call wing)', mny: 1.10, inBucket: (m) => m >= 1.05 },
];

function sviIvPct(raw: { a: number; b: number; rho: number; m: number; sigma: number }, k: number, ttm: number) {
  const x = k - raw.m;
  const w = raw.a + raw.b * (raw.rho * x + Math.sqrt(x * x + raw.sigma * raw.sigma));
  return Math.sqrt(Math.max(w, 1e-12) / Math.max(ttm, 1e-8)) * 100;
}

const VolAnalyticsView: React.FC<Props> = ({ product = 'IO', tradeDate }) => {
  const [surface, setSurface] = useState<any>(null);
  const [cone, setCone] = useState<any>(null);
  const [fit, setFit] = useState<any>(null);
  const [tenorIdx, setTenorIdx] = useState(0);
  const [termSide, setTermSide] = useState<TermMoneyness>('ATM');
  const [coneAxis, setConeAxis] = useState<'days' | 'tenor'>('tenor');
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setErr(null);
    fetchSurface(product, tradeDate, 'otm').then(setSurface).catch((e) => setErr(String(e)));
    fetchVolCone(product).then(setCone).catch(() => {});
  }, [product, tradeDate]);

  const slices = surface?.slices ?? [];

  const pickPreset = (days: number) => {
    if (!slices.length) return;
    let best = 0;
    let bestDist = Infinity;
    slices.forEach((s: any, i: number) => {
      const d = Math.abs((s.ttm || 0) * 365 - days);
      if (d < bestDist) {
        bestDist = d;
        best = i;
      }
    });
    setTenorIdx(best);
  };

  const slice = slices[Math.min(tenorIdx, Math.max(slices.length - 1, 0))];

  useEffect(() => {
    if (!surface || !slice) return;
    const mkt = (surface.market_points || []).filter((p: any) => p.expiry === slice.expiry);
    if (mkt.length < 5) {
      setFit(null);
      return;
    }
    const strikes = mkt.map((p: any) => p.strike);
    const ivs = mkt.map((p: any) => p.iv);
    calibrateJwSvi({ ttm: slice.ttm, strikes, ivs, forward: slice.forward, method: 'SLSQP' })
      .then(setFit)
      .catch(() => setFit(null));
  }, [surface, slice?.expiry, slice?.ttm, slice?.forward]);

  const skewChart = useMemo(() => {
    if (!slice || !surface) return { scatter: [] as any[], line: [] as any[] };
    const mkt = (surface.market_points || []).filter((p: any) => p.expiry === slice.expiry);
    const scatter = mkt.map((p: any) => ({
      k: Math.log(p.strike / slice.forward),
      strike: p.strike,
      iv: p.iv * 100,
      bidVol: (p.iv * 0.995) * 100,
      askVol: (p.iv * 1.005) * 100,
      midVol: p.iv * 100,
      oi: p.oi ?? null,
      volume: p.volume ?? null,
      cp: p.cp,
    }));
    const line: any[] = [];
    if (fit?.raw) {
      const { a, b, rho, m, sigma } = fit.raw;
      for (let i = -50; i <= 50; i++) {
        const k = i / 80;
        const x = k - m;
        const w = a + b * (rho * x + Math.sqrt(x * x + sigma * sigma));
        line.push({ k, iv: Math.sqrt(Math.max(w, 1e-12) / slice.ttm) * 100 });
      }
    }
    return { scatter, line };
  }, [slice, surface, fit]);

  const fitStats = useMemo(() => {
    if (!fit || !skewChart.scatter.length || !fit.raw || !slice) return null;
    const { a, b, rho, m, sigma } = fit.raw;
    const residuals = skewChart.scatter.map((p) => {
      const x = p.k - m;
      const w = a + b * (rho * x + Math.sqrt(x * x + sigma * sigma));
      const model = Math.sqrt(Math.max(w, 1e-12) / slice.ttm) * 100;
      return Math.abs(model - p.iv);
    });
    const maxRes = Math.max(...residuals, 0);
    const arb = fit.arbitrage_check || fit.arbitrage || {};
    const butterflyFail =
      arb.has_butterfly_arbitrage === true ||
      arb.butterfly_ok === false;
    return {
      rmsePct: (fit.rmse || 0) * 100,
      maePct: (fit.mae || 0) * 100,
      maxResPct: (fit.max_residual != null ? fit.max_residual : maxRes / 100) * 100,
      butterflyPass: !butterflyFail,
    };
  }, [fit, skewChart, slice]);

  const termSpec = TERM_SLICES.find((t) => t.id === termSide) || TERM_SLICES[0];

  /** IV term structure as a moneyness cross-section of the surface (scatter + JW-SVI). */
  const termChart = useMemo(() => {
    if (!slices.length) return { scatter: [] as any[], line: [] as any[], badge: null as null | { n: number; mnyLabel: string } };
    const kFit = Math.log(termSpec.mny);
    const scatter: any[] = [];
    for (const s of slices) {
      const F = s.forward || surface?.spot || 1;
      const pts = (surface?.market_points || []).filter((p: any) => p.expiry === s.expiry);
      for (const p of pts) {
        const m = p.strike / F;
        if (!termSpec.inBucket(m)) continue;
        scatter.push({
          ttm: s.ttm,
          days: Math.round(s.ttm * 365),
          expiry: s.expiry,
          strike: p.strike,
          mny: m,
          iv: p.iv * 100,
          bidVol: p.iv * 0.995 * 100,
          askVol: p.iv * 1.005 * 100,
          midVol: p.iv * 100,
          cp: p.cp,
          oi: p.oi ?? null,
          volume: p.volume ?? null,
        });
      }
    }
    const line = slices.map((s: any) => {
      let ivFit: number | null = null;
      if (s.raw) {
        ivFit = sviIvPct(s.raw, kFit, s.ttm);
      } else if (termSide === 'ATM') {
        ivFit = Math.sqrt(Math.max(s.jw?.v ?? s.w_atm / s.ttm, 0)) * 100;
      }
      return {
        ttm: s.ttm,
        days: Math.round(s.ttm * 365),
        expiry: s.expiry,
        iv: ivFit,
        mny: termSpec.mny,
      };
    }).filter((d) => d.iv != null);
    return {
      scatter,
      line,
      badge: { n: scatter.length, mnyLabel: `${(termSpec.mny * 100).toFixed(0)}%` },
    };
  }, [slices, surface, termSpec, termSide]);

  const coneData = useMemo(() => {
    return (cone?.cone || []).map((c: any) => ({
      ...c,
      x: coneAxis === 'tenor' ? c.tenor || c.label || `${c.window}D` : c.days ?? c.window,
      // stacked band bases for Area stacking
      minBase: c.min,
      coreBase: c.p25,
      highBase: c.p75,
      bandLow: Math.max(0, (c.p25 ?? 0) - (c.min ?? 0)),
      bandCore: Math.max(0, (c.p75 ?? 0) - (c.p25 ?? 0)),
      bandHigh: Math.max(0, (c.max ?? 0) - (c.p75 ?? 0)),
      iv: c.current_iv,
      recent: c.recent_rv,
    }));
  }, [cone, coneAxis]);

  const vegaSignals = useMemo(() => {
    return coneData.filter((c: any) => c.signal).map((c: any) => ({
      x: c.x,
      signal: c.signal,
      iv: c.iv,
    }));
  }, [coneData]);

  const surface3d = useMemo(() => {
    if (!surface?.matrix?.iv) return null;
    const mat = surface.matrix;
    return {
      x: mat.strikes,
      y: mat.ttms,
      z: mat.iv.map((row: (number | null)[]) => row.map((v) => (v == null ? null : v * 100))),
    };
  }, [surface]);

  return (
    <div className="space-y-4">
      {err && <div className="text-xs text-red-600">{err}</div>}

      <div className="bg-white rounded-lg border border-slate-200 p-4 relative">
        <div className="flex flex-wrap justify-between items-center gap-2 mb-2">
          <h3 className="font-bold text-[#000066] text-sm">JW-SVI Skew (Scatter + Fit)</h3>
          <div className="flex flex-wrap gap-1">
            {TENOR_PRESETS.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => pickPreset(p.days)}
                className={`px-2 py-0.5 text-[10px] font-bold rounded border ${
                  slice && Math.abs((slice.ttm || 0) * 365 - p.days) < 40
                    ? 'bg-[#000066] text-white border-[#000066]'
                    : 'bg-white text-slate-500 border-slate-200'
                }`}
              >
                {p.label}
              </button>
            ))}
            <select
              className="text-[10px] border rounded px-2 py-1 font-mono"
              value={tenorIdx}
              onChange={(e) => setTenorIdx(Number(e.target.value))}
            >
              {slices.map((s: any, i: number) => (
                <option key={s.expiry} value={i}>{s.expiry} (T={s.ttm.toFixed(3)})</option>
              ))}
            </select>
          </div>
        </div>

        {fitStats && (
          <div className="absolute top-12 right-6 z-10 bg-white/95 border border-slate-200 rounded-md px-2 py-1 shadow-sm text-[10px] font-mono space-y-0.5">
            <div>RMSE: {fitStats.rmsePct.toFixed(2)}%</div>
            <div>MAE: {fitStats.maePct.toFixed(2)}%</div>
            <div>Max Res: {fitStats.maxResPct.toFixed(2)}%</div>
            <div className="flex items-center gap-1">
              Butterfly:
              <span className={`inline-block w-2 h-2 rounded-full ${fitStats.butterflyPass ? 'bg-emerald-500' : 'bg-red-500'}`} />
              {fitStats.butterflyPass ? 'Pass' : 'Fail'}
            </div>
          </div>
        )}

        <div className="h-[280px]">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="k" type="number" domain={['auto', 'auto']} tick={{ fontSize: 10 }} label={{ value: 'log-moneyness', fontSize: 10, position: 'insideBottom', offset: -2 }} />
              <YAxis tick={{ fontSize: 10 }} unit="%" />
              <Tooltip
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null;
                  const d = payload[0].payload;
                  if (d.strike == null) return <div className="bg-white border px-2 py-1 text-[10px]">k={d.k?.toFixed?.(3)} IV={d.iv?.toFixed?.(2)}%</div>;
                  return (
                    <div className="bg-white border border-slate-200 rounded px-2 py-1 text-[10px] font-mono shadow">
                      <div>Strike: {d.strike}</div>
                      <div>Bid Vol: {d.bidVol?.toFixed?.(2)}%</div>
                      <div>Ask Vol: {d.askVol?.toFixed?.(2)}%</div>
                      <div>Mid Vol: {d.midVol?.toFixed?.(2)}%</div>
                      <div>OI: {d.oi ?? '—'}</div>
                      <div>Volume: {d.volume ?? '—'}</div>
                    </div>
                  );
                }}
              />
              <Legend wrapperStyle={{ fontSize: 10 }} />
              <Scatter data={skewChart.scatter.filter((d) => d.cp === 'C')} dataKey="iv" name="Call Mid" fill="#2563eb" />
              <Scatter data={skewChart.scatter.filter((d) => d.cp === 'P')} dataKey="iv" name="Put Mid" fill="#dc2626" />
              <Line data={skewChart.line} dataKey="iv" name="JW-SVI fit" stroke="#EAB308" dot={false} strokeWidth={2} type="monotone" />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* IV Term Structure — moneyness cross-section (ATM / OTM / ITM), full width */}
      <div className="bg-white rounded-lg border border-slate-200 p-4 relative">
        <div className="flex flex-wrap justify-between items-center gap-2 mb-2">
          <div>
            <h3 className="font-bold text-[#000066] text-sm">IV Term Structure</h3>
            <p className="text-[10px] text-slate-400">Vol surface cross-section vs maturity · {termSpec.hint}</p>
          </div>
          <div className="flex flex-wrap gap-1">
            {TERM_SLICES.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => setTermSide(t.id)}
                className={`px-2.5 py-0.5 text-[10px] font-bold rounded border ${
                  termSide === t.id
                    ? 'bg-[#000066] text-white border-[#000066]'
                    : 'bg-white text-slate-500 border-slate-200'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>

        {termChart.badge && (
          <div className="absolute top-12 right-6 z-10 bg-white/95 border border-slate-200 rounded-md px-2 py-1 shadow-sm text-[10px] font-mono space-y-0.5">
            <div>Slice: {termSide}</div>
            <div>Fit @ K/F={termChart.badge.mnyLabel}</div>
            <div>Mkt pts: {termChart.badge.n}</div>
          </div>
        )}

        <div className="h-[280px]">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis
                dataKey="ttm"
                type="number"
                domain={['auto', 'auto']}
                tick={{ fontSize: 10 }}
                label={{ value: 'TTM (years)', fontSize: 10, position: 'insideBottom', offset: -2 }}
              />
              <YAxis tick={{ fontSize: 10 }} unit="%" domain={['auto', 'auto']} />
              <Tooltip
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null;
                  const d = payload[0].payload;
                  if (d.strike == null) {
                    return (
                      <div className="bg-white border px-2 py-1 text-[10px] font-mono shadow">
                        <div>TTM: {d.ttm?.toFixed?.(3)} ({d.days}D)</div>
                        <div>JW-SVI @ {(d.mny * 100).toFixed(0)}%: {d.iv?.toFixed?.(2)}%</div>
                        <div>Expiry: {d.expiry}</div>
                      </div>
                    );
                  }
                  return (
                    <div className="bg-white border border-slate-200 rounded px-2 py-1 text-[10px] font-mono shadow">
                      <div>Expiry: {d.expiry}</div>
                      <div>Strike: {d.strike} · K/F={(d.mny * 100).toFixed(1)}%</div>
                      <div>Bid Vol: {d.bidVol?.toFixed?.(2)}%</div>
                      <div>Ask Vol: {d.askVol?.toFixed?.(2)}%</div>
                      <div>Mid Vol: {d.midVol?.toFixed?.(2)}%</div>
                      <div>OI: {d.oi ?? '—'} · Vol: {d.volume ?? '—'}</div>
                    </div>
                  );
                }}
              />
              <Legend wrapperStyle={{ fontSize: 10 }} />
              <Scatter
                data={termChart.scatter.filter((d) => d.cp === 'C')}
                dataKey="iv"
                name="Call Mid"
                fill="#2563eb"
              />
              <Scatter
                data={termChart.scatter.filter((d) => d.cp === 'P')}
                dataKey="iv"
                name="Put Mid"
                fill="#dc2626"
              />
              <Line
                data={termChart.line}
                dataKey="iv"
                name={`JW-SVI ${termSide} @ ${(termSpec.mny * 100).toFixed(0)}%`}
                stroke="#EAB308"
                dot={{ r: 3 }}
                strokeWidth={2}
                type="monotone"
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Volatility Cone — below term structure */}
      <div className="bg-white rounded-lg border border-slate-200 p-4">
        <div className="flex justify-between items-center mb-2">
          <h3 className="font-bold text-[#000066] text-sm">Volatility Cone</h3>
          <div className="flex gap-1 text-[10px]">
            <button type="button" className={`px-2 py-0.5 rounded border ${coneAxis === 'days' ? 'bg-[#000066] text-white' : ''}`} onClick={() => setConeAxis('days')}>Days</button>
            <button type="button" className={`px-2 py-0.5 rounded border ${coneAxis === 'tenor' ? 'bg-[#000066] text-white' : ''}`} onClick={() => setConeAxis('tenor')}>Tenor</button>
          </div>
        </div>
        <div className="h-[260px]">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={coneData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="x" tick={{ fontSize: 10 }} />
              <YAxis tick={{ fontSize: 10 }} tickFormatter={(v) => `${(v * 100).toFixed(0)}%`} />
              <Tooltip formatter={(v: number) => `${(Number(v) * 100).toFixed(2)}%`} />
              <Legend wrapperStyle={{ fontSize: 9 }} />
              <Area type="monotone" dataKey="min" stackId="band" stroke="none" fill="transparent" legendType="none" />
              <Area type="monotone" dataKey="bandLow" stackId="band" stroke="none" fill="#86efac" fillOpacity={0.45} name="Min–P25 (cheap)" />
              <Area type="monotone" dataKey="bandCore" stackId="band" stroke="none" fill="#94a3b8" fillOpacity={0.35} name="P25–P75" />
              <Area type="monotone" dataKey="bandHigh" stackId="band" stroke="none" fill="#fca5a5" fillOpacity={0.45} name="P75–Max (rich)" />
              <Line type="monotone" dataKey="median" stroke="#334155" strokeWidth={2.5} name="Median" dot={false} />
              <Line type="monotone" dataKey="iv" stroke="#EAB308" strokeWidth={2.5} name="Current IV" dot={{ r: 3 }} />
              <Line type="monotone" dataKey="recent" stroke="#2563eb" strokeWidth={1.5} strokeDasharray="4 3" name="Recent RV(20)" dot={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
        {vegaSignals.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-2 text-[10px]">
            {vegaSignals.map((s: any) => (
              <span
                key={`${s.x}-${s.signal}`}
                className={`px-2 py-0.5 rounded font-bold ${
                  s.signal === 'Sell Vega' ? 'bg-red-100 text-red-700' : 'bg-emerald-100 text-emerald-700'
                }`}
              >
                {s.x}: {s.signal}
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="bg-white rounded-lg border border-slate-200 p-4">
        <h3 className="font-bold text-[#000066] text-sm mb-2">3D Volatility Surface</h3>
        <div className="h-[320px]">
          {surface3d ? (
            <Plot
              data={[{
                type: 'surface',
                x: surface3d.x,
                y: surface3d.y,
                z: surface3d.z,
                colorscale: [[0, '#FEFCE8'], [0.5, '#EAB308'], [1, '#713F12']],
                showscale: true,
              }]}
              layout={{
                autosize: true,
                margin: { l: 0, r: 0, b: 0, t: 10 },
                scene: {
                  xaxis: { title: 'Strike' },
                  yaxis: { title: 'TTM' },
                  zaxis: { title: 'IV %' },
                },
                paper_bgcolor: 'rgba(0,0,0,0)',
              }}
              style={{ width: '100%', height: '100%' }}
              useResizeHandler
              config={{ displayModeBar: false }}
            />
          ) : (
            <div className="h-full flex items-center justify-center text-slate-400 text-sm">No matrix</div>
          )}
        </div>
      </div>
    </div>
  );
};

export default VolAnalyticsView;
