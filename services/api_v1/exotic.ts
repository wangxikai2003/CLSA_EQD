import { API_BASE, postJson } from './http';

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
