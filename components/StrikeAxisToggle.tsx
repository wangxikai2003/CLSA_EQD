import React from 'react';
import { StrikeAxisMode } from '../utils/strikeAxis';

interface Props {
  mode: StrikeAxisMode;
  onChange: (m: StrikeAxisMode) => void;
  className?: string;
}

const StrikeAxisToggle: React.FC<Props> = ({ mode, onChange, className = '' }) => (
  <div className={`inline-flex rounded border border-slate-200 overflow-hidden text-[10px] font-bold ${className}`}>
    <button
      type="button"
      onClick={() => onChange('sticky_delta')}
      className={`px-2 py-0.5 transition ${
        mode === 'sticky_delta' ? 'bg-[#000066] text-white' : 'bg-white text-slate-500 hover:bg-slate-50'
      }`}
      title="相对 ATM：moneyness / log-moneyness（delta sticky）"
    >
      δ-sticky
    </button>
    <button
      type="button"
      onClick={() => onChange('strike')}
      className={`px-2 py-0.5 transition border-l border-slate-200 ${
        mode === 'strike' ? 'bg-[#000066] text-white' : 'bg-white text-slate-500 hover:bg-slate-50'
      }`}
      title="真实行权价 Strike"
    >
      Strike
    </button>
  </div>
);

export default StrikeAxisToggle;
