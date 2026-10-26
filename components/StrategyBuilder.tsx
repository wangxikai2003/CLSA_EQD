import React from 'react';
import { OptionLeg, OptionType, PositionType, VolSurfaceModel } from '../types';
import { Trash2, Plus, Calculator, RefreshCw, Lock } from 'lucide-react';
import { calculateBS } from '../services/pricingEngine';

interface Props {
  legs: OptionLeg[];
  setLegs: (legs: OptionLeg[]) => void;
  marketSpot: number;
  riskFreeRate: number;
  volModel?: VolSurfaceModel; // Optional: Link to Tab 3
  volShift?: number; // Global vol shift
  volSlope?: number; // Global vol slope (skew)
  volStrategy?: 'surface' | 'flat';
  flatVol?: number;
}

const StrategyBuilder: React.FC<Props> = ({ 
    legs, setLegs, marketSpot, riskFreeRate, volModel, 
    volShift = 0, volSlope = 0, volStrategy = 'surface', flatVol = 0.20 
}) => {
  
  const getVol = (k: number, t: number) => {
      if (volStrategy === 'flat') return flatVol;
      if (volModel) return volModel.predict(k, t);
      return 0.20; // Fallback
  }

  const addLeg = () => {
    const ttm = 30 / 365;
    const initialIV = getVol(marketSpot, ttm);
    
    const newLeg: OptionLeg = {
      id: Math.random().toString(36).substr(2, 9),
      type: OptionType.CALL,
      position: PositionType.LONG,
      strike: marketSpot,
      quantity: 1,
      iv: initialIV,
      t: ttm,
      r: riskFreeRate,
    };
    setLegs([...legs, newLeg]);
  };

  const removeLeg = (id: string) => {
    setLegs(legs.filter((l) => l.id !== id));
  };

  const updateLeg = (id: string, field: keyof OptionLeg, value: any) => {
    setLegs(
      legs.map((l) => {
        if (l.id === id) {
          const updatedLeg = { ...l, [field]: value };
          return updatedLeg;
        }
        return l;
      })
    );
  };
  
  // Refresh IV based on current Strike/Time against Model
  const refreshIV = (id: string) => {
      setLegs(legs.map(l => {
          if (l.id === id) {
              return { ...l, iv: getVol(l.strike, l.t) };
          }
          return l;
      }));
  }

  const isFlatMode = volStrategy === 'flat';

  return (
    <div className="bg-white rounded-lg p-6 shadow-md border border-slate-200">
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-bold text-[#000066] flex items-center gap-2">
          <span className="bg-[#000066] w-2 h-6 rounded-sm"></span>
          Strategy Legs
        </h2>
        <button
          onClick={addLeg}
          className="bg-[#000066] hover:bg-[#000088] text-white font-bold px-3 py-1 rounded text-sm flex items-center gap-1 transition shadow-sm"
        >
          <Plus size={16} /> Add Leg
        </button>
      </div>

      <div className="space-y-3">
        {legs.length === 0 && (
          <div className="text-slate-400 text-center py-8 italic border border-dashed border-slate-300 rounded bg-slate-50">
            No legs added. Start building your exotic structure.
          </div>
        )}
        
        {legs.map((leg) => {
          // Calculate Effective IV including Shift and Slope
          const baseIV = isFlatMode ? flatVol : leg.iv;
          
          const moneyness = leg.strike / marketSpot;
          const skewAdjustment = volSlope * (moneyness - 1);
          const effectiveVol = baseIV + volShift + skewAdjustment;

          const stats = calculateBS(marketSpot, leg.strike, leg.t, riskFreeRate, effectiveVol, leg.type);
          const totalLegPrice = stats.price * leg.quantity;
          const isLong = leg.position === PositionType.LONG;
          
          const textColor = isLong ? 'text-green-600' : 'text-[#E20000]';
          
          return (
            <div
              key={leg.id}
              className="flex flex-col gap-3 bg-slate-50 p-3 rounded-lg border border-slate-200 hover:border-blue-300 transition shadow-sm"
            >
              {/* Row 1: Main Config (Position, Qty, Type, Delete) */}
              <div className="flex gap-2 items-center">
                <select
                    value={leg.position}
                    onChange={(e) => updateLeg(leg.id, 'position', e.target.value)}
                    className={`flex-1 bg-white border text-sm rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-[#000066] font-bold uppercase tracking-wider ${
                      leg.position === PositionType.LONG
                        ? 'border-green-600 text-green-700'
                        : 'border-[#E20000] text-[#E20000]'
                    }`}
                  >
                    <option value={PositionType.LONG}>Long</option>
                    <option value={PositionType.SHORT}>Short</option>
                  </select>

                   <div className="relative w-20">
                    <span className="absolute left-2 top-1.5 text-xs text-slate-400 pointer-events-none">x</span>
                    <input
                        type="number"
                        min="0.1"
                        step="0.1"
                        value={leg.quantity}
                        onChange={(e) => updateLeg(leg.id, 'quantity', parseFloat(e.target.value))}
                        className="w-full pl-5 bg-white border border-slate-300 text-slate-800 text-sm rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-[#000066] text-center"
                    />
                  </div>

                   <select
                    value={leg.type}
                    onChange={(e) => updateLeg(leg.id, 'type', e.target.value)}
                    className="w-24 bg-white border border-slate-300 text-slate-800 text-sm rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-[#000066]"
                  >
                    <option value={OptionType.CALL}>Call</option>
                    <option value={OptionType.PUT}>Put</option>
                  </select>

                  <button
                    onClick={() => removeLeg(leg.id)}
                    className="ml-auto text-slate-400 hover:text-[#E20000] transition p-1"
                    title="Remove Leg"
                  >
                    <Trash2 size={18} />
                  </button>
              </div>

              {/* Row 2: Parameters (Strike, Days, IV) */}
              <div className="grid grid-cols-3 gap-2">
                 <div>
                    <label className="text-[10px] uppercase text-slate-500 font-bold block mb-1">Strike %</label>
                    <input
                        type="number"
                        step="1"
                        value={((leg.strike / marketSpot) * 100).toFixed(2)}
                        onChange={(e) => updateLeg(leg.id, 'strike', (parseFloat(e.target.value) / 100) * marketSpot)}
                        className="w-full bg-white border border-slate-300 text-slate-800 text-xs rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-[#000066] font-bold"
                    />
                    <span className="text-[9px] text-slate-400 block text-right mt-0.5">${leg.strike.toFixed(0)}</span>
                 </div>
                 <div>
                    <label className="text-[10px] uppercase text-slate-500 font-bold block mb-1">Days</label>
                    <input
                        type="number"
                        step="1"
                        value={Math.round(leg.t * 365)}
                        onChange={(e) => updateLeg(leg.id, 't', parseFloat(e.target.value) / 365)}
                        className="w-full bg-white border border-slate-300 text-slate-800 text-xs rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-[#000066]"
                    />
                 </div>
                 <div className="relative">
                    <label className="text-[10px] uppercase text-slate-500 font-bold block mb-1 flex justify-between items-center">
                        IV %
                        <div className="flex gap-1">
                            {(volShift !== 0 || volSlope !== 0) && (
                                <span className={`text-[9px] font-mono ${(volShift > 0 || volSlope > 0) ? 'text-green-600' : 'text-[#E20000]'}`}>
                                    {(effectiveVol*100).toFixed(1)}%
                                </span>
                            )}
                            {!isFlatMode && (
                                <button onClick={() => refreshIV(leg.id)} title="Reset to Surface IV" className="hover:text-[#000066]"><RefreshCw size={10}/></button>
                            )}
                            {isFlatMode && <Lock size={8} className="text-slate-400"/>}
                        </div>
                    </label>
                    {isFlatMode ? (
                         <div className="w-full bg-slate-100 border border-slate-200 text-slate-500 text-xs rounded px-2 py-1.5 italic cursor-not-allowed">
                             Flat: {(flatVol * 100).toFixed(1)}%
                         </div>
                    ) : (
                        <input
                            type="number"
                            step="1"
                            value={Math.round(leg.iv * 100)}
                            onChange={(e) => updateLeg(leg.id, 'iv', parseFloat(e.target.value) / 100)}
                            className={`w-full bg-white border text-slate-800 text-xs rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-[#000066] ${(volShift !== 0 || volSlope !== 0) ? 'border-dashed border-slate-400' : 'border-slate-300'}`}
                        />
                    )}
                 </div>
              </div>

              {/* Row 3: Live Readouts */}
              <div className="grid grid-cols-2 gap-x-4 gap-y-2 bg-white p-2 rounded text-xs border border-slate-200">
                 <div className="flex justify-between items-center">
                    <span className="text-slate-500 font-semibold">Value</span>
                    <span className={`font-mono font-bold ${textColor}`}>
                         ${totalLegPrice.toFixed(2)}
                    </span>
                 </div>
                 <div className="flex justify-between items-center">
                    <span className="text-slate-500 font-semibold">Delta</span>
                    <span className={`font-mono ${textColor}`}>
                        {stats.delta.toFixed(3)}
                    </span>
                 </div>
                 <div className="flex justify-between items-center">
                    <span className="text-slate-500 font-semibold">Gamma</span>
                    <span className={`font-mono ${textColor}`}>
                        {stats.gamma.toFixed(4)}
                    </span>
                 </div>
                 <div className="flex justify-between items-center">
                    <span className="text-slate-500 font-semibold">Vega</span>
                    <span className={`font-mono ${textColor}`}>
                        {stats.vega.toFixed(3)}
                    </span>
                 </div>
              </div>

            </div>
          );
        })}
      </div>
    </div>
  );
};

export default StrategyBuilder;