/**
 * Analytical expiry (t = T) payoff for Snowball / FCN under
 * Before/After KI × hard/soft barrier × nominal/shifted barriers.
 *
 * Smoothing replaces digital 1_{S≥H} with a call-spread ramp on relative spot.
 */

export type ExoticKind = 'snowball' | 'fcn' | 'phoenix';

export interface ExpiryPayoffParams {
  kind: ExoticKind;
  /** Relative KO, e.g. 1.03 */
  ko: number;
  /** Relative KI, e.g. 0.75 */
  ki: number;
  /** Put strike relative, e.g. 1.0 or 0.75 for FCN */
  putStrike: number;
  /** Accrued / KO coupon paid at T if redeemed (fraction of notional), e.g. 0.20 */
  couponAtT: number;
  /** Bonus if never KI/KO (snowball); defaults to couponAtT */
  bonusAtT?: number;
  /** Barrier shift in bps (desk over-hedge) */
  barrierShiftBps?: number;
  /** Smoothing half-width in relative spot units (UI% / 100), e.g. 0.02 */
  smoothingWidth?: number;
}

export type KiRegime = 'before_ki' | 'after_ki';
export type DeskVariant = 'baseline' | 'shift_only' | 'smooth_only' | 'desk';

function softAbove(S: number, H: number, w: number): number {
  if (w <= 0) return S >= H ? 1 : 0;
  const x = (S - (H - w)) / (2 * w);
  return Math.min(1, Math.max(0, x));
}

function softBelow(S: number, H: number, w: number): number {
  if (w <= 0) return S < H ? 1 : 0;
  const x = (H + w - S) / (2 * w);
  return Math.min(1, Math.max(0, x));
}

function effectiveBarriers(ko: number, ki: number, bps: number) {
  const d = Math.abs(bps) / 10000;
  if (d <= 0) return { ko, ki };
  return { ko: ko * (1 - d), ki: ki * (1 + d) };
}

/** Terminal redemption as fraction of notional at relative spot m = S/S0. */
export function expiryPayoffAt(
  m: number,
  params: ExpiryPayoffParams,
  regime: KiRegime,
  variant: DeskVariant,
): number {
  const bps = variant === 'shift_only' || variant === 'desk' ? (params.barrierShiftBps ?? 0) : 0;
  const w = variant === 'smooth_only' || variant === 'desk' ? (params.smoothingWidth ?? 0) : 0;
  const { ko, ki } = effectiveBarriers(params.ko, params.ki, bps);
  const K = Math.max(params.putStrike, 1e-12);
  const cpn = params.couponAtT;
  const bonus = params.bonusAtT ?? cpn;

  const pKO = softAbove(m, ko, w);

  if (params.kind === 'fcn') {
    // FCN: KO → 100%; else before KI capital protected 100%; after KI min(1, S/K)
    // Coupons already paid along path — at T show redemption only (1 or put).
    const put = Math.min(1, m / K);
    if (regime === 'before_ki') {
      // European AXKI / protected: blend KO vs 100%
      return pKO * 1.0 + (1 - pKO) * 1.0;
    }
    // after KI: KO vs put
    return pKO * 1.0 + (1 - pKO) * put;
  }

  // Snowball / phoenix-like terminal:
  // KO → 1 + coupon; after KI no-KO → put; before KI no-KO → 1 + bonus
  const koPay = 1 + cpn;
  const putPay = Math.min(1, m / K);
  const safePay = 1 + bonus;

  if (regime === 'after_ki') {
    return pKO * koPay + (1 - pKO) * putPay;
  }

  // before KI: physically only valid for m > ki under daily monitoring;
  // still evaluate math; UI may NaN-mask below KI.
  // If also soft-KI at T for european flavour, mix put weight
  const pKI = softBelow(m, ki, w);
  // never KI path: safe; if soft-KI triggers at T, go to put (euro AXKI at expiry)
  const noKoPay = (1 - pKI) * safePay + pKI * putPay;
  return pKO * koPay + (1 - pKO) * noKoPay;
}

export interface ExpiryGridPoint {
  m: number; // S/S0
  moneyness: number; // %
  /** baseline hard, no shift */
  before_ki_baseline: number;
  after_ki_baseline: number;
  before_ki_shift: number;
  after_ki_shift: number;
  before_ki_smooth: number;
  after_ki_smooth: number;
  before_ki_desk: number;
  after_ki_desk: number;
  /** mask: false if American-style before KI invalid (m <= ki) */
  before_ki_valid: boolean;
}

export function buildExpiryPayoffGrid(
  params: ExpiryPayoffParams,
  opts?: { mLo?: number; mHi?: number; steps?: number; dailyKi?: boolean },
): ExpiryGridPoint[] {
  const mLo = opts?.mLo ?? 0.5;
  const mHi = opts?.mHi ?? 1.4;
  const steps = opts?.steps ?? 161;
  const dailyKi = opts?.dailyKi ?? true;
  const out: ExpiryGridPoint[] = [];
  for (let i = 0; i < steps; i++) {
    const m = mLo + (mHi - mLo) * (i / Math.max(steps - 1, 1));
    const valid = !dailyKi || m > params.ki + 1e-12;
    const row: ExpiryGridPoint = {
      m,
      moneyness: m * 100,
      before_ki_baseline: expiryPayoffAt(m, params, 'before_ki', 'baseline'),
      after_ki_baseline: expiryPayoffAt(m, params, 'after_ki', 'baseline'),
      before_ki_shift: expiryPayoffAt(m, params, 'before_ki', 'shift_only'),
      after_ki_shift: expiryPayoffAt(m, params, 'after_ki', 'shift_only'),
      before_ki_smooth: expiryPayoffAt(m, params, 'before_ki', 'smooth_only'),
      after_ki_smooth: expiryPayoffAt(m, params, 'after_ki', 'smooth_only'),
      before_ki_desk: expiryPayoffAt(m, params, 'before_ki', 'desk'),
      after_ki_desk: expiryPayoffAt(m, params, 'after_ki', 'desk'),
      before_ki_valid: valid,
    };
    if (!valid) {
      row.before_ki_baseline = NaN;
      row.before_ki_shift = NaN;
      row.before_ki_smooth = NaN;
      row.before_ki_desk = NaN;
    }
    out.push(row);
  }
  return out;
}

/** Central-difference Greeks on relative payoff vs absolute spot S = m * S0. */
export function fdGreeksFromPayoff(
  grid: ExpiryGridPoint[],
  key: keyof ExpiryGridPoint,
  S0: number,
): { m: number; moneyness: number; pv: number; delta: number; gamma: number }[] {
  const n = grid.length;
  return grid.map((g, i) => {
    const pv = Number(g[key]);
    if (!Number.isFinite(pv)) {
      return { m: g.m, moneyness: g.moneyness, pv: NaN, delta: NaN, gamma: NaN };
    }
    let delta = 0;
    let gamma = 0;
    if (i > 0 && i < n - 1) {
      const pm = Number(grid[i - 1][key]);
      const pp = Number(grid[i + 1][key]);
      if (Number.isFinite(pm) && Number.isFinite(pp)) {
        const dS = (grid[i + 1].m - grid[i - 1].m) * S0;
        delta = (pp - pm) / Math.max(dS, 1e-12);
        const h = 0.5 * dS;
        gamma = (pp - 2 * pv + pm) / Math.max(h * h, 1e-12);
      }
    } else if (i === 0 && n > 1) {
      const pp = Number(grid[1][key]);
      if (Number.isFinite(pp)) {
        const dS = (grid[1].m - grid[0].m) * S0;
        delta = (pp - pv) / Math.max(dS, 1e-12);
      }
    } else if (i === n - 1 && n > 1) {
      const pm = Number(grid[n - 2][key]);
      if (Number.isFinite(pm)) {
        const dS = (grid[n - 1].m - grid[n - 2].m) * S0;
        delta = (pv - pm) / Math.max(dS, 1e-12);
      }
    }
    return { m: g.m, moneyness: g.moneyness, pv, delta, gamma };
  });
}
