
import { OptionLeg, OptionType, PositionType, GreekResult, PayoffPoint, AnalysisResult } from '../types';
import { cnd, ndf } from '../utils/math';
import { simulateWorstOfPrice } from './monteCarlo';

/**
 * Calculates Black-Scholes Price and Greeks for a single leg
 */
export const calculateBS = (
  S: number, // Spot Price
  K: number, // Strike Price
  T: number, // Time to Maturity (years)
  r: number, // Risk-Free Rate
  sigma: number, // Volatility
  type: OptionType
): GreekResult => {
  // Ensure non-negative volatility for calculation
  const effSigma = Math.max(0.001, sigma);

  if (T <= 0) {
    // Expiration logic
    const intrinsic = type === OptionType.CALL ? Math.max(0, S - K) : Math.max(0, K - S);
    return { price: intrinsic, delta: 0, gamma: 0, theta: 0, vega: 0, rho: 0, vanna: 0, volga: 0 };
  }

  const d1 = (Math.log(S / K) + (r + 0.5 * effSigma * effSigma) * T) / (effSigma * Math.sqrt(T));
  const d2 = d1 - effSigma * Math.sqrt(T);

  let price = 0;
  let delta = 0;
  let theta = 0;
  let rho = 0;

  const nd1 = cnd(d1);
  const nd2 = cnd(d2);
  const nnd1 = cnd(-d1);
  const nnd2 = cnd(-d2);
  const pdf_d1 = ndf(d1);

  const sqrtT = Math.sqrt(T);
  const ert = Math.exp(-r * T);

  // Common Greeks
  const gamma = (pdf_d1) / (S * effSigma * sqrtT);
  const vega = (S * pdf_d1 * sqrtT) / 100; // Divide by 100 to get per 1% vol change

  // Second Order Greeks
  // Vanna: dDelta/dVol or dVega/dSpot. Normalized per 1% vol change.
  // Formula: -pdf(d1) * d2 / sigma. Divided by 100 for 1% vol scaling.
  const vanna = ((-pdf_d1 * d2) / effSigma) / 100;

  // Volga (Vomma): dVega/dVol. Normalized per 1% vol change.
  // Formula: Vega * d1 * d2 / sigma. 
  // Note: Since 'vega' var is already scaled by 1/100, this result is consistent for a unit change in vol metric.
  const volga = (vega * d1 * d2) / effSigma;

  if (type === OptionType.CALL) {
    price = S * nd1 - K * ert * nd2;
    delta = nd1;
    theta = ((-S * pdf_d1 * effSigma) / (2 * sqrtT) - r * K * ert * nd2) / 365;
    rho = (K * T * ert * nd2) / 100;
  } else {
    price = K * ert * nnd2 - S * nnd1;
    delta = nd1 - 1;
    theta = ((-S * pdf_d1 * effSigma) / (2 * sqrtT) + r * K * ert * nnd2) / 365;
    rho = (-K * T * ert * nnd2) / 100;
  }

  return { price, delta, gamma, theta, vega, rho, vanna, volga };
};

/**
 * Calculates the combined Greeks and Price for a strategy at a specific Spot Price
 * Now accepts volShift (parallel) and volSlope (skew rotation)
 */
export const calculateStrategyGreeks = (legs: OptionLeg[], spot: number, volShift: number = 0, volSlope: number = 0): GreekResult => {
  let total: GreekResult = { price: 0, delta: 0, gamma: 0, theta: 0, vega: 0, rho: 0, vanna: 0, volga: 0 };

  legs.forEach((leg) => {
    // Apply Vol Skew Logic: IV_final = IV_base + Shift + Slope * (Moneyness - 1)
    const moneyness = leg.strike / spot;
    // Note: This slope is applied relative to the CURRENT spot being calculated
    const skewAdjustment = volSlope * (moneyness - 1);
    
    const effectiveIV = leg.iv + volShift + skewAdjustment;
    
    const bs = calculateBS(spot, leg.strike, leg.t, leg.r, effectiveIV, leg.type);
    const direction = leg.position === PositionType.LONG ? 1 : -1;
    const qty = leg.quantity;

    total.price += bs.price * direction * qty;
    total.delta += bs.delta * direction * qty;
    total.gamma += bs.gamma * direction * qty;
    total.theta += bs.theta * direction * qty;
    total.vega += bs.vega * direction * qty;
    total.rho += bs.rho * direction * qty;
    total.vanna += bs.vanna * direction * qty;
    total.volga += bs.volga * direction * qty;
  });

  return total;
};

/**
 * Generates data points for charts (Payoff & Greeks vs Spot)
 */
export const generateAnalysisData = (
    legs: OptionLeg[], 
    currentSpot: number, 
    rangePercent: number = 0.5, 
    volShift: number = 0, 
    volSlope: number = 0,
    isWorstOf: boolean = false,
    rho: number = 0.5
): PayoffPoint[] => {
  const points: PayoffPoint[] = [];
  const minSpot = currentSpot * (1 - rangePercent);
  const maxSpot = currentSpot * (1 + rangePercent);
  const steps = 100;
  const stepSize = (maxSpot - minSpot) / steps;

  // Calculate entry cost (Net Premium) at CURRENT spot
  const entryGreeks = calculateStrategyGreeks(legs, currentSpot, volShift, volSlope);
  const netPremium = entryGreeks.price; 
  
  // Calculate Average IV/Time for approximate pricing
  let totalQty = 0;
  let weightedIV = 0;
  let maxT = 0;
  let avgR = 0.05;
  legs.forEach(l => {
      totalQty += l.quantity;
      weightedIV += l.iv * l.quantity;
      if(l.t > maxT) maxT = l.t;
      avgR = l.r;
  });
  const avgIV = totalQty > 0 ? weightedIV / totalQty : 0.2;

  for (let s = minSpot; s <= maxSpot; s += stepSize) {
    const greeksAtS = calculateStrategyGreeks(legs, s, volShift, volSlope);
    
    // Strategy Value at Expiry (Intrinsic only, Vol doesn't matter at T=0)
    let strategyValueAtExpiry = 0;
    legs.forEach(leg => {
        const direction = leg.position === PositionType.LONG ? 1 : -1;
        let val = 0;
        if (leg.type === OptionType.CALL) val = Math.max(0, s - leg.strike);
        else val = Math.max(0, leg.strike - s);
        strategyValueAtExpiry += val * direction * leg.quantity;
    });

    // Skew Risk Calculation:
    // Difference between Price(With Skewed IVs) and Price(With Flat Avg IV)
    let flatPrice = 0;
    legs.forEach(leg => {
        const bs = calculateBS(s, leg.strike, leg.t, leg.r, avgIV + volShift, leg.type);
        const direction = leg.position === PositionType.LONG ? 1 : -1;
        flatPrice += bs.price * direction * leg.quantity;
    });
    const skewRisk = greeksAtS.price - flatPrice;

    // Correlation Risk Calculation (Worst Of)
    // Risk = Price(Single/High Corr) - Price(WorstOf/Low Corr)
    let correlationRisk = 0;
    if (isWorstOf) {
        // Use Common Random Numbers (CRN) via Seeded RNG for smoothness across spot prices
        // Increased simulations to 2500 for better convergence
        const worstOfPrice = simulateWorstOfPrice(
            legs, 
            s, 
            avgIV + volShift, 
            avgR, 
            maxT, 
            rho, 
            2500,
            1337 // Fixed Seed -> Deterministic Noise -> Smooth Curve
        );
        // INVERTED definition: High Correlation (BS) - Low Correlation (MC)
        correlationRisk = greeksAtS.price - worstOfPrice;
    }

    points.push({
      spot: s,
      moneyness: (s / currentSpot) * 100, // Calculate Moneyness
      pnl: strategyValueAtExpiry - netPremium,
      intrinsicValue: strategyValueAtExpiry,
      totalPrice: greeksAtS.price,
      delta: greeksAtS.delta,
      gamma: greeksAtS.gamma,
      vega: greeksAtS.vega,
      theta: greeksAtS.theta,
      vanna: greeksAtS.vanna,
      volga: greeksAtS.volga,
      skewRisk: skewRisk,
      correlationRisk: correlationRisk
    });
  }
  return points;
};

/**
 * Calculates probability of profit (Win Rate)
 */
export const calculateProbabilities = (legs: OptionLeg[], spot: number, volShift: number = 0, volSlope: number = 0): AnalysisResult => {
    const entryValues = calculateStrategyGreeks(legs, spot, volShift, volSlope);
    const netCost = entryValues.price;

    const range = generateAnalysisData(legs, spot, 0.8, volShift, volSlope); 
    const breakEvens: number[] = [];
    
    let inProfit = range[0].pnl > 0;
    for(let i=1; i<range.length; i++) {
        if ((range[i].pnl > 0) !== inProfit) {
            breakEvens.push(range[i].spot);
            inProfit = !inProfit;
        }
    }

    let winRate = 0;
    // Assume effective Vol of first leg or default
    const T = legs.length > 0 ? legs[0].t : 1;
    // Use ATM vol for probability
    const atmVol = legs.length > 0 ? (legs[0].iv + volShift) : 0.2; 
    const r = legs.length > 0 ? legs[0].r : 0.05;
    
    let totalProb = 0;
    const stepSize = range[1].spot - range[0].spot;

    range.forEach(pt => {
        if(pt.pnl > 0) {
            const ln_S_K = Math.log(pt.spot / spot);
            const num = Math.pow(ln_S_K - (r - 0.5*atmVol*atmVol)*T, 2);
            const den = 2 * atmVol * atmVol * T;
            const probDensity = (1 / (pt.spot * atmVol * Math.sqrt(2 * Math.PI * T))) * Math.exp(-num / den);
            totalProb += probDensity * stepSize;
        }
    });
    
    winRate = Math.min(Math.max(totalProb, 0), 1);

    const profits = range.map(p => p.pnl);
    const maxProfit = Math.max(...profits);
    const maxLoss = Math.min(...profits);

    return {
        totalPrice: netCost,
        breakEvenPoints: breakEvens,
        maxProfit: maxProfit, 
        maxLoss: maxLoss,
        winRate: winRate
    }
}
