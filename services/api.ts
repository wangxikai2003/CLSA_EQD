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

export interface SurfaceResponse {
  product: string;
  trade_date: string;
  spot: number;
  n_slices: number;
  n_iv: number;
  slices: SviSlice[];
  points: { strike: number; ttm: number; iv: number }[];
  market_points: { strike: number; ttm: number; iv: number; cp: string; expiry: string; k: number }[];
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
export const fetchDates = (product: ProductCode) => getJson<string[]>(`/api/dates/${product}`);
export const fetchSurface = (product: ProductCode, date?: string) => {
  const q = date ? `?date=${encodeURIComponent(date)}` : '';
  return getJson<SurfaceResponse>(`/api/surface/${product}${q}`);
};
export const fetchHealth = () => getJson<{ ok: boolean; db: string; size_mb: number }>('/api/health');
