import React, { useMemo } from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  AreaChart,
  Area,
  ComposedChart,
} from 'recharts';
import { PayoffPoint, OptionLeg } from '../types';

interface ChartProps {
  data: any[];
  marketSpot: number;
  yScale?: number; 
  height?: number | string;
  legs?: OptionLeg[]; // Optional: For Decomposition
  legOpacity?: number; // Optional: Opacity for decomposition legs
}

const LEG_COLORS = ['#3b82f6', '#8b5cf6', '#ec4899', '#14b8a6', '#f97316', '#6366f1'];

const formatYAxis = (val: number) => {
  if (val === 0) return '0';
  if (Math.abs(val) >= 1_000_000_000) return `${(val / 1_000_000_000).toFixed(1)}B`;
  if (Math.abs(val) >= 1_000_000) return `${(val / 1_000_000).toFixed(1)}M`;
  if (Math.abs(val) >= 1_000) return `${(val / 1_000).toFixed(1)}k`;
  if (Math.abs(val) < 0.01) return val.toFixed(4);
  return val.toFixed(2);
};

const getDomain = (data: any[], key: string, scale: number = 1.1) => {
    if (!data.length) return ['auto', 'auto'];
    const values = data.map(d => d[key]);
    const min = Math.min(...values);
    const max = Math.max(...values);
    if (min === max) return [min - 10, max + 10];
    const center = (max + min) / 2;
    const spread = max - min;
    const halfRange = (spread * scale) / 2;
    return [center - halfRange, center + halfRange];
};

interface PayoffChartProps extends ChartProps {
    dataKey?: string;
    referenceLineY?: number;
}

export const PayoffChart: React.FC<PayoffChartProps> = ({ data, marketSpot, yScale = 1.1, height = 300, legs, dataKey = 'pnl', referenceLineY = 0, legOpacity }) => {
  const yDomain = useMemo(() => getDomain(data, dataKey, yScale), [data, yScale, dataKey]);

  return (
    <div className="w-full" style={{ height: height }}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 10, right: 30, left: 10, bottom: 0 }}>
          <defs>
            <linearGradient id="colorPnL" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#E20000" stopOpacity={0.3} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" strokeOpacity={1} />
          
          <XAxis 
            dataKey="moneyness" 
            stroke="#64748b" 
            tickFormatter={(val) => `${val.toFixed(0)}%`}
            type="number"
            domain={['auto', 'auto']}
            allowDataOverflow
            tick={{ fill: '#64748b', fontSize: 12 }}
          />
          <YAxis 
            stroke="#64748b"
            tick={{ fill: '#64748b', fontSize: 12 }}
            tickFormatter={(val) => `${((val / marketSpot) * 100).toFixed(1)}%`}
            domain={yDomain}
            allowDataOverflow={true}
            width={45}
          />
          <Tooltip
            contentStyle={{ backgroundColor: 'rgba(255, 255, 255, 0.8)', borderColor: '#e2e8f0', color: '#0f172a', padding: '2px 5px', fontSize: '10px' }}
            itemStyle={{ padding: 0, margin: 0 }}
            formatter={(value: number, name: string) => {
               if(name.startsWith('val_')) {
                   return [`$${value.toFixed(0)}`, 'Leg'];
               }
               return [
                `$${value.toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 0})}`, 
                dataKey === 'pnl' ? 'PnL' : 'Payoff'
               ]
            }}
            labelFormatter={(label: number) => `K%: ${label.toFixed(0)}%`}
          />
          <ReferenceLine y={referenceLineY} stroke="#EAB308" strokeWidth={2} strokeDasharray="3 3" label={{ value: referenceLineY !== 0 ? 'Net Cost' : '0', fill: '#EAB308', fontSize: 9, position: 'insideTopLeft' }} />
          <ReferenceLine x={100} stroke="#000066" strokeDasharray="3 3" />
          
          {/* Main Strategy Area */}
          <Area
            type="monotone"
            dataKey={dataKey}
            stroke="#000066"
            strokeWidth={3}
            fill="url(#colorPnL)"
            isAnimationActive={false}
            name="Strategy"
          />

          {/* Individual Legs (Decomposition) */}
          {legs && legs.map((leg, idx) => (
             <Line 
                key={leg.id}
                type="monotone"
                // If plotting PnL, use pnl_id. If plotting Intrinsic, we might need a different metric or disable decomp for now.
                // Assuming PnL chart decomp is mainly used.
                dataKey={`pnl_${leg.id}`}
                stroke={LEG_COLORS[idx % LEG_COLORS.length]}
                strokeWidth={1}
                strokeDasharray="5 5"
                dot={false}
                isAnimationActive={false}
                opacity={legOpacity ?? 0.7}
                name={`Leg ${idx + 1}`}
             />
          ))}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
};

interface GreeksChartProps extends ChartProps {
  dataKey: string;
  color: string;
  name: string;
}

export const GreeksChart: React.FC<GreeksChartProps> = ({ data, marketSpot, dataKey, color, name, yScale = 1.1, legs, legOpacity }) => {
  const yDomain = useMemo(() => getDomain(data, dataKey, yScale), [data, dataKey, yScale]);

  return (
    <div className="h-[200px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" strokeOpacity={1} />
          <XAxis 
            dataKey="moneyness" 
            stroke="#64748b" 
            tickFormatter={(val) => `${val.toFixed(0)}%`}
            type="number"
            domain={['auto', 'auto']}
            allowDataOverflow
            tick={{ fill: '#64748b', fontSize: 10 }}
            interval="preserveStartEnd"
          />
          <YAxis 
            stroke="#64748b" 
            fontSize={12} 
            tickFormatter={formatYAxis} 
            tick={{ fill: '#64748b' }} 
            domain={yDomain}
            allowDataOverflow={true}
            width={45}
          />
          <Tooltip
            contentStyle={{ backgroundColor: 'rgba(255, 255, 255, 0.8)', borderColor: '#e2e8f0', color: '#0f172a', padding: '2px 5px', fontSize: '10px' }}
            formatter={(value: number, nameProp: string) => {
                if (nameProp.includes('_')) return [value.toFixed(4), 'Leg'];
                return [value.toFixed(4), name];
            }}
            labelFormatter={(label: number) => `K%: ${label.toFixed(0)}%`}
            itemStyle={{ padding: 0, margin: 0 }}
          />
          <ReferenceLine y={0} stroke="#64748b" strokeWidth={1} />
          <ReferenceLine x={100} stroke="#000066" strokeDasharray="3 3" />
          
          <Line type="monotone" dataKey={dataKey} stroke={color} strokeWidth={2} dot={false} isAnimationActive={false} name="Total" />
          
          {/* Decomposition */}
          {legs && legs.map((leg, idx) => (
             <Line 
                key={leg.id}
                type="monotone"
                dataKey={`${dataKey}_${leg.id}`}
                stroke={LEG_COLORS[idx % LEG_COLORS.length]}
                strokeWidth={1}
                strokeDasharray="3 3"
                dot={false}
                isAnimationActive={false}
                opacity={legOpacity ?? 0.6}
                name={`Leg ${idx+1}`}
             />
          ))}

        </LineChart>
      </ResponsiveContainer>
    </div>
  );
};