/**
 * Shared /api/v1 transport. Domain modules import this file.
 * Do not paste new endpoints here.
 */
/**
 * Strongly-typed /api/v1 client with hash cache, debounce helper, AbortController.
 */

export const API_BASE = (import.meta as any).env?.VITE_API_BASE ?? '';


export const cache = new Map<string, { t: number; data: unknown }>();
const CACHE_TTL_MS = 15_000;

function hashKey(obj: unknown): string {
  return JSON.stringify(obj);
}

export async function postJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
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

export function _asRetryOpts(opts?: AbortSignal | FetchRetryOpts): FetchRetryOpts {
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

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { signal });
  if (!res.ok) throw new Error(`${res.status} ${path}: ${await res.text()}`);
  return res.json() as Promise<T>;
}

/** GET with patience for cold starts / gateway 503s — keeps retrying instead of failing once. */
export async function getJsonWithRetry<T>(path: string, opts?: FetchRetryOpts): Promise<T> {
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
