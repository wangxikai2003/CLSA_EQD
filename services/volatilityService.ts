import { VolSurfacePoint, VolSurfaceModel, InterpolationMethod } from '../types';
import { SviSlice } from './api';

// --- Helper: Monotone Cubic Spline Interpolation ---
class CubicSpline {
    xs: number[];
    ys: number[];
    ks: number[];

    constructor(xs: number[], ys: number[]) {
        this.xs = xs;
        this.ys = ys;
        const n = xs.length;
        this.ks = new Array(n).fill(0);

        const m = new Array(n - 1);
        for (let i = 0; i < n - 1; i++) {
            m[i] = (ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]);
        }

        this.ks[0] = m[0];
        for (let i = 1; i < n - 1; i++) {
            this.ks[i] = (m[i - 1] + m[i]) / 2;
        }
        this.ks[n - 1] = m[n - 2];
    }

    at(x: number): number {
        const n = this.xs.length;
        if (x <= this.xs[0]) return this.ys[0];
        if (x >= this.xs[n - 1]) return this.ys[n - 1];

        let i = 0;
        while (i < n - 2 && x > this.xs[i + 1]) i++;

        const h = this.xs[i + 1] - this.xs[i];
        const t = (x - this.xs[i]) / h;
        const t2 = t * t;
        const t3 = t * t * t;

        const h00 = 2 * t3 - 3 * t2 + 1;
        const h10 = t3 - 2 * t2 + t;
        const h01 = -2 * t3 + 3 * t2;
        const h11 = t3 - t2;

        return (
            h00 * this.ys[i] +
            h10 * h * this.ks[i] +
            h01 * this.ys[i + 1] +
            h11 * h * this.ks[i + 1]
        );
    }
}

function rawSviW(k: number, a: number, b: number, rho: number, m: number, sigma: number): number {
    const x = k - m;
    return a + b * (rho * x + Math.sqrt(x * x + sigma * sigma));
}

/**
 * JW-SVI surface: per-expiry raw SVI (JW params stored as diagnostics),
 * interpolate total variance linearly in time.
 */
export const fitJwSviSurface = (slices: SviSlice[], spot: number): VolSurfaceModel => {
    const sorted = [...slices].sort((a, b) => a.ttm - b.ttm);

    const predict = (strike: number, t: number): number => {
        if (!sorted.length || strike <= 0 || t <= 0) return 0.2;
        const ts = sorted.map(s => s.ttm);

        const ivFromSlice = (s: SviSlice, kStrike: number, ttm: number) => {
            const k = Math.log(kStrike / s.forward);
            const w = rawSviW(k, s.raw.a, s.raw.b, s.raw.rho, s.raw.m, s.raw.sigma);
            return Math.sqrt(Math.max(w, 1e-12) / Math.max(ttm, 1e-8));
        };

        if (t <= ts[0]) return ivFromSlice(sorted[0], strike, ts[0]);
        if (t >= ts[ts.length - 1]) return ivFromSlice(sorted[sorted.length - 1], strike, ts[ts.length - 1]);

        for (let i = 0; i < ts.length - 1; i++) {
            if (t >= ts[i] && t <= ts[i + 1]) {
                const s1 = sorted[i];
                const s2 = sorted[i + 1];
                const k1 = Math.log(strike / s1.forward);
                const k2 = Math.log(strike / s2.forward);
                const w1 = rawSviW(k1, s1.raw.a, s1.raw.b, s1.raw.rho, s1.raw.m, s1.raw.sigma);
                const w2 = rawSviW(k2, s2.raw.a, s2.raw.b, s2.raw.rho, s2.raw.m, s2.raw.sigma);
                const a = (t - ts[i]) / (ts[i + 1] - ts[i]);
                const w = w1 + a * (w2 - w1);
                return Math.sqrt(Math.max(w, 1e-12) / t);
            }
        }
        return ivFromSlice(sorted[sorted.length - 1], strike, ts[ts.length - 1]);
    };

    return { predict, spot, modelName: 'JW-SVI' };
};

/**
 * Legacy: Spline Smiles + Linear Variance in Time (+ Local/Stoch decorations)
 */
export const fitVolSurface = (
    data: VolSurfacePoint[],
    method: InterpolationMethod = 'Cubic Spline',
    refSpot = 6600
): VolSurfaceModel => {
    if (method === 'JW SVI') {
        // Without slices, fall back to spline on points
        method = 'Cubic Spline';
    }

    const buckets: { [ttm: number]: VolSurfacePoint[] } = {};

    data.forEach(p => {
        const tKey = Math.round(p.ttm * 100) / 100;
        if (!buckets[tKey]) buckets[tKey] = [];
        buckets[tKey].push(p);
    });

    const expiries = Object.keys(buckets).map(Number).sort((a, b) => a - b);
    const smiles: { [ttm: number]: CubicSpline } = {};

    expiries.forEach(t => {
        const points = buckets[t].sort((a, b) => a.strike - b.strike);
        const minK = points[0].strike;
        const maxK = points[points.length - 1].strike;
        const minIV = points[0].iv;
        const maxIV = points[points.length - 1].iv;

        const xs = [minK * 0.5, ...points.map(p => p.strike), maxK * 1.5];
        const ys = [minIV * 1.2, ...points.map(p => p.iv), maxIV * 1.2];

        smiles[t] = new CubicSpline(xs, ys);
    });

    const predict = (k: number, t: number): number => {
        let t1 = expiries[0];
        let t2 = expiries[expiries.length - 1];

        if (t <= t1) t2 = t1;
        else if (t >= t2) t1 = t2;
        else {
            for (let i = 0; i < expiries.length - 1; i++) {
                if (t >= expiries[i] && t <= expiries[i + 1]) {
                    t1 = expiries[i];
                    t2 = expiries[i + 1];
                    break;
                }
            }
        }

        let iv1: number, iv2: number;
        if (t <= expiries[0]) {
            iv1 = smiles[expiries[0]].at(k);
            iv2 = iv1;
        } else if (t >= expiries[expiries.length - 1]) {
            iv1 = smiles[expiries[expiries.length - 1]].at(k);
            iv2 = iv1;
        } else {
            iv1 = smiles[t1].at(k);
            iv2 = smiles[t2].at(k);
        }

        const var1 = iv1 * iv1 * t1;
        const var2 = iv2 * iv2 * t2;

        let totalVar: number;
        if (t2 === t1) totalVar = var1;
        else {
            const ratio = (t - t1) / (t2 - t1);
            totalVar = var1 + (var2 - var1) * ratio;
        }

        let baseVal = Math.sqrt(totalVar / Math.max(t, 1e-8));

        if (method === 'Local Volatility') {
            const moneyness = Math.log(k / refSpot);
            const skewFactor = 1 + 0.5 * Math.exp(-t) * (moneyness * moneyness);
            baseVal = baseVal * skewFactor;
        } else if (method === 'Stochastic Volatility') {
            const moneyness = Math.log(k / refSpot);
            const meanRev = 1 - Math.exp(-2 * t);
            baseVal = baseVal + 0.02 * meanRev + 0.05 * Math.exp(-t) * moneyness * moneyness * 0.5;
        }

        return baseVal;
    };

    return { predict, spot: refSpot, modelName: method };
};

/** Fallback demo data if API offline */
export const RAW_VOL_DATA: VolSurfacePoint[] = [
    { strike: 6000, ttm: 0.08, iv: 0.32 },
    { strike: 6200, ttm: 0.08, iv: 0.28 },
    { strike: 6400, ttm: 0.08, iv: 0.24 },
    { strike: 6600, ttm: 0.08, iv: 0.2 },
    { strike: 6800, ttm: 0.08, iv: 0.17 },
    { strike: 7000, ttm: 0.08, iv: 0.15 },
    { strike: 5800, ttm: 0.25, iv: 0.3 },
    { strike: 6200, ttm: 0.25, iv: 0.26 },
    { strike: 6600, ttm: 0.25, iv: 0.21 },
    { strike: 7000, ttm: 0.25, iv: 0.18 },
    { strike: 7400, ttm: 0.25, iv: 0.16 },
    { strike: 5500, ttm: 0.5, iv: 0.28 },
    { strike: 6000, ttm: 0.5, iv: 0.25 },
    { strike: 6600, ttm: 0.5, iv: 0.22 },
    { strike: 7200, ttm: 0.5, iv: 0.19 },
    { strike: 5000, ttm: 1.0, iv: 0.28 },
    { strike: 6000, ttm: 1.0, iv: 0.24 },
    { strike: 6600, ttm: 1.0, iv: 0.23 },
    { strike: 8000, ttm: 1.0, iv: 0.2 },
];
