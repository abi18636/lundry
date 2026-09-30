#!/usr/bin/env python3
"""
Optimize Turtle-NS / hybrid alts with an anti-overfit protocol.

Protocol
--------
  IS   : 2021-08-01 → 2024-12-31   (fit / screen only)
  OOS1 : 2025-01-01 → 2026-03-16   (sealed validation)
  OOS2 : 2026-03-17 → 2026-09-15   (user's 6M report window — never used to pick)
  FULL : 2021-08-01 → 2026-09-15

Selection rules (pre-registered)
  A. IS MaxDD >= -22%  and  IS Sharpe >= 0.50  and  IS total_return > 0
  B. OOS1 total_return > 0  and  OOS1 Sharpe >= 0.30  and  OOS1 MaxDD >= -20%
  C. Among survivors, maximise  plateau = mean(IS_sharpe, OOS1_sharpe) - 0.25*|IS_sh-OOS1_sh|
     tie-break: higher OOS1 return, then lower |IS_DD|
  D. Report OOS2 (6M) honestly — it does NOT pick the winner.
  E. Prefer structural upgrades over microscopic grid peaks.

Structural ideas tested
  1. entry/exit Donchian grid + asset sets
  2. ATR-risk sizing vs fixed fraction
  3. pyramid on unrealised profit
  4. vol-scaled notional (target sleeve vol)
  5. cross-sectional: only top-K strongest breakouts
  6. regime gate (skip low ER / high chop)
  7. hybrid: turtle core + small volbreak satellite
  8. asymmetric long/short notional
  9. time-stop (max hold) + breakeven stop after +1R
"""
from __future__ import annotations
import os, sys, json, itertools, time
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
RESULTS = os.path.join(HERE, "results")
DATA = os.path.join(HERE, "data")

from alt_turtle_ns import (
    load_panel, load_csv, prepare, sig_turtle_ns, sig_vol_breakout,
    run_backtest, stats, trade_stats, monte_carlo, buy_hold,
    FEE, SLIP, FUNDING_RATE, FUNDING_DEFAULT, FUNDING_HOURS,
    VERIFY_UNTIL, HOURS_PER_YEAR, atr, adx, ema, kaufman_er, donchian,
    Trade, fmt_pct, fmt_num, rma, true_range,
)

IS_END = pd.Timestamp("2024-12-31 23:00", tz="UTC")
OOS1_START = pd.Timestamp("2025-01-01", tz="UTC")
OOS2_START = pd.Timestamp("2026-03-17", tz="UTC")
FULL_START = pd.Timestamp("2021-08-01", tz="UTC")
UNTIL = VERIFY_UNTIL


# ═══════════════════════════════════════════════════════════════════════════
# Enhanced engine: pyramiding, ATR risk, vol-scale, top-K, breakeven, time-stop
# ═══════════════════════════════════════════════════════════════════════════
def run_enhanced(
    frames: Dict[str, pd.DataFrame],
    side_sig: Dict[str, pd.Series],
    capital: float = 100.0,
    trade_from: Optional[pd.Timestamp] = None,
    trade_to: Optional[pd.Timestamp] = None,
    # sizing
    sizing: str = "fixed",          # fixed | atr_risk | vol_target
    notional_frac: float = 0.25,
    risk_per_trade: float = 0.01,
    atr_risk_mult: float = 2.5,
    vol_target: float = 0.15,
    vol_lookback: int = 336,
    lev_cap: float = 1.0,
    max_positions: int = 4,
    # pyramid
    pyramid_levels: int = 0,        # 0 = off, 1..3 adds
    pyramid_step_atr: float = 1.0,
    pyramid_scale: float = 0.5,     # each add = scale * base notional
    # stops / exits beyond signal
    use_atr_stop: bool = False,
    atr_stop_mult: float = 3.0,
    atr_trail_mult: float = 3.0,
    breakeven_at_R: float = 0.0,    # move stop to entry after +R*stop_dist (0=off)
    time_stop_bars: int = 0,        # 0=off
    # cross-sectional
    top_k: int = 0,                 # 0=all signals; else only K strongest |move|
    top_k_lookback: int = 72,
    # long/short asymmetry
    short_frac_mult: float = 1.0,   # short notional = long * this
    cooldown: int = 0,
):
    assets = list(frames.keys())
    n = len(next(iter(frames.values())))
    dts = next(iter(frames.values()))["dt"].values
    hours = pd.DatetimeIndex(dts).hour.values
    O = {t: frames[t]["open"].values for t in assets}
    H = {t: frames[t]["high"].values for t in assets}
    L = {t: frames[t]["low"].values for t in assets}
    C = {t: frames[t]["close"].values for t in assets}
    ATR = {t: frames[t]["atr"].values for t in assets}
    SIG = {t: np.asarray(side_sig[t], float) for t in assets}

    # precompute momentum for top-k
    MOM = {}
    for t in assets:
        c = pd.Series(C[t])
        MOM[t] = c.pct_change(top_k_lookback).fillna(0).values

    # asset vol for vol targeting
    RV = {}
    for t in assets:
        r = np.log(pd.Series(C[t]) / pd.Series(C[t]).shift(1))
        RV[t] = (r.rolling(vol_lookback, min_periods=max(24, vol_lookback // 4)).std(ddof=0)
                 * np.sqrt(HOURS_PER_YEAR)).fillna(0.5).values

    dti = pd.DatetimeIndex(dts)
    if dti.tz is None:
        dti = dti.tz_localize("UTC")
    start_i = 1
    end_i = n
    if trade_from is not None:
        tf = pd.Timestamp(trade_from)
        if tf.tzinfo is None: tf = tf.tz_localize("UTC")
        else: tf = tf.tz_convert("UTC")
        hits = np.where(dti >= tf)[0]
        if len(hits):
            start_i = max(int(hits[0]), 1)
    if trade_to is not None:
        tt = pd.Timestamp(trade_to)
        if tt.tzinfo is None: tt = tt.tz_localize("UTC")
        else: tt = tt.tz_convert("UTC")
        hits = np.where(dti <= tt)[0]
        if len(hits):
            end_i = int(hits[-1]) + 1

    cash = float(capital)
    units = {t: 0.0 for t in assets}
    stop = {t: np.nan for t in assets}
    side = {t: 0 for t in assets}
    entry_px = {t: np.nan for t in assets}
    entry_i = {t: -1 for t in assets}
    base_units = {t: 0.0 for t in assets}
    pyramid_count = {t: 0 for t in assets}
    stop_dist0 = {t: np.nan for t in assets}
    cool_until = {t: -1 for t in assets}
    cur = {t: None for t in assets}
    eq = np.full(n, np.nan); eq[:start_i] = capital
    costs_arr = np.zeros(n); fund_arr = np.zeros(n)
    exposure = np.zeros(n)
    trades: List[Trade] = []
    cost_ps = FEE + SLIP

    def mtm(i):
        return cash + sum(units[t] * C[t][i] for t in assets)

    def flatten(t, i, px, reason):
        nonlocal cash
        u = units[t]
        if u == 0:
            return
        cost = abs(u) * px * cost_ps
        cash += u * px - cost
        costs_arr[i] += cost
        if cur[t] is not None:
            direction = 1 if cur[t]["side"] == "long" else -1
            raw_ret = (px / cur[t]["entry_px"] - 1.0) * direction
            pnl = u * (px - cur[t]["entry_px"]) - cost
            trades.append(Trade(
                asset=t, side=cur[t]["side"],
                entry_dt=cur[t]["entry_dt"], entry_px=cur[t]["entry_px"],
                exit_dt=str(pd.Timestamp(dts[i])), exit_px=float(px),
                reason=reason, ret=float(raw_ret), pnl_usdt=float(pnl),
                notional=float(cur[t]["notional"]),
                bars_held=int(i - cur[t]["entry_i"]),
            ))
            cur[t] = None
        units[t] = 0.0; stop[t] = np.nan; side[t] = 0
        entry_px[t] = np.nan; entry_i[t] = -1
        base_units[t] = 0.0; pyramid_count[t] = 0; stop_dist0[t] = np.nan
        if reason == "stop":
            cool_until[t] = i + cooldown

    def size_units(t, i, new_side, px, equity_now):
        a = ATR[t][i - 1] if i > 0 else ATR[t][i]
        if px <= 0 or np.isnan(px):
            return 0.0, np.nan
        frac = notional_frac
        if new_side < 0:
            frac *= short_frac_mult
        if sizing == "fixed":
            abs_u = (frac * equity_now) / px
            sdist = atr_risk_mult * a if (not np.isnan(a) and a > 0) else px * 0.02
        elif sizing == "atr_risk":
            if np.isnan(a) or a <= 0:
                return 0.0, np.nan
            sdist = atr_risk_mult * a
            risk_budget = risk_per_trade * equity_now
            abs_u = risk_budget / sdist
            # cap by frac
            abs_u = min(abs_u, (frac * 1.5 * equity_now) / px)
        else:  # vol_target
            rv = RV[t][i - 1] if i > 0 else RV[t][i]
            if rv <= 1e-6:
                rv = 0.5
            lev = min(lev_cap, vol_target / rv)
            abs_u = (lev * frac * equity_now) / px  # frac distributes across names
            sdist = atr_risk_mult * a if (not np.isnan(a) and a > 0) else px * 0.02
        current_notional = sum(abs(units[x]) * C[x][i - 1] for x in assets)
        room = max(0.0, lev_cap * equity_now - current_notional)
        abs_u = min(abs_u, room / px if px > 0 else 0.0)
        if abs_u * px < max(1.0, 0.015 * capital):
            return 0.0, np.nan
        return abs_u, sdist

    def open_pos(t, i, new_side, px, equity_now):
        nonlocal cash
        abs_u, sdist = size_units(t, i, new_side, px, equity_now)
        if abs_u <= 0:
            return
        signed = abs_u if new_side > 0 else -abs_u
        cost = abs_u * px * cost_ps
        cash -= signed * px + cost
        costs_arr[i] += cost
        units[t] = signed
        side[t] = int(np.sign(signed))
        entry_px[t] = px
        entry_i[t] = i
        base_units[t] = abs_u
        pyramid_count[t] = 0
        stop_dist0[t] = sdist
        if use_atr_stop and not np.isnan(sdist):
            stop[t] = px - side[t] * sdist
        else:
            stop[t] = np.nan
        cur[t] = dict(
            side="long" if signed > 0 else "short",
            entry_dt=str(pd.Timestamp(dts[i])), entry_px=float(px),
            entry_i=i, notional=float(abs_u * px),
        )

    def try_pyramid(t, i, px, equity_now):
        nonlocal cash
        if pyramid_levels <= 0 or pyramid_count[t] >= pyramid_levels:
            return
        if units[t] == 0 or np.isnan(stop_dist0[t]):
            return
        # price moved pyramid_step_atr * ATR in our favour from last add/entry
        a = ATR[t][i - 1]
        if np.isnan(a) or a <= 0:
            return
        move = (px - entry_px[t]) * side[t]
        need = pyramid_step_atr * a * (pyramid_count[t] + 1)
        if move < need:
            return
        add_u = base_units[t] * pyramid_scale
        current_notional = sum(abs(units[x]) * C[x][i - 1] for x in assets)
        room = max(0.0, lev_cap * equity_now - current_notional)
        add_u = min(add_u, room / px if px > 0 else 0.0)
        if add_u * px < max(0.5, 0.01 * capital):
            return
        signed_add = add_u if side[t] > 0 else -add_u
        cost = add_u * px * cost_ps
        cash -= signed_add * px + cost
        costs_arr[i] += cost
        # weighted avg entry
        old_u = abs(units[t])
        new_u = old_u + add_u
        entry_px[t] = (entry_px[t] * old_u + px * add_u) / new_u
        units[t] += signed_add
        pyramid_count[t] += 1
        if cur[t] is not None:
            cur[t]["notional"] = float(abs(units[t]) * entry_px[t])
            cur[t]["entry_px"] = float(entry_px[t])
        # tighten stop toward price
        if use_atr_stop and not np.isnan(stop[t]):
            if side[t] > 0:
                stop[t] = max(stop[t], px - atr_trail_mult * a)
            else:
                stop[t] = min(stop[t], px + atr_trail_mult * a)

    for i in range(start_i, min(end_i, n)):
        # 1) manage exits
        for t in assets:
            if units[t] == 0:
                continue
            # time stop
            if time_stop_bars > 0 and entry_i[t] >= 0 and (i - entry_i[t]) >= time_stop_bars:
                flatten(t, i, O[t][i], "time_stop")
                continue
            # ATR / breakeven stop
            if use_atr_stop and not np.isnan(stop[t]):
                a_prev = ATR[t][i - 1]
                if not np.isnan(a_prev) and a_prev > 0:
                    if units[t] > 0:
                        trail = C[t][i - 1] - atr_trail_mult * a_prev
                        stop[t] = max(stop[t], trail)
                    else:
                        trail = C[t][i - 1] + atr_trail_mult * a_prev
                        stop[t] = min(stop[t], trail)
                # breakeven
                if breakeven_at_R > 0 and not np.isnan(stop_dist0[t]) and not np.isnan(entry_px[t]):
                    fav = (C[t][i - 1] - entry_px[t]) * side[t]
                    if fav >= breakeven_at_R * stop_dist0[t]:
                        if side[t] > 0:
                            stop[t] = max(stop[t], entry_px[t])
                        else:
                            stop[t] = min(stop[t], entry_px[t])
                hit, fill = False, O[t][i]
                if units[t] > 0 and L[t][i] <= stop[t]:
                    fill, hit = min(O[t][i], stop[t]), True
                elif units[t] < 0 and H[t][i] >= stop[t]:
                    fill, hit = max(O[t][i], stop[t]), True
                if hit:
                    flatten(t, i, fill, "stop")
                    continue
            # signal exit
            des = int(np.sign(SIG[t][i - 1])) if not np.isnan(SIG[t][i - 1]) else 0
            if des == 0 and side[t] != 0:
                flatten(t, i, O[t][i], "signal_exit")
            elif des != 0 and des != side[t]:
                flatten(t, i, O[t][i], "reverse")

        # 2) pyramid existing
        equity_now = mtm(i - 1)
        if pyramid_levels > 0:
            for t in assets:
                if units[t] != 0:
                    try_pyramid(t, i, O[t][i], equity_now)

        # 3) new entries
        equity_now = mtm(i - 1)
        n_open = sum(1 for t in assets if units[t] != 0)
        # candidate entries
        cands = []
        for t in assets:
            if units[t] != 0 or i < cool_until[t]:
                continue
            des = int(np.sign(SIG[t][i - 1])) if not np.isnan(SIG[t][i - 1]) else 0
            if des == 0:
                continue
            strength = abs(MOM[t][i - 1]) if not np.isnan(MOM[t][i - 1]) else 0.0
            # directional strength: momentum aligned with des
            aligned = MOM[t][i - 1] * des
            cands.append((aligned if top_k > 0 else strength, t, des))
        if top_k > 0 and cands:
            cands.sort(reverse=True)
            cands = cands[: max(0, top_k - n_open)]
        for _, t, des in cands:
            if n_open >= max_positions:
                break
            open_pos(t, i, des, O[t][i], equity_now)
            if units[t] != 0:
                n_open += 1

        # funding
        if hours[i] % FUNDING_HOURS == 0:
            for t in assets:
                if units[t] == 0:
                    continue
                rate = FUNDING_RATE.get(t, FUNDING_DEFAULT)
                f = units[t] * C[t][i] * rate
                cash -= f; fund_arr[i] += f

        eq[i] = mtm(i)
        exposure[i] = (sum(abs(units[t]) * C[t][i] for t in assets) / eq[i]
                       if eq[i] > 0 else 0.0)

    last = min(end_i, n) - 1
    for t in assets:
        if units[t] != 0:
            flatten(t, last, C[t][last], "eod")
    if last >= 0:
        eq[last] = mtm(last)
    # fill
    eq = pd.Series(eq).ffill().bfill().values
    return dict(
        equity=eq, costs=costs_arr, funding=fund_arr, exposure=exposure,
        dts=dts, trades=trades, start_i=start_i, end_i=end_i, capital=capital,
    )


def slice_result(res, capital=100.0, label=""):
    si, ei = res["start_i"], res.get("end_i", len(res["equity"]))
    eq = res["equity"][si:ei]
    # drop trailing nan
    if np.isnan(eq).any():
        valid = ~np.isnan(eq)
        if not valid.any():
            return None
        # keep contiguous from first valid
        eq = pd.Series(eq).ffill().bfill().values
    dts = res["dts"][si:ei]
    st = stats(eq, dts, capital, label)
    ts = trade_stats(res["trades"])
    costs = float(res["costs"][si:ei].sum())
    fund = float(res["funding"][si:ei].sum())
    return dict(
        label=label,
        total_return=st["total_return"], cagr=st["cagr"], sharpe=st["sharpe"],
        sortino=st.get("sortino"), max_dd=st["max_dd"], calmar=st.get("calmar"),
        end_equity=st["end_equity"], ann_vol=st.get("ann_vol"),
        n_trades=ts.get("n", 0), win_rate=ts.get("win_rate"),
        profit_factor=ts.get("profit_factor"), avg_hold=ts.get("avg_hold_hours"),
        costs=costs, funding=fund, stats=st, trades_s=ts, res=res,
    )


def make_signals(frames, family, params):
    sides = {}
    for t, d in frames.items():
        if family == "turtle":
            sides[t] = sig_turtle_ns(
                d,
                entry_n=params.get("entry_n", 504),
                exit_n=params.get("exit_n", 72),
                adx_min=params.get("adx_min", 15.0),
                er_min=params.get("er_min", 0.08),
            )
        elif family == "volbreak":
            sides[t] = sig_vol_breakout(
                d,
                mult=params.get("vb_mult", 3.0),
                exit_mult=params.get("vb_exit", 1.2),
                base_n=params.get("vb_base", 336),
            )
        elif family == "hybrid":
            # turtle primary; OR in volbreak only when turtle flat — via max abs with turtle priority
            st = sig_turtle_ns(d, params.get("entry_n", 504), params.get("exit_n", 72),
                               params.get("adx_min", 15.0), params.get("er_min", 0.08))
            vb = sig_vol_breakout(d, params.get("vb_mult", 3.0), params.get("vb_exit", 1.2),
                                  params.get("vb_base", 336))
            # hybrid: use turtle when nonzero else volbreak * hybrid_w sign
            hw = params.get("hybrid_w", 1.0)
            arr = np.where(st.values != 0, st.values, hw * vb.values)
            sides[t] = pd.Series(arr, index=d.index)
        else:
            raise ValueError(family)
    return sides


def evaluate_cfg(frames_cache, cfg, capital=100.0):
    """Run IS / OOS1 / OOS2 / FULL for one config."""
    assets = cfg["assets"]
    key = tuple(assets)
    frames = frames_cache[key]
    sides = make_signals(frames, cfg["family"], cfg)
    engine_kw = {k: cfg[k] for k in (
        "sizing", "notional_frac", "risk_per_trade", "atr_risk_mult",
        "vol_target", "vol_lookback", "lev_cap", "max_positions",
        "pyramid_levels", "pyramid_step_atr", "pyramid_scale",
        "use_atr_stop", "atr_stop_mult", "atr_trail_mult",
        "breakeven_at_R", "time_stop_bars", "top_k", "top_k_lookback",
        "short_frac_mult", "cooldown",
    ) if k in cfg}

    out = {}
    windows = {
        "IS": (FULL_START, IS_END),
        "OOS1": (OOS1_START, OOS2_START - pd.Timedelta(hours=1)),
        "OOS2": (OOS2_START, None),
        "FULL": (FULL_START, None),
    }
    for lab, (a, b) in windows.items():
        res = run_enhanced(frames, sides, capital=capital, trade_from=a, trade_to=b, **engine_kw)
        sl = slice_result(res, capital, lab)
        if sl is None:
            return None
        out[lab] = sl
    return out


def plateau_score(is_sh, oos1_sh):
    if any(x is None or (isinstance(x, float) and np.isnan(x)) for x in (is_sh, oos1_sh)):
        return -9.0
    return 0.5 * (is_sh + oos1_sh) - 0.25 * abs(is_sh - oos1_sh)


def passes_rules(row):
    is_, o1 = row["IS"], row["OOS1"]
    if is_["total_return"] <= 0: return False
    if is_["max_dd"] < -0.22: return False
    if is_["sharpe"] < 0.50: return False
    if o1["total_return"] <= 0: return False
    if o1["sharpe"] < 0.30: return False
    if o1["max_dd"] < -0.20: return False
    return True


def show_row(name, row, mark=""):
    def f(lab):
        r = row[lab]
        return (f"{r['total_return']*100:+5.1f}%/{r['sharpe']:+.2f}/{r['max_dd']*100:+.1f}%")
    print(f"  {mark}{name:42s}  IS {f('IS')}  OOS1 {f('OOS1')}  "
          f"OOS2 {f('OOS2')}  FULL {f('FULL')}  nIS={row['IS']['n_trades']}", flush=True)


# ═══════════════════════════════════════════════════════════════════════════
# Config grid (structural, not insane cartesian)
# ═══════════════════════════════════════════════════════════════════════════
def build_grid():
    configs = []
    asset_sets = [
        ("BTC", "ETH", "SOL"),
        ("BTC", "ETH"),
        ("BTC", "ETH", "SOL", "XRP"),
        ("BTC", "ETH", "SOL", "DOGE"),
        ("BTC", "ETH", "SOL", "XRP", "DOGE"),
        ("BTC", "ETH", "SOL", "LINK"),
    ]

    def base(**kw):
        d = dict(
            family="turtle",
            sizing="fixed", notional_frac=0.25, risk_per_trade=0.01,
            atr_risk_mult=2.5, vol_target=0.15, vol_lookback=336,
            lev_cap=1.0, max_positions=4,
            pyramid_levels=0, pyramid_step_atr=1.0, pyramid_scale=0.5,
            use_atr_stop=False, atr_stop_mult=3.0, atr_trail_mult=3.0,
            breakeven_at_R=0.0, time_stop_bars=0,
            top_k=0, top_k_lookback=72, short_frac_mult=1.0, cooldown=0,
            entry_n=504, exit_n=72, adx_min=15.0, er_min=0.08,
            assets=("BTC", "ETH", "SOL"),
        )
        d.update(kw)
        d["max_positions"] = min(d.get("max_positions", len(d["assets"])), len(d["assets"]))
        return d

    # --- baseline reference ---
    configs.append(base(name="BASE_turtle_504_72_f25_3"))

    # 1) entry/exit grid on best asset sets
    for assets in asset_sets[:4]:
        for entry in (336, 420, 504, 600, 672, 840):
            for exit_ in (48, 72, 96, 120, 168):
                if exit_ >= entry:
                    continue
                for frac in (0.20, 0.25, 0.30, 0.35):
                    if len(assets) * frac > 1.05:
                        continue
                    configs.append(base(
                        name=f"T_e{entry}_x{exit_}_f{frac}_{len(assets)}a",
                        assets=assets, entry_n=entry, exit_n=exit_,
                        notional_frac=frac,
                    ))

    # 2) filter dial
    for adx_min, er_min in [(12, 0.06), (15, 0.08), (18, 0.10), (22, 0.12), (15, 0.05), (10, 0.08)]:
        configs.append(base(
            name=f"T_filt_adx{adx_min}_er{er_min}",
            entry_n=504, exit_n=72, adx_min=adx_min, er_min=er_min,
            notional_frac=0.25, assets=("BTC", "ETH", "SOL"),
        ))
        configs.append(base(
            name=f"T_filt2_adx{adx_min}_er{er_min}",
            entry_n=420, exit_n=96, adx_min=adx_min, er_min=er_min,
            notional_frac=0.25, assets=("BTC", "ETH", "SOL"),
        ))

    # 3) sizing variants
    for assets, frac in [(("BTC", "ETH", "SOL"), 0.25), (("BTC", "ETH"), 0.40),
                         (("BTC", "ETH", "SOL", "XRP"), 0.22)]:
        for sizing, extra in [
            ("fixed", dict(notional_frac=frac)),
            ("atr_risk", dict(notional_frac=frac, risk_per_trade=0.01, atr_risk_mult=2.5)),
            ("atr_risk", dict(notional_frac=frac, risk_per_trade=0.012, atr_risk_mult=3.0)),
            ("atr_risk", dict(notional_frac=frac, risk_per_trade=0.008, atr_risk_mult=2.0)),
            ("vol_target", dict(notional_frac=frac, vol_target=0.12)),
            ("vol_target", dict(notional_frac=frac, vol_target=0.18)),
            ("vol_target", dict(notional_frac=0.33, vol_target=0.15)),
        ]:
            configs.append(base(
                name=f"T_sz_{sizing}_{len(assets)}a_{extra}",
                assets=assets, sizing=sizing, entry_n=504, exit_n=72, **extra,
            ))

    # 4) pyramid
    for pyr, step, scale in [(1, 1.0, 0.5), (2, 1.0, 0.5), (2, 1.5, 0.4), (1, 0.75, 0.6), (3, 1.0, 0.33)]:
        configs.append(base(
            name=f"T_pyr{pyr}_s{step}_sc{scale}",
            pyramid_levels=pyr, pyramid_step_atr=step, pyramid_scale=scale,
            entry_n=504, exit_n=96, notional_frac=0.22,
            assets=("BTC", "ETH", "SOL"),
        ))
        configs.append(base(
            name=f"T_pyr{pyr}_e420",
            pyramid_levels=pyr, pyramid_step_atr=step, pyramid_scale=scale,
            entry_n=420, exit_n=72, notional_frac=0.22,
            assets=("BTC", "ETH", "SOL"),
        ))

    # 5) soft ATR trail (protective) without killing trend
    for sm, tm in [(4.0, 4.0), (5.0, 4.0), (3.5, 3.5), (6.0, 5.0)]:
        configs.append(base(
            name=f"T_trail_{sm}_{tm}",
            use_atr_stop=True, atr_stop_mult=sm, atr_trail_mult=tm,
            entry_n=504, exit_n=120, notional_frac=0.25,
            assets=("BTC", "ETH", "SOL"),
        ))
        configs.append(base(
            name=f"T_trail_be_{sm}",
            use_atr_stop=True, atr_stop_mult=sm, atr_trail_mult=tm,
            breakeven_at_R=1.0, entry_n=504, exit_n=96, notional_frac=0.25,
            assets=("BTC", "ETH", "SOL"),
        ))

    # 6) top-K cross sectional breakout
    for k, assets in [(2, ("BTC", "ETH", "SOL", "XRP")), (2, ("BTC", "ETH", "SOL", "XRP", "DOGE")),
                      (1, ("BTC", "ETH", "SOL", "XRP")), (3, ("BTC", "ETH", "SOL", "XRP", "DOGE")),
                      (2, ("BTC", "ETH", "SOL"))]:
        for entry, exit_ in [(504, 72), (420, 96), (672, 120), (504, 120)]:
            configs.append(base(
                name=f"T_top{k}_e{entry}_x{exit_}_{len(assets)}a",
                assets=assets, top_k=k, entry_n=entry, exit_n=exit_,
                notional_frac=min(0.40, 0.85 / max(k, 1)),
                max_positions=k,
            ))

    # 7) short dampening (crypto short often bleeds funding)
    for sm in (0.5, 0.75, 0.0):
        configs.append(base(
            name=f"T_short{sm}",
            short_frac_mult=sm, entry_n=504, exit_n=72, notional_frac=0.30,
            assets=("BTC", "ETH", "SOL"),
        ))
        configs.append(base(
            name=f"T_short{sm}_e420",
            short_frac_mult=sm, entry_n=420, exit_n=96, notional_frac=0.30,
            assets=("BTC", "ETH", "SOL"),
        ))

    # 8) hybrid turtle + volbreak
    for assets in [("BTC", "ETH", "SOL"), ("BTC", "ETH")]:
        for hw in (0.5, 1.0):
            configs.append(base(
                name=f"HYB_w{hw}_{len(assets)}a",
                family="hybrid", hybrid_w=hw, assets=assets,
                entry_n=504, exit_n=72, vb_mult=3.0, vb_exit=1.2, vb_base=336,
                notional_frac=0.25 if len(assets) == 3 else 0.40,
            ))
            configs.append(base(
                name=f"HYB2_w{hw}_{len(assets)}a",
                family="hybrid", hybrid_w=hw, assets=assets,
                entry_n=420, exit_n=96, vb_mult=2.5, vb_exit=1.0, vb_base=336,
                notional_frac=0.25 if len(assets) == 3 else 0.40,
                use_atr_stop=True, atr_stop_mult=3.5, atr_trail_mult=3.5,
            ))

    # 9) volbreak optimized
    for assets in [("BTC", "ETH"), ("BTC", "ETH", "SOL")]:
        for mult in (2.5, 3.0, 3.5, 4.0):
            for frac in (0.25, 0.35, 0.45):
                if len(assets) * frac > 1.05:
                    continue
                configs.append(base(
                    name=f"VB_m{mult}_f{frac}_{len(assets)}a",
                    family="volbreak", assets=assets, vb_mult=mult, vb_exit=1.2,
                    vb_base=336, notional_frac=frac,
                    use_atr_stop=True, atr_stop_mult=mult, atr_trail_mult=mult,
                    cooldown=6,
                ))

    # 10) time stop
    for ts_ in (168, 336, 504, 672):
        configs.append(base(
            name=f"T_tstop{ts_}",
            time_stop_bars=ts_, entry_n=504, exit_n=72, notional_frac=0.25,
            assets=("BTC", "ETH", "SOL"),
        ))

    # 11) lev soft 1.25 with lower frac (still conservative)
    configs.append(base(
        name="T_lev1.15_f22",
        lev_cap=1.15, notional_frac=0.22, entry_n=504, exit_n=72,
        assets=("BTC", "ETH", "SOL"),
    ))
    configs.append(base(
        name="T_lev1.25_atr",
        lev_cap=1.25, sizing="atr_risk", risk_per_trade=0.012, atr_risk_mult=2.5,
        notional_frac=0.35, entry_n=504, exit_n=96,
        assets=("BTC", "ETH", "SOL"),
    ))

    # dedupe by name
    seen = set(); uniq = []
    for c in configs:
        n = c["name"]
        if n in seen: continue
        seen.add(n); uniq.append(c)
    return uniq


def main():
    t0 = time.time()
    capital = 100.0
    print("[load panels]", flush=True)
    all_assets = ("BTC", "ETH", "SOL", "XRP", "DOGE", "LINK")
    # preload each needed set
    frames_cache = {}
    needed_sets = set()
    grid = build_grid()
    print(f"[grid] {len(grid)} configs", flush=True)
    for c in grid:
        needed_sets.add(tuple(c["assets"]))
    for s in needed_sets:
        print(f"  load {s}", flush=True)
        frames_cache[s] = load_panel(s, until=UNTIL)

    rows = []
    errors = 0
    for i, cfg in enumerate(grid):
        try:
            out = evaluate_cfg(frames_cache, cfg, capital)
            if out is None:
                errors += 1
                continue
            out["cfg"] = cfg
            out["name"] = cfg["name"]
            out["score"] = plateau_score(out["IS"]["sharpe"], out["OOS1"]["sharpe"])
            out["pass"] = passes_rules(out)
            rows.append(out)
        except Exception as e:
            errors += 1
            if errors < 5:
                print(f"  ERR {cfg['name']}: {e}", flush=True)
        if (i + 1) % 40 == 0:
            print(f"  ... {i+1}/{len(grid)} done  survivors={sum(1 for r in rows if r['pass'])}", flush=True)

    print(f"\n[done grid] {len(rows)} ok, {errors} err, {time.time()-t0:.1f}s", flush=True)

    survivors = [r for r in rows if r["pass"]]
    survivors.sort(key=lambda r: (r["score"], r["OOS1"]["total_return"], r["IS"]["sharpe"]), reverse=True)

    print(f"\n=== SURVIVORS ({len(survivors)}) by plateau score ===")
    for r in survivors[:20]:
        show_row(r["name"], r, mark="✓ ")

    # also top by OOS2 among survivors (honest, not selection)
    print(f"\n=== Survivors ranked by OOS2 (6M) return — info only ===")
    by6 = sorted(survivors, key=lambda r: r["OOS2"]["total_return"], reverse=True)
    for r in by6[:10]:
        show_row(r["name"], r)

    # top by FULL sharpe among survivors
    print(f"\n=== Survivors by FULL Sharpe ===")
    byf = sorted(survivors, key=lambda r: r["FULL"]["sharpe"], reverse=True)
    for r in byf[:10]:
        show_row(r["name"], r)

    # baseline comparison
    base = next((r for r in rows if r["name"] == "BASE_turtle_504_72_f25_3"), None)
    if base:
        print("\n=== BASELINE ===")
        show_row(base["name"], base, mark="· ")

    # pick champion alt
    if survivors:
        champ = survivors[0]
        # secondary: best FULL sharpe that also passes and OOS2 > 0
        alt = None
        for r in byf:
            if r["OOS2"]["total_return"] > 0 and r["OOS2"]["max_dd"] > -0.15:
                alt = r; break
        if alt is None:
            alt = byf[0] if byf else champ
    else:
        # relax rule B slightly
        print("\n[relax] no survivors — soft filter")
        soft = [r for r in rows
                if r["IS"]["total_return"] > 0 and r["IS"]["sharpe"] >= 0.40
                and r["IS"]["max_dd"] >= -0.28
                and r["OOS1"]["total_return"] > -0.05 and r["OOS1"]["sharpe"] >= 0.15]
        soft.sort(key=lambda r: (r["score"], r["FULL"]["sharpe"]), reverse=True)
        for r in soft[:15]:
            show_row(r["name"], r, mark="~ ")
        champ = soft[0] if soft else max(rows, key=lambda r: r["score"])
        alt = champ

    def slim(r):
        return dict(
            name=r["name"], score=r["score"], pass_=r["pass"],
            cfg={k: v for k, v in r["cfg"].items() if k != "name"},
            IS={k: r["IS"][k] for k in ("total_return","sharpe","max_dd","n_trades","end_equity","costs")},
            OOS1={k: r["OOS1"][k] for k in ("total_return","sharpe","max_dd","n_trades","end_equity","costs")},
            OOS2={k: r["OOS2"][k] for k in ("total_return","sharpe","max_dd","n_trades","end_equity","costs")},
            FULL={k: r["FULL"][k] for k in ("total_return","sharpe","max_dd","n_trades","end_equity","costs","cagr","calmar")},
        )

    print("\n=== SELECTED CHAMPION ALT ===")
    show_row(champ["name"], champ, mark="★ ")
    print("cfg:", {k: champ["cfg"][k] for k in champ["cfg"] if k in (
        "family","assets","entry_n","exit_n","notional_frac","sizing","pyramid_levels",
        "use_atr_stop","top_k","short_frac_mult","adx_min","er_min","lev_cap","risk_per_trade",
        "vol_target","hybrid_w","vb_mult")})

    if alt["name"] != champ["name"]:
        print("\n=== SECONDARY (best FULL among robust) ===")
        show_row(alt["name"], alt, mark="☆ ")

    # deep refine around champion (local neighbourhood)
    print("\n=== Local refine around champion ===")
    c0 = champ["cfg"]
    refine = []
    if c0["family"] == "turtle":
        for de in (-84, -48, -24, 0, 24, 48, 84):
            for dx in (-24, -12, 0, 12, 24, 48):
                for df in (-0.05, 0, 0.05, 0.08):
                    e = max(168, c0.get("entry_n", 504) + de)
                    x = max(24, c0.get("exit_n", 72) + dx)
                    if x >= e: continue
                    f = round(min(0.45, max(0.12, c0.get("notional_frac", 0.25) + df)), 3)
                    cfg = dict(c0)
                    cfg.update(entry_n=e, exit_n=x, notional_frac=f,
                               name=f"REF_e{e}_x{x}_f{f}")
                    cfg["max_positions"] = min(cfg.get("max_positions", len(cfg["assets"])), len(cfg["assets"]))
                    try:
                        out = evaluate_cfg(frames_cache, cfg, capital)
                        if not out: continue
                        out["cfg"] = cfg; out["name"] = cfg["name"]
                        out["score"] = plateau_score(out["IS"]["sharpe"], out["OOS1"]["sharpe"])
                        out["pass"] = passes_rules(out)
                        refine.append(out)
                    except Exception:
                        pass
        # also refine pyramid / short if base had them off
        for pyr in (0, 1, 2):
            for sm in (0.5, 0.75, 1.0):
                cfg = dict(c0)
                cfg.update(pyramid_levels=pyr, pyramid_step_atr=1.0, pyramid_scale=0.5,
                           short_frac_mult=sm, name=f"REF_pyr{pyr}_sh{sm}")
                try:
                    out = evaluate_cfg(frames_cache, cfg, capital)
                    if not out: continue
                    out["cfg"]=cfg; out["name"]=cfg["name"]
                    out["score"]=plateau_score(out["IS"]["sharpe"], out["OOS1"]["sharpe"])
                    out["pass"]=passes_rules(out)
                    refine.append(out)
                except Exception:
                    pass

    ref_surv = [r for r in refine if r["pass"]]
    ref_surv.sort(key=lambda r: (r["score"], r["OOS1"]["total_return"]), reverse=True)
    print(f"  refine survivors: {len(ref_surv)}/{len(refine)}")
    for r in ref_surv[:10]:
        show_row(r["name"], r, mark="◆ ")

    if ref_surv and ref_surv[0]["score"] > champ["score"] + 0.02:
        print("  → refine improves plateau — adopting")
        champ = ref_surv[0]
    elif ref_surv:
        # take refine if better OOS1 return with similar score
        best_ref = ref_surv[0]
        if best_ref["score"] >= champ["score"] - 0.05 and best_ref["OOS1"]["total_return"] > champ["OOS1"]["total_return"]:
            print("  → refine better OOS1 — adopting")
            champ = best_ref

    print("\n=== FINAL CHAMPION ALT ===")
    show_row(champ["name"], champ, mark="★★ ")
    print(json.dumps({k: champ["cfg"][k] for k in champ["cfg"] if k != "name" and not isinstance(champ["cfg"][k], (list, dict)) or k == "assets"}, default=str, indent=2))

    # save
    os.makedirs(RESULTS, exist_ok=True)
    payload = dict(
        protocol=dict(IS="2021-08-01→2024-12-31", OOS1="2025-01-01→2026-03-16",
                      OOS2="2026-03-17→2026-09-15", rules="A/B/C/D/E"),
        n_grid=len(grid), n_evaluated=len(rows), n_survivors=len(survivors),
        baseline=slim(base) if base else None,
        champion=slim(champ),
        secondary=slim(alt) if alt else None,
        top_survivors=[slim(r) for r in survivors[:25]],
        top_refine=[slim(r) for r in ref_surv[:15]],
    )
    with open(os.path.join(RESULTS, "alt_optimize_results.json"), "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print("[saved] results/alt_optimize_results.json")

    # write champion cfg for runner
    with open(os.path.join(RESULTS, "alt_optimized_cfg.json"), "w") as f:
        json.dump(dict(cfg=champ["cfg"], metrics=slim(champ)), f, indent=2, default=str)
    print("[saved] results/alt_optimized_cfg.json")
    print(f"elapsed {time.time()-t0:.1f}s")
    return champ


if __name__ == "__main__":
    main()
