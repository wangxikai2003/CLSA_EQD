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

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { signal });
  if (!res.ok) throw new Error(`${res.status} ${path}: ${await res.text()}`);
  return res.json() as Promise<T>;
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

export interface SynthTenorPoint {
  d: string;
  settle: number;
  legs: string[];
  leg_dtm: number[];
  w0: number;
  extrapolated?: boolean;
}

export interface SynthTenorsResponse {
  tenors: SynthTenorKey[];
  tenor_days: Record<string, number>;
  products: { product: string; fut: string; name: string }[];
  levels: Record<string, Partial<Record<SynthTenorKey, SynthTenorPoint[]>>>;
  coverage: Record<string, Record<string, number>>;
  method: string;
}

export function fetchSynthTenors(refresh = false, signal?: AbortSignal) {
  const q = refresh ? '?refresh=true' : '';
  return getJson<SynthTenorsResponse>(`/api/v1/market/synth-tenors${q}`, signal);
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
