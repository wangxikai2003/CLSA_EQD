#!/usr/bin/env python3
"""Local QIS-like board for Equity Calendar Vol signal_mode=regime (CSI300 3M–6M).

Runs the production engine, writes a static HTML (Plotly) mirroring the online QIS tab
(NAV, metrics windows, greek decomp, daily greeks, spread/garch_z, trade markers),
then opens it in the browser. No Cloud Run deploy.
"""
from __future__ import annotations

import json
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from engine.calendar_vol_strategy import CalendarVolBacktestEngine  # noqa: E402

EXPORT_DIR = ROOT / "data" / "exports"
# Prefer sibling quant_research/viz under CITICS CLSA; fall back under vol_trading.
VIZ_CANDIDATES = [
    ROOT.parent.parent / "quant_research" / "viz",  # .../CITICS CLSA/quant_research/viz
    ROOT.parent / "quant_research" / "viz",  # .../vol_trading/quant_research/viz
]
CHART = {
    "nav": "#EAB308",
    "spread": "#EAB308",
    "long": "#DC2626",
    "short": "#16A34A",
    "close": "#2563EB",
    "enter": "#7C3AED",
    "exit": "#64748B",
    "vega": "#7C3AED",
    "theta": "#0F766E",
    "gamma": "#C2410C",
    "fade": "#0EA5E9",
    "momentum": "#F97316",
}


def _viz_dir() -> Path:
    for p in VIZ_CANDIDATES:
        p.mkdir(parents=True, exist_ok=True)
        return p
    p = VIZ_CANDIDATES[-1]
    p.mkdir(parents=True, exist_ok=True)
    return p


def _opt(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:  # NaN
        return None
    return x


def _downsample(dates: List[str], *series: List[Any], max_pts: int = 900) -> Dict[str, List[Any]]:
    n = len(dates)
    if n <= max_pts:
        idx = list(range(n))
    else:
        step = max(1, n // max_pts)
        idx = list(range(0, n, step))
        if idx[-1] != n - 1:
            idx.append(n - 1)
    out: Dict[str, List[Any]] = {"dates": [dates[i] for i in idx]}
    names = ["s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10", "s11"]
    for k, ser in enumerate(series):
        key = names[k]
        out[key] = [ser[i] if i < len(ser) else None for i in idx]
    return out


def pack_board(payload: Dict[str, Any], daily: Any) -> Dict[str, Any]:
    """Pack engine payload + optional full daily frame into a board JSON blob."""
    sig = payload["signal"]
    scale = float(sig.get("level_scale") or 100.0)
    nav = payload["nav"]
    decomp = payload.get("pnl_decomposition") or {}
    daily_g = payload.get("pnl_daily_greeks") or {}
    contrib = payload.get("pnl_contribution") or {}
    metrics = payload.get("metrics") or {}

    windows = []
    for key, label in (
        ("1y", "1 Year"),
        ("3y", "3 Year"),
        ("5y", "5 Year"),
        ("max", "Since inception"),
    ):
        m = metrics.get(key) or {}
        windows.append(
            {
                "key": key,
                "label": label,
                "ann_return": m.get("ann_return"),
                "ann_vol": m.get("ann_vol"),
                "sharpe": m.get("sharpe"),
                "mdd": m.get("mdd"),
                "mdd_vol": m.get("mdd_vol"),
                "pct_positive_months": m.get("pct_positive_months"),
                "n_days": m.get("n_days"),
                "start": m.get("start"),
                "end": m.get("end"),
            }
        )

    # Prefer full daily rows when available (better charts).
    if daily is not None and len(daily) > 0:
        dates = [str(x)[:10] for x in daily["d"].astype(str).tolist()]
        nav_mult = (daily["nav"].astype(float) / float(payload["params"]["initial_nav"])).tolist()
        spread = [_opt(x) for x in daily["spread"].tolist()]
        spread_iv = [_opt(x) for x in daily.get("spread_iv", daily["spread"]).tolist()]
        garch_z = [_opt(x) for x in daily.get("garch_z", [None] * len(dates)).tolist()]
        enter_hi = [_opt(x) for x in daily.get("enter_hi", [None] * len(dates)).tolist()]
        enter_lo = [_opt(x) for x in daily.get("enter_lo", [None] * len(dates)).tolist()]
        exit_hi = [_opt(x) for x in daily.get("exit_hi", [None] * len(dates)).tolist()]
        exit_lo = [_opt(x) for x in daily.get("exit_lo", [None] * len(dates)).tolist()]
        pos = [int(x) for x in daily["position"].tolist()]
        regime = [
            (None if (x is None or (isinstance(x, float) and x != x)) else str(x))
            for x in daily.get("regime_mode", [None] * len(dates)).tolist()
        ]
        vega_d = [_opt(x) or 0.0 for x in daily["vega_pnl"].tolist()]
        gamma_d = [_opt(x) or 0.0 for x in daily["gamma_pnl"].tolist()]
        theta_d = [_opt(x) or 0.0 for x in daily["theta_pnl"].tolist()]
        cum_vega = daily["cum_vega"].astype(float).tolist()
        cum_theta = daily["cum_theta"].astype(float).tolist()
        cum_gamma = daily["cum_gamma"].astype(float).tolist()
        cum_basis = daily["cum_basis"].astype(float).tolist()
        cum_delta = daily["cum_delta"].astype(float).tolist()
        cum_resid = daily["cum_resid"].astype(float).tolist()

        markers = []
        for i in range(1, len(pos)):
            if pos[i] == pos[i - 1]:
                continue
            kind = "long" if pos[i] == 1 else ("short" if pos[i] == -1 else "close")
            sp = spread[i]
            markers.append(
                {
                    "d": dates[i],
                    "kind": kind,
                    "sos": None if sp is None else round(sp * scale, 4),
                    "regime_mode": regime[i],
                }
            )
    else:
        dates = list(sig["dates"])
        # Align nav onto signal dates via lookup
        nav_by_d = {r["d"]: r["nav"] for r in nav}
        sos_by_d = {r["d"]: r.get("sos") for r in nav}
        nav_mult = [nav_by_d.get(d) for d in dates]
        spread = [_opt(x) for x in sig["spread"]]
        spread_iv = [_opt(x) for x in (sig.get("spread_iv") or sig["spread"])]
        garch_z = [_opt(x) for x in (sig.get("garch_z") or [])]
        if len(garch_z) < len(dates):
            garch_z = garch_z + [None] * (len(dates) - len(garch_z))
        enter_hi = [_opt(x) for x in (sig.get("enter_hi") or [])]
        enter_lo = [_opt(x) for x in (sig.get("enter_lo") or [])]
        exit_hi = [_opt(x) for x in (sig.get("exit_hi") or [])]
        exit_lo = [_opt(x) for x in (sig.get("exit_lo") or [])]
        pos = [int(x) for x in sig["position"]]
        regime = list(sig.get("regime_mode") or [None] * len(dates))
        markers = list(sig.get("trade_markers") or [])
        # fill sos from nav chart when missing
        for m in markers:
            if m.get("sos") is None:
                m["sos"] = sos_by_d.get(m["d"])
        vega_d = list(daily_g.get("vega_pnl") or [])
        gamma_d = list(daily_g.get("gamma_pnl") or [])
        theta_d = list(daily_g.get("theta_pnl") or [])
        cum_vega = list(decomp.get("vega_pnl") or [])
        cum_theta = list(decomp.get("theta_pnl") or [])
        cum_gamma = list(decomp.get("gamma_pnl") or [])
        cum_basis = list(decomp.get("basis_pnl") or [])
        cum_delta = list(decomp.get("net_delta_pnl") or [])
        cum_resid = list(decomp.get("residual_pnl") or [])
        decomp_dates = list(decomp.get("dates") or dates)
        daily_dates = list(daily_g.get("dates") or dates)

    # Build chart packs (downsample heavy series)
    def _scaled(xs: List[Optional[float]]) -> List[Optional[float]]:
        out: List[Optional[float]] = []
        for x in xs:
            out.append(None if x is None else x * scale)
        # pad / trim to dates length
        if len(out) < len(dates):
            out.extend([None] * (len(dates) - len(out)))
        return out[: len(dates)]

    gz_series = garch_z if garch_z else [None] * len(dates)
    if len(gz_series) < len(dates):
        gz_series = list(gz_series) + [None] * (len(dates) - len(gz_series))

    nav_pack = _downsample(
        dates,
        nav_mult,
        _scaled(spread),
        _scaled(enter_hi if enter_hi else [None] * len(dates)),
        _scaled(enter_lo if enter_lo else [None] * len(dates)),
        _scaled(exit_hi if exit_hi else [None] * len(dates)),
        _scaled(exit_lo if exit_lo else [None] * len(dates)),
        gz_series[: len(dates)],
        regime if len(regime) >= len(dates) else list(regime) + [None] * (len(dates) - len(regime)),
    )

    if daily is not None and len(daily) > 0:
        decomp_pack = _downsample(
            dates, cum_vega, cum_theta, cum_gamma, cum_basis, cum_delta, cum_resid
        )
        daily_pack = _downsample(dates, vega_d, gamma_d, theta_d)
    else:
        decomp_pack = {
            "dates": decomp_dates,
            "s0": cum_vega,
            "s1": cum_theta,
            "s2": cum_gamma,
            "s3": cum_basis,
            "s4": cum_delta,
            "s5": cum_resid,
        }
        daily_pack = {
            "dates": daily_dates,
            "s0": vega_d,
            "s1": gamma_d,
            "s2": theta_d,
        }

    # Regime open counts
    n_fade = n_mom = 0
    for i in range(1, len(pos)):
        if pos[i - 1] == 0 and pos[i] != 0:
            if regime[i] == "fade":
                n_fade += 1
            elif regime[i] == "momentum":
                n_mom += 1

    return {
        "asof": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z"),
        "chart": CHART,
        "strategy_name": payload.get("strategy_name"),
        "description": payload.get("description"),
        "summary": payload["summary"],
        "params": payload["params"],
        "windows": windows,
        "contribution": contrib,
        "legs": payload.get("legs") or [],
        "markers": markers,
        "counts": {"opens_fade": n_fade, "opens_momentum": n_mom},
        "nav": {
            "dates": nav_pack["dates"],
            "nav": nav_pack["s0"],
            "spread_pts": nav_pack["s1"],
            "enter_hi": nav_pack["s2"],
            "enter_lo": nav_pack["s3"],
            "exit_hi": nav_pack["s4"],
            "exit_lo": nav_pack["s5"],
            "garch_z": nav_pack["s6"],
            "regime_mode": nav_pack["s7"],
        },
        "decomp": {
            "dates": decomp_pack["dates"],
            "vega": decomp_pack["s0"],
            "theta": decomp_pack["s1"],
            "gamma": decomp_pack["s2"],
            "basis": decomp_pack["s3"],
            "net_delta": decomp_pack["s4"],
            "residual": decomp_pack["s5"],
        },
        "daily_greeks": {
            "dates": daily_pack["dates"],
            "vega": daily_pack["s0"],
            "gamma": daily_pack["s1"],
            "theta": daily_pack["s2"],
        },
        "docs": {
            "data": (
                "Wind close 库：期权 mid←close；期货 close。"
                "执行：t−1 close 算信号 → t close 成交。CSI300 · 3M–6M · iv · both。"
                "初始 NAV ¥1亿；成本 v1 不计。"
            ),
            "signal": (
                "Regime：Momentum 在加速区（q75–q92 / q08–q25）且 garch_z 出 90/10 顺势；"
                "平仓含中轴衰竭 30–70、反向冲击 20/80、空间 1/99、最长 8 日。"
                "Fade：Spread 出 10/90 且当日/前日冲击收敛（z≤q80且z_{t−1}≤q80 / 对称）；"
                "平仓回 40–60 + 危机熔断；冷却 3 日。"
                "预算 Fade 1% / Momentum 0.7% NAV（远端美元 Vega）。"
            ),
        },
    }


def write_html(path: Path, board: Dict[str, Any]) -> None:
    blob = json.dumps(board, ensure_ascii=False)
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>QIS · Calendar Vol Regime (local)</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
  :root {{
    --navy:#000066; --yellow:#EAB308; --long:#DC2626; --short:#16A34A; --close:#2563EB;
    --bg:#f8fafc; --card:#fff; --border:#e2e8f0; --muted:#64748b; --text:#0f172a;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
    background:var(--bg); color:var(--text); }}
  .top {{ background:#fff; border-bottom:1px solid var(--border); padding:12px 20px;
    display:flex; flex-wrap:wrap; gap:10px; align-items:center; justify-content:space-between; }}
  .brand {{ font-weight:800; color:var(--navy); font-size:15px; }}
  .pill {{ font-size:11px; font-weight:700; padding:3px 8px; border-radius:999px;
    border:1px solid var(--border); background:#fff; color:var(--muted); }}
  .pill.warn {{ background:#fffbeb; border-color:#fde68a; color:#92400e; }}
  .pill.mode {{ background:#eff6ff; border-color:#bfdbfe; color:#1e3a8a; }}
  .wrap {{ max-width:1180px; margin:0 auto; padding:16px 20px 48px; }}
  .card {{ background:var(--card); border:1px solid var(--border); border-radius:10px; padding:16px; margin:12px 0; }}
  h3 {{ margin:0 0 8px; color:var(--navy); font-size:14px; }}
  .meta {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:10px; color:var(--muted); }}
  .docs {{ font-size:11px; color:#334155; line-height:1.55; border-top:1px solid #f1f5f9; margin-top:12px; padding-top:12px; }}
  .docs .h {{ font-size:10px; font-weight:800; text-transform:uppercase; letter-spacing:.04em; color:var(--navy); margin:8px 0 2px; }}
  .grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; margin-top:12px; }}
  @media (max-width:800px) {{ .grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
  .stat {{ background:#f8fafc; border:1px solid var(--border); border-radius:8px; padding:10px; }}
  .stat .k {{ font-size:10px; color:var(--muted); }} .stat .v {{ font-size:16px; font-weight:800; color:var(--navy); }}
  table {{ width:100%; border-collapse:collapse; font-size:11px; margin-top:8px; }}
  th, td {{ border-bottom:1px solid #f1f5f9; padding:6px 4px; text-align:right; }}
  th:first-child, td:first-child {{ text-align:left; }}
  th {{ color:var(--muted); font-weight:700; }}
  .legend {{ font-size:11px; color:var(--muted); margin-top:6px; }}
  .dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:4px; }}
  .legs {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }}
  .leg {{ font-size:10px; font-weight:700; padding:3px 8px; border-radius:6px; border:1px solid var(--border); }}
  .leg.pos {{ background:#ecfdf5; color:#065f46; border-color:#a7f3d0; }}
  .leg.neg {{ background:#fff1f2; color:#9f1239; border-color:#fecdd3; }}
  .leg.zero {{ background:#f8fafc; color:#475569; }}
</style>
</head>
<body>
  <div class="top">
    <div class="brand">QIS · Equity Calendar Vol <span style="font-weight:500;color:#64748b">（本地 Regime 看板）</span></div>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <span class="pill warn">未推送 · Local only</span>
      <span class="pill mode">signal_mode = regime</span>
      <span class="pill">300 · 3M–6M · iv · both</span>
      <span class="pill" id="asof"></span>
    </div>
  </div>
  <div class="wrap" id="main"></div>
<script>
const D = {blob};
const C = D.chart;
document.getElementById('asof').textContent = D.asof;

function pct(x, d=2) {{
  if (x==null || Number.isNaN(x)) return '—';
  return (x*100).toFixed(d) + '%';
}}
function num(x, d=3) {{
  if (x==null || Number.isNaN(x)) return '—';
  return Number(x).toFixed(d);
}}
function money(x) {{
  if (x==null || Number.isNaN(x)) return '—';
  return Number(x).toLocaleString('en-CN', {{maximumFractionDigits:0}});
}}

function markerSets(markers) {{
  const longX=[], longY=[], shortX=[], shortY=[], closeX=[], closeY=[];
  const fadeX=[], fadeY=[], momX=[], momY=[];
  for (const m of markers) {{
    const y = m.sos;
    if (y==null) continue;
    if (m.kind==='long') {{ longX.push(m.d); longY.push(y); }}
    else if (m.kind==='short') {{ shortX.push(m.d); shortY.push(y); }}
    else {{ closeX.push(m.d); closeY.push(y); }}
    if (m.kind!=='close' && m.regime_mode==='fade') {{ fadeX.push(m.d); fadeY.push(y); }}
    if (m.kind!=='close' && m.regime_mode==='momentum') {{ momX.push(m.d); momY.push(y); }}
  }}
  return {{longX,longY,shortX,shortY,closeX,closeY,fadeX,fadeY,momX,momY}};
}}

function paint() {{
  const s = D.summary;
  const p = D.params;
  const maxW = D.windows.find(w => w.key==='max') || {{}};
  const wins = D.windows.map(w => `<tr>
    <td>${{w.label}}<div class="meta">${{w.start && w.end ? (w.start+' → '+w.end) : ''}}</div></td>
    <td>${{pct(w.ann_return)}}</td>
    <td>${{pct(w.ann_vol)}}</td>
    <td><b>${{num(w.sharpe,3)}}</b></td>
    <td>${{pct(w.mdd)}}</td>
    <td>${{num(w.mdd_vol,2)}}</td>
    <td>${{w.pct_positive_months==null?'—':(Number(w.pct_positive_months)>1?Number(w.pct_positive_months).toFixed(1)+'%':pct(w.pct_positive_months,1))}}</td>
    <td>${{w.n_days ?? '—'}}</td>
  </tr>`).join('');
  const legs = (D.legs||[]).map(l => {{
    const cls = l.weight>0?'pos':(l.weight<0?'neg':'zero');
    return `<span class="leg ${{cls}}">${{l.product}} ${{l.tenor}} · ${{l.weight>0?'+':''}}${{l.weight}}</span>`;
  }}).join('');
  const c = D.contribution || {{}};
  document.getElementById('main').innerHTML = `
  <div class="card">
    <div style="display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px;align-items:baseline">
      <h3>${{D.strategy_name || 'Regime Calendar Vol'}}</h3>
      <div class="meta">${{s.start}} → ${{s.end}} · final ×${{num(s.final_nav_multiple,3)}} · first open ${{s.first_open||'—'}}</div>
    </div>
    <div class="docs">
      <div class="h">回测数据</div><p>${{D.docs.data}}</p>
      <div class="h">信号算子</div><p>${{D.docs.signal}}</p>
      <div class="h">仓位 / 参数</div>
      <p>signal_mode=<code>regime</code> · budget_fade=${{pct(p.budget_fade)}} · budget_momentum=${{pct(p.budget_momentum)}} ·
      z_window=${{p.z_window}} · ${{p.signal_lag}}</p>
    </div>
    <div class="legs">${{legs}}</div>
    <div class="grid">
      <div class="stat"><div class="k">Final ×</div><div class="v">×${{num(s.final_nav_multiple,4)}}</div></div>
      <div class="stat"><div class="k">Final NAV</div><div class="v">${{money(s.final_nav)}}</div></div>
      <div class="stat"><div class="k">Sharpe (max)</div><div class="v">${{num(maxW.sharpe,3)}}</div></div>
      <div class="stat"><div class="k">MDD (max)</div><div class="v">${{pct(maxW.mdd)}}</div></div>
      <div class="stat"><div class="k">Fade opens</div><div class="v">${{D.counts.opens_fade}}</div></div>
      <div class="stat"><div class="k">Momentum opens</div><div class="v">${{D.counts.opens_momentum}}</div></div>
      <div class="stat"><div class="k">Total PnL</div><div class="v">${{money(s.total_pnl)}}</div></div>
      <div class="stat"><div class="k">Days</div><div class="v">${{s.n_days}}</div></div>
    </div>
  </div>

  <div class="card">
    <h3>净值曲线 (NAV / initial)</h3>
    <div id="navChart" style="height:380px"></div>
    <div class="legend">
      <span class="dot" style="background:${{C.nav}}"></span>NAV×（左）· Spread pts（右）·
      <span class="dot" style="background:${{C.long}}"></span>Long ·
      <span class="dot" style="background:${{C.short}}"></span>Short ·
      <span class="dot" style="background:${{C.close}}"></span>Close ·
      <span class="dot" style="background:${{C.fade}}"></span>Fade 开仓 ·
      <span class="dot" style="background:${{C.momentum}}"></span>Momentum 开仓
    </div>
  </div>

  <div class="card">
    <h3>绩效指标</h3>
    <table>
      <thead><tr>
        <th>Window</th><th>Ann. Return</th><th>Ann. Vol</th><th>Sharpe</th>
        <th>MDD</th><th>MDD / Vol</th><th>% +Months</th><th>Days</th>
      </tr></thead>
      <tbody>${{wins}}</tbody>
    </table>
  </div>

  <div class="card">
    <h3>PnL Decomposition（累积）</h3>
    <div id="decompChart" style="height:320px"></div>
  </div>

  <div class="card">
    <h3>Daily Greeks PnL</h3>
    <div id="dailyChart" style="height:300px"></div>
    <div class="legend">Vega 左轴；Gamma / Theta 右轴（量级通常更小）。</div>
  </div>

  <div class="card">
    <h3>3M–6M Spread & Level Quantile Bands</h3>
    <div id="spreadChart" style="height:320px"></div>
    <div class="legend">黄线 Spread（vol pts）。紫虚线 10/90 入场；灰虚线 40/60 出场（Fade 平仓带）。</div>
  </div>

  <div class="card">
    <h3>GARCH-z（冲击层）</h3>
    <div id="zChart" style="height:280px"></div>
    <div class="legend">黄线 garch_z。Momentum 开仓看 90/10 极端冲击；持仓衰竭看 30–70 / 反向冲击 20–80。</div>
  </div>

  <div class="card">
    <h3>PnL Risk Contribution</h3>
    <table>
      <thead><tr>
        <th>Total PnL</th><th>Vega %</th><th>Theta %</th><th>Gamma %</th>
        <th>Basis %</th><th>Net Delta %</th><th>Residual %</th>
      </tr></thead>
      <tbody><tr>
        <td><b>${{money(c.total_pnl)}}</b></td>
        <td style="color:${{C.vega}}">${{c.vega_pct==null?'—':c.vega_pct.toFixed(1)+'%'}}</td>
        <td style="color:${{C.theta}}">${{c.theta_pct==null?'—':c.theta_pct.toFixed(1)+'%'}}</td>
        <td style="color:${{C.gamma}}">${{c.gamma_pct==null?'—':c.gamma_pct.toFixed(1)+'%'}}</td>
        <td>${{c.basis_pct==null?'—':c.basis_pct.toFixed(1)+'%'}}</td>
        <td>${{c.net_delta_pct==null?'—':c.net_delta_pct.toFixed(1)+'%'}}</td>
        <td>${{c.residual_pct==null?'—':c.residual_pct.toFixed(1)+'%'}}</td>
      </tr></tbody>
    </table>
  </div>
  `;

  const M = markerSets(D.markers);
  const N = D.nav;
  Plotly.newPlot('navChart', [
    {{x:N.dates, y:N.nav, name:'NAV ×', yaxis:'y', line:{{color:C.nav, width:2.4}}}},
    {{x:N.dates, y:N.spread_pts, name:'Spread pts', yaxis:'y2', line:{{color:C.spread, width:1.2, dash:'solid'}}, opacity:0.7}},
    {{x:M.longX, y:M.longY, name:'Long', yaxis:'y2', mode:'markers', marker:{{color:C.long, size:8}}}},
    {{x:M.shortX, y:M.shortY, name:'Short', yaxis:'y2', mode:'markers', marker:{{color:C.short, size:8}}}},
    {{x:M.closeX, y:M.closeY, name:'Close', yaxis:'y2', mode:'markers', marker:{{color:C.close, size:8}}}},
    {{x:M.fadeX, y:M.fadeY, name:'Fade open', yaxis:'y2', mode:'markers',
      marker:{{symbol:'diamond', color:C.fade, size:10, line:{{width:1, color:'#0369a1'}}}}}},
    {{x:M.momX, y:M.momY, name:'Momentum open', yaxis:'y2', mode:'markers',
      marker:{{symbol:'triangle-up', color:C.momentum, size:11, line:{{width:1, color:'#c2410c'}}}}}},
  ], {{
    margin:{{t:24,r:55,b:40,l:50}}, legend:{{orientation:'h', y:1.14}},
    paper_bgcolor:'#fff', plot_bgcolor:'#fff',
    xaxis:{{gridcolor:'#f1f5f9'}},
    yaxis:{{title:'NAV ×', gridcolor:'#f1f5f9', tickfont:{{color:C.nav}}}},
    yaxis2:{{title:'Spread (vol pts)', overlaying:'y', side:'right', gridcolor:'#f8fafc'}},
  }}, {{responsive:true, displayModeBar:true}});

  const Dc = D.decomp;
  Plotly.newPlot('decompChart', [
    {{x:Dc.dates, y:Dc.vega, name:'Vega', line:{{color:C.vega}}}},
    {{x:Dc.dates, y:Dc.theta, name:'Theta', line:{{color:C.theta}}}},
    {{x:Dc.dates, y:Dc.gamma, name:'Gamma', line:{{color:C.gamma}}}},
    {{x:Dc.dates, y:Dc.basis, name:'Basis', line:{{color:'#A16207'}}}},
    {{x:Dc.dates, y:Dc.net_delta, name:'Net Delta', line:{{color:'#64748B'}}}},
    {{x:Dc.dates, y:Dc.residual, name:'Residual', line:{{color:'#94A3B8', dash:'dot'}}}},
  ], {{
    margin:{{t:20,r:20,b:40,l:55}}, legend:{{orientation:'h', y:1.12}},
    paper_bgcolor:'#fff', plot_bgcolor:'#fff',
    xaxis:{{gridcolor:'#f1f5f9'}}, yaxis:{{title:'Cum PnL (¥)', gridcolor:'#f1f5f9'}},
  }}, {{responsive:true, displayModeBar:false}});

  const Dg = D.daily_greeks;
  Plotly.newPlot('dailyChart', [
    {{x:Dg.dates, y:Dg.vega, name:'Vega', type:'bar', marker:{{color:C.vega}}, yaxis:'y'}},
    {{x:Dg.dates, y:Dg.gamma, name:'Gamma', type:'bar', marker:{{color:C.gamma}}, yaxis:'y2'}},
    {{x:Dg.dates, y:Dg.theta, name:'Theta', type:'bar', marker:{{color:C.theta}}, yaxis:'y2'}},
  ], {{
    barmode:'relative', margin:{{t:20,r:50,b:40,l:55}}, legend:{{orientation:'h', y:1.12}},
    paper_bgcolor:'#fff', plot_bgcolor:'#fff',
    xaxis:{{gridcolor:'#f1f5f9'}},
    yaxis:{{title:'Vega ¥', gridcolor:'#f1f5f9'}},
    yaxis2:{{title:'Γ / Θ ¥', overlaying:'y', side:'right'}},
  }}, {{responsive:true, displayModeBar:false}});

  Plotly.newPlot('spreadChart', [
    {{x:N.dates, y:N.spread_pts, name:'Spread', line:{{color:C.spread, width:1.6}}}},
    {{x:N.dates, y:N.enter_hi, name:'Enter hi', line:{{color:C.enter, width:1, dash:'dot'}}}},
    {{x:N.dates, y:N.enter_lo, name:'Enter lo', line:{{color:C.enter, width:1, dash:'dot'}}}},
    {{x:N.dates, y:N.exit_hi, name:'Exit hi', line:{{color:C.exit, width:1, dash:'dash'}}}},
    {{x:N.dates, y:N.exit_lo, name:'Exit lo', line:{{color:C.exit, width:1, dash:'dash'}}}},
    {{x:M.longX, y:M.longY, name:'Long', mode:'markers', marker:{{color:C.long, size:7}}}},
    {{x:M.shortX, y:M.shortY, name:'Short', mode:'markers', marker:{{color:C.short, size:7}}}},
    {{x:M.closeX, y:M.closeY, name:'Close', mode:'markers', marker:{{color:C.close, size:7}}}},
  ], {{
    margin:{{t:20,r:20,b:40,l:50}}, legend:{{orientation:'h', y:1.12}},
    paper_bgcolor:'#fff', plot_bgcolor:'#fff',
    xaxis:{{gridcolor:'#f1f5f9'}}, yaxis:{{title:'vol pts', gridcolor:'#f1f5f9'}},
  }}, {{responsive:true, displayModeBar:false}});

  // garch_z markers on z scale: use garch_z at marker dates when available
  const zByD = {{}};
  N.dates.forEach((d,i) => {{ zByD[d] = N.garch_z[i]; }});
  const zLongX=[], zLongY=[], zShortX=[], zShortY=[], zCloseX=[], zCloseY=[];
  for (const m of D.markers) {{
    const z = zByD[m.d];
    if (z==null) continue;
    if (m.kind==='long') {{ zLongX.push(m.d); zLongY.push(z); }}
    else if (m.kind==='short') {{ zShortX.push(m.d); zShortY.push(z); }}
    else {{ zCloseX.push(m.d); zCloseY.push(z); }}
  }}
  Plotly.newPlot('zChart', [
    {{x:N.dates, y:N.garch_z, name:'garch_z', line:{{color:C.spread, width:1.4}}}},
    {{x:zLongX, y:zLongY, name:'Long', mode:'markers', marker:{{color:C.long, size:7}}}},
    {{x:zShortX, y:zShortY, name:'Short', mode:'markers', marker:{{color:C.short, size:7}}}},
    {{x:zCloseX, y:zCloseY, name:'Close', mode:'markers', marker:{{color:C.close, size:7}}}},
  ], {{
    margin:{{t:20,r:20,b:40,l:50}}, legend:{{orientation:'h', y:1.12}},
    paper_bgcolor:'#fff', plot_bgcolor:'#fff',
    xaxis:{{gridcolor:'#f1f5f9'}}, yaxis:{{title:'garch_z', gridcolor:'#f1f5f9'}},
  }}, {{responsive:true, displayModeBar:false}});
}}
paint();
</script>
</body></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


def main() -> int:
    import pandas as pd

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    viz = _viz_dir()

    print("Running regime backtest (CSI300 · 3M–6M · iv · both)…", flush=True)
    eng = CalendarVolBacktestEngine(
        universe="300",
        initial_nav=100_000_000.0,
        z_window=252,
        tenor_pair="3M-6M",
        side_mode="both",
        spread_mode="iv",
        signal_mode="regime",
    )
    rows_box: Dict[str, Any] = {}
    orig = eng._pack_payload

    def _pack(rows, first_open_i):  # type: ignore[no-untyped-def]
        rows_box["rows"] = rows
        return orig(rows, first_open_i)

    eng._pack_payload = _pack  # type: ignore[method-assign]
    payload = eng.run()
    daily = pd.DataFrame(rows_box["rows"])
    daily["d"] = daily["d"].astype(str)

    print(
        f"  ×{payload['summary']['final_nav_multiple']:.4f}  "
        f"sharpe={payload['metrics']['max']['sharpe']}  "
        f"{payload['summary']['start']}→{payload['summary']['end']}",
        flush=True,
    )

    board = pack_board(payload, daily)
    json_path = EXPORT_DIR / "calendar_vol_regime_qis_board.json"
    json_path.write_text(json.dumps(board, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path = viz / "calendar_vol_regime_qis_board.html"
    write_html(html_path, board)
    print(f"wrote {html_path}", flush=True)
    print(f"wrote {json_path}", flush=True)
    webbrowser.open(html_path.resolve().as_uri())
    print("opened in browser", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
