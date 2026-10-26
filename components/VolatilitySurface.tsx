
import React, { useEffect, useState } from 'react';
import Plot from 'react-plotly.js'; 
import { VolSurfaceModel, InterpolationMethod } from '../types';
import { Layers } from 'lucide-react';

interface Props {
  model: VolSurfaceModel;
  spot: number;
  method: InterpolationMethod;
}

const VolatilitySurfaceChart: React.FC<Props> = ({ model, spot, method }) => {
  const [data, setData] = useState<any[]>([]);

  useEffect(() => {
    // Generate Grid
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
       y_ttm.push((j / stepsT) * 2.0); // Up to 2 years
    }

    // Evaluate Surface
    for (let j = 0; j < y_ttm.length; j++) {
        const row: number[] = [];
        const t = y_ttm[j];
        for (let i = 0; i < x_strike.length; i++) {
            const k = x_strike[i];
            row.push(model.predict(k, t));
        }
        z_iv.push(row);
    }

    // Colorscale configurations based on method for visual distinction
    // Cubic Spline: Default Corporate Blue
    let colorscale = [
        [0, '#e0f2fe'], [0.5, '#3b82f6'], [1, '#000066'] 
    ];

    if (method === 'JW SVI') {
        colorscale = [[0, '#ECFDF5'], [0.5, '#14B8A6'], [1, '#134E4A']];
    } else if (method === 'Local Volatility') {
        colorscale = [[0, '#FEFCE8'], [0.5, '#EAB308'], [1, '#713F12']]; 
    } else if (method === 'Stochastic Volatility') {
        colorscale = [[0, '#FEF2F2'], [0.5, '#E20000'], [1, '#7F1D1D']]; 
    }

    setData([
      {
        z: z_iv,
        x: x_strike,
        y: y_ttm,
        type: 'surface',
        colorscale: colorscale,
        showscale: true,
        contours: {
            z: {
                show: true,
                usecolormap: true,
                highlightcolor: "#FFF100", 
                project: { z: true }
            },
            x: { show: true, color: 'rgba(0,0,0,0.1)' },
            y: { show: true, color: 'rgba(0,0,0,0.1)' }
        },
        lightposition: {x: 100, y: 100, z: 2000},
        opacity: 0.95
      }
    ]);

  }, [model, spot, method]);

  return (
    <div className="w-full h-full bg-white rounded-lg shadow-sm border border-slate-200 flex flex-col">
      {/* Header Title Only */}
      <div className="px-4 py-3 border-b border-slate-100 flex justify-between items-center">
          <h3 className="font-bold text-[#000066] flex items-center gap-2">
              <Layers size={18} /> Implied Volatility Surface
          </h3>
      </div>

      {/* Plot Area */}
      <div className="flex-1 p-2 min-h-0">
        <Plot
            data={data}
            layout={{
            width: undefined, // Responsive
            height: undefined, // Handled by parent flex
            autosize: true,
            margin: { l: 0, r: 0, b: 0, t: 10 },
            scene: {
                xaxis: { title: { text: 'Strike ($)', font: {size: 10} } },
                yaxis: { title: { text: 'Maturity (Yrs)', font: {size: 10} } },
                zaxis: { title: { text: 'Implied Vol', font: {size: 10} } },
                camera: {
                    eye: { x: 1.5, y: 1.5, z: 0.5 }
                },
                aspectratio: { x: 1, y: 1, z: 0.5 }
            },
            paper_bgcolor: 'rgba(0,0,0,0)',
            plot_bgcolor: 'rgba(0,0,0,0)',
            showlegend: false
            }}
            useResizeHandler={true}
            style={{ width: '100%', height: '100%' }}
            config={{ displayModeBar: true, displaylogo: false }}
        />
      </div>
      
      {/* Footer Info */}
      <div className="px-4 py-2 border-t border-slate-100 bg-slate-50 text-[10px] text-slate-500 text-center">
        {method === 'JW SVI' && "* Gatheral–Jacquier JW-SVI fitted to CFFEX market IV (Black-76 vs IH/IF/IM forwards)."}
        {method === 'Cubic Spline' && "* Smooth interpolation ensuring differentiability across strikes."}
        {method === 'Local Volatility' && "* Dupire-calibrated surface representing the instantaneous volatility function σ(S,t)."}
        {method === 'Stochastic Volatility' && "* Heston model calibration incorporating mean-reverting variance and vol-of-vol dynamics."}
      </div>
    </div>
  );
};

export default VolatilitySurfaceChart;
