import React, { useEffect, useMemo, useState } from 'react';
import Plot from 'react-plotly.js';
import { VolSurfaceModel, InterpolationMethod } from '../types';
import {
  Layers, Move3d, Compass, Sun, Grid3X3, AlignJustify, Eye, RotateCcw,
} from 'lucide-react';
import StrikeAxisToggle from './StrikeAxisToggle';
import { StrikeAxisMode, paddedDomain } from '../utils/strikeAxis';

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
  /** Controlled axis mode; if omitted, chart keeps its own toggle */
  xAxisMode?: StrikeAxisMode;
  onXAxisModeChange?: (m: StrikeAxisMode) => void;
}

const DEFAULT_CAMERA = { x: 1.5, y: 1.5, z: 0.5 };

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
  return pts.filter(
    (p) => (p.cp === 'C' && p.strike >= spot * 0.98) || (p.cp === 'P' && p.strike <= spot * 1.02)
  );
}

function strikeBounds(spot: number, pts: MarketPoint[]): [number, number] {
  const strikes = pts.map((p) => p.strike).filter((k) => Number.isFinite(k) && k > 0);
  if (strikes.length >= 2) {
    const [lo, hi] = paddedDomain(strikes, 0.03, spot * 0.005);
    return [Math.max(lo, spot * 0.5), Math.min(hi, spot * 2.0)];
  }
  if (strikes.length === 1) {
    const k = strikes[0];
    return [Math.min(k, spot) * 0.97, Math.max(k, spot) * 1.03];
  }
  return [spot * 0.85, spot * 1.15];
}

function ttmBounds(pts: MarketPoint[]): [number, number] {
  const ttms = pts.map((p) => p.ttm).filter((t) => Number.isFinite(t) && t > 0);
  if (ttms.length) {
    const hi = Math.max(...ttms);
    return [Math.min(...ttms, 1 / 365), Math.max(hi * 1.05, 0.08)];
  }
  return [1 / 365, 2.0];
}

const Toggle: React.FC<{ on: boolean; onToggle: () => void }> = ({ on, onToggle }) => (
  <button
    type="button"
    onClick={onToggle}
    className={`w-10 h-5 rounded-full relative transition-colors ${on ? 'bg-[#000066]' : 'bg-slate-300'}`}
  >
    <span
      className={`absolute top-1 left-1 bg-white w-3 h-3 rounded-full transition-transform ${
        on ? 'translate-x-5' : 'translate-x-0'
      }`}
    />
  </button>
);

const VolatilitySurfaceChart: React.FC<Props> = ({
  model,
  spot,
  method,
  side = 'otm',
  product,
  tradeDate,
  marketPoints = [],
  xAxisMode: controlledMode,
  onXAxisModeChange,
}) => {
  const [internalMode, setInternalMode] = useState<StrikeAxisMode>('sticky_delta');
  const xAxisMode = controlledMode ?? internalMode;
  const setXAxisMode = (m: StrikeAxisMode) => {
    onXAxisModeChange?.(m);
    if (controlledMode === undefined) setInternalMode(m);
  };

  // --- View Controls (Booster-style) ---
  const [camera, setCamera] = useState({ ...DEFAULT_CAMERA });
  const [showGrid, setShowGrid] = useState(true);
  const [showZContours, setShowZContours] = useState(true);
  const [surfaceOpacity, setSurfaceOpacity] = useState(0.92);
  const [showMarketPts, setShowMarketPts] = useState(true);

  const [data, setData] = useState<any[]>([]);
  const [xDomain, setXDomain] = useState<[number, number] | undefined>(undefined);
  const sideMeta = SIDE_META[side];

  const filteredMkt = useMemo(() => {
    const pts = filterMarket(side, marketPoints, spot);
    if (pts.length <= 800) return pts;
    const step = Math.ceil(pts.length / 800);
    return pts.filter((_, i) => i % step === 0);
  }, [marketPoints, side, spot]);

  useEffect(() => {
    const x_axis: number[] = [];
    const y_ttm: number[] = [];
    const z_iv: number[][] = [];

    const [minStrike, maxStrike] = strikeBounds(spot, filteredMkt);
    const [tMin, tMax] = ttmBounds(filteredMkt);
    const stepsK = 30;
    const stepsT = 20;
    const sticky = xAxisMode === 'sticky_delta';

    const toX = (k: number) => (sticky ? (k / spot) * 100 : k);
    const fromX = (x: number) => (sticky ? (x / 100) * spot : x);

    const xMin = toX(minStrike);
    const xMax = toX(maxStrike);
    setXDomain([xMin, xMax]);

    for (let i = 0; i <= stepsK; i++) {
      x_axis.push(xMin + (xMax - xMin) * (i / stepsK));
    }
    for (let j = 0; j <= stepsT; j++) {
      y_ttm.push(tMin + (tMax - tMin) * (j / stepsT));
    }

    for (let j = 0; j < y_ttm.length; j++) {
      const row: number[] = [];
      const t = y_ttm[j];
      for (let i = 0; i < x_axis.length; i++) {
        row.push(model.predict(fromX(x_axis[i]), t));
      }
      z_iv.push(row);
    }

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
        x: x_axis,
        y: y_ttm,
        type: 'surface',
        name: sideMeta.label,
        colorscale,
        showscale: true,
        colorbar: { title: { text: 'IV', font: { size: 10 } }, thickness: 14, len: 0.7 },
        contours: {
          z: {
            show: showZContours,
            usecolormap: true,
            highlightcolor: '#FFF100',
            project: { z: true },
          },
          x: { show: showGrid, color: 'rgba(0,0,0,0.15)' },
          y: { show: showGrid, color: 'rgba(0,0,0,0.15)' },
        },
        lightposition: { x: 100, y: 100, z: 2000 },
        opacity: surfaceOpacity,
      },
    ];

    if (showMarketPts && filteredMkt.length > 0) {
      const calls = filteredMkt.filter((p) => p.cp === 'C');
      const puts = filteredMkt.filter((p) => p.cp === 'P');
      if (calls.length) {
        traces.push({
          type: 'scatter3d',
          mode: 'markers',
          name: 'Call quotes',
          x: calls.map((p) => toX(p.strike)),
          y: calls.map((p) => p.ttm),
          z: calls.map((p) => p.iv),
          marker: { size: 2.2, color: '#2563eb', opacity: 0.65 },
          hovertemplate: sticky
            ? 'Call K%=%{x:.1f}<br>T=%{y:.3f}<br>IV=%{z:.3f}<extra></extra>'
            : 'Call K=%{x:.0f}<br>T=%{y:.3f}<br>IV=%{z:.3f}<extra></extra>',
        });
      }
      if (puts.length) {
        traces.push({
          type: 'scatter3d',
          mode: 'markers',
          name: 'Put quotes',
          x: puts.map((p) => toX(p.strike)),
          y: puts.map((p) => p.ttm),
          z: puts.map((p) => p.iv),
          marker: { size: 2.2, color: '#dc2626', opacity: 0.65 },
          hovertemplate: sticky
            ? 'Put K%=%{x:.1f}<br>T=%{y:.3f}<br>IV=%{z:.3f}<extra></extra>'
            : 'Put K=%{x:.0f}<br>T=%{y:.3f}<br>IV=%{z:.3f}<extra></extra>',
        });
      }
    }

    setData(traces);
  }, [
    model, spot, method, side, sideMeta, filteredMkt, xAxisMode,
    showGrid, showZContours, surfaceOpacity, showMarketPts,
  ]);

  const xTitle =
    xAxisMode === 'sticky_delta' ? 'Moneyness % (δ-sticky)' : 'Strike';

  const resetView = () => {
    setCamera({ ...DEFAULT_CAMERA });
    setShowGrid(true);
    setShowZContours(true);
    setSurfaceOpacity(0.92);
    setShowMarketPts(true);
  };

  return (
    <div className="w-full h-full bg-white rounded-lg shadow-sm border border-slate-200 flex flex-col min-h-0">
      <div className="px-4 py-3 border-b border-slate-100 flex justify-between items-center gap-3 shrink-0">
        <h3 className="font-bold text-[#000066] flex items-center gap-2 min-w-0">
          <Layers size={18} />
          <span className="truncate">Implied Volatility Surface</span>
        </h3>
        <div className="flex items-center gap-2 shrink-0">
          <StrikeAxisToggle mode={xAxisMode} onChange={setXAxisMode} />
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

      <div className="px-4 py-1.5 bg-slate-50 border-b border-slate-100 text-[10px] text-slate-500 flex justify-between shrink-0">
        <span>{sideMeta.label} — {sideMeta.hint}</span>
        <span className="font-mono">
          {filteredMkt.length > 0 ? `${filteredMkt.length} mkt pts` : 'fitted surface'}
          {xDomain
            ? ` · X[${xDomain[0].toFixed(xAxisMode === 'sticky_delta' ? 1 : 0)}, ${xDomain[1].toFixed(xAxisMode === 'sticky_delta' ? 1 : 0)}]`
            : ''}
        </span>
      </div>

      <div className="flex-1 p-2 min-h-[320px]">
        <Plot
          data={data}
          layout={{
            width: undefined,
            height: undefined,
            autosize: true,
            margin: { l: 0, r: 0, b: 0, t: 10 },
            uirevision: 'vol-surface',
            scene: {
              xaxis: {
                title: { text: xTitle, font: { size: 10 } },
                ...(xDomain ? { range: xDomain } : {}),
              },
              yaxis: { title: { text: 'Maturity (Yrs)', font: { size: 10 } } },
              zaxis: { title: { text: 'Implied Vol', font: { size: 10 } } },
              camera: { eye: camera },
              aspectratio: { x: 1, y: 1, z: 0.5 },
            },
            paper_bgcolor: 'rgba(0,0,0,0)',
            plot_bgcolor: 'rgba(0,0,0,0)',
            showlegend: showMarketPts && filteredMkt.length > 0,
            legend: { x: 0, y: 1, font: { size: 10 } },
          }}
          useResizeHandler={true}
          style={{ width: '100%', height: '100%' }}
          config={{ displayModeBar: true, displaylogo: false }}
        />
      </div>

      {/* 3D View Controls — Booster-style */}
      <div className="border-t border-slate-200 bg-white px-4 py-3 shrink-0">
        <div className="flex justify-between items-center mb-3">
          <h4 className="font-bold text-[#000066] text-sm flex items-center gap-2">
            <Move3d size={16} /> 3D View Controls
          </h4>
          <button
            type="button"
            onClick={resetView}
            className="text-[10px] flex items-center gap-1 bg-slate-100 hover:bg-slate-200 text-slate-600 px-2 py-1 rounded transition border border-transparent font-bold"
          >
            <RotateCcw size={10} /> Reset
          </button>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div className="p-3 bg-slate-50 rounded border border-slate-200 space-y-2.5">
            <h5 className="text-[10px] font-bold text-slate-500 uppercase flex items-center gap-1.5">
              <Sun size={11} /> Surface Appearance
            </h5>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-xs font-bold text-slate-600">
                <Grid3X3 size={13} />
                <span>Mountain Lines</span>
              </div>
              <Toggle on={showGrid} onToggle={() => setShowGrid((v) => !v)} />
            </div>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-xs font-bold text-slate-600">
                <AlignJustify size={13} className="rotate-90" />
                <span>Level Contours</span>
              </div>
              <Toggle on={showZContours} onToggle={() => setShowZContours((v) => !v)} />
            </div>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-xs font-bold text-slate-600">
                <Eye size={13} />
                <span>Market Quotes</span>
              </div>
              <Toggle on={showMarketPts} onToggle={() => setShowMarketPts((v) => !v)} />
            </div>
            <div>
              <div className="flex justify-between text-xs mb-1">
                <span>Surface Opacity</span>
                <span className="font-mono font-bold text-[#000066]">
                  {(surfaceOpacity * 100).toFixed(0)}%
                </span>
              </div>
              <input
                type="range"
                min={0.15}
                max={1}
                step={0.05}
                value={surfaceOpacity}
                onChange={(e) => setSurfaceOpacity(parseFloat(e.target.value))}
                className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer"
              />
            </div>
          </div>

          <div className="p-3 bg-slate-50 rounded border border-slate-200 space-y-2.5">
            <h5 className="text-[10px] font-bold text-slate-500 uppercase flex items-center gap-1.5">
              <Compass size={11} /> Camera Position
            </h5>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <div className="flex justify-between text-xs mb-1">
                  <span>Eye X</span>
                  <span className="font-mono text-[10px] text-slate-500">{camera.x.toFixed(1)}</span>
                </div>
                <input
                  type="range"
                  min={-5}
                  max={5}
                  step={0.1}
                  value={camera.x}
                  onChange={(e) => setCamera({ ...camera, x: parseFloat(e.target.value) })}
                  className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                />
              </div>
              <div>
                <div className="flex justify-between text-xs mb-1">
                  <span>Eye Y</span>
                  <span className="font-mono text-[10px] text-slate-500">{camera.y.toFixed(1)}</span>
                </div>
                <input
                  type="range"
                  min={-5}
                  max={5}
                  step={0.1}
                  value={camera.y}
                  onChange={(e) => setCamera({ ...camera, y: parseFloat(e.target.value) })}
                  className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                />
              </div>
            </div>
            <div>
              <div className="flex justify-between text-xs mb-1">
                <span>Eye Z (Zoom)</span>
                <span className="font-mono text-[10px] text-slate-500">{camera.z.toFixed(1)}</span>
              </div>
              <input
                type="range"
                min={0.1}
                max={5}
                step={0.1}
                value={camera.z}
                onChange={(e) => setCamera({ ...camera, z: parseFloat(e.target.value) })}
                className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer"
              />
            </div>
            <button
              type="button"
              onClick={() => setCamera({ ...DEFAULT_CAMERA })}
              className="w-full text-[10px] bg-slate-200 hover:bg-slate-300 text-slate-600 font-bold py-1 rounded"
            >
              Reset Camera
            </button>
          </div>
        </div>
      </div>

      <div className="px-4 py-1.5 border-t border-slate-100 bg-slate-50 text-[10px] text-slate-500 text-center shrink-0">
        {side === 'otm' && 'OTM：虚值侧 Call+Put 拼成完整微笑（desk 默认）。'}
        {side === 'call' && 'Call：仅用认购报价拟合的曲面（偏看涨翼）。'}
        {side === 'put' && 'Put：仅用认沽报价拟合的曲面（偏看跌翼）。'}
        {method === 'JW SVI' ? ' · JW-SVI / Black-76' : ` · ${method}`}
      </div>
    </div>
  );
};

export default VolatilitySurfaceChart;
