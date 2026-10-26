
import { ExoticProduct, ExoticType, BarrierProduct, BarrierType, OptionType, OptionLeg, PositionType } from '../types';

// Standard Normal Generator (Box-Muller)
const randn_bm = (): number => {
    let u = 0, v = 0;
    while(u === 0) u = Math.random(); 
    while(v === 0) v = Math.random();
    return Math.sqrt( -2.0 * Math.log( u ) ) * Math.cos( 2.0 * Math.PI * v );
};

// Seeded Random Number Generator (Mulberry32)
// Provides deterministic noise for smooth chart rendering
const createSeededRng = (seed: number) => {
    let a = seed;
    return () => {
      let t = a += 0x6D2B79F5;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    }
}

const randn_custom = (rng: () => number): number => {
    let u = 0, v = 0;
    while(u === 0) u = rng(); 
    while(v === 0) v = rng();
    return Math.sqrt( -2.0 * Math.log( u ) ) * Math.cos( 2.0 * Math.PI * v );
};

// Simulation Core - Optimized to reuse buffer
// Removed return type of number[] to avoid allocation
const simulatePath = (
    S0: number, 
    drift: number, 
    diff: number, 
    steps: number, 
    buffer: Float32Array
): void => {
    buffer[0] = S0;
    let currentS = S0;
    
    for (let i = 1; i <= steps; i++) {
        currentS = currentS * Math.exp(drift + diff * randn_bm());
        buffer[i] = currentS;
    }
};

/**
 * Simulates a 2-Asset Worst-Of basket to price a vanilla structure
 * Used for Correlation Risk Analysis in Tab 1
 */
export const simulateWorstOfPrice = (
    legs: OptionLeg[],
    spot: number,
    vol: number, // Use ATM or flat vol for correlation risk approx
    r: number,
    t: number,
    rho: number,
    simulations: number = 1000,
    seed?: number // Optional seed for Common Random Numbers (CRN) technique
): number => {
    if (t <= 0) {
        // Intrinsic
        let totalPayoff = 0;
        legs.forEach(leg => {
            const dir = leg.position === PositionType.LONG ? 1 : -1;
            let val = 0;
            if (leg.type === OptionType.CALL) val = Math.max(0, spot - leg.strike);
            else val = Math.max(0, leg.strike - spot);
            totalPayoff += val * dir * leg.quantity;
        });
        return totalPayoff;
    }

    // Initialize RNG
    // If seed is provided, use it to reset RNG for this call. 
    // This ensures consistent paths across different Spot prices if called in a loop.
    const rng = seed !== undefined ? createSeededRng(seed) : Math.random;
    const getNorm = seed !== undefined ? () => randn_custom(rng) : randn_bm;

    // Geometric Brownian Motion params
    const drift = (r - 0.5 * vol * vol) * t;
    const diff = vol * Math.sqrt(t);
    const sqrt1MinusRhoSq = Math.sqrt(1 - rho * rho);

    let totalPayoff = 0;

    for (let i = 0; i < simulations; i++) {
        const z1 = getNorm();
        const z2 = getNorm();

        // Correlated Standard Normals (Cholesky)
        // W1 = Z1
        // W2 = rho*Z1 + sqrt(1-rho^2)*Z2
        const eps1 = z1;
        const eps2 = rho * z1 + sqrt1MinusRhoSq * z2;

        // Asset Prices at Maturity
        const S1_T = spot * Math.exp(drift + diff * eps1);
        const S2_T = spot * Math.exp(drift + diff * eps2);

        // Worst Of Performance (rebased to initial spot for payout calculation)
        // Assuming the structure is on the "Worst Of Basket" directly
        const S_worst = Math.min(S1_T, S2_T);

        // Calculate Structure Payoff based on S_worst
        let pathPayoff = 0;
        for (const leg of legs) {
            const dir = leg.position === PositionType.LONG ? 1 : -1;
            let val = 0;
            if (leg.type === OptionType.CALL) val = Math.max(0, S_worst - leg.strike);
            else val = Math.max(0, leg.strike - S_worst);
            
            pathPayoff += val * dir * leg.quantity;
        }
        totalPayoff += pathPayoff;
    }

    return (totalPayoff / simulations) * Math.exp(-r * t);
};

export const priceExotic = (
    product: ExoticProduct, 
    spot: number,
    volOverride?: number, // Allow overriding vol for Vega calc
    maturityOverride?: number, // Allow overriding maturity for Theta calc
    simulations: number = 1000 
): number => {
    const r = product.r;
    const vol = volOverride !== undefined ? volOverride : product.vol;
    const maturity = maturityOverride !== undefined ? maturityOverride : product.maturity;
    const notional = product.notional;
    
    // Safety check
    if (maturity <= 0) return 0;

    // Simulation Parameters
    const stepsPerYear = 252; 
    const totalSteps = Math.floor(maturity * stepsPerYear);
    const dt = 1 / stepsPerYear;

    // Pre-calc constants to save ops inside loop
    const drift = (r - 0.5 * vol * vol) * dt;
    const diff = vol * Math.sqrt(dt);

    // Memory Optimization: Allocate buffer ONCE per price call, reuse for all simulations
    // This avoids creating 'simulations' * 'totalSteps' worth of arrays that need GC
    const pathBuffer = new Float32Array(totalSteps + 1);

    let totalPayoff = 0;

    for (let i = 0; i < simulations; i++) {
        // Reuse the buffer
        simulatePath(spot, drift, diff, totalSteps, pathBuffer);
        // pathBuffer is now filled with the new path
        const path = pathBuffer; 

        let payoff = 0;

        if (product.type === ExoticType.SNOWBALL) {
            const kiPrice = product.spotRef * (product.kiBarrier || 0.75);
            const initialKoPct = product.koBarrier || 1.03;
            
            // New Coupons: Separate KO and Dividend
            const koCoupon = product.koCoupon || 0.2; 
            const divCoupon = product.dividendCoupon || 0.2; // Bonus if Safe

            const stepDown = product.koStepDown || 0;
            
            let knockedOut = false;
            let knockedIn = false;
            
            // Observation Logic
            // Default to Monthly (21 days) if not specified
            let obsInterval = 21;
            if (product.obsFreq === 'Daily') obsInterval = 1;
            else if (product.obsFreq === 'Monthly') obsInterval = 21;
            else if (product.obsFreq === 'Yearly') obsInterval = 252;
            else if (product.obsFreq === 'European') obsInterval = totalSteps; // Check only at end

            for (let t = 1; t <= totalSteps; t++) {
                const s_t = path[t];
                
                // KI Check (Always Daily in standard Snowball)
                if (s_t < kiPrice) knockedIn = true;

                // KO Check (Per Frequency)
                if (t % obsInterval === 0) {
                    // Step Down Logic
                    const obsCount = (t / obsInterval) - 1;
                    const currentKoPct = initialKoPct - (obsCount * stepDown);
                    const currentKoPrice = product.spotRef * currentKoPct;

                    if (s_t > currentKoPrice) {
                        knockedOut = true;
                        // Payoff: Principal + Accrued KO Coupon
                        const timeElapsed = t / stepsPerYear;
                        payoff = 1 + koCoupon * timeElapsed;
                        // Discount back
                        payoff = payoff * Math.exp(-r * timeElapsed); 
                        break;
                    }
                }
            }

            if (!knockedOut) {
                // At maturity
                const S_T = path[totalSteps];
                if (knockedIn) {
                    // Massive Loss Scenario: Track Index downside
                    // Ratio payoff capped at 1 (though usually just S_T/S_0)
                    payoff = Math.min(1, S_T / product.spotRef);
                } else {
                    // Protected Scenario (Bonus Coupon)
                    payoff = 1 + divCoupon * maturity;
                }
                payoff = payoff * Math.exp(-r * maturity);
            }

        } else if (product.type === ExoticType.SHARKFIN) {
            const K = product.spotRef * (product.strike || 1.0);
            const B = product.spotRef * (product.barrier || 1.2);
            const rebate = product.rebate || 0; 
            
            let barrierHit = false;
            
            for (let t = 1; t <= totalSteps; t++) {
                if (path[t] >= B) {
                    barrierHit = true;
                    break;
                }
            }

            if (barrierHit) {
                payoff = rebate; 
            } else {
                const S_T = path[totalSteps];
                payoff = Math.max(0, (S_T - K) / product.spotRef); 
            }
            payoff = payoff * Math.exp(-r * maturity);
        }

        totalPayoff += payoff;
    }

    return (totalPayoff / simulations) * notional;
};

// Calculate Greeks using Finite Difference
export const calculateExoticGreeks = (product: ExoticProduct, spot: number) => {
    // 1. Delta & Gamma (Spot bump)
    const ds = spot * 0.01; 
    // Use lower simulations for Greeks to keep it fast, but consistent
    const simCount = 1000; 

    const p_base = priceExotic(product, spot, undefined, undefined, simCount);
    const p_up = priceExotic(product, spot + ds, undefined, undefined, simCount);
    const p_down = priceExotic(product, spot - ds, undefined, undefined, simCount);

    const delta = (p_up - p_down) / (2 * ds);
    const gamma = (p_up - 2 * p_base + p_down) / (ds * ds);

    // 2. Vega (Vol bump 1%)
    const dVol = 0.01;
    const p_vol_up = priceExotic(product, spot, product.vol + dVol, undefined, simCount);
    const p_vol_down = priceExotic(product, spot, product.vol - dVol, undefined, simCount);
    const vega = (p_vol_up - p_vol_down) / 2; // Change in price for 1% vol change (already bumped by 0.01)

    // 3. Theta (Time bump 1 day)
    const oneDay = 1/365;
    let theta = 0;
    if (product.maturity > oneDay) {
        const p_time_less = priceExotic(product, spot, undefined, product.maturity - oneDay, simCount);
        theta = p_time_less - p_base; // New Price (closer to expiry) - Old Price
    }

    return {
        price: p_base,
        delta: delta,
        gamma: gamma,
        vega: vega,
        theta: theta
    };
};

export const generateExoticAnalysis = (product: ExoticProduct, currentSpot: number, rangePercent: number = 0.20) => {
    const steps = 15; 
    const data = [];
    
    const start = currentSpot * (1 - rangePercent);
    const end = currentSpot * (1 + rangePercent);
    const stepSize = (end - start) / steps;

    for (let s = start; s <= end; s+= stepSize) {
        const res = calculateExoticGreeks(product, s);
        
        data.push({
            spot: s,
            moneyness: (s / currentSpot) * 100, 
            price: res.price,
            delta: res.delta,
            gamma: res.gamma,
            vega: res.vega,
            theta: res.theta
        });
    }
    return data;
};

// ... runMonteCarlo (Barrier) logic omitted for brevity as it is unused in Tab 2 main flow but follows similar pattern ...
// If used, similar buffer optimization should be applied.
export const runMonteCarlo = (
    product: BarrierProduct,
    spot: number,
    vol: number,
    r: number,
    simulations: number = 5000
): { price: number, eventProb: number } => {
    const { maturity, type, barrierType, barrierLevel, strike, rebate, observationFreq } = product;
    const steps = Math.floor(maturity * observationFreq);
    const dt = maturity / steps;
    const drift = (r - 0.5 * vol * vol) * dt;
    const diff = vol * Math.sqrt(dt);
    
    const pathBuffer = new Float32Array(steps + 1);

    let totalPayoff = 0;
    let eventCount = 0;

    for (let i = 0; i < simulations; i++) {
        // Reuse buffer
        simulatePath(spot, drift, diff, steps, pathBuffer);
        const path = pathBuffer;

        let barrierHit = false;
        for (let t = 1; t <= steps; t++) {
            const S_t = path[t];
            if (
                (barrierType === BarrierType.UP_OUT && S_t >= barrierLevel) || 
                (barrierType === BarrierType.UP_IN && S_t >= barrierLevel) ||
                (barrierType === BarrierType.DOWN_OUT && S_t <= barrierLevel) ||
                (barrierType === BarrierType.DOWN_IN && S_t <= barrierLevel)
            ) { barrierHit = true; break; }
        }
        if (barrierHit) eventCount++;
        
        const S_T = path[steps];
        let vanillaPayoff = 0;
        if (type === OptionType.CALL) vanillaPayoff = Math.max(0, S_T - strike);
        else vanillaPayoff = Math.max(0, strike - S_T);

        let pathPayoff = 0;
        if (barrierType === BarrierType.UP_OUT || barrierType === BarrierType.DOWN_OUT) {
            pathPayoff = barrierHit ? rebate : vanillaPayoff;
        } else {
            pathPayoff = barrierHit ? vanillaPayoff : rebate;
        }
        totalPayoff += pathPayoff * Math.exp(-r * maturity);
    }
    return { price: totalPayoff / simulations, eventProb: eventCount / simulations };
};
