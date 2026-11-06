import React, { useEffect, useMemo, useState } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  ReferenceLine,
} from 'recharts';
import { Layers, RefreshCw } from 'lucide-react';
import { fetchQisBacktest, fetchQisStrategies, QisBacktestResponse, QisStrategyInfo } from '../../services/api_v1';

const WINDOW_LABELS: { key: keyof QisBacktestResponse['metrics']; label: string }[] = [
  { key: '1y', label: '1 Year' },
  { key: '3y', label: '3 Year' },
  { key: '5y', label: '5 Year' },
  { key: 'max', label: 'Since inception' },
];

const DECOMP_COLORS = {
  vega: '#7C3AED',
  theta: '#16A34A',
  gamma: '#DC2626',
  basis: '#EA580C',
  net_delta: '#64748B',
  residual: '#94A3B8',
};

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

const QisView: React.FC = () => {
  const [strategies, setStrategies] = useState<QisStrategyInfo[]>([]);
  const [strategyId, setStrategyId] = useState('cross_calendar_carry');
  const [data, setData] = useState<QisBacktestResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadStatus, setLoadStatus] = useState('Running backtest…');
  const [err, setErr] = useState<string | null>(null);

  const load = (refresh = false, signal?: AbortSignal) => {
    setLoading(true);
    setErr(null);
    setLoadStatus(refresh ? '刷新回测…' : 'Running backtest…');
    fetchQisBacktest(strategyId, refresh, {
      signal,
      onRetry: ({ attempt, maxAttempts, delayMs }) => {
        setLoadStatus(
          `服务冷启动/繁忙，继续等待（${attempt}/${maxAttempts}），约 ${Math.ceil(delayMs / 1000)}s 后重试…`
        );
      },
    })
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
            { id: 'cross_calendar_carry', name: 'Cross Calendar Carry', status: 'live' },
            {
              id: 'equity_3_6_calendar_vol_300',
              name: 'Equity 3-6 Calendar Volatility Long Short Strategy — 300',
              status: 'live',
            },
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
  }, [strategyId]);

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

  const zChart = useMemo(() => {
    const s = data?.signal;
    if (!s?.dates?.length) return [];
    return s.dates.map((dt, i) => ({
      d: dt,
      z: s.zscore[i],
      spread_bp: (s.spread[i] ?? 0) * 10000,
      position: s.position[i],
    }));
  }, [data]);

  const sosLabel =
    data?.strategy_id === 'equity_3_6_calendar_vol_300' ? 'IV spread (vol pts)' : 'SoS (pts)';

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold text-[#000066] flex items-center gap-2">
            <Layers size={18} className="text-[#EAB308]" />
            Systematic Trading Strategies
          </h2>
          <p className="text-[11px] text-slate-500 max-w-3xl mt-1">
            QIS 回测框架：期货 carry + Equity 3-6 Calendar Vol（50 / 300 / 1000）。
            日历波动策略结果预计算写入 <code className="font-mono">qis_results.sqlite</code>，打开即读缓存；
            Refresh 可强制重算。Z 默认 252 交易日（仅用历史窗口，无前瞻）。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            className="px-3 py-1.5 text-xs font-bold border border-slate-200 rounded-md bg-slate-50 text-[#000066] max-w-[320px]"
            value={strategyId}
            onChange={(e) => setStrategyId(e.target.value)}
          >
            {(strategies.length
              ? strategies
              : [{ id: 'cross_calendar_carry', name: 'Cross Calendar Carry' }]
            ).map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => load(true)}
            className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-bold rounded-md bg-[#000066] text-white"
          >
            <RefreshCw size={12} /> Refresh
          </button>
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
                {typeof data.params.z_window === 'number' ? ` · Z=${data.params.z_window}d` : ''}
              </div>
            </div>
            <p className="text-[11px] text-slate-600 leading-relaxed">{data.description}</p>
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
            <div className="h-[340px]">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={data.nav}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                  <XAxis dataKey="d" tick={{ fontSize: 9 }} minTickGap={40} />
                  <YAxis yAxisId="nav" tick={{ fontSize: 10 }} domain={['auto', 'auto']} />
                  <YAxis yAxisId="sos" orientation="right" tick={{ fontSize: 9 }} domain={['auto', 'auto']} />
                  <Tooltip contentStyle={{ fontSize: 11 }} />
                  <Legend wrapperStyle={{ fontSize: 10 }} />
                  <Line
                    yAxisId="nav"
                    type="monotone"
                    dataKey="nav"
                    name="NAV multiple"
                    stroke="#000066"
                    dot={false}
                    strokeWidth={2}
                  />
                  <Line
                    yAxisId="sos"
                    type="monotone"
                    dataKey="sos"
                    name={sosLabel}
                    stroke="#EAB308"
                    dot={false}
                    strokeWidth={1}
                    strokeOpacity={0.7}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
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
                <div className="h-[320px]">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={decompChart}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                      <XAxis dataKey="d" tick={{ fontSize: 9 }} minTickGap={40} />
                      <YAxis tick={{ fontSize: 10 }} domain={['auto', 'auto']} />
                      <Tooltip
                        contentStyle={{ fontSize: 11 }}
                        formatter={(v: number) => fmtMoney(v)}
                      />
                      <Legend wrapperStyle={{ fontSize: 10 }} />
                      <Line type="monotone" dataKey="vega" name="Vega" stroke={DECOMP_COLORS.vega} dot={false} strokeWidth={1.5} />
                      <Line type="monotone" dataKey="theta" name="Theta" stroke={DECOMP_COLORS.theta} dot={false} strokeWidth={1.5} />
                      <Line type="monotone" dataKey="gamma" name="Gamma" stroke={DECOMP_COLORS.gamma} dot={false} strokeWidth={1.5} />
                      <Line type="monotone" dataKey="basis" name="Basis" stroke={DECOMP_COLORS.basis} dot={false} strokeWidth={1.5} />
                      <Line type="monotone" dataKey="net_delta" name="Net Delta" stroke={DECOMP_COLORS.net_delta} dot={false} strokeWidth={1.5} />
                      <Line type="monotone" dataKey="residual" name="Residual" stroke={DECOMP_COLORS.residual} dot={false} strokeWidth={1} strokeDasharray="4 3" />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>

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
                        <td className="py-2 pr-3 tabular-nums">{data.pnl_contribution.net_delta_pct == null ? '—' : `${data.pnl_contribution.net_delta_pct.toFixed(1)}%`}</td>
                        <td className="py-2 tabular-nums text-slate-500">
                          {data.pnl_contribution.residual_pct == null ? '—' : `${data.pnl_contribution.residual_pct.toFixed(1)}%`}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              )}

              {data.signal && zChart.length > 0 && (
                <div className="bg-white rounded-lg border border-slate-200 p-4">
                  <h3 className="font-bold text-[#000066] text-sm mb-3">3M−6M Spread Z-Score Signal</h3>
                  <div className="h-[280px]">
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={zChart}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                        <XAxis dataKey="d" tick={{ fontSize: 9 }} minTickGap={40} />
                        <YAxis tick={{ fontSize: 10 }} domain={['auto', 'auto']} />
                        <Tooltip contentStyle={{ fontSize: 11 }} />
                        <Legend wrapperStyle={{ fontSize: 10 }} />
                        <ReferenceLine y={data.signal.enter_band} stroke="#DC2626" strokeDasharray="4 4" />
                        <ReferenceLine y={-data.signal.enter_band} stroke="#DC2626" strokeDasharray="4 4" />
                        <ReferenceLine y={data.signal.exit_band} stroke="#16A34A" strokeDasharray="2 4" />
                        <ReferenceLine y={-data.signal.exit_band} stroke="#16A34A" strokeDasharray="2 4" />
                        <ReferenceLine y={0} stroke="#cbd5e1" />
                        <Line type="monotone" dataKey="z" name="Z-score" stroke="#000066" dot={false} strokeWidth={1.5} connectNulls />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                  <p className="text-[9px] text-slate-400 mt-1">
                    红虚线 ±{data.signal.enter_band} 入场；绿虚线 ±{data.signal.exit_band} 出场。Reverse 最长持仓 20 交易日。
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
