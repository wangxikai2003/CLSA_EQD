
import React, { useState, useEffect, useMemo, useRef } from 'react';
import { ExoticProduct, ExoticType, VolSurfaceModel, BacktestSummary, HistoricalDataPoint, InterpolationMethod } from '../types';
import { generateExoticAnalysis, priceExoticBackend, USE_LOCAL_MC } from '../services/monteCarlo';
import { generateMockHistory, runRollingBacktest } from '../services/backtestService';
import { Play, TrendingUp, Activity, Settings, ChevronRight, Maximize2, History, Calendar, RotateCcw, RefreshCw } from 'lucide-react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine, ReferenceArea } from 'recharts';
import { GreeksChart } from './Charts';
import { JwParams } from '../services/api';
import JwParamPanel from './JwParamPanel';
import ProgressBar from './ProgressBar';
import { useExoticPricing } from '../hooks/useExoticPricing';
import GreeksTermStructureView from './GreeksTermStructureView';
import {
  abortGreeksTermStructure,
  fetchGreeksTermStructure,
  type GreeksTermStructureResponse,
} from '../services/api_v1';

interface Props {
  spot: number;
  r: number;
  vol: number; // Base vol from global slider (Tab 1)
  volModel?: VolSurfaceModel; // New: Surface Model
  volMethod?: InterpolationMethod;
  onMethodChange?: (m: InterpolationMethod) => void;
  jwBase?: JwParams | null;
  jwDelta?: JwParams;
  onJwDeltaChange?: (d: JwParams) => void;
  barrierFill?: { ko?: number; ki?: number; barrier?: number; strike?: number } | null;
}

const ExoticPricer: React.FC<Props> = ({
  spot, r, vol, volModel, volMethod, onMethodChange,
  jwBase, jwDelta, onJwDeltaChange, barrierFill,
}) => {
  const [productType, setProductType] = useState<ExoticType>(ExoticType.SNOWBALL);
  
  // Params
  const [maturity, setMaturity] = useState(2); // Default 2 years for Snowball
  const [notional, setNotional] = useState(1000000);
  
  // Volatility Control (Local to Tab 2)
  const [pricingVolType, setPricingVolType] = useState<'surface' | 'flat'>('surface');
  const [manualVol, setManualVol] = useState(0.20);

  // Sync manual vol with global prop initially
  useEffect(() => { setManualVol(vol); }, [vol]);

  // Sync barriers from Market Intel T-quote strike pick
  useEffect(() => {
    if (!barrierFill) return;
    if (barrierFill.ko != null) setKoLevel(barrierFill.ko);
    if (barrierFill.ki != null) setKiLevel(barrierFill.ki);
    if (barrierFill.barrier != null) setBarrierPct(barrierFill.barrier);
    if (barrierFill.strike != null) setStrikePct(barrierFill.strike);
  }, [barrierFill]);

  // Catalog-like defaults when switching product type
  useEffect(() => {
    if (productType === ExoticType.FCN) {
      setKoLevel(1.05);
      setStrikePct(0.75);
      setObsFreq('Monthly');
      setMaturity(1);
      setFixedCouponPa(0.15);
      setKiMonitoring('EUROPEAN');
    } else if (productType === ExoticType.SNOWBALL) {
      setKoLevel(1.03);
      setKiLevel(0.75);
      setKiMonitoring('DAILY');
    } else if (productType === ExoticType.SHARKFIN) {
      setStrikePct(1.0);
      setBarrierPct(1.2);
    }
  }, [productType]);

  // Snowball specific
  const [kiLevel, setKiLevel] = useState(0.75); // 75%
  const [koLevel, setKoLevel] = useState(1.03); // 103%
  
  const [koCoupon, setKoCoupon] = useState(0.20);   // KO Coupon
  const [divCoupon, setDivCoupon] = useState(0.20); // Dividend/Bonus Coupon

  const [stepDown, setStepDown] = useState(0.00); // Step down per observation
  const [obsFreq, setObsFreq] = useState<'Monthly'|'Daily'|'Yearly'|'European'>('Monthly');

  // Sharkfin / FCN strike-barrier
  const [strikePct, setStrikePct] = useState(1.0);
  const [barrierPct, setBarrierPct] = useState(1.2);
  const [rebatePct, setRebatePct] = useState(0.02); // 2%
  // FCN (catalog defaults: KO 105%, Strike ~75%, coupon 15% p.a.)
  const [fixedCouponPa, setFixedCouponPa] = useState(0.15);

  // Desk over-hedging & smoothing
  const [deskOpen, setDeskOpen] = useState(false);
  const [barrierShiftBps, setBarrierShiftBps] = useState(0);
  const [smoothingPct, setSmoothingPct] = useState(2.0); // percent → width
  const [showNominal, setShowNominal] = useState(true);
  const [showDesk, setShowDesk] = useState(true);
  const [showPreKI, setShowPreKI] = useState(true);
  const [showPostKI, setShowPostKI] = useState(true);
  const [reserveInfo, setReserveInfo] = useState<{ reserve: number; pv_shifted: number } | null>(null);
  const [kiSpread, setKiSpread] = useState<number | null>(null);
  const priceAbortRef = useRef<AbortController | null>(null);
  const gtsAbortRef = useRef<AbortController | null>(null);
  const [gtsData, setGtsData] = useState<GreeksTermStructureResponse | null>(null);
  const [gtsLoading, setGtsLoading] = useState(false);
  const [kiMonitoring, setKiMonitoring] = useState<'DAILY' | 'EUROPEAN'>('DAILY');
  const { progress, cancel: cancelStream, loading: streamLoading } = useExoticPricing();
  
  // View Control
  const [viewRange, setViewRange] = useState(0.20); // 20% range default

  // Pricing State
  const [simData, setSimData] = useState<any[]>([]);
  const [calculating, setCalculating] = useState(false);
  const [currentResult, setCurrentResult] = useState<any>(null);

  // Backtest State
  const [fullHistory, setFullHistory] = useState<HistoricalDataPoint[]>([]); // Store complete loaded data
  const [backtestHistory, setBacktestHistory] = useState<HistoricalDataPoint[]>([]); // Store filtered visualization data
  const [backtestResult, setBacktestResult] = useState<BacktestSummary | null>(null);
  const [runningBacktest, setRunningBacktest] = useState(false);
  
  // Date Range State
  const [startDate, setStartDate] = useState('2004-12-31');
  const [endDate, setEndDate] = useState(new Date().toISOString().split('T')[0]);

  // Determine effective volatility to use
  const effectiveVol = useMemo(() => {
      if (pricingVolType === 'surface' && volModel) {
          return volModel.predict(spot, maturity);
      }
      return manualVol;
  }, [pricingVolType, volModel, spot, maturity, manualVol]);

  const product: ExoticProduct = useMemo(() => ({
    type: productType,
    spotRef: spot,
    maturity,
    vol: effectiveVol, // Use Local Effective Vol
    r,
    notional,
    kiBarrier: productType === ExoticType.FCN ? strikePct : kiLevel,
    koBarrier: productType === ExoticType.FCN ? koLevel : koLevel,
    koStepDown: stepDown,
    obsFreq: obsFreq,
    
    // Coupons
    koCoupon: koCoupon,
    dividendCoupon: divCoupon,
    fixedCouponPa,

    strike: strikePct,
    barrier: productType === ExoticType.FCN ? koLevel : barrierPct,
    rebate: rebatePct,
  }), [productType, spot, maturity, effectiveVol, r, notional, kiLevel, koLevel, koCoupon, divCoupon, stepDown, obsFreq, strikePct, barrierPct, rebatePct, fixedCouponPa]);

  // Greeks term-structure (AbortController + debounce)
  useEffect(() => {
    if (productType === ExoticType.SHARKFIN) {
      setGtsData(null);
      return;
    }
    gtsAbortRef.current?.abort();
    abortGreeksTermStructure();
    const ctrl = new AbortController();
    gtsAbortRef.current = ctrl;
    const timer = window.setTimeout(async () => {
      setGtsLoading(true);
      try {
        const ptype = productType === ExoticType.FCN ? 'fcn' : 'snowball';
        const res = await fetchGreeksTermStructure(
          {
            product_type: ptype,
            product: {
              tenor: maturity,
              ko_barrier: koLevel,
              ki_barrier: productType === ExoticType.FCN ? strikePct : kiLevel,
              strike: productType === ExoticType.FCN ? strikePct : 1.0,
              coupon: productType === ExoticType.FCN ? fixedCouponPa : koCoupon,
              bonus_coupon: divCoupon,
              step_down: stepDown,
              obs_freq: obsFreq.toLowerCase(),
              fixed_coupon_pa: fixedCouponPa,
              snowball_coupon_pa: koCoupon,
            },
            market: {
              spots: [spot],
              r,
              q: [0],
              flat_vols: [effectiveVol],
              vol_mode: 'flat',
            },
            spot_range: [spot * (1 - viewRange * 1.5), spot * (1 + viewRange * 1.5)],
            spot_steps: 41,
            ki_monitoring_type: kiMonitoring,
            ko_monitoring_type: 'EUROPEAN',
            include_greeks: ['pv', 'delta', 'gamma'],
            n_paths: 4000,
            barrier_shift_bps: barrierShiftBps,
            spread_smoothing_width: smoothingPct / 100,
          },
          ctrl.signal,
        );
        if (!ctrl.signal.aborted) setGtsData(res);
      } catch (e) {
        if ((e as Error)?.name === 'AbortError') return;
        console.warn('greeks-term-structure', e);
      } finally {
        if (!ctrl.signal.aborted) setGtsLoading(false);
      }
    }, 280);
    return () => {
      window.clearTimeout(timer);
      ctrl.abort();
    };
  }, [
    productType, maturity, koLevel, kiLevel, strikePct, fixedCouponPa, koCoupon, divCoupon,
    stepDown, obsFreq, spot, r, effectiveVol, viewRange, kiMonitoring, barrierShiftBps, smoothingPct,
  ]);

  // Initial Calculation & Backtest Data Gen
  useEffect(() => {
     handleCalculate();
     // Load full history once
     const history = generateMockHistory(); 
     setFullHistory(history);
     
     // Set default start/end based on data if available
     if(history.length > 0) {
         const dataStart = history[0].date;
         if (startDate < dataStart) {
             setStartDate(dataStart);
         }
         // Ensure end date doesn't exceed history
         const lastDate = history[history.length - 1].date;
         if (endDate > lastDate) {
             setEndDate(lastDate);
         }
     }
  }, []);

  // Run backtest initially when history is loaded (Auto-run for convenience)
  // Re-run if product type changes
  useEffect(() => {
      if (fullHistory.length > 0 && !backtestResult) {
          runBacktest();
      }
  }, [fullHistory]); // Run once on load

  const handleCalculate = () => {
      // Abort any in-flight request (race-condition guard)
      priceAbortRef.current?.abort();
      cancelStream();
      const ctrl = new AbortController();
      priceAbortRef.current = ctrl;
      setCalculating(true);
      const desk = {
        barrier_shift_bps: barrierShiftBps,
        spread_smoothing_width: smoothingPct / 100,
        return_profiles: barrierShiftBps > 0 || smoothingPct > 0,
        n_paths: barrierShiftBps > 0 ? 50000 : 15000,
      };

      (async () => {
          try {
            let data = generateExoticAnalysis(product, spot, viewRange);
            let center = data.reduce((prev, curr) => Math.abs(curr.spot - spot) < Math.abs(prev.spot - spot) ? curr : prev);
            setReserveInfo(null);
            setKiSpread(null);
            if (!USE_LOCAL_MC) {
              try {
                let raw: import('../services/api_v1').ExoticPriceResponse | undefined;
                let px: number;
                const out = await priceExoticBackend(product, spot, effectiveVol, ctrl.signal, {
                  ...desk,
                  n_paths: desk.n_paths,
                });
                px = out.price;
                raw = out.raw;
                if (ctrl.signal.aborted) return;
                center = { ...center, price: px };

                // Prefer pre/post KI profiles for Greeks charts
                if (raw?.pre_ki_profile && raw?.post_ki_profile) {
                  const pre = raw.pre_ki_profile;
                  const post = raw.post_ki_profile;
                  const n = Math.min(pre.spot_mult.length, post.spot_mult.length);
                  data = Array.from({ length: n }, (_, i) => {
                    const m = pre.spot_mult[i];
                    const row: any = {
                      moneyness: m * 100,
                      spot: spot * m,
                      price: (raw!.raw_profile?.pv?.[i] ?? pre.pv[i]) * notional,
                      price_pre_ki: pre.pv[i] * notional,
                      price_post_ki: post.pv[i] * notional,
                      delta: pre.delta[i] * notional,
                      delta_pre_ki: pre.delta[i] * notional,
                      delta_post_ki: post.delta[i] * notional,
                      gamma: pre.gamma[i] * notional,
                      gamma_pre_ki: pre.gamma[i] * notional,
                      gamma_post_ki: post.gamma[i] * notional,
                      vega: 0,
                    };
                    if (raw!.shifted_profile) {
                      row.price_desk = raw!.shifted_profile.pv[i] * notional;
                      row.delta_desk = raw!.shifted_profile.delta[i] * notional;
                      row.gamma_desk = raw!.shifted_profile.gamma[i] * notional;
                    }
                    return row;
                  });
                  if (raw.diagnostics?.ki_spread != null) {
                    setKiSpread(Number(raw.diagnostics.ki_spread) * notional);
                  }
                } else if (raw?.raw_profile && raw?.shifted_profile) {
                  const n = Math.min(raw.raw_profile.spot_mult.length, raw.shifted_profile.spot_mult.length);
                  data = Array.from({ length: n }, (_, i) => {
                    const m = raw!.raw_profile!.spot_mult[i];
                    return {
                      moneyness: m * 100,
                      spot: spot * m,
                      price: raw!.raw_profile!.pv[i] * notional,
                      price_desk: raw!.shifted_profile!.pv[i] * notional,
                      delta: raw!.raw_profile!.delta[i] * notional,
                      delta_desk: raw!.shifted_profile!.delta[i] * notional,
                      gamma: raw!.raw_profile!.gamma[i] * notional,
                      gamma_desk: raw!.shifted_profile!.gamma[i] * notional,
                      vega: 0,
                    };
                  });
                }
                if (raw?.reserve_metrics) {
                  setReserveInfo({
                    reserve: raw.reserve_metrics.reserve * notional,
                    pv_shifted: raw.reserve_metrics.pv_shifted * notional,
                  });
                }
              } catch (e) {
                if ((e as Error)?.name === 'AbortError') return;
                console.warn('backend pricing fallback to local MC', e);
              }
            }
            if (ctrl.signal.aborted) return;
            setSimData(data);
            setCurrentResult(center);
          } finally {
            if (!ctrl.signal.aborted) setCalculating(false);
          }
      })();
  };

  const runBacktest = () => {
      setRunningBacktest(true);
      setTimeout(() => {
        // Pass the full history and let the service filter based on dates
        const { summary, historyWithLosses } = runRollingBacktest(fullHistory, product, startDate, endDate);
        setBacktestResult(summary);
        setBacktestHistory(historyWithLosses);
        setRunningBacktest(false);
      }, 50);
  };

  // Colors
  const COLORS = {
      CLSA_BLUE: '#000066',
      CLSA_YELLOW: '#EAB308', 
      CITIC_RED: '#E20000' 
  };

  return (
    <div className="flex flex-col space-y-8 pb-10">
        
        {/* --- SECTION 1: PRICING ENGINE --- */}
        <div className="bg-white rounded-lg border border-slate-200 shadow-sm overflow-hidden">
            <div className="bg-slate-50 px-6 py-3 border-b border-slate-200 flex justify-between items-center">
                <h3 className="font-bold text-[#000066] flex items-center gap-2">
                    <Activity size={18} /> Structure Pricing & Risk
                </h3>
                <span className="text-xs font-mono text-slate-400">Monte Carlo Simulation</span>
            </div>
            
            <div className="p-6 grid grid-cols-1 lg:grid-cols-12 gap-8">
                {/* Left: Inputs */}
                <div className="lg:col-span-3 space-y-6">
                     <div className="space-y-4 text-sm">
                        <div>
                            <label className="block text-xs font-bold text-slate-500 uppercase mb-1">Product Type</label>
                            <select 
                                value={productType} 
                                onChange={(e) => setProductType(e.target.value as ExoticType)}
                                className="w-full border border-slate-300 rounded p-2 text-slate-700 font-semibold focus:ring-2 focus:ring-[#000066] focus:outline-none"
                            >
                                <option value={ExoticType.SNOWBALL}>Snowball (Autocall)</option>
                                <option value={ExoticType.FCN}>FCN (Fixed Coupon Note)</option>
                                <option value={ExoticType.SHARKFIN}>Sharkfin (Barrier Call)</option>
                            </select>
                        </div>
                        
                        {/* Volatility Section */}
                        <div className="bg-blue-50 p-2 rounded border border-blue-100 mb-2">
                            <div className="flex justify-between items-center mb-2">
                                <span className="text-[10px] text-blue-800 uppercase font-bold flex items-center gap-1"><RefreshCw size={10}/> Vol Model</span>
                                <div className="flex bg-white rounded p-0.5 border border-blue-100">
                                    <button 
                                        onClick={() => setPricingVolType('flat')}
                                        className={`px-2 py-0.5 text-[9px] font-bold rounded transition ${pricingVolType === 'flat' ? 'bg-[#000066] text-white shadow-sm' : 'text-slate-400 hover:text-slate-600'}`}
                                    >Flat</button>
                                    <button 
                                        onClick={() => setPricingVolType('surface')}
                                        className={`px-2 py-0.5 text-[9px] font-bold rounded transition ${pricingVolType === 'surface' ? 'bg-[#000066] text-white shadow-sm' : 'text-slate-400 hover:text-slate-600'}`}
                                    >Surface</button>
                                </div>
                            </div>

                            {pricingVolType === 'flat' ? (
                                <div>
                                    <div className="flex justify-between text-xs mb-1">
                                        <span className="text-blue-800 text-[10px] font-bold uppercase">Manual Vol</span>
                                        <span className="font-mono font-bold text-[#000066]">{(manualVol * 100).toFixed(1)}%</span>
                                    </div>
                                    <input 
                                        type="range" min="0.05" max="0.6" step="0.005" 
                                        value={manualVol} onChange={(e) => setManualVol(parseFloat(e.target.value))}
                                        className="w-full accent-[#000066] h-1.5 bg-blue-200 rounded-lg appearance-none cursor-pointer"
                                    />
                                </div>
                            ) : (
                                <>
                                    <div className="flex justify-between items-center">
                                        <span className="text-[10px] text-blue-800 uppercase font-bold">ATM Vol</span>
                                        <span className="font-mono font-bold text-[#000066] text-sm">{(effectiveVol * 100).toFixed(2)}%</span>
                                    </div>
                                    {/* Surface Selector */}
                                    {onMethodChange && volMethod && (
                                        <div className="flex bg-white/50 p-0.5 rounded mt-2">
                                            {(['JW SVI', 'Cubic Spline', 'Local Volatility', 'Stochastic Volatility'] as InterpolationMethod[]).map(m => {
                                                const activeClass = m === 'JW SVI' || m === 'Local Volatility'
                                                    ? 'bg-[#EAB308] text-white shadow-sm'
                                                    : m === 'Stochastic Volatility' 
                                                    ? 'bg-[#E20000] text-white shadow-sm' 
                                                    : 'bg-[#000066] text-white shadow-sm';
                                                const short = m === 'JW SVI' ? 'JW' : m === 'Cubic Spline' ? 'Cubic' : m === 'Local Volatility' ? 'Local' : 'Stoch';
                                                
                                                return (
                                                    <button 
                                                        key={m}
                                                        onClick={() => onMethodChange(m)}
                                                        className={`flex-1 py-1 text-[8px] font-bold rounded transition text-center leading-tight ${volMethod === m ? activeClass : 'text-slate-400 hover:text-slate-600'}`}
                                                    >
                                                        {short}
                                                    </button>
                                                )
                                            })}
                                        </div>
                                    )}
                                    {volMethod === 'JW SVI' && jwDelta && onJwDeltaChange && (
                                      <div className="mt-2">
                                        <JwParamPanel base={jwBase} delta={jwDelta} onChange={onJwDeltaChange} compact />
                                      </div>
                                    )}
                                </>
                            )}
                        </div>

                        {productType === ExoticType.SNOWBALL && (
                            <>
                                <div className="grid grid-cols-2 gap-3">
                                    <div>
                                        <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Knock-Out (%)</label>
                                        {/* Changed value text color to green-600 */}
                                        <input type="number" step="0.01" value={koLevel} onChange={e => setKoLevel(parseFloat(e.target.value))} className="w-full border p-1.5 rounded focus:outline-none focus:border-[#000066] text-green-600 font-bold" />
                                    </div>
                                    <div>
                                        <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Knock-In (%)</label>
                                        <input type="number" step="0.01" value={kiLevel} onChange={e => setKiLevel(parseFloat(e.target.value))} className="w-full border p-1.5 rounded text-[#E20000] focus:outline-none focus:border-[#E20000]" />
                                    </div>
                                </div>
                                <div className="grid grid-cols-2 gap-3">
                                     <div>
                                        <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">KO Step Down</label>
                                        <input 
                                            type="number" step="0.005" 
                                            value={stepDown} 
                                            onChange={e => setStepDown(parseFloat(e.target.value))} 
                                            className="w-full border p-1.5 rounded text-slate-700 focus:outline-none focus:border-[#000066]" 
                                            placeholder="0.00"
                                        />
                                    </div>
                                    <div>
                                        <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Obs Freq</label>
                                        <select 
                                            value={obsFreq}
                                            onChange={(e) => setObsFreq(e.target.value as any)}
                                            className="w-full border p-1.5 rounded text-xs"
                                        >
                                            <option value="Daily">Daily</option>
                                            <option value="Monthly">Monthly</option>
                                            <option value="Yearly">Yearly</option>
                                            <option value="European">European (End)</option>
                                        </select>
                                    </div>
                                </div>
                                
                                <div className="space-y-3 pt-2 border-t border-slate-100">
                                    <div>
                                        <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">KO Coupon (%)</label>
                                        <input type="number" step="0.01" value={koCoupon} onChange={e => setKoCoupon(parseFloat(e.target.value))} className="w-full border p-1.5 rounded text-green-600 font-bold focus:outline-none focus:border-green-600" />
                                        <span className="text-[9px] text-slate-400">Paid if Knocked Out</span>
                                    </div>
                                    <div>
                                        <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Bonus Coupon (%)</label>
                                        <input type="number" step="0.01" value={divCoupon} onChange={e => setDivCoupon(parseFloat(e.target.value))} className="w-full border p-1.5 rounded text-[#EAB308] font-bold focus:outline-none focus:border-[#EAB308]" />
                                        <span className="text-[9px] text-slate-400">Paid at Maturity (No KI/KO)</span>
                                    </div>
                                </div>
                            </>
                        )}

                        {productType === ExoticType.FCN && (
                            <>
                                <div className="text-[9px] text-slate-400 mb-1">
                                  Catalog: fixed coupon each obs until KO; else Min(100%, Final/Strike) at maturity
                                </div>
                                <div className="grid grid-cols-2 gap-3">
                                    <div>
                                        <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">KO Barrier</label>
                                        <input type="number" step="0.01" value={koLevel} onChange={e => setKoLevel(parseFloat(e.target.value))} className="w-full border p-1.5 rounded text-green-600 font-bold" />
                                    </div>
                                    <div>
                                        <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Strike (Put)</label>
                                        <input type="number" step="0.01" value={strikePct} onChange={e => setStrikePct(parseFloat(e.target.value))} className="w-full border p-1.5 rounded text-[#E20000] font-bold" />
                                    </div>
                                </div>
                                <div className="grid grid-cols-2 gap-3">
                                    <div>
                                        <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Fixed Coupon p.a.</label>
                                        <input type="number" step="0.01" value={fixedCouponPa} onChange={e => setFixedCouponPa(parseFloat(e.target.value))} className="w-full border p-1.5 rounded text-[#EAB308] font-bold" />
                                    </div>
                                    <div>
                                        <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Obs Freq</label>
                                        <select
                                            value={obsFreq}
                                            onChange={(e) => setObsFreq(e.target.value as any)}
                                            className="w-full border p-1.5 rounded text-xs"
                                        >
                                            <option value="Daily">Daily</option>
                                            <option value="Monthly">Monthly</option>
                                            <option value="Yearly">Yearly</option>
                                            <option value="European">European (End)</option>
                                        </select>
                                    </div>
                                </div>
                            </>
                        )}

                        {productType === ExoticType.SHARKFIN && (
                            <>
                                <div className="grid grid-cols-2 gap-3">
                                    <div>
                                        <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Strike (%)</label>
                                        <input type="number" step="0.01" value={strikePct} onChange={e => setStrikePct(parseFloat(e.target.value))} className="w-full border p-1.5 rounded" />
                                    </div>
                                    <div>
                                        <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Barrier (%)</label>
                                        <input type="number" step="0.01" value={barrierPct} onChange={e => setBarrierPct(parseFloat(e.target.value))} className="w-full border p-1.5 rounded text-[#E20000]" />
                                    </div>
                                </div>
                                <div>
                                    <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Rebate (%)</label>
                                    <input type="number" step="0.01" value={rebatePct} onChange={e => setRebatePct(parseFloat(e.target.value))} className="w-full border p-1.5 rounded" />
                                </div>
                            </>
                        )}

                        <div className="grid grid-cols-2 gap-3 mt-4">
                            <div>
                                <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Maturity (Yr)</label>
                                <input type="number" step="0.1" value={maturity} onChange={e => setMaturity(parseFloat(e.target.value))} className="w-full border p-1.5 rounded" />
                            </div>
                            <div>
                                <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Notional ($)</label>
                                <input type="number" step="10000" value={notional} onChange={e => setNotional(parseFloat(e.target.value))} className="w-full border p-1.5 rounded" />
                            </div>
                        </div>

                        {(productType === ExoticType.SNOWBALL || productType === ExoticType.FCN) && (
                          <div className="mt-3">
                            <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">KI Monitoring</label>
                            <select
                              value={kiMonitoring}
                              onChange={(e) => setKiMonitoring(e.target.value as 'DAILY' | 'EUROPEAN')}
                              className="w-full border p-1.5 rounded text-xs"
                            >
                              <option value="DAILY">Daily / American (AKI)</option>
                              <option value="EUROPEAN">European / AXKI</option>
                            </select>
                          </div>
                        )}

                        <div className="mt-4 border border-slate-200 rounded-lg overflow-hidden">
                            <button
                                type="button"
                                onClick={() => setDeskOpen(!deskOpen)}
                                className="w-full flex items-center justify-between px-3 py-2 bg-slate-50 text-[11px] font-bold text-[#000066]"
                            >
                                <span>Desk Over-hedging & Smoothing</span>
                                <ChevronRight size={14} className={deskOpen ? 'rotate-90 transition' : 'transition'} />
                            </button>
                            {deskOpen && (
                                <div className="p-3 space-y-3 text-[10px]">
                                    <div>
                                        <div className="flex justify-between mb-1">
                                            <span className="font-bold text-slate-500 uppercase">Barrier Shift (bps)</span>
                                            <span className="font-mono">{barrierShiftBps}</span>
                                        </div>
                                        <input
                                            type="range" min={0} max={200} step={5}
                                            value={barrierShiftBps}
                                            onChange={(e) => setBarrierShiftBps(Number(e.target.value))}
                                            className="w-full accent-[#000066]"
                                        />
                                    </div>
                                    <div>
                                        <div className="flex justify-between mb-1">
                                            <span className="font-bold text-slate-500 uppercase">Smoothing Spread (%)</span>
                                            <span className="font-mono">{smoothingPct.toFixed(1)}</span>
                                        </div>
                                        <input
                                            type="range" min={0.5} max={5} step={0.1}
                                            value={smoothingPct}
                                            onChange={(e) => setSmoothingPct(Number(e.target.value))}
                                            className="w-full accent-[#EAB308]"
                                        />
                                    </div>
                                    <div className="flex flex-wrap gap-3 text-slate-600">
                                        <label className="flex items-center gap-1">
                                            <input type="checkbox" checked={showPreKI} onChange={(e) => setShowPreKI(e.target.checked)} />
                                            Pre-KI Greeks
                                        </label>
                                        <label className="flex items-center gap-1">
                                            <input type="checkbox" checked={showPostKI} onChange={(e) => setShowPostKI(e.target.checked)} />
                                            Post-KI Greeks
                                        </label>
                                        <label className="flex items-center gap-1">
                                            <input type="checkbox" checked={showNominal} onChange={(e) => setShowNominal(e.target.checked)} />
                                            Contract Nominal
                                        </label>
                                        <label className="flex items-center gap-1">
                                            <input type="checkbox" checked={showDesk} onChange={(e) => setShowDesk(e.target.checked)} />
                                            Desk Shifted
                                        </label>
                                    </div>
                                    {kiSpread != null && (
                                        <div className="bg-slate-50 border border-slate-100 rounded px-2 py-1 font-mono text-[10px]">
                                            Pre−Post KI spread ${kiSpread.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                                        </div>
                                    )}
                                    {reserveInfo && (
                                        <div className="bg-amber-50 border border-amber-100 rounded px-2 py-1 font-mono text-[10px]">
                                            Reserve ${(reserveInfo.reserve).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                                            <span className="text-slate-400"> · shifted PV ${(reserveInfo.pv_shifted).toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>

                        <button 
                            onClick={handleCalculate}
                            disabled={calculating}
                            className="w-full bg-[#000066] hover:bg-[#000088] text-white py-2 rounded font-bold mt-4 flex justify-center items-center gap-2 transition shadow-sm"
                        >
                            {calculating ? 'Simulating...' : <><Play size={16}/> Re-Price</>}
                        </button>
                    </div>

                    <div className="pt-4 border-t border-slate-100">
                        <div className="flex justify-between text-xs mb-1 font-bold text-slate-500 uppercase">
                            <span>Analysis Range</span>
                            <span>{(viewRange * 100).toFixed(0)}%</span>
                        </div>
                        <input 
                            type="range" min="0.05" max="0.5" step="0.05" 
                            value={viewRange} onChange={(e) => setViewRange(parseFloat(e.target.value))}
                            className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none"
                        />
                    </div>
                </div>

                {/* Center: Charts */}
                <div className="lg:col-span-6 space-y-6">
                    {(calculating || streamLoading) && progress && (
                      <ProgressBar
                        progress={progress.progress}
                        currentPaths={progress.currentPaths}
                        totalPaths={progress.totalPaths}
                        runningPrice={progress.runningPrice}
                        stdError={progress.stdError}
                        stage={progress.stage}
                        history={progress.history}
                        onCancel={() => {
                          cancelStream();
                          priceAbortRef.current?.abort();
                          setCalculating(false);
                        }}
                      />
                    )}
                    <div className="h-[300px] w-full">
                        <h4 className="text-xs font-bold text-slate-400 mb-2 uppercase flex items-center gap-2">Payoff Profile</h4>
                        <ResponsiveContainer width="100%" height="90%">
                            <AreaChart data={simData}>
                                <CartesianGrid strokeDasharray="3 3" opacity={0.5} />
                                <XAxis 
                                    dataKey="moneyness" 
                                    type="number" 
                                    domain={['auto', 'auto']} 
                                    tickFormatter={v => `${v.toFixed(0)}%`} 
                                    tick={{fontSize: 10}}
                                />
                                {/* Updated YAxis to Percent of Notional */}
                                <YAxis 
                                    domain={['auto', 'auto']} 
                                    tickFormatter={v => `${((v / notional) * 100).toFixed(1)}%`} 
                                    tick={{fontSize: 10}} 
                                    width={40} 
                                />
                                <Tooltip 
                                    labelFormatter={v => {
                                        const s = (Number(v) / 100) * spot;
                                        return `Moneyness: ${Number(v).toFixed(0)}% (Spot: ${s.toFixed(2)})`;
                                    }} 
                                    // Updated Tooltip to Percent of Notional
                                    formatter={v => [
                                        `${((Number(v) / notional) * 100).toFixed(2)}% ($${Number(v).toLocaleString(undefined, {maximumFractionDigits: 0})})`,
                                        'Price'
                                    ]} 
                                    contentStyle={{fontSize: '12px'}}
                                />
                                <ReferenceLine x={100} stroke="#EAB308" strokeDasharray="3 3" />
                                {showNominal && (
                                  <Area type="monotone" dataKey="price" stroke="#000066" fill="#000066" fillOpacity={0.1} name="Blended / Nominal" />
                                )}
                                {showPreKI && simData.some((d) => d.price_pre_ki != null) && (
                                  <Area type="monotone" dataKey="price_pre_ki" stroke="#16a34a" fill="#16a34a" fillOpacity={0.06} name="Pre-KI" />
                                )}
                                {showPostKI && simData.some((d) => d.price_post_ki != null) && (
                                  <Area type="monotone" dataKey="price_post_ki" stroke="#E20000" fill="#E20000" fillOpacity={0.06} name="Post-KI" />
                                )}
                                {showDesk && simData.some((d) => d.price_desk != null) && (
                                  <Area type="monotone" dataKey="price_desk" stroke="#EAB308" fill="#EAB308" fillOpacity={0.08} name="Desk Shifted" />
                                )}
                            </AreaChart>
                        </ResponsiveContainer>
                    </div>
                    
                    <div className="grid grid-cols-2 gap-4 h-[200px]">
                        <div className="bg-white rounded border border-slate-100 p-2">
                            <h4 className="text-[10px] font-bold mb-1 text-center" style={{color: COLORS.CITIC_RED}}>
                              Delta · Pre-KI vs Post-KI
                            </h4>
                            <GreeksChart
                              data={simData}
                              marketSpot={spot}
                              dataKey={simData.some((d) => d.delta_pre_ki != null) ? 'delta_pre_ki' : 'delta'}
                              color="#16a34a"
                              name="Pre-KI"
                              altDataKey={simData.some((d) => d.delta_post_ki != null) ? 'delta_post_ki' : (showDesk ? 'delta_desk' : undefined)}
                              altColor="#E20000"
                              altName="Post-KI"
                              showPrimary={showPreKI || showNominal}
                              showAlt={showPostKI || showDesk}
                              yScale={1.1}
                            />
                        </div>
                        <div className="bg-white rounded border border-slate-100 p-2">
                            <h4 className="text-[10px] font-bold mb-1 text-center text-purple-700">
                              Gamma · Pre-KI vs Post-KI
                            </h4>
                            <GreeksChart
                              data={simData}
                              marketSpot={spot}
                              dataKey={simData.some((d) => d.gamma_pre_ki != null) ? 'gamma_pre_ki' : 'gamma'}
                              color="#16a34a"
                              name="Pre-KI"
                              altDataKey={simData.some((d) => d.gamma_post_ki != null) ? 'gamma_post_ki' : (showDesk ? 'gamma_desk' : undefined)}
                              altColor="#E20000"
                              altName="Post-KI"
                              showPrimary={showPreKI || showNominal}
                              showAlt={showPostKI || showDesk}
                              yScale={1.1}
                            />
                        </div>
                    </div>

                    {(productType === ExoticType.SNOWBALL || productType === ExoticType.FCN) && (
                      <GreeksTermStructureView
                        data={gtsData}
                        loading={gtsLoading}
                        spot={spot}
                      />
                    )}
                </div>

                {/* Right: Summary */}
                <div className="lg:col-span-3 space-y-4">
                     <div className="text-center p-4 bg-slate-50 rounded border border-slate-200">
                         <h3 className="text-slate-400 font-bold uppercase text-[10px] mb-1">Theoretical Value</h3>
                         <div className="text-3xl font-mono font-bold text-[#000066]">
                             {currentResult ? `$${currentResult.price.toLocaleString(undefined, {maximumFractionDigits: 0})}` : '---'}
                         </div>
                     </div>

                     {/* Removed Fair Coupon Block */}

                     <div>
                         <h3 className="font-bold text-[#000066] text-sm mb-3 border-b pb-1">Spot Greeks</h3>
                         <div className="space-y-3 text-sm">
                             <div className="flex justify-between items-center">
                                 <span className="text-slate-500">Delta</span>
                                 <span className="font-mono font-bold" style={{color: COLORS.CITIC_RED}}>{currentResult ? currentResult.delta.toFixed(4) : '-'}</span>
                             </div>
                             <div className="flex justify-between items-center">
                                 <span className="text-slate-500">Gamma</span>
                                 <span className="font-mono font-bold" style={{color: COLORS.CLSA_BLUE}}>{currentResult ? (currentResult.gamma * spot).toFixed(4) : '-'} <span className="text-[10px] text-slate-400 font-normal">(1%)</span></span>
                             </div>
                             <div className="flex justify-between items-center">
                                 <span className="text-slate-500">Vega</span>
                                 <span className="font-mono font-bold" style={{color: COLORS.CLSA_BLUE}}>{currentResult ? currentResult.vega.toFixed(2) : '-'}</span>
                             </div>
                             <div className="flex justify-between items-center">
                                 <span className="text-slate-500">Theta</span>
                                 <span className="font-mono font-bold" style={{color: COLORS.CLSA_YELLOW}}>{currentResult ? currentResult.theta.toFixed(2) : '-'}</span>
                             </div>
                         </div>
                     </div>
                     
                     <div className="text-[10px] text-slate-400 leading-relaxed">
                        Price derived via Finite Difference Monte Carlo using {pricingVolType} volatility.
                     </div>
                </div>
            </div>
        </div>
        
        {/* --- SECTION 2: HISTORICAL BACKTEST --- */}
        <div className="bg-white rounded-lg border border-slate-200 shadow-sm overflow-hidden">
            {/* Updated Header with Clean Corporate Scheme (Matching Pricing Section) */}
            <div className="bg-slate-50 px-6 py-4 border-b border-slate-200 flex flex-col xl:flex-row justify-between items-center gap-4">
                {/* Left: Title */}
                <div className="flex items-center gap-3">
                        <h3 className="font-bold text-[#000066] flex items-center gap-2 text-lg">
                            <History size={20} /> Historical Backtest Analysis
                        </h3>
                        <span className="hidden sm:inline-block h-4 w-px bg-slate-300"></span>
                        <span className="hidden sm:inline-block text-xs font-mono text-slate-400">
                            Rolling S&P 500
                        </span>
                </div>
                
                {/* Middle: Controls */}
                <div className="flex flex-wrap items-center justify-center gap-3">
                    <div className="flex items-center gap-2 bg-white border border-slate-200 p-1 rounded-md shadow-sm">
                        <div className="flex items-center gap-1 px-2 py-1 rounded text-xs font-bold">
                            <span className="text-slate-400 uppercase text-[10px]">Start</span>
                            <input 
                                type="date" 
                                value={startDate} 
                                onChange={(e) => setStartDate(e.target.value)}
                                className="bg-transparent text-[#000066] focus:outline-none w-24 font-mono"
                            />
                        </div>
                        <span className="text-slate-300"><ChevronRight size={14}/></span>
                        <div className="flex items-center gap-1 px-2 py-1 rounded text-xs font-bold">
                            <span className="text-slate-400 uppercase text-[10px]">End</span>
                            <input 
                                type="date" 
                                value={endDate} 
                                onChange={(e) => setEndDate(e.target.value)}
                                className="bg-transparent text-[#000066] focus:outline-none w-24 font-mono"
                            />
                        </div>
                    </div>

                    <button 
                        onClick={runBacktest}
                        disabled={runningBacktest}
                        className="bg-[#000066] hover:bg-[#000088] text-white px-4 py-1.5 rounded-md font-bold text-xs shadow-sm flex items-center gap-2 transition disabled:opacity-50"
                    >
                            {runningBacktest ? <RotateCcw className="animate-spin" size={14}/> : <Play size={14} fill="currentColor"/>}
                            Run Analysis
                    </button>
                </div>

                {/* Right: Win Rate */}
                <div className="text-right flex items-center gap-3 xl:border-l border-slate-200 xl:pl-4">
                        <div className="flex flex-col items-end">
                            <div className="text-[10px] font-bold uppercase text-slate-400">Win Rate</div>
                            <div className="text-2xl font-bold text-[#000066]">{(backtestResult?.winRate ? backtestResult.winRate * 100 : 0).toFixed(2)}%</div>
                        </div>
                </div>
            </div>

            <div className="p-6 grid grid-cols-1 xl:grid-cols-12 gap-8">
                {/* History Chart */}
                <div className="xl:col-span-8">
                    {/* Wrapper for Chart to allow Legend to flow naturally below it without height constraint issues */}
                        <div className="h-[400px] w-full">
                        <ResponsiveContainer width="100%" height="100%">
                            <AreaChart data={backtestHistory} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                                <defs>
                                    <linearGradient id="colorPrice" x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="5%" stopColor="#000066" stopOpacity={0.1}/>
                                    <stop offset="95%" stopColor="#000066" stopOpacity={0}/>
                                    </linearGradient>
                                </defs>
                                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                                <XAxis dataKey="date" minTickGap={50} tick={{fontSize: 10}} stroke="#94a3b8" />
                                <YAxis domain={['auto', 'auto']} stroke="#94a3b8" tick={{fontSize: 10}} width={40}/>
                                <Tooltip 
                                    contentStyle={{fontSize: '12px'}}
                                    labelFormatter={(label) => `Date: ${label}`}
                                />
                                <Area type="monotone" dataKey="price" stroke="#000066" strokeWidth={1.5} fill="url(#colorPrice)" activeDot={{r: 4}} />
                                
                                {/* Highlight Loss Areas (RED) */}
                                {backtestHistory.map((pt, idx) => {
                                    if (pt.isLossEvent) {
                                        // Draw area for loss periods
                                        return <ReferenceArea key={idx} x1={pt.date} x2={backtestHistory[Math.min(idx+1, backtestHistory.length-1)].date} fill="#E20000" opacity={0.5} strokeOpacity={0} />
                                    }
                                    return null;
                                })}
                            </AreaChart>
                        </ResponsiveContainer>
                        </div>
                    
                    {/* Legend with Full Width and Wrapping */}
                    <div className="w-full flex flex-wrap justify-center gap-x-6 gap-y-2 mt-4 text-xs font-bold text-center">
                            <div className="flex items-center gap-2">
                                <span className="w-3 h-3 bg-[#000066] rounded-full"></span>
                                <span>S&P 500 Index</span>
                            </div>
                            <div className="flex items-center gap-2">
                                {/* Changed to Red */}
                                <span className="w-3 h-3 bg-[#E20000] opacity-50 rounded-sm"></span>
                                <span>Entry Dates Resulting in Loss</span>
                            </div>
                    </div>
                </div>

                {/* Stats Tables */}
                <div className="xl:col-span-4 space-y-6">
                    {/* Table 1: Expired Products Breakdown */}
                    <div className="border border-slate-200 rounded overflow-hidden shadow-sm">
                        <table className="w-full text-sm">
                            <thead className="bg-slate-100 text-slate-600 border-b border-slate-200">
                                <tr>
                                    <th className="p-2 text-left text-xs uppercase font-bold">Scenario</th>
                                    <th className="p-2 text-right text-xs uppercase font-bold">Count</th>
                                    <th className="p-2 text-right text-xs uppercase font-bold">Freq</th>
                                </tr>
                            </thead>
                            <tbody className="bg-white">
                                <tr className="border-b border-slate-100">
                                    <td className="p-2 font-semibold text-slate-700">
                                        {productType === ExoticType.SNOWBALL ? 'Knock-Out (Win)' : 'Barrier Hit (Rebate)'}
                                    </td>
                                    <td className="p-2 text-right font-mono text-slate-600">{backtestResult?.knockOutCount}</td>
                                    <td className="p-2 text-right font-mono font-bold text-green-600">{(backtestResult?.knockOutRate! * 100).toFixed(2)}%</td>
                                </tr>
                                <tr className="border-b border-slate-100">
                                    <td className="p-2 font-semibold text-slate-700">
                                        {productType === ExoticType.SNOWBALL ? 'Dividend/Bonus (Win)' : 'Participation Win'}
                                    </td>
                                    <td className="p-2 text-right font-mono text-slate-600">{backtestResult?.dividendCount}</td>
                                    <td className="p-2 text-right font-mono font-bold text-green-600">
                                        {backtestResult?.totalExpired ? ((backtestResult.dividendCount / backtestResult.totalExpired) * 100).toFixed(2) : 0}%
                                    </td>
                                </tr>
                                <tr className="bg-red-50">
                                    <td className="p-2 font-semibold text-[#E20000]">
                                        {productType === ExoticType.SNOWBALL ? 'Loss (KI & No KO)' : 'No Payout (Loss)'}
                                    </td>
                                    <td className="p-2 text-right font-mono text-[#E20000]">{backtestResult?.lossCount}</td>
                                    <td className="p-2 text-right font-mono font-bold text-[#E20000]">
                                            {backtestResult?.totalExpired ? ((backtestResult.lossCount / backtestResult.totalExpired) * 100).toFixed(2) : 0}%
                                    </td>
                                </tr>
                                <tr className="border-t border-slate-200 font-bold bg-slate-50">
                                    <td className="p-2 text-[#000066]">Total Expired</td>
                                    <td className="p-2 text-right font-mono">{backtestResult?.totalExpired}</td>
                                    <td className="p-2 text-right font-mono">100%</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>

                        {/* Additional Stats Grid */}
                        <div className="grid grid-cols-2 gap-4">
                            <div className="bg-white border border-slate-200 p-4 rounded shadow-sm text-center">
                                <div className="text-[10px] text-slate-400 uppercase font-bold mb-1">
                                    {productType === ExoticType.SNOWBALL ? 'Avg KO Duration' : 'Avg Life'}
                                </div>
                                <div className="text-xl font-bold text-[#000066]">{backtestResult?.avgDuration.toFixed(1)} <span className="text-xs font-normal text-slate-400">Months</span></div>
                            </div>
                            
                            {/* Recovery Rate only makes sense for Snowball KI then KO context mostly, but for Sharkfin we can repurpose or hide */}
                            {productType === ExoticType.SNOWBALL ? (
                                <div className="bg-white border border-slate-200 p-4 rounded shadow-sm text-center">
                                    <div className="text-[10px] text-slate-400 uppercase font-bold mb-1">Recovery Rate</div>
                                    <div className="text-xl font-bold text-[#EAB308]">{(backtestResult?.kiThenKoRate! * 100).toFixed(1)}%</div>
                                    <div className="text-[9px] text-slate-400 mt-1">(KI then KO)</div>
                                </div>
                            ) : (
                                <div className="bg-white border border-slate-200 p-4 rounded shadow-sm text-center">
                                    <div className="text-[10px] text-slate-400 uppercase font-bold mb-1">Rebate Yield</div>
                                    <div className="text-xl font-bold text-[#EAB308]">{(rebatePct * 100).toFixed(1)}%</div>
                                    <div className="text-[9px] text-slate-400 mt-1">Fixed Rebate</div>
                                </div>
                            )}
                        </div>

                        {/* Active Table */}
                        <div className="border border-slate-200 rounded overflow-hidden">
                            <div className="bg-[#000066] text-white p-2 text-xs font-bold uppercase flex justify-between">
                                <span>Active Products</span>
                                <span className="opacity-70 font-mono">{backtestResult?.totalActive}</span>
                            </div>
                            <div className="flex text-sm bg-white divide-x divide-slate-100">
                                <div className="flex-1 p-3 text-center">
                                    <div className="text-xs text-slate-400 mb-1">Safe</div>
                                    <div className="font-bold text-green-600">{backtestResult?.activeNoKI}</div>
                                </div>
                                {productType === ExoticType.SNOWBALL && (
                                    <div className="flex-1 p-3 text-center bg-red-50">
                                        <div className="text-xs text-red-400 mb-1">Knocked-In</div>
                                        <div className="font-bold text-[#E20000]">{backtestResult?.activeKI}</div>
                                    </div>
                                )}
                            </div>
                        </div>
                </div>
            </div>
        </div>
    </div>
  );
};

export default ExoticPricer;
