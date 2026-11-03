import React from 'react';
import { LineChart, Line, XAxis, YAxis, ResponsiveContainer, Tooltip, ReferenceArea } from 'recharts';
import { X } from 'lucide-react';

export interface ProgressPoint {
  paths: number;
  price: number;
  lo: number;
  hi: number;
}

interface Props {
  progress: number; // 0..1
  currentPaths: number;
  totalPaths: number;
  runningPrice?: number;
  stdError?: number;
  stage?: string;
  history?: ProgressPoint[];
  onCancel?: () => void;
}

const ProgressBar: React.FC<Props> = ({
  progress,
  currentPaths,
  totalPaths,
  runningPrice,
  stdError,
  stage,
  history = [],
  onCancel,
}) => {
  const pct = Math.min(100, Math.max(0, progress * 100));
  return (
    <div className="bg-white border border-slate-200 rounded-lg p-3 space-y-2 shadow-sm">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[10px] font-bold text-[#000066] uppercase tracking-wide">
          {stage || 'Monte Carlo'}
        </div>
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            className="text-[10px] flex items-center gap-1 px-2 py-0.5 rounded border border-slate-200 text-slate-600 hover:bg-red-50 hover:text-red-700"
          >
            <X size={12} /> Cancel
          </button>
        )}
      </div>
      <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
        <div
          className="h-full bg-[#000066] transition-all duration-300 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="flex justify-between text-[10px] font-mono text-slate-500">
        <span>{pct.toFixed(0)}% · {currentPaths.toLocaleString()} / {totalPaths.toLocaleString()} paths</span>
        {runningPrice != null && (
          <span>
            PV {runningPrice.toFixed(4)}
            {stdError != null && stdError > 0 && (
              <span className="text-slate-400"> ± {(2 * stdError).toFixed(4)}</span>
            )}
          </span>
        )}
      </div>
      {history.length > 1 && (
        <div className="h-[100px]">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={history}>
              <XAxis dataKey="paths" hide />
              <YAxis domain={['auto', 'auto']} tick={{ fontSize: 9 }} width={40} />
              <Tooltip
                contentStyle={{ fontSize: 10 }}
                formatter={(v: number, n: string) => [Number(v).toFixed(5), n]}
              />
              <ReferenceArea y1={history[history.length - 1]?.lo} y2={history[history.length - 1]?.hi} fill="#EAB308" fillOpacity={0.08} />
              <Line type="monotone" dataKey="price" stroke="#000066" strokeWidth={2} dot={false} name="Running" />
              <Line type="monotone" dataKey="hi" stroke="#94a3b8" strokeDasharray="3 2" dot={false} name="+2σ" />
              <Line type="monotone" dataKey="lo" stroke="#94a3b8" strokeDasharray="3 2" dot={false} name="-2σ" />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
};

export default ProgressBar;
