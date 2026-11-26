import React, { useEffect, useMemo, useState } from 'react';
import { Download } from 'lucide-react';
import {
  fetchQisLedger,
  QisBlotterRow,
  QisLedgerResponse,
  QisLedgerRow,
  QisVolRvParams,
} from '../../services/api_v1';

const ROLES = [
  { id: 'all', label: '全部' },
  { id: 'SHORT_LEG', label: 'Short Leg' },
  { id: 'LONG_LEG', label: 'Long Leg' },
  { id: 'HEDGE_FUT', label: 'Hedge Fut' },
] as const;

function money(v: number | null | undefined) {
  if (v == null || !Number.isFinite(v)) return '—';
  const abs = Math.abs(v);
  const body = abs >= 1e6 ? `${(v / 1e6).toFixed(2)}m` : abs >= 1e4 ? `${(v / 1e4).toFixed(1)}万` : v.toFixed(0);
  return body;
}

function num(v: number | null | undefined, digits = 2) {
  if (v == null || !Number.isFinite(v)) return '—';
  return v.toFixed(digits);
}

function pnlClass(v: number | null | undefined) {
  if (v == null || !Number.isFinite(v) || Math.abs(v) < 1e-8) return 'text-slate-500';
  return v > 0 ? 'text-emerald-700' : 'text-rose-700';
}

function badges(actions: string[]) {
  const out: string[] = [];
  if (actions.includes('EXPIRY_SETTLE')) out.push('EXPIRY');
  if (actions.includes('CLOSE_ROLL') || actions.includes('FUTURES_ROLL')) out.push('ROLL');
  if (actions.includes('OPEN_NEW_VINTAGE')) out.push('NEW');
  return out;
}

function csvEscape(v: unknown) {
  const s = v == null ? '' : String(v);
  if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

function downloadCsv(name: string, rows: QisLedgerRow[]) {
  const cols = [
    'ticker', 'leg_role', 'prev_pos', 'prev_close', 'trade_qty', 'avg_trade_price',
    'curr_pos', 'close_price', 'holding_pnl', 'trading_pnl', 'total_pnl',
    'eod_delta', 'eod_gamma', 'eod_vega', 'eod_theta',
  ];
  const lines = [cols.join(',')].concat(
    rows.map((r) => cols.map((c) => csvEscape((r as unknown as Record<string, unknown>)[c])).join(',')),
  );
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

function LedgerTable({
  rows,
  blotter,
  expanded,
  onToggle,
  onOpen,
  aggregate,
}: {
  rows: QisLedgerRow[];
  blotter: QisBlotterRow[];
  expanded: string | null;
  onToggle: (ticker: string) => void;
  onOpen: (ticker: string) => void;
  aggregate: boolean;
}) {
  const byTicker = useMemo(() => {
    const m = new Map<string, QisBlotterRow[]>();
    for (const b of blotter) {
      const list = m.get(b.ticker) || [];
      list.push(b);
      m.set(b.ticker, list);
    }
    return m;
  }, [blotter]);

  return (
    <table className="w-full text-[11px]">
      <thead>
        <tr className="text-left text-slate-500 border-b border-slate-200">
          <th className="py-1.5 pr-2 font-bold">合约</th>
          <th className="py-1.5 pr-2 font-bold">角色</th>
          {!aggregate && <th className="py-1.5 pr-2 font-bold text-right">昨仓</th>}
          {!aggregate && <th className="py-1.5 pr-2 font-bold text-right">昨收</th>}
          <th className="py-1.5 pr-2 font-bold text-right">{aggregate ? '有交易天数' : '今日成交'}</th>
          {!aggregate && <th className="py-1.5 pr-2 font-bold text-right">成交均价</th>}
          <th className="py-1.5 pr-2 font-bold text-right">{aggregate ? '期末仓' : '今仓'}</th>
          <th className="py-1.5 pr-2 font-bold text-right">今收/结算</th>
          <th className="py-1.5 pr-2 font-bold text-right">持有盈亏</th>
          <th className="py-1.5 pr-2 font-bold text-right">交易盈亏</th>
          <th className="py-1.5 pr-2 font-bold text-right">总盈亏</th>
          {!aggregate && <th className="py-1.5 font-bold text-right">Δ / Γ / ν / Θ</th>}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const fills = byTicker.get(r.ticker) || [];
          const tags = badges(fills.map((b) => b.action_type));
          const open = expanded === r.ticker;
          return (
            <React.Fragment key={r.ticker}>
              <tr
                className="border-b border-slate-100 text-[#000066] cursor-pointer hover:bg-slate-50"
                onClick={() => onToggle(r.ticker)}
              >
                <td className="py-1.5 pr-2 font-mono">
                  <button
                    type="button"
                    className="underline decoration-slate-300 hover:decoration-[#000066]"
                    onClick={(e) => {
                      e.stopPropagation();
                      onOpen(r.ticker);
                    }}
                  >
                    {r.ticker}
                  </button>
                  {tags.map((t) => (
                    <span key={t} className="ml-1 px-1 py-0.5 text-[9px] font-bold rounded bg-slate-100 text-slate-600">
                      {t}
                    </span>
                  ))}
                </td>
                <td className="py-1.5 pr-2">{r.leg_role || '—'}</td>
                {!aggregate && <td className="py-1.5 pr-2 text-right tabular-nums">{num(r.prev_pos, 2)}</td>}
                {!aggregate && <td className="py-1.5 pr-2 text-right tabular-nums">{num(r.prev_close, 2)}</td>}
                <td className="py-1.5 pr-2 text-right tabular-nums">
                  {aggregate ? num(r.trade_days, 0) : num(r.trade_qty, 2)}
                </td>
                {!aggregate && <td className="py-1.5 pr-2 text-right tabular-nums">{num(r.avg_trade_price, 2)}</td>}
                <td className="py-1.5 pr-2 text-right tabular-nums">{num(r.curr_pos, 2)}</td>
                <td className="py-1.5 pr-2 text-right tabular-nums">{num(r.close_price, 2)}</td>
                <td className={`py-1.5 pr-2 text-right tabular-nums ${pnlClass(r.holding_pnl)}`}>{money(r.holding_pnl)}</td>
                <td className={`py-1.5 pr-2 text-right tabular-nums ${pnlClass(r.trading_pnl)}`}>{money(r.trading_pnl)}</td>
                <td className={`py-1.5 pr-2 text-right tabular-nums font-bold ${pnlClass(r.total_pnl)}`}>{money(r.total_pnl)}</td>
                {!aggregate && (
                  <td className="py-1.5 text-right tabular-nums text-slate-600">
                    {num(r.eod_delta, 1)} / {num(r.eod_gamma, 2)} / {num(r.eod_vega, 1)} / {num(r.eod_theta, 1)}
                  </td>
                )}
              </tr>
              {open && fills.length > 0 && (
                <tr className="bg-slate-50">
                  <td colSpan={aggregate ? 8 : 12} className="px-2 py-2">
                    <table className="w-full text-[10px]">
                      <thead>
                        <tr className="text-slate-400">
                          <th className="text-left font-bold pr-2">动作</th>
                          <th className="text-left font-bold pr-2">切片</th>
                          <th className="text-right font-bold pr-2">手数</th>
                          <th className="text-right font-bold pr-2">价格</th>
                          <th className="text-right font-bold">名义</th>
                        </tr>
                      </thead>
                      <tbody>
                        {fills.map((b, i) => (
                          <tr key={`${b.action_type}-${i}`}>
                            <td className="pr-2 font-mono">{b.action_type}</td>
                            <td className="pr-2 text-slate-500">{b.vintage_id || '—'}</td>
                            <td className="pr-2 text-right tabular-nums">{num(b.qty, 2)}</td>
                            <td className="pr-2 text-right tabular-nums">{num(b.trade_price, 2)}</td>
                            <td className="text-right tabular-nums">{money(b.notional)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </td>
                </tr>
              )}
            </React.Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

const QisLedgerPanel: React.FC<{
  params: QisVolRvParams;
  chartDate: string | null;
}> = ({ params, chartDate }) => {
  const [view, setView] = useState<'day' | 'aggregate'>('day');
  const [tradeDate, setTradeDate] = useState<string | null>(null);
  const [role, setRole] = useState<(typeof ROLES)[number]['id']>('all');
  const [onlyTrades, setOnlyTrades] = useState(false);
  const [data, setData] = useState<QisLedgerResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [drawer, setDrawer] = useState(false);
  const [focus, setFocus] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    if (!chartDate) return;
    setView('day');
    setTradeDate(chartDate);
    setDrawer(true);
  }, [chartDate]);

  const paramKey = JSON.stringify({
    universe: params.universe,
    tilt: params.tilt,
    target_otm_delta: params.target_otm_delta,
    long_delta_threshold: params.long_delta_threshold,
    hedger_mode: params.hedger_mode,
    option_fee_pct: params.option_fee_pct,
    option_slippage_pct: params.option_slippage_pct,
    futures_fee_pct: params.futures_fee_pct,
    futures_slippage_pct: params.futures_slippage_pct,
    lambda_tilt: params.lambda_tilt,
    l_min: params.l_min,
    l_max: params.l_max,
  });

  useEffect(() => {
    setTradeDate(null);
  }, [paramKey]);

  useEffect(() => {
    const ac = new AbortController();
    setLoading(true);
    setErr(null);
    fetchQisLedger(
      {
        ...params,
        view,
        trade_date: view === 'day' ? tradeDate : null,
      },
      ac.signal,
    )
      .then((res) => {
        setData(res);
        if (view === 'day' && res.trade_date && res.trade_date !== tradeDate) {
          setTradeDate(res.trade_date);
        }
      })
      .catch((e: unknown) => {
        if (ac.signal.aborted) return;
        setErr(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!ac.signal.aborted) setLoading(false);
      });
    return () => ac.abort();
    // tradeDate is read; setting it from the response must not refetch the same date.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paramKey, view, tradeDate]);

  const dates = data?.dates || [];
  const idx = tradeDate ? dates.indexOf(tradeDate) : dates.length - 1;
  const filtered = useMemo(() => {
    const rows = data?.rows || [];
    return rows.filter((r) => {
      if (role !== 'all' && r.leg_role !== role) return false;
      if (!onlyTrades) return true;
      if (view === 'aggregate') return Math.abs(r.trade_days || 0) > 0;
      return Math.abs(r.trade_qty || 0) > 1e-12;
    });
  }, [data, role, onlyTrades, view]);

  const openTicker = (ticker: string) => {
    setFocus(ticker);
    setExpanded(ticker);
    setDrawer(true);
  };

  const summary = data?.summary;
  const ratio = summary?.margin_to_nav_ratio;

  const toolbar = (
    <div className="flex flex-wrap items-center gap-2 mb-3">
      <div className="flex rounded-md border border-slate-200 overflow-hidden">
        <button
          type="button"
          className={`px-2 py-1 text-[10px] font-bold ${view === 'day' ? 'bg-[#000066] text-white' : 'bg-white text-slate-600'}`}
          onClick={() => setView('day')}
        >
          单日
        </button>
        <button
          type="button"
          className={`px-2 py-1 text-[10px] font-bold ${view === 'aggregate' ? 'bg-[#000066] text-white' : 'bg-white text-slate-600'}`}
          onClick={() => setView('aggregate')}
        >
          全周期按合约
        </button>
      </div>
      {view === 'day' && (
        <div className="flex items-center gap-1">
          <button
            type="button"
            className="px-2 py-1 text-[10px] font-bold border border-slate-200 rounded disabled:opacity-40"
            disabled={idx <= 0}
            onClick={() => idx > 0 && setTradeDate(dates[idx - 1])}
          >
            &lt; 前一日
          </button>
          <input
            type="date"
            className="text-[11px] border border-slate-200 rounded px-1 py-1"
            value={tradeDate || ''}
            onChange={(e) => e.target.value && setTradeDate(e.target.value)}
          />
          <button
            type="button"
            className="px-2 py-1 text-[10px] font-bold border border-slate-200 rounded disabled:opacity-40"
            disabled={idx < 0 || idx >= dates.length - 1}
            onClick={() => idx >= 0 && idx < dates.length - 1 && setTradeDate(dates[idx + 1])}
          >
            后一日 &gt;
          </button>
        </div>
      )}
      <div className="flex rounded-md border border-slate-200 overflow-hidden">
        {ROLES.map((r) => (
          <button
            key={r.id}
            type="button"
            className={`px-2 py-1 text-[10px] font-bold ${role === r.id ? 'bg-slate-800 text-white' : 'bg-white text-slate-600'}`}
            onClick={() => setRole(r.id)}
          >
            {r.label}
          </button>
        ))}
      </div>
      <label className="text-[10px] text-slate-600 flex items-center gap-1">
        <input type="checkbox" checked={onlyTrades} onChange={(e) => setOnlyTrades(e.target.checked)} />
        只看有交易
      </label>
      <button
        type="button"
        className="ml-auto inline-flex items-center gap-1 px-2 py-1 text-[10px] font-bold border border-slate-200 rounded"
        onClick={() => downloadCsv(`vol_rv_ledger_${view}_${tradeDate || 'all'}.csv`, filtered)}
      >
        <Download size={12} /> CSV
      </button>
    </div>
  );

  const strip = summary && (
    <div className="grid grid-cols-2 md:grid-cols-6 gap-2 mb-3 text-[10px]">
      <div className="rounded border border-slate-200 px-2 py-1.5">
        <div className="text-slate-400 font-bold">Total PnL</div>
        <div className={`font-bold tabular-nums ${pnlClass(summary.total_pnl)}`}>{money(summary.total_pnl)}</div>
        <div className="text-slate-400">持有 {money(summary.holding_pnl)} + 交易 {money(summary.trading_pnl)}</div>
      </div>
      <div className="rounded border border-slate-200 px-2 py-1.5">
        <div className="text-slate-400 font-bold">{view === 'aggregate' ? '有交易的合约日' : '成交笔数'}</div>
        <div className="font-bold tabular-nums text-[#000066]">{summary.n_trades}</div>
      </div>
      <div className="rounded border border-slate-200 px-2 py-1.5">
        <div className="text-slate-400 font-bold">收盘净 Greeks</div>
        <div className="tabular-nums text-[#000066]">
          Δ {num(summary.eod_delta, 1)} · Γ {num(summary.eod_gamma, 2)}
        </div>
        <div className="tabular-nums text-slate-500">ν {num(summary.eod_vega, 1)} · Θ {num(summary.eod_theta, 1)}</div>
      </div>
      <div className="rounded border border-slate-200 px-2 py-1.5">
        <div className="text-slate-400 font-bold">保证金 / NAV</div>
        <div className="font-bold tabular-nums text-[#000066]">
          {ratio == null || !Number.isFinite(ratio) ? '—' : `${(ratio * 100).toFixed(2)}%`}
        </div>
        <div className="text-slate-400">{summary.margin_note || '费用未计入台账'}</div>
      </div>
    </div>
  );

  return (
    <div className="bg-white rounded-lg border border-slate-200 p-4">
      <div className="flex items-center gap-2 mb-2">
        <h3 className="font-bold text-[#000066] text-sm">持仓盈亏台账</h3>
        <button
          type="button"
          className="px-2 py-1 text-[10px] font-bold rounded border border-[#000066] text-[#000066]"
          onClick={() => setDrawer(true)}
        >
          打开完整交易台账
        </button>
        {loading && <span className="text-[10px] text-slate-400">加载中…</span>}
      </div>
      {err && <div className="text-[11px] text-rose-700 mb-2">{err}</div>}
      {toolbar}
      {strip}
      <div className="overflow-x-auto">
        <LedgerTable
          rows={filtered}
          blotter={data?.blotter || []}
          expanded={expanded}
          onToggle={(t) => setExpanded((cur) => (cur === t ? null : t))}
          onOpen={openTicker}
          aggregate={view === 'aggregate'}
        />
        {!loading && filtered.length === 0 && (
          <p className="text-[11px] text-slate-400 py-3">这一天没有持仓。</p>
        )}
      </div>
      <p className="text-[9px] text-slate-400 mt-2">
        按 (日期, 合约) 记账。持有盈亏是昨仓盯市，交易盈亏是当日成交相对收盘。费用和滑点不在这张表里。股指期权保证金按中金所卖方公式估算，商品期权不套用。
      </p>

      {drawer && (
        <div className="fixed inset-0 z-40 flex justify-end bg-slate-900/30" onClick={() => setDrawer(false)}>
          <div
            className="h-full w-full max-w-5xl bg-white shadow-xl overflow-y-auto p-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-2">
              <h3 className="font-bold text-[#000066] text-sm">
                交易台账 {focus ? `· ${focus}` : ''}
              </h3>
              {focus && (
                <button type="button" className="text-[11px] font-bold text-[#000066]" onClick={() => setFocus(null)}>
                  全部合约
                </button>
              )}
              <button type="button" className="text-[11px] font-bold text-slate-500" onClick={() => setDrawer(false)}>
                关闭
              </button>
            </div>
            {toolbar}
            {strip}
            <div className="overflow-x-auto">
              <LedgerTable
                rows={focus ? filtered.filter((r) => r.ticker === focus) : filtered}
                blotter={data?.blotter || []}
                expanded={expanded}
                onToggle={(t) => setExpanded((cur) => (cur === t ? null : t))}
                onOpen={openTicker}
                aggregate={view === 'aggregate'}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default QisLedgerPanel;
