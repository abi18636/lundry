#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ZENITH refine — deeper search to BEAT inst-v3 / almasi
Focus: multi-horizon z-momentum, ATR-channel, ER-breakout, DI+z hybrid,
       MTF refined, equity blends with crown objective.
"""
from __future__ import annotations

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

from alt_turtle_ns import stats, trade_stats, atr, ema, kaufman_er, donchian, HOURS_PER_YEAR
from alt_optimize import run_enhanced
from zenith_discover import (
    prepare, ASSETS, FULL_START, IS_END, OOS1_START, OOS2_START, BENCH,
    _tz, equity_series, window_metrics, blend_equity, hard_pass, beats_champions,
    sig_supertrend, sig_di_trend, sig_mtf_ema, sig_zmom, sig_kama_cross,
    sig_mid_reclaim, sig_hybrid_st_di, sig_bb_squeeze,
)

# ── new / refined signals ───────────────────────────────────────────────────
def sig_multiz(
    frames, horizons=(24, 96, 336), z_entry=0.8, z_exit=0.0,
    vote_min=0.34, confirm=3, adx_min=18.0, er_min=0.10,
    vol_n=168, long_only=True, lag=1,
) -> Dict[str, pd.Series]:
    """
    Multi-horizon vol-normalized momentum VOTE (novel vs plain sign-TSMOM).
    Each horizon contributes sign(z_h); enter when mean vote >= vote_min.
    """
    out = {}
    for a, df in frames.items():
        c = df["close"].astype(float)
        rets = c.pct_change()
        vol = rets.rolling(vol_n, min_periods=max(24, vol_n // 4)).std()
        votes = np.zeros(len(c))
        for h in horizons:
            roc = c.pct_change(h)
            z = (roc / (vol * np.sqrt(h) + 1e-12)).replace([np.inf, -np.inf], np.nan).fillna(0)
            votes += np.sign(z.values - 0.0) * (np.abs(z.values) >= z_entry * 0.5).astype(float)
            # softer: use tanh of z
            # votes += np.tanh(z.values)
        votes /= max(len(horizons), 1)
        # recompute cleaner: average of tanh(z_h)
        acc = np.zeros(len(c))
        for h in horizons:
            roc = c.pct_change(h)
            z = (roc / (vol * np.sqrt(h) + 1e-12)).replace([np.inf, -np.inf], np.nan).fillna(0).values
            acc += np.tanh(z)
        acc /= max(len(horizons), 1)

        adx_v = df["adx"].astype(float).values
        er_v = df["er"].astype(float).values
        n = len(c)
        raw = np.zeros(n)
        pos = conf = 0
        for i in range(n):
            q = (adx_v[i] >= adx_min) and (er_v[i] >= er_min)
            v = acc[i]
            if pos == 0:
                if q and v >= z_entry * 0.5 + vote_min * 0.5:  # combined
                    # simpler threshold on vote
                    if v >= vote_min:
                        conf += 1
                        if conf >= confirm:
                            pos = 1
                    else:
                        conf = 0
                else:
                    conf = 0
            else:
                if v <= z_exit or not q:
                    pos = 0
                    conf = 0
            raw[i] = pos
        sig = np.zeros(n)
        sig[lag:] = raw[:-lag]
        out[a] = pd.Series(sig, index=df.index)
    return out


def sig_multiz_v2(
    frames, horizons=(24, 168, 720), entry=0.35, exit=0.05,
    confirm=4, adx_min=20.0, er_min=0.10, vol_n=336, lag=1,
) -> Dict[str, pd.Series]:
    """Clean multi-horizon tanh(z) vote — institutional residual momentum."""
    out = {}
    for a, df in frames.items():
        c = df["close"].astype(float)
        rets = np.log(c / c.shift(1))
        vol = rets.rolling(vol_n, min_periods=48).std()
        acc = np.zeros(len(c))
        for h in horizons:
            # log return over h normalized by vol*sqrt(h)
            lr = np.log(c / c.shift(h))
            z = (lr / (vol * np.sqrt(h) + 1e-12)).replace([np.inf, -np.inf], np.nan).fillna(0).values
            acc += np.tanh(z)
        acc /= max(len(horizons), 1)
        adx_v = df["adx"].astype(float).values
        er_v = df["er"].astype(float).values
        n = len(c)
        raw = np.zeros(n)
        pos = conf = 0
        for i in range(n):
            q = (adx_v[i] >= adx_min) and (er_v[i] >= er_min)
            if pos == 0:
                if q and acc[i] >= entry:
                    conf += 1
                    if conf >= confirm:
                        pos = 1
                else:
                    conf = 0
            else:
                if acc[i] <= exit or not q:
                    pos = 0
                    conf = 0
            raw[i] = pos
        sig = np.zeros(n)
        sig[lag:] = raw[:-lag]
        out[a] = pd.Series(sig, index=df.index)
    return out


def sig_atr_channel(
    frames, ma_n=168, atr_n=24, entry_mult=2.0, exit_mult=0.5,
    adx_min=18.0, er_min=0.10, confirm=3, lag=1,
) -> Dict[str, pd.Series]:
    """ATR envelope channel breakout (Keltner-style) — distinct from Donchian."""
    out = {}
    for a, df in frames.items():
        c = df["close"].astype(float)
        h = df["high"].astype(float)
        l = df["low"].astype(float)
        mid = ema(c, ma_n)
        av = atr(h, l, c, atr_n)
        up = (mid + entry_mult * av).values
        mid_a = mid.values
        ex = (mid - exit_mult * av).values  # exit below mid - small atr
        adx_v = df["adx"].astype(float).values
        er_v = df["er"].astype(float).values
        cv = c.values
        n = len(cv)
        raw = np.zeros(n)
        pos = conf = 0
        for i in range(1, n):
            q = (adx_v[i] >= adx_min) and (er_v[i] >= er_min)
            # use prior bar channel to avoid look-ahead
            if pos == 0:
                if q and np.isfinite(up[i - 1]) and cv[i] > up[i - 1]:
                    conf += 1
                    if conf >= confirm:
                        pos = 1
                else:
                    conf = 0
            else:
                if np.isfinite(mid_a[i - 1]) and cv[i] < mid_a[i - 1]:
                    pos = 0
                    conf = 0
            raw[i] = pos
        sig = np.zeros(n)
        sig[lag:] = raw[:-lag]
        out[a] = pd.Series(sig, index=df.index)
    return out


def sig_er_breakout(
    frames, er_n=48, er_gate=0.15, ch_n=120, confirm=2,
    adx_min=15.0, lag=1,
) -> Dict[str, pd.Series]:
    """Only trade Donchian breakouts when ER is HIGH (trending regime gate first)."""
    out = {}
    for a, df in frames.items():
        c = df["close"].astype(float)
        h = df["high"].astype(float)
        l = df["low"].astype(float)
        er = kaufman_er(c, er_n).values
        up, lo = donchian(h, l, ch_n)
        up_a, lo_a = up.values, lo.values
        adx_v = df["adx"].astype(float).values
        cv = c.values
        n = len(cv)
        raw = np.zeros(n)
        pos = conf = 0
        for i in range(n):
            q = (er[i] >= er_gate) and (adx_v[i] >= adx_min)
            if pos == 0:
                if q and np.isfinite(up_a[i]) and cv[i] > up_a[i]:
                    conf += 1
                    if conf >= confirm:
                        pos = 1
                else:
                    conf = 0
            else:
                if (np.isfinite(lo_a[i]) and cv[i] < lo_a[i]) or er[i] < er_gate * 0.5:
                    pos = 0
                    conf = 0
            raw[i] = pos
        sig = np.zeros(n)
        sig[lag:] = raw[:-lag]
        out[a] = pd.Series(sig, index=df.index)
    return out


def sig_consensus3(
    frames, st_mult=2.5, di_gap=1.0, z_entry=0.3,
    adx_min=18.0, er_min=0.08, confirm=2, lag=1,
) -> Dict[str, pd.Series]:
    """Need 2-of-3: SuperTrend long, DI+, multi-z positive."""
    st = sig_supertrend(frames, mult=st_mult, adx_min=0, er_min=0, confirm=1, lag=0)
    di = sig_di_trend(frames, adx_min=0, er_min=0, confirm=1, di_gap=di_gap, lag=0)
    mz = sig_multiz_v2(frames, entry=z_entry, exit=-0.05, confirm=1, adx_min=0, er_min=0, lag=0)
    out = {}
    for a in frames:
        a1 = (st[a].values > 0).astype(int)
        a2 = (di[a].values > 0).astype(int)
        a3 = (mz[a].values > 0).astype(int)
        votes = a1 + a2 + a3
        adx_v = frames[a]["adx"].astype(float).values
        er_v = frames[a]["er"].astype(float).values
        n = len(votes)
        raw = np.zeros(n)
        pos = conf = 0
        for i in range(n):
            q = (adx_v[i] >= adx_min) and (er_v[i] >= er_min)
            want = q and votes[i] >= 2
            if pos == 0:
                if want:
                    conf += 1
                    if conf >= confirm:
                        pos = 1
                else:
                    conf = 0
            else:
                if votes[i] < 2 or not q:
                    pos = 0
                    conf = 0
            raw[i] = pos
        sig = np.zeros(n)
        sig[lag:] = raw[:-lag]
        out[a] = pd.Series(sig, index=frames[a].index)
    return out


def make_signal(cfg, frames):
    k = cfg["kind"]
    fr = {a: frames[a] for a in cfg.get("assets", ASSETS) if a in frames}
    p = {kk: vv for kk, vv in cfg.items() if kk not in ("kind", "ek", "assets", "tag")}
    fn = {
        "multiz": sig_multiz,
        "multiz2": sig_multiz_v2,
        "atr_ch": sig_atr_channel,
        "er_bo": sig_er_breakout,
        "cons3": sig_consensus3,
        "zmom": sig_zmom,
        "mtf_ema": sig_mtf_ema,
        "di_trend": sig_di_trend,
        "supertrend": sig_supertrend,
        "hybrid_st_di": sig_hybrid_st_di,
        "kama": sig_kama_cross,
        "mid_reclaim": sig_mid_reclaim,
        "bb_squeeze": sig_bb_squeeze,
    }[k]
    return fn(fr, **p)


def ek(vt=0.12, cd=4, stop=False, ts=0, nf=0.42, top_k=0):
    d = dict(
        sizing="vol_target", vol_target=vt, notional_frac=nf,
        max_positions=3, lev_cap=1.0, cooldown=cd, short_frac_mult=0.0,
        use_atr_stop=stop, atr_stop_mult=3.0 if stop else 99.0,
        atr_trail_mult=3.5 if stop else 99.0,
        breakeven_at_R=1.0 if stop else 0.0, time_stop_bars=ts,
        pyramid_levels=0, risk_per_trade=0.01, atr_risk_mult=2.5,
        vol_lookback=336, top_k=top_k, top_k_lookback=72,
    )
    return d


def bt(frames, side, capital=100.0, ekcfg=None):
    return run_enhanced(
        frames, side, capital=capital, trade_from=FULL_START, **(ekcfg or ek())
    )


def score_crown(win, n_trades, costs, capital=100.0) -> float:
    """Selection: OOS1 primary + crown bonus for beating champions. No OOS2/6M."""
    o1, is_, full = win["OOS1"], win["IS"], win["FULL"]
    if not np.isfinite(o1.get("sharpe", np.nan)):
        return -99.0
    if o1["ret"] <= 0 or o1["sharpe"] < 0.7:
        return -90 + (o1.get("sharpe") or -1)
    if full["mdd"] < -0.15 or is_["sharpe"] < 0.5:
        return -75

    sh = float(o1["sharpe"])
    gap = max(0.0, sh - float(is_["sharpe"]))
    luck = 0.30 * gap
    # crown bonuses (still OOS1/FULL only — FULL sharpe mild)
    crown = 0.0
    if sh > BENCH["inst_stable"]["oos1_sh"] and o1["ret"] > BENCH["inst_stable"]["oos1_ret"]:
        crown += 0.55
    if sh > BENCH["inst_primary"]["oos1_sh"] and o1["ret"] > BENCH["inst_primary"]["oos1_ret"]:
        crown += 0.35
    if full["sharpe"] > BENCH["inst_stable"]["full_sh"]:
        crown += 0.25
    if full["mdd"] > BENCH["inst_primary"]["full_mdd"]:  # less negative
        crown += 0.10

    cost_drag = costs / capital
    tpen = 0.15 * max(0, n_trades - 500) / 500 if n_trades > 500 else (0.3 if n_trades < 15 else 0)
    ret_b = 0.4 * min(o1["ret"], 0.25) * 6
    full_b = 0.2 * min(max(full["sharpe"], 0), 2.2)
    return float(sh + ret_b + full_b + crown - luck - 0.45 * cost_drag - tpen)


def eval_cfg(cfg, frames, capital=100.0):
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
        sc = score_crown(win, len(tr), costs, capital)
        return dict(
            cfg=cfg, windows=win, score=sc, n_trades=len(tr), costs=costs,
            trade_stats=trade_stats(tr) if tr else {"n": 0},
            end_equity=float(eq.dropna().iloc[-1]), equity=eq,
            hard=hard_pass(win), beats=beats_champions(win),
        )
    except Exception as e:
        return dict(error=str(e), cfg=cfg, score=-999.0)


def build() -> List[dict]:
    c = []
    # Multi-z v2 deep grid — closest to beating TSMOM quality
    for hz, entry, ex, conf, adx_m, er_m, vt, cd in itertools.product(
        [(24, 168, 720), (24, 96, 336), (48, 168, 504), (12, 72, 336, 720), (72, 288, 720)],
        [0.25, 0.35, 0.45, 0.55, 0.65],
        [0.0, 0.05, 0.10, 0.15],
        [2, 3, 5, 8],
        [15.0, 18.0, 20.0, 22.0, 25.0],
        [0.06, 0.08, 0.10, 0.12],
        [0.10, 0.12, 0.14],
        [4, 8],
    ):
        c.append(dict(
            kind="multiz2", tag=f"MZ2_{hz[0]}h_e{entry}_c{conf}_a{adx_m}",
            horizons=hz, entry=entry, exit=ex, confirm=conf,
            adx_min=adx_m, er_min=er_m, vol_n=336,
            ek=ek(vt, cd=cd), assets=ASSETS,
        ))

    # ATR channel
    for ma, em, conf, adx_m, vt in itertools.product(
        [96, 168, 336, 504],
        [1.5, 2.0, 2.5, 3.0],
        [2, 3, 5],
        [15.0, 18.0, 22.0],
        [0.10, 0.12, 0.14],
    ):
        c.append(dict(
            kind="atr_ch", tag=f"ATR_m{ma}_x{em}_c{conf}",
            ma_n=ma, entry_mult=em, exit_mult=0.0, confirm=conf,
            adx_min=adx_m, er_min=0.10, ek=ek(vt, cd=6), assets=ASSETS,
        ))

    # ER breakout
    for erg, ch, conf, adx_m, vt in itertools.product(
        [0.12, 0.15, 0.20, 0.25],
        [72, 120, 168, 240, 336],
        [1, 2, 3],
        [12.0, 15.0, 20.0],
        [0.10, 0.12, 0.14],
    ):
        c.append(dict(
            kind="er_bo", tag=f"ER_{erg}_ch{ch}_c{conf}",
            er_gate=erg, ch_n=ch, confirm=conf, adx_min=adx_m, er_n=48,
            ek=ek(vt, cd=4), assets=ASSETS,
        ))

    # Consensus 3
    for sm, conf, adx_m, vt in itertools.product(
        [2.0, 2.5, 3.0], [2, 3, 5], [18.0, 22.0, 25.0], [0.10, 0.12, 0.14],
    ):
        c.append(dict(
            kind="cons3", tag=f"C3_m{sm}_c{conf}_a{adx_m}",
            st_mult=sm, confirm=conf, adx_min=adx_m, er_min=0.10,
            di_gap=1.0, z_entry=0.25, ek=ek(vt, cd=6), assets=ASSETS,
        ))

    # Refined MTF (was strong raw return)
    for fa, sl, conf, adx_m, er_m, vt in itertools.product(
        [8, 12, 18, 24],
        [168, 336, 504, 720],
        [2, 3, 5, 8],
        [12.0, 16.0, 20.0, 22.0],
        [0.06, 0.08, 0.10, 0.12],
        [0.10, 0.12, 0.14],
    ):
        if fa >= sl // 4:
            continue
        c.append(dict(
            kind="mtf_ema", tag=f"MTF_{fa}_{sl}_c{conf}_a{adx_m}",
            fast=fa, slow=sl, confirm=conf, adx_min=adx_m, er_min=er_m,
            h4_fast=max(12, fa * 3), h4_slow=max(48, sl // 2),
            ek=ek(vt, cd=4), assets=ASSETS,
        ))

    # Refined single-z (v1 best family)
    for roc, ze, conf, adx_m, er_m, vt in itertools.product(
        [48, 72, 96, 144, 192],
        [1.0, 1.15, 1.25, 1.4, 1.6],
        [2, 3, 4, 5],
        [12.0, 15.0, 18.0, 22.0],
        [0.06, 0.08, 0.10, 0.12],
        [0.10, 0.12, 0.14],
    ):
        c.append(dict(
            kind="zmom", tag=f"Z_r{roc}_z{ze}_c{conf}_a{adx_m}",
            roc_n=roc, z_entry=ze, z_exit=0.0, confirm=conf,
            adx_min=adx_m, er_min=er_m, vol_n=168,
            ek=ek(vt, cd=4), assets=ASSETS,
        ))

    # DI refined
    for adx_m, gap, conf, vt in itertools.product(
        [20.0, 25.0, 30.0], [1.0, 2.0, 3.0, 5.0], [3, 5, 8], [0.10, 0.12, 0.14],
    ):
        c.append(dict(
            kind="di_trend", tag=f"DI_a{adx_m}_g{gap}_c{conf}",
            adx_min=adx_m, di_gap=gap, confirm=conf, er_min=0.10,
            ek=ek(vt), assets=ASSETS,
        ))

    return c


def main():
    t0 = time.time()
    print("loading…")
    frames = prepare(ASSETS)
    cands = build()
    print(f"raw candidates: {len(cands)}")
    # stratified sample to ~400
    max_n = 400
    if len(cands) > max_n:
        rng = np.random.default_rng(29)
        by = {}
        for c in cands:
            by.setdefault(c["kind"], []).append(c)
        keep = []
        # quota per kind
        quotas = {
            "multiz2": 120, "zmom": 80, "mtf_ema": 70, "atr_ch": 40,
            "er_bo": 40, "cons3": 30, "di_trend": 20,
        }
        for k, lst in by.items():
            q = quotas.get(k, 30)
            if len(lst) <= q:
                keep.extend(lst)
            else:
                # structured: take grid stride + random
                step = max(1, len(lst) // q)
                core = lst[::step][: q // 2]
                rest = [x for x in lst if x not in core]
                idx = rng.choice(len(rest), size=min(q - len(core), len(rest)), replace=False)
                keep.extend(core + [rest[i] for i in idx])
        cands = keep[:max_n]
    print(f"scanning: {len(cands)}")

    results = []
    best = -999
    for i, cfg in enumerate(cands):
        r = eval_cfg(cfg, frames)
        if r.get("score", -999) <= -900:
            if r.get("error") and i < 5:
                print("ERR", r["error"], cfg.get("tag"))
            continue
        results.append({k: v for k, v in r.items() if k != "equity"})
        if r["score"] > best:
            best = r["score"]
            w = r["windows"]
            b = r["beats"]
            print(
                f"[{i+1}/{len(cands)}] ★ {best:.3f} {cfg.get('tag','')[:36]:36s} "
                f"OOS1 {w['OOS1']['ret']*100:+.2f}%/{w['OOS1']['sharpe']:.2f} "
                f"FULL {w['FULL']['sharpe']:.2f}/{w['FULL']['mdd']*100:.1f}% "
                f"6M {w['6M']['ret']*100:+.2f}% hard={r['hard']} "
                f"beatST={b['beats_inst_stable_oos1']} beatP={b['beats_inst_primary_oos1']}"
            )
        elif (i + 1) % 50 == 0:
            print(f"[{i+1}/{len(cands)}] best={best:.3f}")

    results.sort(key=lambda x: (x.get("hard", False), x["score"]), reverse=True)
    print("\nTOP 20 singles:")
    for j, r in enumerate(results[:20]):
        w = r["windows"]
        b = r["beats"]
        print(
            f"{j+1:2d}. {r['score']:.3f} h={r['hard']} {str(r['cfg'].get('tag'))[:34]:34s} "
            f"OOS1 {w['OOS1']['ret']*100:+5.2f}/{w['OOS1']['sharpe']:.2f} "
            f"F {w['FULL']['sharpe']:.2f}/{w['FULL']['mdd']*100:.1f} "
            f"6M {w['6M']['ret']*100:+5.2f} "
            f"ST={b['beats_inst_stable_oos1']} P={b['beats_inst_primary_oos1']} allF={b['beats_all_full_sh']}"
        )

    # blends
    print("\nBlends…")
    rich = []
    for r in results[:8]:
        rr = eval_cfg(r["cfg"], frames)
        if rr and "equity" in rr:
            rich.append(rr)

    blends = []
    for r in rich:
        blends.append(dict(
            name=f"single:{r['cfg'].get('tag')}", type="single",
            windows=r["windows"], score=r["score"], hard=r["hard"], beats=r["beats"],
            n_trades=r["n_trades"], costs=r["costs"], equity=r["equity"],
            legs=[r["cfg"]], end_equity=r["end_equity"], trade_stats=r["trade_stats"],
        ))

    for i, j in itertools.combinations(range(len(rich)), 2):
        if rich[i]["cfg"]["kind"] == rich[j]["cfg"]["kind"] and rich[i]["cfg"].get("tag") == rich[j]["cfg"].get("tag"):
            continue
        for wa, wb in ((0.7, 0.3), (0.6, 0.4), (0.5, 0.5), (0.8, 0.2)):
            eq = blend_equity([(wa, rich[i]["equity"]), (wb, rich[j]["equity"])])
            win = window_metrics(eq)
            n_tr = int(wa * rich[i]["n_trades"] + wb * rich[j]["n_trades"])
            costs = wa * rich[i]["costs"] + wb * rich[j]["costs"]
            sc = score_crown(win, n_tr, costs)
            blends.append(dict(
                name=f"b_{str(rich[i]['cfg'].get('tag'))[:14]}_{str(rich[j]['cfg'].get('tag'))[:14]}_{int(wa*100)}",
                type="blend", weights=(wa, wb), windows=win, score=sc,
                hard=hard_pass(win), beats=beats_champions(win),
                n_trades=n_tr, costs=costs, equity=eq,
                legs=[rich[i]["cfg"], rich[j]["cfg"]],
                end_equity=float(eq.dropna().iloc[-1]),
            ))

    if len(rich) >= 3:
        for ws in ((0.5, 0.3, 0.2), (0.45, 0.35, 0.20), (0.6, 0.25, 0.15)):
            eq = blend_equity([(ws[k], rich[k]["equity"]) for k in range(3)])
            win = window_metrics(eq)
            n_tr = int(sum(ws[k] * rich[k]["n_trades"] for k in range(3)))
            costs = sum(ws[k] * rich[k]["costs"] for k in range(3))
            sc = score_crown(win, n_tr, costs)
            blends.append(dict(
                name=f"trip_{'_'.join(str(int(x*100)) for x in ws)}",
                type="triple", weights=ws, windows=win, score=sc,
                hard=hard_pass(win), beats=beats_champions(win),
                n_trades=n_tr, costs=costs, equity=eq,
                legs=[rich[k]["cfg"] for k in range(3)],
                end_equity=float(eq.dropna().iloc[-1]),
            ))

    def rk(b):
        bt = b.get("beats") or {}
        crown = (
            3 * int(bt.get("beats_inst_primary_oos1", False))
            + 2 * int(bt.get("beats_inst_stable_oos1", False))
            + int(bt.get("beats_all_full_sh", False))
            + int(bt.get("beats_almasi_oos1", False))
        )
        return (b.get("hard", False), crown, b["score"])

    blends.sort(key=rk, reverse=True)
    print("Top 12 final:")
    for j, b in enumerate(blends[:12]):
        w = b["windows"]
        bt = b.get("beats") or {}
        print(
            f"{j+1:2d}. {b['name'][:48]:48s} sc={b['score']:.3f} h={b['hard']} "
            f"OOS1 {w['OOS1']['ret']*100:+5.2f}/{w['OOS1']['sharpe']:.2f} "
            f"F {w['FULL']['sharpe']:.2f}/{w['FULL']['mdd']*100:.1f} "
            f"6M {w['6M']['ret']*100:+5.2f} "
            f"P={bt.get('beats_inst_primary_oos1')} ST={bt.get('beats_inst_stable_oos1')} Fsh={bt.get('beats_all_full_sh')}"
        )

    # pick winner: max crown among hard
    winner = blends[0]
    for b in blends:
        if b.get("hard") and (b.get("beats") or {}).get("beats_inst_stable_oos1"):
            winner = b
            break
    else:
        for b in blends:
            if b.get("hard") and (b.get("beats") or {}).get("beats_almasi_oos1"):
                winner = b
                break
        else:
            for b in blends:
                if b.get("hard"):
                    winner = b
                    break

    w = winner["windows"]
    print("\n*** WINNER ***", winner["name"])
    for lab in ("IS", "OOS1", "OOS2", "6M", "FULL"):
        ww = w[lab]
        print(f"  {lab:5s} ret={ww['ret']*100:+7.2f}% Sh={ww['sharpe']:6.2f} MDD={ww['mdd']*100:7.2f}%")
    print(" beats", winner.get("beats"))
    print(f" end={winner['end_equity']:.2f} n={winner['n_trades']} costs={winner['costs']:.2f}")

    winner["equity"].to_csv(os.path.join(RESULTS, "zenith_v001_equity.csv"), header=["eq"])

    vs = {}
    for name, b in BENCH.items():
        vs[name] = dict(
            delta_oos1_sh=w["OOS1"]["sharpe"] - b["oos1_sh"],
            delta_oos1_ret=w["OOS1"]["ret"] - b["oos1_ret"],
            delta_full_sh=w["FULL"]["sharpe"] - b["full_sh"],
            delta_full_mdd=w["FULL"]["mdd"] - b["full_mdd"],
            delta_6m=w["6M"]["ret"] - b["m6_ret"],
            wins_oos1=bool(w["OOS1"]["sharpe"] > b["oos1_sh"] and w["OOS1"]["ret"] > b["oos1_ret"]),
            wins_full_sh=bool(w["FULL"]["sharpe"] > b["full_sh"]),
        )

    crown_level = "none"
    if (winner.get("beats") or {}).get("beats_inst_primary_oos1"):
        crown_level = "beats_inst_primary"
    elif (winner.get("beats") or {}).get("beats_inst_stable_oos1"):
        crown_level = "beats_inst_stable"
    elif (winner.get("beats") or {}).get("beats_almasi_oos1"):
        crown_level = "beats_almasi"
    elif (winner.get("beats") or {}).get("beats_all_full_sh"):
        crown_level = "beats_full_sh_only"

    payload = dict(
        name="zenith-v001",
        title="ZENITH v001 — new challenger strategy",
        winner_name=winner["name"],
        crown_level=crown_level,
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
        vs_champions=vs,
        top_singles=[{
            "score": r["score"], "hard": r["hard"], "cfg": r["cfg"],
            "windows": r["windows"], "beats": r["beats"],
            "n_trades": r["n_trades"],
        } for r in results[:15]],
        top_final=[{
            "name": b["name"], "score": b["score"], "hard": b.get("hard"),
            "windows": b["windows"], "beats": b.get("beats"),
            "weights": b.get("weights"), "legs": b.get("legs"),
        } for b in blends[:12]],
        protocol="IS observe / OOS1 select / OOS2+6M report / MDD<=15%",
        elapsed_sec=round(time.time() - t0, 1),
        n_scanned=len(results),
    )
    with open(os.path.join(RESULTS, "zenith_v001_results.json"), "w") as f:
        json.dump(payload, f, indent=2, default=str)

    # FA summary
    lines = [
        "# ZENITH v001 — استراتژی جدید کشف‌شده",
        "",
        f"**winner:** `{winner['name']}`",
        f"**hard_pass:** `{'✅' if winner.get('hard') else '❌'}` · **crown:** `{crown_level}`",
        "",
        "## نتایج ($100 · 7bp/side · lev≤1 · long-only)",
        "",
        "| بازه | بازده | Sharpe | MaxDD |",
        "|---|---:|---:|---:|",
    ]
    for lab in ("IS", "OOS1", "OOS2", "6M", "FULL"):
        ww = w[lab]
        lines.append(f"| **{lab}** | {ww['ret']*100:+.2f}% | {ww['sharpe']:.2f} | {ww['mdd']*100:.2f}% |")
    lines += [
        "",
        f"- end **{winner['end_equity']:.2f}** · trades≈**{winner['n_trades']}** · costs **{winner['costs']:.2f}**",
        "",
        "## vs قهرمان‌ها (هدف: اول شدن)",
        "",
        "| حریف | OOS1 Sh حریف | ZENITH | Δ Sh | Δ ret | برد؟ |",
        "|---|---:|---:|---:|---:|:---:|",
    ]
    for name, b in BENCH.items():
        v = vs[name]
        lines.append(
            f"| {name} | {b['oos1_sh']:.2f} | {w['OOS1']['sharpe']:.2f} | "
            f"{v['delta_oos1_sh']:+.2f} | {v['delta_oos1_ret']*100:+.1f}pp | "
            f"{'✅' if v['wins_oos1'] else '❌'} |"
        )
    lines += [
        "",
        "| حریف | FULL Sh حریف | ZENITH | Δ |",
        "|---|---:|---:|---:|",
    ]
    for name, b in BENCH.items():
        v = vs[name]
        lines.append(
            f"| {name} | {b['full_sh']:.2f} | {w['FULL']['sharpe']:.2f} | {v['delta_full_sh']:+.2f} |"
        )
    lines += [
        "",
        "## پاها",
        "```json",
        json.dumps(winner["legs"], indent=2, default=str)[:5000],
        "```",
        "",
        f"elapsed {payload['elapsed_sec']}s · scanned {payload['n_scanned']}",
        "",
        "فایل‌ها: `zenith_v001.py` · `results/zenith_v001_*`",
    ]
    with open(os.path.join(RESULTS, "ZENITH_V001_SUMMARY_FA.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("saved zenith_v001_results.json · ZENITH_V001_SUMMARY_FA.md · elapsed", payload["elapsed_sec"])


if __name__ == "__main__":
    main()
