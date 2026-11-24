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
export type QisVolRvTilt = 'balanced' | 'carry' | 'defensive';
export type QisVolRvUniverse = '300' | '50' | '1000' | 'AU' | 'SC';
export type QisHedgerMode = 'eod_close' | 'intraday_curve';
export type QisBasketMethod = 'vol_rp' | 'cvar_rp';
export type QisBasketSignal = 'quantile' | 'garch_z' | 'regime2';

export interface QisBasketParams {
  method?: QisBasketMethod | string;
  target_vol?: number;
  cvar_alpha?: number;
  lookback?: number;
  rebalance?: number;
  universe?: '50' | '300' | '1000' | string;
  signals?: QisBasketSignal[] | string[];
  /** Explicit member ids: cal:… / vrv:… / carry:cross:na */
  members?: string;
  tenor_pair?: QisTenorPair | string;
  side_mode?: QisSideMode | string;
  spread_mode?: QisSpreadMode | string;
}

export interface QisBasketBlock {
  method: string;
  target_vol: number;
  cvar_alpha: number;
  lookback: number;
  rebalance_days: number;
  members: { id: string; label: string; weight: number }[];
  weights: Record<string, number | string>[];
  rebalance_log?: Record<string, number | string>[];
  components?: { id: string; label: string; nav: { d: string; nav: number }[] }[];
  equal_weight_label?: string;
}

export interface QisNavPoint {
  d: string;
  nav: number;
  sos?: number | null;
}

export interface QisVolRvAblation {
  step1_eod_vanilla: QisNavPoint[];
  step2_long_threshold: QisNavPoint[];
  step3_dynamic_leverage: QisNavPoint[];
}

export interface QisVolRvAttribution {
  dates: string[];
  short_option_pnl: number[];
  long_option_pnl: number[];
  futures_hedge_pnl: number[];
  transaction_cost: number[];
  net_pnl: number[];
}

export interface QisVolRvGreeksSummary {
  dates: string[];
  net_delta: number[];
  net_gamma: number[];
  net_vega: number[];
  net_theta: number[];
  long_dynamic_leverage: (number | null)[];
}

export interface QisVolRvParams {
  universe?: QisVolRvUniverse | string;
  tilt?: QisVolRvTilt | string;
  target_otm_delta?: number;
  long_delta_threshold?: number;
  lambda_tilt?: number;
  l_min?: number;
  l_max?: number;
  hedger_mode?: QisHedgerMode | string;
  option_fee_pct?: number;
  option_slippage_pct?: number;
  futures_fee_pct?: number;
  futures_slippage_pct?: number;
  ablation?: boolean;
}

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
  /** CSI 300 Vol RV Step1/2/3 ablation NAV paths. */
  ablation?: QisVolRvAblation;
  /** CSI 300 Vol RV daily PnL attribution. */
  pnl_attribution?: QisVolRvAttribution;
  /** CSI 300 Vol RV net greeks + dynamic leverage. */
  greeks_summary?: QisVolRvGreeksSummary;
  /** QIS Basket risk-parity block. */
  basket?: QisBasketBlock;
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
  volRv?: QisVolRvParams,
  basket?: QisBasketParams,
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
  if (strategyId === 'csi300_vol_rv' && volRv) {
    if (volRv.universe) q.set('universe', String(volRv.universe));
    if (volRv.tilt) q.set('tilt', String(volRv.tilt));
    if (volRv.target_otm_delta != null) q.set('target_otm_delta', String(volRv.target_otm_delta));
    if (volRv.long_delta_threshold != null) {
      q.set('long_delta_threshold', String(volRv.long_delta_threshold));
    }
    if (volRv.lambda_tilt != null) q.set('lambda_tilt', String(volRv.lambda_tilt));
    if (volRv.l_min != null) q.set('l_min', String(volRv.l_min));
    if (volRv.l_max != null) q.set('l_max', String(volRv.l_max));
    if (volRv.hedger_mode) q.set('hedger_mode', String(volRv.hedger_mode));
    if (volRv.option_fee_pct != null) q.set('option_fee_pct', String(volRv.option_fee_pct));
    if (volRv.option_slippage_pct != null) {
      q.set('option_slippage_pct', String(volRv.option_slippage_pct));
    }
    if (volRv.futures_fee_pct != null) q.set('futures_fee_pct', String(volRv.futures_fee_pct));
    if (volRv.futures_slippage_pct != null) {
      q.set('futures_slippage_pct', String(volRv.futures_slippage_pct));
    }
    if (volRv.ablation != null) q.set('ablation', volRv.ablation ? 'true' : 'false');
  }
  if (strategyId === 'qis_basket' && basket) {
    if (basket.method) q.set('basket_method', String(basket.method));
    if (basket.target_vol != null) q.set('target_vol', String(basket.target_vol));
    if (basket.cvar_alpha != null) q.set('cvar_alpha', String(basket.cvar_alpha));
    if (basket.lookback != null) q.set('basket_lookback', String(basket.lookback));
    if (basket.rebalance != null) q.set('basket_rebalance', String(basket.rebalance));
    if (basket.universe) q.set('basket_universe', String(basket.universe));
    if (basket.members) q.set('basket_members', String(basket.members));
    else if (basket.signals?.length) q.set('basket_signals', basket.signals.join(','));
    if (basket.tenor_pair) q.set('tenor_pair', String(basket.tenor_pair));
    if (basket.side_mode) q.set('side_mode', String(basket.side_mode));
    if (basket.spread_mode) q.set('spread_mode', String(basket.spread_mode));
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
