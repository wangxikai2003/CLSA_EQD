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

const CAL_VOL_SIGNALS: { key: QisSignalMode; label: string }[] = [
  { key: 'quantile', label: 'Quantile' },
  { key: 'garch_z', label: 'GARCH-z' },
];

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
};

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
    setLoadStatus(refresh ? '刷新回测…' : 'Running backtest…');
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

  /** CT-IV spread + its own rolling quantile bands (always vol-pt scale). */
  const ivSpreadChart = useMemo(() => {
    const s = data?.signal;
    if (!s?.dates?.length) return [];
    const ivSeries = s.spread_iv ?? (levelScale === 100 ? s.spread : null);
    if (!ivSeries?.length) return [];
    const useIvBands =
      Array.isArray(s.iv_enter_hi) && s.iv_enter_hi.length === s.dates.length;
    const useDecBands =
      !useIvBands && Array.isArray(s.enter_hi) && s.enter_hi.length === s.dates.length && levelScale === 100;
    const kindByD = byKindIv;
    return s.dates.map((dt, i) => {
      const raw = ivSeries[i];
      const spreadPts = raw == null || Number.isNaN(raw) ? 0 : raw * 100;
      const kind = kindByD.get(dt);
      const eh = useIvBands ? s.iv_enter_hi![i] : useDecBands ? s.enter_hi![i] : null;
      const el = useIvBands ? s.iv_enter_lo![i] : useDecBands ? s.enter_lo![i] : null;
      const xh = useIvBands ? s.iv_exit_hi![i] : useDecBands ? s.exit_hi![i] : null;
      const xl = useIvBands ? s.iv_exit_lo![i] : useDecBands ? s.exit_lo![i] : null;
      return {
        d: dt,
        spread_pts: spreadPts,
        enter_hi: eh != null ? (eh as number) * 100 : null,
        enter_lo: el != null ? (el as number) * 100 : null,
        exit_hi: xh != null ? (xh as number) * 100 : null,
        exit_lo: xl != null ? (xl as number) * 100 : null,
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
  const signalLabel = CAL_VOL_SIGNALS.find((s) => s.key === activeSignal)?.label ?? activeSignal;
  const sosLabel = isCalVol
    ? activeSignal === 'garch_z'
      ? `${tenorLabel} GARCH-z`
      : activeSpread === 'iv2'
        ? `${tenorLabel} IV² spread (var ×100)`
        : `${tenorLabel} IV spread (vol pts)`
    : 'SoS (pts)';
  const ivSpreadLabel = isCalVol
    ? activeSpread === 'iv2'
      ? `${tenorLabel} IV² spread (var ×100)`
      : `${tenorLabel} IV spread (vol pts)`
    : 'Spread (pts)';
  const showGarchSignalChart = isCalVol && activeSignal === 'garch_z';

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
        ? '信号层：对 ΔSpread 做 expanding GARCH(1,1)-t（预热 504 日、每 5 日重估参数，仅用截至当日历史）得到 garch_z；再对 garch_z 做同样的滚动分位入场/出场（无未来数据；t−1 信号 → t close 成交）。'
        : '信号层：直接对 Spread 做滚动分位入场/出场（无未来数据；t−1 信号 → t close 成交）。';

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

    return {
      near,
      far,
      tNear,
      tFar,
      zWin,
      spreadFormula,
      spreadModeNote,
      signalModeNote,
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
        <div className="flex flex-wrap items-center gap-2">
          <select
            className="px-3 py-1.5 text-xs font-bold border border-slate-200 rounded-md bg-slate-50 text-[#000066]"
            value={family}
            onChange={(e) => setFamily(e.target.value as FamilyKey)}
          >
            <option value="cross_calendar_carry">Cross Calendar Carry</option>
            <option value="calendar_vol">Equity Calendar Vol</option>
          </select>
          {family === 'calendar_vol' && (
            <>
              <div className="flex rounded-md border border-slate-200 overflow-hidden">
                {CAL_VOL_UNIVERSES.map((u) => (
                  <button
                    key={u.key}
                    type="button"
                    onClick={() => setUniverse(u.key)}
                    className={`px-3 py-1.5 text-xs font-bold ${
                      universe === u.key
                        ? 'bg-[#000066] text-white'
                        : 'bg-slate-50 text-slate-600 hover:bg-slate-100'
                    }`}
                  >
                    {u.label}
                  </button>
                ))}
              </div>
              <div className="flex rounded-md border border-slate-200 overflow-hidden">
                {CAL_VOL_TENORS.map((t) => (
                  <button
                    key={t.key}
                    type="button"
                    onClick={() => setTenorPair(t.key)}
                    className={`px-3 py-1.5 text-xs font-bold ${
                      tenorPair === t.key
                        ? 'bg-[#000066] text-white'
                        : 'bg-slate-50 text-slate-600 hover:bg-slate-100'
                    }`}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
              <div className="flex rounded-md border border-slate-200 overflow-hidden">
                {CAL_VOL_SIDES.map((s) => (
                  <button
                    key={s.key}
                    type="button"
                    onClick={() => setSideMode(s.key)}
                    className={`px-3 py-1.5 text-xs font-bold ${
                      sideMode === s.key
                        ? 'bg-[#000066] text-white'
                        : 'bg-slate-50 text-slate-600 hover:bg-slate-100'
                    }`}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
              <div className="flex rounded-md border border-slate-200 overflow-hidden">
                {CAL_VOL_SPREADS.map((s) => (
                  <button
                    key={s.key}
                    type="button"
                    onClick={() => setSpreadMode(s.key)}
                    className={`px-3 py-1.5 text-xs font-bold ${
                      spreadMode === s.key
                        ? 'bg-[#000066] text-white'
                        : 'bg-slate-50 text-slate-600 hover:bg-slate-100'
                    }`}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
              <div className="flex rounded-md border border-slate-200 overflow-hidden">
                {CAL_VOL_SIGNALS.map((s) => (
                  <button
                    key={s.key}
                    type="button"
                    onClick={() => setSignalMode(s.key)}
                    className={`px-3 py-1.5 text-xs font-bold ${
                      signalMode === s.key
                        ? 'bg-[#000066] text-white'
                        : 'bg-slate-50 text-slate-600 hover:bg-slate-100'
                    }`}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            </>
          )}
          <button
            type="button"
            onClick={() => load(true)}
            className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-bold rounded-md bg-[#000066] text-white"
          >
            <RefreshCw size={12} /> Refresh
          </button>
          {isCalVol && (
            <a
              href={qisHoldingsExcelUrl(strategyId, 252, tenorPair, sideMode, spreadMode, signalMode)}
              className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-bold rounded-md border border-slate-200 bg-white text-[#000066] hover:bg-slate-50"
              title="Download concrete holdings Excel (t−1 signal / t close)"
            >
              <Download size={12} /> Holdings.xlsx
            </a>
          )}
        </div>
      </div>

      {loading && (
        <div className="text-xs text-slate-500 flex items-center gap-2">
          <span className="inline-block h-2 w-2 rounded-full bg-[#EAB308] animate-pulse" />
          {loadStatus}
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
                    信号算子
                  </div>
                  <p>
                    CT-IV：SVI otm，总方差对 T 插值（ACT/365），目标 T={calVolDocs.tNear}（{calVolDocs.near}）/
                    {calVolDocs.tFar}（{calVolDocs.far}）。
                    {calVolDocs.spreadModeNote}
                    公式 <code className="font-mono text-[10px]">{calVolDocs.spreadFormula}</code>。
                    {calVolDocs.signalModeNote}
                    滚动分位窗 {calVolDocs.zWin} 日（不含当日/未来）：入场 {enterPct}（level &gt; hi → Normal/+1；
                    &lt; lo → Reverse/−1）；出场落入 {exitPct}% 带平仓。Reverse max hold = 20 交易日。
                  </p>
                </div>
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
                    远端美元 Vega 预算 = 1% NAV / vol-pt；
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
              </tbody>
            </table>
            <p className="text-[9px] text-slate-400 mt-2">
              Sharpe 以 rf=0、252 交易日年化；MDD 为净值最大回撤。
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

              {showGarchSignalChart && signalChart.length > 0 && (
                <div className="bg-white rounded-lg border border-slate-200 p-4">
                  <h3 className="font-bold text-[#000066] text-sm mb-3">
                    {tenorLabel} GARCH-z & Rolling Quantile Bands
                  </h3>
                  <ChartZoomFrame
                    data={signalChart}
                    yKeys={['spread_pts', 'enter_hi', 'enter_lo', 'exit_hi', 'exit_lo']}
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
                          <Line type="monotone" dataKey="spread_pts" name={sosLabel} stroke={CHART.spread} dot={false} strokeWidth={1.5} />
                          <Line type="monotone" dataKey="enter_hi" name={`Enter hi (${enterPct.split('/')[1]}%)`} stroke={CHART.enterBand} dot={false} strokeWidth={1} strokeDasharray="4 3" connectNulls />
                          <Line type="monotone" dataKey="enter_lo" name={`Enter lo (${enterPct.split('/')[0]}%)`} stroke={CHART.enterBand} dot={false} strokeWidth={1} strokeDasharray="4 3" connectNulls />
                          <Line type="monotone" dataKey="exit_hi" name="Exit hi" stroke={CHART.exitBand} dot={false} strokeWidth={1} strokeDasharray="2 4" connectNulls />
                          <Line type="monotone" dataKey="exit_lo" name="Exit lo" stroke={CHART.exitBand} dot={false} strokeWidth={1} strokeDasharray="2 4" connectNulls />
                          <Line type="monotone" dataKey="long_pt" name="Long" stroke={CHART.long} strokeWidth={0} dot={markerDot(CHART.long)} activeDot={false} legendType="circle" connectNulls={false} isAnimationActive={false} />
                          <Line type="monotone" dataKey="short_pt" name="Short" stroke={CHART.short} strokeWidth={0} dot={markerDot(CHART.short)} activeDot={false} legendType="circle" connectNulls={false} isAnimationActive={false} />
                          <Line type="monotone" dataKey="close_pt" name="Close" stroke={CHART.close} strokeWidth={0} dot={markerDot(CHART.close)} activeDot={false} legendType="circle" connectNulls={false} isAnimationActive={false} />
                        </LineChart>
                      </ResponsiveContainer>
                    )}
                  </ChartZoomFrame>
                  <p className="text-[9px] text-slate-400 mt-1">
                    黄线：garch_z（决策变量）。紫/灰虚线：对 garch_z 的滚动分位入场/出场带（与 Spread 策略同口径 10/90·40/60）。
                    散点为实际成交：Long 红 · Short 绿 · 平仓蓝。
                  </p>
                </div>
              )}

              {data.signal && (showGarchSignalChart ? ivSpreadChart : signalChart).length > 0 && (
                <div className="bg-white rounded-lg border border-slate-200 p-4">
                  <h3 className="font-bold text-[#000066] text-sm mb-3">
                    {tenorLabel} Spread & Rolling Quantile Bands
                  </h3>
                  <ChartZoomFrame
                    data={showGarchSignalChart ? ivSpreadChart : signalChart}
                    yKeys={['spread_pts', 'enter_hi', 'enter_lo', 'exit_hi', 'exit_lo']}
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
                            name={showGarchSignalChart ? ivSpreadLabel : sosLabel}
                            stroke={CHART.spread}
                            dot={false}
                            strokeWidth={1.5}
                          />
                          <Line type="monotone" dataKey="enter_hi" name={`Enter hi (${enterPct.split('/')[1]}%)`} stroke={CHART.enterBand} dot={false} strokeWidth={1} strokeDasharray="4 3" connectNulls />
                          <Line type="monotone" dataKey="enter_lo" name={`Enter lo (${enterPct.split('/')[0]}%)`} stroke={CHART.enterBand} dot={false} strokeWidth={1} strokeDasharray="4 3" connectNulls />
                          <Line type="monotone" dataKey="exit_hi" name="Exit hi" stroke={CHART.exitBand} dot={false} strokeWidth={1} strokeDasharray="2 4" connectNulls />
                          <Line type="monotone" dataKey="exit_lo" name="Exit lo" stroke={CHART.exitBand} dot={false} strokeWidth={1} strokeDasharray="2 4" connectNulls />
                          <Line type="monotone" dataKey="long_pt" name="Long" stroke={CHART.long} strokeWidth={0} dot={markerDot(CHART.long)} activeDot={false} legendType="circle" connectNulls={false} isAnimationActive={false} />
                          <Line type="monotone" dataKey="short_pt" name="Short" stroke={CHART.short} strokeWidth={0} dot={markerDot(CHART.short)} activeDot={false} legendType="circle" connectNulls={false} isAnimationActive={false} />
                          <Line type="monotone" dataKey="close_pt" name="Close" stroke={CHART.close} strokeWidth={0} dot={markerDot(CHART.close)} activeDot={false} legendType="circle" connectNulls={false} isAnimationActive={false} />
                        </LineChart>
                      </ResponsiveContainer>
                    )}
                  </ChartZoomFrame>
                  <p className="text-[9px] text-slate-400 mt-1">
                    黄线：CT-IV Spread（vol pts）。紫虚线：对 Spread 水平的滚动入场分位；灰虚线：出场带（落入 {exitPct}% 区间）。
                    {showGarchSignalChart
                      ? ' 本图为对照：GARCH-z 模式下实际开平仓由上方 garch_z 分位决定，散点仍标在成交日对应的 Spread 水平。'
                      : ` 散点：Long 红 · Short 绿 · 平仓蓝。分位仅用过去 ${data.signal.window ?? data.params.z_window ?? 252} 日、不含当日/未来。Reverse 最长 20 交易日。`}
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
