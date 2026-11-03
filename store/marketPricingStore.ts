/**
 * Lightweight global market/pricing state (Zustand-compatible API without hard dep).
 * If zustand is installed, prefer create from 'zustand'; this fallback uses a tiny store.
 */
import { MarketSnapshot } from '../services/api_v1';

export interface PricingSnapshot {
  version: number;
  market: MarketSnapshot;
  product: Record<string, unknown>;
  product_type: string;
  sim: { n_paths: number; n_steps: number; seed: number };
  result?: unknown;
}

type Listener = () => void;

let state: PricingSnapshot = {
  version: 1,
  market: { spots: [100], r: 0.02, q: [0], flat_vols: [0.2], corr: [[1]] },
  product: { tenor: 1, ko_barrier: 1.03, ki_barrier: 0.75, coupon: 0.2 },
  product_type: 'snowball',
  sim: { n_paths: 20000, n_steps: 252, seed: 42 },
};

const listeners = new Set<Listener>();

export function getMarketPricingState() {
  return state;
}

export function setMarketPricingState(partial: Partial<PricingSnapshot>) {
  state = { ...state, ...partial };
  listeners.forEach((l) => l());
}

export function subscribeMarketPricing(listener: Listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** React hook-like helper */
export function useMarketPricingStore<T>(selector: (s: PricingSnapshot) => T): T {
  // Minimal: components should call getMarketPricingState + subscribe via useSyncExternalStore
  return selector(state);
}
