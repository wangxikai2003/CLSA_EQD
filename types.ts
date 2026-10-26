
export enum OptionType {
  CALL = 'Call',
  PUT = 'Put',
}

export enum PositionType {
  LONG = 'Long',
  SHORT = 'Short',
}

export interface OptionLeg {
  id: string;
  type: OptionType;
  position: PositionType; // Long = Buy, Short = Sell
  strike: number;
  quantity: number;
  iv: number; // Implied Volatility (decimal, e.g., 0.2 for 20%)
  t: number; // Time to maturity in years
  r: number; // Risk-free rate
}

export interface GreekResult {
  price: number;
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  rho: number;
  vanna: number;
  volga: number;
}

export interface PayoffPoint {
  spot: number;
  moneyness: number; // New field: % of Spot (e.g. 100, 105)
  pnl: number;
  intrinsicValue: number;
  totalPrice: number;
  delta: number;
  gamma: number;
  vega: number;
  theta: number;
  vanna: number;
  volga: number;
  skewRisk: number; // New field: Price(Skew) - Price(Flat)
  correlationRisk: number; // New field: Price(WorstOf) - Price(Single)
}

export interface AnalysisResult {
  totalPrice: number;
  breakEvenPoints: number[];
  maxProfit: number | 'Unlimited';
  maxLoss: number | 'Unlimited';
  winRate: number; // Probability of profit > 0
}

// --- Exotic Types ---

export enum ExoticType {
  SNOWBALL = 'Snowball (Autocall)',
  SHARKFIN = 'Sharkfin (Up-and-Out Call)'
}

export interface ExoticProduct {
  type: ExoticType;
  spotRef: number; // Initial Reference Spot
  maturity: number; // Years
  vol: number;
  r: number;
  notional: number;
  
  // Snowball Specifics
  kiBarrier?: number; // % of spot (e.g. 0.8)
  koBarrier?: number; // % of spot (e.g. 1.03)
  koStepDown?: number; // New: Amount to decrease KO barrier per observation period (decimal, e.g. 0.005)
  
  koCoupon?: number; // Annualized coupon if Knocked Out
  dividendCoupon?: number; // Annualized bonus coupon if No KI & No KO (Safe Maturity)
  
  obsFreq?: 'Monthly' | 'Daily' | 'Yearly' | 'European';

  // Sharkfin Specifics
  strike?: number; // % of spot
  barrier?: number; // % of spot
  rebate?: number; // % payout if knocked out
}

export interface ExoticAnalysisData {
  spot: number;
  price: number;
  delta: number;
  gamma: number;
  vega: number;
  theta: number;
}

// --- Barrier Types ---

export enum BarrierType {
  UP_OUT = 'Up-and-Out',
  UP_IN = 'Up-and-In',
  DOWN_OUT = 'Down-and-Out',
  DOWN_IN = 'Down-and-In'
}

export interface BarrierProduct {
  type: OptionType;
  barrierType: BarrierType;
  barrierLevel: number;
  strike: number;
  rebate: number;
  observationFreq: number;
  maturity: number;
  notional: number;
}

// --- Volatility Surface Types ---

export type InterpolationMethod = 'JW SVI' | 'Cubic Spline' | 'Local Volatility' | 'Stochastic Volatility';

export interface VolSurfacePoint {
  strike: number;
  ttm: number;
  iv: number;
}

export interface VolSurfaceModel {
  predict: (strike: number, ttm: number) => number;
  spot?: number;
  modelName?: string;
}

// --- Backtest Types ---

export interface HistoricalDataPoint {
  date: string;
  price: number;
  isLossEvent?: boolean; // Used for UI highlighting
}

export interface BacktestSummary {
  totalExpired: number;
  knockOutCount: number;
  knockOutRate: number;
  dividendCount: number; // No KO, No KI (Win)
  principalCount: number; // No KO, KI, but Price > Strike (Rare for Snowball, usually loss)
  lossCount: number;
  winRate: number; // KO + Dividend
  avgDuration: number; // Months
  
  // Active Products
  totalActive: number;
  activeNoKI: number;
  activeKI: number;

  // KI then KO specific
  kiThenKoCount: number;
  kiThenKoRate: number;
}
