import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

export type ChartWindow = {
  /** Visible slice of the full series. */
  data: Record<string, unknown>[];
  /** Optional Y domain after vertical zoom/pan; null = leave auto. */
  yDomain: [number, number] | null;
  xZoom: number;
  yZoom: number;
};

type Props = {
  data: Record<string, unknown>[];
  /** Numeric keys used to compute Y extents for vertical zoom (visible window only). */
  yKeys?: string[];
  height?: number;
  className?: string;
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

/** Edge-attached zoom + pan bars (browser-scrollbar style) around a chart. */
const ChartZoomFrame: React.FC<Props> = ({
  data,
  yKeys = [],
  height = 300,
  className = '',
  children,
}) => {
  const n = data.length;
  // visible fraction of X (1 = full); pan in [0,1] along scrollable range
  const [xZoom, setXZoom] = useState(1);
  const [xPan, setXPan] = useState(0);
  const [yZoom, setYZoom] = useState(1);
  const [yPan, setYPan] = useState(0);

  // Reset window when series identity changes (length / first-last date).
  const seriesKey = `${n}:${String(data[0]?.d ?? '')}:${String(data[n - 1]?.d ?? '')}`;
  useEffect(() => {
    setXZoom(1);
    setXPan(0);
    setYZoom(1);
    setYPan(0);
  }, [seriesKey]);

  const win = useMemo((): ChartWindow => {
    if (n <= 1) {
      return { data, yDomain: null, xZoom: 1, yZoom: 1 };
    }
    const xz = clamp(xZoom, 0.05, 1);
    const winLen = Math.max(2, Math.round(n * xz));
    const maxStart = Math.max(0, n - winLen);
    const start = Math.round(clamp(xPan, 0, 1) * maxStart);
    const sliced = data.slice(start, start + winLen);

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
  }, [data, n, xZoom, xPan, yZoom, yPan, yKeys]);

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

  return (
    <div className={`flex flex-col gap-0 ${className}`}>
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
