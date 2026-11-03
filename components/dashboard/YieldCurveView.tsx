import React, { useEffect, useState } from 'react';
import { fetchYieldCurve } from '../../services/api_v1';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';

const COLORS = ['#000066', '#EAB308', '#E20000', '#16a34a'];

interface Props {
  asof?: string;
}

const YieldCurveView: React.FC<Props> = ({ asof }) => {
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetchYieldCurve({ history: '1W,1M,1Y', asof: asof || undefined })
      .then(setData)
      .catch((e) => setErr(String(e)));
  }, [asof]);

  const chartRows = React.useMemo(() => {
    if (!data?.curves?.Current) return [];
    const tenors = data.curves.Current.map((p: any) => p.tenor_years);
    return tenors.map((t: number, i: number) => {
      const row: any = { tenor: t, label: data.curves.Current[i].code };
      for (const [name, pts] of Object.entries(data.curves) as [string, any[]][]) {
        const hit = pts.find((p) => Math.abs(p.tenor_years - t) < 1e-9) || pts[i];
        if (hit) row[name] = hit.rate_pct;
      }
      return row;
    });
  }, [data]);

  return (
    <div className="bg-white rounded-lg border border-slate-200 p-4 h-full flex flex-col gap-3">
      <div className="flex justify-between items-center">
        <h3 className="font-bold text-[#000066] text-sm">Yield Curve Hub</h3>
        <span className="text-[10px] text-slate-400 font-mono">asof {data?.asof ?? asof ?? '—'}</span>
      </div>
      {err && <div className="text-xs text-red-600">{err}</div>}
      <div className="flex-1 min-h-[240px]">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartRows}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
            <XAxis dataKey="tenor" tick={{ fontSize: 10 }} label={{ value: 'Tenor (Y)', fontSize: 10, position: 'insideBottom', offset: -2 }} />
            <YAxis tick={{ fontSize: 10 }} unit="%" domain={['auto', 'auto']} />
            <Tooltip />
            <Legend wrapperStyle={{ fontSize: 10 }} />
            {data && Object.keys(data.curves).map((name, i) => (
              <Line key={name} type="monotone" dataKey={name} stroke={COLORS[i % COLORS.length]} dot={false} strokeWidth={name === 'Current' ? 2.5 : 1.5} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      {data?.spreads && (
        <div className="grid grid-cols-2 gap-2 text-[10px]">
          {Object.entries(data.spreads).map(([k, v]) => (
            <div key={k} className="bg-slate-50 rounded px-2 py-1 flex justify-between">
              <span className="text-slate-500">{k}</span>
              <span className="font-mono font-bold text-[#000066]">{(v as number).toFixed(1)} bp</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default YieldCurveView;
