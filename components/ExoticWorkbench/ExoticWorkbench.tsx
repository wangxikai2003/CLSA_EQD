import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Play, RefreshCw } from 'lucide-react';
import UniversalConfigPanel from './UniversalConfigPanel';
import RiskDashboard from './RiskDashboard';
import GreeksTermStructureView from '../GreeksTermStructureView';
import {
  abortExoticWorkbench,
  fetchExoticPreset,
  fetchUniversalGreeksTermStructure,
  priceUniversalExotic,
  solveUniversalExotic,
  type GreeksTermStructureResponse,
  type UniversalExoticProductDTO,
} from '../../services/api_v1';

interface Props {
  spot: number;
  r: number;
  vol: number;
}

const DEFAULT_PRODUCT: UniversalExoticProductDTO = {
  name: 'Snowball Classic',
  tenor: 2,
  notional: 1,
  asset: { underlying: ['SPX'], basket_type: 'WORSTOF' },
  funding: {
    funding_index: 'FIXED',
    funding_spread: 0,
    funding_scale: 1,
    funding_freq: '1M',
  },
  ko: {
    ko_freq: '1M',
    ko_monitoring_type: 'EUROPEAN',
    ko_schedule: Array.from({ length: 24 }, () => 1.03),
  },
  ki: {
    ki_level: 0.75,
    ki_monitoring_type: 'DAILY',
    put_strike: 1.0,
    put_participation: -1,
    settlement_type: 'CASH',
  },
  coupon: {
    cpn_type: 'SNOWBALL_ACCRUED',
    cpn_rate: 0.2,
    ko_rebate: 0.2,
    memory_cpn: false,
  },
};

const ExoticWorkbench: React.FC<Props> = ({ spot, r, vol }) => {
  const [product, setProduct] = useState<UniversalExoticProductDTO>(DEFAULT_PRODUCT);
  const [mode, setMode] = useState<'price' | 'solve'>('price');
  const [solveFor, setSolveFor] = useState<'cpn_rate' | 'ko_rebate' | 'ki_level' | 'put_strike'>(
    'cpn_rate',
  );
  const [targetPrice, setTargetPrice] = useState(0.99);
  const [loading, setLoading] = useState(false);
  const [gtsLoading, setGtsLoading] = useState(false);
  const [risk, setRisk] = useState<Record<string, any> | null>(null);
  const [solveDiag, setSolveDiag] = useState<Record<string, any> | null>(null);
  const [gts, setGts] = useState<GreeksTermStructureResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const market = useMemo(() => {
    const n = product.asset.underlying.length;
    return {
      spots: Array.from({ length: n }, () => spot),
      r,
      q: product.asset.div_yields || Array.from({ length: n }, () => 0),
      flat_vols: Array.from({ length: n }, () => vol),
      corr: product.asset.correlation_matrix,
      vol_mode: 'flat' as const,
    };
  }, [product, spot, r, vol]);

  const loadPreset = useCallback(async (name: string) => {
    try {
      if (name === 'Custom Exotic') {
        setProduct((p) => ({ ...p, name: 'Custom Exotic' }));
        return;
      }
      const p = await fetchExoticPreset(name);
      setProduct(p);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  const runPriceOrSolve = useCallback(async () => {
    abortRef.current?.abort();
    abortExoticWorkbench();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setLoading(true);
    setError(null);
    try {
      if (mode === 'solve') {
        const res = await solveUniversalExotic(
          {
            product,
            market,
            solve_for: solveFor,
            target_price: targetPrice,
            n_paths: 6000,
          },
          ctrl.signal,
        );
        if (ctrl.signal.aborted) return;
        setSolveDiag(res);
        if (res.solution != null && Number.isFinite(Number(res.solution))) {
          const sol = Number(res.solution);
          setProduct((prev) => {
            if (solveFor === 'ki_level') {
              return { ...prev, ki: { ...prev.ki, ki_level: sol } };
            }
            if (solveFor === 'put_strike') {
              return { ...prev, ki: { ...prev.ki, put_strike: sol } };
            }
            return {
              ...prev,
              coupon: { ...prev.coupon, cpn_rate: sol, ko_rebate: sol },
            };
          });
        }
        // refresh risk at solved product
        const priced = await priceUniversalExotic(
          { product, market, include_risk_summary: true, sim: { n_paths: 10000 } },
          ctrl.signal,
        );
        if (!ctrl.signal.aborted) setRisk(priced);
      } else {
        const priced = await priceUniversalExotic(
          { product, market, include_risk_summary: true, sim: { n_paths: 15000 } },
          ctrl.signal,
        );
        if (!ctrl.signal.aborted) {
          setRisk(priced);
          setSolveDiag(null);
        }
      }
    } catch (e) {
      if ((e as Error)?.name === 'AbortError') return;
      setError(String(e));
    } finally {
      if (!ctrl.signal.aborted) setLoading(false);
    }
  }, [mode, product, market, solveFor, targetPrice]);

  // Debounced GTS
  useEffect(() => {
    const ctrl = new AbortController();
    const t = window.setTimeout(async () => {
      setGtsLoading(true);
      try {
        const res = await fetchUniversalGreeksTermStructure(
          {
            product,
            market,
            spot_range: [spot * 0.55, spot * 1.35],
            spot_steps: 25,
            n_paths: 2000,
            include_greeks: ['pv', 'delta', 'gamma'],
            spread_smoothing_width: 0.02,
          },
          ctrl.signal,
        );
        if (!ctrl.signal.aborted) setGts(res as GreeksTermStructureResponse);
      } catch (e) {
        if ((e as Error)?.name === 'AbortError') return;
      } finally {
        if (!ctrl.signal.aborted) setGtsLoading(false);
      }
    }, 320);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [product, market, spot]);

  useEffect(() => {
    loadPreset('Snowball Classic').then(() => {
      /* initial */
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const priceDisplay = risk?.price != null ? Number(risk.price) : null;

  return (
    <div className="bg-white rounded-lg border border-slate-200 shadow-sm overflow-hidden">
      <div className="bg-slate-50 px-4 py-3 border-b border-slate-200 flex items-center justify-between">
        <div>
          <h3 className="font-bold text-[#000066] text-lg">Universal Exotic Workbench</h3>
          <p className="text-[10px] text-slate-400">
            Lego legs · Solve-For · Before/After KI term structure · Desk risk pack
          </p>
        </div>
        <button
          type="button"
          onClick={runPriceOrSolve}
          disabled={loading}
          className="flex items-center gap-2 bg-[#000066] hover:bg-[#000088] text-white px-4 py-2 rounded text-sm font-bold disabled:opacity-60"
        >
          {loading ? <RefreshCw size={14} className="animate-spin" /> : <Play size={14} />}
          {mode === 'solve' ? 'Solve' : 'Price'}
        </button>
      </div>

      <div className="grid lg:grid-cols-12 gap-4 p-4">
        <div className="lg:col-span-3 max-h-[78vh] overflow-y-auto pr-1">
          <UniversalConfigPanel
            product={product}
            onChange={setProduct}
            onLoadPreset={loadPreset}
            mode={mode}
            onModeChange={setMode}
            solveFor={solveFor}
            onSolveForChange={setSolveFor}
            targetPrice={targetPrice}
            onTargetPriceChange={setTargetPrice}
          />
        </div>

        <div className="lg:col-span-6 space-y-3">
          <RiskDashboard
            risk={risk}
            solveDiag={solveDiag}
            childrenGreeks={
              <GreeksTermStructureView data={gts} loading={gtsLoading} spot={spot} />
            }
          />
        </div>

        <div className="lg:col-span-3 space-y-3">
          <div className="text-center p-4 bg-slate-50 rounded border border-slate-200">
            <div className="text-[10px] uppercase text-slate-400 font-bold">Note PV</div>
            <div className="text-3xl font-mono font-bold text-[#000066]">
              {priceDisplay != null ? `${(priceDisplay * 100).toFixed(2)}%` : '—'}
            </div>
            {risk?.elapsed_ms != null && (
              <div className="text-[9px] text-slate-400 mt-1">{Number(risk.elapsed_ms).toFixed(0)} ms</div>
            )}
          </div>
          {solveDiag?.solution != null && (
            <div className="p-3 border border-green-100 bg-green-50 rounded text-[11px]">
              <div className="text-green-800 font-bold uppercase text-[9px]">Solved {String(solveDiag.solve_for)}</div>
              <div className="font-mono text-lg font-bold text-green-900">
                {Number(solveDiag.solution).toFixed(6)}
              </div>
              <div className="text-slate-500">
                residual {Number(solveDiag.residual ?? 0).toExponential(2)}
              </div>
            </div>
          )}
          {error && (
            <div className="p-2 bg-red-50 border border-red-100 text-red-700 text-[10px] rounded">
              {error}
            </div>
          )}
          <div className="text-[10px] text-slate-500 space-y-1 border-t pt-2">
            <div>
              Catalog: <span className="font-mono">{String(risk?.meta?.catalog_type ?? '—')}</span>
            </div>
            <div>
              KI {product.ki.ki_monitoring_type} · KO {product.ko.ko_monitoring_type}
            </div>
            <div>
              Basket {product.asset.basket_type} × {product.asset.underlying.join('/')}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ExoticWorkbench;
