import React, { useEffect, useMemo, useState } from 'react';
import Plot from 'react-plotly.js';
import { VolSurfaceModel, InterpolationMethod } from '../types';
import { Layers } from 'lucide-react';

export type SurfaceSide = 'otm' | 'call' | 'put';

export interface MarketPoint {
  strike: number;
  ttm: number;
  iv: number;
  cp: string;
}

interface Props {
  model: VolSurfaceModel;
  spot: number;
  method: InterpolationMethod;
  side?: SurfaceSide;
  product?: string;
  tradeDate?: string;
  marketPoints?: MarketPoint[];
}

const SIDE_META: Record<
  SurfaceSide,
  { label: string; short: string; hint: string; badge: string; colorscale: [number, string][] }
> = {
  otm: {
    label: 'OTM Surface',
    short: 'OTM',
    hint: '虚值 Call+Put 混合微笑',
    badge: 'bg-[#EAB308] text-white',
    colorscale: [
      [0, '#FEFCE8'],
      [0.5, '#EAB308'],
      [1, '#713F12'],
    ],
  },
  call: {
    label: 'Call Surface',
    short: 'Call',
    hint: '仅认购期权拟合',
    badge: 'bg-[#000066] text-white',
    colorscale: [
      [0, '#e0f2fe'],
      [0.5, '#3b82f6'],
      [1, '#000066'],
    ],
  },
  put: {
    label: 'Put Surface',
    short: 'Put',
    hint: '仅认沽期权拟合',
    badge: 'bg-[#E20000] text-white',
    colorscale: [
      [0, '#FEF2F2'],
      [0.5, '#E20000'],
      [1, '#7F1D1D'],
    ],
  },
};

function filterMarket(side: SurfaceSide, pts: MarketPoint[], spot: number): MarketPoint[] {
  if (side === 'call') return pts.filter((p) => p.cp === 'C');
  if (side === 'put') return pts.filter((p) => p.cp === 'P');
  // OTM hybrid: OTM calls (K>=F≈spot) + OTM puts (K<=spot)
  return pts.filter(
    (p) => (p.cp === 'C' && p.strike >= spot * 0.98) || (p.cp === 'P' && p.strike <= spot * 1.02)
  );
}

const VolatilitySurfaceChart: React.FC<Props> = ({
  model,
  spot,
  method,
  side = 'otm',
  product,
  tradeDate,
  marketPoints = [],
}) => {
  const [data, setData] = useState<any[]>([]);
  const sideMeta = SIDE_META[side];

  const filteredMkt = useMemo(() => {
    const pts = filterMarket(side, marketPoints, spot);
    // Downsample for Plotly performance
    if (pts.length <= 800) return pts;
    const step = Math.ceil(pts.length / 800);
    return pts.filter((_, i) => i % step === 0);
  }, [marketPoints, side, spot]);

  useEffect(() => {
    const x_strike: number[] = [];
    const y_ttm: number[] = [];
    const z_iv: number[][] = [];

    const minStrike = spot * 0.7;
    const maxStrike = spot * 1.3;
    const stepsK = 30;
    const stepsT = 20;

    for (let i = 0; i <= stepsK; i++) {
      x_strike.push(minStrike + (maxStrike - minStrike) * (i / stepsK));
    }
    for (let j = 1; j <= stepsT; j++) {
      y_ttm.push((j / stepsT) * 2.0);
    }

    for (let j = 0; j < y_ttm.length; j++) {
      const row: number[] = [];
      const t = y_ttm[j];
      for (let i = 0; i < x_strike.length; i++) {
        row.push(model.predict(x_strike[i], t));
      }
      z_iv.push(row);
    }

    // Side colorscale takes priority for JW SVI; other methods keep method tint on OTM only
    let colorscale = sideMeta.colorscale;
    if (method === 'Stochastic Volatility' && side === 'otm') {
      colorscale = [
        [0, '#FEF2F2'],
        [0.5, '#E20000'],
        [1, '#7F1D1D'],
      ];
    } else if (method === 'Cubic Spline' && side === 'otm') {
      colorscale = [
        [0, '#e0f2fe'],
        [0.5, '#3b82f6'],
        [1, '#000066'],
      ];
    }

    const traces: any[] = [
      {
        z: z_iv,
        x: x_strike,
        y: y_ttm,
        type: 'surface',
        name: sideMeta.label,
        colorscale,
        showscale: true,
        colorbar: { title: { text: 'IV', font: { size: 10 } }, thickness: 14, len: 0.7 },
        contours: {
          z: {
            show: true,
            usecolormap: true,
            highlightcolor: '#FFF100',
            project: { z: true },
          },
          x: { show: true, color: 'rgba(0,0,0,0.1)' },
          y: { show: true, color: 'rgba(0,0,0,0.1)' },
        },
        lightposition: { x: 100, y: 100, z: 2000 },
        opacity: 0.92,
      },
    ];

    if (filteredMkt.length > 0) {
      const calls = filteredMkt.filter((p) => p.cp === 'C');
      const puts = filteredMkt.filter((p) => p.cp === 'P');
      if (calls.length) {
        traces.push({
          type: 'scatter3d',
          mode: 'markers',
          name: 'Call quotes',
          x: calls.map((p) => p.strike),
          y: calls.map((p) => p.ttm),
          z: calls.map((p) => p.iv),
          marker: { size: 2.2, color: '#2563eb', opacity: 0.65 },
          hovertemplate: 'Call K=%{x:.0f}<br>T=%{y:.3f}<br>IV=%{z:.3f}<extra></extra>',
        });
      }
      if (puts.length) {
        traces.push({
          type: 'scatter3d',
          mode: 'markers',
          name: 'Put quotes',
          x: puts.map((p) => p.strike),
          y: puts.map((p) => p.ttm),
          z: puts.map((p) => p.iv),
          marker: { size: 2.2, color: '#dc2626', opacity: 0.65 },
          hovertemplate: 'Put K=%{x:.0f}<br>T=%{y:.3f}<br>IV=%{z:.3f}<extra></extra>',
        });
      }
    }

    setData(traces);
  }, [model, spot, method, side, sideMeta, filteredMkt]);

  return (
    <div className="w-full h-full bg-white rounded-lg shadow-sm border border-slate-200 flex flex-col">
      <div className="px-4 py-3 border-b border-slate-100 flex justify-between items-center gap-3">
        <h3 className="font-bold text-[#000066] flex items-center gap-2 min-w-0">
          <Layers size={18} />
          <span className="truncate">Implied Volatility Surface</span>
        </h3>
        <div className="flex items-center gap-2 shrink-0">
          {product && (
            <span className="text-[10px] font-mono text-slate-500">{product}</span>
          )}
          {tradeDate && (
            <span className="text-[10px] font-mono text-slate-400">{tradeDate}</span>
          )}
          <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${sideMeta.badge}`}>
            {sideMeta.short}
          </span>
        </div>
      </div>

      <div className="px-4 py-1.5 bg-slate-50 border-b border-slate-100 text-[10px] text-slate-500 flex justify-between">
        <span>{sideMeta.label} — {sideMeta.hint}</span>
        <span className="font-mono">
          {filteredMkt.length > 0 ? `${filteredMkt.length} mkt pts` : 'fitted surface'}
        </span>
      </div>

      <div className="flex-1 p-2 min-h-0">
        <Plot
          data={data}
          layout={{
            width: undefined,
            height: undefined,
            autosize: true,
            margin: { l: 0, r: 0, b: 0, t: 10 },
            scene: {
              xaxis: { title: { text: 'Strike', font: { size: 10 } } },
              yaxis: { title: { text: 'Maturity (Yrs)', font: { size: 10 } } },
              zaxis: { title: { text: 'Implied Vol', font: { size: 10 } } },
              camera: { eye: { x: 1.5, y: 1.5, z: 0.5 } },
              aspectratio: { x: 1, y: 1, z: 0.5 },
            },
            paper_bgcolor: 'rgba(0,0,0,0)',
            plot_bgcolor: 'rgba(0,0,0,0)',
            showlegend: filteredMkt.length > 0,
            legend: { x: 0, y: 1, font: { size: 10 } },
          }}
          useResizeHandler={true}
          style={{ width: '100%', height: '100%' }}
          config={{ displayModeBar: true, displaylogo: false }}
        />
      </div>

      <div className="px-4 py-2 border-t border-slate-100 bg-slate-50 text-[10px] text-slate-500 text-center">
        {side === 'otm' && 'OTM：虚值侧 Call+Put 拼成完整微笑（desk 默认）。'}
        {side === 'call' && 'Call：仅用认购报价拟合的曲面（偏看涨翼）。'}
        {side === 'put' && 'Put：仅用认沽报价拟合的曲面（偏看跌翼）。'}
        {method === 'JW SVI' ? ' · JW-SVI / Black-76' : ` · ${method}`}
      </div>
    </div>
  );
};

export default VolatilitySurfaceChart;
