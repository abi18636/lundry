#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ZENITH apex REBIRTH — DNA families deliberately unlike multiz2 apex-v001.

Goal: a NEW apex profile whose equity path / trade stats / window mix
look materially different from v001 multiz2 (OOS1-Sh-max, 6M soft, n≈1200).

Novel families (not TQ/VQ Donchian, not multiz2 tanh-z vote):
  1) cs_resid   — cross-sectional residual momentum (rank vs peer mean z)
  2) impulse    — large-bar impulse + EMA hold / time-stop
  3) compress   — ATR percentile compression → expansion break
  4) beta_rs    — residual vs BTC beta + ADX gate
  5) twin_mtf   — 4h EMA slope + 1h DI+ consensus with cool exit
  6) path_thrust— ER-weighted ROC thrust (path efficiency × return)
  7) kelt_rsi   — Keltner break + RSI band (not overbought chase)
  8) gap_fill   — overnight/session range reclaim (crypto 24h: prior-N range)

Protocol: IS observe / OOS1 SELECT / OOS2+6M report / MDD≤15% / long-only / lev≤1
Selection never ranks on OOS2/6M; we DO prefer diversity vs v001 apex path.

Usage:
  python3 zenith_apex_rebirth.py
  python3 zenith_apex_rebirth.py --quick
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
from copy import deepcopy
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
RESULTS = os.path.join(HERE, "results")
os.makedirs(RESULTS, exist_ok=True)

from alt_turtle_ns import (  # noqa: E402
    stats, trade_stats, atr, ema, kaufman_er, donchian, HOURS_PER_YEAR,
)
from alt_optimize import run_enhanced  # noqa: E402
from zenith_discover import (  # noqa: E402
    prepare, ASSETS, FULL_START, IS_END, OOS1_START, OOS2_START, BENCH,
    _tz, equity_series, window_metrics, blend_equity, hard_pass, beats_champions,
)
from zenith_refine import ek as base_ek  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────
def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0.0)
    dn = (-d).clip(lower=0.0)
    au = up.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    ad = dn.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    rs = au / ad.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def lag_sig(raw: np.ndarray, lag: int = 1) -> np.ndarray:
    sig = np.zeros_like(raw, dtype=float)
    if lag <= 0:
        return raw.astype(float)
    sig[lag:] = raw[:-lag]
    return sig


def state_machine(want_entry, want_exit, confirm=2, lag=1) -> np.ndarray:
    n = len(want_entry)
    raw = np.zeros(n)
    pos = conf = 0
    for i in range(n):
        if pos == 0:
            if want_entry[i]:
                conf += 1
                if conf >= confirm:
                    pos = 1
            else:
                conf = 0
        else:
            if want_exit[i]:
                pos = 0
                conf = 0
        raw[i] = pos
    return lag_sig(raw, lag)


# ── DNA 1: cross-sectional residual momentum ─────────────────────────────────
def sig_cs_resid(
    frames,
    roc_n=24,
    vol_n=168,
    entry_z=0.35,
    exit_z=-0.05,
    top_frac=0.67,
    confirm=2,
    adx_min=15.0,
    er_min=0.06,
    lag=1,
) -> Dict[str, pd.Series]:
    """
    Rank assets by vol-normalized ROC residual vs cross-sectional mean.
    Long only top residual names when residual z >= entry (anti-TSMOM-sign).
    """
    assets = list(frames.keys())
    # align
    idx = frames[assets[0]].index
    for a in assets[1:]:
        idx = idx.intersection(frames[a].index)
    idx = idx.sort_values()

    zmat = {}
    for a in assets:
        df = frames[a].reindex(idx)
        c = df["close"].astype(float)
        lr = np.log(c / c.shift(roc_n))
        vol = np.log(c / c.shift(1)).rolling(vol_n, min_periods=max(24, vol_n // 4)).std()
        z = (lr / (vol * np.sqrt(roc_n) + 1e-12)).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        zmat[a] = z.values

    Z = np.column_stack([zmat[a] for a in assets])
    mu = np.nanmean(Z, axis=1, keepdims=True)
    resid = Z - mu  # residual momentum
    # rank: higher residual better
    ranks = np.zeros_like(resid)
    for i in range(len(idx)):
        row = resid[i]
        order = np.argsort(np.argsort(-row))  # 0 = best
        ranks[i] = order

    k_keep = max(1, int(np.ceil(top_frac * len(assets))))
    out = {}
    for j, a in enumerate(assets):
        df = frames[a].reindex(idx)
        adx_v = df["adx"].astype(float).values
        er_v = df["er"].astype(float).values
        r = resid[:, j]
        rk = ranks[:, j]
        want_e = (r >= entry_z) & (rk < k_keep) & (adx_v >= adx_min) & (er_v >= er_min)
        want_x = (r <= exit_z) | (rk >= k_keep) | (adx_v < adx_min * 0.7)
        sig = state_machine(want_e, want_x, confirm=confirm, lag=lag)
        # map back to original index
        s = pd.Series(sig, index=idx)
        out[a] = s.reindex(frames[a].index).fillna(0.0)
    return out


# ── DNA 2: impulse bar + EMA hold ────────────────────────────────────────────
def sig_impulse(
    frames,
    impulse_atr=1.8,
    ema_n=72,
    hold_atr_trail=2.5,
    max_hold=120,
    confirm=1,
    adx_min=16.0,
    er_min=0.05,
    lag=1,
) -> Dict[str, pd.Series]:
    """
    Enter on a single large bullish impulse bar (range/ATR), hold while
    price > EMA and not trailing-stop broken; hard time-stop.
    Path ≠ slow multiz vote.
    """
    out = {}
    for a, df in frames.items():
        c = df["close"].astype(float).values
        h = df["high"].astype(float).values
        l = df["low"].astype(float).values
        o = df["open"].astype(float).values
        av = df["atr"].astype(float).values
        adx_v = df["adx"].astype(float).values
        er_v = df["er"].astype(float).values
        em = ema(df["close"].astype(float), ema_n).values
        n = len(c)
        raw = np.zeros(n)
        pos = conf = held = 0
        peak = 0.0
        for i in range(1, n):
            rng = h[i] - l[i]
            bull = c[i] > o[i] and rng >= impulse_atr * max(av[i], 1e-12) and c[i] > c[i - 1]
            q = adx_v[i] >= adx_min and er_v[i] >= er_min
            if pos == 0:
                if q and bull:
                    conf += 1
                    if conf >= confirm:
                        pos = 1
                        held = 0
                        peak = c[i]
                else:
                    conf = 0
            else:
                held += 1
                peak = max(peak, c[i])
                trail = peak - hold_atr_trail * max(av[i], 1e-12)
                if c[i] < em[i] or c[i] < trail or held >= max_hold:
                    pos = 0
                    conf = 0
            raw[i] = pos
        out[a] = pd.Series(lag_sig(raw, lag), index=df.index)
    return out


# ── DNA 3: ATR compression → expansion break ─────────────────────────────────
def sig_compress(
    frames,
    atr_n=24,
    pct_n=336,
    pct_lo=0.25,
    break_n=48,
    exit_n=24,
    confirm=2,
    adx_min=14.0,
    lag=1,
) -> Dict[str, pd.Series]:
    """
    Trade only after realized ATR% is compressed (percentile low), then
    break prior break_n high — classic squeeze without Bollinger.
    """
    out = {}
    for a, df in frames.items():
        c = df["close"].astype(float)
        h = df["high"].astype(float)
        l = df["low"].astype(float)
        av = atr(h, l, c, atr_n)
        ap = (av / c.replace(0, np.nan)).fillna(0.0)
        # rolling percentile rank of atr%
        def pct_rank(x):
            last = x[-1]
            return float(np.mean(x <= last))

        pr = ap.rolling(pct_n, min_periods=max(48, pct_n // 4)).apply(pct_rank, raw=True)
        up, _ = donchian(h, l, break_n)
        _, lo = donchian(h, l, exit_n)
        adx_v = df["adx"].astype(float).values
        cv = c.values
        up_a = up.shift(1).values  # no look-ahead
        lo_a = lo.shift(1).values
        pr_a = pr.values
        n = len(cv)
        raw = np.zeros(n)
        pos = conf = 0
        compressed_recent = 0
        for i in range(n):
            if np.isfinite(pr_a[i]) and pr_a[i] <= pct_lo:
                compressed_recent = 24  # remember squeeze for 24h
            elif compressed_recent > 0:
                compressed_recent -= 1
            q = adx_v[i] >= adx_min
            if pos == 0:
                if q and compressed_recent > 0 and np.isfinite(up_a[i]) and cv[i] > up_a[i]:
                    conf += 1
                    if conf >= confirm:
                        pos = 1
                else:
                    conf = 0
            else:
                if np.isfinite(lo_a[i]) and cv[i] < lo_a[i]:
                    pos = 0
                    conf = 0
            raw[i] = pos
        out[a] = pd.Series(lag_sig(raw, lag), index=df.index)
    return out


# ── DNA 4: residual vs BTC beta ──────────────────────────────────────────────
def sig_beta_rs(
    frames,
    look=168,
    entry=0.02,
    exit=0.0,
    confirm=3,
    adx_min=16.0,
    er_min=0.07,
    lag=1,
    btc_key="BTC",
) -> Dict[str, pd.Series]:
    """
    For each alt: residual log-return vs beta*BTC over `look`.
    Long when cumulative residual rises (relative strength not raw momentum).
    BTC itself uses pure vol-z ROC fallback.
    """
    if btc_key not in frames:
        btc_key = list(frames.keys())[0]
    bc = frames[btc_key]["close"].astype(float)
    br = np.log(bc / bc.shift(1)).fillna(0.0)

    out = {}
    for a, df in frames.items():
        c = df["close"].astype(float)
        r = np.log(c / c.shift(1)).fillna(0.0)
        # align br
        br_a = br.reindex(c.index).fillna(0.0)
        if a == btc_key:
            vol = r.rolling(look, min_periods=48).std()
            z = (r.rolling(24).sum() / (vol * np.sqrt(24) + 1e-12)).fillna(0.0)
            score = z
        else:
            # rolling beta
            cov = r.rolling(look, min_periods=48).cov(br_a)
            var = br_a.rolling(look, min_periods=48).var().replace(0, np.nan)
            beta = (cov / var).fillna(1.0).clip(-3, 3)
            resid = r - beta * br_a
            score = resid.rolling(24, min_periods=8).sum().fillna(0.0)
        adx_v = df["adx"].astype(float).values
        er_v = df["er"].astype(float).values
        sc = score.values
        want_e = (sc >= entry) & (adx_v >= adx_min) & (er_v >= er_min)
        want_x = (sc <= exit) | (adx_v < adx_min * 0.65)
        out[a] = pd.Series(state_machine(want_e, want_x, confirm=confirm, lag=lag), index=df.index)
    return out


# ── DNA 5: twin MTF (4h EMA slope + 1h DI) ───────────────────────────────────
def sig_twin_mtf(
    frames,
    ema_4h=48,  # 48*1h ≈ 2d; we also require 4h-resampled slope
    di_gap=2.0,
    confirm=3,
    adx_min=18.0,
    er_min=0.08,
    lag=1,
) -> Dict[str, pd.Series]:
    out = {}
    for a, df in frames.items():
        c = df["close"].astype(float)
        # 4h proxy: ema of close, slope over 4 bars of 4h ≈ 16h of 1h → use ema diff over 16
        e = ema(c, ema_4h)
        slope = e - e.shift(16)
        # 4h trend: price above ema and slope > 0
        mtf_ok = (c > e) & (slope > 0)
        pdi = df["plus_di"].astype(float)
        mdi = df["minus_di"].astype(float)
        di_ok = (pdi - mdi) >= di_gap
        adx_v = df["adx"].astype(float).values
        er_v = df["er"].astype(float).values
        want_e = mtf_ok.values & di_ok.values & (adx_v >= adx_min) & (er_v >= er_min)
        want_x = (~mtf_ok.values) | ((pdi - mdi).values < 0) | (adx_v < adx_min * 0.7)
        out[a] = pd.Series(state_machine(want_e, want_x, confirm=confirm, lag=lag), index=df.index)
    return out


# ── DNA 6: path thrust (ER × ROC) ────────────────────────────────────────────
def sig_path_thrust(
    frames,
    roc_n=48,
    er_n=48,
    entry=0.04,
    exit=0.005,
    confirm=2,
    adx_min=17.0,
    lag=1,
) -> Dict[str, pd.Series]:
    """
    Thrust = Kaufman ER * ROC  (path efficiency times move size).
    High only when move is both large AND directional — unlike plain z-mom.
    """
    out = {}
    for a, df in frames.items():
        c = df["close"].astype(float)
        roc = c.pct_change(roc_n).fillna(0.0)
        er = kaufman_er(c, er_n).fillna(0.0)
        thrust = (er * roc).values
        adx_v = df["adx"].astype(float).values
        want_e = (thrust >= entry) & (adx_v >= adx_min)
        want_x = (thrust <= exit) | (adx_v < adx_min * 0.6)
        out[a] = pd.Series(state_machine(want_e, want_x, confirm=confirm, lag=lag), index=df.index)
    return out


# ── DNA 7: Keltner + RSI band ────────────────────────────────────────────────
def sig_kelt_rsi(
    frames,
    ma_n=120,
    atr_n=24,
    mult=1.8,
    rsi_n=14,
    rsi_max=72.0,
    rsi_exit=45.0,
    confirm=2,
    adx_min=16.0,
    er_min=0.06,
    lag=1,
) -> Dict[str, pd.Series]:
    out = {}
    for a, df in frames.items():
        c = df["close"].astype(float)
        h = df["high"].astype(float)
        l = df["low"].astype(float)
        mid = ema(c, ma_n)
        av = atr(h, l, c, atr_n)
        up = (mid + mult * av).shift(1)
        rs = rsi(c, rsi_n)
        adx_v = df["adx"].astype(float).values
        er_v = df["er"].astype(float).values
        want_e = (
            (c.values > up.values)
            & (rs.values <= rsi_max)
            & (adx_v >= adx_min)
            & (er_v >= er_min)
            & np.isfinite(up.values)
        )
        want_x = (c.values < mid.shift(1).values) | (rs.values < rsi_exit)
        out[a] = pd.Series(state_machine(want_e, want_x, confirm=confirm, lag=lag), index=df.index)
    return out


# ── DNA 8: prior-range reclaim ───────────────────────────────────────────────
def sig_range_reclaim(
    frames,
    range_n=72,
    confirm=3,
    adx_min=18.0,
    er_min=0.08,
    lag=1,
) -> Dict[str, pd.Series]:
    """
    Long when close reclaims prior range midpoint from below and holds
    above mid — mid-channel reclaim family (not Donchian high entry).
    """
    out = {}
    for a, df in frames.items():
        c = df["close"].astype(float)
        h = df["high"].astype(float)
        l = df["low"].astype(float)
        hh = h.rolling(range_n).max().shift(1)
        ll = l.rolling(range_n).min().shift(1)
        mid = (hh + ll) / 2.0
        adx_v = df["adx"].astype(float).values
        er_v = df["er"].astype(float).values
        cv = c.values
        mv = mid.values
        # reclaim: was below mid recently, now above
        below = cv < mv
        want_e = np.zeros(len(cv), dtype=bool)
        for i in range(1, len(cv)):
            if (
                np.isfinite(mv[i])
                and cv[i] > mv[i]
                and below[i - 1]
                and adx_v[i] >= adx_min
                and er_v[i] >= er_min
            ):
                want_e[i] = True
        want_x = cv < mv
        out[a] = pd.Series(state_machine(want_e, want_x, confirm=confirm, lag=lag), index=df.index)
    return out


def make_signal(cfg, frames):
    k = cfg["kind"]
    fr = {a: frames[a] for a in cfg.get("assets", ASSETS) if a in frames}
    p = {kk: vv for kk, vv in cfg.items() if kk not in ("kind", "ek", "assets", "tag")}
    fn = {
        "cs_resid": sig_cs_resid,
        "impulse": sig_impulse,
        "compress": sig_compress,
        "beta_rs": sig_beta_rs,
        "twin_mtf": sig_twin_mtf,
        "path_thrust": sig_path_thrust,
        "kelt_rsi": sig_kelt_rsi,
        "range_rc": sig_range_reclaim,
    }[k]
    return fn(fr, **p)


def ek(vt=0.12, cd=6, stop=False, ts=0, nf=0.40, top_k=0, pyr=0):
    d = base_ek(vt=vt, cd=cd, stop=stop, ts=ts, nf=nf, top_k=top_k)
    if pyr:
        d["pyramid_levels"] = int(pyr)
    return d


def bt(frames, side, capital=100.0, ekcfg=None):
    return run_enhanced(
        frames, side, capital=capital, trade_from=FULL_START, **(ekcfg or ek())
    )


def load_v001_apex_eq():
    p = os.path.join(RESULTS, "zenith_v001_apex_equity.csv")
    if not os.path.exists(p):
        return None
    s = pd.read_csv(p, index_col=0, parse_dates=True).iloc[:, 0]
    s.index = _tz(s.index)
    return s.astype(float)


def path_distance(eq_new: pd.Series, eq_old: pd.Series) -> float:
    """0..2-ish: higher = more different equity path (corr low / tracking error high)."""
    if eq_old is None:
        return 1.0
    a = eq_new.copy()
    b = eq_old.copy()
    a.index = _tz(a.index)
    b.index = _tz(b.index)
    idx = a.dropna().index.intersection(b.dropna().index)
    if len(idx) < 500:
        return 1.0
    ra = a.reindex(idx).pct_change().dropna()
    rb = b.reindex(idx).pct_change().dropna()
    idx2 = ra.index.intersection(rb.index)
    ra, rb = ra.reindex(idx2), rb.reindex(idx2)
    if ra.std() < 1e-12 or rb.std() < 1e-12:
        return 1.0
    corr = float(ra.corr(rb))
    # tracking error of log equity
    la = np.log(a.reindex(idx).replace(0, np.nan).ffill())
    lb = np.log(b.reindex(idx).replace(0, np.nan).ffill())
    te = float((la - lb).std())
    return float((1.0 - corr) + min(te * 5.0, 1.0))


def score_rebirth(win, n_trades, costs, path_div, capital=100.0) -> float:
    """
    OOS1-primary (anti-overfit) + diversity bonus vs v001 apex path.
    Never ranks on OOS2/6M.
    """
    o1, is_, full = win["OOS1"], win["IS"], win["FULL"]
    if not np.isfinite(o1.get("sharpe", np.nan)):
        return -99.0
    if o1["ret"] <= 0 or o1["sharpe"] < 0.7:
        return -90 + (o1.get("sharpe") or -1)
    if full["mdd"] < -0.15 or is_["sharpe"] < 0.5:
        return -75

    sh = float(o1["sharpe"])
    gap = max(0.0, sh - float(is_["sharpe"]))
    luck = 0.28 * gap
    crown = 0.0
    if sh > BENCH["almasi"]["oos1_sh"] and o1["ret"] > BENCH["almasi"]["oos1_ret"]:
        crown += 0.35
    if sh > BENCH["inst_stable"]["oos1_sh"] and o1["ret"] > BENCH["inst_stable"]["oos1_ret"]:
        crown += 0.45
    if sh >= BENCH["inst_primary"]["oos1_sh"] * 0.95 and o1["ret"] >= BENCH["inst_primary"]["oos1_ret"] * 0.9:
        crown += 0.30
    if full["sharpe"] > BENCH["inst_stable"]["full_sh"]:
        crown += 0.30
    # mild FULL quality (not 6M)
    full_b = 0.22 * min(max(full["sharpe"], 0), 2.2)
    ret_b = 0.35 * min(o1["ret"], 0.30) * 6
    # PATH DIVERSITY — key for "very different results"
    div_b = 0.55 * min(max(path_div, 0), 2.0)
    cost_drag = costs / capital
    tpen = 0.12 * max(0, n_trades - 550) / 550 if n_trades > 550 else (0.35 if n_trades < 12 else 0)
    # prefer not cloning v001 trade-count regime
    if 200 <= n_trades <= 800:
        tpen -= 0.08
    return float(sh + ret_b + full_b + crown + div_b - luck - 0.45 * cost_drag - tpen)


def eval_cfg(cfg, frames, eq_old, capital=100.0):
    try:
        side = make_signal(cfg, frames)
        assets = cfg.get("assets", ASSETS)
        fr = {a: frames[a] for a in assets}
        for a in assets:
            if a not in side:
                side[a] = pd.Series(0.0, index=frames[a].index)
        port = bt(fr, side, capital=capital, ekcfg=cfg.get("ek"))
        eq = equity_series(port)
        win = window_metrics(eq, capital)
        costs = float(np.nansum(port["costs"])) if isinstance(port["costs"], np.ndarray) else float(port.get("costs") or 0)
        tr = port.get("trades") or []
        pdv = path_distance(eq, eq_old)
        sc = score_rebirth(win, len(tr), costs, pdv, capital)
        return dict(
            cfg=cfg, windows=win, score=sc, n_trades=len(tr), costs=costs,
            trade_stats=trade_stats(tr) if tr else {"n": 0},
            end_equity=float(eq.dropna().iloc[-1]), equity=eq,
            hard=hard_pass(win), beats=beats_champions(win),
            path_div=pdv,
        )
    except Exception as e:
        return dict(error=str(e), cfg=cfg, score=-999.0, path_div=0.0)


def build(quick=False) -> List[dict]:
    c: List[dict] = []
    vts = [0.10, 0.12, 0.14] if not quick else [0.12]
    cds = [4, 8, 12] if not quick else [6, 10]

    # cs residual
    for roc, ez, conf, adx_m, er_m, vt, cd in itertools.product(
        [12, 24, 48, 72] if not quick else [24, 48],
        [0.15, 0.25, 0.35, 0.50] if not quick else [0.25, 0.40],
        [1, 2, 3] if not quick else [2],
        [12.0, 16.0, 20.0] if not quick else [16.0],
        [0.04, 0.08] if not quick else [0.06],
        vts, cds,
    ):
        c.append(dict(
            kind="cs_resid", tag=f"CS_r{roc}_z{ez}_c{conf}_a{adx_m}",
            roc_n=roc, entry_z=ez, exit_z=-0.05, top_frac=0.67,
            confirm=conf, adx_min=adx_m, er_min=er_m,
            ek=ek(vt, cd=cd), assets=ASSETS,
        ))

    # impulse
    for imp, en, mx, conf, adx_m, vt, cd in itertools.product(
        [1.4, 1.8, 2.2, 2.8] if not quick else [1.8, 2.4],
        [48, 72, 120] if not quick else [72],
        [72, 120, 168] if not quick else [120],
        [1, 2] if not quick else [1],
        [14.0, 18.0] if not quick else [16.0],
        vts, cds,
    ):
        c.append(dict(
            kind="impulse", tag=f"IMP_{imp}_e{en}_h{mx}_c{conf}",
            impulse_atr=imp, ema_n=en, hold_atr_trail=2.2, max_hold=mx,
            confirm=conf, adx_min=adx_m, er_min=0.05,
            ek=ek(vt, cd=cd, ts=0), assets=ASSETS,
        ))

    # compress
    for plo, bn, conf, adx_m, vt, cd in itertools.product(
        [0.15, 0.25, 0.35] if not quick else [0.25],
        [36, 48, 72, 96] if not quick else [48, 72],
        [2, 3] if not quick else [2],
        [12.0, 16.0, 20.0] if not quick else [16.0],
        vts, cds,
    ):
        c.append(dict(
            kind="compress", tag=f"CMP_p{plo}_b{bn}_c{conf}_a{adx_m}",
            pct_lo=plo, break_n=bn, exit_n=max(18, bn // 2), confirm=conf,
            adx_min=adx_m, ek=ek(vt, cd=cd), assets=ASSETS,
        ))

    # beta residual
    for look, ent, conf, adx_m, vt, cd in itertools.product(
        [96, 168, 240] if not quick else [168],
        [0.01, 0.02, 0.035, 0.05] if not quick else [0.02, 0.04],
        [2, 3, 5] if not quick else [3],
        [14.0, 18.0, 22.0] if not quick else [16.0],
        vts, cds,
    ):
        c.append(dict(
            kind="beta_rs", tag=f"BETA_l{look}_e{ent}_c{conf}",
            look=look, entry=ent, exit=0.0, confirm=conf, adx_min=adx_m,
            er_min=0.06, ek=ek(vt, cd=cd), assets=ASSETS,
        ))

    # twin mtf
    for e4, gap, conf, adx_m, vt, cd in itertools.product(
        [36, 48, 72, 96] if not quick else [48, 72],
        [1.0, 2.0, 3.5] if not quick else [2.0],
        [2, 3, 5] if not quick else [3],
        [16.0, 20.0, 24.0] if not quick else [18.0],
        vts, cds,
    ):
        c.append(dict(
            kind="twin_mtf", tag=f"TWIN_e{e4}_g{gap}_c{conf}_a{adx_m}",
            ema_4h=e4, di_gap=gap, confirm=conf, adx_min=adx_m, er_min=0.07,
            ek=ek(vt, cd=cd), assets=ASSETS,
        ))

    # path thrust
    for rn, ent, conf, adx_m, vt, cd in itertools.product(
        [24, 48, 72, 96] if not quick else [48, 72],
        [0.02, 0.035, 0.05, 0.07] if not quick else [0.035, 0.055],
        [1, 2, 3] if not quick else [2],
        [14.0, 18.0, 22.0] if not quick else [18.0],
        vts, cds,
    ):
        c.append(dict(
            kind="path_thrust", tag=f"THR_r{rn}_e{ent}_c{conf}",
            roc_n=rn, er_n=rn, entry=ent, exit=ent * 0.15, confirm=conf,
            adx_min=adx_m, ek=ek(vt, cd=cd), assets=ASSETS,
        ))

    # kelt rsi
    for ma, mult, rmax, conf, vt, cd in itertools.product(
        [72, 120, 168] if not quick else [120],
        [1.4, 1.8, 2.2] if not quick else [1.8],
        [65.0, 72.0, 78.0] if not quick else [72.0],
        [2, 3] if not quick else [2],
        vts, cds,
    ):
        c.append(dict(
            kind="kelt_rsi", tag=f"KELT_m{ma}_x{mult}_r{rmax}_c{conf}",
            ma_n=ma, mult=mult, rsi_max=rmax, rsi_exit=42.0, confirm=conf,
            adx_min=16.0, er_min=0.06, ek=ek(vt, cd=cd), assets=ASSETS,
        ))

    # range reclaim
    for rn, conf, adx_m, vt, cd in itertools.product(
        [48, 72, 96, 120] if not quick else [72, 96],
        [2, 3, 5] if not quick else [3],
        [16.0, 20.0, 24.0] if not quick else [18.0],
        vts, cds,
    ):
        c.append(dict(
            kind="range_rc", tag=f"RRC_n{rn}_c{conf}_a{adx_m}",
            range_n=rn, confirm=conf, adx_min=adx_m, er_min=0.07,
            ek=ek(vt, cd=cd), assets=ASSETS,
        ))

    # stratified sample if huge
    if len(c) > 420:
        rng = np.random.default_rng(77)
        by: Dict[str, list] = {}
        for x in c:
            by.setdefault(x["kind"], []).append(x)
        quota = {
            "cs_resid": 70, "impulse": 55, "compress": 50, "beta_rs": 55,
            "twin_mtf": 55, "path_thrust": 55, "kelt_rsi": 45, "range_rc": 40,
        }
        picked = []
        for k, arr in by.items():
            q = quota.get(k, 40)
            if len(arr) <= q:
                picked.extend(arr)
            else:
                idx = rng.choice(len(arr), size=q, replace=False)
                picked.extend([arr[i] for i in idx])
        c = picked
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    t0 = time.time()
    print("ZENITH apex REBIRTH — loading panel…")
    frames = prepare(ASSETS)
    eq_old = load_v001_apex_eq()
    print("v001 apex equity loaded:", eq_old is not None, "bars" if eq_old is None else len(eq_old))
    cands = build(quick=args.quick)
    print(f"candidates: {len(cands)}")

    results = []
    best = -999.0
    for i, cfg in enumerate(cands):
        r = eval_cfg(cfg, frames, eq_old)
        if r.get("score", -999) > -900:
            results.append(r)
        if r.get("score", -999) > best:
            best = r["score"]
            w = r["windows"]
            print(
                f"[{i+1}/{len(cands)}] ★ {best:.3f} {cfg.get('tag','')[:34]:34s} "
                f"OOS1 {w['OOS1']['ret']*100:+.2f}%/{w['OOS1']['sharpe']:.2f} "
                f"FULL {w['FULL']['sharpe']:.2f}/{w['FULL']['mdd']*100:.1f}% "
                f"6M {w['6M']['ret']*100:+.2f}% n={r['n_trades']} "
                f"div={r.get('path_div',0):.2f} hard={r['hard']}"
            )
        elif (i + 1) % 40 == 0:
            print(f"[{i+1}/{len(cands)}] best={best:.3f}")

    results.sort(key=lambda x: (x.get("hard", False), x["score"]), reverse=True)
    print("\nTOP 25 singles:")
    for j, r in enumerate(results[:25]):
        w = r["windows"]
        b = r["beats"]
        print(
            f"{j+1:2d}. {r['score']:.3f} h={r['hard']} div={r.get('path_div',0):.2f} "
            f"{str(r['cfg'].get('tag'))[:32]:32s} "
            f"OOS1 {w['OOS1']['ret']*100:+5.2f}/{w['OOS1']['sharpe']:.2f} "
            f"F {w['FULL']['sharpe']:.2f}/{w['FULL']['mdd']*100:.1f} "
            f"6M {w['6M']['ret']*100:+5.2f} n={r['n_trades']} "
            f"P={b['beats_inst_primary_oos1']} ST={b['beats_inst_stable_oos1']} "
            f"Fsh={b['beats_all_full_sh']}"
        )

    # blends of complementary kinds
    print("\nBlends…")
    # take top hard per kind
    by_kind = {}
    for r in results:
        if not r.get("hard"):
            continue
        k = r["cfg"]["kind"]
        if k not in by_kind or r["score"] > by_kind[k]["score"]:
            # need equity
            rr = eval_cfg(r["cfg"], frames, eq_old)
            if rr and "equity" in rr:
                by_kind[k] = rr
    rich = sorted(by_kind.values(), key=lambda x: x["score"], reverse=True)[:6]
    # also force top 4 overall hard
    for r in results:
        if r.get("hard") and len(rich) < 8:
            if all(r["cfg"].get("tag") != x["cfg"].get("tag") for x in rich):
                rr = eval_cfg(r["cfg"], frames, eq_old)
                if rr and "equity" in rr:
                    rich.append(rr)

    blends = []
    for r in rich:
        pdv = path_distance(r["equity"], eq_old)
        blends.append(dict(
            name=f"single:{r['cfg'].get('tag')}",
            type="single", windows=r["windows"], score=r["score"], hard=r["hard"],
            beats=r["beats"], n_trades=r["n_trades"], costs=r["costs"],
            equity=r["equity"], legs=[r["cfg"]], end_equity=r["end_equity"],
            trade_stats=r["trade_stats"], path_div=pdv, weights=(1.0,),
        ))

    for i, j in itertools.combinations(range(len(rich)), 2):
        if rich[i]["cfg"]["kind"] == rich[j]["cfg"]["kind"]:
            continue
        for wa, wb in ((0.7, 0.3), (0.6, 0.4), (0.55, 0.45), (0.8, 0.2), (0.5, 0.5)):
            eq = blend_equity([(wa, rich[i]["equity"]), (wb, rich[j]["equity"])])
            win = window_metrics(eq)
            n_tr = int(wa * rich[i]["n_trades"] + wb * rich[j]["n_trades"])
            costs = wa * rich[i]["costs"] + wb * rich[j]["costs"]
            pdv = path_distance(eq, eq_old)
            sc = score_rebirth(win, n_tr, costs, pdv)
            blends.append(dict(
                name=f"b_{rich[i]['cfg']['kind'][:6]}_{rich[j]['cfg']['kind'][:6]}_{int(wa*100)}",
                type="blend", weights=(wa, wb), windows=win, score=sc,
                hard=hard_pass(win), beats=beats_champions(win),
                n_trades=n_tr, costs=costs, equity=eq,
                legs=[rich[i]["cfg"], rich[j]["cfg"]],
                end_equity=float(eq.dropna().iloc[-1]), path_div=pdv,
            ))

    if len(rich) >= 3:
        for ws in ((0.5, 0.3, 0.2), (0.45, 0.35, 0.20), (0.4, 0.35, 0.25)):
            eq = blend_equity([(ws[k], rich[k]["equity"]) for k in range(3)])
            win = window_metrics(eq)
            n_tr = int(sum(ws[k] * rich[k]["n_trades"] for k in range(3)))
            costs = sum(ws[k] * rich[k]["costs"] for k in range(3))
            pdv = path_distance(eq, eq_old)
            sc = score_rebirth(win, n_tr, costs, pdv)
            blends.append(dict(
                name=f"trip_{'_'.join(str(int(x*100)) for x in ws)}",
                type="triple", weights=ws, windows=win, score=sc,
                hard=hard_pass(win), beats=beats_champions(win),
                n_trades=n_tr, costs=costs, equity=eq,
                legs=[rich[k]["cfg"] for k in range(3)],
                end_equity=float(eq.dropna().iloc[-1]), path_div=pdv,
            ))

    def rk(b):
        bt = b.get("beats") or {}
        # prioritize hard + path diversity + crown + score
        div = float(b.get("path_div") or 0)
        crown = (
            3 * int(bt.get("beats_inst_primary_oos1", False))
            + 2 * int(bt.get("beats_inst_stable_oos1", False))
            + int(bt.get("beats_all_full_sh", False))
            + int(bt.get("beats_almasi_oos1", False))
        )
        # require meaningful diversity for top rank
        div_gate = 1 if div >= 0.55 else 0
        return (b.get("hard", False), div_gate, crown, div, b["score"])

    blends.sort(key=rk, reverse=True)
    print("Top 15 final:")
    for j, b in enumerate(blends[:15]):
        w = b["windows"]
        bt = b.get("beats") or {}
        print(
            f"{j+1:2d}. {b['name'][:46]:46s} sc={b['score']:.3f} h={b['hard']} "
            f"div={b.get('path_div',0):.2f} "
            f"OOS1 {w['OOS1']['ret']*100:+5.2f}/{w['OOS1']['sharpe']:.2f} "
            f"F {w['FULL']['sharpe']:.2f}/{w['FULL']['mdd']*100:.1f} "
            f"6M {w['6M']['ret']*100:+5.2f} n={b['n_trades']} "
            f"P={bt.get('beats_inst_primary_oos1')} ST={bt.get('beats_inst_stable_oos1')} "
            f"Fsh={bt.get('beats_all_full_sh')}"
        )

    # pick winner: hard + diverse + best crown/score
    winner = blends[0]
    # prefer div>=0.6 if available among hard
    for b in blends:
        if b.get("hard") and float(b.get("path_div") or 0) >= 0.60 and b["windows"]["OOS1"]["sharpe"] >= 1.0:
            winner = b
            break
    else:
        for b in blends:
            if b.get("hard") and float(b.get("path_div") or 0) >= 0.45:
                winner = b
                break

    w = winner["windows"]
    print("\n*** REBIRTH APEX WINNER ***", winner["name"])
    for lab in ("IS", "OOS1", "OOS2", "6M", "FULL"):
        ww = w[lab]
        print(f"  {lab:5s} ret={ww['ret']*100:+7.2f}% Sh={ww['sharpe']:6.2f} MDD={ww['mdd']*100:7.2f}%")
    print(" beats", winner.get("beats"))
    print(f" end={winner['end_equity']:.2f} n={winner['n_trades']} costs={winner['costs']:.2f} div={winner.get('path_div'):.3f}")

    # compare to v001 apex windows
    v001 = {
        "OOS1": dict(ret=0.1166, sharpe=2.10, mdd=-0.020),
        "FULL": dict(ret=0.3756, sharpe=1.20, mdd=-0.043),
        "6M": dict(ret=0.0171, sharpe=0.55, mdd=-0.041),
        "IS": dict(ret=0.2158, sharpe=1.09, mdd=-0.042),
        "OOS2": dict(ret=0.0130, sharpe=0.42, mdd=-0.041),
    }
    print("\nΔ vs v001 apex:")
    for lab in ("IS", "OOS1", "OOS2", "6M", "FULL"):
        print(
            f"  {lab:5s} Δret={(w[lab]['ret']-v001[lab]['ret'])*100:+.2f}pp "
            f"ΔSh={w[lab]['sharpe']-v001[lab]['sharpe']:+.2f} "
            f"ΔMDD={(w[lab]['mdd']-v001[lab]['mdd'])*100:+.2f}pp"
        )

    winner["equity"].to_csv(os.path.join(RESULTS, "zenith_apex2_equity.csv"), header=["eq"])

    payload = dict(
        name="zenith-apex2-rebirth",
        title="ZENITH apex REBIRTH — new DNA, different path vs v001 multiz2",
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
        trade_stats=winner.get("trade_stats"),
        path_div=winner.get("path_div"),
        vs_v001_apex={
            lab: dict(
                delta_ret=w[lab]["ret"] - v001[lab]["ret"],
                delta_sh=w[lab]["sharpe"] - v001[lab]["sharpe"],
                delta_mdd=w[lab]["mdd"] - v001[lab]["mdd"],
            )
            for lab in v001
        },
        top_singles=[{
            "score": r["score"], "hard": r["hard"], "cfg": r["cfg"],
            "windows": r["windows"], "beats": r["beats"],
            "n_trades": r["n_trades"], "path_div": r.get("path_div"),
        } for r in results[:20]],
        top_final=[{
            "name": b["name"], "score": b["score"], "hard": b.get("hard"),
            "windows": b["windows"], "beats": b.get("beats"),
            "weights": b.get("weights"), "legs": b.get("legs"),
            "path_div": b.get("path_div"), "n_trades": b.get("n_trades"),
        } for b in blends[:15]],
        dna_families=list({c["kind"] for c in cands}),
        protocol="IS observe / OOS1 select / OOS2+6M report / MDD<=15% / path-diversity vs v001",
        elapsed_sec=round(time.time() - t0, 1),
        n_scanned=len(results),
    )
    with open(os.path.join(RESULTS, "zenith_apex2_results.json"), "w") as f:
        json.dump(payload, f, indent=2, default=str)

    print("saved zenith_apex2_results.json · equity · elapsed", payload["elapsed_sec"])


if __name__ == "__main__":
    main()
