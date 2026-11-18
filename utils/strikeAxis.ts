/** sticky_delta = relative to ATM/forward (moneyness / log-k); strike = absolute K */
export type StrikeAxisMode = 'sticky_delta' | 'strike';

export function paddedDomain(
  values: number[],
  padFrac = 0.05,
  minPad = 0
): [number, number] {
  const xs = values.filter((v) => Number.isFinite(v));
  if (!xs.length) return [0, 1];
  const lo = Math.min(...xs);
  const hi = Math.max(...xs);
  const span = hi - lo;
  if (span <= 0) {
    const pad = Math.max(Math.abs(lo) * 0.05, minPad || 1);
    return [lo - pad, hi + pad];
  }
  const pad = Math.max(span * padFrac, minPad);
  return [lo - pad, hi + pad];
}

export function axisToggleLabel(mode: StrikeAxisMode): string {
  return mode === 'sticky_delta' ? 'δ-sticky (相对 ATM)' : 'Strike (真实行权价)';
}
