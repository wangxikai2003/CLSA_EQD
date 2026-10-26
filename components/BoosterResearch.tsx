
import React, { useState, useEffect, useMemo, useRef } from 'react';
import Plot from 'react-plotly.js';
import { OptionType, PositionType, VolSurfaceModel, OptionLeg, GreekResult, InterpolationMethod } from '../types';
import { calculateBS, generateAnalysisData } from '../services/pricingEngine';
import { simulateWorstOfPrice } from '../services/monteCarlo';
import { PayoffChart, GreeksChart } from './Charts';
import { Settings, RefreshCw, BarChart2, Layers, Edit3, Eye, Move3d, Compass, Grid3X3, Sun, AlignJustify, TrendingUp, Calculator, Sigma, Info, Activity, Maximize2, ZoomIn, MoveVertical, Shield, Clock, RotateCcw, Monitor, Split, EyeOff, Link2 } from 'lucide-react';

interface Props {
  // Removed spot and r from props to decouple
  volModel?: VolSurfaceModel;
  volMethod?: InterpolationMethod;
  onMethodChange?: (m: InterpolationMethod) => void;
}

enum AnalysisMode {
    STRIKE_SENSITIVITY = 'Strike Sensitivity',
    QUANTITY_SENSITIVITY = 'Quantity Sensitivity'
}

type GreekType = 'price' | 'delta' | 'gamma' | 'vega' | 'skew_risk' | 'correlation_risk';

type GreekTypeKeys = 'price' | 'delta' | 'gamma' | 'vega';

// Hook to throttle values
function useThrottle<T>(value: T, limit: number): T {
  const [throttledValue, setThrottledValue] = useState(value);
  const lastRan = useRef(Date.now());

  useEffect(() => {
    const handler = setTimeout(function() {
      if (Date.now() - lastRan.current >= limit) {
        setThrottledValue(value);
        lastRan.current = Date.now();
      }
    }, limit - (Date.now() - lastRan.current));

    return () => {
      clearTimeout(handler);
    };
  }, [value, limit]);

  return throttledValue;
}

const BoosterResearch: React.FC<Props> = ({ volModel, volMethod, onMethodChange }) => {
  // --- INTERNAL Market Params (Independent of Tab 1) ---
  const [spot, setSpot] = useState(6600);
  const [r, setR] = useState(0.05);

  // --- Independent Variables ---
  const [qty1, setQty1] = useState(1.0);
  const [qty2, setQty2] = useState(1.0);
  
  // Volatility State
  const [volMode, setVolMode] = useState<'flat' | 'surface'>('flat');
  const [iv, setIv] = useState(0.20);
  const [volShift, setVolShift] = useState(0.0);
  const [skewSlope, setSkewSlope] = useState(0.0); 

  // Correlation State (Environment 2)
  const [structureMode, setStructureMode] = useState<'single' | 'worst-of'>('single');
  const [rho, setRho] = useState(0.5);

  // Strikes
  const [kHighPct, setKHighPct] = useState(1.05);     
  const [kMidRightPct, setKMidRightPct] = useState(1.00); 
  const [kMidLeftPct, setKMidLeftPct] = useState(1.00);   
  const [kLowPct, setKLowPct] = useState(0.85);       

  // Maturity
  const [maturity, setMaturity] = useState(0.25);

  // Decomposition State
  const [showDecomposition, setShowDecomposition] = useState(false);
  const [decompOpacity, setDecompOpacity] = useState(0.5);
  const [hiddenLegIds, setHiddenLegIds] = useState<Set<string>>(new Set());

  // Chart View State (Independent Controls)
  // Metric/Payoff use Range/Scale. 
  // IV uses Range (Eye X) and Scale (Eye Y).
  const defaultViewState = {
      metric: { range: 0.20, scale: 1.1 },
      payoff: { range: 0.20, scale: 1.1 },
      iv: { range: 1.5, scale: 1.4 } // Range = Eye X, Scale = Eye Y
  };
  const [viewState, setViewState] = useState(defaultViewState);
  const [activeViewTab, setActiveViewTab] = useState<'metric' | 'payoff' | 'iv'>('metric');

  // Chart Mode
  const [mode, setMode] = useState<AnalysisMode>(AnalysisMode.STRIKE_SENSITIVITY);
  const [greekType, setGreekType] = useState<GreekType>('price'); // Default to Price

  const [planeOpacity, setPlaneOpacity] = useState(0.2); 
  const [surfaceOpacity, setSurfaceOpacity] = useState(0.9); 
  const [showGrid, setShowGrid] = useState(false); 
  const [showZContours, setShowZContours] = useState(true); 
  
  // Camera State (Main 3D Plot)
  const [camera, setCamera] = useState({ x: 1.8, y: 1.8, z: 0.8 });

  // Optimization
  const [revision, setRevision] = useState(0); 
  const [zRange, setZRange] = useState({ min: 0, max: 0 });

  // Helper to get Volatility (Now includes Slope for Flat mode)
  const getVol = (k: number, t: number) => {
      if (volMode === 'flat') {
          // IV_skew = IV_base + Slope * (Moneyness - 1)
          const moneyness = k / spot;
          return iv + skewSlope * (moneyness - 1);
      }
      return (volModel ? volModel.predict(k, t) : 0.2) + volShift;
  };

  // --- Derived Data ---
  
  const legs = useMemo(() => {
      const kHigh = spot * kHighPct;
      const kMidRight = spot * kMidRightPct;
      const kMidLeft = spot * kMidLeftPct;
      const kLow = spot * kLowPct;

      return [
          { id: '1', type: OptionType.CALL, position: PositionType.LONG, strike: kMidRight, quantity: qty1, iv: getVol(kMidRight, maturity), t: maturity, r },
          { id: '2', type: OptionType.CALL, position: PositionType.SHORT, strike: kHigh, quantity: qty1, iv: getVol(kHigh, maturity), t: maturity, r },
          { id: '3', type: OptionType.PUT, position: PositionType.SHORT, strike: kMidLeft, quantity: qty2, iv: getVol(kMidLeft, maturity), t: maturity, r },
          { id: '4', type: OptionType.PUT, position: PositionType.LONG, strike: kLow, quantity: qty2, iv: getVol(kLow, maturity), t: maturity, r },
      ];
  }, [spot, kHighPct, kMidRightPct, kMidLeftPct, kLowPct, qty1, qty2, maturity, r, iv, volMode, volModel, volShift, skewSlope]);

  // Derived: Legs to show in decomposition
  const decompositionLegs = useMemo(() => {
      if (!showDecomposition) return undefined;
      return legs.filter(l => !hiddenLegIds.has(l.id));
  }, [legs, showDecomposition, hiddenLegIds]);

  // --- Analysis Data for METRIC Chart (Uses metric view range) ---
  const metricData = useMemo(() => {
      const range = viewState.metric.range;
      // 1. Calculate Total Strategy Data (supports worst-of now)
      const baseData = generateAnalysisData(legs, spot, range, volShift, volMode === 'flat' ? skewSlope : 0, structureMode === 'worst-of', rho);
      
      // 2. Calculate Individual Leg Data for Decomposition
      // Fix: Pass isWorstOf and rho to decomposition too if we want to see component contribution
      const legDatasets = legs.map(leg => generateAnalysisData([leg], spot, range, volShift, volMode === 'flat' ? skewSlope : 0, structureMode === 'worst-of', rho));

      // 3. Merge
      return baseData.map((point, i) => {
          const merged: any = { ...point };
          legs.forEach((leg, idx) => {
              const legPoint = legDatasets[idx][i];
              if (legPoint) {
                  merged[`totalPrice_${leg.id}`] = legPoint.totalPrice;
                  merged[`delta_${leg.id}`] = legPoint.delta;
                  merged[`gamma_${leg.id}`] = legPoint.gamma;
                  merged[`vega_${leg.id}`] = legPoint.vega;
                  merged[`theta_${leg.id}`] = legPoint.theta;
                  merged[`skewRisk_${leg.id}`] = legPoint.skewRisk;
                  merged[`correlationRisk_${leg.id}`] = legPoint.correlationRisk;
              }
          });
          return merged;
      });
  }, [legs, spot, volShift, skewSlope, volMode, viewState.metric.range, structureMode, rho]);

  // --- Analysis Data for PAYOFF Chart (Uses payoff view range) ---
  const payoffChartData = useMemo(() => {
      const range = viewState.payoff.range;
      // 1. Base Data
      const baseData = generateAnalysisData(legs, spot, range, volShift, volMode === 'flat' ? skewSlope : 0, structureMode === 'worst-of', rho);
      
      // 2. Leg Data for Decomposition
      const legDatasets = legs.map(leg => generateAnalysisData([leg], spot, range, volShift, volMode === 'flat' ? skewSlope : 0));

      return baseData.map((point, i) => {
          const merged: any = { ...point };
          legs.forEach((leg, idx) => {
              const legPoint = legDatasets[idx][i];
              if (legPoint) {
                  merged[`pnl_${leg.id}`] = legPoint.pnl;
                  merged[`intrinsicValue_${leg.id}`] = legPoint.intrinsicValue;
              }
          });
          return merged;
      });
  }, [legs, spot, volShift, skewSlope, volMode, viewState.payoff.range, structureMode, rho]);

  // --- Dynamic Total Metric Calculation (Strategy Total) ---
  const currentTotalMetric = useMemo(() => {
      let total = 0;
      
      // If Correlation Risk, we need to compare Basket vs Single
      if ((greekType as string) === 'correlation_risk' || structureMode === 'worst-of') {
           const centerIdx = Math.floor(metricData.length / 2);
           const centerPt = metricData[centerIdx];
           
           if (centerPt) {
               if (greekType === 'price') return centerPt.totalPrice;
               if ((greekType as string) === 'correlation_risk') return centerPt.correlationRisk;
               if ((greekType as string) === 'skew_risk') return centerPt.skewRisk;
           } else {
               // Fallback if metricData empty
               return 0;
           }
      }

      // Single Asset Analytic Logic
      legs.forEach(leg => {
          const bs = calculateBS(spot, leg.strike, maturity, r, leg.iv, leg.type);
          const direction = leg.position === PositionType.LONG ? 1 : -1;
          
          if ((greekType as string) === 'skew_risk') {
              const ivZero = volMode === 'flat' ? iv : leg.iv;
              const bsZero = calculateBS(spot, leg.strike, maturity, r, Math.max(0.001, ivZero), leg.type);
              total += (bs.price - bsZero.price) * direction * leg.quantity;
          } else if ((greekType as string) === 'correlation_risk') {
              // Should use Metric Data logic above
              total = 0; 
          } else {
              const key = greekType === 'price' ? 'price' : greekType;
              // @ts-ignore
              total += bs[key] * direction * leg.quantity;
          }
      });
      return total;
  }, [legs, spot, maturity, r, greekType, volMode, iv, metricData, structureMode]);

  const metricChartKey = useMemo(() => {
      switch (greekType) {
          case 'price': return 'totalPrice';
          case 'skew_risk' as any: return 'skewRisk';
          case 'correlation_risk' as any: return 'correlationRisk';
          default: return greekType;
      }
  }, [greekType]);

  const metricLabel = useMemo(() => {
      switch (greekType) {
          case 'price': return 'Total Price ($)';
          case 'skew_risk' as any: return 'Skew Risk ($)';
          case 'correlation_risk' as any: return 'Correlation Risk ($)';
          case 'delta': return 'Total Delta';
          case 'gamma': return 'Total Gamma';
          case 'vega': return 'Total Vega';
          default: return greekType;
      }
  }, [greekType]);


  // --- Leg Visualization Data ---
  const legVisuals = useMemo(() => {
      return legs.map(leg => {
          const isLong = leg.position === PositionType.LONG;
          let metric = 0;
          
          if ((greekType as string) === 'skew_risk') {
              const ivZero = volMode === 'flat' ? iv : leg.iv;
              const bsCurrent = calculateBS(spot, leg.strike, maturity, r, leg.iv, leg.type);
              const bsZero = calculateBS(spot, leg.strike, maturity, r, Math.max(0.001, ivZero), leg.type);
              metric = (bsCurrent.price - bsZero.price) * (isLong ? 1 : -1) * leg.quantity;
          } else if ((greekType as string) === 'correlation_risk') {
              // Explicitly calculate individual leg correlation risk:
              // Risk = Price(Single Leg) - Price(WorstOf 1 Leg)
              // Note: WorstOf 1 Leg on basket vs 1 Leg on Single is Price(Min(S1,S2)) vs Price(S).
              const bsPrice = calculateBS(spot, leg.strike, maturity, r, leg.iv, leg.type).price;
              const mcPrice = simulateWorstOfPrice([leg], spot, leg.iv, r, maturity, rho, 400, 1337);
              // INVERTED definition: High Correlation (BS) - Low Correlation (MC)
              metric = (bsPrice - mcPrice) * (isLong ? 1 : -1) * leg.quantity;
          } else {
              const bs = calculateBS(spot, leg.strike, maturity, r, leg.iv, leg.type);
              const key = greekType as keyof GreekResult;
              metric = bs[key] * (isLong ? 1 : -1) * leg.quantity;
          }

          let role = '';
          if(leg.type === OptionType.CALL && isLong) role = 'Upside Entry';
          if(leg.type === OptionType.CALL && !isLong) role = 'Upside Cap';
          if(leg.type === OptionType.PUT && !isLong) role = 'Downside Entry';
          if(leg.type === OptionType.PUT && isLong) role = 'Protection';

          return { 
              id: leg.id,
              type: leg.position + ' ' + leg.type, 
              strikeStr: `${((leg.strike/spot)*100).toFixed(1)}%`, 
              strikeVal: leg.strike, 
              optType: leg.type, 
              isLong, 
              qty: leg.quantity, 
              role,
              metric 
          };
      });
  }, [legs, greekType, spot, maturity, r, volMode, iv, rho]); // Added rho dependency

  const toggleLegVisibility = (id: string) => {
      const next = new Set(hiddenLegIds);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      setHiddenLegIds(next);
  };

  // --- Boundary Condition Logic (Z=0) ---
  const boundaryInfo = useMemo(() => {
      // Boundary logic simpler for price/greeks. For correlation risk it's complex, so maybe hide.
      if ((greekType as string) === 'correlation_risk') return null;
      if (legVisuals.length < 4) return null;
      
      const g0 = legVisuals[0].metric; 
      const g1 = legVisuals[1].metric; 
      const g2 = legVisuals[2].metric; 
      const g3 = legVisuals[3].metric; 
      
      let greekLabel = "";
      let symbol = "";

      if (greekType === 'price') {
          greekLabel = "Cost";
          symbol = "PV";
      } else if ((greekType as string) === 'skew_risk') {
          greekLabel = 'Skew Risk';
          symbol = 'SR';
      } else {
          greekLabel = greekType.charAt(0).toUpperCase() + greekType.slice(1);
          symbol = greekType === 'vega' ? 'ν' : (greekType === 'delta' ? 'Δ' : 'Γ');
      }

      if (mode === AnalysisMode.QUANTITY_SENSITIVITY) {
          const safeQty1 = qty1 === 0 ? 1 : qty1;
          const safeQty2 = qty2 === 0 ? 1 : qty2;
          
          const unit_g0 = g0 / safeQty1;
          const unit_g1 = g1 / safeQty1;
          const unit_g2 = g2 / safeQty2;
          const unit_g3 = g3 / safeQty2;

          const A = unit_g0 + unit_g1; 
          const B = unit_g2 + unit_g3; 
          
          const slope = Math.abs(B) > 1e-9 ? -A / B : Infinity;
          const isVertical = Math.abs(B) < 1e-9;
          
          return {
              type: 'linear',
              eq: isVertical ? `x = 0` : `y = ${slope.toFixed(4)}x`,
              desc: `Zero-${greekLabel} Quantity Iso-Line`,
              derivation: [
                  { label: "Condition", expr: `Total ${greekLabel} (Z) = 0` },
                  { label: "Master Eq", expr: `Q₁·α + Q₂·β = 0` },
                  { label: "Coeff α", expr: `${symbol}(LC) + ${symbol}(SC) = ${A.toFixed(4)}` },
                  { label: "Coeff β", expr: `${symbol}(SP) + ${symbol}(LP) = ${B.toFixed(4)}` },
              ]
          };
      } else {
          const constant = g0 + g2;
          return {
              type: 'implicit',
              eq: `Q₁·${symbol}(SC) + Q₂·${symbol}(LP) = ${(-constant).toFixed(2)}`,
              desc: `Zero-${greekLabel} Strike Boundary`,
              derivation: [
                   { label: "Condition", expr: `Total ${greekLabel} (Z) = 0` },
                   { label: "Master Eq", expr: `Q₁·${symbol}(K_high) + Q₂·${symbol}(K_low) + C = 0` },
                   { label: "Constant C", expr: `Q₁·${symbol}(LC) + Q₂·${symbol}(SP) = ${constant.toFixed(4)}` },
                   { label: "Solution", expr: `Q₁·${symbol}(x) + Q₂·${symbol}(y) = -C` }
              ]
          };
      }
  }, [legVisuals, mode, qty1, qty2, greekType]);

  // --- IV Surface Mini Data ---
  const ivSurfaceMiniData = useMemo(() => {
      const xs: number[] = [];
      const ys: number[] = [];
      const zs: number[][] = [];
      
      const stepsK = 15;
      const stepsT = 10;
      
      // Fixed Range for Visualization Consistency so Rotation is clear
      // Do not use viewState.iv.range here as that is now for Camera Rotation
      const range = 0.25; 
      const startK = 1 - range;
      const endK = 1 + range;
      
      for(let i=0; i<=stepsK; i++) xs.push(spot * (startK + ((endK - startK) * i / stepsK)));
      for(let j=0; j<=stepsT; j++) ys.push(0.1 + (0.9 * j / stepsT));
      
      for(let j=0; j<=stepsT; j++) {
          const row: number[] = [];
          for(let i=0; i<=stepsK; i++) {
              row.push(getVol(xs[i], ys[j]));
          }
          zs.push(row);
      }
      return { x: xs, y: ys, z: zs };
  }, [spot, volMode, iv, skewSlope, volModel, volShift]);


  // --- 3D Calculation Params ---
  const calcParams = useMemo(() => {
      return {
          spot, r, iv, volShift, volMode, volModel, 
          qty1: mode === AnalysisMode.QUANTITY_SENSITIVITY ? 0 : qty1,
          qty2: mode === AnalysisMode.QUANTITY_SENSITIVITY ? 0 : qty2,
          kHighPct, kMidRightPct, kMidLeftPct, kLowPct, maturity, mode, greekType, skewSlope,
          structureMode, rho
      };
  }, [spot, r, iv, volShift, volMode, volModel, qty1, qty2, kHighPct, kMidRightPct, kMidLeftPct, kLowPct, maturity, mode, greekType, skewSlope, structureMode, rho]);

  const throttledParams = useThrottle(calcParams, 50);

  // --- CONSTANTS & BUFFERS (ZERO GC) ---
  const STEPS = 24;
  
  // 1. Static Grids (Pre-calculated once)
  const staticGrids = useMemo(() => {
      const sX: number[] = [];
      const sXLabels: string[] = [];
      const sY: number[] = [];
      const sYLabels: string[] = [];
      
      for(let i=0; i<=STEPS; i++) {
          const valX = 1.50 - (0.49 * i / STEPS);
          sX.push(valX);
          sXLabels.push((valX*100).toFixed(1)+'%');

          const valY = 0.99 - (0.49 * i / STEPS);
          sY.push(valY);
          sYLabels.push((valY*100).toFixed(1)+'%');
      }

      const qX: number[] = [];
      const qLabels: string[] = [];
      for(let i=0; i<=STEPS; i++) {
          const val = 0.1 + (4.9 * i / STEPS);
          qX.push(val);
          qLabels.push(val.toFixed(2));
      }

      return {
          strike: { x: sX, xLbl: sXLabels, y: sY, yLbl: sYLabels },
          qty: { vals: qX, labels: qLabels }
      };
  }, []);

  // 2. Mutable Z-Buffer (Allocated ONCE)
  const zBuffer = useRef<number[][]>(
    Array.from({ length: STEPS + 1 }, () => new Array(STEPS + 1).fill(0))
  );

  // 3. Mutable Zero Plane (Allocated ONCE)
  const zeroPlaneBuffer = useRef<number[][]>(
    Array.from({ length: STEPS + 1 }, () => new Array(STEPS + 1).fill(0))
  );

  // --- CALCULATION EFFECT (WRITES TO BUFFER) ---
  useEffect(() => {
    const { 
        spot, r, iv, volShift, volMode, volModel, 
        qty1, qty2, kHighPct, kMidRightPct, kMidLeftPct, kLowPct, maturity, mode, greekType, skewSlope,
        structureMode, rho
    } = throttledParams;

    const getVolInner = (k: number, t: number) => {
        if (volMode === 'flat') {
            const m = k/spot;
            return iv + skewSlope * (m - 1);
        }
        return (volModel ? volModel.predict(k, t) : 0.2) + volShift;
    };

    // Helper: Compute Greek or Skew Risk or Correlation Risk
    const calcG = (k: number, type: OptionType, isLong: boolean): number => {
        const v = getVolInner(k, maturity);
        const leg: OptionLeg = { 
            id: 'temp', type, position: isLong ? PositionType.LONG : PositionType.SHORT, 
            strike: k, quantity: 1, iv: v, t: maturity, r 
        };

        // Correlation Risk Calculation (Worst Of MC - Single BS)
        if ((greekType as string) === 'correlation_risk') {
             // 1. BS Single Price
             const bs = calculateBS(spot, k, maturity, r, v, type);
             // 2. Worst Of MC Price
             // Use 400 simulations with seeded RNG for 3D speed/smoothness
             const mcPrice = simulateWorstOfPrice([leg], spot, v, r, maturity, rho, 400, 1337);
             
             // INVERTED definition: High Correlation (BS) - Low Correlation (MC)
             return bs.price - mcPrice;
        }

        // Price in Worst-Of Mode
        if (structureMode === 'worst-of' && greekType === 'price') {
             const mcPrice = simulateWorstOfPrice([leg], spot, v, r, maturity, rho, 400, 1337);
             return mcPrice;
        }

        // Skew Risk Calculation
        if ((greekType as string) === 'skew_risk') {
            const bsCurrent = calculateBS(spot, k, maturity, r, v, type);
            // Zero Slope IV
            const vZero = volMode === 'flat' ? iv : v; // In surface mode, assume no change
            const bsZero = calculateBS(spot, k, maturity, r, Math.max(0.001, vZero), type);
            return (bsCurrent.price - bsZero.price) * (isLong ? 1 : -1);
        } 
        
        // Standard Greeks (Fallback to BS even for Worst-Of for now to avoid freezing)
        // Note: Ideally Delta/Gamma would use Finite Diff MC, but that's too slow for real-time 3D.
        const res = calculateBS(spot, k, maturity, r, v, type);
        const key = greekType as keyof GreekResult;
        return res[key] * (isLong ? 1 : -1);
    };

    let minZ = Infinity;
    let maxZ = -Infinity;
    const buffer = zBuffer.current; 

    if (mode === AnalysisMode.STRIKE_SENSITIVITY) {
        const xData = staticGrids.strike.x;
        const yData = staticGrids.strike.y;

        const k_mr = spot * kMidRightPct;
        const k_ml = spot * kMidLeftPct;
        
        const g_mr = calcG(k_mr, OptionType.CALL, true); 
        const g_ml = calcG(k_ml, OptionType.PUT, false); 

        const fixedPart = (g_mr * qty1) + (g_ml * qty2);

        // Vectorized Calc for X Axis
        const xValues = new Float32Array(xData.length);
        for(let i=0; i<xData.length; i++) {
            const k_h = spot * xData[i];
            xValues[i] = calcG(k_h, OptionType.CALL, false);
        }

        // Vectorized Calc for Y Axis
        const yValues = new Float32Array(yData.length);
        for(let i=0; i<yData.length; i++) {
            const k_l = spot * yData[i];
            yValues[i] = calcG(k_l, OptionType.PUT, true);
        }

        for(let y=0; y<yData.length; y++) {
            const valY = yValues[y] * qty2; 
            const rowRef = buffer[y]; 
            for(let x=0; x<xData.length; x++) {
                const val = fixedPart + (xValues[x] * qty1) + valY;
                if(val < minZ) minZ = val;
                if(val > maxZ) maxZ = val;
                rowRef[x] = val; 
            }
        }

    } else if (mode === AnalysisMode.QUANTITY_SENSITIVITY) {
        const xData = staticGrids.qty.vals;
        const yData = staticGrids.qty.vals;
        
        const k_h = spot * kHighPct;
        const k_mr = spot * kMidRightPct;
        const k_ml = spot * kMidLeftPct;
        const k_l = spot * kLowPct;
        
        const g1 = calcG(k_mr, OptionType.CALL, true);  
        const g2 = calcG(k_h, OptionType.CALL, false);  
        const g3 = calcG(k_ml, OptionType.PUT, false);  
        const g4 = calcG(k_l, OptionType.PUT, true);    

        const coeffQty1 = g1 + g2;
        const coeffQty2 = g3 + g4;

        const yContribs = new Float32Array(yData.length);
        for (let y = 0; y < yData.length; y++) {
            yContribs[y] = yData[y] * coeffQty2;
        }

        for(let y=0; y<yData.length; y++) {
            const valFromQty2 = yContribs[y];
            const rowRef = buffer[y]; 
            for(let x=0; x<xData.length; x++) {
                const total = (xData[x] * coeffQty1) + valFromQty2;
                if(total < minZ) minZ = total;
                if(total > maxZ) maxZ = total;
                rowRef[x] = total; 
            }
        }
    }

    setZRange({ min: minZ, max: maxZ });
    setRevision(r => r + 1);

  }, [throttledParams, staticGrids]); 


  // --- VISUALIZATION PROPS ---

  const dynamicColorscale = useMemo(() => {
     const { min, max } = zRange;
     if (Math.abs(max - min) < 0.00001) {
        return [[0, min >= 0 ? '#10b981' : '#E20000'], [1, min >= 0 ? '#10b981' : '#E20000']];
    } else {
        const range = max - min;
        const zeroPos = (0 - min) / range;
        const clampedZero = Math.max(0, Math.min(1, zeroPos));
        let scale: (string|number)[][] = [[0, '#E20000'], [clampedZero, '#0000FF'], [1, '#10b981']];
        if (clampedZero <= 0.01) scale = [[0, '#0000FF'], [1, '#10b981']];
        else if (clampedZero >= 0.99) scale = [[0, '#E20000'], [1, '#0000FF']];
        return scale;
    }
  }, [zRange]);

  const plotData = useMemo(() => {
    const isStrikeMode = mode === AnalysisMode.STRIKE_SENSITIVITY;
    const x = isStrikeMode ? staticGrids.strike.xLbl : staticGrids.qty.labels;
    const y = isStrikeMode ? staticGrids.strike.yLbl : staticGrids.qty.labels;
    
    let label = "";
    if (greekType === 'price') label = "Strategy Price ($)";
    else if ((greekType as string) === 'skew_risk') label = "Skew Risk ($)";
    else if ((greekType as string) === 'correlation_risk') label = "Correlation Risk ($)";
    else label = `Strategy ${greekType.charAt(0).toUpperCase() + greekType.slice(1)}`;

    return [
        {
            z: zBuffer.current, 
            x: x,
            y: y,
            type: 'surface',
            colorscale: dynamicColorscale,
            showscale: true,
            colorbar: { title: label, len: 0.5, thickness: 15 },
            contours: {
                z: { show: showZContours, usecolormap: false, highlightcolor: "#FFF100", project: { z: true }, color: '#FFF100', width: 5 },
                x: { show: showGrid, color: '#333333', width: 1 },
                y: { show: showGrid, color: '#333333', width: 1 }
            } as any,
            opacity: surfaceOpacity,
            name: label,
        },
        {
            z: zeroPlaneBuffer.current, 
            x: x,
            y: y,
            type: 'surface',
            showscale: false,
            opacity: planeOpacity,
            colorscale: [[0, '#FFF100'], [1, '#FFF100']],
            hoverinfo: 'skip',
            name: `Zero Plane`
        }
    ];
  }, [mode, dynamicColorscale, surfaceOpacity, planeOpacity, showGrid, showZContours, staticGrids, greekType]);

  const layout = useMemo(() => {
    let zTitle = "";
    if (greekType === 'price') zTitle = "Strategy Price ($)";
    else if ((greekType as string) === 'skew_risk') zTitle = "Skew Risk ($)";
    else if ((greekType as string) === 'correlation_risk') zTitle = "Correlation Risk ($)";
    else zTitle = `Total ${greekType.charAt(0).toUpperCase() + greekType.slice(1)}`;

    return {
        width: undefined, 
        height: undefined,
        autosize: true,
        margin: { l: 20, r: 20, b: 20, t: 20 },
        datarevision: revision, 
        scene: {
            xaxis: { 
                title: { text: mode === AnalysisMode.STRIKE_SENSITIVITY ? 'High Strike %' : 'Call Qty (Ratio)', font: { size: 12 } },
                tickfont: { size: 10 }
            },
            yaxis: { 
                title: { text: mode === AnalysisMode.STRIKE_SENSITIVITY ? 'Low Strike %' : 'Put Qty (Ratio)', font: { size: 12 } },
                tickfont: { size: 10 }
            },
            zaxis: { title: { text: zTitle }, tickfont: { size: 10 } },
            camera: { eye: camera },
            aspectratio: { x: 1.6, y: 1.6, z: 0.4 }
        },
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        showlegend: false,
        uirevision: 'true', 
    };
  }, [mode, camera, revision, greekType]);

  const updateViewConfig = (key: 'range' | 'scale', value: number) => {
      setViewState(prev => ({
          ...prev,
          [activeViewTab]: { ...prev[activeViewTab], [key]: value }
      }));
  };

  const resetViewConfig = () => {
      setViewState(defaultViewState);
  };

  return (
    <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 min-h-[600px]">
        {/* LEFT: Structure Config (Editable) */}
        <div className="xl:col-span-3 space-y-6">
            <div className="bg-white rounded-lg shadow-md border border-slate-200 p-6">
                 <h2 className="text-xl font-bold text-[#000066] mb-4 flex items-center gap-2">
                    <Edit3 size={20} />
                    Structure Setup
                </h2>
                <div className="space-y-4">
                    {/* Manual Input Group */}
                    <div className="bg-slate-50 p-3 rounded border border-slate-200 space-y-3">
                         <div className="grid grid-cols-2 gap-2">
                             <div className="flex flex-col gap-1">
                                 <label className="text-[10px] font-bold text-slate-500 uppercase">Qty 1 (Calls)</label>
                                 <input 
                                    type="number" step="0.1" 
                                    value={qty1} 
                                    onChange={(e) => setQty1(parseFloat(e.target.value))}
                                    className="w-full border border-slate-300 rounded px-2 py-1 text-sm font-mono font-bold text-[#000066] focus:outline-none focus:ring-2 focus:ring-[#000066]"
                                 />
                             </div>
                             <div className="flex flex-col gap-1">
                                 <label className="text-[10px] font-bold text-slate-500 uppercase">Qty 2 (Puts)</label>
                                 <input 
                                    type="number" step="0.1" 
                                    value={qty2} 
                                    onChange={(e) => setQty2(parseFloat(e.target.value))}
                                    className="w-full border border-slate-300 rounded px-2 py-1 text-sm font-mono font-bold text-[#000066] focus:outline-none focus:ring-2 focus:ring-[#000066]"
                                 />
                             </div>
                         </div>
                         <div className="h-px bg-slate-200 my-2"></div>
                         
                         <div className="grid grid-cols-2 gap-2">
                            <div className="flex flex-col gap-1">
                                <label className="text-[10px] font-bold text-slate-500 uppercase">High Call %</label>
                                <div className="flex items-center gap-1">
                                    <input 
                                        type="number" step="1" 
                                        value={Math.round(kHighPct * 100)} 
                                        onChange={(e) => setKHighPct(parseFloat(e.target.value)/100)}
                                        className="w-full border border-slate-300 rounded px-1 py-1 text-xs font-mono font-bold text-[#000066] focus:outline-none focus:ring-1 focus:ring-[#000066]"
                                    />
                                    <span className="text-[10px] font-bold text-slate-400">%</span>
                                </div>
                            </div>
                            <div className="flex flex-col gap-1">
                                <label className="text-[10px] font-bold text-slate-500 uppercase">Mid Call %</label>
                                <div className="flex items-center gap-1">
                                    <input 
                                        type="number" step="1" 
                                        value={Math.round(kMidRightPct * 100)} 
                                        onChange={(e) => setKMidRightPct(parseFloat(e.target.value)/100)}
                                        className="w-full border border-slate-300 rounded px-1 py-1 text-xs font-mono font-bold text-[#000066] focus:outline-none focus:ring-1 focus:ring-[#000066]"
                                    />
                                    <span className="text-[10px] font-bold text-slate-400">%</span>
                                </div>
                            </div>
                         </div>

                         <div className="grid grid-cols-2 gap-2">
                            <div className="flex flex-col gap-1">
                                <label className="text-[10px] font-bold text-slate-500 uppercase">Mid Put %</label>
                                <div className="flex items-center gap-1">
                                    <input 
                                        type="number" step="1" 
                                        value={Math.round(kMidLeftPct * 100)} 
                                        onChange={(e) => setKMidLeftPct(parseFloat(e.target.value)/100)}
                                        className="w-full border border-slate-300 rounded px-1 py-1 text-xs font-mono font-bold text-[#000066] focus:outline-none focus:ring-1 focus:ring-[#000066]"
                                    />
                                    <span className="text-[10px] font-bold text-slate-400">%</span>
                                </div>
                            </div>
                            <div className="flex flex-col gap-1">
                                <label className="text-[10px] font-bold text-slate-500 uppercase">Low Put %</label>
                                <div className="flex items-center gap-1">
                                    <input 
                                        type="number" step="1" 
                                        value={Math.round(kLowPct * 100)} 
                                        onChange={(e) => setKLowPct(parseFloat(e.target.value)/100)}
                                        className="w-full border border-slate-300 rounded px-1 py-1 text-xs font-mono font-bold text-[#000066] focus:outline-none focus:ring-1 focus:ring-[#000066]"
                                    />
                                    <span className="text-[10px] font-bold text-slate-400">%</span>
                                </div>
                            </div>
                         </div>

                    </div>

                    {/* Visual List */}
                    <div className="space-y-2 pt-2">
                        {legVisuals.map((leg, idx) => {
                            let greekLabel = '';
                            let val = leg.metric;
                            if (greekType === 'price') { greekLabel = 'PV'; }
                            else if ((greekType as string) === 'skew_risk') { greekLabel = 'SR'; }
                            else if ((greekType as string) === 'correlation_risk') { greekLabel = 'CR'; }
                            else if (greekType === 'gamma') { greekLabel = 'Γ'; val = val * 100; }
                            else if (greekType === 'delta') { greekLabel = 'Δ'; }
                            else { greekLabel = 'ν'; }

                            const isHidden = hiddenLegIds.has(leg.id);

                            return (
                                <div key={idx} className={`bg-white p-2 rounded border flex justify-between items-center text-xs shadow-sm transition ${isHidden ? 'border-slate-100 opacity-60' : 'border-slate-200'}`}>
                                    <div className="flex items-center gap-2">
                                        {/* Eye Toggle for Decomposition */}
                                        <button 
                                            onClick={() => toggleLegVisibility(leg.id)}
                                            className={`p-1 rounded hover:bg-slate-100 ${isHidden ? 'text-slate-300' : 'text-slate-500'}`}
                                            title="Toggle in Decomposition"
                                        >
                                            {isHidden ? <EyeOff size={14} /> : <Eye size={14} />}
                                        </button>
                                        
                                        <div className="flex flex-col">
                                            <span className={`font-bold ${leg.isLong ? 'text-green-600' : 'text-[#E20000]'}`}>{leg.type}</span>
                                            <span className="text-[10px] text-slate-400">{leg.role}</span>
                                        </div>
                                    </div>
                                    <div className="text-right">
                                        <div className="font-mono font-bold text-slate-700">{val.toFixed(2)} {greekLabel}</div>
                                        <div className="font-mono text-slate-500 text-[10px]">x{leg.qty.toFixed(2)} @ {leg.strikeStr}</div>
                                    </div>
                                </div>
                            )
                        })}
                    </div>
                </div>
            </div>

            {/* Total Metrics & Chart Display */}
            <div className="bg-white rounded-lg shadow-md border border-slate-200 p-4">
                 <div className="flex justify-between items-center border-b border-slate-100 pb-2 mb-2">
                     <h3 className="text-[10px] font-bold text-slate-500 uppercase flex items-center gap-1">
                        <Activity size={12} className="text-[#000066]" /> {metricLabel}
                     </h3>
                     
                     <div className="flex items-center gap-2">
                         <span className={`text-lg font-mono font-bold ${currentTotalMetric >= 0 ? 'text-[#000066]' : 'text-[#E20000]'}`}>
                            {currentTotalMetric.toFixed(2)}
                         </span>
                     </div>
                 </div>
                 
                 {/* 2D Metric Chart using reusable GreeksChart for Tab 1 styling (with Decomposition) */}
                 <GreeksChart 
                    data={metricData}
                    marketSpot={spot}
                    dataKey={metricChartKey}
                    color="#000066"
                    name={greekType === 'price' ? 'Price' : ((greekType as string) === 'correlation_risk' ? 'Corr Risk' : (greekType.charAt(0).toUpperCase() + greekType.slice(1)))}
                    yScale={viewState.metric.scale}
                    legs={decompositionLegs}
                    legOpacity={decompOpacity}
                 />
                 <div className="text-[9px] text-slate-400 text-right italic mt-1">Metric vs Moneyness (%)</div>
            </div>

            {/* 2D Payoff Chart Mini */}
            <div className="bg-white rounded-lg shadow-md border border-slate-200 p-4">
                <div className="flex justify-between items-center mb-3">
                    <h3 className="text-sm font-bold text-[#000066] flex items-center gap-2">
                        <TrendingUp size={16} /> Payoff at Expiry
                    </h3>
                </div>
                
                <div className="w-full">
                    {/* Plot Intrinsic Value (Gross Payoff) and set Reference Line to 0 (Gross Payoff = 0) */}
                    <PayoffChart 
                        data={payoffChartData} 
                        marketSpot={spot} 
                        yScale={viewState.payoff.scale} 
                        height={200} 
                        dataKey="intrinsicValue"
                        referenceLineY={0}
                        legs={decompositionLegs}
                        legOpacity={decompOpacity}
                    />
                </div>
            </div>

            {/* IV Surface Mini Visualization */}
            <div className="bg-white rounded-lg shadow-md border border-slate-200 p-2 relative h-[250px]">
                <h3 className="absolute top-3 left-3 text-xs font-bold text-[#000066] z-10 flex items-center gap-2 bg-white/80 p-1 rounded">
                    <Grid3X3 size={12}/> IV Surface Map
                </h3>
                <Plot
                    data={[{
                        x: ivSurfaceMiniData.x,
                        y: ivSurfaceMiniData.y,
                        z: ivSurfaceMiniData.z,
                        type: 'surface',
                        colorscale: 'Viridis',
                        showscale: false,
                        opacity: 0.9,
                        contours: {
                            x: { show: true, color: 'white', width: 1 },
                            y: { show: true, color: 'white', width: 1 }
                        } as any
                    }]}
                    layout={{
                        autosize: true,
                        margin: { l: 0, r: 0, b: 0, t: 0 },
                        scene: {
                            xaxis: { 
                                title: { text: 'Strike', font: { size: 10, color: '#64748b' } }, 
                                showticklabels: true, 
                                tickfont: { size: 9, color: '#64748b' },
                                nticks: 3
                            },
                            yaxis: { 
                                title: { text: 'Time', font: { size: 10, color: '#64748b' } }, 
                                showticklabels: true,
                                tickfont: { size: 9, color: '#64748b' },
                                nticks: 3
                            },
                            zaxis: { 
                                title: { text: 'IV', font: { size: 10, color: '#64748b' } }, 
                                showticklabels: true,
                                tickfont: { size: 9, color: '#64748b' },
                                nticks: 3
                            },
                            // Eye X controlled by viewState.iv.range (Camera X)
                            // Eye Y controlled by viewState.iv.scale (Camera Y)
                            // Eye Z fixed at 0.6 for mini view visibility
                            camera: { eye: { x: viewState.iv.range, y: viewState.iv.scale, z: 0.6 } },
                            aspectratio: { x: 1, y: 1, z: 0.4 }
                        },
                        paper_bgcolor: 'rgba(0,0,0,0)',
                        plot_bgcolor: 'rgba(0,0,0,0)',
                    } as any}
                    useResizeHandler={true}
                    style={{ width: '100%', height: '100%' }}
                    config={{ displayModeBar: false }}
                />
            </div>

            {/* Zero Boundary Condition Box */}
            {boundaryInfo && (
                <div className="bg-slate-50 rounded-lg shadow-sm border border-slate-200 p-3">
                    <div className="flex justify-between items-center mb-2">
                        <h4 className="text-[10px] font-bold text-slate-500 uppercase flex items-center gap-1">
                            <Sigma size={10} /> {boundaryInfo.desc}
                        </h4>
                    </div>
                    
                    {/* Analytical Solution Box */}
                    <div className="bg-white border border-slate-200 rounded p-2 text-center mb-3 shadow-sm">
                        <span className="font-mono text-xs font-bold text-[#000066]">{boundaryInfo.eq}</span>
                    </div>

                    {/* Derivation Steps (Theoretical) */}
                    <div className="space-y-2">
                        <h5 className="text-[10px] font-bold text-[#000066] uppercase border-b border-slate-200 pb-1 flex items-center gap-1">
                            <Info size={10} /> Analytical Derivation
                        </h5>
                        <div className="grid grid-cols-1 gap-1.5 pl-1">
                            {boundaryInfo.derivation.map((step, i) => (
                                <div key={i} className="flex justify-between items-center border-b border-slate-100 last:border-0 pb-1 last:pb-0">
                                    <span className="text-[9px] text-slate-400 font-bold">{step.label}</span>
                                    <span className="text-[10px] font-mono text-slate-700">{step.expr}</span>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            )}
        </div>

        {/* MIDDLE: 3D Visualization */}
        <div className="xl:col-span-6">
            <div className="bg-white rounded-lg shadow-md border border-slate-200 p-2 h-full flex flex-col min-h-[600px]">
                <div className="p-4 border-b border-slate-100 flex flex-col gap-4">
                    <div className="flex justify-between items-center">
                        <h3 className="font-bold text-[#000066] flex items-center gap-2">
                            <Layers size={18}/> ATM {greekType === 'price' ? 'Price' : ((greekType as string) === 'skew_risk' ? 'Skew Risk' : ((greekType as string) === 'correlation_risk' ? 'Corr Risk' : (greekType.charAt(0).toUpperCase() + greekType.slice(1))))} Surface
                        </h3>
                        {/* Greek Selector */}
                        <div className="flex flex-wrap bg-slate-100 rounded p-1 gap-0.5">
                             <button 
                                onClick={() => setGreekType('price')}
                                className={`px-2 py-1 text-xs font-bold rounded transition ${greekType === 'price' ? 'bg-[#000066] shadow text-white' : 'text-slate-400 hover:text-slate-600'}`}
                            >Price</button>
                             <button 
                                onClick={() => setGreekType('delta')}
                                className={`px-2 py-1 text-xs font-bold rounded transition ${greekType === 'delta' ? 'bg-[#000066] shadow text-white' : 'text-slate-400 hover:text-slate-600'}`}
                            >Delta</button>
                             <button 
                                onClick={() => setGreekType('gamma')}
                                className={`px-2 py-1 text-xs font-bold rounded transition ${greekType === 'gamma' ? 'bg-[#000066] shadow text-white' : 'text-slate-400 hover:text-slate-600'}`}
                            >Gamma</button>
                             <button 
                                onClick={() => setGreekType('vega')}
                                className={`px-2 py-1 text-xs font-bold rounded transition ${greekType === 'vega' ? 'bg-[#000066] shadow text-white' : 'text-slate-400 hover:text-slate-600'}`}
                            >Vega</button>
                             <button 
                                onClick={() => setGreekType('skew_risk')}
                                className={`px-2 py-1 text-xs font-bold rounded transition ${(greekType as string) === 'skew_risk' ? 'bg-[#000066] shadow text-white' : 'text-slate-400 hover:text-slate-600'}`}
                            >Skew Risk</button>
                            <button 
                                onClick={() => setGreekType('correlation_risk')}
                                className={`px-2 py-1 text-xs font-bold rounded transition ${(greekType as string) === 'correlation_risk' ? 'bg-[#000066] shadow text-white' : 'text-slate-400 hover:text-slate-600'}`}
                            >Corr Risk</button>
                        </div>
                    </div>
                    
                    {/* Mode Selector */}
                    <div className="flex w-full bg-slate-100 rounded p-1">
                        <button 
                            onClick={() => setMode(AnalysisMode.STRIKE_SENSITIVITY)}
                            className={`flex-1 py-1.5 text-xs font-bold rounded transition text-center ${mode === AnalysisMode.STRIKE_SENSITIVITY ? 'bg-white shadow text-[#000066]' : 'text-slate-400'}`}
                        >
                            Strike Sensitivity
                        </button>
                        <button 
                             onClick={() => setMode(AnalysisMode.QUANTITY_SENSITIVITY)}
                             className={`flex-1 py-1.5 text-xs font-bold rounded transition text-center ${mode === AnalysisMode.QUANTITY_SENSITIVITY ? 'bg-white shadow text-[#000066]' : 'text-slate-400'}`}
                        >
                            Quantity Sensitivity
                        </button>
                    </div>
                    
                    {/* Active Basket Indicator */}
                    {structureMode === 'worst-of' && (
                        <div className="bg-blue-50 border border-blue-100 text-[#000066] px-3 py-1.5 rounded text-xs flex items-center justify-between">
                            <span className="font-bold flex items-center gap-1"><Link2 size={12}/> Basket Mode Active</span>
                            <span className="font-mono">Rho = {rho}</span>
                        </div>
                    )}
                </div>
                {/* Main Plot Area */}
                <div className="flex-1 w-full relative">
                     <Plot
                        data={plotData as any}
                        layout={layout as any}
                        useResizeHandler={true}
                        style={{ width: '100%', height: '100%', minHeight: '500px' }}
                        config={{ displayModeBar: true }}
                    />
                </div>
            </div>
        </div>

        {/* RIGHT: Controls */}
        <div className="xl:col-span-3 space-y-6">
             {/* Research Sliders (Existing) */}
             <div className="bg-white rounded-lg shadow-md border border-slate-200 p-6">
                <h3 className="font-bold text-[#000066] mb-4 flex items-center gap-2">
                    <Settings size={18} /> Research Sliders
                </h3>
                
                <div className="space-y-6">
                    {/* Market Params */}
                    <div className="p-3 bg-slate-50 rounded border border-slate-200">
                        <h4 className="text-xs font-bold text-slate-500 uppercase mb-3 flex items-center gap-2">
                            <Activity size={12} className="text-[#000066]" /> Market Params
                        </h4>
                        
                        {/* Spot and Risk-Free */}
                        <div className="grid grid-cols-2 gap-3">
                            <div>
                                <div className="flex justify-between text-xs mb-1">
                                    <span className="text-slate-500 font-bold">Spot</span>
                                    <span className="font-mono text-[#000066] font-bold">{((spot / 6600) * 100).toFixed(2)}%</span>
                                </div>
                                <input 
                                    type="range" min="4000" max="8000" step="10" 
                                    value={spot} onChange={(e) => setSpot(Number(e.target.value))}
                                    className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                                />
                            </div>
                            <div>
                                <div className="flex justify-between text-xs mb-1">
                                    <span className="text-slate-500 font-bold">Risk-Free</span>
                                    <span className="font-mono text-[#000066] font-bold">{(r * 100).toFixed(1)}%</span>
                                </div>
                                <input 
                                    type="range" min="0.0" max="0.2" step="0.005" 
                                    value={r} onChange={(e) => setR(parseFloat(e.target.value))}
                                    className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                                />
                            </div>
                        </div>
                    </div>

                    <div className="p-3 bg-slate-50 rounded border border-slate-200">
                        <h4 className="text-xs font-bold text-slate-500 uppercase mb-3 flex items-center gap-2"><BarChart2 size={12}/> Ratios</h4>
                        <div className="mb-4">
                            <div className="flex justify-between text-xs mb-1">
                                <span>Quantity 1 (Calls)</span>
                                <span className="font-mono font-bold text-[#000066]">{qty1.toFixed(2)}</span>
                            </div>
                            {/* Expanded Range */}
                            <input 
                                type="range" min="0.1" max="5.0" step="0.1"
                                value={qty1} onChange={(e) => setQty1(parseFloat(e.target.value))}
                                className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                            />
                        </div>
                         <div>
                            <div className="flex justify-between text-xs mb-1">
                                <span>Quantity 2 (Puts)</span>
                                <span className="font-mono font-bold text-[#000066]">{qty2.toFixed(2)}</span>
                            </div>
                            {/* Expanded Range */}
                            <input 
                                type="range" min="0.1" max="5.0" step="0.1"
                                value={qty2} onChange={(e) => setQty2(parseFloat(e.target.value))}
                                className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                            />
                        </div>
                    </div>

                    <div className="p-3 bg-slate-50 rounded border border-slate-200">
                        <h4 className="text-xs font-bold text-slate-500 uppercase mb-3 flex items-center gap-2"><Layers size={12}/> Strikes</h4>
                        
                        <div className="mb-3">
                            <div className="flex justify-between text-xs mb-1">
                                <span>High Call %</span>
                                <span className="font-mono font-bold text-[#000066]">{(kHighPct * 100).toFixed(0)}%</span>
                            </div>
                            {/* Expanded Range */}
                            <input 
                                type="range" min="1.00" max="1.50" step="0.01"
                                value={kHighPct} onChange={(e) => setKHighPct(parseFloat(e.target.value))}
                                className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                            />
                        </div>

                         <div className="mb-3">
                            <div className="flex justify-between text-xs mb-1">
                                <span>Mid Call %</span>
                                <span className="font-mono font-bold text-[#000066]">{(kMidRightPct * 100).toFixed(0)}%</span>
                            </div>
                            <input 
                                type="range" min="0.80" max="1.20" step="0.01"
                                value={kMidRightPct} onChange={(e) => setKMidRightPct(parseFloat(e.target.value))}
                                className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                            />
                        </div>

                         <div className="mb-3">
                            <div className="flex justify-between text-xs mb-1">
                                <span>Mid Put %</span>
                                <span className="font-mono font-bold text-[#000066]">{(kMidLeftPct * 100).toFixed(0)}%</span>
                            </div>
                            <input 
                                type="range" min="0.80" max="1.20" step="0.01"
                                value={kMidLeftPct} onChange={(e) => setKMidLeftPct(parseFloat(e.target.value))}
                                className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                            />
                        </div>

                         <div>
                            <div className="flex justify-between text-xs mb-1">
                                <span>Low Put %</span>
                                <span className="font-mono font-bold text-[#000066]">{(kLowPct * 100).toFixed(0)}%</span>
                            </div>
                            {/* Expanded Range */}
                            <input 
                                type="range" min="0.50" max="1.00" step="0.01"
                                value={kLowPct} onChange={(e) => setKLowPct(parseFloat(e.target.value))}
                                className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                            />
                        </div>
                    </div>

                    <div className="p-3 bg-slate-50 rounded border border-slate-200 space-y-1">
                         <h4 className="text-xs font-bold text-slate-500 uppercase mb-2 flex items-center gap-2"><RefreshCw size={12}/> Vol Mode</h4>
                         
                         {/* Vol Mode Toggle */}
                         <div className="flex justify-between items-center mb-1">
                            <span className="text-[10px] font-bold text-slate-400 uppercase">Vol Mode</span>
                            <div className="flex bg-slate-200 rounded p-0.5">
                                <button 
                                    onClick={() => setVolMode('flat')}
                                    className={`px-2 py-0.5 text-[10px] font-bold rounded transition ${volMode === 'flat' ? 'bg-white text-[#000066] shadow' : 'text-slate-500'}`}
                                >Flat</button>
                                <button 
                                    onClick={() => setVolMode('surface')}
                                    className={`px-2 py-0.5 text-[10px] font-bold rounded transition ${volMode === 'surface' ? 'bg-white text-[#000066] shadow' : 'text-slate-500'}`}
                                >Surface</button>
                            </div>
                         </div>

                         {/* Conditional Slider */}
                         {volMode === 'flat' ? (
                            <>
                                <div>
                                    <div className="flex justify-between text-xs mb-1">
                                        <span>Flat Vol</span>
                                        <span className="font-mono font-bold text-[#000066]">{(iv * 100).toFixed(1)}%</span>
                                    </div>
                                    <input 
                                        type="range" min="0.1" max="0.5" step="0.01"
                                        value={iv} onChange={(e) => setIv(parseFloat(e.target.value))}
                                        className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                                    />
                                </div>
                                <div>
                                    <div className="flex justify-between text-xs mb-1">
                                        <span>Skew Slope</span>
                                        <span className="font-mono font-bold text-[#000066]">{skewSlope.toFixed(2)}</span>
                                    </div>
                                    <input 
                                        type="range" min="-0.5" max="0.5" step="0.05"
                                        value={skewSlope} onChange={(e) => setSkewSlope(parseFloat(e.target.value))}
                                        className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                                    />
                                    <span className="text-[9px] text-slate-400 mt-1 block">Rotates surface for Skew Risk calc</span>
                                </div>
                            </>
                         ) : (
                             <>
                                {/* Vol Surface Selector for Tab 4 */}
                                {onMethodChange && volMethod && (
                                    <div className="flex bg-slate-200 p-0.5 rounded my-2">
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
                                                    onClick={() => onMethodChange(m)}
                                                    className={`flex-1 py-1 text-[8px] font-bold rounded transition text-center leading-tight ${volMethod === m ? activeClass : 'text-slate-500 hover:text-slate-700'}`}
                                                >
                                                    {short}
                                                </button>
                                            )
                                        })}
                                    </div>
                                )}
                                <div>
                                    <div className="flex justify-between text-xs mb-1">
                                        <span>Vol Shift</span>
                                        <span className="font-mono font-bold text-[#000066]">{volShift > 0 ? '+' : ''}{(volShift * 100).toFixed(1)}%</span>
                                    </div>
                                    <input 
                                        type="range" min="-0.1" max="0.1" step="0.005"
                                        value={volShift} onChange={(e) => setVolShift(parseFloat(e.target.value))}
                                        className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                                    />
                                </div>
                            </>
                         )}
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

                    {/* Time Horizon (Separated) */}
                    <div className="p-3 bg-slate-50 rounded border border-slate-200">
                        <h4 className="text-xs font-bold text-slate-500 uppercase mb-3 flex items-center gap-2"><Clock size={12}/> Time Horizon</h4>
                        <div>
                            <div className="flex justify-between text-xs mb-1">
                                <span>Maturity (Years)</span>
                                <span className="font-mono font-bold text-[#000066]">{maturity.toFixed(2)}</span>
                            </div>
                            <input 
                                type="range" min="0.1" max="2.0" step="0.1"
                                value={maturity} onChange={(e) => setMaturity(parseFloat(e.target.value))}
                                className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                            />
                        </div>
                    </div>
                </div>
             </div>

             {/* View Controls Component - Left Charts */}
             <div className="bg-white rounded-lg shadow-md border border-slate-200 p-6">
                <div className="flex justify-between items-center mb-4">
                    <h3 className="font-bold text-[#000066] flex items-center gap-2">
                        <Maximize2 size={18} /> View Controls (Left)
                    </h3>
                    <button 
                        onClick={resetViewConfig}
                        className="text-[10px] flex items-center gap-1 bg-slate-100 hover:bg-slate-200 text-slate-600 px-2 py-1 rounded transition border border-transparent"
                        title="Reset Views"
                    >
                        <RotateCcw size={10} /> Reset
                    </button>
                </div>
                
                {/* Independent Control Toggles */}
                <div className="flex bg-slate-100 rounded p-1 mb-4">
                    {(['metric', 'payoff', 'iv'] as const).map(tab => (
                        <button
                            key={tab}
                            onClick={() => setActiveViewTab(tab)}
                            className={`flex-1 py-1.5 text-[10px] font-bold uppercase rounded transition text-center ${activeViewTab === tab ? 'bg-white shadow text-[#000066]' : 'text-slate-400 hover:text-slate-500'}`}
                        >
                            {tab === 'metric' ? 'Metric' : tab === 'payoff' ? 'Payoff' : 'IV Map'}
                        </button>
                    ))}
                </div>

                <div className="grid grid-cols-2 gap-4 bg-slate-50 p-3 rounded border border-slate-200 mb-3">
                    <div>
                         <div className="flex justify-between text-xs mb-1 font-bold text-slate-500 uppercase">
                            <span>{activeViewTab === 'iv' ? 'Camera X' : 'X-Axis Range'}</span>
                            <span>
                                {activeViewTab === 'iv' 
                                    ? viewState.iv.range.toFixed(1) 
                                    : `${(viewState[activeViewTab].range * 100).toFixed(0)}%`
                                }
                            </span>
                         </div>
                         <div className="flex items-center gap-2">
                            {activeViewTab === 'iv' ? <Monitor size={14} className="text-slate-400"/> : <ZoomIn size={14} className="text-slate-400"/>}
                            {activeViewTab === 'iv' ? (
                                <input 
                                    type="range" min="-10" max="10" step="0.1" 
                                    value={viewState.iv.range} 
                                    onChange={(e) => updateViewConfig('range', parseFloat(e.target.value))}
                                    className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none"
                                />
                            ) : (
                                <input 
                                    type="range" min="0.05" max="0.5" step="0.05" 
                                    value={viewState[activeViewTab].range} 
                                    onChange={(e) => updateViewConfig('range', parseFloat(e.target.value))}
                                    className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none"
                                />
                            )}
                         </div>
                    </div>
                    <div>
                         <div className="flex justify-between text-xs mb-1 font-bold text-slate-500 uppercase">
                            <span>{activeViewTab === 'iv' ? 'Camera Y' : 'Y-Axis Scale'}</span>
                            <span>{viewState[activeViewTab].scale.toFixed(1)}{activeViewTab !== 'iv' ? 'x' : ''}</span>
                         </div>
                         <div className="flex items-center gap-2">
                            {activeViewTab === 'iv' ? <Eye size={14} className="text-slate-400"/> : <MoveVertical size={14} className="text-slate-400"/>}
                            {activeViewTab === 'iv' ? (
                                <input 
                                    type="range" min="-10" max="10" step="0.1" 
                                    value={viewState.iv.scale} 
                                    onChange={(e) => updateViewConfig('scale', parseFloat(e.target.value))}
                                    className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none"
                                />
                            ) : (
                                <input 
                                    type="range" min="0.5" max="3.0" step="0.1" 
                                    value={viewState[activeViewTab].scale} 
                                    onChange={(e) => updateViewConfig('scale', parseFloat(e.target.value))}
                                    className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none"
                                />
                            )}
                         </div>
                    </div>
                </div>

                {/* Decomposition Opacity Control */}
                <div className="bg-slate-50 p-3 rounded border border-slate-200">
                    <div className="flex justify-between text-xs mb-1 font-bold text-slate-500 uppercase">
                        <span>Decomposition Opacity</span>
                        <span>{(decompOpacity * 100).toFixed(0)}%</span>
                    </div>
                    <div className="flex items-center gap-3">
                        <button 
                            onClick={() => setShowDecomposition(!showDecomposition)}
                            className={`flex-none text-[10px] flex items-center gap-1 px-3 py-1.5 rounded transition font-bold border ${showDecomposition ? 'bg-[#000066] text-white border-[#000066]' : 'bg-white text-slate-500 border-slate-200 hover:bg-slate-50'}`}
                            title="Toggle Decomposition"
                        >
                            <Split size={14} /> {showDecomposition ? 'Hide' : 'Show'}
                        </button>
                        <input 
                            type="range" min="0.1" max="1" step="0.05" 
                            value={decompOpacity} 
                            onChange={(e) => setDecompOpacity(parseFloat(e.target.value))}
                            disabled={!showDecomposition}
                            className={`w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none ${!showDecomposition ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
                        />
                    </div>
                </div>
             </div>

             {/* 3D View Controls Component */}
             <div className="bg-white rounded-lg shadow-md border border-slate-200 p-6">
                <h3 className="font-bold text-[#000066] mb-4 flex items-center gap-2">
                    <Move3d size={18} /> 3D View Controls
                </h3>
                
                <div className="space-y-4">
                    {/* Surface Appearance Controls */}
                    <div className="p-3 bg-slate-50 rounded border border-slate-200">
                         <h4 className="text-xs font-bold text-slate-500 uppercase mb-3 flex items-center gap-2"><Sun size={12}/> Surface Appearance</h4>
                         
                         {/* Grid Toggle (Cancel Mountain Lines) */}
                        <div className="flex items-center justify-between mb-2">
                            <div className="flex items-center gap-2 text-xs font-bold text-slate-600">
                                <Grid3X3 size={14} />
                                <span>Show Mountain Lines</span>
                            </div>
                            <button 
                                onClick={() => setShowGrid(!showGrid)}
                                className={`w-10 h-5 rounded-full relative transition-colors ${showGrid ? 'bg-[#000066]' : 'bg-slate-300'}`}
                            >
                                <span className={`absolute top-1 left-1 bg-white w-3 h-3 rounded-full transition-transform ${showGrid ? 'translate-x-5' : 'translate-x-0'}`}></span>
                            </button>
                        </div>

                        {/* Z Contours Toggle */}
                        <div className="flex items-center justify-between mb-4">
                            <div className="flex items-center gap-2 text-xs font-bold text-slate-600">
                                <AlignJustify size={14} className="rotate-90"/>
                                <span>Show Level Contours</span>
                            </div>
                            <button 
                                onClick={() => setShowZContours(!showZContours)}
                                className={`w-10 h-5 rounded-full relative transition-colors ${showZContours ? 'bg-[#000066]' : 'bg-slate-300'}`}
                            >
                                <span className={`absolute top-1 left-1 bg-white w-3 h-3 rounded-full transition-transform ${showZContours ? 'translate-x-5' : 'translate-x-0'}`}></span>
                            </button>
                        </div>

                         {/* Opacity Sliders in ONE Row */}
                         <div className="grid grid-cols-2 gap-3">
                             <div>
                                <div className="flex justify-between text-xs mb-1">
                                    <span>Surface Opacity</span>
                                    <span className="font-mono font-bold text-[#000066]">{(surfaceOpacity * 100).toFixed(0)}%</span>
                                </div>
                                <input 
                                    type="range" min="0" max="1" step="0.05"
                                    value={surfaceOpacity} onChange={(e) => setSurfaceOpacity(parseFloat(e.target.value))}
                                    className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                                />
                             </div>
                             <div>
                                <div className="flex justify-between text-xs mb-1">
                                    <span>Zero Plane Opacity</span>
                                    <span className="font-mono font-bold text-[#000066]">{(planeOpacity * 100).toFixed(0)}%</span>
                                </div>
                                <input 
                                    type="range" min="0" max="1" step="0.05"
                                    value={planeOpacity} onChange={(e) => setPlaneOpacity(parseFloat(e.target.value))}
                                    className="w-full accent-[#EAB308] h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                                />
                             </div>
                         </div>
                    </div>

                    {/* Camera Controls */}
                    <div className="p-3 bg-slate-50 rounded border border-slate-200">
                         <h4 className="text-xs font-bold text-slate-500 uppercase mb-3 flex items-center gap-2"><Compass size={12}/> Camera Position</h4>
                         
                         <div className="space-y-3">
                            {/* Eye X and Eye Y in Row */}
                            <div className="grid grid-cols-2 gap-3">
                                <div>
                                    <div className="flex justify-between text-xs mb-1">
                                        <span>Eye X</span>
                                        <span className="font-mono text-[10px] text-slate-500">{camera.x.toFixed(1)}</span>
                                    </div>
                                    <input 
                                        type="range" min="-5" max="5" step="0.1"
                                        value={camera.x} onChange={(e) => setCamera({...camera, x: parseFloat(e.target.value)})}
                                        className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                                    />
                                </div>
                                <div>
                                    <div className="flex justify-between text-xs mb-1">
                                        <span>Eye Y</span>
                                        <span className="font-mono text-[10px] text-slate-500">{camera.y.toFixed(1)}</span>
                                    </div>
                                    <input 
                                        type="range" min="-5" max="5" step="0.1"
                                        value={camera.y} onChange={(e) => setCamera({...camera, y: parseFloat(e.target.value)})}
                                        className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                                    />
                                </div>
                            </div>
                            
                            <div>
                                <div className="flex justify-between text-xs mb-1">
                                    <span>Eye Z (Zoom)</span>
                                    <span className="font-mono text-[10px] text-slate-500">{camera.z.toFixed(1)}</span>
                                </div>
                                <input 
                                    type="range" min="0.1" max="5" step="0.1"
                                    value={camera.z} onChange={(e) => setCamera({...camera, z: parseFloat(e.target.value)})}
                                    className="w-full accent-[#000066] h-1.5 bg-slate-200 rounded-lg appearance-none cursor-pointer"
                                />
                            </div>
                            <button 
                                onClick={() => setCamera({x: 1.8, y: 1.8, z: 0.8})}
                                className="w-full text-[10px] bg-slate-200 hover:bg-slate-300 text-slate-600 font-bold py-1 rounded mt-2"
                            >
                                Reset Camera
                            </button>
                         </div>
                    </div>
                </div>
             </div>
        </div>
    </div>
  );
};

export default BoosterResearch;
