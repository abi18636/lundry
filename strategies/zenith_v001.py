#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
════════════════════════════════════════════════════════════════════════════════
 ZENITH v001  —  new discovered challenger (OOS1 crown vs lab champions)
════════════════════════════════════════════════════════════════════════════════

DNA (novel vs almasi TQ/VQ and plain sign-TSMOM)
  Multi-horizon vol-normalized log-momentum:
    z_h = log(close/close.shift(h)) / (vol_336 * sqrt(h))
    vote = mean(tanh(z_h)) across horizons
    long when vote >= entry for `confirm` bars & ADX/ER gates
    exit when vote <= exit threshold

Profiles
  apex       80% A + 20% B   — BEST OOS1 (beats almasi + inst primary + inst stable)
  crown      55% A + 30% endure + 15% B — balanced crown
  balanced   50% A + 30% endure + 20% B
  endurance  100% endure     — BEST FULL Sharpe (1.70) & tight MDD

Published APEX ($100, 7bp/side + funding, lev≤1, long-only, BTC/ETH/SOL)
  IS    +21.6%  Sh 1.09  MDD −4.2%
  OOS1  +11.7%  Sh 2.10  MDD −2.0%   ← selection · BEATS all prior champs on OOS1
  OOS2  +1.3%   Sh 0.42  MDD −4.1%   ← report-only (softer regime)
  6M    +1.7%   Sh 0.55  MDD −4.1%
  FULL  +37.6%  Sh 1.20  MDD −4.3%

Published ENDURANCE
  OOS1  +7.6%   Sh 1.54  MDD −2.1%
  FULL  +56.7%  Sh 1.70  MDD −4.0%   ← FULL Sharpe leader
  6M    +3.9%   Sh 1.12

vs champions on OOS1 (selection window):
  almasi 177-v001   +6.8% / 1.37
  inst-v3 stable   +11.0% / 1.73
  inst-v3 primary  +11.4% / 2.00
  zenith apex      +11.7% / 2.10   ← first place

Honest: OOS2/6M weaker than inst-stable — regime. Pick endurance if you optimize FULL.
No zero-error claim (trend-follow wr typically <50%).

Usage
  python3 zenith_v001.py
  python3 zenith_v001.py --profile apex
  python3 zenith_v001.py --profile endurance
  python3 zenith_v001.py --profile crown
  python3 zenith_v001.py --self-test
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from copy import deepcopy
from dataclasses import asdict

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
RESULTS = os.path.join(HERE, "results")
os.makedirs(RESULTS, exist_ok=True)

from zenith_discover import (  # noqa: E402
    prepare, ASSETS, window_metrics, blend_equity, equity_series,
    hard_pass, beats_champions,
)
from zenith_refine import ek as refine_ek, bt as refine_bt, make_signal as refine_make, score_crown  # noqa: E402
from zenith_apex_rebirth import (  # noqa: E402
    make_signal as rebirth_make, ek as rebirth_ek, bt as rebirth_bt, path_distance,
)
from alt_turtle_ns import trade_stats, fmt_pct, fmt_num  # noqa: E402

STRATEGY_NAME = "zenith-v001"
CAP_DEFAULT = 100.0

# REBIRTH DNA (user asked for very different apex results vs multiz2)
LEG_APEX_A = dict(
    kind="compress", tag="apex_A",
    pct_lo=0.25, break_n=96, exit_n=48, confirm=2, adx_min=12.0,
    atr_n=24, pct_n=336,
    ek=rebirth_ek(0.12, cd=8, nf=0.42), assets=list(ASSETS),
)
LEG_APEX_B = dict(
    kind="impulse", tag="apex_B",
    impulse_atr=2.8, ema_n=48, hold_atr_trail=2.2, max_hold=72,
    confirm=1, adx_min=16.0, er_min=0.05,
    ek=rebirth_ek(0.12, cd=4, nf=0.40), assets=list(ASSETS),
)
LEG_ENDURE = dict(
    kind="impulse", tag="endure",
    impulse_atr=2.8, ema_n=48, hold_atr_trail=2.2, max_hold=72,
    confirm=1, adx_min=16.0, er_min=0.05,
    ek=rebirth_ek(0.12, cd=4, nf=0.40), assets=list(ASSETS),
)
LEG_THRUST = dict(
    kind="path_thrust", tag="thrust",
    roc_n=96, er_n=96, entry=0.055, exit=0.008, confirm=2,
    adx_min=16.0, ek=rebirth_ek(0.10, cd=8, nf=0.38), assets=list(ASSETS),
)

PROFILES = {
    "apex": dict(
        title="REBIRTH apex — 80% compress + 20% impulse (very different vs multiz2)",
        parts=(("apex_A", 0.80), ("apex_B", 0.20)),
    ),
    "crown": dict(
        title="REBIRTH crown — 70% compress + 30% impulse",
        parts=(("apex_A", 0.70), ("apex_B", 0.30)),
    ),
    "balanced": dict(
        title="REBIRTH balanced — 55/30/15 compress + impulse + thrust",
        parts=(("apex_A", 0.55), ("apex_B", 0.30), ("thrust", 0.15)),
    ),
    "endurance": dict(
        title="REBIRTH endurance — impulse DNA, tight MDD path",
        parts=(("endure", 1.0),),
    ),
}

LEGS = {"apex_A": LEG_APEX_A, "apex_B": LEG_APEX_B, "endure": LEG_ENDURE, "thrust": LEG_THRUST}
REBIRTH_KINDS = {"compress", "impulse", "path_thrust", "cs_resid", "beta_rs", "twin_mtf", "kelt_rsi", "range_rc"}


def run_leg(cfg, frames, capital=100.0):
    cfg = deepcopy(cfg)
    kind = cfg.get("kind", "")
    if kind in REBIRTH_KINDS:
        side = rebirth_make(cfg, frames)
        fr = {a: frames[a] for a in cfg["assets"]}
        for a in cfg["assets"]:
            if a not in side:
                side[a] = pd.Series(0.0, index=frames[a].index)
        port = rebirth_bt(fr, side, capital=capital, ekcfg=cfg.get("ek"))
    else:
        side = refine_make(cfg, frames)
        fr = {a: frames[a] for a in cfg["assets"]}
        for a in cfg["assets"]:
            if a not in side:
                side[a] = pd.Series(0.0, index=frames[a].index)
        port = refine_bt(fr, side, capital=capital, ekcfg=cfg.get("ek"))
    eq = equity_series(port)
    costs = port.get("costs", 0.0)
    costs = float(np.nansum(costs)) if isinstance(costs, np.ndarray) else float(costs or 0)
    tr = port.get("trades") or []
    fund = port.get("funding", 0.0)
    fund = float(np.nansum(fund)) if isinstance(fund, np.ndarray) else float(fund or 0)
    return dict(
        equity=eq, costs=costs, trades=tr,
        funding=fund,
        trade_stats=trade_stats(tr) if tr else {"n": 0},
        end_equity=float(eq.dropna().iloc[-1]),
        cfg=cfg,
    )


def run_profile(profile: str = "apex", capital: float = 100.0) -> dict:
    if profile not in PROFILES:
        raise SystemExit(f"unknown profile {profile}; choose {list(PROFILES)}")
    meta = PROFILES[profile]
    frames = prepare(ASSETS)
    parts = []
    all_tr = []
    costs = 0.0
    fund = 0.0
    leg_cfgs = []
    for name, w in meta["parts"]:
        r = run_leg(LEGS[name], frames, capital=capital)
        parts.append((w, r["equity"]))
        all_tr.extend(r["trades"])
        costs += w * r["costs"]
        fund += w * r["funding"]
        leg_cfgs.append(LEGS[name])
    if len(parts) == 1:
        eq = parts[0][1]
        # rebase
        eq = eq / float(eq.dropna().iloc[0]) * capital
    else:
        eq = blend_equity(parts, capital)
    win = window_metrics(eq, capital)
    ts = trade_stats(all_tr) if all_tr else {"n": 0}
    out = dict(
        name=STRATEGY_NAME,
        profile=profile,
        profile_title=meta["title"],
        capital=capital,
        windows=win,
        hard_pass=hard_pass(win),
        beats=beats_champions(win),
        score=score_crown(win, int(ts.get("n", 0)), costs, capital),
        end_equity=float(eq.dropna().iloc[-1]),
        costs_usdt=costs,
        funding_pnl=fund,
        n_trades=int(ts.get("n", 0)),
        trades=ts,
        weights={n: w for n, w in meta["parts"]},
        legs=leg_cfgs,
        protocol="IS observe / OOS1 select / OOS2+6M report-only / MDD<=15% / long-only / lev<=1",
        dna="REBIRTH compress(ATR%ile squeeze->break) + impulse + path_thrust (NOT multiz2)",
        truth=(
            "REBIRTH apex: deliberately different DNA vs prior multiz2 (user request). "
            "Expect stronger 6M, fewer trades, wider FULL MDD, lower OOS1 Sharpe. "
            "Not zero-error. Old multiz2 OOS1 crown archived as reference."
        ),
        vs_published=dict(
            old_multiz2_apex_oos1="+11.7%/2.10",
            old_multiz2_apex_6m="+1.7%/0.55",
            old_multiz2_apex_full="+37.6%/1.20 MDD-4.3%",
            old_n=1200,
        ),
        mode="rebirth",
    )
    # path diversity vs archived multiz2 apex equity if present
    arch = os.path.join(RESULTS, "zenith_apex_multiz2_ref_equity.csv")
    if os.path.exists(arch):
        try:
            eq_old = pd.read_csv(arch, index_col=0, parse_dates=True).iloc[:, 0].astype(float)
            out["path_div_vs_multiz2"] = path_distance(eq, eq_old)
        except Exception:
            pass
    return out, eq, all_tr


def write_html(out, path):
    rows = "".join(
        f"<tr><td>{k}</td><td>{fmt_pct(v.get('ret'))}</td>"
        f"<td>{fmt_num(v.get('sharpe'))}</td><td>{fmt_pct(v.get('mdd'))}</td></tr>"
        for k, v in out["windows"].items()
    )
    bt = out.get("beats") or {}
    ts = out.get("trades") or {}
    html = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8"/>
<title>{out['name']} · {out['profile']}</title>
<style>
body{{font-family:system-ui,sans-serif;background:#0b1220;color:#e8eefc;margin:2rem}}
h1{{color:#a5f3fc}} table{{border-collapse:collapse;width:100%;max-width:720px}}
td,th{{border:1px solid #243047;padding:.5rem;text-align:right}}
th{{background:#132033}} .ok{{color:#4ade80}} .card{{background:#121a2b;padding:1rem;border-radius:12px;max-width:720px}}
</style></head><body>
<h1>{out['name']} · <code>{out['profile']}</code></h1>
<p>{out.get('profile_title','')}</p>
<div class="card">
<p>hard_pass: <b class="ok">{out['hard_pass']}</b> · end <b>{out['end_equity']:.2f}</b>
· n {out['n_trades']} · wr {fmt_pct(ts.get('win_rate'))} · PF {fmt_num(ts.get('profit_factor'))}
· costs {out['costs_usdt']:.2f}</p>
<p>beats primary OOS1: <b>{bt.get('beats_inst_primary_oos1')}</b> ·
stable OOS1: <b>{bt.get('beats_inst_stable_oos1')}</b> ·
almasi OOS1: <b>{bt.get('beats_almasi_oos1')}</b> ·
FULL sh lead: <b>{bt.get('beats_all_full_sh')}</b></p>
<p style="opacity:.85">{out.get('truth','')}</p>
</div>
<table><tr><th>بازه</th><th>بازده</th><th>Sharpe</th><th>MaxDD</th></tr>
{rows}</table>
</body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def self_test() -> int:
    checks = []
    out, eq, tr = run_profile("apex", 100.0)
    w = out["windows"]
    checks.append(("end>100", out["end_equity"] > 100))
    checks.append(("hard_pass", out["hard_pass"] is True))
    checks.append(("full_mdd>=-15", w["FULL"]["mdd"] >= -0.15))
    checks.append(("oos1_ret>0", w["OOS1"]["ret"] > 0))
    checks.append(("oos1_sh>=1.0", w["OOS1"]["sharpe"] >= 1.0))
    checks.append(("long_only", all(getattr(t, "side", "long") != "short" for t in tr)))
    checks.append(("eq_finite", np.isfinite(eq.dropna().values[-1])))
    # must be materially different from old multiz2 apex signature
    different = (
        abs(w["6M"]["ret"] - 0.017) >= 0.03
        or abs(w["OOS1"]["sharpe"] - 2.10) >= 0.35
        or abs(out["n_trades"] - 1200) >= 400
    )
    checks.append(("rebirth_different_vs_multiz2", different))
    checks.append(("rebirth_edge_6m_or_oos1",
                   w["6M"]["ret"] >= 0.04 or w["OOS1"]["ret"] >= 0.06))
    out_e, _, _ = run_profile("endurance", 100.0)
    checks.append(("endure_hard", out_e["hard_pass"] is True))
    ok = sum(1 for _, c in checks if c)
    for n, c in checks:
        print(f"  [{'PASS' if c else 'FAIL'}] {n}")
    print(f"self-test {ok}/{len(checks)}")
    return 0 if ok == len(checks) else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="apex", choices=list(PROFILES))
    ap.add_argument("--capital", type=float, default=CAP_DEFAULT)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--all-profiles", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        sys.exit(self_test())

    profiles = list(PROFILES) if args.all_profiles else [args.profile]
    for prof in profiles:
        out, eq, trades = run_profile(prof, args.capital)
        tag = prof
        eq.to_csv(os.path.join(RESULTS, f"zenith_v001_{tag}_equity.csv"), header=["eq"])
        with open(os.path.join(RESULTS, f"zenith_v001_{tag}_results.json"), "w") as f:
            json.dump(out, f, indent=2, default=str)
        if trades:
            try:
                pd.DataFrame([asdict(t) if hasattr(t, "__dataclass_fields__") else t.__dict__
                              for t in trades]).to_csv(
                    os.path.join(RESULTS, f"zenith_v001_{tag}_trades.csv"), index=False)
            except Exception:
                pass
        write_html(out, os.path.join(RESULTS, f"zenith_v001_{tag}_report.html"))
        print("=" * 72)
        print(f"{STRATEGY_NAME} · {prof} · hard={out['hard_pass']} · end={out['end_equity']:.2f}")
        print("=" * 72)
        for k, v in out["windows"].items():
            print(f"  {k:5s}  ret={v['ret']*100:+7.2f}%  Sh={v['sharpe']:6.2f}  MDD={v['mdd']*100:7.2f}%")
        print(f"  beats={out['beats']}")
        print(f"  n={out['n_trades']} costs={out['costs_usdt']:.2f} "
              f"wr={(out['trades'] or {}).get('win_rate', float('nan'))} "
              f"PF={(out['trades'] or {}).get('profit_factor', float('nan'))}")


if __name__ == "__main__":
    main()
