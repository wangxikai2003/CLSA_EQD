import { API_BASE, FetchRetryOpts, _asRetryOpts, getJsonWithRetry } from './http';

/* ---------- Realized Correlation ---------- */

export interface CorrTarget {
  symbol: string;
  name: string;
  subregion: string;
  currency: string;
  capabilities: string[];
  instrument?: string;
}

/** AssetClass → Market → Instrument (INDEX / SINGLE_STOCK / FX) → targets */
export type CorrUniverseTree = Record<string, Record<string, Record<string, CorrTarget[]>>>;

export interface CorrUniverseResponse {
  tree: CorrUniverseTree;
  targets: Array<
    CorrTarget & {
      market: string;
      asset_class: string;
      instrument?: string;
      kind?: string;
      type?: string;
    }
  >;
  presets: { id: string; label: string; symbols: string[] }[];
  rolling_windows: number[];
  asset_classes?: string[];
  source: string;
}

export interface CorrPairwiseResponse {
  symbol1: string;
  symbol2: string;
  rolling_win: number;
  lookback_months: number;
  use_rules: boolean;
  pad: number;
  alpha: number;
  rule_desc: string;
  dates: string[];
  rolling_corr: (number | null)[];
  bands: { p10: number; p25: number; p50: number; p75: number; p90: number };
  realized_last: number | null;
  desk_mark: number | null;
  lambda: number | null;
  n_obs: number;
}

export interface CorrMatrixResponse {
  symbols: string[];
  metric: string;
  matrix: (number | null)[][];
  details: Array<{
    symbol1: string;
    symbol2: string;
    ok: boolean;
    realized_last?: number | null;
    desk_mark?: number | null;
    rule_desc?: string;
    value?: number | null;
    error?: string;
  }>;
  rolling_win: number;
}

export interface CorrSpreadsResponse {
  mode: string;
  label: string;
  series: Array<{ d: string; v: number; short?: number; long?: number; pair1?: number; pair2?: number }>;
  stats: {
    mean?: number;
    std?: number;
    last?: number;
    pctl?: number;
    z?: number;
    band_1?: number[];
    band_2?: number[];
    low?: number;
    high?: number;
  };
}

export function fetchCorrUniverse(opts?: AbortSignal | FetchRetryOpts) {
  return getJsonWithRetry<CorrUniverseResponse>(`/api/v1/correlation/universe`, _asRetryOpts(opts));
}

export function fetchCorrPairwise(
  params: {
    symbol1: string;
    symbol2: string;
    rolling_win?: number;
    lookback_months?: number;
    use_rules?: boolean;
    pad_override?: number | null;
  },
  opts?: AbortSignal | FetchRetryOpts
) {
  const q = new URLSearchParams({
    symbol1: params.symbol1,
    symbol2: params.symbol2,
    rolling_win: String(params.rolling_win ?? 63),
    lookback_months: String(params.lookback_months ?? 24),
    use_rules: String(params.use_rules ?? true),
  });
  if (params.pad_override != null) q.set('pad_override', String(params.pad_override));
  return getJsonWithRetry<CorrPairwiseResponse>(`/api/v1/correlation/pairwise?${q}`, _asRetryOpts(opts));
}

export async function postCorrMatrix(
  body: {
    symbols: string[];
    rolling_win?: number;
    metric?: 'realized' | 'desk_mark' | 'diff';
    use_rules?: boolean;
    lookback_months?: number;
  },
  opts?: AbortSignal | FetchRetryOpts
): Promise<CorrMatrixResponse> {
  const o = _asRetryOpts(opts);
  const res = await fetch(`${API_BASE}/api/v1/correlation/matrix`, {
    method: 'POST',
    signal: o.signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      symbols: body.symbols,
      rolling_win: body.rolling_win ?? 63,
      metric: body.metric ?? 'realized',
      use_rules: body.use_rules ?? true,
      lookback_months: body.lookback_months ?? 24,
    }),
  });
  if (!res.ok) throw new Error(`${res.status} /api/v1/correlation/matrix: ${await res.text()}`);
  return res.json() as Promise<CorrMatrixResponse>;
}

export function fetchCorrSpreads(
  params: {
    mode: 'term' | 'cross';
    symbol1: string;
    symbol2: string;
    symbol3?: string;
    symbol4?: string;
    short_win?: number;
    long_win?: number;
  },
  opts?: AbortSignal | FetchRetryOpts
) {
  const q = new URLSearchParams({
    mode: params.mode,
    symbol1: params.symbol1,
    symbol2: params.symbol2,
    short_win: String(params.short_win ?? 21),
    long_win: String(params.long_win ?? 126),
  });
  if (params.symbol3) q.set('symbol3', params.symbol3);
  if (params.symbol4) q.set('symbol4', params.symbol4);
  return getJsonWithRetry<CorrSpreadsResponse>(`/api/v1/correlation/spreads?${q}`, _asRetryOpts(opts));
}
