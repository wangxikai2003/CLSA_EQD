import React from 'react';
import { Minus, Plus, RotateCcw } from 'lucide-react';
import { JwParams } from '../services/api';
import { ZERO_JW_DELTA } from '../services/volatilityService';

type Key = keyof JwParams;

const PARAMS: { key: Key; symbol: string; word: string; step: number }[] = [
  { key: 'v', symbol: 'v', word: 'ATM', step: 0.002 },
  { key: 'psi', symbol: 'ψ', word: 'Skew', step: 0.005 },
  { key: 'p', symbol: 'p', word: 'Put', step: 0.02 },
  { key: 'c', symbol: 'c', word: 'Call', step: 0.02 },
  { key: 'vtild', symbol: 'ṽ', word: 'Floor', step: 0.002 },
];

interface Props {
  base: JwParams | null | undefined;
  delta: JwParams;
  onChange: (next: JwParams) => void;
  /** denser layout for side panels */
  compact?: boolean;
}

const JwParamPanel: React.FC<Props> = ({ base, delta, onChange, compact }) => {
  if (!base) {
    return (
      <div className="text-[10px] text-slate-400 italic">Load a JW surface to edit parameters.</div>
    );
  }

  const bump = (key: Key, dir: 1 | -1) => {
    const step = PARAMS.find(p => p.key === key)!.step;
    onChange({ ...delta, [key]: delta[key] + dir * step });
  };

  const reset = () => onChange({ ...ZERO_JW_DELTA });
  const dirty = PARAMS.some(p => Math.abs(delta[p.key]) > 1e-12);

  return (
    <div className={`rounded border border-amber-200 bg-amber-50/60 ${compact ? 'p-2' : 'p-3'}`}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-[10px] font-bold uppercase text-amber-800 tracking-wide">
          JW Params {dirty ? '(shifted)' : ''}
        </span>
        <button
          type="button"
          onClick={reset}
          disabled={!dirty}
          className="flex items-center gap-0.5 text-[9px] font-bold text-amber-700 disabled:opacity-30 hover:text-amber-900"
          title="Reset to calibrated"
        >
          <RotateCcw size={10} /> Reset
        </button>
      </div>
      <div className="space-y-1.5">
        {PARAMS.map(({ key, symbol, word }) => {
          const d = delta[key];
          const val = base[key] + d;
          const shifted = Math.abs(d) > 1e-12;
          return (
            <div key={key} className="flex items-center gap-1">
              <span className="w-4 text-[10px] font-bold text-amber-900 font-serif shrink-0">{symbol}</span>
              <button
                type="button"
                onClick={() => bump(key, -1)}
                className="w-5 h-5 flex items-center justify-center rounded border border-amber-300 bg-white text-amber-800 hover:bg-amber-100 shrink-0"
              >
                <Minus size={10} />
              </button>
              <span className="flex-1 flex items-baseline justify-center gap-1 min-w-0 px-0.5">
                <span className="text-[9px] font-bold text-amber-800/80 shrink-0">{word}</span>
                <span className="font-mono text-[10px] font-bold text-[#000066] tabular-nums">
                  {val.toFixed(4)}
                </span>
                {shifted && (
                  <span className="text-[9px] font-semibold text-amber-700 font-mono">
                    ({d > 0 ? '+' : ''}{d.toFixed(3)})
                  </span>
                )}
              </span>
              <button
                type="button"
                onClick={() => bump(key, 1)}
                className="w-5 h-5 flex items-center justify-center rounded border border-amber-300 bg-white text-amber-800 hover:bg-amber-100 shrink-0"
              >
                <Plus size={10} />
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default JwParamPanel;
