import { FetchRetryOpts, _asRetryOpts, getJsonWithRetry } from './http';

export interface QisStrategyInfo {
  id: string;
  name: string;
  status?: string;
  blurb?: string;
  family?: string;
  universe?: string;
}

export interface QisMetrics {
  n_days: number;
  ann_return: number | null;
  ann_vol: number | null;
  sharpe: number | null;
  mdd: number | null;
  mdd_vol: number | null;
  pct_positive_months: number | null;
  start?: string | null;
  end?: string | null;
  /** Present on yearly_metrics rows only. */
  year?: number;
}

export interface QisPnLDecomposition {
  dates: string[];
  vega_pnl: number[];
  theta_pnl: number[];
  gamma_pnl: number[];
  basis_pnl: number[];
  net_delta_pnl: number[];
  residual_pnl: number[];
}

/** Daily (non-cumulative) greek PnL for bar charts. */
export interface QisDailyGreeks {
  dates: string[];
  vega_pnl: number[];
  theta_pnl: number[];
  gamma_pnl: number[];
}

export type QisTenorPair = '3M-6M' | '1M-3M';
export type QisSideMode = 'both' | 'long_only' | 'short_only';
export type QisSpreadMode = 'iv' | 'iv2';
export type QisSignalMode =
  | 'quantile'
  | 'garch_z'
  | 'regime'
  | 'regime_momentum'
  | 'regime_fade'
  | 'regime2';

/** True for any Regime v2 book (both / momentum-only / fade-only). */
export function isRegimeSignalMode(mode: string | null | undefined): boolean {
  return (
    mode === 'regime' || mode === 'regime_momentum' || mode === 'regime_fade'
  );
}

/** Quantile base + dual-day garch_z stress gate. */
export function isRegime2SignalMode(mode: string | null | undefined): boolean {
  return mode === 'regime2';
}

export interface QisTradeMarker {
  d: string;
  kind: 'long' | 'short' | 'close' | string;
  spread?: number | null;
  sos?: number | null;
  /** CT-IV spread level (for IV reference chart under garch_z / regime). */
  spread_iv?: number | null;
  sos_iv?: number | null;
  /** garch_z at trade date (for GARCH-z chart markers under regime). */
  garch_z?: number | null;
  position?: number;
  prev_position?: number;
  regime_mode?: string | null;
}

export interface QisSignalSeries {
  dates: string[];
  /** Decision level: raw CT-IV spread or garch_z (see signal_mode / level_scale). */
  spread: number[];
  spread_iv?: (number | null)[];
  /** Expanding GARCH-z (regime / garch_z modes). */
  garch_z?: (number | null)[];
  zscore: (number | null)[];
  position: number[];
  signal_next?: number[];
  /** Executed position transitions (presentation); fallback: derive from position[] */
  trade_markers?: QisTradeMarker[];
  /** @deprecated fixed Z bands — prefer enter_hi/lo time series */
  enter_band?: number;
  exit_band?: number;
  enter_hi?: (number | null)[];
  enter_lo?: (number | null)[];
  exit_hi?: (number | null)[];
  exit_lo?: (number | null)[];
  /** Prior-window median. Quantile and regime2 exit when spread returns to this level. */
  q50?: (number | null)[];
  /** CT-IV spread rolling bands (reference; equals enter_* when signal_mode=quantile). */
  iv_enter_hi?: (number | null)[];
  iv_enter_lo?: (number | null)[];
  iv_exit_hi?: (number | null)[];
  iv_exit_lo?: (number | null)[];
  /** Rolling 10/90·40/60 bands on garch_z (legacy chart fields; regime UI uses shk_*). */
  gz_enter_hi?: (number | null)[];
  gz_enter_lo?: (number | null)[];
  gz_exit_hi?: (number | null)[];
  gz_exit_lo?: (number | null)[];
  /** Regime decision level (Spread) quantiles — prior W days, shift(1). */
  lvl_q01?: (number | null)[];
  lvl_q10?: (number | null)[];
  lvl_q25?: (number | null)[];
  lvl_q50?: (number | null)[];
  lvl_q75?: (number | null)[];
  lvl_q90?: (number | null)[];
  lvl_q99?: (number | null)[];
  /** Regime decision shock (garch_z) quantiles. */
  shk_q10?: (number | null)[];
  shk_q20?: (number | null)[];
  shk_q25?: (number | null)[];
  shk_q75?: (number | null)[];
  shk_q80?: (number | null)[];
  shk_q90?: (number | null)[];
  enter_q?: number;
  exit_lo_q?: number;
  exit_hi_q?: number;
  window?: number;
  signal_mode?: QisSignalMode | string;
  /** Multiply spread/bands for chart: 100 = vol pts, 1 = garch_z units. */
  level_scale?: number;
  perf_start?: string;
  first_open?: string;
}

export interface QisPnLContribution {
  total_pnl: number;
  vega_pct: number | null;
  theta_pct: number | null;
  gamma_pct: number | null;
  basis_pct: number | null;
  net_delta_pct: number | null;
  residual_pct: number | null;
}

export interface QisBacktestResponse {
  strategy_id: string;
  strategy_name: string;
  description: string;
  legs: { id: string; weight: number; product: string; tenor: string }[];
  params: {
    initial_nav: number;
    slippage_bp: number;
    fee_pct: number;
    cost_rate: number;
    rebalance: string;
    signal_lag: string;
    price: string;
    sizing?: string;
    [k: string]: unknown;
  };
  nav: { d: string; nav: number; sos: number }[];
  metrics: Record<'1y' | '3y' | '5y' | 'max', QisMetrics>;
  /** Calendar-year rows (nav rebased at each year start). */
  yearly_metrics?: QisMetrics[];
  summary: {
    start: string;
    end: string;
    n_days: number;
    final_nav: number;
    final_nav_multiple: number;
    total_cost: number;
    total_pnl?: number;
    first_open?: string;
  };
  pnl_decomposition?: QisPnLDecomposition;
  /** Daily greek PnL bars (calendar-vol). */
  pnl_daily_greeks?: QisDailyGreeks;
  signal?: QisSignalSeries;
  pnl_contribution?: QisPnLContribution;
  /** Result-store reuse: hit=true means engine was skipped. */
  _cache?: {
    hit: boolean;
    source?: 'db' | 'memory' | 'miss' | string;
    source_key?: string;
    persisted?: boolean;
    db?: string;
    tenor_pair?: string;
    side_mode?: string;
    spread_mode?: string;
    signal_mode?: string;
  };
}

export function fetchQisStrategies(opts?: AbortSignal | FetchRetryOpts) {
  return getJsonWithRetry<{ strategies: QisStrategyInfo[] }>(
    '/api/v1/qis/strategies',
    _asRetryOpts(opts)
  );
}

export function fetchQisBacktest(
  strategyId: string,
  refresh = false,
  opts?: AbortSignal | FetchRetryOpts,
  zWindow = 252,
  tenorPair: QisTenorPair = '3M-6M',
  sideMode: QisSideMode = 'both',
  spreadMode: QisSpreadMode = 'iv',
  signalMode: QisSignalMode = 'quantile',
  stopLoss = false,
  maxDrawdownTarget = 0.1,
  reboundRatio = 0.9,
) {
  const q = new URLSearchParams();
  if (refresh) q.set('refresh', 'true');
  q.set('z_window', String(zWindow));
  q.set('tenor_pair', tenorPair);
  q.set('side_mode', sideMode);
  q.set('spread_mode', spreadMode);
  q.set('signal_mode', signalMode);
  if (stopLoss) {
    q.set('stop_loss', 'true');
    q.set('max_drawdown_target', String(maxDrawdownTarget));
    q.set('rebound_ratio', String(reboundRatio));
  }
  const qs = q.toString();
  return getJsonWithRetry<QisBacktestResponse>(
    `/api/v1/qis/backtest/${strategyId}?${qs}`,
    _asRetryOpts(opts)
  );
}

/** Calendar-vol concrete holdings Excel (date/contract/dir/qty/price). */
export function qisHoldingsExcelUrl(
  strategyId: string,
  zWindow = 252,
  tenorPair: QisTenorPair = '3M-6M',
  sideMode: QisSideMode = 'both',
  spreadMode: QisSpreadMode = 'iv',
  signalMode: QisSignalMode = 'quantile',
  stopLoss = false,
  maxDrawdownTarget = 0.1,
  reboundRatio = 0.9,
) {
  const q = new URLSearchParams();
  q.set('z_window', String(zWindow));
  q.set('tenor_pair', tenorPair);
  q.set('side_mode', sideMode);
  q.set('spread_mode', spreadMode);
  q.set('signal_mode', signalMode);
  if (stopLoss) {
    q.set('stop_loss', 'true');
    q.set('max_drawdown_target', String(maxDrawdownTarget));
    q.set('rebound_ratio', String(reboundRatio));
  }
  q.set('persist', 'true');
  return `/api/v1/qis/backtest/${encodeURIComponent(strategyId)}/holdings.xlsx?${q}`;
}
