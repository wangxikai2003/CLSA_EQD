import React, { useState, useEffect } from 'react';
import { BarrierProduct, BarrierType, OptionType } from '../types';
import { runMonteCarlo } from '../services/monteCarlo';
import { Play, RotateCcw } from 'lucide-react';

interface Props {
  spot: number;
  r: number;
  vol: number;
}

const BarrierBuilder: React.FC<Props> = ({ spot, r, vol }) => {
  const [product, setProduct] = useState<BarrierProduct>({
    type: OptionType.CALL,
    barrierType: BarrierType.UP_OUT,
    barrierLevel: spot * 1.1,
    strike: spot,
    rebate: 0,
    observationFreq: 252,
    maturity: 1,
    notional: 1000000
  });

  const [result, setResult] = useState<{ price: number, eventProb: number } | null>(null);
  const [simulating, setSimulating] = useState(false);

  const handleSimulate = () => {
    setSimulating(true);
    // Use timeout to allow UI update before heavy calc
    setTimeout(() => {
        const res = runMonteCarlo(product, spot, vol, r, 5000);
        setResult(res);
        setSimulating(false);
    }, 100);
  };

  // Reset barrier defaults when spot changes significantly
  useEffect(() => {
     if(Math.abs(product.barrierLevel - spot) > spot * 0.5) {
         setProduct(p => ({...p, barrierLevel: spot * 1.1, strike: spot}));
     }
  }, [spot]);

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
      {/* Settings */}
      <div className="lg:col-span-4 space-y-4">
        <div className="bg-white p-5 rounded-lg shadow-md border border-slate-200">
            <h3 className="font-bold text-[#000066] mb-4 border-b pb-2">Product Structure</h3>
            
            <div className="space-y-3">
                <div>
                    <label className="block text-xs font-bold text-slate-500 uppercase mb-1">Type</label>
                    <div className="flex gap-2">
                        <button 
                            onClick={() => setProduct({...product, type: OptionType.CALL})}
                            className={`flex-1 py-1.5 text-sm rounded border ${product.type === OptionType.CALL ? 'bg-[#000066] text-white' : 'bg-white'}`}
                        >Call</button>
                         <button 
                            onClick={() => setProduct({...product, type: OptionType.PUT})}
                            className={`flex-1 py-1.5 text-sm rounded border ${product.type === OptionType.PUT ? 'bg-[#000066] text-white' : 'bg-white'}`}
                        >Put</button>
                    </div>
                </div>

                <div>
                    <label className="block text-xs font-bold text-slate-500 uppercase mb-1">Barrier Type</label>
                    <select 
                        value={product.barrierType}
                        onChange={(e) => setProduct({...product, barrierType: e.target.value as BarrierType})}
                        className="w-full border p-2 rounded text-sm"
                    >
                        <option value={BarrierType.UP_OUT}>Up-and-Out (Knock-out)</option>
                        <option value={BarrierType.UP_IN}>Up-and-In (Knock-in)</option>
                        <option value={BarrierType.DOWN_OUT}>Down-and-Out</option>
                        <option value={BarrierType.DOWN_IN}>Down-and-In</option>
                    </select>
                </div>

                 <div className="grid grid-cols-2 gap-3">
                    <div>
                        <label className="block text-xs font-bold text-slate-500 uppercase mb-1">Strike</label>
                        <input type="number" value={product.strike} onChange={(e) => setProduct({...product, strike: parseFloat(e.target.value)})} className="w-full border p-2 rounded text-sm font-mono" />
                    </div>
                     <div>
                        <label className="block text-xs font-bold text-slate-500 uppercase mb-1">Barrier Level</label>
                        <input type="number" value={product.barrierLevel} onChange={(e) => setProduct({...product, barrierLevel: parseFloat(e.target.value)})} className="w-full border p-2 rounded text-sm font-mono text-[#E20000]" />
                    </div>
                 </div>

                 <div className="grid grid-cols-2 gap-3">
                    <div>
                        <label className="block text-xs font-bold text-slate-500 uppercase mb-1">Rebate ($)</label>
                        <input type="number" value={product.rebate} onChange={(e) => setProduct({...product, rebate: parseFloat(e.target.value)})} className="w-full border p-2 rounded text-sm font-mono" />
                    </div>
                     <div>
                        <label className="block text-xs font-bold text-slate-500 uppercase mb-1">Maturity (Yrs)</label>
                        <input type="number" step="0.1" value={product.maturity} onChange={(e) => setProduct({...product, maturity: parseFloat(e.target.value)})} className="w-full border p-2 rounded text-sm font-mono" />
                    </div>
                 </div>
            </div>
        </div>

        <button 
            onClick={handleSimulate}
            disabled={simulating}
            className="w-full bg-[#000066] hover:bg-[#000088] text-white py-3 rounded-lg font-bold shadow-lg flex items-center justify-center gap-2 transition disabled:opacity-50"
        >
            {simulating ? <RotateCcw className="animate-spin" /> : <Play size={20} />}
            {simulating ? 'Pricing...' : 'Run Monte Carlo Simulation'}
        </button>
      </div>

      {/* Visuals & Results */}
      <div className="lg:col-span-8 space-y-6">
        {/* Simplified Path Visualizer Placeholder */}
        <div className="bg-white p-6 rounded-lg shadow-md border border-slate-200 h-[300px] relative overflow-hidden">
             <h3 className="font-bold text-slate-400 absolute top-4 left-4">Payoff Structure</h3>
             <div className="w-full h-full flex items-center justify-center">
                 {/* CSS Drawing of Barrier Logic */}
                 <div className="relative w-3/4 h-3/4 border-l border-b border-slate-300">
                    {/* Spot Line */}
                    <div className="absolute left-0 bottom-1/2 w-full h-0.5 bg-slate-200 border-t border-dashed border-slate-400" title="Current Spot"></div>
                    <span className="absolute -left-8 bottom-1/2 text-xs text-slate-400">{spot}</span>

                    {/* Barrier Line */}
                    <div 
                        className="absolute left-0 w-full h-0.5 bg-red-500 opacity-50" 
                        style={{ bottom: product.barrierLevel > spot ? '80%' : '20%' }}
                        title="Barrier"
                    ></div>
                    <span className="absolute right-0 text-xs text-red-500 font-bold" style={{ bottom: product.barrierLevel > spot ? '82%' : '22%' }}>
                        Barrier: {product.barrierLevel}
                    </span>

                    {/* Payoff Indication */}
                    <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                        <p className="text-4xl font-bold text-slate-100 uppercase tracking-widest">{product.barrierType}</p>
                    </div>
                 </div>
             </div>
        </div>

        {/* Pricing Result */}
        <div className="grid grid-cols-2 gap-4">
             <div className="bg-white p-5 rounded-lg shadow border border-slate-200">
                <span className="block text-xs font-bold text-slate-500 uppercase mb-2">Theoretical Price</span>
                <div className="text-3xl font-mono font-bold text-[#000066]">
                    {result ? `$${result.price.toFixed(2)}` : '---'}
                </div>
                <div className="text-xs text-slate-400 mt-1">
                    {(result && product.notional) ? `Notional: $${(result.price * product.notional).toLocaleString()}` : ''}
                </div>
             </div>

             <div className="bg-white p-5 rounded-lg shadow border border-slate-200">
                <span className="block text-xs font-bold text-slate-500 uppercase mb-2">Hit Probability</span>
                <div className="text-3xl font-mono font-bold text-slate-700">
                    {result ? `${(result.eventProb * 100).toFixed(1)}%` : '---'}
                </div>
                <div className="text-xs text-slate-400 mt-1">
                    {product.barrierType.includes('In') ? 'Knock-In Chance' : 'Knock-Out Chance'}
                </div>
             </div>
        </div>
      </div>
    </div>
  );
};

export default BarrierBuilder;