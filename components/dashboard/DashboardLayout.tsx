import React, { useState } from 'react';
import YieldCurveView from './YieldCurveView';
import VolAnalyticsView from './VolAnalyticsView';
import OptionTQuoteBoard, { StrikePickPayload } from './OptionTQuoteBoard';
import { ProductCode } from '../../services/api';

interface Props {
  product: ProductCode;
  tradeDate: string;
  onProductChange?: (p: ProductCode) => void;
  onStrikePick?: (payload: StrikePickPayload) => void;
}

const DashboardLayout: React.FC<Props> = ({ product, tradeDate, onProductChange, onStrikePick }) => {
  const [sub, setSub] = useState<'yield' | 'vol' | 'tquote'>('vol');

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold text-[#000066]">Market Intelligence Dashboard</h2>
          <p className="text-[11px] text-slate-500">Yield · JW-SVI Skew/Surface · Vol Cone · T-Quote</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex gap-1">
            {(['HO', 'IO', 'MO'] as ProductCode[]).map((p) => (
              <button
                key={p}
                onClick={() => onProductChange?.(p)}
                className={`px-2 py-1 text-[10px] font-bold rounded border ${
                  product === p ? 'bg-[#000066] text-white border-[#000066]' : 'bg-white text-slate-500 border-slate-200'
                }`}
              >
                {p}
              </button>
            ))}
          </div>
          <span className="text-[10px] font-mono text-slate-400">{tradeDate || 'latest'}</span>
        </div>
      </div>

      <div className="flex gap-1 bg-slate-100 p-1 rounded-lg w-fit">
        {([
          ['yield', 'Yield Curve'],
          ['vol', 'Vol Analytics'],
          ['tquote', 'T-Quote'],
        ] as const).map(([id, label]) => (
          <button
            key={id}
            onClick={() => setSub(id)}
            className={`px-3 py-1.5 text-xs font-bold rounded-md transition ${
              sub === id ? 'bg-white text-[#000066] shadow' : 'text-slate-500'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="min-h-[560px]">
        {sub === 'yield' && <YieldCurveView asof={tradeDate || undefined} />}
        {sub === 'vol' && <VolAnalyticsView product={product} tradeDate={tradeDate || undefined} />}
        {sub === 'tquote' && (
          <OptionTQuoteBoard
            product={product}
            tradeDate={tradeDate || undefined}
            onStrikePick={onStrikePick}
          />
        )}
      </div>
    </div>
  );
};

export default DashboardLayout;
