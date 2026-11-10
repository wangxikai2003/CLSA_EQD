import React, { useMemo, useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

type Tab = 'greeks' | 'cross' | 'prob' | 'diag';

interface Props {
  risk: Record<string, any> | null;
  solveDiag: Record<string, any> | null;
  childrenGreeks?: React.ReactNode;
}

const RiskDashboard: React.FC<Props> = ({ risk, solveDiag, childrenGreeks }) => {
  const [tab, setTab] = useState<Tab>('greeks');

  const koBars = useMemo(() => {
    const m = risk?.event_probabilities?.marginal_ko || [];
    return m.map((r: any) => ({
      obs: `O${r.obs}`,
      marginal: Number(r.marginal_ko) * 100,
      cum: undefined as number | undefined,
    }));
  }, [risk]);

  const cumBars = useMemo(() => {
    const m = risk?.event_probabilities?.cumulative_ko || [];
    return m.map((r: any) => ({ obs: `O${r.obs}`, cum: Number(r.cumulative_ko) * 100 }));
  }, [risk]);

  const mergedBars = useMemo(() => {
    const map = new Map<string, any>();
    koBars.forEach((r) => map.set(r.obs, { ...r }));
    cumBars.forEach((r) => {
      const prev = map.get(r.obs) || { obs: r.obs };
      map.set(r.obs, { ...prev, cum: r.cum });
    });
    return Array.from(map.values());
  }, [koBars, cumBars]);

  const cross = (risk?.cross_gamma as number[][]) || [];
  const corrRho = (risk?.correlation_rho as any[]) || [];

  const tabs: { id: Tab; label: string }[] = [
    { id: 'greeks', label: 'Greeks & Profiles' },
    { id: 'cross', label: 'Cross-Asset & Corr' },
    { id: 'prob', label: 'Probabilities & Timing' },
    { id: 'diag', label: 'Pricing Diagnostics' },
  ];

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1 border-b border-slate-200 pb-1">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={`px-2 py-1 text-[10px] font-bold rounded-t ${
              tab === t.id ? 'bg-[#000066] text-white' : 'text-slate-500 hover:bg-slate-50'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'greeks' && <div>{childrenGreeks}</div>}

      {tab === 'cross' && (
        <div className="grid md:grid-cols-2 gap-3 text-[11px]">
          <div className="border border-slate-100 rounded p-2">
            <div className="font-bold text-slate-500 uppercase text-[10px] mb-2">Cross-Gamma</div>
            {cross.length === 0 ? (
              <div className="text-slate-400">Single-asset — diagonal only after price.</div>
            ) : (
              <table className="w-full font-mono text-[10px]">
                <tbody>
                  {cross.map((row, i) => (
                    <tr key={i}>
                      {row.map((v, j) => (
                        <td
                          key={j}
                          className="p-1 text-center"
                          style={{
                            background: `rgba(226,0,0,${Math.min(Math.abs(v) * 50, 0.45)})`,
                          }}
                        >
                          {v.toExponential(2)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          <div className="border border-slate-100 rounded p-2">
            <div className="font-bold text-slate-500 uppercase text-[10px] mb-2">Correlation Rho</div>
            {corrRho.length === 0 ? (
              <div className="text-slate-400">Need ≥2 underlyings.</div>
            ) : (
              <ul className="space-y-1 font-mono">
                {corrRho.map((r, idx) => (
                  <li key={idx}>
                    ρ{r.i}{r.j}={Number(r.rho).toFixed(2)} → ∂V/∂ρ = {Number(r.corr_rho).toExponential(3)}
                  </li>
                ))}
              </ul>
            )}
            {risk?.vanna != null && (
              <div className="mt-3 text-slate-600">
                Vanna (∂Δ/∂σ): <span className="font-mono font-bold">{Number(risk.vanna).toExponential(3)}</span>
              </div>
            )}
          </div>
        </div>
      )}

      {tab === 'prob' && (
        <div className="space-y-3">
          <div className="grid grid-cols-3 gap-2 text-[11px]">
            <div className="bg-slate-50 rounded p-2">
              <div className="text-slate-400 text-[9px] uppercase">KO Prob</div>
              <div className="font-mono font-bold text-lg">
                {((risk?.event_probabilities?.ko_prob ?? 0) * 100).toFixed(1)}%
              </div>
            </div>
            <div className="bg-slate-50 rounded p-2">
              <div className="text-slate-400 text-[9px] uppercase">Final KI</div>
              <div className="font-mono font-bold text-lg text-[#E20000]">
                {((risk?.event_probabilities?.ki_prob ?? 0) * 100).toFixed(1)}%
              </div>
            </div>
            <div className="bg-slate-50 rounded p-2">
              <div className="text-slate-400 text-[9px] uppercase">E[Duration]</div>
              <div className="font-mono font-bold text-lg">
                {Number(risk?.expected_duration ?? 0).toFixed(2)}y
              </div>
            </div>
          </div>
          <div className="h-[200px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={mergedBars}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.4} />
                <XAxis dataKey="obs" tick={{ fontSize: 9 }} />
                <YAxis tick={{ fontSize: 9 }} unit="%" />
                <Tooltip contentStyle={{ fontSize: 11 }} />
                <Bar dataKey="marginal" fill="#000066" name="Marginal KO %" />
                <Bar dataKey="cum" fill="#EAB308" name="Cumulative KO %" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {tab === 'diag' && (
        <div className="space-y-2 text-[11px]">
          {solveDiag?.detail && (
            <div className="bg-amber-50 border border-amber-200 text-amber-900 rounded px-2 py-1.5">
              {String(solveDiag.detail)}
            </div>
          )}
          {(solveDiag?.warnings as string[] | undefined)?.map((w, i) => (
            <div key={i} className="bg-orange-50 border border-orange-100 rounded px-2 py-1">
              {w}
            </div>
          ))}
          <div className="grid grid-cols-2 gap-2">
            <div className="bg-slate-50 rounded p-2">
              <div className="text-slate-400 text-[9px] uppercase">ZCB Floor</div>
              <div className="font-mono font-bold">
                {Number(risk?.zero_coupon_bond_floor?.pv ?? 0).toFixed(4)}
              </div>
              <div className="text-slate-400 text-[9px]">
                y = {Number(risk?.zero_coupon_bond_floor?.implied_yield ?? 0).toFixed(4)}
              </div>
            </div>
            <div className="bg-slate-50 rounded p-2">
              <div className="text-slate-400 text-[9px] uppercase">Gap Risk</div>
              <div className="font-mono font-bold text-[#E20000]">
                {Number(risk?.gap_risk ?? 0).toExponential(3)}
              </div>
            </div>
          </div>
          {solveDiag && (
            <pre className="bg-slate-900 text-slate-100 rounded p-2 text-[9px] overflow-auto max-h-40">
              {JSON.stringify(
                {
                  ok: solveDiag.ok,
                  solve_for: solveDiag.solve_for,
                  solution: solveDiag.solution,
                  residual: solveDiag.residual,
                  diagnostics: solveDiag.diagnostics,
                },
                null,
                2,
              )}
            </pre>
          )}
        </div>
      )}
    </div>
  );
};

export default RiskDashboard;
