import React, { useEffect, useMemo, useState } from 'react';
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import { Download, Layers, RefreshCw } from 'lucide-react';
import {
  fetchQisBacktest,
  fetchQisStrategies,
  qisHoldingsExcelUrl,
  QisBacktestResponse,
  QisSideMode,
  QisSignalMode,
  QisSpreadMode,
  QisStrategyInfo,
  QisTenorPair,
  QisTradeMarker,
  isRegime2SignalMode,
  isRegimeSignalMode,
} from '../../services/api_v1';
import ChartZoomFrame from './ChartZoomFrame';

const CAL_VOL_TENORS: { key: QisTenorPair; label: string }[] = [
  { key: '3M-6M', label: '3M–6M' },
  { key: '1M-3M', label: '1M–3M' },
];

const CAL_VOL_SIDES: { key: QisSideMode; label: string }[] = [
  { key: 'both', label: 'Long+Short' },
  { key: 'long_only', label: 'Only Long+Close' },
  { key: 'short_only', label: 'Only Short+Close' },
];

const CAL_VOL_SPREADS: { key: QisSpreadMode; label: string }[] = [
  { key: 'iv', label: 'IV spread' },
  { key: 'iv2', label: 'IV² spread' },
];

/** Top-level signal family (Regime expands into book sub-toggle). */
const CAL_VOL_SIGNAL_FAMILIES: {
  key: 'quantile' | 'garch_z' | 'regime' | 'regime2';
  label: string;
}[] = [
  { key: 'quantile', label: 'Quantile' },
  { key: 'garch_z', label: 'GARCH-z' },
  { key: 'regime', label: 'Regime' },
  { key: 'regime2', label: 'Regime2' },
];

const CAL_VOL_REGIME_BOOKS: { key: QisSignalMode; label: string; hint: string }[] = [
  { key: 'regime', label: 'Both', hint: 'Momentum + Mean-reversion' },
  { key: 'regime_momentum', label: 'Momentum', hint: 'Momentum only' },
  { key: 'regime_fade', label: 'Mean-rev', hint: 'Fade / mean-reversion only' },
];

function segBtnClass(active: boolean, disabled?: boolean) {
  const base = 'px-3 py-1.5 text-xs font-bold transition-colors';
  if (disabled) {
    return `${base} ${active ? 'bg-[#000066]/80 text-white' : 'bg-slate-100 text-slate-400'} cursor-wait`;
  }
  return `${base} ${
    active ? 'bg-[#000066] text-white' : 'bg-slate-50 text-slate-600 hover:bg-slate-100'
  }`;
}

const WINDOW_LABELS: { key: keyof QisBacktestResponse['metrics']; label: string }[] = [
  { key: '1y', label: '1 Year' },
  { key: '3y', label: '3 Year' },
  { key: '5y', label: '5 Year' },
  { key: 'max', label: 'Since inception' },
];

const DECOMP_COLORS = {
  vega: '#7C3AED',
  theta: '#0F766E',
  gamma: '#C2410C',
  basis: '#A16207',
  net_delta: '#64748B',
  residual: '#94A3B8',
};

/** Chart palette: NAV/spread yellow; Long red / Short green / Close blue; bands avoid those. */
const CHART = {
  nav: '#EAB308',
  spread: '#EAB308',
  long: '#DC2626',
  short: '#16A34A',
  close: '#2563EB',
  enterBand: '#7C3AED',
  exitBand: '#64748B',
  /** Regime decision-band colors (match strategy thresholds, not fixed 10/90). */
  fadeExt: '#7C3AED',
  accel: '#0EA5E9',
  exhaust: '#64748B',
  meanRev: '#A16207',
  shockExt: '#7C3AED',
  shockCalm: '#0EA5E9',
};

function scaleOpt(v: number | null | undefined, mul: number): number | null {
  if (v == null || Number.isNaN(v)) return null;
  return v * mul;
}

const CAL_VOL_UNIVERSES = [
  { key: '50', id: 'equity_3_6_calendar_vol_50', label: '50' },
  { key: '300', id: 'equity_3_6_calendar_vol_300', label: '300' },
  { key: '1000', id: 'equity_3_6_calendar_vol_1000', label: '1000' },
] as const;

type FamilyKey = 'cross_calendar_carry' | 'calendar_vol';

type MarkerKind = 'long' | 'short' | 'close';

function fmtPct(x: number | null | undefined, digits = 2) {
  if (x == null || Number.isNaN(x)) return '—';
  return `${(x * 100).toFixed(digits)}%`;
}

function fmtNum(x: number | null | undefined, digits = 2) {
  if (x == null || Number.isNaN(x)) return '—';
  return x.toFixed(digits);
}

function fmtMoney(x: number | null | undefined) {
  if (x == null || Number.isNaN(x)) return '—';
  return x.toLocaleString('en-CN', { maximumFractionDigits: 0 });
}

/** Derive Long/Short/Close markers from executed position transitions. */
function markersFromPosition(
  dates: string[],
  position: number[],
  yAt: (i: number) => number | null,
): { d: string; y: number; kind: MarkerKind }[] {
  const out: { d: string; y: number; kind: MarkerKind }[] = [];
  for (let i = 1; i < dates.length; i++) {
    const prev = position[i - 1] ?? 0;
    const curr = position[i] ?? 0;
    if (prev === curr) continue;
    const y = yAt(i);
    if (y == null || Number.isNaN(y)) continue;
    const kind: MarkerKind = curr === 1 ? 'long' : curr === -1 ? 'short' : 'close';
    out.push({ d: dates[i], y, kind });
  }
  return out;
}

function normalizeTradeMarkers(
  raw: QisTradeMarker[] | undefined,
  fallback: { d: string; y: number; kind: MarkerKind }[],
  levelScale = 100,
): { d: string; y: number; kind: MarkerKind }[] {
  if (!raw?.length) return fallback;
  const out: { d: string; y: number; kind: MarkerKind }[] = [];
  for (const m of raw) {
    const kind = (m.kind === 'long' || m.kind === 'short' || m.kind === 'close'
      ? m.kind
      : m.position === 1
        ? 'long'
        : m.position === -1
          ? 'short'
          : 'close') as MarkerKind;
    const y =
      m.sos != null && Number.isFinite(m.sos)
        ? m.sos
        : m.spread != null && Number.isFinite(m.spread)
          ? m.spread * levelScale
          : null;
    if (y == null) continue;
    out.push({ d: m.d, y, kind });
  }
  return out.length ? out : fallback;
}

function markerMap(markers: { d: string; y: number; kind: MarkerKind }[]) {
  const m = new Map<string, MarkerKind>();
  for (const x of markers) m.set(x.d, x.kind);
  return m;
}

const markerDot = (fill: string) => (props: { cx?: number; cy?: number }) => {
  const { cx, cy } = props;
  if (cx == null || cy == null) return null;
  return <circle cx={cx} cy={cy} r={3.5} fill={fill} stroke="#fff" strokeWidth={0.75} />;
};

const QisView: React.FC = () => {
  const [strategies, setStrategies] = useState<QisStrategyInfo[]>([]);
  const [family, setFamily] = useState<FamilyKey>('calendar_vol');
  const [universe, setUniverse] = useState<'50' | '300' | '1000'>('300');
  const [tenorPair, setTenorPair] = useState<QisTenorPair>('3M-6M');
  const [sideMode, setSideMode] = useState<QisSideMode>('both');
  const [spreadMode, setSpreadMode] = useState<QisSpreadMode>('iv');
  const [signalMode, setSignalMode] = useState<QisSignalMode>('quantile');
  const [data, setData] = useState<QisBacktestResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadStatus, setLoadStatus] = useState('Running backtest…');
  const [err, setErr] = useState<string | null>(null);

  const strategyId = useMemo(() => {
    if (family === 'cross_calendar_carry') return 'cross_calendar_carry';
    return CAL_VOL_UNIVERSES.find((u) => u.key === universe)?.id ?? 'equity_3_6_calendar_vol_300';
  }, [family, universe]);

  const load = (refresh = false, signal?: AbortSignal) => {
    setLoading(true);
    setErr(null);
    setLoadStatus(refresh ? '强制重算并覆盖缓存…' : '加载回测（优先读库）…');
    fetchQisBacktest(
      strategyId,
      refresh,
      {
        signal,
        onRetry: ({ attempt, maxAttempts, delayMs }) => {
          setLoadStatus(
            `服务冷启动/繁忙，继续等待（${attempt}/${maxAttempts}），约 ${Math.ceil(delayMs / 1000)}s 后重试…`
          );
        },
      },
      252,
      family === 'calendar_vol' ? tenorPair : '3M-6M',
      family === 'calendar_vol' ? sideMode : 'both',
      family === 'calendar_vol' ? spreadMode : 'iv',
      family === 'calendar_vol' ? signalMode : 'quantile',
    )
      .then((d) => {
        if (signal?.aborted) return;
        setData(d);
        setErr(null);
        const c = d._cache;
        if (c?.hit) {
          setLoadStatus(
            c.source === 'memory' ? '已从进程内存复用' : '已从结果库复用（未重跑引擎）',
          );
        } else if (c?.persisted) {
          setLoadStatus('已重算并写入结果库');
        } else {
          setLoadStatus('回测完成');
        }
      })
      .catch((e) => {
        if ((e as Error)?.name === 'AbortError' || signal?.aborted) return;
        setErr('回测仍在准备中，请稍后点 Refresh 再试。');
      })
      .finally(() => {
        if (!signal?.aborted) setLoading(false);
      });
  };

  useEffect(() => {
    const ac = new AbortController();
    fetchQisStrategies({ signal: ac.signal })
      .then((d) => {
        if (!ac.signal.aborted) setStrategies(d.strategies ?? []);
      })
      .catch(() => {
        if (!ac.signal.aborted) {
          setStrategies([
            { id: 'cross_calendar_carry', name: 'Cross Calendar Carry', family: 'futures_carry' },
            ...CAL_VOL_UNIVERSES.map((u) => ({
              id: u.id,
              name: `Equity 3-6 Calendar Vol — ${u.label}`,
              family: 'calendar_vol',
              universe: u.key,
            })),
          ]);
        }
      });
    return () => ac.abort();
  }, []);

  useEffect(() => {
    const ac = new AbortController();
    load(false, ac.signal);
    return () => ac.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategyId, tenorPair, sideMode, spreadMode, signalMode]);

  const levelScale = useMemo(() => {
    const fromSignal = data?.signal?.level_scale;
    if (typeof fromSignal === 'number' && Number.isFinite(fromSignal)) return fromSignal;
    const fromParams = data?.params?.level_scale;
    if (typeof fromParams === 'number' && Number.isFinite(fromParams)) return fromParams;
    const mode =
      (typeof data?.params?.signal_mode === 'string'
        ? data.params.signal_mode
        : signalMode) as QisSignalMode;
    return mode === 'garch_z' ? 1 : 100;
  }, [data, signalMode]);

  const decompChart = useMemo(() => {
    const d = data?.pnl_decomposition;
    if (!d?.dates?.length) return [];
    return d.dates.map((dt, i) => ({
      d: dt,
      vega: d.vega_pnl[i],
      theta: d.theta_pnl[i],
      gamma: d.gamma_pnl[i],
      basis: d.basis_pnl[i],
      net_delta: d.net_delta_pnl[i],
      residual: d.residual_pnl[i],
    }));
  }, [data]);

  const dailyGreeksChart = useMemo(() => {
    const d = data?.pnl_daily_greeks;
    if (!d?.dates?.length) return [];
    return d.dates.map((dt, i) => ({
      d: dt,
      vega: d.vega_pnl[i],
      theta: d.theta_pnl[i],
      gamma: d.gamma_pnl[i],
    }));
  }, [data]);

  const tradeMarkers = useMemo(() => {
    const s = data?.signal;
    if (!s?.dates?.length || !s.position?.length) return [] as { d: string; y: number; kind: MarkerKind }[];
    const fallback = markersFromPosition(s.dates, s.position, (i) => {
      const sp = s.spread[i];
      return sp == null || Number.isNaN(sp) ? null : sp * levelScale;
    });
    return normalizeTradeMarkers(s.trade_markers, fallback, levelScale);
  }, [data, levelScale]);

  /** Markers placed on CT-IV spread scale (vol pts) for the IV reference chart. */
  const tradeMarkersIv = useMemo(() => {
    const s = data?.signal;
    if (!s?.dates?.length || !s.position?.length) return [] as { d: string; y: number; kind: MarkerKind }[];
    const fallback = markersFromPosition(s.dates, s.position, (i) => {
      const sp = s.spread_iv?.[i] ?? (levelScale === 100 ? s.spread[i] : null);
      return sp == null || Number.isNaN(sp) ? null : sp * 100;
    });
    const raw = s.trade_markers;
    if (!raw?.length) return fallback;
    const out: { d: string; y: number; kind: MarkerKind }[] = [];
    for (const m of raw) {
      const kind = (m.kind === 'long' || m.kind === 'short' || m.kind === 'close'
        ? m.kind
        : m.position === 1
          ? 'long'
          : m.position === -1
            ? 'short'
            : 'close') as MarkerKind;
      const y =
        m.sos_iv != null && Number.isFinite(m.sos_iv)
          ? m.sos_iv
          : m.spread_iv != null && Number.isFinite(m.spread_iv)
            ? m.spread_iv * 100
            : null;
      if (y == null) continue;
      out.push({ d: m.d, y, kind });
    }
    return out.length ? out : fallback;
  }, [data, levelScale]);

  const byKind = useMemo(() => markerMap(tradeMarkers), [tradeMarkers]);
  const byKindIv = useMemo(() => markerMap(tradeMarkersIv), [tradeMarkersIv]);

  /** Markers on garch_z scale (regime / garch_z charts). */
  const tradeMarkersGz = useMemo(() => {
    const s = data?.signal;
    if (!s?.dates?.length || !s.position?.length) return [] as { d: string; y: number; kind: MarkerKind }[];
    const gz = s.garch_z;
    const fallback = markersFromPosition(s.dates, s.position, (i) => {
      if (gz?.[i] != null && Number.isFinite(gz[i] as number)) return gz[i] as number;
      // garch_z mode: decision level IS garch_z
      if (levelScale === 1) {
        const sp = s.spread[i];
        return sp == null || Number.isNaN(sp) ? null : sp;
      }
      return null;
    });
    const raw = s.trade_markers;
    if (!raw?.length) return fallback;
    const out: { d: string; y: number; kind: MarkerKind }[] = [];
    for (const m of raw) {
      const kind = (m.kind === 'long' || m.kind === 'short' || m.kind === 'close'
        ? m.kind
        : m.position === 1
          ? 'long'
          : m.position === -1
            ? 'short'
            : 'close') as MarkerKind;
      const y =
        m.garch_z != null && Number.isFinite(m.garch_z)
          ? m.garch_z
          : null;
      if (y == null) continue;
      out.push({ d: m.d, y, kind });
    }
    return out.length ? out : fallback;
  }, [data, levelScale]);

  const byKindGz = useMemo(() => markerMap(tradeMarkersGz), [tradeMarkersGz]);

  const signalChart = useMemo(() => {
    const s = data?.signal;
    if (!s?.dates?.length) return [];
    const hasBands = Array.isArray(s.enter_hi) && s.enter_hi.length === s.dates.length;
    const kindByD = byKind;
    return s.dates.map((dt, i) => {
      const spreadPts = (s.spread[i] ?? 0) * levelScale;
      const kind = kindByD.get(dt);
      return {
        d: dt,
        spread_pts: spreadPts,
        enter_hi: hasBands && s.enter_hi![i] != null ? (s.enter_hi![i] as number) * levelScale : null,
        enter_lo: hasBands && s.enter_lo![i] != null ? (s.enter_lo![i] as number) * levelScale : null,
        exit_hi: hasBands && s.exit_hi![i] != null ? (s.exit_hi![i] as number) * levelScale : null,
        exit_lo: hasBands && s.exit_lo![i] != null ? (s.exit_lo![i] as number) * levelScale : null,
        position: s.position[i],
        long_pt: kind === 'long' ? spreadPts : null,
        short_pt: kind === 'short' ? spreadPts : null,
        close_pt: kind === 'close' ? spreadPts : null,
      };
    });
  }, [data, byKind, levelScale]);

  /** GARCH-z chart: garch_z uses enter_*; regime uses shk_q*; regime2 uses gz_enter_* stress gate. */
  const garchZChart = useMemo(() => {
    const s = data?.signal;
    if (!s?.dates?.length) return [];
    const mode =
      (typeof s.signal_mode === 'string' ? s.signal_mode : null) ??
      (typeof data?.params?.signal_mode === 'string' ? data.params.signal_mode : null);
    const kindByD = byKindGz;
    // garch_z mode: decision series is s.spread with enter_* bands
    if (mode === 'garch_z') {
      const hasBands = Array.isArray(s.enter_hi) && s.enter_hi.length === s.dates.length;
      return s.dates.map((dt, i) => {
        const z = s.spread[i] ?? 0;
        const kind = kindByD.get(dt);
        return {
          d: dt,
          spread_pts: z,
          enter_hi: hasBands && s.enter_hi![i] != null ? (s.enter_hi![i] as number) : null,
          enter_lo: hasBands && s.enter_lo![i] != null ? (s.enter_lo![i] as number) : null,
          exit_hi: hasBands && s.exit_hi![i] != null ? (s.exit_hi![i] as number) : null,
          exit_lo: hasBands && s.exit_lo![i] != null ? (s.exit_lo![i] as number) : null,
          long_pt: kind === 'long' ? z : null,
          short_pt: kind === 'short' ? z : null,
          close_pt: kind === 'close' ? z : null,
        };
      });
    }
    if (!s.garch_z?.length) return [];
    // regime2: stress gate = gz rolling 10/90 (gz_enter_*)
    if (mode === 'regime2') {
      const hasGz =
        Array.isArray(s.gz_enter_hi) && s.gz_enter_hi.length === s.dates.length;
      return s.dates.map((dt, i) => {
        const zRaw = s.garch_z![i];
        const z = zRaw == null || Number.isNaN(zRaw) ? 0 : (zRaw as number);
        const kind = kindByD.get(dt);
        return {
          d: dt,
          spread_pts: z,
          enter_hi: hasGz && s.gz_enter_hi![i] != null ? (s.gz_enter_hi![i] as number) : null,
          enter_lo: hasGz && s.gz_enter_lo![i] != null ? (s.gz_enter_lo![i] as number) : null,
          exit_hi: hasGz && s.gz_exit_hi?.[i] != null ? (s.gz_exit_hi![i] as number) : null,
          exit_lo: hasGz && s.gz_exit_lo?.[i] != null ? (s.gz_exit_lo![i] as number) : null,
          long_pt: kind === 'long' ? z : null,
          short_pt: kind === 'short' ? z : null,
          close_pt: kind === 'close' ? z : null,
        };
      });
    }
    // regime: garch_z + decision shock quantiles (same as FSM thresholds)
    const useShk =
      Array.isArray(s.shk_q10) && s.shk_q10.length === s.dates.length;
    return s.dates.map((dt, i) => {
      const zRaw = s.garch_z![i];
      const z = zRaw == null || Number.isNaN(zRaw) ? 0 : (zRaw as number);
      const kind = kindByD.get(dt);
      return {
        d: dt,
        spread_pts: z,
        q10: useShk ? scaleOpt(s.shk_q10?.[i], 1) : null,
        q20: useShk ? scaleOpt(s.shk_q20?.[i], 1) : null,
        q80: useShk ? scaleOpt(s.shk_q80?.[i], 1) : null,
        q90: useShk ? scaleOpt(s.shk_q90?.[i], 1) : null,
        long_pt: kind === 'long' ? z : null,
        short_pt: kind === 'short' ? z : null,
        close_pt: kind === 'close' ? z : null,
      };
    });
  }, [data, byKindGz]);

  /**
   * CT-IV spread chart — only decision-relevant overlays for the active signal_mode:
   * - regime: lvl_q* used by Fade/Mom FSM
   * - garch_z: raw CT-IV spread only (decision bands live on the GARCH-z chart)
   * - quantile: handled via signalChart (enter/exit on Spread)
   */
  const ivSpreadChart = useMemo(() => {
    const s = data?.signal;
    if (!s?.dates?.length) return [];
    const ivSeries = s.spread_iv ?? (levelScale === 100 ? s.spread : null);
    if (!ivSeries?.length) return [];
    const mode =
      (typeof s.signal_mode === 'string' ? s.signal_mode : null) ??
      (typeof data?.params?.signal_mode === 'string' ? data.params.signal_mode : null);
    const kindByD = byKindIv;

    if (isRegimeSignalMode(mode)) {
      const useLvl = Array.isArray(s.lvl_q10) && s.lvl_q10.length === s.dates.length;
      return s.dates.map((dt, i) => {
        const raw = ivSeries[i];
        const spreadPts = raw == null || Number.isNaN(raw) ? 0 : raw * 100;
        const kind = kindByD.get(dt);
        return {
          d: dt,
          spread_pts: spreadPts,
          q10: useLvl ? scaleOpt(s.lvl_q10?.[i], 100) : null,
          q25: useLvl ? scaleOpt(s.lvl_q25?.[i], 100) : null,
          q50: useLvl ? scaleOpt(s.lvl_q50?.[i], 100) : null,
          q75: useLvl ? scaleOpt(s.lvl_q75?.[i], 100) : null,
          q90: useLvl ? scaleOpt(s.lvl_q90?.[i], 100) : null,
          long_pt: kind === 'long' ? spreadPts : null,
          short_pt: kind === 'short' ? spreadPts : null,
          close_pt: kind === 'close' ? spreadPts : null,
        };
      });
    }

    // garch_z (and any other non-regime path here): no reference IV quantile bands —
    // those are not the live decision signal.
    return s.dates.map((dt, i) => {
      const raw = ivSeries[i];
      const spreadPts = raw == null || Number.isNaN(raw) ? 0 : raw * 100;
      const kind = kindByD.get(dt);
      return {
        d: dt,
        spread_pts: spreadPts,
        long_pt: kind === 'long' ? spreadPts : null,
        short_pt: kind === 'short' ? spreadPts : null,
        close_pt: kind === 'close' ? spreadPts : null,
      };
    });
  }, [data, byKindIv, levelScale]);

  const navChart = useMemo(() => {
    if (!data?.nav?.length) return [];
    const kindByD = byKind;
    return data.nav.map((r) => {
      const kind = kindByD.get(r.d);
      const sos = r.sos;
      return {
        ...r,
        long_pt: kind === 'long' && sos != null ? sos : null,
        short_pt: kind === 'short' && sos != null ? sos : null,
        close_pt: kind === 'close' && sos != null ? sos : null,
      };
    });
  }, [data, byKind]);

  const isCalVol = strategyId.startsWith('equity_3_6_calendar_vol_');
  const activeTenor =
    (typeof data?.params?.tenor_pair === 'string' ? data.params.tenor_pair : tenorPair) as QisTenorPair;
  const activeSide =
    (typeof data?.params?.side_mode === 'string' ? data.params.side_mode : sideMode) as QisSideMode;
  const activeSpread =
    (typeof data?.params?.spread_mode === 'string' ? data.params.spread_mode : spreadMode) as QisSpreadMode;
  const activeSignal =
    (typeof data?.params?.signal_mode === 'string'
      ? data.params.signal_mode
      : typeof data?.signal?.signal_mode === 'string'
        ? data.signal.signal_mode
        : signalMode) as QisSignalMode;
  const tenorLabel = CAL_VOL_TENORS.find((t) => t.key === activeTenor)?.label ?? activeTenor;
  const sideLabel = CAL_VOL_SIDES.find((s) => s.key === activeSide)?.label ?? activeSide;
  const spreadLabel = CAL_VOL_SPREADS.find((s) => s.key === activeSpread)?.label ?? activeSpread;
  const signalLabel = isRegimeSignalMode(activeSignal)
    ? `Regime · ${CAL_VOL_REGIME_BOOKS.find((b) => b.key === activeSignal)?.label ?? activeSignal}`
    : CAL_VOL_SIGNAL_FAMILIES.find((s) => s.key === activeSignal)?.label ?? activeSignal;
  const sosLabel = isCalVol
    ? activeSignal === 'garch_z'
      ? `${tenorLabel} GARCH-z`
      : isRegimeSignalMode(activeSignal)
        ? activeSpread === 'iv2'
          ? `${tenorLabel} IV² · Regime v2`
          : `${tenorLabel} IV · Regime v2`
        : isRegime2SignalMode(activeSignal)
          ? activeSpread === 'iv2'
            ? `${tenorLabel} IV² · Regime2`
            : `${tenorLabel} IV · Regime2`
          : activeSpread === 'iv2'
            ? `${tenorLabel} IV² spread (var ×100)`
            : `${tenorLabel} IV spread (vol pts)`
    : 'SoS (pts)';
  const ivSpreadLabel = isCalVol
    ? activeSpread === 'iv2'
      ? `${tenorLabel} IV² spread (var ×100)`
      : `${tenorLabel} IV spread (vol pts)`
    : 'Spread (pts)';
  const showGarchSignalChart =
    isCalVol &&
    (activeSignal === 'garch_z' ||
      isRegimeSignalMode(activeSignal) ||
      isRegime2SignalMode(activeSignal));
  const showSpreadBandChart = isCalVol && !!data?.signal;
  const garchChartData =
    isRegimeSignalMode(activeSignal) ||
    activeSignal === 'garch_z' ||
    isRegime2SignalMode(activeSignal)
      ? garchZChart
      : signalChart;
  // Regime2 decisions use Spread 10/90·40/60 like quantile → signalChart.
  const spreadChartData =
    activeSignal === 'garch_z' || isRegimeSignalMode(activeSignal)
      ? ivSpreadChart
      : signalChart;
  const regimeShowMom = activeSignal === 'regime' || activeSignal === 'regime_momentum';
  const regimeShowFade = activeSignal === 'regime' || activeSignal === 'regime_fade';
  const regimeLevelYKeys = [
    'spread_pts',
    'q10',
    'q90',
    ...(regimeShowMom ? (['q25', 'q75'] as const) : []),
    'q50',
  ];
  const regimeShockYKeys = [
    'spread_pts',
    'q10',
    'q90',
    ...(regimeShowFade ? (['q20', 'q80'] as const) : []),
  ];

  const enterPct = data?.signal?.enter_q != null ? `${((1 - data.signal.enter_q) * 100).toFixed(0)}/${(data.signal.enter_q * 100).toFixed(0)}` : '10/90';
  const exitPct =
    data?.signal?.exit_lo_q != null && data?.signal?.exit_hi_q != null
      ? `${(data.signal.exit_lo_q * 100).toFixed(0)}–${(data.signal.exit_hi_q * 100).toFixed(0)}`
      : '40–60';

  const calVolDocs = useMemo(() => {
    if (!isCalVol) return null;
    const near = activeTenor === '1M-3M' ? '1M' : '3M';
    const far = activeTenor === '1M-3M' ? '3M' : '6M';
    const tNear = activeTenor === '1M-3M' ? '30/365' : '90/365';
    const tFar = activeTenor === '1M-3M' ? '90/365' : '180/365';
    const zWin =
      typeof data?.params?.z_window === 'number'
        ? data.params.z_window
        : 252;
    const spreadFormula =
      activeSpread === 'iv2'
        ? `Spread = IV_${near}² − IV_${far}²`
        : `Spread = IV_${near} − IV_${far}`;
    const spreadModeNote =
      activeSpread === 'iv2'
        ? '先 CT-IV 插值得到近/远端 IV，再平方后相减（IV² spread / variance diff）。'
        : '先 CT-IV 插值得到近/远端 IV，再做水平差（IV spread）。';
    const signalModeNote =
      activeSignal === 'garch_z'
        ? '对 ΔSpread 做 expanding GARCH(1,1)-t（预热 252 日、每 5 日重估参数，仅用截至当日历史）得到 garch_z；再对 garch_z 做滚动分位入场/出场（窗长默认 252，满半窗≈126 日即可出带；无未来数据；t−1 信号 → t close 成交）。'
        : isRegimeSignalMode(activeSignal)
          ? null
          : isRegime2SignalMode(activeSignal)
            ? '基线与 Quantile 完全相同（Spread 滚动 10/90 进、40/60 出；无 crisis stop）。压力态：garch_z 连续 N 日越出其滚动 10/90 → 仅停新开，已有仓位仍按 Quantile 规则平仓。'
            : '直接对 Spread 做滚动分位入场/出场（无未来数据；t−1 信号 → t close 成交）。';

    /** Structured Regime v2 logic for the docs card (applies to every tenor/side/spread/universe). */
    const regimeBookLabel =
      activeSignal === 'regime_momentum'
        ? 'Momentum only'
        : activeSignal === 'regime_fade'
          ? 'Mean-reversion only'
          : 'Both (Mom + Fade)';
    const regimeLogic =
      isRegimeSignalMode(activeSignal)
        ? {
            title: `信号层 · Regime v2 · ${regimeBookLabel}`,
            intro:
              `在当前 CT-IV ${activeSpread === 'iv2' ? 'IV²' : 'IV'} Spread（${tenorLabel}）上，` +
              (activeSignal === 'regime_momentum'
                ? '仅启用 Momentum 开仓（Fade 入场关闭）。'
                : activeSignal === 'regime_fade'
                  ? '仅启用 Fade / mean-reversion 开仓（Momentum 入场关闭）。'
                  : '用水平分位 + garch_z 冲击分位切换 Fade / Momentum；空仓时 Momentum 优先于 Fade。') +
              '分位均为过去 ' +
              String(
                typeof data?.params?.z_window === 'number' ? data.params.z_window : 252,
              ) +
              ' 日、shift(1)（无未来函数）。执行：t 收盘出信号 → t+1 收盘成交。' +
              'Book 选择与 side_mode / tenor / spread / universe 正交组合。',
            bullets: [
              '状态变量：S = Spread；z = expanding GARCH(1,1)-t 标准化残差（garch_z）；z_{t−1} = 前日 z。',
              ...(activeSignal !== 'regime_fade'
                ? [
                    'Momentum 开仓（优先）：上侧 q75 < S ≤ q90 且 z > q90 → Reverse(−1)；下侧 q10 ≤ S < q25 且 z < q10 → Normal(+1)。',
                    'Momentum 平仓（任一触发）：持仓≥2 日后 S 穿过 q50 均值；冲击反向穿过极端 q10/q90；空间止盈 S 出 q01/q99；最长持有 12 日 → 冷却 3 日。',
                  ]
                : []),
              ...(activeSignal !== 'regime_momentum'
                ? [
                    'Fade 开仓：S > q90 且 z≤q80 且 z_{t−1}≤q80 → Normal(+1)；S < q10 且 z≥q20 且 z_{t−1}≥q20 → Reverse(−1)（双日冲击收敛，避免硬抗突破）。',
                    'Fade 平仓：S 穿过 q50；危机熔断（持仓方向继续恶化且冲击极端）；Reverse 最长 20 日 → 冷却 3 日。',
                  ]
                : []),
              '预算：Fade 远端美元 Vega = 1% NAV；Momentum = 0.8% NAV（按成交日 regime_mode 选预算）。',
            ],
          }
        : null;

    let sideBlock: { title: string; body: string };
    if (activeSide === 'long_only') {
      sideBlock = {
        title: 'side_mode = long_only（Only Long+Close）',
        body:
          `只做 Normal(+1) = 空近端 / 多远端（${tenorLabel}：空${near}多${far}），整体偏 short gamma / short front vol；` +
          '忽略所有 Reverse(−1) 开仓，平仓（exit 带 → 0）始终允许。Reverse max_hold 在此模式下不触发。',
      };
    } else if (activeSide === 'short_only') {
      sideBlock = {
        title: 'side_mode = short_only（Only Short+Close）',
        body:
          `只做 Reverse(−1) = 多近端 / 空远端（${tenorLabel}：多${near}空${far}），腿向与 Normal 相反；` +
          '忽略 +1；平仓始终允许；Reverse 最长 20 交易日强制平。',
      };
    } else {
      sideBlock = {
        title: 'side_mode = both（Long+Short）',
        body:
          `两侧都做：Normal(+1)=空${near}/多${far}；Reverse(−1)=多${near}/空${far}。` +
          'Reverse 最长 20 交易日；不是事后把 both 的 NAV 切片，而是信号层过滤后整段回测。',
      };
    }

    const uniMap: Record<string, string> = {
      '50': '上证50 · HO / IH / 000016 · 乘数 300',
      '300': '沪深300 · IO / IF / 000300 · 乘数 300',
      '1000': '中证1000 · MO / IM / 000852 · 乘数 200',
    };

    const budgetNote =
      isRegimeSignalMode(activeSignal)
        ? '远端美元 Vega 预算：Fade = 1% NAV / vol-pt，Momentum = 0.8% NAV / vol-pt（按成交日 regime_mode 切换）；'
        : '远端美元 Vega 预算 = 1% NAV / vol-pt；';

    return {
      near,
      far,
      tNear,
      tFar,
      zWin,
      spreadFormula,
      spreadModeNote,
      signalModeNote,
      regimeLogic,
      budgetNote,
      sideBlock,
      universeLine: uniMap[universe] ?? universe,
    };
  }, [isCalVol, activeTenor, activeSide, activeSpread, activeSignal, tenorLabel, universe, data?.params?.z_window]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold text-[#000066] flex items-center gap-2">
            <Layers size={18} className="text-[#EAB308]" />
            Systematic Trading Strategies
          </h2>
          <p className="text-[11px] text-slate-500 max-w-3xl mt-1">
            QIS：期货 carry + Equity Calendar Vol。选指数 / tenor / side / spread / signal 后，下方策略卡会同步中文口径说明（数据、信号、腿向、PnL 希腊）。
            缓存 <code className="font-mono">qis_results.sqlite</code>（tenor × side × spread × signal 分键）。
          </p>
        </div>
        <div className="flex flex-col items-stretch sm:items-end gap-2 min-w-0">
          <div className="flex flex-wrap items-center justify-end gap-2">
            <select
              className="px-3 py-1.5 text-xs font-bold border border-slate-200 rounded-md bg-slate-50 text-[#000066] disabled:opacity-60"
              value={family}
              disabled={loading}
              onChange={(e) => setFamily(e.target.value as FamilyKey)}
            >
              <option value="cross_calendar_carry">Cross Calendar Carry</option>
              <option value="calendar_vol">Equity Calendar Vol</option>
            </select>
            <button
              type="button"
              disabled={loading}
              onClick={() => load(true)}
              className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-bold rounded-md bg-[#000066] text-white disabled:opacity-60"
              title="强制重跑引擎并覆盖结果库"
            >
              <RefreshCw size={12} className={loading ? 'animate-spin' : undefined} /> Refresh
            </button>
            {isCalVol && (
              <a
                href={qisHoldingsExcelUrl(strategyId, 252, tenorPair, sideMode, spreadMode, signalMode)}
                className={`inline-flex items-center gap-1 px-3 py-1.5 text-xs font-bold rounded-md border border-slate-200 bg-white text-[#000066] hover:bg-slate-50 ${
                  loading ? 'pointer-events-none opacity-60' : ''
                }`}
                title="Download concrete holdings Excel (t−1 signal / t close)"
              >
                <Download size={12} /> Holdings.xlsx
              </a>
            )}
          </div>
          {family === 'calendar_vol' && (
            <div className="flex flex-col gap-1.5 w-full max-w-3xl">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 justify-end">
                <div className="flex items-center gap-1.5">
                  <span className="text-[9px] font-bold uppercase tracking-wide text-slate-400">Index</span>
                  <div className="flex rounded-md border border-slate-200 overflow-hidden">
                    {CAL_VOL_UNIVERSES.map((u) => (
                      <button
                        key={u.key}
                        type="button"
                        disabled={loading}
                        onClick={() => setUniverse(u.key)}
                        className={segBtnClass(universe === u.key, loading)}
                      >
                        {u.label}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="text-[9px] font-bold uppercase tracking-wide text-slate-400">Tenor</span>
                  <div className="flex rounded-md border border-slate-200 overflow-hidden">
                    {CAL_VOL_TENORS.map((t) => (
                      <button
                        key={t.key}
                        type="button"
                        disabled={loading}
                        onClick={() => setTenorPair(t.key)}
                        className={segBtnClass(tenorPair === t.key, loading)}
                      >
                        {t.label}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="text-[9px] font-bold uppercase tracking-wide text-slate-400">Side</span>
                  <div className="flex rounded-md border border-slate-200 overflow-hidden">
                    {CAL_VOL_SIDES.map((s) => (
                      <button
                        key={s.key}
                        type="button"
                        disabled={loading}
                        onClick={() => setSideMode(s.key)}
                        className={segBtnClass(sideMode === s.key, loading)}
                      >
                        {s.label}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="text-[9px] font-bold uppercase tracking-wide text-slate-400">Spread</span>
                  <div className="flex rounded-md border border-slate-200 overflow-hidden">
                    {CAL_VOL_SPREADS.map((s) => (
                      <button
                        key={s.key}
                        type="button"
                        disabled={loading}
                        onClick={() => setSpreadMode(s.key)}
                        className={segBtnClass(spreadMode === s.key, loading)}
                      >
                        {s.label}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 justify-end">
                <div className="flex items-center gap-1.5">
                  <span className="text-[9px] font-bold uppercase tracking-wide text-slate-400">Signal</span>
                  <div className="flex rounded-md border border-slate-200 overflow-hidden">
                    {CAL_VOL_SIGNAL_FAMILIES.map((s) => {
                      const active =
                        s.key === 'regime' ? isRegimeSignalMode(signalMode) : signalMode === s.key;
                      return (
                        <button
                          key={s.key}
                          type="button"
                          disabled={loading}
                          onClick={() => {
                            if (s.key === 'regime') {
                              setSignalMode(isRegimeSignalMode(signalMode) ? signalMode : 'regime');
                            } else {
                              setSignalMode(s.key);
                            }
                          }}
                          className={segBtnClass(active, loading)}
                        >
                          {s.label}
                        </button>
                      );
                    })}
                  </div>
                </div>
                {isRegimeSignalMode(signalMode) && (
                  <div className="flex items-center gap-1.5">
                    <span className="text-[9px] font-bold uppercase tracking-wide text-slate-400">Book</span>
                    <div className="flex rounded-md border border-[#000066]/30 overflow-hidden">
                      {CAL_VOL_REGIME_BOOKS.map((b) => (
                        <button
                          key={b.key}
                          type="button"
                          disabled={loading}
                          title={b.hint}
                          onClick={() => setSignalMode(b.key)}
                          className={segBtnClass(signalMode === b.key, loading)}
                        >
                          {b.label}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {loading && (
        <div className="text-xs text-slate-500 flex items-center gap-2">
          <span className="inline-block h-2 w-2 rounded-full bg-[#EAB308] animate-pulse" />
          {loadStatus}
        </div>
      )}
      {!loading && data?._cache && (
        <div className="text-[11px] text-slate-600 bg-slate-50 border border-slate-200 rounded px-3 py-2">
          结果落库复用：同参数首次成功回测写入 <code className="font-mono text-[10px]">qis_results.sqlite</code>
          ，之后直接读库不重跑引擎；仅点 Refresh / <code className="font-mono text-[10px]">refresh=true</code>{' '}
          强制重算覆盖。
          <span className="font-mono text-[10px] text-slate-500 ml-1">
            cache {data._cache.hit ? 'HIT' : 'MISS'}/{data._cache.source ?? '?'}
            {data._cache.persisted ? ' · persisted' : ''}
          </span>
        </div>
      )}
      {err && !loading && (
        <div className="text-xs text-amber-800 bg-amber-50 border border-amber-100 rounded p-2">{err}</div>
      )}

      {data && (
        <>
          <div className="bg-white rounded-lg border border-slate-200 p-4 space-y-3">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h3 className="font-bold text-[#000066] text-sm">{data.strategy_name}</h3>
              <div className="text-[10px] font-mono text-slate-500">
                {data.summary.start} → {data.summary.end} · final ×{data.summary.final_nav_multiple.toFixed(3)} ·
                NAV {data.summary.final_nav.toLocaleString('en-CN', { maximumFractionDigits: 0 })}
                {data.summary.first_open ? ` · first open ${data.summary.first_open}` : ''}
                {typeof data.params.z_window === 'number' ? ` · window=${data.params.z_window}d` : ''}
                {isCalVol
                  ? ` · tenor=${tenorLabel} · side=${sideLabel} · spread=${spreadLabel} · signal=${signalLabel}`
                  : ''}
                {data._cache
                  ? ` · cache ${data._cache.hit ? 'HIT' : 'MISS'}/${data._cache.source ?? '?'}`
                  : ''}
              </div>
            </div>
            {calVolDocs ? (
              <div className="text-[11px] text-slate-700 leading-relaxed space-y-2.5 border-t border-slate-100 pt-3">
                <div>
                  <div className="text-[10px] font-bold uppercase tracking-wide text-[#000066] mb-0.5">
                    回测数据
                  </div>
                  <p>
                    Wind close 库：期权 <code className="font-mono text-[10px]">mid_price←close</code>
                    （S_DQ_CLOSE，无 settle fallback）；期货只用 close。
                    执行时序：t−1 close 算信号 → t close 成交；持仓按次日 mid 差价 + 期货对冲 mark-to-market。
                    标的：{calVolDocs.universeLine}。成本 v1 不计；初始 NAV ¥
                    {(data.params.initial_nav / 1e8).toFixed(0)}亿。
                  </p>
                </div>
                <div>
                  <div className="text-[10px] font-bold uppercase tracking-wide text-[#000066] mb-0.5">
                    价差构造
                  </div>
                  <p>
                    CT-IV：SVI otm，总方差对 T 插值（ACT/365），目标 T={calVolDocs.tNear}（{calVolDocs.near}）/
                    {calVolDocs.tFar}（{calVolDocs.far}）。
                    {calVolDocs.spreadModeNote}
                    公式 <code className="font-mono text-[10px]">{calVolDocs.spreadFormula}</code>。
                    滚动分位窗 {calVolDocs.zWin} 日（不含当日/未来）。
                  </p>
                </div>
                {calVolDocs.regimeLogic ? (
                  <div>
                    <div className="text-[10px] font-bold uppercase tracking-wide text-[#000066] mb-0.5">
                      {calVolDocs.regimeLogic.title}
                    </div>
                    <p className="mb-1.5">{calVolDocs.regimeLogic.intro}</p>
                    <ul className="list-disc pl-4 space-y-1 text-slate-700">
                      {calVolDocs.regimeLogic.bullets.map((b, i) => (
                        <li key={`regime-logic-${i}`}>{b}</li>
                      ))}
                    </ul>
                  </div>
                ) : (
                  <div>
                    <div className="text-[10px] font-bold uppercase tracking-wide text-[#000066] mb-0.5">
                      信号算子
                    </div>
                    <p>
                      {calVolDocs.signalModeNote}
                      入场 {enterPct}（level &gt; hi → Normal/+1；&lt; lo → Reverse/−1）；
                      出场落入 {exitPct}% 带平仓。Reverse max hold = 20 交易日。
                    </p>
                  </div>
                )}
                <div>
                  <div className="text-[10px] font-bold uppercase tracking-wide text-[#000066] mb-0.5">
                    {calVolDocs.sideBlock.title}
                  </div>
                  <p>{calVolDocs.sideBlock.body}</p>
                </div>
                <div>
                  <div className="text-[10px] font-bold uppercase tracking-wide text-[#000066] mb-0.5">
                    仓位 / 参数
                  </div>
                  <p>
                    tenor_pair=<code className="font-mono text-[10px]">{activeTenor}</code> ·
                    side_mode=<code className="font-mono text-[10px]">{activeSide}</code> ·
                    spread_mode=<code className="font-mono text-[10px]">{activeSpread}</code> ·
                    signal_mode=<code className="font-mono text-[10px]">{activeSignal}</code> ·
                    z_window={calVolDocs.zWin}。
                    {calVolDocs.budgetNote}
                    N_{calVolDocs.near}=−N_{calVolDocs.far}·ν{calVolDocs.far}/ν{calVolDocs.near}（日频，净美元 Vega≈0）。
                    双腿 ATM straddle + 日频期货 delta hedge。绩效自首次开仓次日起算。
                  </p>
                </div>
                <div>
                  <div className="text-[10px] font-bold uppercase tracking-wide text-[#000066] mb-0.5">
                    PnL / 希腊分解
                  </div>
                  <p>
                    实际 PnL = 期权 mid 差价 + 期货对冲差价（数据源同上 close）。
                    另有 Black-76 希腊分解面板（见下方 Cumulative Greeks）：
                    ΔPnL≈(m_opt·ΣnΔ + m_fut·n_fut)·dS；
                    Γ≈½ m_opt·ΣnΓ·(dS)²；
                    Vega≈m_opt·Σ n·vega_pt·ΔIV·100；
                    Theta≈m_opt·ΣnΘ·(1/365)；
                    Basis≈m_fut·n_fut·Δ(F−S)；
                    Residual = actual − (Δ+Γ+Vega+Θ+Basis)。
                    实现：<code className="font-mono text-[10px]">backend/engine/calendar_vol_strategy.py</code>。
                  </p>
                </div>
              </div>
            ) : (
              <p className="text-[11px] text-slate-600 leading-relaxed">{data.description}</p>
            )}
            <div className="flex flex-wrap gap-2">
              {data.legs.map((l) => (
                <span
                  key={l.id}
                  className={`px-2 py-1 text-[10px] font-bold rounded border ${
                    l.weight > 0
                      ? 'bg-emerald-50 text-emerald-800 border-emerald-200'
                      : l.weight < 0
                        ? 'bg-rose-50 text-rose-800 border-rose-200'
                        : 'bg-slate-50 text-slate-700 border-slate-200'
                  }`}
                >
                  {l.product} {l.tenor} · {l.weight > 0 ? '+' : ''}
                  {l.weight} lot
                </span>
              ))}
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-[10px] text-slate-500">
              <div>Initial capital: ¥{(data.params.initial_nav / 1e8).toFixed(0)}亿</div>
              <div>Slippage: {data.params.slippage_bp} bp</div>
              <div>Fee (L/S): {data.params.fee_pct}%</div>
              <div>
                Rebalance: {data.params.rebalance} · {data.params.signal_lag}
              </div>
            </div>
          </div>

          <div className="bg-white rounded-lg border border-slate-200 p-4">
            <h3 className="font-bold text-[#000066] text-sm mb-3">净值曲线 (NAV / initial)</h3>
            <ChartZoomFrame data={navChart} yKeys={['nav', 'sos']} height={340}>
              {(win) => (
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={win.data}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    <XAxis dataKey="d" tick={{ fontSize: 9 }} minTickGap={40} />
                    <YAxis
                      yAxisId="nav"
                      tick={{ fontSize: 10 }}
                      domain={win.yDomain ?? ['auto', 'auto']}
                      allowDataOverflow={!!win.yDomain}
                    />
                    <YAxis yAxisId="sos" orientation="right" tick={{ fontSize: 9 }} domain={['auto', 'auto']} />
                    <Tooltip contentStyle={{ fontSize: 11 }} />
                    <Legend wrapperStyle={{ fontSize: 10 }} />
                    <Line yAxisId="nav" type="monotone" dataKey="nav" name="NAV multiple" stroke={CHART.nav} dot={false} strokeWidth={2} />
                    <Line yAxisId="sos" type="monotone" dataKey="sos" name={sosLabel} stroke={CHART.spread} dot={false} strokeWidth={1} strokeOpacity={0.55} />
                    <Line yAxisId="sos" type="monotone" dataKey="long_pt" name="Long" stroke={CHART.long} strokeWidth={0} dot={markerDot(CHART.long)} activeDot={false} legendType="circle" connectNulls={false} isAnimationActive={false} />
                    <Line yAxisId="sos" type="monotone" dataKey="short_pt" name="Short" stroke={CHART.short} strokeWidth={0} dot={markerDot(CHART.short)} activeDot={false} legendType="circle" connectNulls={false} isAnimationActive={false} />
                    <Line yAxisId="sos" type="monotone" dataKey="close_pt" name="Close" stroke={CHART.close} strokeWidth={0} dot={markerDot(CHART.close)} activeDot={false} legendType="circle" connectNulls={false} isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              )}
            </ChartZoomFrame>
            <p className="text-[9px] text-slate-400 mt-1">
              黄线：净值（粗）与 IV spread（细）。点落在 spread 轴上：Long/Normal 红 · Short/Reverse 绿 · 平仓蓝
              （由 executed position 变化推导）。贴边滑条：横向/纵向缩放与平移。
            </p>
          </div>

          <div className="bg-white rounded-lg border border-slate-200 p-4 overflow-x-auto">
            <h3 className="font-bold text-[#000066] text-sm mb-3">绩效指标</h3>
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-slate-500 border-b border-slate-200">
                  <th className="py-2 pr-3 font-bold">Window</th>
                  <th className="py-2 pr-3 font-bold">Ann. Return</th>
                  <th className="py-2 pr-3 font-bold">Ann. Vol</th>
                  <th className="py-2 pr-3 font-bold">Sharpe</th>
                  <th className="py-2 pr-3 font-bold">MDD</th>
                  <th className="py-2 pr-3 font-bold">MDD / Vol</th>
                  <th className="py-2 pr-3 font-bold">% +Months</th>
                  <th className="py-2 font-bold">Days</th>
                </tr>
              </thead>
              <tbody>
                {WINDOW_LABELS.map(({ key, label }) => {
                  const m = data.metrics[key];
                  if (!m) return null;
                  return (
                    <tr key={key} className="border-b border-slate-100 text-[#000066]">
                      <td className="py-2 pr-3 font-bold">
                        {label}
                        <div className="text-[9px] font-normal text-slate-400 font-mono">
                          {m.start && m.end ? `${m.start} → ${m.end}` : ''}
                        </div>
                      </td>
                      <td className="py-2 pr-3 tabular-nums">{fmtPct(m.ann_return)}</td>
                      <td className="py-2 pr-3 tabular-nums">{fmtPct(m.ann_vol)}</td>
                      <td className="py-2 pr-3 tabular-nums font-bold">{fmtNum(m.sharpe)}</td>
                      <td className="py-2 pr-3 tabular-nums">{fmtPct(m.mdd)}</td>
                      <td className="py-2 pr-3 tabular-nums">{fmtNum(m.mdd_vol)}</td>
                      <td className="py-2 pr-3 tabular-nums">
                        {m.pct_positive_months == null ? '—' : `${m.pct_positive_months.toFixed(1)}%`}
                      </td>
                      <td className="py-2 tabular-nums text-slate-500">{m.n_days}</td>
                    </tr>
                  );
                })}
                {(data.yearly_metrics?.length ?? 0) > 0 && (
                  <tr className="border-b border-slate-200">
                    <td colSpan={8} className="pt-3 pb-1 text-[10px] font-bold uppercase tracking-wide text-slate-500">
                      Calendar year
                    </td>
                  </tr>
                )}
                {(data.yearly_metrics ?? []).map((m) => (
                  <tr key={`y-${m.year}`} className="border-b border-slate-100 text-[#000066]">
                    <td className="py-2 pr-3 font-bold">
                      {m.year}
                      <div className="text-[9px] font-normal text-slate-400 font-mono">
                        {m.start && m.end ? `${m.start} → ${m.end}` : ''}
                      </div>
                    </td>
                    <td className="py-2 pr-3 tabular-nums">{fmtPct(m.ann_return)}</td>
                    <td className="py-2 pr-3 tabular-nums">{fmtPct(m.ann_vol)}</td>
                    <td className="py-2 pr-3 tabular-nums font-bold">{fmtNum(m.sharpe)}</td>
                    <td className="py-2 pr-3 tabular-nums">{fmtPct(m.mdd)}</td>
                    <td className="py-2 pr-3 tabular-nums">{fmtNum(m.mdd_vol)}</td>
                    <td className="py-2 pr-3 tabular-nums">
                      {m.pct_positive_months == null ? '—' : `${m.pct_positive_months.toFixed(1)}%`}
                    </td>
                    <td className="py-2 tabular-nums text-slate-500">{m.n_days}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="text-[9px] text-slate-400 mt-2">
              Sharpe 以 rf=0、252 交易日年化；MDD 为净值最大回撤。Calendar year
              按自然年切分，年初净值再基准化为 1；首尾不完整年份同样按实际交易日年化。
            </p>
          </div>

          {data.pnl_decomposition && decompChart.length > 0 && (
            <>
              <div className="bg-white rounded-lg border border-slate-200 p-4">
                <h3 className="font-bold text-[#000066] text-sm mb-3">Cumulative Greeks & Basis PnL</h3>
                <ChartZoomFrame
                  data={decompChart}
                  yKeys={['vega', 'theta', 'gamma', 'basis', 'net_delta', 'residual']}
                  height={320}
                >
                  {(win) => (
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={win.data}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                        <XAxis dataKey="d" tick={{ fontSize: 9 }} minTickGap={40} />
                        <YAxis
                          tick={{ fontSize: 10 }}
                          domain={win.yDomain ?? ['auto', 'auto']}
                          allowDataOverflow={!!win.yDomain}
                        />
                        <Tooltip contentStyle={{ fontSize: 11 }} formatter={(v: number) => fmtMoney(v)} />
                        <Legend wrapperStyle={{ fontSize: 10 }} />
                        <Line type="monotone" dataKey="vega" name="Vega" stroke={DECOMP_COLORS.vega} dot={false} strokeWidth={1.5} />
                        <Line type="monotone" dataKey="theta" name="Theta" stroke={DECOMP_COLORS.theta} dot={false} strokeWidth={1.5} />
                        <Line type="monotone" dataKey="gamma" name="Gamma" stroke={DECOMP_COLORS.gamma} dot={false} strokeWidth={1.5} />
                        <Line type="monotone" dataKey="basis" name="Basis" stroke={DECOMP_COLORS.basis} dot={false} strokeWidth={1.5} />
                        <Line type="monotone" dataKey="net_delta" name="Net Delta" stroke={DECOMP_COLORS.net_delta} dot={false} strokeWidth={1.5} />
                        <Line type="monotone" dataKey="residual" name="Residual" stroke={DECOMP_COLORS.residual} dot={false} strokeWidth={1} strokeDasharray="4 3" />
                      </LineChart>
                    </ResponsiveContainer>
                  )}
                </ChartZoomFrame>
                <p className="text-[9px] text-slate-400 mt-1">
                  累积曲线对应上方口径中的 Black-76 分解；账面 NAV 仍以 mid MTM + 期货对冲为准，Residual 吸收模型与 MTM 之差。
                </p>
              </div>

              {dailyGreeksChart.length > 0 && (
                <div className="bg-white rounded-lg border border-slate-200 p-4">
                  <h3 className="font-bold text-[#000066] text-sm mb-3">Daily Greeks PnL</h3>
                  <ChartZoomFrame data={dailyGreeksChart} yKeys={['vega', 'gamma', 'theta']} height={280}>
                    {(win) => (
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={win.data}>
                          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                          <XAxis dataKey="d" tick={{ fontSize: 9 }} minTickGap={40} />
                          <YAxis
                            yAxisId="vega"
                            tick={{ fontSize: 10 }}
                            domain={['auto', 'auto']}
                            stroke={DECOMP_COLORS.vega}
                          />
                          <YAxis
                            yAxisId="gt"
                            orientation="right"
                            tick={{ fontSize: 10 }}
                            domain={['auto', 'auto']}
                            stroke={DECOMP_COLORS.gamma}
                          />
                          <Tooltip contentStyle={{ fontSize: 11 }} formatter={(v: number) => fmtMoney(v)} />
                          <Legend wrapperStyle={{ fontSize: 10 }} />
                          <Bar yAxisId="vega" dataKey="vega" name="Vega (左轴)" fill={DECOMP_COLORS.vega} maxBarSize={6} />
                          <Bar yAxisId="gt" dataKey="gamma" name="Gamma (右轴)" fill={DECOMP_COLORS.gamma} maxBarSize={6} />
                          <Bar yAxisId="gt" dataKey="theta" name="Theta (右轴)" fill={DECOMP_COLORS.theta} maxBarSize={6} />
                        </BarChart>
                      </ResponsiveContainer>
                    )}
                  </ChartZoomFrame>
                  <p className="text-[9px] text-slate-400 mt-1">
                    日度（非累积）分解：Vega 用左轴；Gamma / Theta 用右轴（量级通常更小）。贴边滑条可缩放/平移。
                  </p>
                </div>
              )}

              {data.pnl_contribution && (
                <div className="bg-white rounded-lg border border-slate-200 p-4 overflow-x-auto">
                  <h3 className="font-bold text-[#000066] text-sm mb-3">PnL Risk Contribution</h3>
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-left text-slate-500 border-b border-slate-200">
                        <th className="py-2 pr-3 font-bold">Total PnL (¥)</th>
                        <th className="py-2 pr-3 font-bold">Vega %</th>
                        <th className="py-2 pr-3 font-bold">Theta Carry %</th>
                        <th className="py-2 pr-3 font-bold">Gamma Drag %</th>
                        <th className="py-2 pr-3 font-bold">Basis Impact %</th>
                        <th className="py-2 pr-3 font-bold">Net Delta %</th>
                        <th className="py-2 font-bold">Residual %</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr className="text-[#000066]">
                        <td className="py-2 pr-3 tabular-nums font-bold">{fmtMoney(data.pnl_contribution.total_pnl)}</td>
                        <td className="py-2 pr-3 tabular-nums" style={{ color: DECOMP_COLORS.vega }}>
                          {data.pnl_contribution.vega_pct == null ? '—' : `${data.pnl_contribution.vega_pct.toFixed(1)}%`}
                        </td>
                        <td className="py-2 pr-3 tabular-nums" style={{ color: DECOMP_COLORS.theta }}>
                          {data.pnl_contribution.theta_pct == null ? '—' : `${data.pnl_contribution.theta_pct.toFixed(1)}%`}
                        </td>
                        <td className="py-2 pr-3 tabular-nums" style={{ color: DECOMP_COLORS.gamma }}>
                          {data.pnl_contribution.gamma_pct == null ? '—' : `${data.pnl_contribution.gamma_pct.toFixed(1)}%`}
                        </td>
                        <td className="py-2 pr-3 tabular-nums" style={{ color: DECOMP_COLORS.basis }}>
                          {data.pnl_contribution.basis_pct == null ? '—' : `${data.pnl_contribution.basis_pct.toFixed(1)}%`}
                        </td>
                        <td className="py-2 pr-3 tabular-nums">
                          {data.pnl_contribution.net_delta_pct == null ? '—' : `${data.pnl_contribution.net_delta_pct.toFixed(1)}%`}
                        </td>
                        <td className="py-2 tabular-nums text-slate-500">
                          {data.pnl_contribution.residual_pct == null ? '—' : `${data.pnl_contribution.residual_pct.toFixed(1)}%`}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              )}

              {showGarchSignalChart && garchChartData.length > 0 && (
                <div className="bg-white rounded-lg border border-slate-200 p-4">
                  <h3 className="font-bold text-[#000066] text-sm mb-3">
                    {tenorLabel} GARCH-z &{' '}
                    {isRegimeSignalMode(activeSignal)
                      ? 'Decision Shock Bands'
                      : isRegime2SignalMode(activeSignal)
                        ? 'Stress Gate (10/90)'
                        : 'Rolling Quantile Bands'}
                  </h3>
                  <ChartZoomFrame
                    data={garchChartData}
                    yKeys={
                      isRegimeSignalMode(activeSignal)
                        ? [...regimeShockYKeys]
                        : ['spread_pts', 'enter_hi', 'enter_lo', 'exit_hi', 'exit_lo']
                    }
                    height={300}
                  >
                    {(win) => (
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={win.data}>
                          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                          <XAxis dataKey="d" tick={{ fontSize: 9 }} minTickGap={40} />
                          <YAxis
                            tick={{ fontSize: 10 }}
                            domain={win.yDomain ?? ['auto', 'auto']}
                            allowDataOverflow={!!win.yDomain}
                          />
                          <Tooltip contentStyle={{ fontSize: 11 }} />
                          <Legend wrapperStyle={{ fontSize: 10 }} />
                          <Line type="monotone" dataKey="spread_pts" name="garch_z" stroke={CHART.spread} dot={false} strokeWidth={1.5} />
                          {isRegimeSignalMode(activeSignal) ? (
                            <>
                              <Line type="monotone" dataKey="q90" name="shk q90 (ext)" stroke={CHART.shockExt} dot={false} strokeWidth={1} strokeDasharray="4 3" connectNulls />
                              <Line type="monotone" dataKey="q10" name="shk q10 (ext)" stroke={CHART.shockExt} dot={false} strokeWidth={1} strokeDasharray="4 3" connectNulls />
                              {regimeShowFade && (
                                <>
                                  <Line type="monotone" dataKey="q80" name="shk q80 (calm)" stroke={CHART.shockCalm} dot={false} strokeWidth={1} strokeDasharray="2 4" connectNulls />
                                  <Line type="monotone" dataKey="q20" name="shk q20 (calm)" stroke={CHART.shockCalm} dot={false} strokeWidth={1} strokeDasharray="2 4" connectNulls />
                                </>
                              )}
                            </>
                          ) : (
                            <>
                              <Line type="monotone" dataKey="enter_hi" name={`Enter hi (${enterPct.split('/')[1]}%)`} stroke={CHART.enterBand} dot={false} strokeWidth={1} strokeDasharray="4 3" connectNulls />
                              <Line type="monotone" dataKey="enter_lo" name={`Enter lo (${enterPct.split('/')[0]}%)`} stroke={CHART.enterBand} dot={false} strokeWidth={1} strokeDasharray="4 3" connectNulls />
                              <Line type="monotone" dataKey="exit_hi" name="Exit hi" stroke={CHART.exitBand} dot={false} strokeWidth={1} strokeDasharray="2 4" connectNulls />
                              <Line type="monotone" dataKey="exit_lo" name="Exit lo" stroke={CHART.exitBand} dot={false} strokeWidth={1} strokeDasharray="2 4" connectNulls />
                            </>
                          )}
                          <Line type="monotone" dataKey="long_pt" name="Long" stroke={CHART.long} strokeWidth={0} dot={markerDot(CHART.long)} activeDot={false} legendType="circle" connectNulls={false} isAnimationActive={false} />
                          <Line type="monotone" dataKey="short_pt" name="Short" stroke={CHART.short} strokeWidth={0} dot={markerDot(CHART.short)} activeDot={false} legendType="circle" connectNulls={false} isAnimationActive={false} />
                          <Line type="monotone" dataKey="close_pt" name="Close" stroke={CHART.close} strokeWidth={0} dot={markerDot(CHART.close)} activeDot={false} legendType="circle" connectNulls={false} isAnimationActive={false} />
                        </LineChart>
                      </ResponsiveContainer>
                    )}
                  </ChartZoomFrame>
                  <p className="text-[9px] text-slate-400 mt-1">
                    {isRegimeSignalMode(activeSignal)
                      ? '黄线：garch_z。紫虚线：冲击极端 shk q10/q90（Mom 加速入场 / Mom 不利冲击出场 / Fade crisis）。青虚线：冷静带 shk q20/q80（Fade 双日冷静入场）。分位 = 过去 W 日、shift(1)，与 FSM 决策同一套。'
                      : isRegime2SignalMode(activeSignal)
                        ? '黄线：garch_z。紫虚线：压力门 10/90（连续 N 日越出 → 停新开）。灰虚线：参考 40/60。决策开平仓仍看上方 Spread Quantile 图。'
                        : `黄线：garch_z。紫/灰虚线：对 garch_z 的滚动分位带（${enterPct} 入场 · ${exitPct} 出场口径）。`}
                    {' '}散点为实际成交：Long 红 · Short 绿 · 平仓蓝
                    {isRegimeSignalMode(activeSignal) || isRegime2SignalMode(activeSignal)
                      ? '（标在成交日 garch_z 水平）。'
                      : '。'}
                  </p>
                </div>
              )}

              {showSpreadBandChart && spreadChartData.length > 0 && (
                <div className="bg-white rounded-lg border border-slate-200 p-4">
                  <h3 className="font-bold text-[#000066] text-sm mb-3">
                    {isRegimeSignalMode(activeSignal)
                      ? `${tenorLabel} Spread & Decision Level Bands`
                      : activeSignal === 'garch_z'
                        ? `${tenorLabel} CT-IV Spread`
                        : `${tenorLabel} Spread & Rolling Quantile Bands`}
                  </h3>
                  <ChartZoomFrame
                    data={spreadChartData}
                    yKeys={
                      isRegimeSignalMode(activeSignal)
                        ? [...regimeLevelYKeys]
                        : activeSignal === 'garch_z'
                          ? ['spread_pts']
                          : ['spread_pts', 'enter_hi', 'enter_lo', 'exit_hi', 'exit_lo']
                    }
                    height={300}
                  >
                    {(win) => (
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={win.data}>
                          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                          <XAxis dataKey="d" tick={{ fontSize: 9 }} minTickGap={40} />
                          <YAxis
                            tick={{ fontSize: 10 }}
                            domain={win.yDomain ?? ['auto', 'auto']}
                            allowDataOverflow={!!win.yDomain}
                            unit="pt"
                          />
                          <Tooltip contentStyle={{ fontSize: 11 }} />
                          <Legend wrapperStyle={{ fontSize: 10 }} />
                          <Line
                            type="monotone"
                            dataKey="spread_pts"
                            name={ivSpreadLabel}
                            stroke={CHART.spread}
                            dot={false}
                            strokeWidth={1.5}
                          />
                          {isRegimeSignalMode(activeSignal) ? (
                            <>
                              <Line type="monotone" dataKey="q90" name="lvl q90" stroke={CHART.fadeExt} dot={false} strokeWidth={1} strokeDasharray="4 3" connectNulls />
                              <Line type="monotone" dataKey="q10" name="lvl q10" stroke={CHART.fadeExt} dot={false} strokeWidth={1} strokeDasharray="4 3" connectNulls />
                              {regimeShowMom && (
                                <>
                                  <Line type="monotone" dataKey="q75" name="lvl q75 (Mom)" stroke={CHART.accel} dot={false} strokeWidth={1} strokeDasharray="2 3" connectNulls />
                                  <Line type="monotone" dataKey="q25" name="lvl q25 (Mom)" stroke={CHART.accel} dot={false} strokeWidth={1} strokeDasharray="2 3" connectNulls />
                                </>
                              )}
                              <Line type="monotone" dataKey="q50" name="lvl q50 (mean)" stroke={CHART.meanRev} dot={false} strokeWidth={1.25} strokeDasharray="1 3" connectNulls />
                            </>
                          ) : activeSignal === 'quantile' || isRegime2SignalMode(activeSignal) ? (
                            <>
                              <Line type="monotone" dataKey="enter_hi" name={`Enter hi (${enterPct.split('/')[1]}%)`} stroke={CHART.enterBand} dot={false} strokeWidth={1} strokeDasharray="4 3" connectNulls />
                              <Line type="monotone" dataKey="enter_lo" name={`Enter lo (${enterPct.split('/')[0]}%)`} stroke={CHART.enterBand} dot={false} strokeWidth={1} strokeDasharray="4 3" connectNulls />
                              <Line type="monotone" dataKey="exit_hi" name="Exit hi" stroke={CHART.exitBand} dot={false} strokeWidth={1} strokeDasharray="2 4" connectNulls />
                              <Line type="monotone" dataKey="exit_lo" name="Exit lo" stroke={CHART.exitBand} dot={false} strokeWidth={1} strokeDasharray="2 4" connectNulls />
                            </>
                          ) : null}
                          <Line type="monotone" dataKey="long_pt" name="Long" stroke={CHART.long} strokeWidth={0} dot={markerDot(CHART.long)} activeDot={false} legendType="circle" connectNulls={false} isAnimationActive={false} />
                          <Line type="monotone" dataKey="short_pt" name="Short" stroke={CHART.short} strokeWidth={0} dot={markerDot(CHART.short)} activeDot={false} legendType="circle" connectNulls={false} isAnimationActive={false} />
                          <Line type="monotone" dataKey="close_pt" name="Close" stroke={CHART.close} strokeWidth={0} dot={markerDot(CHART.close)} activeDot={false} legendType="circle" connectNulls={false} isAnimationActive={false} />
                        </LineChart>
                      </ResponsiveContainer>
                    )}
                  </ChartZoomFrame>
                  <p className="text-[9px] text-slate-400 mt-1">
                    {isRegimeSignalMode(activeSignal)
                      ? '黄线：CT-IV Spread。紫：共享极端/外沿 lvl q10/q90；青：Momentum 内沿 lvl q25/q75；褐：均值 lvl q50（Fade 出场 + Mom 衰竭）。分位与决策同一套（过去 W 日、shift(1)）。散点：Long 红 · Short 绿 · 平仓蓝。'
                      : activeSignal === 'garch_z'
                        ? '黄线：CT-IV Spread（vol pts）。本图不叠分位带——开平仓由上方 GARCH-z 决策分位决定。散点标在成交日 Spread 水平。'
                        : isRegime2SignalMode(activeSignal)
                          ? `黄线：CT-IV Spread（vol pts）。紫虚线：入场分位 ${enterPct}；灰虚线：出场带 ${exitPct}%（与 Quantile 相同）。上方 GARCH-z 图为压力门。散点：Long 红 · Short 绿 · 平仓蓝。`
                          : `黄线：CT-IV Spread（vol pts）。紫虚线：入场分位 ${enterPct}；灰虚线：出场带 ${exitPct}%。散点：Long 红 · Short 绿 · 平仓蓝。分位仅用过去 ${data.signal.window ?? data.params.z_window ?? 252} 日、不含当日/未来。Reverse 最长 20 交易日。`}
                  </p>
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
};

export default QisView;
