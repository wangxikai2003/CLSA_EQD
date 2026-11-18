import { FetchRetryOpts, _asRetryOpts, getJson, getJsonWithRetry, postJson } from './http';

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

export function calibrateJwSvi(req: JwCalibRequest, signal?: AbortSignal) {
  return postJson<JwCalibResponse>('/api/v1/calibration/jwsvi', req, signal);
}

export function fetchYieldCurve(
  params?: { market?: 'CN' | 'US' | string; codes?: string; asof?: string; history?: string },
  signal?: AbortSignal
) {
  const q = new URLSearchParams();
  if (params?.market) q.set('market', params.market);
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
