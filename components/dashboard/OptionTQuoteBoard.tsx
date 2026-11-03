import React, { useEffect, useState } from 'react';
import { fetchTQuote } from '../../services/api_v1';

export interface StrikePickPayload {
  strike: number;
  spot: number;
  strikePct: number;
  expiry?: string;
  iv?: number | null;
}

interface Props {
  product?: string;
  tradeDate?: string;
  onStrikePick?: (payload: StrikePickPayload) => void;
}

const fmt = (v: number | null | undefined, d = 2) =>
  v == null || Number.isNaN(v) ? '—' : Number(v).toFixed(d);

const OptionTQuoteBoard: React.FC<Props> = ({ product = 'IO', tradeDate, onStrikePick }) => {
  const [data, setData] = useState<any>(null);
  const [expiry, setExpiry] = useState<string | undefined>();
  const [err, setErr] = useState<string | null>(null);
  const [showGreeks, setShowGreeks] = useState(true);

  useEffect(() => {
    setErr(null);
    fetchTQuote(product, tradeDate, expiry)
      .then((d) => {
        setData(d);
        if (!expiry && d.expiry) setExpiry(d.expiry);
      })
      .catch((e) => setErr(String(e)));
  }, [product, tradeDate, expiry]);

  const handleStrikeClick = (strike: number, row: any) => {
    const spot = data?.spot || strike;
    onStrikePick?.({
      strike,
      spot,
      strikePct: strike / spot,
      expiry: data?.expiry,
      iv: row.call?.iv ?? row.put?.iv,
    });
  };

  return (
    <div className="bg-white rounded-lg border border-slate-200 p-4 flex flex-col gap-3 h-full">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-bold text-[#000066] text-sm">Option T-Quote Board</h3>
        <div className="flex items-center gap-2 text-[10px]">
          <select
            className="border rounded px-2 py-1 font-mono"
            value={expiry || ''}
            onChange={(e) => setExpiry(e.target.value)}
          >
            {(data?.expiries || []).map((x: string) => (
              <option key={x} value={x}>{x}</option>
            ))}
          </select>
          <label className="flex items-center gap-1 text-slate-500">
            <input type="checkbox" checked={showGreeks} onChange={(e) => setShowGreeks(e.target.checked)} />
            Greeks
          </label>
          <span className="text-slate-400 font-mono">spot {data?.spot?.toFixed?.(2) ?? '—'}</span>
        </div>
      </div>
      {err && <div className="text-xs text-red-600">{err}</div>}
      <div className="text-[9px] text-slate-400">
        Click Strike to sync barrier into Exotic Pricer · source={data?.source ?? 'settle'}
      </div>
      <div className="overflow-auto flex-1 border border-slate-100 rounded">
        <table className="w-full text-[10px] font-mono">
          <thead className="bg-slate-50 sticky top-0">
            <tr>
              <th className="px-1.5 py-1 text-right text-blue-700">Bid</th>
              <th className="px-1.5 py-1 text-right text-blue-700">Ask</th>
              <th className="px-1.5 py-1 text-right text-blue-700">Mid</th>
              <th className="px-1.5 py-1 text-right text-blue-700">Sprd</th>
              <th className="px-1.5 py-1 text-right text-blue-700">IV</th>
              {showGreeks && <th className="px-1.5 py-1 text-right text-blue-700">Δ</th>}
              {showGreeks && <th className="px-1.5 py-1 text-right text-blue-700">Γ</th>}
              <th className="px-1.5 py-1 text-right text-blue-700">Vol</th>
              <th className="px-1.5 py-1 text-right text-blue-700">OI</th>
              <th className="px-2 py-1 text-center bg-[#000066] text-white">Strike</th>
              <th className="px-1.5 py-1 text-left text-red-700">OI</th>
              <th className="px-1.5 py-1 text-left text-red-700">Vol</th>
              {showGreeks && <th className="px-1.5 py-1 text-left text-red-700">Γ</th>}
              {showGreeks && <th className="px-1.5 py-1 text-left text-red-700">Δ</th>}
              <th className="px-1.5 py-1 text-left text-red-700">IV</th>
              <th className="px-1.5 py-1 text-left text-red-700">Sprd</th>
              <th className="px-1.5 py-1 text-left text-red-700">Mid</th>
              <th className="px-1.5 py-1 text-left text-red-700">Ask</th>
              <th className="px-1.5 py-1 text-left text-red-700">Bid</th>
            </tr>
          </thead>
          <tbody>
            {(data?.board || []).map((row: any) => {
              const atm = row.strike === data?.atm_strike;
              const c = row.call || {};
              const p = row.put || {};
              return (
                <tr
                  key={row.strike}
                  className={atm ? 'bg-amber-100/80 font-bold shadow-[inset_0_0_12px_rgba(234,179,8,0.35)]' : 'hover:bg-slate-50'}
                >
                  <td className="px-1.5 py-0.5 text-right text-blue-800">{fmt(c.bid)}</td>
                  <td className="px-1.5 py-0.5 text-right text-blue-800">{fmt(c.ask)}</td>
                  <td className="px-1.5 py-0.5 text-right text-blue-800">{fmt(c.mid)}</td>
                  <td className="px-1.5 py-0.5 text-right text-blue-700">{fmt(c.spread)}</td>
                  <td className="px-1.5 py-0.5 text-right">{c.iv != null ? (c.iv * 100).toFixed(1) : '—'}</td>
                  {showGreeks && <td className="px-1.5 py-0.5 text-right">{fmt(c.delta, 3)}</td>}
                  {showGreeks && <td className="px-1.5 py-0.5 text-right">{c.gamma != null ? c.gamma.toExponential(1) : '—'}</td>}
                  <td className="px-1.5 py-0.5 text-right text-slate-400">{fmt(c.volume, 0)}</td>
                  <td className="px-1.5 py-0.5 text-right text-slate-400">{fmt(c.oi, 0)}</td>
                  <td
                    className={`px-2 py-0.5 text-center cursor-pointer select-none ${
                      atm ? 'bg-[#EAB308] text-white' : 'bg-slate-100 hover:bg-[#000066] hover:text-white'
                    }`}
                    title="Click to fill Exotic barrier"
                    onClick={() => handleStrikeClick(row.strike, row)}
                  >
                    {row.strike}
                  </td>
                  <td className="px-1.5 py-0.5 text-left text-slate-400">{fmt(p.oi, 0)}</td>
                  <td className="px-1.5 py-0.5 text-left text-slate-400">{fmt(p.volume, 0)}</td>
                  {showGreeks && <td className="px-1.5 py-0.5 text-left">{p.gamma != null ? p.gamma.toExponential(1) : '—'}</td>}
                  {showGreeks && <td className="px-1.5 py-0.5 text-left">{fmt(p.delta, 3)}</td>}
                  <td className="px-1.5 py-0.5 text-left">{p.iv != null ? (p.iv * 100).toFixed(1) : '—'}</td>
                  <td className="px-1.5 py-0.5 text-left text-red-700">{fmt(p.spread)}</td>
                  <td className="px-1.5 py-0.5 text-left text-red-800">{fmt(p.mid)}</td>
                  <td className="px-1.5 py-0.5 text-left text-red-800">{fmt(p.ask)}</td>
                  <td className="px-1.5 py-0.5 text-left text-red-800">{fmt(p.bid)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default OptionTQuoteBoard;
