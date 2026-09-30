#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
════════════════════════════════════════════════════════════════════════════════
 ALT STRATEGY — Turtle-NS  (Donchian آهسته · بدون ATR-stop · بدون vol-target)
════════════════════════════════════════════════════════════════════════════════

A deliberately DIFFERENT strategy from the RCPE-1 champion.

  Champion RCPE-1          →  this Turtle-NS
  ─────────────────────────────────────────────────────────────
  3-sleeve continuous blend    discrete long/short per asset
  daily vol-target sizing      fixed fraction of equity per name
  no stop-loss                 no ATR stop either (channel exit ONLY)
  daily loss limit             none (size is the risk control)
  BTC/ETH/SOL/DOGE             BTC/ETH/SOL
  target_vol 11%               notional 25% equity / asset (cap 1×)

Signal
  Enter long  when close breaks above Donchian(entry_n) high
              AND ADX≥15 AND Kaufman ER≥0.08 AND close > EMA168
  Enter short when close breaks below Donchian(entry_n) low
              AND same filters AND close < EMA168
  Exit long   when close < Donchian(exit_n) low   (and sym. for short)

Execution realism
  · Decision on CLOSE of bar t  →  fill at OPEN of bar t+1
  · Fee 5 bp + slippage 2 bp per side  (14 bp round-trip)
  · Funding every 8 h at OKX mean rates
  · No leverage (portfolio notional ≤ 100% equity)

Selected by FULL-sample screen first (not 6M peak-picking):
  FULL 2021-08→2026-09 : +173% total, Sharpe 0.80, MDD −29.8%, 301 trades
  Last 6 months         : +18.8%, Sharpe 1.84, MDD −9.6%, 22 trades, $100 book
  (RCPE-1 same 6M ref)  : +7.9%,  Sharpe 1.43, MDD −6.8%

Usage
  python3 alt_turtle_ns.py                     # $100, last 6 months
  python3 alt_turtle_ns.py --capital 100 --months 6
  python3 alt_turtle_ns.py --full-sample
  python3 alt_turtle_ns.py --profile volbreak  # alt #2: ATR vol-breakout
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
RESULTS = os.path.join(HERE, "results")

HOURS_PER_YEAR = 24 * 365
FUNDING_HOURS = 8
FEE, SLIP = 0.0005, 0.0002
COST_SIDE = FEE + SLIP
VERIFY_UNTIL = "2026-09-15 20:00"

FUNDING_RATE = {
    "BTC": 0.000046, "ETH": 0.000034, "SOL": 0.0000246,
    "XRP": 0.0000266, "DOGE": 0.0000564,
}
FUNDING_DEFAULT = 0.00003

# ── profiles ────────────────────────────────────────────────────────────────
PROFILES = {
    # Primary pick: slow Donchian, channel-exit only, BTC/ETH/SOL
    "turtle_ns": dict(
        title="Turtle-NS · Donchian آهسته (بدون استاپ)",
        assets=("BTC", "ETH", "SOL"),
        entry_n=504,       # 21-day breakout
        exit_n=72,         # 3-day exit channel
        adx_min=15.0,
        er_min=0.08,
        ema_filter=168,
        notional_frac=0.25,
        use_stop=False,
        atr_stop_mult=99.0,
        atr_trail_mult=99.0,
        cooldown=0,
        family="turtle",
    ),
    # Secondary pick: ATR expansion breakout on BTC/ETH — very low DD
    "volbreak": dict(
        title="Vol-Breakout · شکست با انبساط ATR",
        assets=("BTC", "ETH"),
        entry_n=0,
        exit_n=0,
        adx_min=0.0,
        er_min=0.0,
        ema_filter=0,
        notional_frac=0.25,
        use_stop=True,
        atr_stop_mult=3.0,
        atr_trail_mult=3.0,
        cooldown=6,
        family="volbreak",
        vb_mult=3.0,
        vb_exit=1.2,
        vb_base=336,
    ),
}
DEFAULT_PROFILE = "turtle_ns"


# ═══════════════════════════════════════════════════════════════════════════
# Indicators
# ═══════════════════════════════════════════════════════════════════════════
def rma(s, n):
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def ema(s, n):
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def true_range(h, l, c):
    pc = c.shift(1)
    return pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)


def atr(h, l, c, n=14):
    return rma(true_range(h, l, c), n)


def adx(h, l, c, n=14):
    up, dn = h.diff(), -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr_n = rma(true_range(h, l, c), n)
    pdi = 100 * rma(pd.Series(plus_dm, index=c.index), n) / tr_n
    mdi = 100 * rma(pd.Series(minus_dm, index=c.index), n) / tr_n
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return rma(dx.fillna(0), n)


def kaufman_er(close, n=48):
    change = (close - close.shift(n)).abs()
    path = close.diff().abs().rolling(n, min_periods=n).sum()
    return (change / path.replace(0, np.nan)).fillna(0.0)


def donchian(h, l, n):
    """Shifted by 1: bar t uses only completed bars t-n…t-1."""
    return (h.rolling(n, min_periods=n).max().shift(1),
            l.rolling(n, min_periods=n).min().shift(1))


# ═══════════════════════════════════════════════════════════════════════════
# Data
# ═══════════════════════════════════════════════════════════════════════════
def load_csv(tag, until=VERIFY_UNTIL):
    path = os.path.join(DATA, f"{tag}_USDT_SWAP_1H.csv")
    df = pd.read_csv(path)
    df["dt"] = pd.to_datetime(df["dt"], utc=True)
    df = df.sort_values("dt").drop_duplicates("dt", keep="last")
    if "confirm" in df.columns:
        df = df[df["confirm"] == 1].copy()
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float)
    if until:
        df = df[df["dt"] <= pd.Timestamp(until, tz="UTC")]
    return df.reset_index(drop=True)


def prepare(df):
    d = df.copy()
    d["atr"] = atr(d["high"], d["low"], d["close"], 14)
    d["adx"] = adx(d["high"], d["low"], d["close"], 14)
    d["er"] = kaufman_er(d["close"], 48)
    d["ema"] = ema(d["close"], 168)
    return d


def load_panel(assets, until=VERIFY_UNTIL):
    frames = {t: prepare(load_csv(t, until)) for t in assets}
    common = None
    for d in frames.values():
        s = set(d["dt"].tolist())
        common = s if common is None else common & s
    common = sorted(common)
    return {t: d[d["dt"].isin(common)].reset_index(drop=True) for t, d in frames.items()}


# ═══════════════════════════════════════════════════════════════════════════
# Signals
# ═══════════════════════════════════════════════════════════════════════════
def sig_turtle_ns(d, entry_n=504, exit_n=72, adx_min=15.0, er_min=0.08):
    don_hi, don_lo = donchian(d["high"], d["low"], entry_n)
    ex_hi, ex_lo = donchian(d["high"], d["low"], exit_n)
    adx_v, er, e = d["adx"].values, d["er"].values, d["ema"].values
    cv = d["close"].values
    n = len(d)
    out = np.zeros(n)
    side = 0
    for i in range(n):
        if np.isnan(don_hi.values[i]) or np.isnan(adx_v[i]):
            out[i] = 0
            continue
        if side > 0:
            side = 0 if cv[i] < ex_lo.values[i] else 1
        elif side < 0:
            side = 0 if cv[i] > ex_hi.values[i] else -1
        else:
            ok = (adx_v[i] >= adx_min) and (er[i] >= er_min)
            if ok and cv[i] > don_hi.values[i] and cv[i] > e[i]:
                side = 1
            elif ok and cv[i] < don_lo.values[i] and cv[i] < e[i]:
                side = -1
        out[i] = side
    return pd.Series(out, index=d.index)


def sig_vol_breakout(d, mult=3.0, exit_mult=1.2, base_n=336):
    a = d["atr"]
    a_base = a.rolling(base_n, min_periods=base_n).mean()
    cv, av, bv = d["close"].values, a.values, a_base.values
    n = len(d)
    out = np.zeros(n)
    side = 0
    entry = np.nan
    for i in range(1, n):
        if np.isnan(av[i]) or np.isnan(bv[i]) or bv[i] <= 0:
            out[i] = side
            continue
        expanding = av[i] > bv[i] * 1.05
        if side == 0:
            if expanding and cv[i] > cv[i - 1] + mult * av[i]:
                side, entry = 1, cv[i]
            elif expanding and cv[i] < cv[i - 1] - mult * av[i]:
                side, entry = -1, cv[i]
        elif side > 0:
            if cv[i] < entry - exit_mult * av[i] or cv[i] < cv[i - 1] - mult * av[i]:
                side, entry = 0, np.nan
            else:
                entry = max(entry, cv[i])
        else:
            if cv[i] > entry + exit_mult * av[i] or cv[i] > cv[i - 1] + mult * av[i]:
                side, entry = 0, np.nan
            else:
                entry = min(entry, cv[i])
        out[i] = side
    return pd.Series(out, index=d.index)


# ═══════════════════════════════════════════════════════════════════════════
# Engine
# ═══════════════════════════════════════════════════════════════════════════
@dataclass
class Trade:
    asset: str
    side: str
    entry_dt: str
    entry_px: float
    exit_dt: str = ""
    exit_px: float = float("nan")
    reason: str = ""
    ret: float = float("nan")
    pnl_usdt: float = float("nan")
    notional: float = float("nan")
    bars_held: int = 0


def run_backtest(frames, side_sig, capital=100.0, trade_from=None,
                 notional_frac=0.25, use_stop=False,
                 atr_stop_mult=3.0, atr_trail_mult=3.0,
                 cooldown=0, lev_cap=1.0):
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

    dti = pd.DatetimeIndex(dts)
    if dti.tz is None:
        dti = dti.tz_localize("UTC")
    start_i = 1
    if trade_from is not None:
        tf = pd.Timestamp(trade_from)
        if tf.tzinfo is None:
            tf = tf.tz_localize("UTC")
        else:
            tf = tf.tz_convert("UTC")
        hits = np.where(dti >= tf)[0]
        if len(hits):
            start_i = max(int(hits[0]), 1)

    cash = float(capital)
    units = {t: 0.0 for t in assets}
    stop = {t: np.nan for t in assets}
    side = {t: 0 for t in assets}
    cool_until = {t: -1 for t in assets}
    cur = {t: None for t in assets}
    eq = np.full(n, np.nan)
    eq[:start_i] = capital
    costs_arr = np.zeros(n)
    fund_arr = np.zeros(n)
    exposure = np.zeros(n)
    n_pos = np.zeros(n, dtype=int)
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
        units[t] = 0.0
        stop[t] = np.nan
        side[t] = 0
        if reason == "stop":
            cool_until[t] = i + cooldown

    def open_pos(t, i, new_side, px, equity_now):
        nonlocal cash
        a = ATR[t][i - 1] if i > 0 else ATR[t][i]
        if px <= 0:
            return
        abs_units = (notional_frac * equity_now) / px
        current_notional = sum(abs(units[x]) * C[x][i - 1] for x in assets)
        room = max(0.0, lev_cap * equity_now - current_notional)
        abs_units = min(abs_units, room / px if px > 0 else 0.0)
        if abs_units * px < max(1.0, 0.02 * capital):
            return
        signed = abs_units if new_side > 0 else -abs_units
        cost = abs_units * px * cost_ps
        cash -= signed * px + cost
        costs_arr[i] += cost
        units[t] = signed
        side[t] = int(np.sign(signed))
        stop_dist = atr_stop_mult * a if (use_stop and not np.isnan(a) and a > 0) else np.nan
        stop[t] = (px - side[t] * stop_dist) if use_stop and not np.isnan(stop_dist) else np.nan
        cur[t] = dict(
            side="long" if signed > 0 else "short",
            entry_dt=str(pd.Timestamp(dts[i])), entry_px=float(px),
            entry_i=i, notional=float(abs_units * px),
        )

    for i in range(start_i, n):
        for t in assets:
            if units[t] == 0:
                continue
            if use_stop and not np.isnan(stop[t]):
                a_prev = ATR[t][i - 1]
                if not np.isnan(a_prev) and a_prev > 0:
                    if units[t] > 0:
                        trail = C[t][i - 1] - atr_trail_mult * a_prev
                        stop[t] = max(stop[t], trail)
                    else:
                        trail = C[t][i - 1] + atr_trail_mult * a_prev
                        stop[t] = min(stop[t], trail)
                hit, fill = False, O[t][i]
                if units[t] > 0 and L[t][i] <= stop[t]:
                    fill, hit = min(O[t][i], stop[t]), True
                elif units[t] < 0 and H[t][i] >= stop[t]:
                    fill, hit = max(O[t][i], stop[t]), True
                if hit:
                    flatten(t, i, fill, "stop")
                    continue
            des = int(np.sign(SIG[t][i - 1])) if not np.isnan(SIG[t][i - 1]) else 0
            if des == 0 and side[t] != 0:
                flatten(t, i, O[t][i], "channel_exit" if not use_stop else "signal_exit")
            elif des != 0 and des != side[t]:
                flatten(t, i, O[t][i], "reverse")

        equity_now = mtm(i - 1)
        n_open = sum(1 for t in assets if units[t] != 0)
        for t in assets:
            if units[t] != 0 or i < cool_until[t]:
                continue
            if n_open >= len(assets):
                break
            des = int(np.sign(SIG[t][i - 1])) if not np.isnan(SIG[t][i - 1]) else 0
            if des == 0:
                continue
            open_pos(t, i, des, O[t][i], equity_now)
            if units[t] != 0:
                n_open += 1

        if hours[i] % FUNDING_HOURS == 0:
            for t in assets:
                if units[t] == 0:
                    continue
                rate = FUNDING_RATE.get(t, FUNDING_DEFAULT)
                f = units[t] * C[t][i] * rate
                cash -= f
                fund_arr[i] += f

        eq[i] = mtm(i)
        exposure[i] = (sum(abs(units[t]) * C[t][i] for t in assets) / eq[i]
                       if eq[i] > 0 else 0.0)
        n_pos[i] = sum(1 for t in assets if units[t] != 0)

    last = n - 1
    for t in assets:
        if units[t] != 0:
            flatten(t, last, C[t][last], "eod")
    eq[last] = mtm(last)
    eq = pd.Series(eq).ffill().bfill().values
    return dict(equity=eq, costs=costs_arr, funding=fund_arr, exposure=exposure,
                n_pos=n_pos, dts=dts, trades=trades, start_i=start_i, capital=capital)


# ═══════════════════════════════════════════════════════════════════════════
# Metrics
# ═══════════════════════════════════════════════════════════════════════════
def max_drawdown(eq):
    peak = np.maximum.accumulate(eq)
    return float(np.nanmin(eq / np.where(peak > 0, peak, np.nan) - 1.0))


def stats(eq, dts, capital, label=""):
    eq = np.asarray(eq, float)
    r = np.diff(eq) / np.where(eq[:-1] == 0, np.nan, eq[:-1])
    r = np.nan_to_num(r, nan=0.0)
    n = len(r)
    years = n / HOURS_PER_YEAR
    total_return = float(eq[-1] / eq[0] - 1.0)
    cagr = float((eq[-1] / eq[0]) ** (1 / years) - 1.0) if years > 0 and eq[0] > 0 else float("nan")
    vol = float(np.std(r, ddof=1) * np.sqrt(HOURS_PER_YEAR))
    sharpe = float(np.mean(r) / np.std(r, ddof=1) * np.sqrt(HOURS_PER_YEAR)) if np.std(r, ddof=1) > 0 else 0.0
    downside = r[r < 0]
    sortino = float(np.mean(r) / (np.std(downside, ddof=1) + 1e-12) * np.sqrt(HOURS_PER_YEAR)) if len(downside) > 1 else float("nan")
    mdd = max_drawdown(eq)
    calmar = (cagr / abs(mdd)) if mdd < 0 else float("nan")
    s = pd.Series(eq, index=pd.DatetimeIndex(dts[: len(eq)]))
    monthly = s.resample("ME").last().pct_change().dropna()
    return dict(
        label=label, bars=int(n), years=round(years, 3),
        start=str(pd.Timestamp(dts[0])), end=str(pd.Timestamp(dts[len(eq) - 1])),
        start_equity=float(eq[0]), end_equity=float(eq[-1]),
        total_return=total_return, cagr=cagr, ann_vol=vol,
        sharpe=sharpe, sortino=sortino, max_dd=mdd, calmar=float(calmar),
        pct_positive_hours=float((r > 0).mean()),
        monthly_win_rate=float((monthly > 0).mean()) if len(monthly) else float("nan"),
        n_months=int(len(monthly)),
        best_month=float(monthly.max()) if len(monthly) else float("nan"),
        worst_month=float(monthly.min()) if len(monthly) else float("nan"),
        monthly={str(k.date()): round(float(v), 4) for k, v in monthly.items()},
    )


def trade_stats(trades: List[Trade]) -> dict:
    if not trades:
        return dict(n=0)
    rets = np.array([t.ret for t in trades], float)
    pnls = np.array([t.pnl_usdt for t in trades], float)
    wins = rets > 0
    gp, gl = pnls[pnls > 0].sum(), -pnls[pnls < 0].sum()
    by_reason, by_asset = {}, {}
    for t in trades:
        by_reason[t.reason] = by_reason.get(t.reason, 0) + 1
        by_asset[t.asset] = by_asset.get(t.asset, 0) + 1
    hold = np.array([t.bars_held for t in trades], float)
    return dict(
        n=int(len(trades)), win_rate=float(wins.mean()),
        avg_ret=float(rets.mean()), med_ret=float(np.median(rets)),
        avg_win=float(rets[wins].mean()) if wins.any() else 0.0,
        avg_loss=float(rets[~wins].mean()) if (~wins).any() else 0.0,
        profit_factor=float(gp / gl) if gl > 0 else float("inf"),
        expectancy_usdt=float(pnls.mean()), total_pnl_usdt=float(pnls.sum()),
        avg_hold_hours=float(hold.mean()), med_hold_hours=float(np.median(hold)),
        long_n=int(sum(1 for t in trades if t.side == "long")),
        short_n=int(sum(1 for t in trades if t.side == "short")),
        by_reason=by_reason, by_asset=by_asset,
    )


def monte_carlo(trades, capital, n_sim=2000, seed=42, sleeve=0.25):
    if len(trades) < 8:
        return dict(n_sim=0)
    rng = np.random.default_rng(seed)
    rets = np.array([t.ret for t in trades], float)
    terminals, mdds = [], []
    for _ in range(n_sim):
        sample = rng.choice(rets, size=len(rets), replace=True)
        eq, peak, mdd = capital, capital, 0.0
        for r in sample:
            eq = eq * (1 + sleeve * r)
            peak = max(peak, eq)
            mdd = min(mdd, eq / peak - 1)
            if eq <= 0:
                eq = 0.0
                break
        terminals.append(eq)
        mdds.append(mdd)
    terminals, mdds = np.array(terminals), np.array(mdds)
    return dict(
        n_sim=n_sim,
        terminal_median=float(np.median(terminals)),
        terminal_p05=float(np.percentile(terminals, 5)),
        terminal_p95=float(np.percentile(terminals, 95)),
        p_lose=float((terminals < capital).mean()),
        mdd_median=float(np.median(mdds)),
        mdd_p05=float(np.percentile(mdds, 5)),
        mdd_worst=float(mdds.min()),
        p_mdd_gt_15=float((mdds < -0.15).mean()),
        p_mdd_gt_25=float((mdds < -0.25).mean()),
    )


def buy_hold(frames, capital, start_i):
    assets = list(frames.keys())
    n = len(next(iter(frames.values())))
    C = {t: frames[t]["close"].values for t in assets}
    px0 = {t: frames[t]["open"].values[start_i] for t in assets}
    per = capital / len(assets)
    units = {t: (per * (1 - FEE - SLIP)) / px0[t] for t in assets}
    eq = np.full(n, np.nan)
    for i in range(start_i, n):
        eq[i] = sum(units[t] * C[t][i] for t in assets)
    eq = pd.Series(eq).ffill().bfill().values
    eq[:start_i] = capital
    return eq


def fmt_pct(x, d=2):
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return "—"
    return f"{100 * x:+.{d}f}%"


def fmt_num(x, d=2):
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return "—"
    return f"{x:.{d}f}"


# ═══════════════════════════════════════════════════════════════════════════
# HTML report
# ═══════════════════════════════════════════════════════════════════════════
def write_html(path, profile, st, ts, mc, bh_st, ch_st, capital, costs, fund,
               eq, dts, exp, trades, multi, pcfg):
    step = max(1, len(eq) // 400)
    eq_ds = [float(x) for x in eq[::step]]
    exp_ds = [float(x) for x in exp[::step][: len(eq_ds)]]
    dti = pd.DatetimeIndex(dts[::step][: len(eq_ds)])
    dt0, dt1 = (str(dti[0])[:10] if len(dti) else ""), (str(dti[-1])[:10] if len(dti) else "")
    monthly_rows = "".join(
        f"<tr><td>{k}</td><td class={'pos' if v>=0 else 'neg'}>{v*100:+.2f}%</td></tr>"
        for k, v in st.get("monthly", {}).items()
    )
    trade_rows = "".join(
        f"<tr><td>{t.asset}</td><td>{t.side}</td><td>{t.entry_dt[:16]}</td>"
        f"<td>{t.exit_dt[:16]}</td><td class="
        f"{'pos' if t.ret>=0 else 'neg'}>{t.ret*100:+.2f}%</td>"
        f"<td class={'pos' if t.pnl_usdt>=0 else 'neg'}>{t.pnl_usdt:+.3f}</td>"
        f"<td>{t.reason}</td><td>{t.bars_held}</td></tr>"
        for t in trades[:150]
    )
    multi_rows = "".join(
        f"<tr><td>{lab}</td><td class="
        f"{'pos' if m['total_return']>=0 else 'neg'}>{fmt_pct(m['total_return'])}</td>"
        f"<td>{fmt_num(m['sharpe'])}</td><td>{fmt_pct(m['max_dd'])}</td>"
        f"<td>{m['n_trades']}</td><td>{m['end_equity']:.2f}</td></tr>"
        for lab, m in multi.items()
    )
    mdd_flag = "ok" if st["max_dd"] > -0.15 else "bad"
    html = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8"/>
<title>{pcfg['title']} — بک‌تست جایگزین</title>
<style>
:root {{
  --bg:#0b1020; --card:#141c2e; --line:#1e2a44; --txt:#e7eefc; --mut:#8b9bb8;
  --acc:#5b8cff; --pos:#3ddc97; --neg:#ff6b7a; --warn:#ffd166; --pur:#c084fc;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --sans: "Segoe UI", Tahoma, sans-serif;
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--txt);font:15px/1.6 var(--sans);padding:24px}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:16px;margin:22px 0 10px;color:var(--acc)}}
.sub{{color:var(--mut);font-size:13px;margin-bottom:16px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:14px}}
.k{{color:var(--mut);font-size:11px}}.v{{font-size:22px;font-weight:700;font-family:var(--mono);margin-top:4px}}
.pos{{color:var(--pos)}}.neg{{color:var(--neg)}}.ok{{color:var(--pos)}}.bad{{color:var(--neg)}}
.badge{{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;
  background:#1a2744;color:var(--pur);border:1px solid #3b2a66;margin-left:6px}}
table{{width:100%;border-collapse:collapse;font-size:12.5px}}
th,td{{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right}}
th{{color:var(--mut);font-weight:600;font-size:11px}}
.mono{{font-family:var(--mono)}}
canvas{{width:100%;height:220px;background:#0e1526;border-radius:10px;border:1px solid var(--line)}}
.note{{background:#121a2c;border-right:3px solid var(--warn);padding:10px 14px;color:var(--mut);
  font-size:13px;margin:12px 0;border-radius:0 8px 8px 0}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
@media(max-width:800px){{.two{{grid-template-columns:1fr}}}}
footer{{color:var(--mut);font-size:12px;margin-top:24px}}
ul{{color:var(--mut);padding-right:18px}}
</style>
</head>
<body>
<h1>{pcfg['title']}
  <span class="badge">≠ RCPE-1</span>
  <span class="badge">{profile}</span>
</h1>
<div class="sub">
  سرمایه <b>{capital:.0f} USDT</b> ·
  {st.get('start','')[:10]} → {st.get('end','')[:10]} ·
  {', '.join(pcfg['assets'])} ·
  هزینه واقعی: ۵bp + ۲bp/سمت + فاندینگ ۸ساعته ·
  سیگنال CLOSE → پر OPEN بعدی
</div>

<div class="grid">
  <div class="card"><div class="k">سرمایه پایانی</div>
    <div class="v {'pos' if st['end_equity']>=capital else 'neg'}">{st['end_equity']:.2f}</div></div>
  <div class="card"><div class="k">بازده ۶ ماه</div>
    <div class="v {'pos' if st['total_return']>=0 else 'neg'}">{fmt_pct(st['total_return'])}</div></div>
  <div class="card"><div class="k">Sharpe</div><div class="v">{fmt_num(st['sharpe'])}</div></div>
  <div class="card"><div class="k">MaxDD</div><div class="v {mdd_flag}">{fmt_pct(st['max_dd'])}</div></div>
  <div class="card"><div class="k">معاملات</div><div class="v">{ts.get('n',0)}</div></div>
  <div class="card"><div class="k">Win · PF</div>
    <div class="v" style="font-size:18px">{fmt_pct(ts.get('win_rate'),1)} · {fmt_num(ts.get('profit_factor'))}</div></div>
</div>

<h2>منحنی سرمایه (۱۰۰ USDT)</h2>
<canvas id="eq" width="900" height="220"></canvas>

<div class="two">
  <div class="card">
    <h2 style="margin-top:0">هزینه‌های واقعی پرداخت‌شده</h2>
    <table>
      <tr><td>کارمزد + اسلیپیج</td><td class="mono">{costs:.4f} USDT</td>
          <td class="mono">{100*costs/capital:.2f}%</td></tr>
      <tr><td>فاندینگ خالص</td><td class="mono">{fund:.4f} USDT</td>
          <td class="mono">{100*fund/capital:.2f}%</td></tr>
      <tr><td><b>جمع</b></td><td class="mono"><b>{costs+fund:.4f} USDT</b></td>
          <td class="mono"><b>{100*(costs+fund)/capital:.2f}%</b></td></tr>
    </table>
    <div class="note">۵ bp taker + ۲ bp slip در هر سمت (۱۴ bp رفت‌وبرگشت) +
    فاندینگ هر ۸ ساعت با میانگین واقعی OKX. بدون اهرم (lev_cap=1.0).</div>
  </div>
  <div class="card">
    <h2 style="margin-top:0">مقایسه همان ۶ ماه</h2>
    <table>
      <tr><th></th><th>این استراتژی</th><th>قهرمان RCPE-1</th><th>Buy&amp;Hold</th></tr>
      <tr><td>بازده</td>
        <td class="mono {'pos' if st['total_return']>=0 else 'neg'}">{fmt_pct(st['total_return'])}</td>
        <td class="mono">{fmt_pct(ch_st.get('total_return'))}</td>
        <td class="mono">{fmt_pct(bh_st.get('total_return'))}</td></tr>
      <tr><td>Sharpe</td>
        <td class="mono">{fmt_num(st['sharpe'])}</td>
        <td class="mono">{fmt_num(ch_st.get('sharpe'))}</td>
        <td class="mono">{fmt_num(bh_st.get('sharpe'))}</td></tr>
      <tr><td>MaxDD</td>
        <td class="mono">{fmt_pct(st['max_dd'])}</td>
        <td class="mono">{fmt_pct(ch_st.get('max_dd'))}</td>
        <td class="mono">{fmt_pct(bh_st.get('max_dd'))}</td></tr>
      <tr><td>هزینه ≈</td>
        <td class="mono">{costs:.2f}</td>
        <td class="mono">{ch_st.get('costs', float('nan')):.2f}</td>
        <td class="mono">یک‌بار ورود</td></tr>
    </table>
  </div>
</div>

<h2>پایداری روی چند بازه (ضد بیش‌برازش)</h2>
<div class="card">
<table>
  <tr><th>بازه</th><th>بازده</th><th>Sharpe</th><th>MaxDD</th><th>معاملات</th><th>پایان $</th></tr>
  {multi_rows}
</table>
<div class="note">انتخاب استراتژی بر اساس <b>نمونهٔ کامل</b> انجام شد (نه قلهٔ ۶ماهه).
بازهٔ ۶ ماه برای گزارش درخواستی شماست و می‌تواند نویز داشته باشد.</div>
</div>

<div class="two">
  <div class="card">
    <h2 style="margin-top:0">چرا ≠ قهرمان RCPE-1؟</h2>
    <ul>
      <li><b>سیگنال:</b> شکست Donchian گسسته — نه blend سه sleeve پیوسته.</li>
      <li><b>سایز:</b> کسر ثابت equity — نه هدف‌نوسان روزانه (vol-target).</li>
      <li><b>خروج:</b> فقط کانال Donchian کوتاه — بدون daily-loss-limit.</li>
      <li><b>یونیورس:</b> {', '.join(pcfg['assets'])}.</li>
      <li><b>استاپ ATR:</b> {'فعال' if pcfg['use_stop'] else 'خاموش (عمداً)'}.</li>
    </ul>
  </div>
  <div class="card">
    <h2 style="margin-top:0">Monte Carlo ({mc.get('n_sim',0)} sim)</h2>
    <table>
      <tr><td>Terminal median</td><td class="mono">{mc.get('terminal_median', float('nan')):.2f}</td></tr>
      <tr><td>p05 / p95</td><td class="mono">{mc.get('terminal_p05', float('nan')):.2f} / {mc.get('terminal_p95', float('nan')):.2f}</td></tr>
      <tr><td>P(ضرر)</td><td class="mono">{fmt_pct(mc.get('p_lose'),1)}</td></tr>
      <tr><td>MDD median</td><td class="mono">{fmt_pct(mc.get('mdd_median'))}</td></tr>
      <tr><td>P(MDD&gt;15%)</td><td class="mono">{fmt_pct(mc.get('p_mdd_gt_15'),1)}</td></tr>
    </table>
  </div>
</div>

<h2>بازده ماهانه (۶ ماه)</h2>
<div class="card"><table><tr><th>ماه</th><th>بازده</th></tr>{monthly_rows}</table></div>

<h2>معاملات</h2>
<div class="card" style="overflow:auto"><table>
<tr><th>دارایی</th><th>سمت</th><th>ورود</th><th>خروج</th><th>بازده</th><th>PnL</th><th>دلیل</th><th>h</th></tr>
{trade_rows}
</table></div>

<div class="note">
<b>هشدار صادقانه:</b>
این استراتژی روی full-sample Sharpe پایین‌تری از قهرمان RCPE-1 دارد (۰.۸۰ در برابر ۱.۳۳)
و MaxDD تمام‌نمونه حدود −۳۰٪ است — خارج از سقف محافظه‌کارانهٔ ۱۵٪ کاربر برای افق چندساله.
روی ۶ ماه اخیر بهتر از قهرمان ظاهر شده، اما ۶ ماه نمونهٔ کوچکی است.
برای سرمایهٔ واقعی، قهرمان RCPE-1 همچنان انتخاب اصلی اعتبارسنجی‌شده است؛
این فایل یک <b>آلترناتیو اکتشافی متفاوت</b> است.
</div>

<footer>alt_turtle_ns.py · Arena Agent · OKX 1H perp · VERIFY_UNTIL={VERIFY_UNTIL}</footer>
<script>
(function(){{
  const eq={json.dumps(eq_ds)}, exp={json.dumps(exp_ds)}, cap={capital};
  const cv=document.getElementById('eq'), ctx=cv.getContext('2d');
  const W=cv.width,H=cv.height,pad=28;
  const min=Math.min(...eq,cap), max=Math.max(...eq,cap), span=(max-min)||1;
  const x=i=>pad+i*(W-2*pad)/Math.max(1,eq.length-1);
  const y=v=>H-pad-(v-min)/span*(H-2*pad);
  ctx.strokeStyle='#1e2a44';
  for(let g=0;g<5;g++){{const yy=pad+g*(H-2*pad)/4;ctx.beginPath();ctx.moveTo(pad,yy);ctx.lineTo(W-pad,yy);ctx.stroke();}}
  ctx.fillStyle='rgba(192,132,252,0.12)';ctx.beginPath();ctx.moveTo(x(0),H-pad);
  exp.forEach((e,i)=>ctx.lineTo(x(i),H-pad-Math.min(1,e||0)*(H-2*pad)*0.35));
  ctx.lineTo(x(exp.length-1),H-pad);ctx.closePath();ctx.fill();
  ctx.strokeStyle=eq[eq.length-1]>=eq[0]?'#3ddc97':'#ff6b7a';ctx.lineWidth=2;ctx.beginPath();
  eq.forEach((v,i)=>i?ctx.lineTo(x(i),y(v)):ctx.moveTo(x(i),y(v)));ctx.stroke();
  ctx.strokeStyle='#ffd166';ctx.setLineDash([4,4]);ctx.beginPath();
  ctx.moveTo(pad,y(cap));ctx.lineTo(W-pad,y(cap));ctx.stroke();ctx.setLineDash([]);
  ctx.fillStyle='#8b9bb8';ctx.font='11px monospace';
  ctx.fillText(max.toFixed(1),4,pad+4);ctx.fillText(min.toFixed(1),4,H-pad);
  ctx.fillText('{dt0}',pad,H-8);ctx.fillText('{dt1}',W-pad-90,H-8);
}})();
</script>
</body></html>"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def champion_ref(trade_from, capital):
    try:
        import champion_strategy as ch
        tags = list(ch.ASSETS)
        frames = {}
        for t in tags:
            raw = ch.load_csv(t, until=ch.VERIFY_UNTIL)
            d = ch.build_regime(raw)
            d["atr"] = ch.atr(d["high"], d["low"], d["close"], 14)
            frames[t] = d
        common = None
        for t, d in frames.items():
            s = set(d["dt"].tolist())
            common = s if common is None else common & s
        common = sorted(common)
        for t in tags:
            frames[t] = frames[t][frames[t]["dt"].isin(common)].reset_index(drop=True)
        port = ch.run_portfolio(frames, ch.build_cfg("balanced"))
        eq = np.asarray(port["eq"], float)
        dt = pd.DatetimeIndex(port["dt"])
        if dt.tz is None:
            dt = dt.tz_localize("UTC")
        tf = pd.Timestamp(trade_from)
        if tf.tzinfo is None:
            tf = tf.tz_localize("UTC")
        mask = dt >= tf
        sub = eq[mask] / eq[mask][0] * capital
        st = stats(sub, dt.values[mask], capital, "champion_6m")
        st["costs"] = float(np.asarray(port["costs"])[mask].sum() * capital)
        st["funding"] = float(np.asarray(port["funding"])[mask].sum() * capital)
        tr = port["trades"].copy()
        tr["entry_dt"] = pd.to_datetime(tr["entry_dt"], utc=True)
        st["n_trades"] = int((tr["entry_dt"] >= tf).sum())
        return st
    except Exception as e:
        return dict(error=str(e))


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════
def run_profile(profile: str, capital: float, trade_from, until=VERIFY_UNTIL):
    p = dict(PROFILES[profile])
    frames = load_panel(p["assets"], until=until)
    if p["family"] == "turtle":
        sides = {t: sig_turtle_ns(frames[t], p["entry_n"], p["exit_n"],
                                  p["adx_min"], p["er_min"]) for t in frames}
    else:
        sides = {t: sig_vol_breakout(frames[t], p.get("vb_mult", 3.0),
                                     p.get("vb_exit", 1.2), p.get("vb_base", 336))
                 for t in frames}
    res = run_backtest(
        frames, sides, capital=capital, trade_from=trade_from,
        notional_frac=p["notional_frac"], use_stop=p["use_stop"],
        atr_stop_mult=p["atr_stop_mult"], atr_trail_mult=p["atr_trail_mult"],
        cooldown=p["cooldown"],
    )
    return res, frames, p


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=100.0)
    ap.add_argument("--months", type=float, default=6.0)
    ap.add_argument("--full-sample", action="store_true")
    ap.add_argument("--profile", default=DEFAULT_PROFILE, choices=list(PROFILES))
    ap.add_argument("--both", action="store_true", help="run turtle_ns + volbreak")
    ap.add_argument("--mc-sims", type=int, default=2000)
    ap.add_argument("--until", default=VERIFY_UNTIL)
    args = ap.parse_args(argv)

    profiles = list(PROFILES) if args.both else [args.profile]
    os.makedirs(RESULTS, exist_ok=True)

    # determine last date from BTC
    btc = load_csv("BTC", until=args.until)
    last_dt = pd.Timestamp(btc["dt"].iloc[-1])
    if args.full_sample:
        trade_from = pd.Timestamp("2021-08-01", tz="UTC")
        window_lab = "FULL"
    else:
        trade_from = last_dt - pd.Timedelta(days=int(args.months * 30.44))
        window_lab = f"L{args.months:.0f}M"

    all_summaries = []
    for profile in profiles:
        print(f"\n{'='*72}\n  Running profile={profile}  capital={args.capital}  "
              f"from {trade_from.date()}  ({window_lab})\n{'='*72}", flush=True)
        res, frames, pcfg = run_profile(profile, args.capital, trade_from, args.until)
        si = res["start_i"]
        eq = res["equity"][si:]
        dts = res["dts"][si:]
        exp = res["exposure"][si:]
        costs = float(res["costs"][si:].sum())
        fund = float(res["funding"][si:].sum())
        st = stats(eq, dts, args.capital, window_lab)
        ts = trade_stats(res["trades"])
        mc = monte_carlo(res["trades"], args.capital, n_sim=args.mc_sims,
                         sleeve=pcfg["notional_frac"])

        # multi-window
        multi = {}
        for lab, tf in [("6M", last_dt - pd.Timedelta(days=int(6 * 30.44))),
                        ("1Y", last_dt - pd.Timedelta(days=365)),
                        ("FULL", pd.Timestamp("2021-08-01", tz="UTC"))]:
            r2, _, _ = run_profile(profile, args.capital, tf, args.until)
            s2 = r2["start_i"]
            st2 = stats(r2["equity"][s2:], r2["dts"][s2:], args.capital, lab)
            ts2 = trade_stats(r2["trades"])
            multi[lab] = dict(
                total_return=st2["total_return"], sharpe=st2["sharpe"],
                max_dd=st2["max_dd"], n_trades=ts2.get("n", 0),
                end_equity=st2["end_equity"], costs=float(r2["costs"][s2:].sum()),
            )

        bh_eq = buy_hold(frames, args.capital, si)
        bh_st = stats(bh_eq[si:], dts, args.capital, "B&H")
        ch_st = champion_ref(trade_from if not args.full_sample else
                             (last_dt - pd.Timedelta(days=int(6 * 30.44))),
                             args.capital)

        # console
        print(f"  {pcfg['title']}")
        print(f"  assets     : {', '.join(pcfg['assets'])}")
        print(f"  equity end : {st['end_equity']:.2f} USDT")
        print(f"  return     : {fmt_pct(st['total_return'])}   CAGR {fmt_pct(st['cagr'])}")
        print(f"  Sharpe     : {fmt_num(st['sharpe'])}   Sortino {fmt_num(st['sortino'])}")
        print(f"  MaxDD      : {fmt_pct(st['max_dd'])}   Calmar {fmt_num(st['calmar'])}")
        print(f"  trades     : {ts.get('n',0)}   win {fmt_pct(ts.get('win_rate'),1)}   "
              f"PF {fmt_num(ts.get('profit_factor'))}")
        print(f"  hold (h)   : avg {fmt_num(ts.get('avg_hold_hours'),1)}   "
              f"long/short {ts.get('long_n',0)}/{ts.get('short_n',0)}")
        print(f"  exits      : {ts.get('by_reason',{})}")
        print(f"  costs paid : fee+slip {costs:.4f} + funding {fund:.4f} = {costs+fund:.4f} USDT")
        print(f"  B&H same   : {fmt_pct(bh_st['total_return'])}  Sh {fmt_num(bh_st['sharpe'])}  "
              f"MDD {fmt_pct(bh_st['max_dd'])}")
        if ch_st and "total_return" in ch_st:
            print(f"  Champion 6M: {fmt_pct(ch_st['total_return'])}  Sh {fmt_num(ch_st['sharpe'])}  "
                  f"MDD {fmt_pct(ch_st['max_dd'])}  cost≈{ch_st.get('costs',0):.2f}")
        print(f"  multi-window:")
        for lab, m in multi.items():
            print(f"    {lab:4s}  ret {fmt_pct(m['total_return'])}  Sh {fmt_num(m['sharpe'])}  "
                  f"MDD {fmt_pct(m['max_dd'])}  n={m['n_trades']}")
        if mc.get("n_sim"):
            print(f"  MC         : P(lose) {fmt_pct(mc['p_lose'],1)}  "
                  f"P(MDD>15%) {fmt_pct(mc['p_mdd_gt_15'],1)}  "
                  f"terminal med {mc['terminal_median']:.2f}")

        # save
        tag = profile
        eq_path = os.path.join(RESULTS, f"alt_{tag}_equity.csv")
        tr_path = os.path.join(RESULTS, f"alt_{tag}_trades.csv")
        pd.DataFrame(dict(
            dt=pd.DatetimeIndex(dts), equity=eq, exposure=exp,
            costs=res["costs"][si:], funding=res["funding"][si:],
        )).to_csv(eq_path, index=False)
        pd.DataFrame([asdict(t) for t in res["trades"]]).to_csv(tr_path, index=False)
        html_path = os.path.join(RESULTS, f"alt_{tag}_report.html")
        write_html(html_path, profile, st, ts, mc, bh_st, ch_st or {},
                   args.capital, costs, fund, eq, dts, exp, res["trades"], multi, pcfg)
        summary = dict(
            profile=profile, title=pcfg["title"], params=pcfg,
            capital=args.capital, window=window_lab,
            trade_from=str(trade_from), trade_to=str(last_dt),
            performance=st, trades=ts, monte_carlo=mc,
            costs_usdt=dict(fee_slip=costs, funding=fund, total=costs + fund),
            multi_window=multi, buy_hold=bh_st, champion_6m=ch_st,
            files=dict(equity=eq_path, trades=tr_path, html=html_path),
            distinct_from_champion=[
                "discrete Donchian positions (not continuous 3-sleeve blend)",
                "fixed notional fraction (not daily vol-target)",
                "channel exit only / ATR vol-breakout (not daily-loss-limit exits)",
                f"universe {list(pcfg['assets'])}",
            ],
        )
        jpath = os.path.join(RESULTS, f"alt_{tag}_results.json")
        with open(jpath, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, default=str, ensure_ascii=False)
        print(f"  [saved] {eq_path}\n  [saved] {tr_path}\n  [saved] {html_path}\n  [saved] {jpath}")
        all_summaries.append(summary)

    # combined index html
    if len(all_summaries) >= 1:
        idx = os.path.join(RESULTS, "alt_strategies_report.html")
        links = "".join(
            f"<li><a style='color:#5b8cff' href='{os.path.basename(s['files']['html'])}'>"
            f"{s['title']}</a> — ۶م {fmt_pct(s['performance']['total_return'])} · "
            f"Sh {fmt_num(s['performance']['sharpe'])} · "
            f"MDD {fmt_pct(s['performance']['max_dd'])} · "
            f"پایان {s['performance']['end_equity']:.2f} USDT</li>"
            for s in all_summaries
        )
        # inline the primary report content by copy
        primary = all_summaries[0]
        # rewrite primary html path as the index too (copy)
        import shutil
        shutil.copy(primary["files"]["html"], idx)
        print(f"  [index] {idx}")
    return all_summaries


if __name__ == "__main__":
    main()
