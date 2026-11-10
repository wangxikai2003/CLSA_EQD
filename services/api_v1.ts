/**
 * Strongly-typed /api/v1 client with hash cache, debounce helper, AbortController.
 */

const API_BASE = (import.meta as any).env?.VITE_API_BASE ?? '';

export type ProductType = 'snowball' | 'fcn' | 'phoenix' | 'booster' | 'airbag' | 'capped_booster';

export interface MarketSnapshot {
  spots: number[];
  r: number;
  q: number[];
  corr?: number[][];
  vol_mode?: 'flat' | 'jwsvi' | 'local_vol';
  flat_vols: number[];
}

export interface ExoticPriceRequest {
  product_type: ProductType;
  product: Record<string, unknown>;
  market: MarketSnapshot;
  sim?: { n_paths?: number; n_steps?: number; seed?: number; bridge?: boolean; bgk?: boolean };
  greeks?: {
    enabled?: boolean;
    bump_spot?: number;
    bump_vol?: number;
    barrier_shift_bps?: number;
    spread_smoothing_width?: number;
    return_profiles?: boolean;
    return_ki_profiles?: boolean;
    barrier_shift?: number;
    digital_width?: number;
  };
}

export interface ProfileCurve {
  spot: number[];
  spot_mult: number[];
  pv: number[];
  delta: number[];
  gamma: number[];
}

export interface ExoticPriceResponse {
  price: number;
  price_ci_95: number[];
  stderr: number;
  greeks: Record<string, number[]>;
  diagnostics: {
    ko_prob?: number;
    ki_prob?: number;
    elapsed_ms?: number;
    shifted_price?: number;
    [k: string]: unknown;
  };
  meta: Record<string, unknown>;
  raw_profile?: ProfileCurve;
  shifted_profile?: ProfileCurve;
  reserve_metrics?: {
    pv_raw: number;
    pv_shifted: number;
    reserve: number;
    theta_release?: { t: number; reserve_left: number }[];
  };
  pre_ki_profile?: ProfileCurve;
  post_ki_profile?: ProfileCurve;
}

export interface JwCalibRequest {
  ttm: number;
  strikes: number[];
  ivs: number[];
  forward: number;
  method?: 'SLSQP' | 'BFGS' | 'LS';
}

export interface JwCalibResponse {
  jw: { v: number; psi: number; p: number; c: number; vtild: number };
  raw: { a: number; b: number; rho: number; m: number; sigma: number };
  rmse: number;
  mae: number;
  max_residual?: number;
  arbitrage: { butterfly_ok: boolean; g_min: number };
  arbitrage_check?: {
    min_g: number;
    has_butterfly_arbitrage: boolean;
    has_calendar_arbitrage: boolean;
    calendar_min_gap?: number;
  };
}

const cache = new Map<string, { t: number; data: unknown }>();
const CACHE_TTL_MS = 15_000;

function hashKey(obj: unknown): string {
  return JSON.stringify(obj);
}

async function postJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const key = hashKey({ path, body });
  const hit = cache.get(key);
  if (hit && Date.now() - hit.t < CACHE_TTL_MS) return hit.data as T;

  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`${res.status} ${path}: ${text}`);
    }
    const data = (await res.json()) as T;
    cache.set(key, { t: Date.now(), data });
    return data;
  } catch (e) {
    if ((e as Error)?.name === 'AbortError' || signal?.aborted) {
      const err = new Error('Aborted');
      err.name = 'AbortError';
      throw err;
    }
    throw e;
  }
}

export type RetryStatus = {
  attempt: number;
  maxAttempts: number;
  delayMs: number;
  reason: string;
};

export type FetchRetryOpts = {
  signal?: AbortSignal;
  onRetry?: (status: RetryStatus) => void;
  /** Default 10 — cover Cloud Run cold-start / first heavy build. */
  maxAttempts?: number;
  baseDelayMs?: number;
  maxDelayMs?: number;
};

function _asRetryOpts(opts?: AbortSignal | FetchRetryOpts): FetchRetryOpts {
  if (!opts) return {};
  if (typeof AbortSignal !== 'undefined' && opts instanceof AbortSignal) return { signal: opts };
  return opts as FetchRetryOpts;
}

function _isTransientStatus(status: number): boolean {
  return status === 408 || status === 425 || status === 429 || status === 500 || status === 502 || status === 503 || status === 504;
}

function _sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      const err = new Error('Aborted');
      err.name = 'AbortError';
      reject(err);
      return;
    }
    const timer = setTimeout(resolve, ms);
    const onAbort = () => {
      clearTimeout(timer);
      const err = new Error('Aborted');
      err.name = 'AbortError';
      reject(err);
    };
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

function _isAbort(e: unknown, signal?: AbortSignal): boolean {
  return (e as Error)?.name === 'AbortError' || !!signal?.aborted || String(e).includes('abort');
}

function _isNetworkError(e: unknown): boolean {
  if (e instanceof TypeError) return true;
  const s = String(e);
  return s.includes('Failed to fetch') || s.includes('NetworkError') || s.includes('Load failed');
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { signal });
  if (!res.ok) throw new Error(`${res.status} ${path}: ${await res.text()}`);
  return res.json() as Promise<T>;
}

/** GET with patience for cold starts / gateway 503s — keeps retrying instead of failing once. */
async function getJsonWithRetry<T>(path: string, opts?: FetchRetryOpts): Promise<T> {
  const maxAttempts = opts?.maxAttempts ?? 10;
  const baseDelayMs = opts?.baseDelayMs ?? 1500;
  const maxDelayMs = opts?.maxDelayMs ?? 15000;
  const signal = opts?.signal;
  let lastErr: Error | null = null;

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    if (signal?.aborted) {
      const err = new Error('Aborted');
      err.name = 'AbortError';
      throw err;
    }
    try {
      const res = await fetch(`${API_BASE}${path}`, { signal });
      if (res.ok) return (await res.json()) as T;
      const text = (await res.text()).trim();
      const msg = `${res.status} ${path}: ${text || res.statusText || 'Service Unavailable'}`;
      lastErr = new Error(msg);
      if (!_isTransientStatus(res.status) || attempt >= maxAttempts) throw lastErr;
      const delayMs = Math.min(maxDelayMs, Math.round(baseDelayMs * Math.pow(1.7, attempt - 1)));
      opts?.onRetry?.({ attempt, maxAttempts, delayMs, reason: msg });
      await _sleep(delayMs, signal);
    } catch (e) {
      if (_isAbort(e, signal)) {
        const err = new Error('Aborted');
        err.name = 'AbortError';
        throw err;
      }
      if (e instanceof Error && e === lastErr && attempt >= maxAttempts) throw e;
      if (e instanceof Error && /^\d{3}\s/.test(e.message) && !_isTransientStatus(parseInt(e.message, 10))) {
        throw e;
      }
      lastErr = e instanceof Error ? e : new Error(String(e));
      const code = parseInt(lastErr.message, 10);
      const transient = _isNetworkError(e) || (Number.isFinite(code) && _isTransientStatus(code));
      if (!transient || attempt >= maxAttempts) throw lastErr;
      const delayMs = Math.min(maxDelayMs, Math.round(baseDelayMs * Math.pow(1.7, attempt - 1)));
      opts?.onRetry?.({ attempt, maxAttempts, delayMs, reason: lastErr.message });
      await _sleep(delayMs, signal);
    }
  }
  throw lastErr ?? new Error(`${path}: exhausted retries`);
}

/** Module-level AbortController so rapid re-prices cancel in-flight POSTs. */
let _priceCtrl: AbortController | null = null;

export function abortPriceExotic() {
  if (_priceCtrl) {
    _priceCtrl.abort();
    _priceCtrl = null;
  }
}

export function priceExotic(req: ExoticPriceRequest, signal?: AbortSignal) {
  // If caller didn't supply a signal, own the lifecycle and abort prior request
  if (!signal) {
    abortPriceExotic();
    _priceCtrl = new AbortController();
    signal = _priceCtrl.signal;
  }
  return postJson<ExoticPriceResponse>('/api/v1/pricing/exotic', req, signal).catch((e) => {
    if ((e as Error)?.name === 'AbortError' || String(e).includes('abort')) {
      const err = new Error('Aborted');
      err.name = 'AbortError';
      throw err;
    }
    throw e;
  });
}

export function pricingStreamUrl(): string {
  const base = API_BASE || (typeof window !== 'undefined' ? window.location.origin : '');
  const proto = base.startsWith('https') ? 'wss' : 'ws';
  const host = base.replace(/^https?:\/\//, '') || (typeof window !== 'undefined' ? window.location.host : '127.0.0.1:8000');
  return `${proto}://${host}/api/v1/pricing/stream`;
}

export function calibrateJwSvi(req: JwCalibRequest, signal?: AbortSignal) {
  return postJson<JwCalibResponse>('/api/v1/calibration/jwsvi', req, signal);
}

export function fetchYieldCurve(params?: { codes?: string; asof?: string; history?: string }, signal?: AbortSignal) {
  const q = new URLSearchParams();
  if (params?.codes) q.set('codes', params.codes);
  if (params?.asof) q.set('asof', params.asof);
  if (params?.history) q.set('history', params.history);
  const qs = q.toString();
  return getJson<any>(`/api/v1/market/yield-curve${qs ? `?${qs}` : ''}`, signal);
}

export function fetchVolCone(product = 'IO', signal?: AbortSignal) {
  return getJson<any>(`/api/v1/market/vol-cone?product=${product}`, signal);
}

export function fetchTQuote(product: string, date?: string, expiry?: string, signal?: AbortSignal) {
  const q = new URLSearchParams({ product });
  if (date) q.set('date', date);
  if (expiry) q.set('expiry', expiry);
  return getJson<any>(`/api/v1/market/tquote?${q}`, signal);
}

export type SynthTenorKey = '3M' | '6M' | '9M' | '18M';
export type SynthLegKind = SynthTenorKey | 'SPOT';
export type SynthAssetClass = 'all' | 'equity' | 'commodity';

export interface SynthTenorPoint {
  d: string;
  settle: number;
  legs: string[];
  leg_dtm: number[];
  w0: number;
  extrapolated?: boolean;
  kind?: 'synth' | 'spot';
}

export interface SynthProductMeta {
  /** Wind futures root (IH/IF/IM, CU, …) — not option root HO/IO/MO */
  product: string;
  db_key?: string;
  fut: string;
  name: string;
  asset_class: 'equity' | 'commodity';
  spot_code?: string | null;
  wind_root?: string;
  bbg?: string;
  exch?: string;
  has_spot: boolean;
}

export interface SynthTenorsResponse {
  tenors: SynthTenorKey[];
  leg_kinds: SynthLegKind[];
  tenor_days: Record<string, number>;
  products: SynthProductMeta[];
  levels: Record<string, Partial<Record<SynthLegKind, SynthTenorPoint[]>>>;
  coverage: Record<string, Record<string, number>>;
  commodity_enabled: boolean;
  commodity_db?: string | null;
  code_convention?: string;
  spot_source: string;
  method: string;
}

export function fetchSynthTenors(
  refresh = false,
  assetClass: SynthAssetClass = 'all',
  opts?: AbortSignal | FetchRetryOpts
) {
  const o = _asRetryOpts(opts);
  const q = new URLSearchParams();
  if (refresh) q.set('refresh', 'true');
  if (assetClass && assetClass !== 'all') q.set('asset_class', assetClass);
  const qs = q.toString();
  return getJsonWithRetry<SynthTenorsResponse>(
    `/api/v1/market/synth-tenors${qs ? `?${qs}` : ''}`,
    o
  );
}

export type VolTenorKey = '1M' | '3M' | '6M' | '12M';
export type VolMoneyness = 'ATM' | 'OTM' | 'ITM';
export type VolFamily = 'RV' | 'IV';

export interface VolSpreadsResponse {
  tenors: VolTenorKey[];
  moneyness: VolMoneyness[];
  families: VolFamily[];
  products: { product: string; db_key: string; asset_class: 'equity' | 'commodity' }[];
  levels: Record<
    string,
    {
      RV: Partial<Record<VolTenorKey, { d: string; v: number }[]>>;
      IV: Partial<Record<VolTenorKey, Partial<Record<VolMoneyness, { d: string; v: number }[]>>>>;
    }
  >;
  coverage: Record<string, unknown>;
  unit: string;
  method: string;
  source: string;
}

export function fetchVolSpreads(
  assetClass: 'equity' | 'commodity' | 'all' = 'equity',
  refresh = false,
  opts?: AbortSignal | FetchRetryOpts
) {
  const o = _asRetryOpts(opts);
  const q = new URLSearchParams({ asset_class: assetClass });
  if (refresh) q.set('refresh', 'true');
  return getJsonWithRetry<VolSpreadsResponse>(`/api/v1/market/vol-spreads?${q}`, o);
}

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

export interface QisSignalSeries {
  dates: string[];
  spread: number[];
  zscore: (number | null)[];
  position: number[];
  /** @deprecated fixed Z bands — prefer enter_hi/lo time series */
  enter_band?: number;
  exit_band?: number;
  enter_hi?: (number | null)[];
  enter_lo?: (number | null)[];
  exit_hi?: (number | null)[];
  exit_lo?: (number | null)[];
  enter_q?: number;
  exit_lo_q?: number;
  exit_hi_q?: number;
  window?: number;
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
  signal?: QisSignalSeries;
  pnl_contribution?: QisPnLContribution;
}

export function fetchQisStrategies(opts?: AbortSignal | FetchRetryOpts) {
  return getJsonWithRetry<{ strategies: QisStrategyInfo[] }>(
    '/api/v1/qis/strategies',
    _asRetryOpts(opts)
  );
}

export type QisTenorPair = '3M-6M' | '1M-3M';

export function fetchQisBacktest(
  strategyId: string,
  refresh = false,
  opts?: AbortSignal | FetchRetryOpts,
  zWindow = 252,
  tenorPair: QisTenorPair = '3M-6M',
) {
  const q = new URLSearchParams();
  if (refresh) q.set('refresh', 'true');
  q.set('z_window', String(zWindow));
  q.set('tenor_pair', tenorPair);
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
) {
  const q = new URLSearchParams();
  q.set('z_window', String(zWindow));
  q.set('tenor_pair', tenorPair);
  q.set('persist', 'true');
  return `/api/v1/qis/backtest/${encodeURIComponent(strategyId)}/holdings.xlsx?${q}`;
}

/** Debounce an async fn; last arg may receive AbortSignal when fn supports it. */
export function debounceAsync<T extends unknown[], R>(
  fn: (...args: [...T, AbortSignal?]) => Promise<R>,
  waitMs = 300
): (...args: T) => Promise<R | undefined> {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let controller: AbortController | null = null;
  return (...args: T) =>
    new Promise<R | undefined>((resolve, reject) => {
      if (timer) clearTimeout(timer);
      if (controller) controller.abort();
      controller = new AbortController();
      const signal = controller.signal;
      timer = setTimeout(() => {
        fn(...args, signal)
          .then(resolve)
          .catch((e) => {
            if ((e as Error)?.name === 'AbortError') {
              resolve(undefined);
              return;
            }
            reject(e);
          });
      }, waitMs);
    });
}

export function clearApiV1Cache() {
  cache.clear();
}

/* ---- Greeks term-structure (legacy ExoticPricer) ---- */

export interface KiRegimeCurve {
  valid_spots: number[];
  pv?: number[] | null;
  delta?: number[] | null;
  gamma?: number[] | null;
}

export interface GreeksTermSlice {
  t: number;
  label: string;
  before_ki: KiRegimeCurve;
  after_ki: KiRegimeCurve;
}

export interface GreeksTermStructureResponse {
  spot_grid: number[];
  S0: number;
  T: number;
  ki_level: number;
  ko_level: number;
  ki_monitoring_type: string;
  ko_monitoring_type: string;
  slices: GreeksTermSlice[];
  elapsed_ms?: number;
}

export interface GreeksTermStructureRequest {
  product_type: ProductType | string;
  product: Record<string, unknown>;
  market: MarketSnapshot;
  time_slices?: number[];
  spot_range?: number[];
  spot_steps?: number;
  ki_monitoring_type?: string;
  ko_monitoring_type?: string;
  include_greeks?: string[];
  n_paths?: number;
  seed?: number;
  barrier_shift_bps?: number;
  spread_smoothing_width?: number;
}

let _gtsCtrl: AbortController | null = null;

export function abortGreeksTermStructure() {
  if (_gtsCtrl) {
    _gtsCtrl.abort();
    _gtsCtrl = null;
  }
}

export function fetchGreeksTermStructure(
  req: GreeksTermStructureRequest,
  signal?: AbortSignal,
) {
  if (!signal) {
    abortGreeksTermStructure();
    _gtsCtrl = new AbortController();
    signal = _gtsCtrl.signal;
  }
  return postJson<GreeksTermStructureResponse>(
    '/api/v1/pricing/greeks-term-structure',
    req,
    signal,
  ).catch((e) => {
    if ((e as Error)?.name === 'AbortError' || String(e).includes('abort')) {
      const err = new Error('Aborted');
      err.name = 'AbortError';
      throw err;
    }
    throw e;
  });
}

/* ---- Universal Exotic Workbench ---- */

export interface UniversalExoticProductDTO {
  name: string;
  tenor: number;
  notional: number;
  asset: {
    underlying: string[];
    basket_type: 'WORSTOF' | 'BESTOF' | 'WEIGHTED';
    correlation_matrix?: number[][] | null;
    repo_rates?: number[] | null;
    div_yields?: number[] | null;
    weights?: number[] | null;
  };
  funding: {
    funding_index: 'SOFR' | 'HONIA' | 'FIXED' | string;
    funding_spread: number;
    funding_scale: number;
    funding_freq: string;
  };
  ko: {
    ko_freq: string;
    ko_monitoring_type: 'EUROPEAN' | 'AMERICAN' | string;
    ko_schedule: number[];
    ko_shift?: number[] | null;
    ko_shift_type?: string;
    ko_shift_value_type?: string;
    memory_ko?: boolean;
  };
  ki: {
    ki_level: number;
    ki_monitoring_type: 'DAILY' | 'EUROPEAN' | string;
    ki_shift?: number;
    ki_shift_type?: string;
    put_strike: number;
    put_participation: number;
    settlement_type: 'CASH' | 'PHYSICAL' | string;
  };
  coupon: {
    cpn_type: 'FIXED' | 'SNOWBALL_ACCRUED' | 'PHOENIX_CONDITIONAL' | string;
    cpn_rate: number;
    coupon_barrier?: number | null;
    memory_cpn?: boolean;
    guaranteed_periods?: number;
    ko_rebate?: number | null;
  };
  day_count?: 'act365' | 'bus252';
}

let _wbCtrl: AbortController | null = null;

export function abortExoticWorkbench() {
  if (_wbCtrl) {
    _wbCtrl.abort();
    _wbCtrl = null;
  }
}

export function fetchExoticPreset(name: string, signal?: AbortSignal) {
  return getJson<UniversalExoticProductDTO>(
    `/api/v1/exotic/presets/${encodeURIComponent(name)}`,
    signal,
  );
}

export function priceUniversalExotic(
  body: {
    product?: UniversalExoticProductDTO;
    preset?: string;
    market?: MarketSnapshot;
    sim?: { n_paths?: number; n_steps?: number; seed?: number; bridge?: boolean };
    include_risk_summary?: boolean;
  },
  signal?: AbortSignal,
) {
  if (!signal) {
    abortExoticWorkbench();
    _wbCtrl = new AbortController();
    signal = _wbCtrl.signal;
  }
  return postJson<Record<string, unknown>>('/api/v1/exotic/price', body, signal).catch((e) => {
    if ((e as Error)?.name === 'AbortError' || String(e).includes('abort')) {
      const err = new Error('Aborted');
      err.name = 'AbortError';
      throw err;
    }
    throw e;
  });
}

export function solveUniversalExotic(
  body: {
    product?: UniversalExoticProductDTO;
    preset?: string;
    market?: MarketSnapshot;
    solve_for?: 'cpn_rate' | 'ko_rebate' | 'ki_level' | 'put_strike';
    target_price?: number;
    bounds?: number[];
    n_paths?: number;
    seed?: number;
  },
  signal?: AbortSignal,
) {
  if (!signal) {
    abortExoticWorkbench();
    _wbCtrl = new AbortController();
    signal = _wbCtrl.signal;
  }
  return postJson<Record<string, any>>('/api/v1/exotic/solve', body, signal).catch((e) => {
    if ((e as Error)?.name === 'AbortError' || String(e).includes('abort')) {
      const err = new Error('Aborted');
      err.name = 'AbortError';
      throw err;
    }
    throw e;
  });
}

export function fetchUniversalGreeksTermStructure(
  body: {
    product?: UniversalExoticProductDTO;
    preset?: string;
    market?: MarketSnapshot;
    time_slices?: number[];
    spot_range?: number[];
    spot_steps?: number;
    include_greeks?: string[];
    n_paths?: number;
    seed?: number;
    barrier_shift_bps?: number;
    spread_smoothing_width?: number;
    include_risk_summary?: boolean;
  },
  signal?: AbortSignal,
) {
  return postJson<GreeksTermStructureResponse>(
    '/api/v1/exotic/greeks-term-structure',
    body,
    signal,
  ).catch((e) => {
    if ((e as Error)?.name === 'AbortError' || String(e).includes('abort')) {
      const err = new Error('Aborted');
      err.name = 'AbortError';
      throw err;
    }
    throw e;
  });
}
