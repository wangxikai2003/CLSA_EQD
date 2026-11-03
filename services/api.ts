export type ProductCode = 'HO' | 'IO' | 'MO';

export interface JwParams {
  v: number;
  psi: number;
  p: number;
  c: number;
  vtild: number;
}

export interface SviSlice {
  expiry: string;
  ttm: number;
  forward: number;
  n_quotes: number;
  raw: { a: number; b: number; rho: number; m: number; sigma: number };
  jw: JwParams;
  w_atm: number;
  rmse: number;
  atm_iv: number;
}

/** Precomputed JW-SVI IV grid: iv[ttm_i][strike_j] */
export interface SviMatrix {
  spot: number;
  forward_ref: number;
  moneyness: number[];
  strikes: number[];
  ttms: number[];
  iv: (number | null)[][];
  n_rows: number;
  n_cols: number;
}

export interface SurfaceResponse {
  product: string;
  trade_date: string;
  side?: SurfaceSide;
  spot: number;
  r?: number | null;
  rate_code?: string | null;
  n_slices: number;
  n_iv: number;
  slices: SviSlice[];
  points: { strike: number; ttm: number; iv: number }[];
  market_points: { strike: number; ttm: number; iv: number; cp: string; expiry: string; k: number }[];
  matrix?: SviMatrix | null;
  model: string;
}

export interface ProductInfo {
  product: ProductCode;
  name: string;
  fut_prefix: string;
  n_dates: number;
  start: string;
  end: string;
}

const API_BASE = (import.meta as any).env?.VITE_API_BASE ?? '';

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${path}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export const fetchProducts = () => getJson<ProductInfo[]>('/api/products');
export type SurfaceSide = 'otm' | 'call' | 'put';

export const fetchDates = (product: ProductCode, side: SurfaceSide = 'otm') =>
  getJson<string[]>(`/api/dates/${product}?side=${side}`);
export const fetchSurface = (product: ProductCode, date?: string, side: SurfaceSide = 'otm') => {
  const params = new URLSearchParams({ side });
  if (date) params.set('date', date);
  return getJson<SurfaceResponse>(`/api/surface/${product}?${params}`);
};
export const fetchMatrix = (product: ProductCode, date?: string, side: SurfaceSide = 'otm') => {
  const params = new URLSearchParams({ side });
  if (date) params.set('date', date);
  return getJson<{ product: string; trade_date: string; side: string; model: string; matrix: SviMatrix }>(
    `/api/matrix/${product}?${params}`
  );
};
export const fetchHealth = () => getJson<{ ok: boolean; db: string; size_mb: number }>('/api/health');
