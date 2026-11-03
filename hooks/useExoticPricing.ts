import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ExoticPriceRequest,
  ExoticPriceResponse,
  abortPriceExotic,
  debounceAsync,
  priceExotic,
  pricingStreamUrl,
} from '../services/api_v1';
import type { ProgressPoint } from '../components/ProgressBar';

const STREAM_PATH_THRESHOLD = 40000;

export interface PricingProgress {
  progress: number;
  currentPaths: number;
  totalPaths: number;
  runningPrice?: number;
  stdError?: number;
  stage?: string;
  history: ProgressPoint[];
}

export function useExoticPricing() {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ExoticPriceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<PricingProgress | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortPriceExotic();
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      try {
        wsRef.current.send(JSON.stringify({ type: 'cancel' }));
        wsRef.current.close();
      } catch {
        /* ignore */
      }
    }
    wsRef.current = null;
    setLoading(false);
    setProgress(null);
  }, []);

  useEffect(() => () => cancel(), [cancel]);

  const priceViaPost = useCallback(async (req: ExoticPriceRequest, signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const res = await priceExotic(req, signal);
      setResult(res);
      return res;
    } catch (e) {
      if ((e as Error)?.name === 'AbortError') return undefined;
      setError(String(e));
      throw e;
    } finally {
      setLoading(false);
      setProgress(null);
    }
  }, []);

  const priceViaStream = useCallback(async (req: ExoticPriceRequest) => {
    setLoading(true);
    setError(null);
    setProgress({
      progress: 0,
      currentPaths: 0,
      totalPaths: req.sim?.n_paths ?? 20000,
      history: [],
    });

    return new Promise<ExoticPriceResponse | undefined>((resolve, reject) => {
      const ws = new WebSocket(pricingStreamUrl());
      wsRef.current = ws;
      ws.onopen = () => {
        ws.send(JSON.stringify({ type: 'start', request: req }));
      };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === 'progress') {
            const se = Number(msg.std_error || 0);
            const rp = Number(msg.running_price ?? msg.partial_price);
            setProgress((prev) => {
              const hist = [...(prev?.history || [])];
              if (Number.isFinite(rp)) {
                hist.push({
                  paths: msg.current_paths ?? msg.n_done ?? 0,
                  price: rp,
                  lo: rp - 2 * se,
                  hi: rp + 2 * se,
                });
              }
              return {
                progress: msg.progress ?? (msg.pct || 0) / 100,
                currentPaths: msg.current_paths ?? msg.n_done ?? 0,
                totalPaths: msg.total_paths ?? prev?.totalPaths ?? 0,
                runningPrice: rp,
                stdError: se,
                stage: msg.stage,
                history: hist.slice(-40),
              };
            });
          } else if (msg.type === 'result') {
            setResult(msg.payload);
            setLoading(false);
            setProgress(null);
            ws.close();
            resolve(msg.payload);
          } else if (msg.type === 'cancelled') {
            setLoading(false);
            setProgress(null);
            resolve(undefined);
          } else if (msg.type === 'error') {
            setError(msg.message);
            setLoading(false);
            reject(new Error(msg.message));
          }
        } catch (e) {
          setError(String(e));
          setLoading(false);
          reject(e);
        }
      };
      ws.onerror = () => {
        setError('WebSocket error');
        setLoading(false);
        reject(new Error('WebSocket error'));
      };
      ws.onclose = () => {
        wsRef.current = null;
      };
    });
  }, []);

  const price = useCallback(
    async (req: ExoticPriceRequest, opts?: { stream?: boolean; signal?: AbortSignal }) => {
      const n = req.sim?.n_paths ?? 20000;
      const useStream = opts?.stream ?? n >= STREAM_PATH_THRESHOLD;
      if (useStream) return priceViaStream(req);
      abortRef.current?.abort();
      abortRef.current = new AbortController();
      const signal = opts?.signal ?? abortRef.current.signal;
      return priceViaPost(req, signal);
    },
    [priceViaPost, priceViaStream]
  );

  const priceDebounced = useCallback(
    debounceAsync(async (req: ExoticPriceRequest, signal?: AbortSignal) => {
      return price(req, { signal });
    }, 300),
    [price]
  );

  return { loading, result, error, progress, price, priceDebounced, cancel };
}

export default useExoticPricing;
