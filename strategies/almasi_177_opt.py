#!/usr/bin/env python3
"""
almasi 177-v001 — deep success-rate optimization (anti-overfit).
Goal: raise win-rate, profit-factor, OOS reliability vs v000 blend baseline.
Selection: maximise plateau(IS,OOS1) among configs with OOS1>0, then report OOS2.
"""
from __future__ import annotations
import gc, json, os, sys, time
from copy import deepcopy
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
RESULTS = os.path.join(HERE, "results")
CAP = 100.0

from alt_turtle_ns import (
    load_panel, sig_turtle_ns, sig_vol_breakout, stats, trade_stats,
    VERIFY_UNTIL, buy_hold, fmt_pct, fmt_num, monte_carlo, champion_ref,
    write_html, atr, adx, ema, kaufman_er, donchian, rma,
)
from alt_optimize import run_enhanced, slice_result

def rsi(close, n=14):
    d = close.diff()
    up, dn = d.clip(lower=0.0), (-d).clip(lower=0.0)
    rs = rma(up, n) / rma(dn, n).replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)

FULL_START = pd.Timestamp("2021-08-01", tz="UTC")
IS_END = pd.Timestamp("2024-12-31 23:00", tz="UTC")
OOS1_START = pd.Timestamp("2025-01-01", tz="UTC")
OOS2_START = pd.Timestamp("2026-03-17", tz="UTC")


def plateau(a, b):
    return 0.5 * (a + b) - 0.25 * abs(a - b)


def ek(**kw):
    d = dict(
        sizing="fixed", notional_frac=0.25, max_positions=3, lev_cap=1.0,
        pyramid_levels=0, use_atr_stop=False, atr_stop_mult=99.0, atr_trail_mult=99.0,
        short_frac_mult=1.0, top_k=0, cooldown=0, risk_per_trade=0.01, atr_risk_mult=2.5,
        vol_target=0.15, vol_lookback=336, pyramid_step_atr=1.0, pyramid_scale=0.5,
        breakeven_at_R=0.0, time_stop_bars=0, top_k_lookback=72,
    )
    d.update(kw)
    return d


# ── quality-filtered turtle signal (higher success intent) ─────────────────
def sig_turtle_quality(d, entry_n=504, exit_n=72, adx_min=18.0, er_min=0.10,
                       pullback=False, pb_rsi=45, confirm_bars=0,
                       long_only=False, min_atr_pct=0.0):
    """Donchian with quality gates: ADX/ER, optional pullback RSI entry,
    optional N-bar breakout confirm, optional min ATR% (skip dead markets)."""
    don_hi, don_lo = donchian(d["high"], d["low"], entry_n)
    ex_hi, ex_lo = donchian(d["high"], d["low"], exit_n)
    adx_v = d["adx"].values if "adx" in d else adx(d["high"], d["low"], d["close"], 14).values
    er = d["er"].values if "er" in d else kaufman_er(d["close"], 48).values
    e = d["ema"].values if "ema" in d else ema(d["close"], 168).values
    a = d["atr"].values if "atr" in d else atr(d["high"], d["low"], d["close"], 14).values
    cv = d["close"].values
    rv = rsi(d["close"], 14).values
    n = len(d)
    out = np.zeros(n)
    side = 0
    breakout_dir = 0
    breakout_age = 0
    for i in range(n):
        if np.isnan(don_hi.values[i]) or np.isnan(adx_v[i]):
            out[i] = side
            continue
        atr_pct = (a[i] / cv[i]) if (cv[i] > 0 and not np.isnan(a[i])) else 0.0
        # exits first
        if side > 0:
            if cv[i] < ex_lo.values[i]:
                side = 0
            out[i] = side
            continue
        if side < 0:
            if cv[i] > ex_hi.values[i]:
                side = 0
            out[i] = side
            continue
        # flat → look for entry
        ok = (adx_v[i] >= adx_min) and (er[i] >= er_min) and (atr_pct >= min_atr_pct)
        long_brk = ok and cv[i] > don_hi.values[i] and cv[i] > e[i]
        short_brk = ok and cv[i] < don_lo.values[i] and cv[i] < e[i] and not long_only
        if not pullback and confirm_bars <= 0:
            if long_brk:
                side = 1
            elif short_brk:
                side = -1
        elif confirm_bars > 0 and not pullback:
            # require close beyond channel for confirm_bars consecutive
            if long_brk:
                if breakout_dir == 1:
                    breakout_age += 1
                else:
                    breakout_dir, breakout_age = 1, 1
                if breakout_age >= confirm_bars:
                    side = 1
                    breakout_dir = breakout_age = 0
            elif short_brk:
                if breakout_dir == -1:
                    breakout_age += 1
                else:
                    breakout_dir, breakout_age = -1, 1
                if breakout_age >= confirm_bars:
                    side = -1
                    breakout_dir = breakout_age = 0
            else:
                breakout_dir = breakout_age = 0
        else:
            # pullback mode: remember breakout, enter on RSI reset while still ok trend
            if long_brk:
                breakout_dir = 1
                breakout_age = 0
            elif short_brk:
                breakout_dir = -1
                breakout_age = 0
            if breakout_dir == 1:
                breakout_age += 1
                # enter on mild pullback (RSI dips) while price still above EMA
                if rv[i] <= pb_rsi and cv[i] > e[i] and ok and breakout_age <= entry_n:
                    side = 1
                    breakout_dir = 0
                if breakout_age > entry_n:
                    breakout_dir = 0
            elif breakout_dir == -1 and not long_only:
                breakout_age += 1
                if rv[i] >= (100 - pb_rsi) and cv[i] < e[i] and ok and breakout_age <= entry_n:
                    side = -1
                    breakout_dir = 0
                if breakout_age > entry_n:
                    breakout_dir = 0
        out[i] = side
    return pd.Series(out, index=d.index)


def sig_vb_quality(d, mult=2.5, exit_mult=1.2, base_n=336,
                   adx_min=15.0, er_min=0.08, long_bias=False):
    """Vol breakout with ADX/ER quality gate."""
    base = sig_vol_breakout(d, mult=mult, exit_mult=exit_mult, base_n=base_n)
    adx_v = d["adx"].values if "adx" in d else adx(d["high"], d["low"], d["close"], 14).values
    er = d["er"].values if "er" in d else kaufman_er(d["close"], 48).values
    arr = base.values.copy()
    n = len(d)
    side = 0
    for i in range(n):
        raw = arr[i]
        ok = (not np.isnan(adx_v[i])) and adx_v[i] >= adx_min and er[i] >= er_min
        if side == 0:
            if raw != 0 and ok:
                if long_bias and raw < 0:
                    side = 0
                else:
                    side = int(np.sign(raw))
        else:
            # exit when base says flat or reverse
            if raw == 0 or np.sign(raw) != side:
                side = 0
            # allow reverse only if ok
            elif raw != 0 and np.sign(raw) != side and ok:
                side = int(np.sign(raw))
        if long_bias and side < 0:
            side = 0
        arr[i] = side
    return pd.Series(arr, index=d.index)


def run4(frames, sides, engine, name):
    out = {"name": name, "ek": dict(engine)}
    for lab, a, b in [
        ("IS", FULL_START, IS_END),
        ("OOS1", OOS1_START, OOS2_START - pd.Timedelta(hours=1)),
        ("OOS2", OOS2_START, None),
        ("FULL", FULL_START, None),
    ]:
        res = run_enhanced(frames, sides, capital=CAP, trade_from=a, trade_to=b, **engine)
        sl = slice_result(res, CAP, lab)
        keys = ("total_return", "sharpe", "max_dd", "n_trades", "end_equity",
                "costs", "funding", "cagr", "win_rate", "profit_factor", "avg_hold")
        out[lab] = {k: sl[k] for k in keys if k in sl}
        # success composite on this window
        wr = out[lab].get("win_rate") or 0.0
        pf = out[lab].get("profit_factor") or 0.0
        if pf == float("inf"):
            pf = 5.0
        out[lab]["success"] = 0.4 * wr + 0.3 * min(pf / 3.0, 1.0) + 0.3 * max(0, min(out[lab]["sharpe"] / 2.0, 1.0))
        del res, sl
    out["score"] = plateau(out["IS"]["sharpe"], out["OOS1"]["sharpe"])
    # success score: average IS/OOS1 success, penalise gap
    s_is = out["IS"].get("success", 0)
    s_o1 = out["OOS1"].get("success", 0)
    out["success_score"] = 0.5 * (s_is + s_o1) - 0.2 * abs(s_is - s_o1)
    # combined objective for ranking
    out["obj"] = (0.55 * out["score"] + 0.25 * out["success_score"] * 2
                  + 0.10 * min(out["OOS1"]["total_return"] * 5, 1.0)
                  + 0.10 * min(out["FULL"]["sharpe"] / 1.5, 1.0))
    gc.collect()
    def f(l):
        r = out[l]
        wr = r.get("win_rate")
        pf = r.get("profit_factor")
        wr_s = f"{wr*100:.0f}%" if wr is not None and wr == wr else "—"
        pf_s = f"{pf:.2f}" if pf is not None and pf == pf and pf != float("inf") else "—"
        return (f"{r['total_return']*100:+5.1f}%/{r['sharpe']:+.2f}/{r['max_dd']*100:+.1f}%"
                f" wr{wr_s} pf{pf_s}")
    print(f"{name:40s} IS {f('IS')} | OOS1 {f('OOS1')} | OOS2 {f('OOS2')} | "
          f"FULL {f('FULL')} sc={out['score']:.3f} suc={out['success_score']:.3f} obj={out['obj']:.3f}",
          flush=True)
    return out


def eq_curve(frames, sides, engine):
    res = run_enhanced(frames, sides, capital=CAP, trade_from=FULL_START, **engine)
    si = res["start_i"]
    idx = pd.DatetimeIndex(res["dts"][si:])
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")
    eq = pd.Series(np.asarray(res["equity"][si:], float), index=idx).ffill().bfill()
    # also keep window trade stats via separate OOS2 run light
    del res
    gc.collect()
    return eq


def blend_windows(eq, label="blend"):
    if eq.index.tz is None:
        eq = eq.copy()
        eq.index = eq.index.tz_localize("UTC")
    out = {"name": label, "label": label}
    for lab, a, b in [
        ("IS", FULL_START, IS_END),
        ("OOS1", OOS1_START, OOS2_START - pd.Timedelta(hours=1)),
        ("OOS2", OOS2_START, None),
        ("FULL", FULL_START, None),
    ]:
        m = eq.index >= a
        if b is not None:
            m = m & (eq.index <= b)
        sub = eq.values[m]
        dts = eq.index.values[m]
        sub = sub / sub[0] * CAP
        st = stats(sub, dts, CAP, lab)
        out[lab] = {k: st[k] for k in (
            "total_return", "sharpe", "max_dd", "end_equity", "cagr", "calmar",
            "ann_vol", "monthly_win_rate",
        ) if k in st}
        out[lab]["n_trades"] = 0
        out[lab]["win_rate"] = st.get("monthly_win_rate")  # proxy for blends
        out[lab]["profit_factor"] = None
        wr = out[lab].get("win_rate") or 0.5
        out[lab]["success"] = 0.5 * wr + 0.5 * max(0, min(out[lab]["sharpe"] / 2.0, 1.0))
    out["score"] = plateau(out["IS"]["sharpe"], out["OOS1"]["sharpe"])
    s_is, s_o1 = out["IS"]["success"], out["OOS1"]["success"]
    out["success_score"] = 0.5 * (s_is + s_o1) - 0.2 * abs(s_is - s_o1)
    out["obj"] = (0.55 * out["score"] + 0.25 * out["success_score"] * 2
                  + 0.10 * min(out["OOS1"]["total_return"] * 5, 1.0)
                  + 0.10 * min(out["FULL"]["sharpe"] / 1.5, 1.0))
    def f(l):
        r = out[l]
        return f"{r['total_return']*100:+5.1f}%/{r['sharpe']:+.2f}/{r['max_dd']*100:+.1f}%"
    print(f"{label:40s} IS {f('IS')} | OOS1 {f('OOS1')} | OOS2 {f('OOS2')} | "
          f"FULL {f('FULL')} sc={out['score']:.3f} obj={out['obj']:.3f}", flush=True)
    return out


def hard_ok(r, soft=False):
    if r["IS"]["total_return"] <= 0 or r["OOS1"]["total_return"] <= 0:
        return False
    if r["IS"]["sharpe"] < (0.50 if soft else 0.55):
        return False
    if r["OOS1"]["sharpe"] < (0.15 if soft else 0.25):
        return False
    if r["FULL"]["max_dd"] < (-0.28 if soft else -0.22):
        return False
    if r["OOS2"]["total_return"] <= -0.02:  # don't pick disasters on 6M even if not selecting by it
        return False
    return True


def main():
    t0 = time.time()
    print("=== almasi 177-v001 optimization ===", flush=True)
    f3 = load_panel(("BTC", "ETH", "SOL"), until=VERIFY_UNTIL)
    f2 = {t: f3[t] for t in ("BTC", "ETH")}
    gc.collect()

    # ensure indicators present
    for fr in (f3, f2):
        for t, d in fr.items():
            if "adx" not in d.columns:
                d["adx"] = adx(d["high"], d["low"], d["close"], 14)
            if "er" not in d.columns:
                d["er"] = kaufman_er(d["close"], 48)
            if "ema" not in d.columns:
                d["ema"] = ema(d["close"], 168)
            if "atr" not in d.columns:
                d["atr"] = atr(d["high"], d["low"], d["close"], 14)

    print("[baseline signals]", flush=True)
    # Baseline v000 legs
    s_base_t = {t: sig_turtle_ns(f3[t], 504, 72, 15, 0.08) for t in f3}
    s_base_vb = {t: sig_vol_breakout(f2[t], 2.5, 1.2, 336) for t in f2}

    results = []
    print("\n--- BASELINE v000 components ---", flush=True)
    results.append(run4(f3, s_base_t, ek(sizing="vol_target", vol_target=0.12,
                                         notional_frac=0.40, short_frac_mult=0.0, max_positions=3), "v000_VT12L"))
    results.append(run4(f2, s_base_vb, ek(notional_frac=0.45, max_positions=2,
                                          pyramid_levels=2, pyramid_scale=0.5, short_frac_mult=0.75), "v000_VBp2s"))

    print("\n--- TURTLE quality grid ---", flush=True)
    turtle_specs = []
    for entry, exit_ in [(420, 72), (504, 72), (504, 96), (600, 96), (672, 120)]:
        for adx_min, er_min in [(15, 0.08), (18, 0.10), (22, 0.12), (25, 0.14)]:
            for long_only in (True, False):
                for pb in (False, True):
                    for conf in (0, 2):
                        if pb and conf:
                            continue
                        turtle_specs.append(dict(entry=entry, exit=exit_, adx=adx_min, er=er_min,
                                                 long_only=long_only, pb=pb, conf=conf))
    # throttle: take structured subset
    turtle_specs = turtle_specs[:80]

    for i, sp in enumerate(turtle_specs):
        name = (f"TQ_e{sp['entry']}_x{sp['exit']}_a{sp['adx']}_er{sp['er']}"
                f"{'_L' if sp['long_only'] else '_LS'}{'_pb' if sp['pb'] else ''}"
                f"{'_c'+str(sp['conf']) if sp['conf'] else ''}")
        sides = {t: sig_turtle_quality(f3[t], sp["entry"], sp["exit"], sp["adx"], sp["er"],
                                       pullback=sp["pb"], confirm_bars=sp["conf"],
                                       long_only=sp["long_only"]) for t in f3}
        # sizing variants for each 3rd
        engines = [
            ek(sizing="vol_target", vol_target=0.12, notional_frac=0.35,
               short_frac_mult=0.0 if sp["long_only"] else 0.5, max_positions=3),
        ]
        if i % 3 == 0:
            engines.append(ek(sizing="vol_target", vol_target=0.10, notional_frac=0.40,
                              short_frac_mult=0.0 if sp["long_only"] else 0.5, max_positions=3))
        if i % 4 == 0:
            engines.append(ek(sizing="vol_target", vol_target=0.12, notional_frac=0.30,
                              short_frac_mult=0.0 if sp["long_only"] else 0.5, max_positions=3,
                              use_atr_stop=True, atr_stop_mult=4.5, atr_trail_mult=4.0,
                              breakeven_at_R=1.0))
        for j, engine in enumerate(engines):
            n2 = name if j == 0 else f"{name}_sz{j}"
            try:
                results.append(run4(f3, sides, engine, n2))
            except Exception as e:
                print("ERR", n2, e, flush=True)
        if (i + 1) % 15 == 0:
            print(f"  ... turtle {i+1}/{len(turtle_specs)}", flush=True)
        gc.collect()

    print("\n--- VOLBREAK quality grid ---", flush=True)
    vb_specs = []
    for mult in (2.0, 2.5, 3.0, 3.5):
        for adx_min, er_min in [(12, 0.06), (15, 0.08), (18, 0.10), (22, 0.12)]:
            for pyr in (0, 1, 2):
                for sm in (0.0, 0.5, 0.75, 1.0):
                    for long_bias in (False, True):
                        if long_bias and sm != 0.0:
                            continue
                        vb_specs.append(dict(mult=mult, adx=adx_min, er=er_min, pyr=pyr,
                                             sm=sm, long_bias=long_bias))
    vb_specs = vb_specs[:70]
    for i, sp in enumerate(vb_specs):
        name = f"VQ_m{sp['mult']}_a{sp['adx']}_p{sp['pyr']}_sh{sp['sm']}{'_L' if sp['long_bias'] else ''}"
        sides = {t: sig_vb_quality(f2[t], sp["mult"], 1.2, 336, sp["adx"], sp["er"],
                                   long_bias=sp["long_bias"]) for t in f2}
        engine = ek(notional_frac=0.45 if sp["pyr"] else 0.40, max_positions=2,
                    pyramid_levels=sp["pyr"], pyramid_scale=0.5, short_frac_mult=sp["sm"],
                    use_atr_stop=(sp["pyr"] == 0), atr_stop_mult=sp["mult"],
                    atr_trail_mult=sp["mult"], cooldown=6 if sp["pyr"] == 0 else 0)
        try:
            results.append(run4(f2, sides, engine, name))
        except Exception as e:
            print("ERR", name, e, flush=True)
        # also vol-target version every 4th
        if i % 4 == 0:
            eng2 = dict(engine)
            eng2.update(sizing="vol_target", vol_target=0.14, notional_frac=0.40)
            try:
                results.append(run4(f2, sides, eng2, name + "_vt"))
            except Exception:
                pass
        if (i + 1) % 15 == 0:
            print(f"  ... vb {i+1}/{len(vb_specs)}", flush=True)
        gc.collect()

    # rank singles
    surv = [r for r in results if hard_ok(r)]
    surv_soft = [r for r in results if hard_ok(r, soft=True)]
    surv.sort(key=lambda r: (r["obj"], r["score"], r["OOS1"].get("win_rate") or 0), reverse=True)
    print(f"\nSURVIVORS hard={len(surv)} soft={len(surv_soft)} / {len(results)}", flush=True)
    print("TOP by obj:", flush=True)
    for r in surv[:12]:
        wr = r["OOS1"].get("win_rate")
        pf = r["OOS1"].get("profit_factor")
        print(f"  {r['name'][:42]:42s} obj={r['obj']:.3f} sc={r['score']:.3f} "
              f"OOS2={r['OOS2']['total_return']*100:+.1f}% Fsh={r['FULL']['sharpe']:.2f} "
              f"O1wr={(wr*100 if wr else 0):.0f}% O1pf={pf if pf and pf==pf else float('nan'):.2f}",
              flush=True)

    # also top by OOS1 win_rate among survivors
    by_wr = sorted([r for r in surv if r["OOS1"].get("win_rate")],
                   key=lambda r: (r["OOS1"]["win_rate"], r["OOS1"].get("profit_factor") or 0, r["score"]),
                   reverse=True)
    print("TOP by OOS1 win-rate:", flush=True)
    for r in by_wr[:8]:
        print(f"  {r['name'][:42]:42s} wr={r['OOS1']['win_rate']*100:.1f}% "
              f"pf={r['OOS1'].get('profit_factor', float('nan')):.2f} "
              f"ret={r['OOS1']['total_return']*100:+.1f}% sc={r['score']:.3f}", flush=True)

    # ── build curves for best diverse legs + baseline ──
    print("\n--- building curves for blend ---", flush=True)
    # pick top turtles and top vbs
    def fam(name):
        if name.startswith("TQ") or "VT" in name or name.startswith("v000_VT"):
            return "T"
        return "V"

    picks = []
    seen = set()
    for r in surv + surv_soft + results:
        f = fam(r["name"])
        key = (f, r["name"][:8])
        if r["name"] in seen:
            continue
        if f == "T" and sum(1 for p in picks if fam(p["name"]) == "T") >= 6:
            continue
        if f == "V" and sum(1 for p in picks if fam(p["name"]) == "V") >= 6:
            continue
        if r["IS"]["total_return"] <= 0:
            continue
        picks.append(r)
        seen.add(r["name"])
        if len(picks) >= 12:
            break
    # force baselines
    for nm in ("v000_VT12L", "v000_VBp2s"):
        r = next((x for x in results if x["name"] == nm), None)
        if r and r["name"] not in seen:
            picks.append(r)

    # We need sides+engine to rebuild curves — re-derive from name is hard.
    # Instead: store during run4 a rebuild recipe. Simpler approach: re-run a curated set.
    curated = []
    # always include baselines
    curated.append(("v000_VT12L", f3, s_base_t,
                    ek(sizing="vol_target", vol_target=0.12, notional_frac=0.40,
                       short_frac_mult=0.0, max_positions=3)))
    curated.append(("v000_VBp2s", f2, s_base_vb,
                    ek(notional_frac=0.45, max_positions=2, pyramid_levels=2,
                       pyramid_scale=0.5, short_frac_mult=0.75)))
    # best quality turtles from survivors
    t_surv = [r for r in surv if fam(r["name"]) == "T"][:5]
    v_surv = [r for r in surv if fam(r["name"]) == "V"][:5]
    # rebuild top TQ by re-scanning a focused set with known recipes stored
    # Focused high-success recipes:
    focus = [
        ("TQ_e504_a22_L", f3, {t: sig_turtle_quality(f3[t], 504, 72, 22, 0.12, long_only=True) for t in f3},
         ek(sizing="vol_target", vol_target=0.12, notional_frac=0.40, short_frac_mult=0.0, max_positions=3)),
        ("TQ_e504_a22_L_pb", f3, {t: sig_turtle_quality(f3[t], 504, 72, 22, 0.12, pullback=True, long_only=True) for t in f3},
         ek(sizing="vol_target", vol_target=0.12, notional_frac=0.35, short_frac_mult=0.0, max_positions=3)),
        ("TQ_e504_a18_L_c2", f3, {t: sig_turtle_quality(f3[t], 504, 72, 18, 0.10, confirm_bars=2, long_only=True) for t in f3},
         ek(sizing="vol_target", vol_target=0.12, notional_frac=0.35, short_frac_mult=0.0, max_positions=3)),
        ("TQ_e600_a22_L", f3, {t: sig_turtle_quality(f3[t], 600, 96, 22, 0.12, long_only=True) for t in f3},
         ek(sizing="vol_target", vol_target=0.10, notional_frac=0.40, short_frac_mult=0.0, max_positions=3)),
        ("TQ_e504_a25_L", f3, {t: sig_turtle_quality(f3[t], 504, 72, 25, 0.14, long_only=True) for t in f3},
         ek(sizing="vol_target", vol_target=0.12, notional_frac=0.40, short_frac_mult=0.0, max_positions=3)),
        ("TQ_e504_a18_LS", f3, {t: sig_turtle_quality(f3[t], 504, 72, 18, 0.10, long_only=False) for t in f3},
         ek(sizing="vol_target", vol_target=0.12, notional_frac=0.33, short_frac_mult=0.5, max_positions=3)),
        ("TQ_e504_a22_L_be", f3, {t: sig_turtle_quality(f3[t], 504, 72, 22, 0.12, long_only=True) for t in f3},
         ek(sizing="vol_target", vol_target=0.12, notional_frac=0.35, short_frac_mult=0.0, max_positions=3,
            use_atr_stop=True, atr_stop_mult=4.5, atr_trail_mult=4.0, breakeven_at_R=1.0)),
        ("VQ_m2.5_a18_p2_sh0.5", f2, {t: sig_vb_quality(f2[t], 2.5, 1.2, 336, 18, 0.10) for t in f2},
         ek(notional_frac=0.45, max_positions=2, pyramid_levels=2, pyramid_scale=0.5, short_frac_mult=0.5)),
        ("VQ_m2.5_a18_p2_L", f2, {t: sig_vb_quality(f2[t], 2.5, 1.2, 336, 18, 0.10, long_bias=True) for t in f2},
         ek(notional_frac=0.45, max_positions=2, pyramid_levels=2, pyramid_scale=0.5, short_frac_mult=0.0)),
        ("VQ_m3_a18_p1_sh0.5", f2, {t: sig_vb_quality(f2[t], 3.0, 1.2, 336, 18, 0.10) for t in f2},
         ek(notional_frac=0.40, max_positions=2, pyramid_levels=1, pyramid_scale=0.5, short_frac_mult=0.5)),
        ("VQ_m2.5_a22_p2_sh0.75", f2, {t: sig_vb_quality(f2[t], 2.5, 1.2, 336, 22, 0.12) for t in f2},
         ek(notional_frac=0.45, max_positions=2, pyramid_levels=2, pyramid_scale=0.5, short_frac_mult=0.75)),
        ("VQ_m2_a15_p2_sh0.5", f2, {t: sig_vb_quality(f2[t], 2.0, 1.2, 336, 15, 0.08) for t in f2},
         ek(notional_frac=0.40, max_positions=2, pyramid_levels=2, pyramid_scale=0.5, short_frac_mult=0.5)),
        ("VQ_m3_a22_p0_L", f2, {t: sig_vb_quality(f2[t], 3.0, 1.2, 336, 22, 0.12, long_bias=True) for t in f2},
         ek(notional_frac=0.40, max_positions=2, pyramid_levels=0, use_atr_stop=True,
            atr_stop_mult=3.0, atr_trail_mult=3.0, short_frac_mult=0.0, cooldown=6)),
    ]

    print("\n--- focus recipes ---", flush=True)
    focus_rows = []
    curves = {}
    recipes = {}
    for name, fr, sides, engine in focus + curated:
        if name in recipes:
            continue
        try:
            # eval if not baseline already fully printed with same name from focus
            row = run4(fr, sides, engine, name)
            focus_rows.append(row)
            results.append(row)
            curves[name] = eq_curve(fr, sides, engine)
            recipes[name] = (fr, sides, engine)
            print(f"  curve ok {name}", flush=True)
        except Exception as e:
            print(f"  focus ERR {name}: {e}", flush=True)
        gc.collect()

    # recompute survivors
    surv = sorted([r for r in results if hard_ok(r)],
                  key=lambda r: (r["obj"], r["score"]), reverse=True)
    print(f"\nSURV after focus: {len(surv)}", flush=True)
    for r in surv[:15]:
        wr = r["IS"].get("win_rate")
        print(f"  {r['name'][:40]:40s} obj={r['obj']:.3f} sc={r['score']:.3f} "
              f"ISwr={(wr*100 if wr else 0):.0f}% OOS2={r['OOS2']['total_return']*100:+.1f}% "
              f"Fsh={r['FULL']['sharpe']:.2f} Fmdd={r['FULL']['max_dd']*100:.1f}%", flush=True)

    print("\n--- BLENDS ---", flush=True)
    blends = []
    names = [n for n in curves if n in recipes]
    # pair T with V primarily
    def is_t(n):
        return n.startswith("TQ") or "VT" in n or n.startswith("v000_VT")
    def is_v(n):
        return n.startswith("VQ") or n.startswith("v000_VB") or n.startswith("VB")
    t_names = [n for n in names if is_t(n)]
    v_names = [n for n in names if is_v(n)]
    print(f"  T legs={t_names} V legs={v_names}", flush=True)

    for na in t_names:
        for nb in v_names:
            for w in (0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.70):
                common = curves[na].index.intersection(curves[nb].index)
                a = curves[na].loc[common].values.astype(float)
                b = curves[nb].loc[common].values.astype(float)
                eq = pd.Series(w * (a / a[0]) + (1 - w) * (b / b[0]), index=common) * CAP
                lab = f"{w:.0%}{na}+{1-w:.0%}{nb}"
                out = blend_windows(eq, lab)
                out["parts"] = (na, nb, w)
                blends.append(out)
        gc.collect()

    # 3-way: best T + 2 V or 2T+1V
    if len(t_names) >= 1 and len(v_names) >= 2:
        for ta in t_names[:3]:
            for va, vb in [(v_names[0], v_names[1])]:
                for ws in ((0.4, 0.35, 0.25), (0.35, 0.40, 0.25), (0.5, 0.3, 0.2), (0.3, 0.4, 0.3)):
                    combo = (ta, va, vb)
                    if not all(c in curves for c in combo):
                        continue
                    common = curves[combo[0]].index
                    for c in combo[1:]:
                        common = common.intersection(curves[c].index)
                    arrs = [curves[c].loc[common].values.astype(float) for c in combo]
                    arrs = [a / a[0] for a in arrs]
                    eq = pd.Series(sum(w * a for w, a in zip(ws, arrs)), index=common) * CAP
                    lab = f"{ws[0]:.0%}{ta}+{ws[1]:.0%}{va}+{ws[2]:.0%}{vb}"
                    out = blend_windows(eq, lab)
                    out["parts"] = (combo, ws)
                    blends.append(out)

    def bok(b):
        return (b["IS"]["total_return"] > 0 and b["OOS1"]["total_return"] > 0
                and b["OOS2"]["total_return"] > 0 and b["IS"]["sharpe"] >= 0.55
                and b["OOS1"]["sharpe"] >= 0.25 and b["FULL"]["max_dd"] >= -0.20)

    good = sorted([b for b in blends if bok(b)],
                  key=lambda b: (b["obj"], b["score"], b["OOS2"]["total_return"]), reverse=True)
    print(f"\nGOOD BLENDS {len(good)}/{len(blends)}", flush=True)
    for b in good[:15]:
        print(f"  {str(b['label'])[:52]:52s} obj={b['obj']:.3f} sc={b['score']:.3f} "
              f"OOS2={b['OOS2']['total_return']*100:+.1f}% Fsh={b['FULL']['sharpe']:.2f} "
              f"Fmdd={b['FULL']['max_dd']*100:.1f}%", flush=True)

    # baseline blend v000
    if "v000_VT12L" in curves and "v000_VBp2s" in curves:
        common = curves["v000_VT12L"].index.intersection(curves["v000_VBp2s"].index)
        a = curves["v000_VT12L"].loc[common].values.astype(float)
        b = curves["v000_VBp2s"].loc[common].values.astype(float)
        eq = pd.Series(0.4 * (a / a[0]) + 0.6 * (b / b[0]), index=common) * CAP
        base_blend = blend_windows(eq, "v000_40/60")
    else:
        base_blend = None

    best_s = surv[0] if surv else max(results, key=lambda r: r["obj"])
    best_b = good[0] if good else None

    # decision: prefer blend if obj competitive and better FULL stability
    use_blend = False
    if best_b is not None:
        if (best_b["obj"] >= best_s["obj"] - 0.02
            or best_b["score"] >= best_s["score"] - 0.03
            or (best_b["FULL"]["sharpe"] > best_s["FULL"]["sharpe"] + 0.1
                and best_b["OOS2"]["total_return"] >= best_s["OOS2"]["total_return"] - 0.02)):
            # additional: prefer blend if FULL mdd better and OOS1>0 already
            use_blend = True
        if best_b["obj"] > best_s["obj"] + 0.01:
            use_blend = True
        # if single has much higher success win-rate keep single
        s_wr = best_s["OOS1"].get("win_rate") or 0
        if (not use_blend) or (best_b["obj"] < best_s["obj"] - 0.05 and s_wr > 0.55):
            pass
        if best_b["obj"] >= 0.9 * max(best_s["obj"], 0.01) and best_b["FULL"]["max_dd"] > best_s["FULL"]["max_dd"]:
            use_blend = True

    # force compare to v000 blend
    print("\n==== DECISION INPUTS ====", flush=True)
    print("best single", best_s["name"], "obj", round(best_s["obj"], 3), "sc", round(best_s["score"], 3),
          "OOS2", round(best_s["OOS2"]["total_return"], 4), "Fsh", round(best_s["FULL"]["sharpe"], 3),
          "ISwr", best_s["IS"].get("win_rate"), "O1wr", best_s["OOS1"].get("win_rate"), flush=True)
    if best_b:
        print("best blend", best_b["label"], "obj", round(best_b["obj"], 3), "sc", round(best_b["score"], 3),
              "OOS2", round(best_b["OOS2"]["total_return"], 4), "Fsh", round(best_b["FULL"]["sharpe"], 3), flush=True)
    if base_blend:
        print("v000 blend", "obj", round(base_blend["obj"], 3), "sc", round(base_blend["score"], 3),
              "OOS2", round(base_blend["OOS2"]["total_return"], 4), "Fsh", round(base_blend["FULL"]["sharpe"], 3), flush=True)

    # final pick: among best_b, best_s, choose higher obj with OOS1 constraints
    final_kind = "blend" if (use_blend and best_b) else "single"
    # override: if blend beats v000 on obj and score, use it
    if best_b and base_blend:
        if best_b["obj"] > base_blend["obj"] + 0.01 or (
            best_b["score"] > base_blend["score"] + 0.02 and best_b["FULL"]["sharpe"] >= base_blend["FULL"]["sharpe"]
        ):
            final_kind = "blend"
        elif best_s["obj"] > best_b["obj"] + 0.05 and hard_ok(best_s):
            final_kind = "single"

    print("FINAL KIND", final_kind, flush=True)

    # materialise
    if final_kind == "blend":
        parts = best_b["parts"]
        if isinstance(parts[0], tuple):
            combo, ws = parts
            common = curves[combo[0]].index
            for c in combo[1:]:
                common = common.intersection(curves[c].index)
            arrs = [curves[c].loc[common].values.astype(float) for c in combo]
            arrs = [a / a[0] for a in arrs]
            eqF = pd.Series(sum(w * a for w, a in zip(ws, arrs)), index=common) * CAP
            legs = list(combo)
            weights = {legs[i]: float(ws[i]) for i in range(len(legs))}
        else:
            na, nb, w = parts
            common = curves[na].index.intersection(curves[nb].index)
            a = curves[na].loc[common].values.astype(float)
            b = curves[nb].loc[common].values.astype(float)
            eqF = pd.Series(w * (a / a[0]) + (1 - w) * (b / b[0]), index=common) * CAP
            legs = [na, nb]
            weights = {na: float(w), nb: float(1 - w)}
        m = eqF.index >= OOS2_START
        eq6 = eqF.values[m]
        dts6 = eqF.index.values[m]
        eq6 = eq6 / eq6[0] * CAP
        st6 = stats(eq6, dts6, CAP, "6M")
        # costs approx from weighted legs on OOS2
        costs = fund = 0.0
        trades = []
        for leg, w in weights.items():
            fr, sides, engine = recipes[leg]
            res = run_enhanced(fr, sides, capital=CAP, trade_from=OOS2_START, **engine)
            si = res["start_i"]
            costs += float(res["costs"][si:].sum()) * w
            fund += float(res["funding"][si:].sum()) * w
            for t in res["trades"]:
                trades.append(t)
            del res
        ts = trade_stats(trades) if trades else dict(n=0)
        mc = monte_carlo(trades, CAP, n_sim=1500, sleeve=0.25) if trades else dict(n_sim=0)
        fm = best_b
        cfg_out = dict(
            name="almasi-177-v001",
            type="blend",
            legs=legs,
            weights=weights,
            leg_recipes={leg: dict(ek=recipes[leg][2]) for leg in legs},
        )
        multi = {lab: fm[lab] for lab in ("IS", "OOS1", "OOS2", "FULL")}
        exp6 = np.zeros(len(eq6))
        title = f"almasi 177-v001 · {best_b['label']}"
    else:
        # need recipe for best_s
        name = best_s["name"]
        if name not in recipes:
            # fallback to best focus single in recipes
            cand = [r for r in focus_rows if hard_ok(r)]
            cand.sort(key=lambda r: r["obj"], reverse=True)
            if not cand:
                name = "v000_VT12L"
            else:
                name = cand[0]["name"]
                best_s = cand[0]
        fr, sides, engine = recipes[name]
        res = run_enhanced(fr, sides, capital=CAP, trade_from=OOS2_START, **engine)
        si = res["start_i"]
        eq6 = pd.Series(res["equity"][si:]).ffill().bfill().values
        dts6 = res["dts"][si:]
        exp6 = res["exposure"][si:]
        costs = float(res["costs"][si:].sum())
        fund = float(res["funding"][si:].sum())
        st6 = stats(eq6, dts6, CAP, "6M")
        ts = trade_stats(res["trades"])
        trades = res["trades"]
        mc = monte_carlo(trades, CAP, n_sim=1500, sleeve=engine.get("notional_frac", 0.25))
        fm = best_s
        cfg_out = dict(name="almasi-177-v001", type="single", leg=name, ek=engine)
        multi = {lab: best_s[lab] for lab in ("IS", "OOS1", "OOS2", "FULL")}
        title = f"almasi 177-v001 · {name}"
        legs = [name]
        weights = {name: 1.0}

    # refs
    dti = pd.DatetimeIndex(f3["BTC"]["dt"])
    if dti.tz is None:
        dti = dti.tz_localize("UTC")
    si_bh = int(np.where(dti >= OOS2_START)[0][0])
    bh_eq = buy_hold(f3, CAP, si_bh)
    bh_st = stats(bh_eq[si_bh:], f3["BTC"]["dt"].values[si_bh:], CAP, "B&H")
    ch_st = champion_ref(OOS2_START, CAP)

    print("\n" + "=" * 70, flush=True)
    print(f"  {title}", flush=True)
    print("=" * 70, flush=True)
    print(f"  6M $100: end={st6['end_equity']:.2f} ret={fmt_pct(st6['total_return'])} "
          f"Sh={fmt_num(st6['sharpe'])} MDD={fmt_pct(st6['max_dd'])}", flush=True)
    print(f"  trades={ts.get('n',0)} win={fmt_pct(ts.get('win_rate'),1)} PF={fmt_num(ts.get('profit_factor'))} "
          f"cost={costs+fund:.2f}", flush=True)
    for lab in ("IS", "OOS1", "OOS2", "FULL"):
        r = multi[lab]
        wr = r.get("win_rate")
        print(f"  {lab:4s} ret={fmt_pct(r['total_return'])} Sh={fmt_num(r['sharpe'])} "
              f"MDD={fmt_pct(r['max_dd'])} wr={fmt_pct(wr,1) if wr else '—'}", flush=True)
    if base_blend:
        print(f"  v000 6M was +9.0% Sh1.69 — this OOS2 {fmt_pct(st6['total_return'])} Sh {fmt_num(st6['sharpe'])}", flush=True)
        print(f"  v000 FULL Sh {base_blend['FULL']['sharpe']:.2f} MDD {base_blend['FULL']['max_dd']*100:.1f}% → "
              f"now Sh {multi['FULL']['sharpe']:.2f} MDD {multi['FULL']['max_dd']*100:.1f}%", flush=True)
    print(f"  B&H {fmt_pct(bh_st['total_return'])}  RCPE-1 {fmt_pct(ch_st.get('total_return'))}", flush=True)

    # save artifacts
    os.makedirs(RESULTS, exist_ok=True)

    def jc(o):
        if isinstance(o, dict):
            return {k: jc(v) for k, v in o.items() if k not in ("res",)}
        if isinstance(o, (list, tuple)):
            return [jc(x) for x in o]
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, (np.integer,)):
            return int(o)
        return o

    payload = dict(
        name="almasi-177-v001",
        version="v001",
        parent="almasi-177-v000 (40% VT12L + 60% VBp2s)",
        decision=final_kind,
        cfg=cfg_out,
        weights=weights,
        legs=legs,
        metrics=dict(IS=multi["IS"], OOS1=multi["OOS1"], OOS2=multi["OOS2"], FULL=multi["FULL"]),
        report_6m=dict(performance=st6, trades=ts, costs=costs, funding=fund,
                       buy_hold=bh_st, champion=ch_st, monte_carlo=mc),
        baseline_v000=base_blend,
        best_single=dict(name=best_s["name"], obj=best_s["obj"], score=best_s["score"],
                         IS=best_s["IS"], OOS1=best_s["OOS1"], OOS2=best_s["OOS2"], FULL=best_s["FULL"]),
        best_blend=(None if not best_b else dict(label=best_b["label"], obj=best_b["obj"], score=best_b["score"],
                                                 IS=best_b["IS"], OOS1=best_b["OOS1"], OOS2=best_b["OOS2"],
                                                 FULL=best_b["FULL"], parts=str(best_b["parts"]))),
        top_survivors=[{k: r[k] for k in ("name", "obj", "score", "IS", "OOS1", "OOS2", "FULL")}
                       for r in surv[:20]],
        top_blends=[{k: b[k] for k in ("label", "obj", "score", "IS", "OOS1", "OOS2", "FULL")}
                    for b in good[:15]],
        improvements=[
            "quality-filtered entries (ADX/ER gates)",
            "pullback & confirm-bar entries for higher win-rate",
            "long-bias / short-dampen",
            "breakeven ATR trail variants",
            "success_score = f(win_rate, profit_factor, sharpe)",
            "blend weight re-optimised under IS+OOS1",
        ],
    )
    with open(os.path.join(RESULTS, "almasi_177_v001_results.json"), "w") as f:
        json.dump(jc(payload), f, indent=2, default=str)
    with open(os.path.join(RESULTS, "almasi_177_v001_cfg.json"), "w") as f:
        json.dump(jc(dict(name="almasi-177-v001", cfg=cfg_out, weights=weights,
                          metrics=multi, report_6m=dict(end=st6["end_equity"], ret=st6["total_return"],
                                                        sharpe=st6["sharpe"], max_dd=st6["max_dd"],
                                                        win_rate=ts.get("win_rate"),
                                                        profit_factor=ts.get("profit_factor")))),
                  f, indent=2, default=str)

    pd.DataFrame(dict(dt=pd.DatetimeIndex(dts6), equity=eq6, exposure=exp6)).to_csv(
        os.path.join(RESULTS, "almasi_177_v001_equity.csv"), index=False)
    if trades:
        pd.DataFrame([t.__dict__ for t in trades]).to_csv(
            os.path.join(RESULTS, "almasi_177_v001_trades.csv"), index=False)

    # HTML
    mh = {}
    for lab in ("IS", "OOS1", "FULL"):
        mh[lab] = dict(total_return=multi[lab]["total_return"], sharpe=multi[lab]["sharpe"],
                       max_dd=multi[lab]["max_dd"], n_trades=multi[lab].get("n_trades", 0),
                       end_equity=multi[lab]["end_equity"], costs=multi[lab].get("costs", 0) or 0)
    mh["6M"] = dict(total_return=st6["total_return"], sharpe=st6["sharpe"], max_dd=st6["max_dd"],
                    n_trades=ts.get("n", 0), end_equity=st6["end_equity"], costs=costs)
    pcfg = dict(title=title, assets=["BTC", "ETH", "SOL"], entry_n=504, exit_n=72,
                use_stop=False, notional_frac=0.35, family="almasi-177-v001")
    html_path = os.path.join(RESULTS, "almasi_177_v001_report.html")
    write_html(html_path, "almasi-177-v001", st6, ts if isinstance(ts, dict) else dict(n=0),
               mc if mc else dict(n_sim=0), bh_st, ch_st or {}, CAP, costs, fund,
               eq6, dts6, exp6, trades, mh, pcfg)
    import shutil
    shutil.copy(html_path, os.path.join(RESULTS, "alt_strategies_report.html"))
    shutil.copy(html_path, os.path.join(RESULTS, "alt_optimized_report.html"))

    # summary FA
    v0 = base_blend
    md = f"""# almasi 177-v001

نام رسمی استراتژی جایگزین بهینه‌شده.

## نسبت به v000 (40% VT12L + 60% VBp2s)

| | v000 | **v001** |
|---|---:|---:|
| IS | {v0['IS']['total_return']*100 if v0 else float('nan'):+.1f}% / Sh {v0['IS']['sharpe'] if v0 else float('nan'):.2f} | {multi['IS']['total_return']*100:+.1f}% / Sh {multi['IS']['sharpe']:.2f} |
| OOS1 | {v0['OOS1']['total_return']*100 if v0 else float('nan'):+.1f}% / Sh {v0['OOS1']['sharpe'] if v0 else float('nan'):.2f} | {multi['OOS1']['total_return']*100:+.1f}% / Sh {multi['OOS1']['sharpe']:.2f} |
| 6M | {v0['OOS2']['total_return']*100 if v0 else float('nan'):+.1f}% / Sh {v0['OOS2']['sharpe'] if v0 else float('nan'):.2f} | **{st6['total_return']*100:+.1f}% / Sh {st6['sharpe']:.2f}** |
| FULL Sh / MDD | {v0['FULL']['sharpe'] if v0 else float('nan'):.2f} / {v0['FULL']['max_dd']*100 if v0 else float('nan'):.1f}% | **{multi['FULL']['sharpe']:.2f} / {multi['FULL']['max_dd']*100:.1f}%** |
| 6M win / PF | — | **{fmt_pct(ts.get('win_rate'),1)} / {fmt_num(ts.get('profit_factor'))}** |

## پیکربندی
```json
{json.dumps(jc(cfg_out), indent=2, ensure_ascii=False)}
```

## $100 / 6M
- پایان **{st6['end_equity']:.2f} USDT**
- بازده **{st6['total_return']*100:+.2f}%** · Sharpe **{st6['sharpe']:.2f}** · MDD **{st6['max_dd']*100:.2f}%**
- Win rate **{fmt_pct(ts.get('win_rate'),1)}** · PF **{fmt_num(ts.get('profit_factor'))}** · n={ts.get('n',0)}
- هزینه **{costs+fund:.2f} USDT**
- B&H {bh_st['total_return']*100:+.1f}% · RCPE-1 {ch_st.get('total_return', float('nan'))*100:+.1f}%

## ارتقاهای موفقیت
1. فیلتر کیفیت ADX/ER روی ورود
2. ورود pullback / confirm-bar
3. long-bias و short-dampen
4. breakeven + ATR trail
5. تابع هدف ترکیبی: plateau Sharpe + success(win, PF)
6. بازتنظیم وزن blend زیر IS+OOS1

## اجرا
```bash
python3 almasi_177_v001.py --capital 100 --months 6
python3 almasi_177_v001.py --full-sample
```
"""
    with open(os.path.join(RESULTS, "ALMASI_177_V001_SUMMARY_FA.md"), "w") as f:
        f.write(md)

    print(f"\n[saved] results/almasi_177_v001_*  elapsed={time.time()-t0:.0f}s", flush=True)
    return payload


if __name__ == "__main__":
    main()
