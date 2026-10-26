
import React, { useState, useMemo, useEffect } from 'react';
import { OptionLeg, PositionType, OptionType, AnalysisResult, VolSurfaceModel, InterpolationMethod } from './types';
import StrategyBuilder from './components/StrategyBuilder';
import ExoticPricer from './components/ExoticPricer';
import VolatilitySurfaceChart from './components/VolatilitySurface';
import BoosterResearch from './components/BoosterResearch';
import { PayoffChart, GreeksChart } from './components/Charts';
import { generateAnalysisData, calculateStrategyGreeks, calculateProbabilities } from './services/pricingEngine';
import { fitVolSurface, fitJwSviSurface, RAW_VOL_DATA } from './services/volatilityService';
import { fetchSurface, fetchProducts, fetchDates, ProductCode, SurfaceResponse, ProductInfo } from './services/api';
import { Activity, BarChart2, TrendingUp, Settings, Zap, ChevronDown, Anchor, Shield, TrendingDown, ZoomIn, Maximize2, MoveHorizontal, MoveVertical, Layers, Grid, BarChart3, Microscope, Split, RefreshCw, Calculator, Copy, Link2, Database, Globe, Server } from 'lucide-react';

// Tabs Enum
enum ActiveTab {
    VANILLA = 'Vanilla & Structures',
    EXOTIC = 'Exotics (Structured)',
    VOL_SURFACE = 'Volatility Surface',
    RESEARCH = 'Booster Research'
}

const PRODUCT_LABEL: Record<ProductCode, string> = {
  HO: 'HO 上证50',
  IO: 'IO 沪深300',
  MO: 'MO 中证1000',
};

const App: React.FC = () => {
  // Global Market State
  const [spotPrice, setSpotPrice] = useState<number>(4000); 
  
  // Volatility State
  const [volStrategy, setVolStrategy] = useState<'surface' | 'flat'>('surface');
  const [flatVol, setFlatVol] = useState<number>(0.20);
  const [volShift, setVolShift] = useState<number>(0); 
  const [volSlope, setVolSlope] = useState<number>(0);
  
  // Volatility Model State
  const [volMethod, setVolMethod] = useState<InterpolationMethod>('JW SVI');
  const [volModel, setVolModel] = useState<VolSurfaceModel | undefined>(undefined);
  const [product, setProduct] = useState<ProductCode>('IO');
  const [tradeDate, setTradeDate] = useState<string>('');
  const [availableDates, setAvailableDates] = useState<string[]>([]);
  const [productsInfo, setProductsInfo] = useState<ProductInfo[]>([]);
  const [surfaceMeta, setSurfaceMeta] = useState<SurfaceResponse | null>(null);
  const [apiStatus, setApiStatus] = useState<'loading' | 'live' | 'offline'>('loading');
  const [surfaceLoading, setSurfaceLoading] = useState(false);
  const [apiError, setApiError] = useState<string>('');

  // Correlation State (Worst-Of)
  const [structureMode, setStructureMode] = useState<'single' | 'worst-of'>('single');
  const [rho, setRho] = useState(0.5);

  const [riskFreeRate, setRiskFreeRate] = useState<number>(0.02);
  
  // Navigation
  const [activeTab, setActiveTab] = useState<ActiveTab>(ActiveTab.VANILLA);

  // Load product catalog once
  useEffect(() => {
      fetchProducts()
        .then(list => {
          setProductsInfo(list);
          setApiStatus('live');
        })
        .catch(err => {
          setApiStatus('offline');
          setApiError(String(err));
          const model = fitVolSurface(RAW_VOL_DATA, 'Cubic Spline', 6600);
          setVolModel(model);
          setSpotPrice(6600);
        });
  }, []);

  // Load dates when product changes
  useEffect(() => {
      if (apiStatus !== 'live') return;
      fetchDates(product)
        .then(ds => {
          setAvailableDates(ds);
          const latest = ds[ds.length - 1] || '';
          setTradeDate(prev => (prev && ds.includes(prev) ? prev : latest));
        })
        .catch(err => setApiError(String(err)));
  }, [product, apiStatus]);

  // Load surface when product/date/method changes
  useEffect(() => {
      if (apiStatus !== 'live' || !tradeDate) return;
      let cancelled = false;
      setSurfaceLoading(true);
      fetchSurface(product, tradeDate)
        .then(surf => {
          if (cancelled) return;
          setSurfaceMeta(surf);
          setSpotPrice(surf.spot);
          if (volMethod === 'JW SVI') {
            setVolModel(fitJwSviSurface(surf.slices, surf.spot));
          } else {
            setVolModel(fitVolSurface(surf.points, volMethod, surf.spot));
          }
          setApiError('');
        })
        .catch(err => {
          if (cancelled) return;
          setApiError(String(err));
        })
        .finally(() => {
          if (!cancelled) setSurfaceLoading(false);
        });
      return () => { cancelled = true; };
  }, [product, tradeDate, volMethod, apiStatus]);

  // Analysis View State
  const [chartRangeX, setChartRangeX] = useState<number>(0.20); 
  const [chartRangeY, setChartRangeY] = useState<number>(1.1);  
  const [showDecomposition, setShowDecomposition] = useState<boolean>(false);

  // Strategy State (Tab 1)
  const [legs, setLegs] = useState<OptionLeg[]>([]);

  // Compute Effective Legs based on Vol Strategy
  const effectiveLegs = useMemo(() => {
      if (volStrategy === 'flat') {
          return legs.map(leg => ({
              ...leg,
              iv: flatVol // Override individual leg IV with global flat IV
          }));
      }
      return legs;
  }, [legs, volStrategy, flatVol]);

  const handleRiskFreeRateChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newRate = parseFloat(e.target.value);
    setRiskFreeRate(newRate);
    setLegs(prevLegs => prevLegs.map(leg => ({ ...leg, r: newRate })));
  };

  const handleStrategyChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const strategy = e.target.value;
    if (!strategy) return;
    
    const ttm = 90 / 365; // 3 Months default
    // Initial IV: If Surface mode, predict. If Flat mode, use flatVol.
    const atmVol = (volStrategy === 'surface' && volModel) ? volModel.predict(spotPrice, ttm) : flatVol;

    const common = {
        iv: atmVol, 
        t: ttm, 
        r: riskFreeRate,
    };
    const id = () => Math.random().toString(36).substr(2, 9);
    
    const getIv = (k: number) => {
        if (volStrategy === 'flat') return flatVol;
        return volModel ? volModel.predict(k, ttm) : atmVol;
    };

    let newLegs: OptionLeg[] = [];

    switch(strategy) {
        case 'booster':
             // 4-Leg Booster Structure (Call Spread + Put Spread)
             const k_atm = spotPrice;
             const k_cap = spotPrice * 1.05;
             const k_put = spotPrice * 0.95;
             const k_prot = spotPrice * 0.85;

             newLegs = [
                { ...common, id: id(), type: OptionType.CALL, position: PositionType.LONG, strike: k_atm, iv: getIv(k_atm), quantity: 1.0 },
                { ...common, id: id(), type: OptionType.CALL, position: PositionType.SHORT, strike: k_cap, iv: getIv(k_cap), quantity: 1.0 },
                { ...common, id: id(), type: OptionType.PUT, position: PositionType.SHORT, strike: k_put, iv: getIv(k_put), quantity: 1.0 },
                { ...common, id: id(), type: OptionType.PUT, position: PositionType.LONG, strike: k_prot, iv: getIv(k_prot), quantity: 1.0 },
            ];
            break;
        case 'bull-call':
            newLegs = [
                { ...common, id: id(), type: OptionType.CALL, position: PositionType.LONG, strike: spotPrice, iv: getIv(spotPrice), quantity: 1 },
                { ...common, id: id(), type: OptionType.CALL, position: PositionType.SHORT, strike: spotPrice * 1.05, iv: getIv(spotPrice * 1.05), quantity: 1 },
            ];
            break;
        case 'straddle':
            newLegs = [
                { ...common, id: id(), type: OptionType.CALL, position: PositionType.LONG, strike: spotPrice, iv: getIv(spotPrice), quantity: 1 },
                { ...common, id: id(), type: OptionType.PUT, position: PositionType.LONG, strike: spotPrice, iv: getIv(spotPrice), quantity: 1 },
            ];
            break;
    }
    setLegs(newLegs);
    e.target.value = "";
  };

  // Derived Analysis Data (Uses effectiveLegs)
  const chartData = useMemo(() => {
    if (effectiveLegs.length === 0) return [];
    
    // 1. Generate Aggregate (Pass correlation params)
    const baseData = generateAnalysisData(
        effectiveLegs, 
        spotPrice, 
        chartRangeX, 
        volShift, 
        volSlope,
        structureMode === 'worst-of',
        rho
    );

    // 2. If Decomposition is Active, generate individual leg data and merge
    if (showDecomposition) {
        // Note: For decomposition, we pass the same global slope/shift
        const legDatasets = effectiveLegs.map(leg => generateAnalysisData([leg], spotPrice, chartRangeX, volShift, volSlope));
        
        return baseData.map((point, i) => {
            const merged: any = { ...point };
            effectiveLegs.forEach((leg, idx) => {
                const legPoint = legDatasets[idx][i];
                if (legPoint) {
                    merged[`pnl_${leg.id}`] = legPoint.pnl;
                    merged[`delta_${leg.id}`] = legPoint.delta;
                    merged[`gamma_${leg.id}`] = legPoint.gamma;
                    merged[`vega_${leg.id}`] = legPoint.vega;
                    merged[`theta_${leg.id}`] = legPoint.theta;
                    merged[`vanna_${leg.id}`] = legPoint.vanna;
                    merged[`volga_${leg.id}`] = legPoint.volga;
                    merged[`skewRisk_${leg.id}`] = legPoint.skewRisk;
                    // Correlation risk decomposition usually doesn't make sense per leg in a basket context same way, omitting
                }
            });
            return merged;
        });
    }

    return baseData; 
  }, [effectiveLegs, spotPrice, riskFreeRate, chartRangeX, volShift, volSlope, showDecomposition, structureMode, rho]);

  const currentGreeks = useMemo(() => {
      return calculateStrategyGreeks(effectiveLegs, spotPrice, volShift, volSlope);
  }, [effectiveLegs, spotPrice, volShift, volSlope]);

  const stats: AnalysisResult = useMemo(() => {
      if (effectiveLegs.length === 0) return { totalPrice: 0, breakEvenPoints: [], maxProfit: 0, maxLoss: 0, winRate: 0 };
      return calculateProbabilities(effectiveLegs, spotPrice, volShift, volSlope);
  }, [effectiveLegs, spotPrice, volShift, volSlope]);

  const structMetrics = useMemo(() => {
      if (effectiveLegs.length === 0) return null;
      // Metrics depend on Strikes/Qty, so we can use legs or effectiveLegs
      const longCalls = effectiveLegs.filter(l => l.type === OptionType.CALL && l.position === PositionType.LONG);
      const shortCalls = effectiveLegs.filter(l => l.type === OptionType.CALL && l.position === PositionType.SHORT);
      const shortPuts = effectiveLegs.filter(l => l.type === OptionType.PUT && l.position === PositionType.SHORT);
      
      const mainLongCall = longCalls.sort((a,b) => a.strike - b.strike)[0];
      const mainShortCall = shortCalls.sort((a,b) => b.strike - a.strike)[0];
      const mainShortPut = shortPuts[0]; 
      
      return {
          participationRate: mainLongCall ? mainLongCall.quantity * 100 : 0,
          capLevel: mainShortCall ? (mainShortCall.strike / spotPrice) * 100 : null,
          protectionLevel: mainShortPut ? (mainShortPut.strike / spotPrice) * 100 : null,
          lowerCallStrike: mainLongCall ? (mainLongCall.strike / spotPrice) * 100 : null,
          putStrike: mainShortPut ? (mainShortPut.strike / spotPrice) * 100 : null,
      };
  }, [effectiveLegs, spotPrice]);

  const COLORS = {
      CLSA_BLUE: '#000066',
      CLSA_YELLOW: '#EAB308', 
      CITIC_RED: '#E20000',
      GREEN_RISK: '#16a34a' 
  };

  // Helper to render the Surface Selection Buttons
  const renderVolMethodSelector = () => (
      <div className="flex bg-slate-200 p-0.5 rounded mt-2">
        {(['JW SVI', 'Cubic Spline', 'Local Volatility', 'Stochastic Volatility'] as InterpolationMethod[]).map(m => {
            const activeClass = m === 'JW SVI'
                ? 'bg-[#0F766E] text-white shadow'
                : m === 'Local Volatility' 
                ? 'bg-[#EAB308] text-white shadow' 
                : m === 'Stochastic Volatility' 
                ? 'bg-[#E20000] text-white shadow' 
                : 'bg-[#000066] text-white shadow';
            const short = m === 'JW SVI' ? 'JW' : m === 'Cubic Spline' ? 'Cubic' : m === 'Local Volatility' ? 'Local' : 'Stoch';

            return (
                <button 
                    key={m}
                    onClick={() => setVolMethod(m)}
                    className={`flex-1 py-1 text-[9px] font-bold rounded transition text-center leading-tight ${volMethod === m ? activeClass : 'text-slate-500 hover:text-slate-700'}`}
                >
                    {short}
                </button>
            )
        })}
      </div>
  );

  return (
    <div className="min-h-screen font-sans bg-slate-100 selection:bg-[#FFF100] selection:text-[#000066]">
      <header className="bg-[#000066] border-b border-[#000066] sticky top-0 z-50 shadow-md">
        <div className="max-w-[1600px] mx-auto px-4 py-3 flex justify-between items-center">
          <div className="flex items-center gap-4">
             <div className="flex items-baseline gap-2 select-none">
                 <span className="text-white font-serif font-bold text-xl tracking-tight">
                    <span className="text-[#E20000]">CITIC</span> Securities
                 </span>
                 <span className="h-4 w-px bg-slate-400 mx-1"></span>
                 <span className="text-[#FFF100] font-bold text-xl tracking-wider font-sans">CLSA</span>
             </div>
             <div className="hidden md:block">
               <p className="text-blue-200 text-xs uppercase tracking-widest font-semibold">Exotic Option Architect - Peter Xikai Wang</p>
             </div>
          </div>
          <div className="flex bg-[#000044] rounded p-1 gap-1">
             {[ActiveTab.VANILLA, ActiveTab.EXOTIC, ActiveTab.VOL_SURFACE, ActiveTab.RESEARCH].map(tab => (
                 <button 
                    key={tab}
                    onClick={() => setActiveTab(tab)}
                    className={`px-4 py-1.5 text-xs font-bold rounded transition flex items-center gap-2 ${activeTab === tab ? 'bg-[#FFF100] text-[#000066]' : 'text-blue-200 hover:text-white'}`}
                 >
                    {tab === ActiveTab.RESEARCH && <Microscope size={14} />}
                    {tab}
                 </button>
             ))}
          </div>
        </div>
      </header>

      <main className="max-w-[1600px] mx-auto px-4 py-8">
        
        {/* --- TAB 1: VANILLA & STRUCTURES --- */}
        <div className={activeTab === ActiveTab.VANILLA ? 'block' : 'hidden'}>
            <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
                <div className="xl:col-span-3 space-y-6">
                     <div className="bg-white rounded-lg p-4 shadow-sm border border-slate-200">
                        <label className="block text-xs font-bold text-slate-500 uppercase mb-2 flex items-center gap-2">
                             <Zap size={14} className="text-[#EAB308]" /> Quick Strategy
                        </label>
                        <div className="relative">
                            <select 
                                onChange={handleStrategyChange}
                                className="w-full pl-3 pr-8 py-2 bg-slate-50 text-sm text-[#000066] font-bold border border-slate-200 rounded-md focus:outline-none focus:ring-2 focus:ring-[#000066] appearance-none cursor-pointer hover:bg-slate-100 transition"
                                defaultValue=""
                            >
                                <option value="" disabled>Select a Template...</option>
                                <option value="booster">Booster (Leveraged Upside)</option>
                                <option value="bull-call">Bull Call Spread</option>
                                <option value="straddle">Straddle</option>
                            </select>
                            <div className="absolute inset-y-0 right-0 pr-2 flex items-center pointer-events-none text-slate-400">
                                <ChevronDown size={14} />
                            </div>
                        </div>
                     </div>

                    <StrategyBuilder 
                        legs={legs} 
                        setLegs={setLegs} 
                        marketSpot={spotPrice} 
                        riskFreeRate={riskFreeRate}
                        volModel={volModel}
                        volShift={volShift}
                        volSlope={volSlope}
                        volStrategy={volStrategy}
                        flatVol={flatVol}
                    />
                </div>

                <div className="xl:col-span-6 space-y-6">
                    <div className="bg-white rounded-lg p-6 shadow-md border border-slate-200 min-h-[400px]">
                        <div className="flex justify-between items-center mb-6">
                            <h2 className="text-xl font-bold text-[#000066] flex items-center gap-2">
                                <TrendingUp className="text-[#000066]" />
                                Payoff & Greeks
                            </h2>
                            
                            <button 
                                onClick={() => setShowDecomposition(!showDecomposition)}
                                className={`flex items-center gap-2 text-xs font-bold px-3 py-1.5 rounded-full border transition ${showDecomposition ? 'bg-[#000066] text-white border-[#000066]' : 'bg-white text-slate-500 border-slate-200 hover:border-slate-300'}`}
                            >
                                <Split size={14} /> Decomposition
                            </button>
                        </div>
                        {effectiveLegs.length > 0 ? (
                            <div className="space-y-6">
                                <div className="w-full">
                                    <PayoffChart 
                                        data={chartData} 
                                        marketSpot={spotPrice} 
                                        yScale={chartRangeY} 
                                        legs={showDecomposition ? effectiveLegs : undefined}
                                    />
                                </div>

                                {/* Taylor Series Expansion Display */}
                                <div className="bg-slate-50 rounded border border-slate-200 p-4 shadow-inner">
                                    <h3 className="text-xs font-bold text-slate-500 uppercase flex items-center gap-2 mb-3">
                                        <Calculator size={14} className="text-[#000066]" /> PnL Attribution (Taylor Expansion)
                                    </h3>
                                    
                                    {/* Numeric Substitution Row */}
                                    <div className="flex flex-wrap items-center justify-center gap-2 text-xs font-mono text-slate-600">
                                        <span className="font-bold">dP</span>
                                        <span>≈</span>
                                        
                                        <span className="text-[#E20000] font-bold">{currentGreeks.delta.toFixed(2)}</span>
                                        <span>·dS</span>
                                        <span>+</span>

                                        <span className="text-[#000066] font-bold">{(0.5 * currentGreeks.gamma).toFixed(5)}</span>
                                        <span>·(dS)²</span>
                                        <span>+</span>

                                        <span className="text-[#000066] font-bold">{currentGreeks.vega.toFixed(2)}</span>
                                        <span>·dσ</span>
                                        <span>+</span>

                                        <span className="text-[#EAB308] font-bold">{currentGreeks.theta.toFixed(2)}</span>
                                        <span>·dt</span>
                                    </div>
                                </div>

                                {/* Greeks Grid */}
                                <div className="grid grid-cols-2 gap-6">
                                    {/* Row 1: Delta & Vega (Swapped Correlation Risk) */}
                                    <div className="bg-slate-50 p-3 rounded border border-slate-200 shadow-sm">
                                        <h4 className="text-xs font-bold mb-2 uppercase text-center flex items-center justify-center gap-1" style={{color: COLORS.CITIC_RED}}><Activity size={12} /> Delta</h4>
                                        <GreeksChart 
                                            data={chartData} 
                                            marketSpot={spotPrice} 
                                            dataKey="delta" 
                                            color={COLORS.CITIC_RED} 
                                            name="Delta" 
                                            yScale={chartRangeY} 
                                            legs={showDecomposition ? effectiveLegs : undefined}
                                        />
                                    </div>
                                    
                                    <div className="bg-slate-50 p-3 rounded border border-slate-200 shadow-sm">
                                        <h4 className="text-xs font-bold mb-2 uppercase text-center flex items-center justify-center gap-1" style={{color: COLORS.CLSA_BLUE}}><Activity size={12} /> Vega</h4>
                                        <GreeksChart 
                                            data={chartData} 
                                            marketSpot={spotPrice} 
                                            dataKey="vega" 
                                            color={COLORS.CLSA_BLUE} 
                                            name="Vega" 
                                            yScale={chartRangeY} 
                                            legs={showDecomposition ? effectiveLegs : undefined}
                                        />
                                    </div>

                                    {/* Row 2: Correlation Risk & Skew Risk */}
                                    <div className="bg-slate-50 p-3 rounded border border-slate-200 shadow-sm relative">
                                        {structureMode !== 'worst-of' && (
                                            <div className="absolute inset-0 bg-white/80 z-10 flex items-center justify-center text-xs text-slate-400 font-bold backdrop-blur-sm rounded">
                                                Enable "Worst-Of" Mode for Correlation Risk
                                            </div>
                                        )}
                                        <h4 className="text-xs font-bold mb-2 uppercase text-center flex items-center justify-center gap-1 text-[#EAB308]"><Link2 size={12} /> Correlation Risk</h4>
                                        <GreeksChart 
                                            data={chartData} 
                                            marketSpot={spotPrice} 
                                            dataKey="correlationRisk" 
                                            color={COLORS.CLSA_YELLOW} // Yellow
                                            name="Correlation Risk" 
                                            yScale={chartRangeY} 
                                            // No leg decomposition for correlation risk (basket property)
                                        />
                                    </div>

                                    <div className="bg-slate-50 p-3 rounded border border-slate-200 shadow-sm">
                                        <h4 className="text-xs font-bold mb-2 uppercase text-center flex items-center justify-center gap-1 text-green-600"><Activity size={12} /> Skew Risk</h4>
                                        <GreeksChart 
                                            data={chartData} 
                                            marketSpot={spotPrice} 
                                            dataKey="skewRisk" 
                                            color={COLORS.GREEN_RISK} 
                                            name="Skew Risk" 
                                            yScale={chartRangeY} 
                                            legs={showDecomposition ? effectiveLegs : undefined}
                                        />
                                    </div>

                                    {/* Row 3: Theta & Gamma */}
                                    <div className="bg-slate-50 p-3 rounded border border-slate-200 shadow-sm">
                                        <h4 className="text-xs font-bold mb-2 uppercase text-center flex items-center justify-center gap-1" style={{color: COLORS.CLSA_YELLOW}}><Activity size={12} /> Theta</h4>
                                        <GreeksChart 
                                            data={chartData} 
                                            marketSpot={spotPrice} 
                                            dataKey="theta" 
                                            color={COLORS.CLSA_YELLOW} 
                                            name="Theta" 
                                            yScale={chartRangeY} 
                                            legs={showDecomposition ? effectiveLegs : undefined}
                                        />
                                    </div>

                                    <div className="bg-slate-50 p-3 rounded border border-slate-200 shadow-sm">
                                        <h4 className="text-xs font-bold mb-2 uppercase text-center flex items-center justify-center gap-1" style={{color: COLORS.CLSA_BLUE}}><Activity size={12} /> Gamma</h4>
                                        <GreeksChart 
                                            data={chartData} 
                                            marketSpot={spotPrice} 
                                            dataKey="gamma" 
                                            color={COLORS.CLSA_BLUE} 
                                            name="Gamma" 
                                            yScale={chartRangeY} 
                                            legs={showDecomposition ? effectiveLegs : undefined}
                                        />
                                    </div>

                                    {/* Row 4: Vol Skew Greeks */}
                                    <div className="bg-slate-50 p-3 rounded border border-slate-200 shadow-sm">
                                        <h4 className="text-xs font-bold mb-2 uppercase text-center flex items-center justify-center gap-1 text-purple-600"><Activity size={12} /> Vanna</h4>
                                        <GreeksChart 
                                            data={chartData} 
                                            marketSpot={spotPrice} 
                                            dataKey="vanna" 
                                            color="#9333ea" 
                                            name="Vanna" 
                                            yScale={chartRangeY} 
                                            legs={showDecomposition ? effectiveLegs : undefined}
                                        />
                                    </div>

                                    <div className="bg-slate-50 p-3 rounded border border-slate-200 shadow-sm">
                                        <h4 className="text-xs font-bold mb-2 uppercase text-center flex items-center justify-center gap-1 text-teal-600"><Activity size={12} /> Volga</h4>
                                        <GreeksChart 
                                            data={chartData} 
                                            marketSpot={spotPrice} 
                                            dataKey="volga" 
                                            color="#0d9488" 
                                            name="Volga" 
                                            yScale={chartRangeY} 
                                            legs={showDecomposition ? effectiveLegs : undefined}
                                        />
                                    </div>
                                </div>
                            </div>
                        ) : (
                            <div className="h-[300px] flex flex-col items-center justify-center text-slate-400">
                                <p>Add legs to see visualization</p>
                            </div>
                        )}
                    </div>
                </div>

                <div className="xl:col-span-3 space-y-6">
                    <div className="bg-white rounded-lg p-6 shadow-md border border-slate-200">
                        <h2 className="text-[#000066] font-bold mb-4 flex items-center gap-2">
                        <Settings size={18} className="text-[#000066]" /> Market Params
                        </h2>
                        <div className="space-y-4">
                        
                        {/* Layout Change: Spot and Risk-Free Side-by-Side */}
                        <div className="grid grid-cols-2 gap-3 mb-2">
                            <div>
                                <div className="flex justify-between text-xs mb-1">
                                <span className="text-slate-500 font-bold">Spot</span>
                                <span className="font-mono text-[#000066] font-bold">{spotPrice.toFixed(1)}</span>
                                </div>
                                <input 
                                type="range" min="4000" max="8000" step="10" 
                                value={spotPrice} onChange={(e) => setSpotPrice(Number(e.target.value))}
                                className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                                />
                            </div>
                            <div>
                                <div className="flex justify-between text-xs mb-1">
                                <span className="text-slate-500 font-bold">Risk-Free</span>
                                <span className="font-mono text-[#000066] font-bold">{(riskFreeRate * 100).toFixed(1)}%</span>
                                </div>
                                <input 
                                type="range" min="0.0" max="0.2" step="0.005" 
                                value={riskFreeRate} onChange={handleRiskFreeRateChange}
                                className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                                />
                            </div>
                        </div>
                        
                        {/* ENVIRONMENT 1: Volatility Control Section */}
                        <div className="p-3 bg-slate-50 rounded border border-slate-200">
                            <h4 className="text-[10px] font-bold text-slate-400 uppercase mb-3 flex items-center gap-1 border-b border-slate-200 pb-1">
                                Environment 1: Volatility
                            </h4>
                            
                            <div className="flex justify-between items-center mb-3">
                                <span className="text-xs font-bold text-slate-500 uppercase flex items-center gap-1"><RefreshCw size={10}/> Vol Mode</span>
                                <div className="flex bg-slate-200 rounded p-0.5">
                                    <button 
                                        onClick={() => setVolStrategy('flat')}
                                        className={`px-2 py-0.5 text-[10px] font-bold rounded transition ${volStrategy === 'flat' ? 'bg-white text-[#000066] shadow' : 'text-slate-500'}`}
                                    >Flat</button>
                                    <button 
                                        onClick={() => setVolStrategy('surface')}
                                        className={`px-2 py-0.5 text-[10px] font-bold rounded transition ${volStrategy === 'surface' ? 'bg-white text-[#000066] shadow' : 'text-slate-500'}`}
                                    >Surface</button>
                                </div>
                            </div>

                            {volStrategy === 'flat' ? (
                                <div className="mb-2">
                                    <div className="flex justify-between text-xs mb-1">
                                        <span className="text-slate-500">Global Flat IV</span>
                                        <span className="font-mono font-bold text-[#000066]">{(flatVol * 100).toFixed(1)}%</span>
                                    </div>
                                    <input 
                                        type="range" min="0.1" max="0.5" step="0.005" 
                                        value={flatVol} onChange={(e) => setFlatVol(parseFloat(e.target.value))}
                                        className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                                    />
                                </div>
                            ) : (
                                <>
                                    {/* Surface Method Selector inside Tab 1 */}
                                    {renderVolMethodSelector()}

                                    <div className="mb-2 mt-3">
                                        <div className="flex justify-between text-xs mb-1">
                                        <span className="text-slate-500">Vol Shift (Skew)</span>
                                        <span className="font-mono text-[#000066] font-bold">{volShift > 0 ? '+' : ''}{(volShift * 100).toFixed(1)}%</span>
                                        </div>
                                        <input 
                                        type="range" min="-0.3" max="0.3" step="0.005" 
                                        value={volShift} onChange={(e) => setVolShift(parseFloat(e.target.value))}
                                        className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                                        />
                                    </div>
                                </>
                            )}

                            <div>
                                <div className="flex justify-between text-xs mb-1">
                                <span className="text-slate-500">Vol Skew Slope</span>
                                <span className="font-mono text-[#000066] font-bold">{volSlope.toFixed(2)}</span>
                                </div>
                                <input 
                                type="range" min="-0.5" max="0.5" step="0.05" 
                                value={volSlope} onChange={(e) => setVolSlope(parseFloat(e.target.value))}
                                className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                                />
                            </div>
                        </div>

                        {/* ENVIRONMENT 2: Correlation (Rho) Section */}
                        <div className="p-3 bg-slate-50 rounded border border-slate-200">
                            <h4 className="text-[10px] font-bold text-slate-400 uppercase mb-3 flex items-center gap-1 border-b border-slate-200 pb-1">
                                Environment 2: Correlation
                            </h4>
                            
                            <div className="flex justify-between items-center mb-3">
                                <span className="text-xs font-bold text-slate-500 uppercase flex items-center gap-1"><Link2 size={10}/> Basket</span>
                                <div className="flex bg-slate-200 rounded p-0.5">
                                    <button 
                                        onClick={() => setStructureMode('single')}
                                        className={`px-2 py-0.5 text-[10px] font-bold rounded transition ${structureMode === 'single' ? 'bg-white text-[#000066] shadow' : 'text-slate-500'}`}
                                    >Single</button>
                                    <button 
                                        onClick={() => setStructureMode('worst-of')}
                                        className={`px-2 py-0.5 text-[10px] font-bold rounded transition ${structureMode === 'worst-of' ? 'bg-white text-[#000066] shadow' : 'text-slate-500'}`}
                                    >Worst-Of</button>
                                </div>
                            </div>

                            <div className={`transition-opacity duration-300 ${structureMode === 'worst-of' ? 'opacity-100' : 'opacity-40 pointer-events-none'}`}>
                                <div className="flex justify-between text-xs mb-1">
                                    <span className="text-slate-500">Correlation (Rho)</span>
                                    <span className="font-mono font-bold text-[#000066]">{rho.toFixed(2)}</span>
                                </div>
                                <input 
                                    type="range" min="-1.0" max="1.0" step="0.05" 
                                    value={rho} onChange={(e) => setRho(parseFloat(e.target.value))}
                                    className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                                />
                                <div className="flex justify-between text-[9px] text-slate-400 mt-1">
                                    <span>Hedge</span>
                                    <span>Uncorrelated</span>
                                    <span>Perf</span>
                                </div>
                            </div>
                        </div>

                        </div>
                    </div>

                    <div className="bg-white rounded-lg p-5 shadow-md border border-slate-200">
                         <h3 className="font-bold text-[#000066] mb-4 flex items-center gap-2">
                            <Maximize2 size={18} className="text-[#000066]" /> View Controls
                        </h3>
                        <div className="space-y-4">
                            <div>
                                <div className="flex justify-between text-xs mb-1 font-bold text-slate-500 uppercase">
                                    <span>X-Axis Range</span>
                                    <span>{(chartRangeX * 100).toFixed(0)}%</span>
                                </div>
                                <div className="flex items-center gap-2">
                                    <ZoomIn size={14} className="text-slate-400"/>
                                    <input 
                                        type="range" min="0.05" max="0.5" step="0.05" 
                                        value={chartRangeX} onChange={(e) => setChartRangeX(parseFloat(e.target.value))}
                                        className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none"
                                    />
                                </div>
                            </div>
                            <div>
                                <div className="flex justify-between text-xs mb-1 font-bold text-slate-500 uppercase">
                                    <span>Y-Axis Scale</span>
                                    <span>{chartRangeY.toFixed(1)}x</span>
                                </div>
                                <div className="flex items-center gap-2">
                                    <MoveVertical size={14} className="text-slate-400"/>
                                    <input 
                                        type="range" min="0.5" max="3.0" step="0.1" 
                                        value={chartRangeY} onChange={(e) => setChartRangeY(parseFloat(e.target.value))}
                                        className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none"
                                    />
                                </div>
                            </div>
                        </div>
                    </div>

                     <div className="bg-white rounded-lg p-5 shadow-md border border-slate-200">
                        <h3 className="font-bold text-[#000066] mb-4 flex items-center gap-2">
                            <Shield size={18} className="text-[#000066]" /> Structure Statistics
                        </h3>
                        <div className="space-y-3">
                            <div className="flex items-center justify-between p-3 bg-slate-50 rounded border border-slate-200">
                                    <div className="flex flex-col">
                                        <span className="text-xs font-bold text-slate-500 uppercase">Participation</span>
                                        <span className="text-[10px] text-slate-400">Upside</span>
                                    </div>
                                    <div className="text-right">
                                        <span className="text-xl font-mono font-bold text-[#000066]">
                                        {structMetrics?.participationRate.toFixed(0)}%
                                        </span>
                                    </div>
                            </div>
                              <div className="grid grid-cols-2 gap-3">
                                <div className="p-2 bg-slate-50 rounded border border-slate-200">
                                    <span className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Cap Level</span>
                                    <span className="block text-base font-mono font-bold text-slate-700">
                                    {structMetrics?.capLevel ? `${structMetrics.capLevel.toFixed(0)}%` : 'Unltd'}
                                    </span>
                                </div>
                                <div className="p-2 bg-slate-50 rounded border border-slate-200">
                                    <span className="block text-[10px] font-bold text-slate-500 uppercase mb-1">Protection</span>
                                    <span className="block text-base font-mono font-bold text-[#E20000]">
                                    {structMetrics?.protectionLevel ? `${structMetrics.protectionLevel.toFixed(0)}%` : 'N/A'}
                                    </span>
                                </div>
                            </div>
                        </div>
                    </div>
                    
                    <div className="bg-white rounded-lg p-5 shadow-md border border-slate-200">
                        <h3 className="font-bold text-[#000066] mb-4 flex items-center gap-2">
                            <Activity size={18} className="text-[#000066]" /> Risk Analysis
                        </h3>
                        <div className="grid grid-cols-1 gap-3 text-sm">
                            <div className="p-3 rounded bg-slate-50 border border-slate-200 flex justify-between items-center">
                                <span className="text-slate-500 text-xs uppercase font-bold">Max Profit</span>
                                <span className="font-mono text-green-600 font-bold text-lg">
                                    {stats.maxProfit === 'Unlimited' ? '∞' : `$${stats.maxProfit.toFixed(2)}`}
                                </span>
                            </div>
                            <div className="p-3 rounded bg-slate-50 border border-slate-200 flex justify-between items-center">
                                <span className="text-slate-500 text-xs uppercase font-bold">Max Loss</span>
                                <span className="font-mono text-[#E20000] font-bold text-lg">
                                    {stats.maxLoss === 'Unlimited' ? '∞' : `$${stats.maxLoss.toFixed(2)}`}
                                </span>
                            </div>
                            <div className="p-3 rounded bg-slate-50 border border-slate-200">
                                <span className="block text-slate-500 text-xs uppercase font-bold mb-1">Break-Even Points</span>
                                <span className="block font-mono text-[#000066] font-bold text-sm">
                                    {stats.breakEvenPoints.length > 0 ? stats.breakEvenPoints.map(b => `${((b / spotPrice) * 100).toFixed(2)}%`).join(', ') : 'None'}
                                </span>
                            </div>
                        </div>
                    </div>
                    
                    <div className="bg-white rounded-lg p-5 shadow-md border border-slate-200">
                        <h2 className="text-[#000066] font-bold mb-4 flex items-center gap-2">
                            <BarChart2 size={18} className="text-[#000066]" /> Live Metrics
                        </h2>
                        <div className="grid grid-cols-2 gap-3">
                            <div className="bg-slate-50 p-2 rounded border border-slate-200 text-center col-span-2">
                                <p className="text-[10px] text-slate-500 uppercase font-bold">Net Premium</p>
                                <p className={`text-lg font-mono font-bold ${currentGreeks.price < 0 ? 'text-green-600' : 'text-[#E20000]'}`}>
                                    ${Math.abs(currentGreeks.price).toFixed(2)} {currentGreeks.price > 0 ? 'Dr' : 'Cr'}
                                </p>
                            </div>
                            <div className="bg-slate-50 p-2 rounded border border-slate-200 text-center">
                                <p className="text-[10px] text-slate-500 uppercase font-bold">Win Rate</p>
                                <p className="text-sm font-mono font-bold text-[#EAB308]">
                                    {(stats.winRate * 100).toFixed(1)}%
                                </p>
                            </div>
                             <div className="bg-slate-50 p-2 rounded border border-slate-200 text-center">
                                <p className="text-[10px] text-slate-500 uppercase font-bold">Delta</p>
                                <p className="text-sm font-mono font-bold text-slate-700">
                                    {currentGreeks.delta.toFixed(2)}
                                </p>
                            </div>
                            <div className="bg-slate-50 p-2 rounded border border-slate-200 text-center">
                                <p className="text-[10px] text-slate-500 uppercase font-bold">Gamma</p>
                                <p className="text-sm font-mono font-bold text-slate-700">
                                    {currentGreeks.gamma.toFixed(4)}
                                </p>
                            </div>
                            <div className="bg-slate-50 p-2 rounded border border-slate-200 text-center">
                                <p className="text-[10px] text-slate-500 uppercase font-bold">Vega</p>
                                <p className="text-sm font-mono font-bold text-slate-700">
                                    {currentGreeks.vega.toFixed(2)}
                                </p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        {/* --- TAB 2: EXOTICS & BARRIER --- */}
        <div className={activeTab === ActiveTab.EXOTIC ? 'block' : 'hidden'}>
             <ExoticPricer 
                spot={spotPrice} 
                r={riskFreeRate} 
                vol={flatVol} 
                volModel={volModel}
                volMethod={volMethod}
                onMethodChange={setVolMethod}
             />
        </div>

        {/* --- TAB 3: VOLATILITY SURFACE --- */}
        <div className={activeTab === ActiveTab.VOL_SURFACE ? 'block' : 'hidden'}>
            <div className="grid grid-cols-1 xl:grid-cols-4 gap-6 h-[600px]">
                <div className="xl:col-span-3 h-full">
                    {volModel ? (
                        <VolatilitySurfaceChart 
                            model={volModel} 
                            spot={spotPrice} 
                            method={volMethod}
                            // onMethodChange handled in parent now
                        />
                    ) : (
                        <div className="flex items-center justify-center h-full bg-white rounded shadow text-slate-400">Loading Model...</div>
                    )}
                </div>
                <div className="xl:col-span-1 space-y-4">
                     
                     {/* 1. Control Panel (Button Slider) */}
                     <div className="bg-white p-4 rounded-lg shadow-md border border-slate-200">
                        <h3 className="font-bold text-[#000066] mb-3 flex items-center gap-2 text-sm uppercase">
                            <Settings size={14}/> Interpolation Method
                        </h3>
                        <div className="flex flex-col gap-2">
                            {(['JW SVI', 'Cubic Spline', 'Local Volatility', 'Stochastic Volatility'] as InterpolationMethod[]).map(m => {
                                let activeColor = 'bg-[#000066] text-white border-[#000066]';
                                if (m === 'JW SVI') activeColor = 'bg-[#0F766E] text-white border-[#0F766E]';
                                if (m === 'Local Volatility') activeColor = 'bg-[#EAB308] text-white border-[#EAB308]';
                                if (m === 'Stochastic Volatility') activeColor = 'bg-[#E20000] text-white border-[#E20000]';

                                return (
                                    <button 
                                        key={m}
                                        onClick={() => setVolMethod(m)}
                                        className={`px-3 py-2 text-xs font-bold rounded border transition text-left flex justify-between items-center ${volMethod === m ? activeColor : 'bg-slate-50 text-slate-500 border-slate-200 hover:border-slate-300'}`}
                                    >
                                        {m}
                                        {volMethod === m && <Activity size={12}/>}
                                    </button>
                                )
                            })}
                        </div>
                     </div>

                     {/* Product / Date from DB */}
                     <div className="bg-white p-4 rounded-lg shadow-md border border-slate-200">
                        <h3 className="font-bold text-[#000066] mb-3 flex items-center gap-2 text-sm uppercase">
                            <Database size={14}/> CFFEX Surface DB
                        </h3>
                        <div className="space-y-3">
                            <div>
                              <div className="text-[10px] font-bold text-slate-400 uppercase mb-1">Product</div>
                              <div className="flex flex-wrap gap-1">
                                {(['HO','IO','MO'] as ProductCode[]).map(p => (
                                  <button key={p} onClick={() => setProduct(p)}
                                    className={`px-2 py-1 text-[10px] font-bold rounded border ${product===p ? 'bg-[#000066] text-white border-[#000066]' : 'bg-slate-50 text-slate-500 border-slate-200'}`}>
                                    {PRODUCT_LABEL[p]}
                                  </button>
                                ))}
                              </div>
                            </div>
                            <div>
                              <div className="text-[10px] font-bold text-slate-400 uppercase mb-1">Trade Date</div>
                              <select
                                className="w-full text-xs border border-slate-200 rounded px-2 py-1.5 font-mono"
                                value={tradeDate}
                                onChange={e => setTradeDate(e.target.value)}
                              >
                                {availableDates.slice().reverse().map(d => (
                                  <option key={d} value={d}>{d}</option>
                                ))}
                              </select>
                            </div>
                            {surfaceMeta && (
                              <div className="text-[10px] text-slate-500 space-y-1">
                                <div className="flex justify-between"><span>Slices</span><span className="font-mono font-bold">{surfaceMeta.n_slices}</span></div>
                                <div className="flex justify-between"><span>IV quotes</span><span className="font-mono font-bold">{surfaceMeta.n_iv}</span></div>
                                <div className="flex justify-between"><span>ATM IV (near)</span><span className="font-mono font-bold">{(100*(surfaceMeta.slices[0]?.atm_iv ?? 0)).toFixed(1)}%</span></div>
                              </div>
                            )}
                            {surfaceLoading && <div className="text-[10px] text-slate-400">Loading surface…</div>}
                            {apiError && <div className="text-[10px] text-red-600 break-all">{apiError}</div>}
                        </div>
                     </div>

                     {/* 2. Surface Info (Dynamic Formulas) */}
                     <div className="bg-white p-6 rounded-lg shadow-md border border-slate-200">
                        <h3 className="font-bold text-[#000066] mb-4 flex items-center gap-2"><Grid size={18}/> Surface Info</h3>
                        
                        <div className="mb-4">
                            <div className="text-xs font-bold text-slate-400 uppercase mb-1">Model Logic</div>
                            <div className="font-serif italic text-slate-700 bg-slate-50 p-2 rounded border border-slate-200 text-center">
                                {volMethod === 'JW SVI' && (
                                    <div className="flex flex-col">
                                        <span className="text-[10px] text-slate-400 not-italic font-sans mb-1">Gatheral–Jacquier JW</span>
                                        <span className="text-xs">v, ψ, p, c, ṽ → raw SVI</span>
                                    </div>
                                )}
                                {volMethod === 'Cubic Spline' && (
                                    <span>σ(k, t) = Spline(k)</span>
                                )}
                                {volMethod === 'Local Volatility' && (
                                    <div className="flex flex-col">
                                        <span className="text-[10px] text-slate-400 not-italic font-sans mb-1">Dupire's Equation</span>
                                        <span>σ_loc² = (∂C/∂T) / [0.5 K² (∂²C/∂K²)]</span>
                                    </div>
                                )}
                                {volMethod === 'Stochastic Volatility' && (
                                    <div className="flex flex-col">
                                        <span className="text-[10px] text-slate-400 not-italic font-sans mb-1">Heston Dynamics</span>
                                        <span>dS = μSdt + √v SdW_s</span>
                                        <span>dv = κ(θ - v)dt + ξ√v dW_v</span>
                                    </div>
                                )}
                            </div>
                        </div>

                        <p className="text-xs text-slate-500 leading-relaxed">
                            {volMethod === 'JW SVI' && "Fits raw SVI per expiry from market IV (Black-76 vs matching IH/IF/IM futures), stores JW parameters (v, ψ, p, c, ṽ), interpolates total variance across maturities."}
                            {volMethod === 'Cubic Spline' && "Constructs a smooth volatility surface ensuring differentiability across strikes using cubic spline interpolation on implied variance slices."}
                            {volMethod === 'Local Volatility' && "Calibrates the instantaneous volatility surface consistent with European vanilla prices, enabling accurate pricing of path-dependent barriers."}
                            {volMethod === 'Stochastic Volatility' && "Models volatility as a random process with mean reversion (Kappa) and vol-of-vol (Xi) to capture heavy tails and smile dynamics."}
                        </p>

                        {volMethod === 'JW SVI' && surfaceMeta && surfaceMeta.slices[0] && (
                          <div className="mt-3 text-[10px] font-mono text-slate-600 bg-slate-50 border border-slate-100 rounded p-2 space-y-0.5">
                            <div className="font-bold text-slate-500">Near expiry JW</div>
                            <div>v={surfaceMeta.slices[0].jw.v.toFixed(4)} ψ={surfaceMeta.slices[0].jw.psi.toFixed(4)}</div>
                            <div>p={surfaceMeta.slices[0].jw.p.toFixed(4)} c={surfaceMeta.slices[0].jw.c.toFixed(4)}</div>
                            <div>ṽ={surfaceMeta.slices[0].jw.vtild.toFixed(4)} rmse={surfaceMeta.slices[0].rmse.toFixed(5)}</div>
                          </div>
                        )}
                     </div>

                     {/* 3. Underlying Info */}
                     <div className="bg-white p-4 rounded-lg shadow-md border border-slate-200">
                        <h3 className="font-bold text-[#000066] mb-3 flex items-center gap-2 text-sm uppercase">
                            <Database size={14}/> Underlying Info
                        </h3>
                        
                        <div className="space-y-3">
                            <div className="flex justify-between items-center border-b border-slate-100 pb-2">
                                <span className="text-xs font-bold text-slate-500">Asset</span>
                                <div className="flex items-center gap-1">
                                    <Globe size={12} className="text-slate-400"/>
                                    <span className="text-sm font-bold text-slate-700">{PRODUCT_LABEL[product]}</span>
                                </div>
                            </div>
                            <div className="flex justify-between items-center border-b border-slate-100 pb-2">
                                <span className="text-xs font-bold text-slate-500">Forward Proxy</span>
                                <span className="text-sm font-mono font-bold text-[#000066]">{spotPrice.toFixed(2)}</span>
                            </div>
                            <div className="flex justify-between items-center">
                                <span className="text-xs font-bold text-slate-500">Data Source</span>
                                <div className={`flex items-center gap-1 px-2 py-0.5 rounded border ${apiStatus==='live' ? 'bg-green-50 text-green-700 border-green-200' : 'bg-amber-50 text-amber-700 border-amber-200'}`}>
                                    <Server size={10}/>
                                    <span className="text-[10px] font-bold">{apiStatus==='live' ? 'Wind SQLite API' : apiStatus==='loading' ? 'Connecting…' : 'Offline fallback'}</span>
                                </div>
                            </div>
                        </div>
                     </div>

                </div>
            </div>
        </div>
        
        {/* --- TAB 4: BOOSTER RESEARCH --- */}
        <div className={activeTab === ActiveTab.RESEARCH ? 'block' : 'hidden'}>
             <BoosterResearch 
                volModel={volModel}
                volMethod={volMethod}
                onMethodChange={setVolMethod}
             />
        </div>

      </main>
    </div>
  );
};

export default App;
