import { getMarketPricingState, setMarketPricingState, PricingSnapshot } from './marketPricingStore';

export function exportSnapshot(): string {
  const s = getMarketPricingState();
  return JSON.stringify(s, null, 2);
}

export function importSnapshot(json: string) {
  const parsed = JSON.parse(json) as PricingSnapshot;
  if (!parsed || parsed.version == null) throw new Error('Invalid snapshot');
  setMarketPricingState(parsed);
  return parsed;
}

export function downloadSnapshot(filename = 'clsa_eqd_snapshot.json') {
  const blob = new Blob([exportSnapshot()], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
