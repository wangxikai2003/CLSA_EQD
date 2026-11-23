import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Play } from 'lucide-react';

export type ChartWindow = {
  /** Visible slice of the full series. */
  data: Record<string, unknown>[];
  /** Optional Y domain after vertical zoom/pan; null = leave auto. */
  yDomain: [number, number] | null;
  xZoom: number;
  yZoom: number;
};

/** How to rebuild series after applying a start date. */
export type ChartRecalcMode = 'none' | 'rebase' | 'zero';

type Props = {
  data: Record<string, unknown>[];
  /** Numeric keys used to compute Y extents for vertical zoom (visible window only). */
  yKeys?: string[];
  height?: number;
  className?: string;
  /** Date field on each row (ISO YYYY-MM-DD). */
  dateKey?: string;
  /**
   * After start-date filter:
   * - none: keep values
   * - rebase: divide each key by its value on the first included day (NAV → 1)
   * - zero: subtract pre-start baseline so cumulative series restarts at 0
   */
  recalcMode?: ChartRecalcMode;
  /** Keys transformed by recalcMode; defaults to yKeys. */
  recalcKeys?: string[];
  children: (win: ChartWindow) => React.ReactNode;
};

function clamp(x: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, x));
}

function finiteNums(rows: Record<string, unknown>[], keys: string[]): number[] {
  const out: number[] = [];
  for (const r of rows) {
    for (const k of keys) {
      const v = r[k];
      if (typeof v === 'number' && Number.isFinite(v)) out.push(v);
    }
  }
  return out;
}

function rowDate(row: Record<string, unknown> | undefined, dateKey: string): string {
  const v = row?.[dateKey];
  return typeof v === 'string' ? v.slice(0, 10) : '';
}

function applyStartRecalc(
  full: Record<string, unknown>[],
  startDate: string | null,
  dateKey: string,
  mode: ChartRecalcMode,
  keys: string[],
): Record<string, unknown>[] {
  if (!full.length) return full;
  if (!startDate) return full;

  let idx = full.findIndex((r) => rowDate(r, dateKey) >= startDate);
  if (idx < 0) return [];
  const sliced = full.slice(idx);
  if (!sliced.length || mode === 'none' || !keys.length) return sliced;

  if (mode === 'rebase') {
    const base: Record<string, number> = {};
    for (const k of keys) {
      const v = sliced[0][k];
      if (typeof v === 'number' && Number.isFinite(v) && v !== 0) base[k] = v;
    }
    return sliced.map((r) => {
      const out: Record<string, unknown> = { ...r };
      for (const k of keys) {
        const v = r[k];
        if (typeof v === 'number' && Number.isFinite(v) && base[k] != null) {
          out[k] = v / base[k];
        }
      }
      return out;
    });
  }

  // zero: restart cumulative at the applied start (subtract prior day's level).
  const baseline: Record<string, number> = {};
  const prior = idx > 0 ? full[idx - 1] : null;
  for (const k of keys) {
    const v = prior?.[k];
    baseline[k] = typeof v === 'number' && Number.isFinite(v) ? v : 0;
  }
  return sliced.map((r) => {
    const out: Record<string, unknown> = { ...r };
    for (const k of keys) {
      const v = r[k];
      if (typeof v === 'number' && Number.isFinite(v)) out[k] = v - baseline[k];
    }
    return out;
  });
}

/** Edge-attached zoom + pan bars (browser-scrollbar style) around a chart. */
const ChartZoomFrame: React.FC<Props> = ({
  data,
  yKeys = [],
  height = 300,
  className = '',
  dateKey = 'd',
  recalcMode = 'none',
  recalcKeys,
  children,
}) => {
  const keysForRecalc = recalcKeys ?? yKeys;
  const keysForRecalcSig = keysForRecalc.join('\0');
  const [draftStart, setDraftStart] = useState('');
  const [appliedStart, setAppliedStart] = useState<string | null>(null);

  const dataMin = rowDate(data[0], dateKey);
  const dataMax = rowDate(data[data.length - 1], dateKey);

  // Reset start-date window when underlying series identity changes.
  const seriesKey = `${data.length}:${dataMin}:${dataMax}:${recalcMode}`;
  useEffect(() => {
    setDraftStart(dataMin);
    setAppliedStart(null);
  }, [seriesKey, dataMin]);

  const sourceData = useMemo(
    () => applyStartRecalc(data, appliedStart, dateKey, recalcMode, keysForRecalc),
    // keysForRecalcSig tracks key list identity; keysForRecalc is read inside.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data, appliedStart, dateKey, recalcMode, keysForRecalcSig],
  );

  const n = sourceData.length;
  // visible fraction of X (1 = full); pan in [0,1] along scrollable range
  const [xZoom, setXZoom] = useState(1);
  const [xPan, setXPan] = useState(0);
  const [yZoom, setYZoom] = useState(1);
  const [yPan, setYPan] = useState(0);

  const zoomSeriesKey = `${n}:${rowDate(sourceData[0], dateKey)}:${rowDate(sourceData[n - 1], dateKey)}`;
  useEffect(() => {
    setXZoom(1);
    setXPan(0);
    setYZoom(1);
    setYPan(0);
  }, [zoomSeriesKey]);

  const win = useMemo((): ChartWindow => {
    if (n <= 1) {
      return { data: sourceData, yDomain: null, xZoom: 1, yZoom: 1 };
    }
    const xz = clamp(xZoom, 0.05, 1);
    const winLen = Math.max(2, Math.round(n * xz));
    const maxStart = Math.max(0, n - winLen);
    const start = Math.round(clamp(xPan, 0, 1) * maxStart);
    const sliced = sourceData.slice(start, start + winLen);

    let yDomain: [number, number] | null = null;
    if (yKeys.length && yZoom < 0.999) {
      const vals = finiteNums(sliced, yKeys);
      if (vals.length >= 2) {
        let lo = Math.min(...vals);
        let hi = Math.max(...vals);
        if (lo === hi) {
          const pad = Math.abs(lo) * 0.05 || 1;
          lo -= pad;
          hi += pad;
        }
        const span = hi - lo;
        const mid = (lo + hi) / 2;
        const half = (span * clamp(yZoom, 0.05, 1)) / 2;
        const fullTravel = Math.max(span / 2 - half, 0);
        const center = mid + (clamp(yPan, 0, 1) * 2 - 1) * fullTravel;
        yDomain = [center - half, center + half];
      }
    }
    return { data: sliced, yDomain, xZoom: xz, yZoom };
  }, [sourceData, n, xZoom, xPan, yZoom, yPan, yKeys]);

  const hTrackRef = useRef<HTMLDivElement>(null);
  const vTrackRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{
    axis: 'x' | 'y';
    mode: 'pan' | 'zoom';
    startPos: number;
    startPan: number;
    startZoom: number;
  } | null>(null);

  const onPointerMove = useCallback((e: PointerEvent) => {
    const d = dragRef.current;
    if (!d) return;
    if (d.axis === 'x') {
      const track = hTrackRef.current;
      if (!track) return;
      const rect = track.getBoundingClientRect();
      const w = Math.max(rect.width, 1);
      if (d.mode === 'pan') {
        const travel = Math.max(1 - d.startZoom, 1e-6);
        const dx = (e.clientX - d.startPos) / w / travel;
        setXPan(clamp(d.startPan + dx, 0, 1));
      } else {
        const rel = clamp((e.clientX - rect.left) / w, 0.05, 1);
        setXZoom(rel);
      }
    } else {
      const track = vTrackRef.current;
      if (!track) return;
      const rect = track.getBoundingClientRect();
      const h = Math.max(rect.height, 1);
      if (d.mode === 'pan') {
        const travel = Math.max(1 - d.startZoom, 1e-6);
        // Screen Y grows downward; invert so dragging thumb down pans to lower values.
        const dy = (e.clientY - d.startPos) / h / travel;
        setYPan(clamp(d.startPan + dy, 0, 1));
      } else {
        const rel = clamp(1 - (e.clientY - rect.top) / h, 0.05, 1);
        setYZoom(rel);
      }
    }
  }, []);

  const onPointerUp = useCallback(() => {
    dragRef.current = null;
    window.removeEventListener('pointermove', onPointerMove);
    window.removeEventListener('pointerup', onPointerUp);
  }, [onPointerMove]);

  const startDrag = (
    axis: 'x' | 'y',
    mode: 'pan' | 'zoom',
    clientPos: number,
    e: React.PointerEvent
  ) => {
    e.preventDefault();
    e.stopPropagation();
    dragRef.current = {
      axis,
      mode,
      startPos: clientPos,
      startPan: axis === 'x' ? xPan : yPan,
      startZoom: axis === 'x' ? xZoom : yZoom,
    };
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onPointerUp);
  };

  const xz = clamp(xZoom, 0.05, 1);
  const yz = clamp(yZoom, 0.05, 1);
  const xThumbPct = xz * 100;
  const xLeftPct = clamp(xPan, 0, 1) * (100 - xThumbPct);
  const yThumbPct = yz * 100;
  const yTopPct = clamp(yPan, 0, 1) * (100 - yThumbPct);

  const applyStart = () => {
    const next = (draftStart || dataMin).slice(0, 10);
    if (!next) return;
    if (!dataMin || next <= dataMin) {
      setAppliedStart(null);
      setDraftStart(dataMin);
      return;
    }
    setAppliedStart(next);
  };

  const clearStart = () => {
    setAppliedStart(null);
    setDraftStart(dataMin);
  };

  const recalcHint =
    recalcMode === 'rebase'
      ? '启动后从该日起截取并将曲线再基准化为起点'
      : recalcMode === 'zero'
        ? '启动后从该日起截取并重算累积（起点归零）'
        : '启动后从该日起截取重绘';

  return (
    <div className={`flex flex-col gap-0 ${className}`}>
      <div className="flex flex-wrap items-center gap-2 mb-2 text-[10px] text-slate-600">
        <span className="font-bold uppercase tracking-wide text-slate-400">开始</span>
        <input
          type="date"
          value={draftStart || dataMin}
          min={dataMin || undefined}
          max={dataMax || undefined}
          onChange={(e) => setDraftStart(e.target.value)}
          className="px-1.5 py-0.5 border border-slate-200 rounded bg-white text-[#000066] font-mono"
        />
        <button
          type="button"
          onClick={applyStart}
          title={recalcHint}
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-[#000066] text-white font-bold hover:bg-[#000066]/90"
        >
          <Play size={10} fill="currentColor" /> 启动
        </button>
        {appliedStart && (
          <>
            <span className="font-mono text-slate-500">自 {appliedStart}</span>
            <button
              type="button"
              onClick={clearStart}
              className="text-[#000066] font-bold hover:underline"
            >
              清除
            </button>
          </>
        )}
      </div>
      <div className="flex" style={{ height }}>
        <div className="flex-1 min-w-0 h-full">{children(win)}</div>
        {/* Right edge: vertical zoom + pan (browser-bar style) */}
        <div className="w-3 flex flex-col ml-1 shrink-0 select-none" style={{ height }} title="纵向：上段缩放，下段平移">
          <div
            ref={vTrackRef}
            className="flex-1 relative rounded-sm bg-slate-100 border border-slate-200 overflow-hidden"
            onPointerDown={(e) => startDrag('y', 'zoom', e.clientY, e)}
            title="纵向缩放（拖动）"
          >
            <div
              className="absolute left-0 right-0 bg-slate-400/70 hover:bg-[#000066]/70 rounded-sm cursor-ns-resize"
              style={{ top: `${(1 - yz) * 50}%`, height: `${yz * 100}%` }}
              onPointerDown={(e) => startDrag('y', 'zoom', e.clientY, e)}
            />
          </div>
          <div
            className="flex-1 relative mt-0.5 rounded-sm bg-slate-100 border border-slate-200 overflow-hidden"
            onPointerDown={(e) => {
              const track = e.currentTarget.getBoundingClientRect();
              const rel = clamp((e.clientY - track.top) / Math.max(track.height, 1), 0, 1);
              const thumb = yz;
              setYPan(clamp((rel - thumb / 2) / Math.max(1 - thumb, 1e-6), 0, 1));
              startDrag('y', 'pan', e.clientY, e);
            }}
            title="纵向平移"
          >
            <div
              className="absolute left-0 right-0 bg-[#000066]/55 hover:bg-[#000066]/80 rounded-sm cursor-grab active:cursor-grabbing"
              style={{ top: `${yTopPct}%`, height: `${yThumbPct}%`, minHeight: 8 }}
              onPointerDown={(e) => startDrag('y', 'pan', e.clientY, e)}
            />
          </div>
        </div>
      </div>
      {/* Bottom edge: horizontal zoom + pan */}
      <div className="mt-1 pr-4 select-none" title="横向：上段缩放，下段平移">
        <div
          className="h-2.5 relative rounded-sm bg-slate-100 border border-slate-200 overflow-hidden"
          onPointerDown={(e) => startDrag('x', 'zoom', e.clientX, e)}
          title="横向缩放（拖动）"
        >
          <div
            className="absolute top-0 bottom-0 bg-slate-400/70 hover:bg-[#000066]/70 rounded-sm cursor-ew-resize"
            style={{ left: `${(1 - xz) * 50}%`, width: `${xz * 100}%` }}
            onPointerDown={(e) => startDrag('x', 'zoom', e.clientX, e)}
          />
        </div>
        <div
          ref={hTrackRef}
          className="h-2.5 mt-0.5 relative rounded-sm bg-slate-100 border border-slate-200 overflow-hidden"
          onPointerDown={(e) => {
            const track = e.currentTarget.getBoundingClientRect();
            const rel = clamp((e.clientX - track.left) / Math.max(track.width, 1), 0, 1);
            const thumb = xz;
            setXPan(clamp((rel - thumb / 2) / Math.max(1 - thumb, 1e-6), 0, 1));
            startDrag('x', 'pan', e.clientX, e);
          }}
          title="横向平移"
        >
          <div
            className="absolute top-0 bottom-0 bg-[#000066]/55 hover:bg-[#000066]/80 rounded-sm cursor-grab active:cursor-grabbing"
            style={{ left: `${xLeftPct}%`, width: `${xThumbPct}%`, minWidth: 12 }}
            onPointerDown={(e) => startDrag('x', 'pan', e.clientX, e)}
          />
        </div>
        <div className="flex justify-between text-[9px] text-slate-400 mt-0.5 px-0.5">
          <span>横/纵缩放 · 平移条贴边</span>
          <button
            type="button"
            className="text-[#000066] font-bold hover:underline"
            onClick={() => {
              setXZoom(1);
              setXPan(0);
              setYZoom(1);
              setYPan(0);
            }}
          >
            重置
          </button>
        </div>
      </div>
    </div>
  );
};

export default ChartZoomFrame;
