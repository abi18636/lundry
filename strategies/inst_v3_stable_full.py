#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
════════════════════════════════════════════════════════════════════════════════
 institutional-v3 STABLE — complete standalone strategy code
════════════════════════════════════════════════════════════════════════════════

Command this file replaces:
  python3 institutional_v3.py --profile stable

Definition
  70% LEG_PRIMARY  +  30% LEG_BROAD   (equity-curve blend)

  Both legs = discrete multi-horizon TSMOM (CTA DNA), long-only, vol-target 12%
  BTC / ETH / SOL perpetual 1h

  PRIMARY: horizons (24,168,720) · vote≥0.67 · confirm 5 · ADX≥22 · ER≥0.10 · exit 0.20
  BROAD:   horizons (24,168,720) · vote≥0.50 · confirm 5 · ADX≥22 · ER≥0.08 · exit 0.20

Published ($100, fee 5bp + slip 2bp / side + funding, lev≤1)
  IS    +34.3%  Sh 1.40  MDD −4.4%
  OOS1  +11.0%  Sh 1.73  MDD −2.9%   ← selection window
  OOS2  +8.4%   Sh 1.73  MDD −4.3%
  6M    +9.4%   Sh 1.94  MDD −3.6%
  FULL  +61.6%  Sh 1.49  MDD −5.0%   end 161.72

This file is SELF-CONTAINED (no import of institutional_* / alt_* required)
except standard libs + numpy/pandas. Data expected at ./data/*_USDT_SWAP_1H.csv

Usage
  python3 institutional_v3_stable_full.py
  python3 institutional_v3_stable_full.py --capital 100
  python3 institutional_v3_stable_full.py --self-test

Honest note: win-rate ~35-45% (trend-follow). NOT zero-error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from copy import deepcopy
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
RESULTS = os.path.join(HERE, "results")
os.makedirs(RESULTS, exist_ok=True)

# ── constants (match live lab) ──────────────────────────────────────────────
FEE = 0.0005          # 5 bp
SLIP = 0.0002         # 2 bp
COST_PS = FEE + SLIP  # 7 bp / side
FUNDING_HOURS = 8
FUNDING_DEFAULT = 3e-5
FUNDING_RATE = {
    "BTC": 4.6e-5, "ETH": 3.4e-5, "SOL": 2.46e-5,
    "XRP": 2.66e-5, "DOGE": 5.64e-5,
}
HOURS_PER_YEAR = 24 * 365
VERIFY_UNTIL = "2026-09-15 20:00"

FULL_START = pd.Timestamp("2021-08-01", tz="UTC")
IS_END = pd.Timestamp("2024-12-31 23:00", tz="UTC")
OOS1_START = pd.Timestamp("2025-01-01", tz="UTC")
OOS2_START = pd.Timestamp("2026-03-17", tz="UTC")
ASSETS = ("BTC", "ETH", "SOL")

STRATEGY_NAME = "institutional-v3-stable"
CAP_DEFAULT = 100.0


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


def true_range(h, l, c):
    pc = c.shift(1)
    return pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)



def rma(s, n):
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()



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



def ema(s, n):
    return s.ewm(span=n, adjust=False, min_periods=n).mean()



def kaufman_er(close, n=48):
    change = (close - close.shift(n)).abs()
    path = close.diff().abs().rolling(n, min_periods=n).sum()
    return (change / path.replace(0, np.nan)).fillna(0.0)



def max_drawdown(eq):
    peak = np.maximum.accumulate(eq)
    return float(np.nanmin(eq / np.where(peak > 0, peak, np.nan) - 1.0))



def fmt_pct(x, d=2):
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return "—"
    return f"{100 * x:+.{d}f}%"



def fmt_num(x, d=2):
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return "—"
    return f"{x:.{d}f}"



# ── data IO ────────────────────────────────────────────────────────────────

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



# ── backtest engine (run_enhanced) ───────────────────────────────────────

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



# ── TSMOM discrete signal (CTA) ──────────────────────────────────────────

def _tz(x):
    idx = pd.DatetimeIndex(x)
    if idx.tz is None:
        return idx.tz_localize("UTC")
    return idx.tz_convert("UTC")


def sig_tsmom_discrete(
    frames: Dict[str, pd.DataFrame],
    horizons: Tuple[int, ...] = (168, 336, 720),
    vote_min: float = 0.34,
    confirm: int = 3,
    adx_min: float = 18.0,
    er_min: float = 0.10,
    long_only: bool = True,
    lag: int = 1,
    exit_vote: float = 0.0,
) -> Dict[str, pd.Series]:
    """
    Classic CTA discrete TSMOM:
      vote = mean(sign(ret_h)) across horizons ∈ [-1,1]
      enter long when vote >= vote_min for `confirm` bars & ADX/ER ok
      exit when vote <= exit_vote (hysteresis) or quality dies
    """
    out = {}
    for a, df in frames.items():
        c = df["close"].astype(float).values
        n = len(c)
        adx_v = df["adx"].astype(float).values if "adx" in df.columns else np.zeros(n)
        er_v = df["er"].astype(float).values if "er" in df.columns else np.ones(n)
        votes = np.zeros(n)
        for h in horizons:
            r = np.zeros(n)
            r[h:] = c[h:] / c[:-h] - 1.0
            votes += np.sign(r)
        votes /= max(len(horizons), 1)

        raw = np.zeros(n)
        # hysteresis state machine
        pos = 0
        run_up = 0
        run_dn = 0
        for i in range(n):
            v = votes[i]
            q = (adx_v[i] >= adx_min) and (er_v[i] >= er_min)
            if pos == 0:
                if q and v >= vote_min:
                    run_up += 1
                    run_dn = 0
                    if run_up >= confirm:
                        pos = 1
                elif (not long_only) and q and v <= -vote_min:
                    run_dn += 1
                    run_up = 0
                    if run_dn >= confirm:
                        pos = -1
                else:
                    run_up = run_dn = 0
            elif pos == 1:
                if (v <= exit_vote) or (not q and v < vote_min * 0.5):
                    pos = 0
                    run_up = run_dn = 0
                elif (not long_only) and q and v <= -vote_min:
                    run_dn += 1
                    if run_dn >= confirm:
                        pos = -1
                        run_up = 0
                else:
                    run_dn = 0
            else:  # short
                if long_only or v >= -exit_vote or (not q and v > -vote_min * 0.5):
                    pos = 0
                    run_up = run_dn = 0
                elif q and v >= vote_min:
                    run_up += 1
                    if run_up >= confirm:
                        pos = 1
                        run_dn = 0
                else:
                    run_up = 0
            raw[i] = pos

        sig = np.zeros(n)
        sig[lag:] = raw[:-lag]
        out[a] = pd.Series(sig, index=df.index)
    return out



def prepare_frames(assets=ASSETS) -> Dict[str, pd.DataFrame]:
    frames = load_panel(assets)
    out = {}
    for a, df in frames.items():
        d = df.copy()
        d["dt"] = _tz(pd.to_datetime(d["dt"], utc=True))
        if "er" not in d.columns:
            d["er"] = kaufman_er(d["close"].astype(float), 48)
        out[a] = d.reset_index(drop=True)
    return out


def make_signal(cfg: dict, frames: Dict[str, pd.DataFrame]) -> Dict[str, pd.Series]:
    fr = {a: frames[a] for a in cfg["assets"] if a in frames}
    if cfg["kind"] != "tsmom":
        raise ValueError(cfg["kind"])
    return sig_tsmom_discrete(
        fr,
        horizons=tuple(cfg["horizons"]),
        vote_min=cfg["vote_min"],
        confirm=cfg["confirm"],
        adx_min=cfg["adx_min"],
        er_min=cfg["er_min"],
        long_only=cfg.get("long_only", True),
        exit_vote=cfg.get("exit_vote", 0.0),
    )


def bt(frames, side, *, capital=100.0, trade_from=None, trade_to=None, ek=None):
    ek = dict(ek or {})
    defaults = dict(
        sizing="vol_target", vol_target=0.12, notional_frac=0.42,
        max_positions=3, lev_cap=1.0, short_frac_mult=0.0,
        pyramid_levels=0, use_atr_stop=False, atr_stop_mult=99.0,
        atr_trail_mult=99.0, cooldown=4, risk_per_trade=0.01,
        atr_risk_mult=2.5, vol_lookback=336, pyramid_step_atr=1.0,
        pyramid_scale=0.5, breakeven_at_R=0.0, time_stop_bars=0,
        top_k=0, top_k_lookback=72,
    )
    defaults.update(ek)
    return run_enhanced(
        frames, side, capital=capital,
        trade_from=trade_from, trade_to=trade_to, **defaults,
    )


def equity_series(port) -> pd.Series:
    eq = port.get("equity", port.get("eq"))
    dts = port.get("dts", port.get("dt"))
    s = pd.Series(np.asarray(eq, float), index=_tz(pd.to_datetime(dts, utc=True)))
    return s.astype(float)


def window_metrics(eq: pd.Series, capital: float = 100.0) -> dict:
    eq = eq.copy()
    eq.index = _tz(eq.index)
    eq = eq[eq.index >= FULL_START].dropna()

    def one(start, end=None):
        s = eq[eq.index >= start]
        if end is not None:
            s = s[s.index <= end]
        if len(s) < 24:
            return dict(ret=np.nan, sharpe=np.nan, mdd=np.nan, n=0, end=np.nan)
        arr = s.values.astype(float)
        arr = arr / arr[0] * capital
        st = stats(arr, s.index, capital)
        return dict(
            ret=float(st["total_return"]),
            sharpe=float(st["sharpe"]),
            mdd=float(st["max_dd"]),
            cagr=float(st["cagr"]),
            n=len(s),
            end=float(s.iloc[-1]),
        )

    end = eq.index.max()
    return {
        "IS": one(FULL_START, IS_END),
        "OOS1": one(OOS1_START, OOS2_START - pd.Timedelta(hours=1)),
        "OOS2": one(OOS2_START, None),
        "FULL": one(FULL_START, None),
        "6M": one(end - pd.Timedelta(days=180), None),
    }


def blend_equity(parts, capital=100.0) -> pd.Series:
    """Weighted equity-curve blend, UTC-aligned, rebased."""
    normed = []
    wsum = 0.0
    for w, s in parts:
        s = s.copy()
        s.index = _tz(s.index)
        s = s.dropna()
        s = s / float(s.iloc[0]) * capital
        normed.append((w, s))
        wsum += w
    idx = normed[0][1].index
    for _, s in normed[1:]:
        idx = idx.intersection(s.index)
    idx = idx.sort_values()
    acc = np.zeros(len(idx))
    for w, s in normed:
        acc += (w / wsum) * s.reindex(idx).ffill().values
    return pd.Series(acc, index=idx, name="stable_blend")


# ═══════════════════════════════════════════════════════════════════════════
# STABLE PROFILE = 70% PRIMARY + 30% BROAD
# ═══════════════════════════════════════════════════════════════════════════

LEG_PRIMARY = dict(
    tag="primary",
    kind="tsmom",
    horizons=(24, 168, 720),
    vote_min=0.67,       # full consensus of 3 horizons (all must agree directionally)
    confirm=5,           # 5 hourly bars confirmation
    adx_min=22.0,
    er_min=0.10,
    exit_vote=0.20,      # hysteresis exit
    long_only=True,
    ek=dict(
        sizing="vol_target", vol_target=0.12, notional_frac=0.42,
        max_positions=3, lev_cap=1.0, cooldown=4, short_frac_mult=0.0,
        use_atr_stop=False, atr_stop_mult=99.0, atr_trail_mult=99.0,
        breakeven_at_R=0.0, time_stop_bars=0,
    ),
    assets=ASSETS,
)

LEG_BROAD = dict(
    tag="broad",
    kind="tsmom",
    horizons=(24, 168, 720),
    vote_min=0.50,       # slightly looser consensus
    confirm=5,
    adx_min=22.0,
    er_min=0.08,
    exit_vote=0.20,
    long_only=True,
    ek=dict(
        sizing="vol_target", vol_target=0.12, notional_frac=0.42,
        max_positions=3, lev_cap=1.0, cooldown=4, short_frac_mult=0.0,
        use_atr_stop=False, atr_stop_mult=99.0, atr_trail_mult=99.0,
        breakeven_at_R=0.0, time_stop_bars=0,
    ),
    assets=ASSETS,
)

STABLE_WEIGHTS = (0.70, 0.30)   # primary, broad


def run_leg(cfg: dict, frames, capital: float = 100.0, trade_from=None) -> dict:
    cfg = deepcopy(cfg)
    side = make_signal(cfg, frames)
    fr = {a: frames[a] for a in cfg["assets"]}
    for a in cfg["assets"]:
        if a not in side:
            side[a] = pd.Series(0.0, index=frames[a].index)
    port = bt(fr, side, capital=capital, trade_from=trade_from or FULL_START, ek=cfg.get("ek"))
    eq = equity_series(port)
    costs = port.get("costs", 0.0)
    if isinstance(costs, np.ndarray):
        costs = float(np.nansum(costs))
    else:
        costs = float(costs or 0.0)
    trades = port.get("trades") or []
    return dict(
        equity=eq,
        costs=costs,
        funding=float(np.nansum(port.get("funding", np.zeros(1)))),
        trades=trades,
        trade_stats=trade_stats(trades) if trades else {"n": 0},
        cfg=cfg,
        end_equity=float(eq.dropna().iloc[-1]),
    )


def run_stable(capital: float = 100.0) -> dict:
    """Official stable profile: 70% primary + 30% broad equity blend."""
    frames = prepare_frames(ASSETS)
    r_p = run_leg(LEG_PRIMARY, frames, capital=capital)
    r_b = run_leg(LEG_BROAD, frames, capital=capital)
    wa, wb = STABLE_WEIGHTS
    eq = blend_equity([(wa, r_p["equity"]), (wb, r_b["equity"])], capital)
    win = window_metrics(eq, capital)
    costs = wa * r_p["costs"] + wb * r_b["costs"]
    fund = wa * r_p["funding"] + wb * r_b["funding"]
    trades = list(r_p["trades"]) + list(r_b["trades"])
    ts = trade_stats(trades) if trades else {"n": 0}

    # hard gates (selection protocol)
    hard = (
        win["OOS1"]["ret"] > 0
        and win["OOS1"]["sharpe"] >= 0.7
        and win["FULL"]["mdd"] >= -0.15
        and win["IS"]["sharpe"] >= 0.5
    )

    out = dict(
        name=STRATEGY_NAME,
        profile="stable",
        weights={"primary": wa, "broad": wb},
        capital=capital,
        windows=win,
        hard_pass=hard,
        end_equity=float(eq.dropna().iloc[-1]),
        costs_usdt=costs,
        funding_pnl=fund,
        n_trades=int(ts.get("n", 0)),
        trades=ts,
        legs=[LEG_PRIMARY, LEG_BROAD],
        protocol="IS observe / OOS1 select / OOS2+6M report-only / MDD<=15%",
        truth="No zero-error. Discrete CTA TSMOM blend. wr typically 35-45%.",
        published=dict(
            IS="+34.3% Sh1.40 MDD-4.4%",
            OOS1="+11.0% Sh1.73 MDD-2.9%",
            OOS2="+8.4% Sh1.73 MDD-4.3%",
            M6="+9.4% Sh1.94 MDD-3.6%",
            FULL="+61.6% Sh1.49 MDD-5.0% end161.72",
        ),
    )
    return out, eq, trades, r_p, r_b


def self_test() -> int:
    out, eq, tr, _, _ = run_stable(100.0)
    checks = [
        ("end>100", out["end_equity"] > 100),
        ("hard_pass", out["hard_pass"] is True),
        ("oos1_sh>=0.7", out["windows"]["OOS1"]["sharpe"] >= 0.7),
        ("full_mdd>=-15", out["windows"]["FULL"]["mdd"] >= -0.15),
        ("n_trades>20", out["n_trades"] > 20),
        ("eq_finite", np.isfinite(eq.dropna().values[-1])),
        ("long_only", all(getattr(t, "side", "long") != "short" for t in tr)),
    ]
    ok_n = sum(1 for _, o in checks if o)
    for n, o in checks:
        print(f"  [{'PASS' if o else 'FAIL'}] {n}")
    print(f"self-test {ok_n}/{len(checks)}")
    return 0 if ok_n == len(checks) else 1


def main():
    ap = argparse.ArgumentParser(description="institutional-v3 STABLE standalone")
    ap.add_argument("--capital", type=float, default=CAP_DEFAULT)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        sys.exit(self_test())

    out, eq, trades, r_p, r_b = run_stable(args.capital)
    eq.to_csv(os.path.join(RESULTS, "institutional_v3_stable_full_equity.csv"), header=["eq"])
    with open(os.path.join(RESULTS, "institutional_v3_stable_full_results.json"), "w") as f:
        json.dump(out, f, indent=2, default=str)
    if trades:
        pd.DataFrame([asdict(t) for t in trades]).to_csv(
            os.path.join(RESULTS, "institutional_v3_stable_full_trades.csv"), index=False
        )

    print("=" * 72)
    print(f"{STRATEGY_NAME}  hard_pass={out['hard_pass']}  end={out['end_equity']:.2f}")
    print("=" * 72)
    print(f"weights: primary {STABLE_WEIGHTS[0]*100:.0f}% + broad {STABLE_WEIGHTS[1]*100:.0f}%")
    for k, v in out["windows"].items():
        print(f"  {k:5s}  ret={v['ret']*100:+7.2f}%  Sh={v['sharpe']:6.2f}  MDD={v['mdd']*100:7.2f}%")
    ts = out["trades"]
    print(f"  costs={out['costs_usdt']:.2f}  n={out['n_trades']}  "
          f"wr={ts.get('win_rate', float('nan')):.1%}  PF={ts.get('profit_factor', float('nan')):.2f}")
    print("  legs separately:")
    print(f"    primary end={r_p['end_equity']:.2f}  broad end={r_b['end_equity']:.2f}")
    print("saved results/institutional_v3_stable_full_*.csv/json")


if __name__ == "__main__":
    main()
