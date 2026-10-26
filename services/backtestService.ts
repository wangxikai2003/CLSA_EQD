
import { ExoticProduct, ExoticType, BacktestSummary, HistoricalDataPoint } from '../types';
import { SPX_CSV_DATA } from './spxData';

// Generates a robust synthetic S&P 500 like history (2000-2025)
// used when CSV data is missing or insufficient.
const generateFallbackHistory = (): HistoricalDataPoint[] => {
    const points: HistoricalDataPoint[] = [];
    let currentPrice = 1400.00; // Approx Year 2000 level
    const startDate = new Date('2000-01-01');
    const totalDays = 9125; // 25 years * 365

    for (let i = 0; i < totalDays; i++) {
        const date = new Date(startDate);
        date.setDate(startDate.getDate() + i);
        
        // Skip weekends
        const day = date.getDay();
        if (day === 0 || day === 6) continue;

        // Market Dynamics Simulation
        // 1. Long term drift (approx 7% annualized)
        const drift = 0.07 / 252;
        // 2. Volatility (approx 20%)
        const vol = 0.20 / Math.sqrt(252);
        // 3. Random shock
        const shock = (Math.random() + Math.random() + Math.random() - 1.5); // Approx Normal Distribution

        // 4. Occasional Market Crashes/Jumps (Fat tails)
        let jump = 0;
        if (Math.random() < 0.001) jump = -0.05; // 0.1% chance of 5% drop
        if (Math.random() < 0.001) jump = 0.03;  // 0.1% chance of 3% rally

        currentPrice = currentPrice * Math.exp(drift + vol * shock + jump);
        
        points.push({
            date: date.toISOString().split('T')[0],
            price: parseFloat(currentPrice.toFixed(2))
        });
    }
    return points;
};

// Robust CSV Parser
const parseCsvData = (): HistoricalDataPoint[] => {
    // If provided CSV is too short (e.g. dummy/snippet), use fallback
    if (!SPX_CSV_DATA || SPX_CSV_DATA.length < 2000) {
        console.warn("SPX Data insufficient, using synthetic history.");
        return generateFallbackHistory();
    }
    
    const lines = SPX_CSV_DATA.trim().split('\n');
    const data: HistoricalDataPoint[] = [];
    
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i].trim();
        // Heuristic to skip headers or malformed lines
        if (!line || /[a-z]/i.test(line[0])) continue; 

        const parts = line.split(',');
        if (parts.length >= 2) {
            const dateStr = parts[0].trim();
            const priceVal = parseFloat(parts[1]);

            if (dateStr.length >= 10 && !isNaN(priceVal)) {
                data.push({ date: dateStr, price: priceVal });
            }
        }
    }

    // Double check parsed length
    if (data.length < 1000) return generateFallbackHistory();
    
    return data;
};

export const generateMockHistory = (): HistoricalDataPoint[] => {
    return parseCsvData();
};

const emptySummary = (): { summary: BacktestSummary, historyWithLosses: HistoricalDataPoint[] } => ({
    summary: {
        totalExpired: 0, knockOutCount: 0, knockOutRate: 0,
        dividendCount: 0, principalCount: 0, lossCount: 0,
        winRate: 0, avgDuration: 0, totalActive: 0,
        activeNoKI: 0, activeKI: 0, kiThenKoCount: 0, kiThenKoRate: 0
    },
    historyWithLosses: []
});

// Main Rolling Backtest Logic
export const runRollingBacktest = (
    fullHistory: HistoricalDataPoint[], 
    product: ExoticProduct,
    startDateStr?: string,
    endDateStr?: string
): { summary: BacktestSummary, historyWithLosses: HistoricalDataPoint[] } => {
    
    if (!fullHistory || fullHistory.length === 0) return emptySummary();

    // 1. Determine Index Range
    let startIndex = 0;
    let endIndex = fullHistory.length - 1;

    // Strict Date Matching
    if (startDateStr) {
        const foundIdx = fullHistory.findIndex(h => h.date >= startDateStr);
        if (foundIdx === -1) {
             // Requested start date is after all available data
             if (startDateStr > fullHistory[fullHistory.length - 1].date) return emptySummary();
             // If not found but within range (shouldn't happen with findIndex >=), default to 0
        } else {
            startIndex = foundIdx;
        }
    }

    if (endDateStr) {
        // Find last index where date <= endDateStr
        // Since list is sorted ascending
        let foundIdx = -1;
        for (let i = startIndex; i < fullHistory.length; i++) {
            if (fullHistory[i].date > endDateStr) {
                foundIdx = i - 1;
                break;
            }
        }
        // If we never exceeded, it means end date covers whole history
        if (foundIdx === -1) endIndex = fullHistory.length - 1;
        else endIndex = foundIdx;
    }

    if (startIndex > endIndex) return emptySummary();

    // --- PRODUCT PARAMETERS ---
    const tenorYears = product.maturity || 2.0;
    
    // Total observation days needed for full maturity
    const maxObsDays = Math.ceil(tenorYears * 252);

    // --- COUNTERS ---
    let expiredCount = 0;
    let koCount = 0;       // Snowball: Autocall | Sharkfin: Barrier Hit
    let dividendCount = 0; // Snowball: Coupon (No KO/KI) | Sharkfin: Participation Win
    let lossCount = 0;     // Snowball: Loss (KI) | Sharkfin: Zero Payoff
    let kiThenKoCount = 0; // Only Snowball

    let activeCount = 0;
    let activeKI = 0;      // Snowball Active KI
    let activeNoKI = 0;    // Snowball Active Safe | Sharkfin Active Safe

    let totalDurationDays = 0;
    const lossDates = new Set<string>();

    // 2. SIMULATION LOOP
    // Iterate through every possible entry date in the range
    for (let i = startIndex; i <= endIndex; i++) {
        const entryPrice = fullHistory[i].price;
        const entryDate = fullHistory[i].date;
        
        const daysRemaining = fullHistory.length - 1 - i;
        const runLimit = Math.min(maxObsDays, daysRemaining);
        
        let isKnockOut = false; // Snowball KO or Sharkfin Barrier
        let isKnockIn = false; // Snowball only
        let duration = 0;

        // --- SNOWBALL LOGIC ---
        if (product.type === ExoticType.SNOWBALL) {
            const kiPct = product.kiBarrier || 0.75;  
            const koPct = product.koBarrier || 1.03;
            const obsFreq = 21; // Monthly

            for (let t = 1; t <= runLimit; t++) {
                const currentPrice = fullHistory[i + t].price;
                const ratio = currentPrice / entryPrice;

                // KI Check (Daily)
                if (ratio < kiPct) {
                    isKnockIn = true;
                }

                // KO Check (Monthly)
                if (t % obsFreq === 0) {
                    if (ratio > koPct) {
                        isKnockOut = true;
                        duration = t;
                        break; 
                    }
                }
            }

            // --- Aggregation for Snowball ---
            if (isKnockOut) {
                expiredCount++;
                koCount++;
                totalDurationDays += duration;
                if (isKnockIn) kiThenKoCount++;
            } else {
                if (daysRemaining < maxObsDays) {
                    // Active
                    activeCount++;
                    if (isKnockIn) activeKI++; else activeNoKI++;
                } else {
                    // Expired
                    expiredCount++;
                    totalDurationDays += maxObsDays;
                    if (isKnockIn) {
                        lossCount++;
                        lossDates.add(entryDate);
                    } else {
                        dividendCount++;
                    }
                }
            }

        // --- SHARKFIN LOGIC ---
        } else if (product.type === ExoticType.SHARKFIN) {
            const strikePct = product.strike || 1.0;
            const barrierPct = product.barrier || 1.2;
            
            // Iterate Path to check Barrier (Up-and-Out)
            for (let t = 1; t <= runLimit; t++) {
                const currentPrice = fullHistory[i + t].price;
                const ratio = currentPrice / entryPrice;

                // Continuous Barrier Check
                if (ratio >= barrierPct) {
                    isKnockOut = true; // Barrier Hit
                    duration = t;
                    break;
                }
            }

            // --- Aggregation for Sharkfin ---
            if (isKnockOut) {
                // Barrier Hit -> Rebate (Usually treated as a "small win" or "return of principal + small coupon")
                // We map this to koCount for "Barrier Events"
                expiredCount++;
                koCount++;
                totalDurationDays += duration;
            } else {
                if (daysRemaining < maxObsDays) {
                    // Active (Safe, Barrier not hit yet)
                    activeCount++;
                    activeNoKI++; 
                } else {
                    // Expired (Barrier never hit)
                    expiredCount++;
                    totalDurationDays += maxObsDays;
                    
                    const finalRatio = fullHistory[i + maxObsDays].price / entryPrice;
                    // Check Payoff: max(0, S_T - K)
                    if (finalRatio > strikePct) {
                        // In The Money -> Participation Win
                        dividendCount++;
                    } else {
                        // Out of The Money -> Zero Payoff (Loss of Option Premium / Opportunity)
                        lossCount++;
                        lossDates.add(entryDate);
                    }
                }
            }
        }
    }

    // 3. AGGREGATE RESULTS
    const denom = expiredCount > 0 ? expiredCount : 1; 

    const summary: BacktestSummary = {
        totalExpired: expiredCount,
        knockOutCount: koCount,
        knockOutRate: koCount / denom,
        dividendCount: dividendCount,
        principalCount: 0,
        lossCount: lossCount,
        winRate: (koCount + dividendCount) / denom, // For Sharkfin: Rebate(KO) + Participation(Div)
        avgDuration: (totalDurationDays / denom) / 21, 
        
        totalActive: activeCount,
        activeNoKI: activeNoKI,
        activeKI: activeKI,
        
        kiThenKoCount: kiThenKoCount,
        kiThenKoRate: kiThenKoCount / denom 
    };

    const historySlice = fullHistory.slice(startIndex, endIndex + 1).map(h => ({
        ...h,
        isLossEvent: lossDates.has(h.date)
    }));

    return { summary, historyWithLosses: historySlice };
};
