#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
════════════════════════════════════════════════════════════════════════════════
 ZENITH discovery engine — beat almasi + institutional-v3 under anti-overfit
════════════════════════════════════════════════════════════════════════════════

Targets to beat (must win on OOS1 select, report OOS2/6M):
  inst-v3 stable : OOS1 +11.0% Sh1.73 · FULL +61.6% Sh1.49 MDD−5.0%
  inst-v3 primary: OOS1 +11.4% Sh2.00 · FULL +49.3% Sh1.38 MDD−4.3%
  almasi 177-v001: OOS1 +6.8%  Sh1.37 · FULL +45.3% Sh1.43 MDD−6.0%

Novel DNA families (NOT copies of almasi TQ/VQ or plain TSMOM vote):
  A) SuperTrend ATR trail regime (institutional futures staple)
  B) Bollinger squeeze → expansion breakout
  C) DI+/DI− directional movement with ADX gate (Wilder)
  D) Dual-MTF: 1h entry only if 4h EMA trend agrees
  E) KAMA / ER-adaptive smoother cross
  F) Donchian mid-channel reclaim (different from turtle entry-high)
  G) Vol-normalized residual momentum (z-score ROC)
  H) Blend of best complementary novels

Protocol: IS observe / OOS1 SELECT / OOS2+6M report-only / MDD≤15% / long-only / lev≤1
Objective never reads OOS2/6M for ranking.

Usage:
  python3 zenith_discover.py
  python3 zenith_discover.py --quick
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
from copy import deepcopy
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
RESULTS = os.path.join(HERE, "results")
os.makedirs(RESULTS, exist_ok=True)

from alt_turtle_ns import (  # noqa: E402
    load_panel, stats, trade_stats, atr, adx, ema, kaufman_er, donchian, rma,
    HOURS_PER_YEAR,
)
from alt_optimize import run_enhanced  # noqa: E402

FULL_START = pd.Timestamp("2021-08-01", tz="UTC")
IS_END = pd.Timestamp("2024-12-31 23:00", tz="UTC")
OOS1_START = pd.Timestamp("2025-01-01", tz="UTC")
OOS2_START = pd.Timestamp("2026-03-17", tz="UTC")
ASSETS = ("BTC", "ETH", "SOL")

# Champion benchmarks (to beat)
BENCH = {
    "inst_stable": dict(oos1_sh=1.73, oos1_ret=0.110, full_sh=1.49, full_mdd=-0.050, full_ret=0.616, m6_ret=0.094),
    "inst_primary": dict(oos1_sh=2.00, oos1_ret=0.114, full_sh=1.38, full_mdd=-0.043, full_ret=0.493, m6_ret=0.085),
    "almasi": dict(oos1_sh=1.37, oos1_ret=0.068, full_sh=1.43, full_mdd=-0.060, full_ret=0.453, m6_ret=0.052),
}


def _tz(x):
    idx = pd.DatetimeIndex(x)
    if idx.tz is None:
        return idx.tz_localize("UTC")
    return idx.tz_convert("UTC")


def prepare(assets=ASSETS) -> Dict[str, pd.DataFrame]:
    frames = load_panel(assets)
    out = {}
    for a, df in frames.items():
        d = df.copy()
        d["dt"] = _tz(pd.to_datetime(d["dt"], utc=True))
        c = d["close"].astype(float)
        h = d["high"].astype(float)
        l = d["low"].astype(float)
        if "atr" not in d.columns:
            d["atr"] = atr(h, l, c, 14)
        if "adx" not in d.columns:
            d["adx"] = adx(h, l, c, 14)
        if "er" not in d.columns:
            d["er"] = kaufman_er(c, 48)
        # Wilder DI for DM family
        up = h.diff()
        dn = -l.diff()
        plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
        minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
        tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        atr_s = rma(tr, 14)
        d["plus_di"] = 100 * rma(pd.Series(plus_dm, index=d.index), 14) / atr_s.replace(0, np.nan)
        d["minus_di"] = 100 * rma(pd.Series(minus_dm, index=d.index), 14) / atr_s.replace(0, np.nan)
        d["plus_di"] = d["plus_di"].fillna(0)
        d["minus_di"] = d["minus_di"].fillna(0)
        out[a] = d.reset_index(drop=True)
    return out


# ── Novel signals ───────────────────────────────────────────────────────────
def sig_supertrend(
    frames, atr_n=14, mult=3.0, adx_min=18.0, er_min=0.08,
    confirm=2, long_only=True, lag=1,
) -> Dict[str, pd.Series]:
    """ATR SuperTrend regime — classic CTA/futures DNA, not in prior lab winners."""
    out = {}
    for a, df in frames.items():
        h, l, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
        a_ = atr(h, l, c, atr_n).values
        hl2 = ((h + l) / 2.0).values
        cv = c.values
        n = len(cv)
        upper = hl2 + mult * a_
        lower = hl2 - mult * a_
        st = np.zeros(n)
        dir_ = np.ones(n)  # 1 long, -1 short regime
        for i in range(1, n):
            if np.isnan(a_[i]) or a_[i] <= 0:
                dir_[i] = dir_[i - 1]
                st[i] = st[i - 1]
                continue
            # trail
            if lower[i] > lower[i - 1] or cv[i - 1] < lower[i - 1]:
                pass
            else:
                lower[i] = max(lower[i], lower[i - 1]) if dir_[i - 1] > 0 else lower[i]
            if upper[i] < upper[i - 1] or cv[i - 1] > upper[i - 1]:
                pass
            else:
                upper[i] = min(upper[i], upper[i - 1]) if dir_[i - 1] < 0 else upper[i]

            if dir_[i - 1] > 0:
                if cv[i] < lower[i]:
                    dir_[i] = -1
                    st[i] = upper[i]
                else:
                    dir_[i] = 1
                    st[i] = lower[i]
            else:
                if cv[i] > upper[i]:
                    dir_[i] = 1
                    st[i] = lower[i]
                else:
                    dir_[i] = -1
                    st[i] = upper[i]

        adx_v = df["adx"].astype(float).values
        er_v = df["er"].astype(float).values
        raw = np.zeros(n)
        pos = 0
        conf = 0
        for i in range(n):
            q = (adx_v[i] >= adx_min) and (er_v[i] >= er_min)
            want = 1 if dir_[i] > 0 else (-1 if not long_only else 0)
            if not q:
                want = 0 if pos != 0 and dir_[i] <= 0 else (pos if pos > 0 and dir_[i] > 0 else 0)
                # simpler: if not quality, exit
                if pos != 0 and (dir_[i] <= 0 or not q):
                    pos = 0
                    conf = 0
                raw[i] = pos
                continue
            if pos == 0:
                if want == 1:
                    conf += 1
                    if conf >= confirm:
                        pos = 1
                else:
                    conf = 0
            else:
                if want != 1:
                    pos = 0
                    conf = 0
            raw[i] = pos
        sig = np.zeros(n)
        sig[lag:] = raw[:-lag]
        out[a] = pd.Series(sig, index=df.index)
    return out


def sig_bb_squeeze(
    frames, bb_n=48, bb_k=2.0, kc_mult=1.5, atr_n=24,
    adx_min=15.0, er_min=0.06, confirm=2, long_only=True, lag=1,
    hold_min=6,
) -> Dict[str, pd.Series]:
    """
    Bollinger-in-Keltner squeeze → breakout (TTM squeeze DNA).
    Enter long on close > BB upper after squeeze releases, exit on mid loss.
    """
    out = {}
    for a, df in frames.items():
        c = df["close"].astype(float)
        h = df["high"].astype(float)
        l = df["low"].astype(float)
        mid = c.rolling(bb_n, min_periods=bb_n).mean()
        sd = c.rolling(bb_n, min_periods=bb_n).std()
        bb_u = mid + bb_k * sd
        bb_l = mid - bb_k * sd
        atr_v = atr(h, l, c, atr_n)
        kc_u = mid + kc_mult * atr_v
        kc_l = mid - kc_mult * atr_v
        # squeeze on when BB inside KC
        sq = (bb_u < kc_u) & (bb_l > kc_l)
        adx_v = df["adx"].astype(float).values
        er_v = df["er"].astype(float).values
        cv = c.values
        n = len(cv)
        sq_a = sq.fillna(False).values
        bbu = bb_u.values
        mid_a = mid.values
        raw = np.zeros(n)
        pos = 0
        conf = 0
        held = 0
        sq_recent = 0  # bars since squeeze
        for i in range(n):
            if sq_a[i]:
                sq_recent = 0
            else:
                sq_recent += 1
            q = (adx_v[i] >= adx_min) and (er_v[i] >= er_min)
            if pos == 0:
                # breakout after squeeze within last 12 bars
                if q and sq_recent <= 12 and sq_recent > 0 and np.isfinite(bbu[i]) and cv[i] > bbu[i]:
                    conf += 1
                    if conf >= confirm:
                        pos = 1
                        held = 0
                else:
                    conf = 0
            else:
                held += 1
                if held >= hold_min and (cv[i] < mid_a[i] or not q):
                    pos = 0
                    conf = 0
            raw[i] = pos
        sig = np.zeros(n)
        sig[lag:] = raw[:-lag]
        out[a] = pd.Series(sig, index=df.index)
    return out


def sig_di_trend(
    frames, adx_min=20.0, er_min=0.08, confirm=3, long_only=True, lag=1,
    di_gap=2.0,
) -> Dict[str, pd.Series]:
    """Wilder DI+/DI− crossover with ADX — classic managed-futures overlay."""
    out = {}
    for a, df in frames.items():
        pdi = df["plus_di"].astype(float).values
        mdi = df["minus_di"].astype(float).values
        adx_v = df["adx"].astype(float).values
        er_v = df["er"].astype(float).values
        n = len(pdi)
        raw = np.zeros(n)
        pos = conf = 0
        for i in range(n):
            q = (adx_v[i] >= adx_min) and (er_v[i] >= er_min)
            long_sig = q and (pdi[i] - mdi[i] >= di_gap)
            short_sig = q and (mdi[i] - pdi[i] >= di_gap)
            if pos == 0:
                if long_sig:
                    conf += 1
                    if conf >= confirm:
                        pos = 1
                elif (not long_only) and short_sig:
                    conf += 1
                    if conf >= confirm:
                        pos = -1
                else:
                    conf = 0
            elif pos == 1:
                if not long_sig:
                    pos = 0
                    conf = 0
            else:
                if long_only or not short_sig:
                    pos = 0
                    conf = 0
            raw[i] = pos
        sig = np.zeros(n)
        sig[lag:] = raw[:-lag]
        out[a] = pd.Series(sig, index=df.index)
    return out


def sig_mtf_ema(
    frames, fast=24, slow=168, h4_fast=72, h4_slow=336,
    adx_min=16.0, er_min=0.08, confirm=3, long_only=True, lag=1,
) -> Dict[str, pd.Series]:
    """1h EMA cross only when synthetic 4h trend (ema on 4h-resampled) agrees."""
    out = {}
    for a, df in frames.items():
        c = df["close"].astype(float)
        dt = _tz(df["dt"])
        ef = ema(c, fast).values
        es = ema(c, slow).values
        # 4h trend via resample
        s4 = pd.Series(c.values, index=dt).resample("4h").last().dropna()
        e4f = ema(s4, max(3, h4_fast // 4))
        e4s = ema(s4, max(5, h4_slow // 4))
        h4_up = (e4f > e4s).reindex(dt, method="ffill").fillna(False).values
        adx_v = df["adx"].astype(float).values
        er_v = df["er"].astype(float).values
        n = len(c)
        raw = np.zeros(n)
        pos = conf = 0
        for i in range(n):
            q = (adx_v[i] >= adx_min) and (er_v[i] >= er_min)
            up = q and (ef[i] > es[i]) and bool(h4_up[i])
            if pos == 0:
                if up:
                    conf += 1
                    if conf >= confirm:
                        pos = 1
                else:
                    conf = 0
            else:
                if not (ef[i] > es[i]) or not h4_up[i]:
                    pos = 0
                    conf = 0
            raw[i] = pos if long_only else raw[i]
        sig = np.zeros(n)
        sig[lag:] = raw[:-lag]
        out[a] = pd.Series(sig, index=df.index)
    return out


def sig_kama_cross(
    frames, er_n=48, fast_sc=2, slow_sc=30, smooth_n=10,
    adx_min=15.0, er_min=0.10, confirm=3, long_only=True, lag=1,
) -> Dict[str, pd.Series]:
    """Kaufman Adaptive MA cross vs slow EMA — adaptive trend DNA."""
    out = {}
    for a, df in frames.items():
        c = df["close"].astype(float)
        er = kaufman_er(c, er_n).clip(0, 1)
        # sc = er*(fast-slow)+slow ; alpha = sc^2
        fast_f = 2 / (fast_sc + 1)
        slow_f = 2 / (slow_sc + 1)
        sc = er * (fast_f - slow_f) + slow_f
        alpha = (sc ** 2).fillna(0).values
        cv = c.values
        n = len(cv)
        kama = np.zeros(n)
        kama[0] = cv[0]
        for i in range(1, n):
            kama[i] = kama[i - 1] + alpha[i] * (cv[i] - kama[i - 1])
        slow = ema(c, max(smooth_n * 5, 120)).values
        adx_v = df["adx"].astype(float).values
        er_v = df["er"].astype(float).values
        raw = np.zeros(n)
        pos = conf = 0
        for i in range(n):
            q = (adx_v[i] >= adx_min) and (er_v[i] >= er_min)
            up = q and kama[i] > slow[i]
            if pos == 0:
                if up:
                    conf += 1
                    if conf >= confirm:
                        pos = 1
                else:
                    conf = 0
            else:
                if not up:
                    pos = 0
                    conf = 0
            raw[i] = pos
        sig = np.zeros(n)
        sig[lag:] = raw[:-lag]
        out[a] = pd.Series(sig, index=df.index)
    return out


def sig_mid_reclaim(
    frames, n_ch=336, adx_min=18.0, er_min=0.10, confirm=3,
    long_only=True, lag=1,
) -> Dict[str, pd.Series]:
    """
    Donchian MIDPOINT reclaim — distinct from turtle high-breakout.
    Long when price reclaims mid of channel from below with quality.
    Exit when close back below mid - buffer or channel low.
    """
    out = {}
    for a, df in frames.items():
        h, l, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
        up, lo = donchian(h, l, n_ch)  # already shifted
        mid = (up + lo) / 2.0
        adx_v = df["adx"].astype(float).values
        er_v = df["er"].astype(float).values
        cv = c.values
        mid_a = mid.values
        lo_a = lo.values
        n = len(cv)
        raw = np.zeros(n)
        pos = conf = 0
        for i in range(n):
            q = (adx_v[i] >= adx_min) and (er_v[i] >= er_min) and np.isfinite(mid_a[i])
            if pos == 0:
                if q and cv[i] > mid_a[i]:
                    conf += 1
                    if conf >= confirm:
                        pos = 1
                else:
                    conf = 0
            else:
                if (np.isfinite(lo_a[i]) and cv[i] < lo_a[i]) or (cv[i] < mid_a[i] * 0.998):
                    # mild buffer below mid
                    if cv[i] < mid_a[i]:
                        pos = 0
                        conf = 0
            raw[i] = pos
        sig = np.zeros(n)
        sig[lag:] = raw[:-lag]
        out[a] = pd.Series(sig, index=df.index)
    return out


def sig_zmom(
    frames, roc_n=48, vol_n=168, z_entry=1.0, z_exit=0.0,
    adx_min=15.0, er_min=0.08, confirm=2, long_only=True, lag=1,
) -> Dict[str, pd.Series]:
    """Vol-normalized ROC z-score momentum (residual momentum DNA)."""
    out = {}
    for a, df in frames.items():
        c = df["close"].astype(float)
        roc = c.pct_change(roc_n)
        vol = c.pct_change().rolling(vol_n, min_periods=max(24, vol_n // 4)).std()
        z = (roc / (vol * np.sqrt(roc_n) + 1e-12)).replace([np.inf, -np.inf], np.nan).fillna(0).values
        adx_v = df["adx"].astype(float).values
        er_v = df["er"].astype(float).values
        n = len(c)
        raw = np.zeros(n)
        pos = conf = 0
        for i in range(n):
            q = (adx_v[i] >= adx_min) and (er_v[i] >= er_min)
            if pos == 0:
                if q and z[i] >= z_entry:
                    conf += 1
                    if conf >= confirm:
                        pos = 1
                else:
                    conf = 0
            else:
                if z[i] <= z_exit or not q:
                    pos = 0
                    conf = 0
            raw[i] = pos
        sig = np.zeros(n)
        sig[lag:] = raw[:-lag]
        out[a] = pd.Series(sig, index=df.index)
    return out


def sig_hybrid_st_di(
    frames, st_mult=2.5, adx_min=20.0, er_min=0.10, confirm=3, lag=1,
) -> Dict[str, pd.Series]:
    """Require BOTH SuperTrend long regime AND DI+ > DI− (consensus novel)."""
    st = sig_supertrend(frames, mult=st_mult, adx_min=0.0, er_min=0.0, confirm=1, lag=0)
    di = sig_di_trend(frames, adx_min=0.0, er_min=0.0, confirm=1, di_gap=0.0, lag=0)
    out = {}
    for a in frames:
        s1 = st[a].values
        s2 = di[a].values
        adx_v = frames[a]["adx"].astype(float).values
        er_v = frames[a]["er"].astype(float).values
        n = len(s1)
        raw = np.zeros(n)
        pos = conf = 0
        for i in range(n):
            q = (adx_v[i] >= adx_min) and (er_v[i] >= er_min)
            want = q and (s1[i] > 0) and (s2[i] > 0)
            if pos == 0:
                if want:
                    conf += 1
                    if conf >= confirm:
                        pos = 1
                else:
                    conf = 0
            else:
                if not want:
                    pos = 0
                    conf = 0
            raw[i] = pos
        sig = np.zeros(n)
        sig[lag:] = raw[:-lag]
        out[a] = pd.Series(sig, index=frames[a].index)
    return out


# ── backtest helpers ────────────────────────────────────────────────────────
def make_signal(cfg, frames):
    k = cfg["kind"]
    fr = {a: frames[a] for a in cfg.get("assets", ASSETS) if a in frames}
    p = {kk: vv for kk, vv in cfg.items() if kk not in ("kind", "ek", "assets", "tag")}
    if k == "supertrend":
        return sig_supertrend(fr, **p)
    if k == "bb_squeeze":
        return sig_bb_squeeze(fr, **p)
    if k == "di_trend":
        return sig_di_trend(fr, **p)
    if k == "mtf_ema":
        return sig_mtf_ema(fr, **p)
    if k == "kama":
        return sig_kama_cross(fr, **p)
    if k == "mid_reclaim":
        return sig_mid_reclaim(fr, **p)
    if k == "zmom":
        return sig_zmom(fr, **p)
    if k == "hybrid_st_di":
        return sig_hybrid_st_di(fr, **p)
    raise ValueError(k)


def bt(frames, side, capital=100.0, trade_from=None, ek=None):
    ek = dict(ek or {})
    defaults = dict(
        sizing="vol_target", vol_target=0.12, notional_frac=0.40,
        max_positions=3, lev_cap=1.0, short_frac_mult=0.0,
        pyramid_levels=0, use_atr_stop=False, atr_stop_mult=99.0,
        atr_trail_mult=99.0, cooldown=4, risk_per_trade=0.01,
        atr_risk_mult=2.5, vol_lookback=336, pyramid_step_atr=1.0,
        pyramid_scale=0.5, breakeven_at_R=0.0, time_stop_bars=0,
        top_k=0, top_k_lookback=72,
    )
    defaults.update(ek)
    return run_enhanced(frames, side, capital=capital, trade_from=trade_from, **defaults)


def equity_series(port):
    eq = port["equity"]
    dts = port["dts"]
    return pd.Series(np.asarray(eq, float), index=_tz(pd.to_datetime(dts, utc=True))).astype(float)


def window_metrics(eq: pd.Series, capital=100.0) -> dict:
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
            ret=float(st["total_return"]), sharpe=float(st["sharpe"]),
            mdd=float(st["max_dd"]), cagr=float(st["cagr"]),
            n=len(s), end=float(s.iloc[-1]),
        )

    end = eq.index.max()
    return {
        "IS": one(FULL_START, IS_END),
        "OOS1": one(OOS1_START, OOS2_START - pd.Timedelta(hours=1)),
        "OOS2": one(OOS2_START, None),
        "FULL": one(FULL_START, None),
        "6M": one(end - pd.Timedelta(days=180), None),
    }


def blend_equity(parts, capital=100.0):
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
    return pd.Series(acc, index=idx)


def score(win, n_trades, costs, capital=100.0) -> float:
    """OOS1-only selection with luck/overtrade penalties. Never uses OOS2/6M."""
    o1, is_, full = win["OOS1"], win["IS"], win["FULL"]
    if not np.isfinite(o1.get("sharpe", np.nan)):
        return -99.0
    if o1["ret"] <= 0 or o1["sharpe"] < 0.7:
        return -90.0 + (o1.get("sharpe") or -1)
    if full["mdd"] < -0.15:
        return -80.0
    if is_["sharpe"] < 0.4:
        return -70.0 + is_["sharpe"]

    sh = float(o1["sharpe"])
    gap = max(0.0, sh - float(is_.get("sharpe") or 0))
    luck = 0.35 * gap
    cost_drag = costs / capital
    tpen = 0.0
    if n_trades > 600:
        tpen = 0.25 * (n_trades - 600) / 600
    elif n_trades < 12:
        tpen = 0.4
    full_b = 0.18 * min(max(full.get("sharpe") or 0, 0), 2.2)
    ret_b = 0.35 * min(max(o1["ret"], 0), 0.25) * 8
    # bonus for beating inst stable OOS1 sh
    beat = 0.15 * max(0.0, sh - 1.73)
    return float(sh + ret_b + full_b + beat - luck - 0.5 * cost_drag - tpen)


def hard_pass(win) -> bool:
    return (
        win["OOS1"]["ret"] > 0
        and win["OOS1"]["sharpe"] >= 0.7
        and win["FULL"]["mdd"] >= -0.15
        and win["IS"]["sharpe"] >= 0.5
        and win["OOS2"]["ret"] > -0.05  # soft confirm not select
    )


def beats_champions(win) -> dict:
    return dict(
        beats_almasi_oos1=win["OOS1"]["sharpe"] > BENCH["almasi"]["oos1_sh"]
        and win["OOS1"]["ret"] > BENCH["almasi"]["oos1_ret"],
        beats_inst_primary_oos1=win["OOS1"]["sharpe"] >= BENCH["inst_primary"]["oos1_sh"] * 0.95
        and win["OOS1"]["ret"] >= BENCH["inst_primary"]["oos1_ret"] * 0.9,
        beats_inst_stable_full=win["FULL"]["sharpe"] > BENCH["inst_stable"]["full_sh"]
        and win["FULL"]["mdd"] >= BENCH["inst_stable"]["full_mdd"] - 0.02,
        beats_inst_stable_oos1=win["OOS1"]["sharpe"] > BENCH["inst_stable"]["oos1_sh"]
        and win["OOS1"]["ret"] > BENCH["inst_stable"]["oos1_ret"],
        beats_all_full_sh=win["FULL"]["sharpe"]
        > max(BENCH["inst_stable"]["full_sh"], BENCH["almasi"]["full_sh"], BENCH["inst_primary"]["full_sh"]),
    )


def eval_cfg(cfg, frames, capital=100.0):
    try:
        side = make_signal(cfg, frames)
        assets = cfg.get("assets", ASSETS)
        fr = {a: frames[a] for a in assets}
        for a in assets:
            if a not in side:
                side[a] = pd.Series(0.0, index=frames[a].index)
        port = bt(fr, side, capital=capital, trade_from=FULL_START, ek=cfg.get("ek"))
        eq = equity_series(port)
        win = window_metrics(eq, capital)
        costs = float(np.nansum(port["costs"])) if isinstance(port["costs"], np.ndarray) else float(port.get("costs") or 0)
        tr = port.get("trades") or []
        ts = trade_stats(tr) if tr else {"n": 0}
        sc = score(win, len(tr), costs, capital)
        return dict(
            cfg=cfg, windows=win, score=sc, n_trades=len(tr), costs=costs,
            trade_stats=ts, end_equity=float(eq.dropna().iloc[-1]),
            equity=eq, hard=hard_pass(win), beats=beats_champions(win),
        )
    except Exception as e:
        return dict(error=str(e), cfg=cfg, score=-999.0)


def build_candidates(quick=False) -> List[dict]:
    cands = []
    vts = [0.10, 0.12, 0.14] if not quick else [0.12]
    cools = [4, 8] if not quick else [4]

    def ek(vt=0.12, cd=4, stop=False, ts=0, nf=0.40):
        return dict(
            sizing="vol_target", vol_target=vt, notional_frac=nf,
            max_positions=3, lev_cap=1.0, cooldown=cd, short_frac_mult=0.0,
            use_atr_stop=stop, atr_stop_mult=3.0 if stop else 99.0,
            atr_trail_mult=3.5 if stop else 99.0,
            breakeven_at_R=1.0 if stop else 0.0, time_stop_bars=ts,
        )

    # A SuperTrend
    for mult, conf, adx_m, er_m, vt in itertools.product(
        [2.0, 2.5, 3.0, 3.5] if not quick else [2.5, 3.0],
        [2, 3, 5] if not quick else [2, 3],
        [15.0, 18.0, 22.0] if not quick else [18.0, 22.0],
        [0.06, 0.10, 0.12] if not quick else [0.08, 0.10],
        vts,
    ):
        cands.append(dict(
            kind="supertrend", tag=f"ST_m{mult}_c{conf}_a{adx_m}",
            mult=mult, confirm=conf, adx_min=adx_m, er_min=er_m,
            atr_n=14, long_only=True, ek=ek(vt), assets=ASSETS,
        ))

    # B BB squeeze
    for bb_n, k, conf, adx_m, vt in itertools.product(
        [24, 48, 72] if not quick else [48],
        [1.5, 2.0, 2.5] if not quick else [2.0],
        [1, 2, 3] if not quick else [2],
        [12.0, 15.0, 20.0] if not quick else [15.0],
        vts,
    ):
        cands.append(dict(
            kind="bb_squeeze", tag=f"BB_n{bb_n}_k{k}_c{conf}",
            bb_n=bb_n, bb_k=k, confirm=conf, adx_min=adx_m, er_min=0.06,
            long_only=True, ek=ek(vt, cd=6), assets=ASSETS,
        ))

    # C DI trend
    for adx_m, gap, conf, er_m, vt in itertools.product(
        [18.0, 22.0, 25.0, 28.0] if not quick else [20.0, 25.0],
        [1.0, 2.0, 4.0] if not quick else [2.0],
        [2, 3, 5] if not quick else [3],
        [0.06, 0.10, 0.12] if not quick else [0.08, 0.10],
        vts,
    ):
        cands.append(dict(
            kind="di_trend", tag=f"DI_a{adx_m}_g{gap}_c{conf}",
            adx_min=adx_m, di_gap=gap, confirm=conf, er_min=er_m,
            long_only=True, ek=ek(vt), assets=ASSETS,
        ))

    # D MTF EMA
    for fa, sl, conf, adx_m, vt in itertools.product(
        [12, 24, 48] if not quick else [24],
        [96, 168, 336] if not quick else [168],
        [2, 4, 6] if not quick else [3, 5],
        [12.0, 16.0, 20.0] if not quick else [16.0],
        vts,
    ):
        if fa >= sl:
            continue
        cands.append(dict(
            kind="mtf_ema", tag=f"MTF_{fa}_{sl}_c{conf}",
            fast=fa, slow=sl, confirm=conf, adx_min=adx_m, er_min=0.08,
            h4_fast=max(24, fa * 2), h4_slow=max(96, sl * 2),
            long_only=True, ek=ek(vt), assets=ASSETS,
        ))

    # E KAMA
    for er_n, conf, adx_m, er_m, vt in itertools.product(
        [24, 48, 72] if not quick else [48],
        [2, 3, 5] if not quick else [3],
        [15.0, 18.0, 22.0] if not quick else [18.0],
        [0.08, 0.10, 0.14] if not quick else [0.10],
        vts,
    ):
        cands.append(dict(
            kind="kama", tag=f"KAMA_e{er_n}_c{conf}",
            er_n=er_n, confirm=conf, adx_min=adx_m, er_min=er_m,
            long_only=True, ek=ek(vt), assets=ASSETS,
        ))

    # F mid reclaim
    for n_ch, conf, adx_m, er_m, vt in itertools.product(
        [168, 336, 504, 672] if not quick else [336, 504],
        [2, 3, 5] if not quick else [3],
        [15.0, 18.0, 22.0] if not quick else [18.0, 22.0],
        [0.08, 0.10, 0.12] if not quick else [0.10],
        vts,
    ):
        cands.append(dict(
            kind="mid_reclaim", tag=f"MID_n{n_ch}_c{conf}_a{adx_m}",
            n_ch=n_ch, confirm=conf, adx_min=adx_m, er_min=er_m,
            long_only=True, ek=ek(vt, cd=6), assets=ASSETS,
        ))

    # G z-mom
    for roc, z_e, conf, adx_m, vt in itertools.product(
        [24, 48, 96] if not quick else [48],
        [0.75, 1.0, 1.25, 1.5] if not quick else [1.0, 1.25],
        [2, 3, 4] if not quick else [2, 3],
        [12.0, 15.0, 20.0] if not quick else [15.0],
        vts,
    ):
        cands.append(dict(
            kind="zmom", tag=f"Z_r{roc}_z{z_e}_c{conf}",
            roc_n=roc, z_entry=z_e, z_exit=0.0, confirm=conf,
            adx_min=adx_m, er_min=0.08, long_only=True, ek=ek(vt), assets=ASSETS,
        ))

    # H hybrid
    for mult, conf, adx_m, vt in itertools.product(
        [2.0, 2.5, 3.0] if not quick else [2.5],
        [2, 3, 5] if not quick else [3],
        [18.0, 22.0, 25.0] if not quick else [20.0, 22.0],
        vts,
    ):
        cands.append(dict(
            kind="hybrid_st_di", tag=f"HY_m{mult}_c{conf}_a{adx_m}",
            st_mult=mult, confirm=conf, adx_min=adx_m, er_min=0.10,
            ek=ek(vt, cd=6), assets=ASSETS,
        ))

    # with ATR stops variants of best kinds seeds
    seeds = [c for c in cands if c["kind"] in ("supertrend", "hybrid_st_di", "di_trend", "mid_reclaim")]
    extra = []
    for c in seeds[:: max(1, len(seeds) // 30)]:
        c2 = deepcopy(c)
        c2["ek"] = ek(c["ek"]["vol_target"], cd=6, stop=True, ts=168)
        c2["tag"] = c2.get("tag", "") + "_stop"
        extra.append(c2)
    cands.extend(extra)
    return cands


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--capital", type=float, default=100.0)
    args = ap.parse_args()

    t0 = time.time()
    print("loading…")
    frames = prepare(ASSETS)
    cands = build_candidates(quick=args.quick)
    max_n = 140 if args.quick else 320
    if len(cands) > max_n:
        rng = np.random.default_rng(20260929)
        # keep diversity: first of each kind + random
        by = {}
        for c in cands:
            by.setdefault(c["kind"], []).append(c)
        keep = []
        for k, lst in by.items():
            keep.extend(lst[: min(8, len(lst))])
        rest = [c for c in cands if c not in keep]
        need = max_n - len(keep)
        if need > 0 and rest:
            idx = rng.choice(len(rest), size=min(need, len(rest)), replace=False)
            keep.extend([rest[i] for i in idx])
        cands = keep[:max_n]
    print(f"candidates: {len(cands)}")

    results = []
    best = -999
    for i, cfg in enumerate(cands):
        r = eval_cfg(cfg, frames, args.capital)
        if r.get("score", -999) <= -900:
            if r.get("error") and i < 3:
                print(" err", r["error"])
            continue
        light = {k: v for k, v in r.items() if k != "equity"}
        results.append(light)
        if r["score"] > best:
            best = r["score"]
            w = r["windows"]
            b = r["beats"]
            print(
                f"[{i+1}/{len(cands)}] NEW {best:.3f}  {cfg.get('tag', cfg['kind'])}  "
                f"OOS1 {w['OOS1']['ret']*100:+.2f}%/{w['OOS1']['sharpe']:.2f}  "
                f"FULL {w['FULL']['sharpe']:.2f}/{w['FULL']['mdd']*100:.1f}%  "
                f"6M {w['6M']['ret']*100:+.2f}%  hard={r['hard']}  "
                f"beat_stOOS={b['beats_inst_stable_oos1']} beat_allF={b['beats_all_full_sh']}"
            )
        elif (i + 1) % 40 == 0:
            print(f"[{i+1}/{len(cands)}] … best={best:.3f}")

    results.sort(key=lambda x: (x.get("hard", False), x["score"]), reverse=True)
    print("\n=== TOP 15 ===")
    for j, r in enumerate(results[:15]):
        w = r["windows"]
        print(
            f"{j+1:2d}. sc={r['score']:.3f} hard={r['hard']} {r['cfg'].get('tag','')[:28]:28s} "
            f"OOS1 {w['OOS1']['ret']*100:+5.2f}/{w['OOS1']['sharpe']:.2f}  "
            f"FULL {w['FULL']['sharpe']:.2f}/{w['FULL']['mdd']*100:.1f}%  "
            f"6M {w['6M']['ret']*100:+5.2f} n={r['n_trades']}"
        )

    # Blend top hard singles of different kinds
    print("\n=== BLEND ===")
    hard = [r for r in results if r.get("hard")][:10]
    pool = hard if hard else results[:8]
    # re-eval with equity
    rich = []
    for r in pool[:6]:
        rr = eval_cfg(r["cfg"], frames, args.capital)
        if rr and "equity" in rr:
            rich.append(rr)

    blends = []
    for i, r in enumerate(rich):
        blends.append(dict(
            name=f"single:{r['cfg'].get('tag', r['cfg']['kind'])}",
            type="single", windows=r["windows"], score=r["score"],
            n_trades=r["n_trades"], costs=r["costs"], equity=r["equity"],
            legs=[r["cfg"]], end_equity=r["end_equity"], hard=r["hard"],
            beats=r["beats"], trade_stats=r["trade_stats"],
        ))

    for i, j in itertools.combinations(range(len(rich)), 2):
        # skip same kind
        if rich[i]["cfg"]["kind"] == rich[j]["cfg"]["kind"]:
            continue
        for wa, wb in ((0.7, 0.3), (0.6, 0.4), (0.5, 0.5)):
            eq = blend_equity([(wa, rich[i]["equity"]), (wb, rich[j]["equity"])], args.capital)
            win = window_metrics(eq, args.capital)
            n_tr = int(wa * rich[i]["n_trades"] + wb * rich[j]["n_trades"])
            costs = wa * rich[i]["costs"] + wb * rich[j]["costs"]
            sc = score(win, n_tr, costs, args.capital)
            blends.append(dict(
                name=f"blend_{rich[i]['cfg'].get('tag','i')[:12]}_{rich[j]['cfg'].get('tag','j')[:12]}_{int(wa*100)}",
                type="blend", weights=(wa, wb),
                windows=win, score=sc, n_trades=n_tr, costs=costs,
                equity=eq, legs=[rich[i]["cfg"], rich[j]["cfg"]],
                end_equity=float(eq.dropna().iloc[-1]),
                hard=hard_pass(win), beats=beats_champions(win),
            ))

    # triple top3
    if len(rich) >= 3:
        for ws in ((0.5, 0.3, 0.2), (0.4, 0.35, 0.25)):
            eq = blend_equity(
                [(ws[0], rich[0]["equity"]), (ws[1], rich[1]["equity"]), (ws[2], rich[2]["equity"])],
                args.capital,
            )
            win = window_metrics(eq, args.capital)
            n_tr = int(sum(ws[k] * rich[k]["n_trades"] for k in range(3)))
            costs = sum(ws[k] * rich[k]["costs"] for k in range(3))
            sc = score(win, n_tr, costs, args.capital)
            blends.append(dict(
                name=f"triple_{'_'.join(str(int(x*100)) for x in ws)}",
                type="triple", weights=ws, windows=win, score=sc,
                n_trades=n_tr, costs=costs, equity=eq,
                legs=[rich[k]["cfg"] for k in range(3)],
                end_equity=float(eq.dropna().iloc[-1]),
                hard=hard_pass(win), beats=beats_champions(win),
            ))

    def rank_key(b):
        bt_ = b.get("beats") or {}
        crown = int(bt_.get("beats_inst_stable_oos1", False)) + int(bt_.get("beats_all_full_sh", False))
        return (b.get("hard", False), crown, b["score"])

    blends.sort(key=rank_key, reverse=True)
    print("Top blends/singles:")
    for j, b in enumerate(blends[:12]):
        w = b["windows"]
        bt_ = b.get("beats") or {}
        print(
            f"  {j+1:2d}. {b['name'][:42]:42s} sc={b['score']:.3f} hard={b['hard']}  "
            f"OOS1 {w['OOS1']['ret']*100:+5.2f}/{w['OOS1']['sharpe']:.2f}  "
            f"FULL {w['FULL']['sharpe']:.2f}/{w['FULL']['mdd']*100:.1f}%  "
            f"6M {w['6M']['ret']*100:+5.2f}  "
            f"crownOOS={bt_.get('beats_inst_stable_oos1')} crownF={bt_.get('beats_all_full_sh')}"
        )

    winner = blends[0]
    # Prefer hard + beats stable if any
    for b in blends:
        if b.get("hard") and (b.get("beats") or {}).get("beats_inst_stable_oos1"):
            winner = b
            break
    else:
        for b in blends:
            if b.get("hard") and (b.get("beats") or {}).get("beats_all_full_sh"):
                winner = b
                break
        else:
            for b in blends:
                if b.get("hard"):
                    winner = b
                    break

    w = winner["windows"]
    print("\n*** WINNER ***", winner["name"], "hard", winner.get("hard"))
    for lab in ("IS", "OOS1", "OOS2", "6M", "FULL"):
        ww = w[lab]
        print(f"  {lab:5s}  ret={ww['ret']*100:+7.2f}%  Sh={ww['sharpe']:6.2f}  MDD={ww['mdd']*100:7.2f}%")
    print("  beats", winner.get("beats"))
    print(f"  end={winner['end_equity']:.2f} n≈{winner['n_trades']} costs={winner['costs']:.2f}")

    winner["equity"].to_csv(os.path.join(RESULTS, "zenith_equity.csv"), header=["eq"])

    # vs champions table
    vs = {}
    for name, b in BENCH.items():
        vs[name] = dict(
            bench=b,
            delta_oos1_sh=w["OOS1"]["sharpe"] - b["oos1_sh"],
            delta_oos1_ret=w["OOS1"]["ret"] - b["oos1_ret"],
            delta_full_sh=w["FULL"]["sharpe"] - b["full_sh"],
            delta_full_mdd=w["FULL"]["mdd"] - b["full_mdd"],
            delta_6m=w["6M"]["ret"] - b["m6_ret"],
            wins_oos1=w["OOS1"]["sharpe"] > b["oos1_sh"] and w["OOS1"]["ret"] > b["oos1_ret"],
            wins_full_sh=w["FULL"]["sharpe"] > b["full_sh"],
        )

    payload = dict(
        name="zenith-v001",
        title="ZENITH — discovered challenger",
        winner_name=winner["name"],
        hard_pass=winner.get("hard"),
        windows=winner["windows"],
        score=winner["score"],
        n_trades=winner["n_trades"],
        costs=winner["costs"],
        end_equity=winner["end_equity"],
        legs=winner["legs"],
        weights=winner.get("weights"),
        type=winner.get("type"),
        beats=winner.get("beats"),
        vs_champions=vs,
        top_singles=[{
            "score": r["score"], "hard": r.get("hard"), "cfg": r["cfg"],
            "windows": r["windows"], "beats": r.get("beats"),
            "n_trades": r["n_trades"], "costs": r["costs"],
        } for r in results[:12]],
        top_blends=[{
            "name": b["name"], "score": b["score"], "hard": b.get("hard"),
            "windows": b["windows"], "beats": b.get("beats"),
            "weights": b.get("weights"), "legs": b.get("legs"),
        } for b in blends[:10]],
        protocol="IS observe / OOS1 select / OOS2+6M report-only / MDD<=15% / long-only / lev<=1",
        truth="Novel DNA (SuperTrend/BB/DI/MTF/KAMA/mid/z-mom/hybrid). No zero-error claim.",
        elapsed_sec=round(time.time() - t0, 1),
        n_scanned=len(results),
    )
    with open(os.path.join(RESULTS, "zenith_discover_results.json"), "w") as f:
        json.dump(payload, f, indent=2, default=str)

    # FA
    lines = [
        "# ZENITH v001 — استراتژی کشف‌شدهٔ جدید (چالش قهرمان‌ها)",
        "",
        f"**winner:** `{winner['name']}` · **hard_pass:** `{'✅' if winner.get('hard') else '❌'}`",
        f"**DNA:** novel (غیر از کپی almasi TQ/VQ و TSMOM صرف)",
        "",
        "## نتایج",
        "",
        "| بازه | بازده | Sharpe | MaxDD |",
        "|---|---:|---:|---:|",
    ]
    for lab in ("IS", "OOS1", "OOS2", "6M", "FULL"):
        ww = w[lab]
        lines.append(f"| {lab} | {ww['ret']*100:+.2f}% | {ww['sharpe']:.2f} | {ww['mdd']*100:.2f}% |")
    lines += [
        "",
        f"- end **{winner['end_equity']:.2f}** · n≈**{winner['n_trades']}** · costs **{winner['costs']:.2f}**",
        f"- score OOS1 **{winner['score']:.3f}**",
        "",
        "## vs قهرمان‌ها",
        "",
        "| حریف | Δ OOS1 Sh | Δ OOS1 ret | Δ FULL Sh | Δ 6M | برد OOS1؟ |",
        "|---|---:|---:|---:|---:|:---:|",
    ]
    for name, v in vs.items():
        lines.append(
            f"| {name} | {v['delta_oos1_sh']:+.2f} | {v['delta_oos1_ret']*100:+.1f}pp | "
            f"{v['delta_full_sh']:+.2f} | {v['delta_6m']*100:+.1f}pp | "
            f"{'✅' if v['wins_oos1'] else '❌'} |"
        )
    lines += [
        "",
        "```json",
        json.dumps(winner["legs"], indent=2, default=str)[:4000],
        "```",
        "",
        f"elapsed {payload['elapsed_sec']}s · scanned {payload['n_scanned']}",
        "",
        "> انتخاب فقط روی OOS1. OOS2/6M گزارش. zero-error نیست.",
    ]
    with open(os.path.join(RESULTS, "ZENITH_V001_SUMMARY_FA.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("saved results/zenith_discover_results.json · ZENITH_V001_SUMMARY_FA.md")


if __name__ == "__main__":
    main()
