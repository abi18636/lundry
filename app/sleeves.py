from __future__ import annotations

"""
Independent strategy sleeves — NO DNA mixing.

Each sleeve:
  - has its own capital weight
  - computes its own long_only side per asset {0, +1}
  - sizes its own notional from ITS capital only
  - never reads another sleeve's signal

Netting happens ONLY at the exchange order layer (single Deribit account):
  target_coin[asset] = sum(sleeve_i.target_coin[asset])
"""


import logging
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STRAT = ROOT / "strategies"
if str(STRAT) not in sys.path:
    sys.path.insert(0, str(STRAT))

from alt_turtle_ns import atr, adx, kaufman_er, rma, donchian, ema  # noqa: E402
from almasi_177_opt import sig_turtle_quality, sig_vb_quality  # noqa: E402
from zenith_apex_rebirth import make_signal as zenith_make, ek as zenith_ek  # noqa: E402

log = logging.getLogger("sleeves")

ASSETS_DEFAULT = ("BTC", "ETH", "SOL")  # overridden by caller

def _asset_order(frames) -> list:
    keys = list(frames.keys())
    pref = [a for a in ASSETS_DEFAULT if a in keys]
    rest = sorted(a for a in keys if a not in pref)
    return pref + rest


# ── shared enrich ───────────────────────────────────────────────────────────
def enrich(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    if not pd.api.types.is_datetime64_any_dtype(d["dt"]):
        d["dt"] = pd.to_datetime(d["dt"], utc=True)
    c = d["close"].astype(float)
    h = d["high"].astype(float)
    l = d["low"].astype(float)
    d["atr"] = atr(h, l, c, 14)
    d["adx"] = adx(h, l, c, 14)
    d["er"] = kaufman_er(c, 48)
    d["ema"] = ema(c, 168)
    up = h.diff()
    dn = -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_s = rma(tr, 14)
    d["plus_di"] = (100 * rma(pd.Series(plus_dm, index=d.index), 14) / atr_s.replace(0, np.nan)).fillna(0)
    d["minus_di"] = (100 * rma(pd.Series(minus_dm, index=d.index), 14) / atr_s.replace(0, np.nan)).fillna(0)
    d = d.set_index(d["dt"], drop=False)
    return d


def prepare_frames(ohlc: Dict[str, pd.DataFrame], assets=None) -> Dict[str, pd.DataFrame]:
    if assets is None:
        assets = list(ohlc.keys())
    out = {}
    for a in assets:
        if a not in ohlc or ohlc[a] is None or len(ohlc[a]) < 300:
            log.warning("skip %s — insufficient candles (%s)", a, 0 if a not in ohlc or ohlc[a] is None else len(ohlc[a]))
            continue
        out[a] = enrich(ohlc[a])
    return out


def _last_side(sig: pd.Series) -> float:
    if sig is None or len(sig) == 0:
        return 0.0
    v = float(sig.iloc[-1])
    if not np.isfinite(v):
        return 0.0
    return 1.0 if v > 0 else ( -1.0 if v < 0 else 0.0)


def _vol_notional(df: pd.DataFrame, capital: float, vol_target: float = 0.12, lev_cap: float = 1.0) -> float:
    rets = np.log(df["close"].astype(float) / df["close"].astype(float).shift(1)).dropna()
    if len(rets) < 48:
        return min(capital * 0.35, capital * lev_cap)
    vol = float(rets.tail(336).std() * np.sqrt(24 * 365))
    if not np.isfinite(vol) or vol <= 1e-6:
        vol = 0.5
    raw = capital * (vol_target / vol)
    return float(max(0.0, min(raw, capital * lev_cap)))


# ── inst-v3 TSMOM (standalone copy of lab DNA — no cross-import of heavy file) ─
def sig_tsmom_discrete(
    frames: Dict[str, pd.DataFrame],
    horizons=(24, 168, 720),
    vote_min: float = 0.67,
    confirm: int = 5,
    adx_min: float = 22.0,
    er_min: float = 0.10,
    long_only: bool = True,
    lag: int = 1,
    exit_vote: float = 0.20,
) -> Dict[str, pd.Series]:
    out = {}
    for a, df in frames.items():
        c = df["close"].astype(float).values
        n = len(c)
        adx_v = df["adx"].astype(float).values if "adx" in df.columns else np.zeros(n)
        er_v = df["er"].astype(float).values if "er" in df.columns else np.ones(n)
        votes = np.zeros(n)
        for h in horizons:
            r = np.zeros(n)
            if h < n:
                r[h:] = c[h:] / c[:-h] - 1.0
            votes += np.sign(r)
        votes /= max(len(horizons), 1)
        raw = np.zeros(n)
        pos = run_up = run_dn = 0
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
                else:
                    run_dn = 0
            else:
                if long_only or v >= -exit_vote or (not q and v > -vote_min * 0.5):
                    pos = 0
                    run_up = run_dn = 0
                else:
                    run_up = 0
            raw[i] = pos
        sig = np.zeros(n)
        if lag > 0:
            sig[lag:] = raw[:-lag]
        else:
            sig = raw.astype(float)
        out[a] = pd.Series(sig, index=df.index)
    return out


@dataclass
class SleeveResult:
    sleeve_id: str
    title: str
    capital: float
    weight: float
    per_asset: Dict[str, dict] = field(default_factory=dict)  # side, vote/detail, price, target_coin, notional_usd
    notes: str = ""


@dataclass
class SleeveSpec:
    sleeve_id: str
    title: str
    weight: float  # fraction of total bot capital
    enabled: bool = True
    kind: str = ""


# ── individual independent sleeves ─────────────────────────────────────────
def sleeve_zenith_apex(frames: Dict[str, pd.DataFrame], capital: float, lev_cap: float = 1.0) -> SleeveResult:
    """Two funded legs, NOT a vote that discards impulse-only entries."""
    assets = _asset_order(frames)
    res = SleeveResult("zenith_apex", "zenith-v001 REBIRTH apex (compress80/impulse20)", capital, 0.0)
    if not assets:
        return res
    compress_frames = {a: frames[a] for a in assets if len(frames[a]) >= 336}
    impulse_frames = {a: frames[a] for a in assets if len(frames[a]) >= 168}
    cfg_c = dict(kind="compress", tag="compress_A", pct_lo=0.25, break_n=96,
                 exit_n=48, confirm=2, adx_min=12.0, atr_n=24, pct_n=336,
                 lag=0, assets=list(compress_frames))
    cfg_i = dict(kind="impulse", tag="impulse_B", impulse_atr=2.8, ema_n=48,
                 hold_atr_trail=2.2, max_hold=72, confirm=1, adx_min=16.0,
                 er_min=0.05, lag=0, assets=list(impulse_frames))
    sc = zenith_make(cfg_c, compress_frames) if compress_frames else {}
    si = zenith_make(cfg_i, impulse_frames) if impulse_frames else {}
    cs = {a: 1.0 if _last_side(sc.get(a)) > 0 else 0.0 for a in assets}
    ins = {a: 1.0 if _last_side(si.get(a)) > 0 else 0.0 for a in assets}
    nc, ni = max(1, sum(cs.values())), max(1, sum(ins.values()))
    for a in assets:
        px = float(frames[a]["close"].iloc[-1])
        cap_c, cap_i = capital * 0.80, capital * 0.20
        nt_c = min(_vol_notional(frames[a], cap_c, 0.12, lev_cap), cap_c * lev_cap / nc) if cs[a] else 0.0
        nt_i = min(_vol_notional(frames[a], cap_i, 0.12, lev_cap), cap_i * lev_cap / ni) if ins[a] else 0.0
        notional = nt_c + nt_i
        res.per_asset[a] = dict(
            side=1.0 if notional > 0 else 0.0,
            detail={"compress": cs[a], "impulse": ins[a],
                    "compress_notional": nt_c, "impulse_notional": nt_i,
                    "weighted_activity": 0.8 * cs[a] + 0.2 * ins[a]},
            price=px, target_coin=notional/px, notional_usd=notional,
            dt=str(frames[a]["dt"].iloc[-1]),
            eligibility={"compress": a in compress_frames, "impulse": a in impulse_frames},
        )
    res.notes = "independent funded 80/20 legs; confirmed bars; 25-asset live allocation is not a bit-exact historical portfolio"
    return res


def sleeve_almasi_primary(frames: Dict[str, pd.DataFrame], capital: float, lev_cap: float = 1.0) -> SleeveResult:
    """almasi 177-v001 PRIMARY — 70% TQ_core + 30% VQ_core (internal only)."""
    res = SleeveResult("almasi_primary", "almasi 177-v001 primary (TQ70/VQ30)", capital, 0.0)
    assets = [a for a in _asset_order(frames) if a in frames]
    if not assets:
        return res
    # TQ on all assets
    tq = {}
    for a in assets:
        tq[a] = (sig_turtle_quality(
            frames[a], entry_n=504, exit_n=72, adx_min=18.0, er_min=0.10,
            pullback=False, confirm_bars=2, long_only=True,
        ) if len(frames[a]) >= 506 else pd.Series(0.0, index=frames[a].index))
    # VQ on BTC/ETH only
    vq_assets = [a for a in ("BTC", "ETH") if a in frames]
    vq = {}
    for a in vq_assets:
        vq[a] = sig_vb_quality(
            frames[a], mult=2.5, exit_mult=1.2, base_n=336,
            adx_min=18.0, er_min=0.10, long_bias=True,
        )

    # Internal capital split of THIS sleeve only
    cap_tq = capital * 0.70
    cap_vq = capital * 0.30

    # TQ book
    tq_sides = {a: (1.0 if _last_side(tq[a]) > 0 else 0.0) for a in assets}
    n_tq = max(1, sum(1 for s in tq_sides.values() if s > 0))
    # VQ book
    vq_sides = {a: (1.0 if _last_side(vq[a]) > 0 else 0.0) for a in vq_assets}
    n_vq = max(1, sum(1 for s in vq_sides.values() if s > 0))

    for a in assets:
        px = float(frames[a]["close"].iloc[-1])
        coin = 0.0
        detail = {"TQ": tq_sides.get(a, 0.0), "VQ": vq_sides.get(a, 0.0)}
        if tq_sides.get(a, 0) > 0:
            ntl = min(_vol_notional(frames[a], cap_tq, 0.12, lev_cap), cap_tq * lev_cap / n_tq)
            coin += ntl / px
        if a in vq_sides and vq_sides[a] > 0:
            # VQ lab used fixed notional_frac ~0.45 of its sleeve with max 2 pos
            ntl = min(cap_vq * 0.45, cap_vq * lev_cap / n_vq, cap_vq * lev_cap)
            coin += ntl / px
        side = 1.0 if coin > 0 else 0.0
        res.per_asset[a] = dict(
            side=side, detail=detail, price=px,
            target_coin=coin, notional_usd=coin * px,
            dt=str(frames[a]["dt"].iloc[-1]),
            eligibility={"TQ": len(frames[a]) >= 506, "VQ": a in vq_assets},
        )
    res.notes = "independent almasi TQ/VQ — not mixed with zenith/iv3"
    return res


def sleeve_inst_v3_stable(frames: Dict[str, pd.DataFrame], capital: float, lev_cap: float = 1.0) -> SleeveResult:
    """institutional-v3 stable — 70% PRIMARY TSMOM + 30% BROAD (internal only)."""
    res = SleeveResult("inst_v3_stable", "institutional-v3 stable (TSMOM 70/30)", capital, 0.0)
    assets = [a for a in _asset_order(frames) if a in frames]
    if not assets:
        return res
    fr = {a: frames[a] for a in assets if len(frames[a]) >= 726}
    prim = sig_tsmom_discrete(
        fr, horizons=(24, 168, 720), vote_min=0.67, confirm=5,
        adx_min=22.0, er_min=0.10, long_only=True, lag=0, exit_vote=0.20,
    )
    broad = sig_tsmom_discrete(
        fr, horizons=(24, 168, 720), vote_min=0.50, confirm=5,
        adx_min=22.0, er_min=0.08, long_only=True, lag=0, exit_vote=0.20,
    )
    cap_p, cap_b = capital * 0.70, capital * 0.30
    sp = {a: (1.0 if _last_side(prim.get(a)) > 0 else 0.0) for a in assets}
    sb = {a: (1.0 if _last_side(broad.get(a)) > 0 else 0.0) for a in assets}
    np_ = max(1, sum(1 for x in sp.values() if x > 0))
    nb_ = max(1, sum(1 for x in sb.values() if x > 0))
    for a in assets:
        px = float(frames[a]["close"].iloc[-1])
        coin = 0.0
        if sp[a] > 0:
            ntl = min(_vol_notional(frames[a], cap_p, 0.12, lev_cap), cap_p * lev_cap / np_)
            coin += ntl / px
        if sb[a] > 0:
            ntl = min(_vol_notional(frames[a], cap_b, 0.12, lev_cap), cap_b * lev_cap / nb_)
            coin += ntl / px
        res.per_asset[a] = dict(
            side=1.0 if coin > 0 else 0.0,
            detail={"primary": sp[a], "broad": sb[a]},
            price=px, target_coin=coin, notional_usd=coin * px,
            dt=str(frames[a]["dt"].iloc[-1]),
        )
    for a in assets:
        res.per_asset[a]["eligibility"] = {"primary": a in fr, "broad": a in fr}
        if a not in fr:
            res.per_asset[a]["reason"] = "insufficient_history: need 726 completed hourly bars"
    res.notes = "independent CTA TSMOM — not mixed with almasi/zenith"
    return res


def sleeve_inst_v3_primary(frames: Dict[str, pd.DataFrame], capital: float, lev_cap: float = 1.0) -> SleeveResult:
    """institutional-v3 primary only — single consensus TSMOM leg."""
    res = SleeveResult("inst_v3_primary", "institutional-v3 primary (TSMOM consensus)", capital, 0.0)
    assets = [a for a in _asset_order(frames) if a in frames]
    if not assets:
        return res
    fr = {a: frames[a] for a in assets if len(frames[a]) >= 726}
    prim = sig_tsmom_discrete(
        fr, horizons=(24, 168, 720), vote_min=0.67, confirm=5,
        adx_min=22.0, er_min=0.10, long_only=True, lag=0, exit_vote=0.20,
    )
    sides = {a: (1.0 if _last_side(prim.get(a)) > 0 else 0.0) for a in assets}
    n_long = max(1, sum(1 for x in sides.values() if x > 0))
    for a in assets:
        px = float(frames[a]["close"].iloc[-1])
        coin = 0.0
        if sides[a] > 0:
            ntl = min(_vol_notional(frames[a], capital, 0.12, lev_cap), capital * lev_cap / n_long)
            coin = ntl / px
        res.per_asset[a] = dict(
            side=sides[a], detail={"primary": sides[a]}, price=px,
            target_coin=coin, notional_usd=coin * px, dt=str(frames[a]["dt"].iloc[-1]),
        )
    for a in assets:
        res.per_asset[a]["eligibility"] = {"primary": a in fr}
        if a not in fr:
            res.per_asset[a]["reason"] = "insufficient_history: need 726 completed hourly bars"
    res.notes = "independent primary-only TSMOM"
    return res


def sleeve_zenith_endurance(frames: Dict[str, pd.DataFrame], capital: float, lev_cap: float = 1.0) -> SleeveResult:
    """zenith endurance — impulse-only (tight MDD path)."""
    res = SleeveResult("zenith_endurance", "zenith-v001 endurance (impulse only)", capital, 0.0)
    assets = [a for a in _asset_order(frames) if a in frames]
    if not assets:
        return res
    cfg = dict(
        kind="impulse", tag="impulse_B", impulse_atr=2.8, ema_n=48,
        hold_atr_trail=2.2, max_hold=72, confirm=1, adx_min=16.0, er_min=0.05,
        ek=zenith_ek(0.12, cd=4, nf=0.40), assets=assets, lag=0,
    )
    sig = zenith_make(cfg, frames)
    sides = {a: (1.0 if _last_side(sig.get(a)) > 0 else 0.0) for a in assets}
    n_long = max(1, sum(1 for x in sides.values() if x > 0))
    for a in assets:
        px = float(frames[a]["close"].iloc[-1])
        coin = 0.0
        if sides[a] > 0:
            ntl = min(_vol_notional(frames[a], capital, 0.12, lev_cap), capital * lev_cap / n_long)
            coin = ntl / px
        res.per_asset[a] = dict(
            side=sides[a], detail={"impulse": sides[a]}, price=px,
            target_coin=coin, notional_usd=coin * px, dt=str(frames[a]["dt"].iloc[-1]),
        )
    return res


# Registry
SLEEVE_BUILDERS = {
    "zenith_apex": sleeve_zenith_apex,
    "zenith_endurance": sleeve_zenith_endurance,
    "almasi_primary": sleeve_almasi_primary,
    "inst_v3_stable": sleeve_inst_v3_stable,
    "inst_v3_primary": sleeve_inst_v3_primary,
}

# Default super-bot allocation (sum=1.0) — independent sleeves
DEFAULT_WEIGHTS = {
    "zenith_apex": 0.25,
    "almasi_primary": 0.25,
    "inst_v3_stable": 0.30,
    "inst_v3_primary": 0.10,
    "zenith_endurance": 0.10,
}


def parse_weights(s: str) -> Dict[str, float]:
    """Format: zenith_apex:0.25,almasi_primary:0.25,..."""
    if not s or not s.strip():
        return dict(DEFAULT_WEIGHTS)
    out = {}
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        out[k.strip()] = float(v)
    if not out:
        return dict(DEFAULT_WEIGHTS)
    # normalize
    tot = sum(max(0.0, w) for w in out.values()) or 1.0
    return {k: max(0.0, w) / tot for k, w in out.items() if k in SLEEVE_BUILDERS}


def run_all_sleeves(
    frames: Dict[str, pd.DataFrame],
    total_capital: float,
    weights: Optional[Dict[str, float]] = None,
    lev_cap: float = 1.0,
    enabled: Optional[List[str]] = None,
) -> Tuple[List[SleeveResult], Dict[str, dict]]:
    """
    Run each enabled sleeve independently.
    Returns (sleeve_results, net_book[asset] = {target_coin, notional, contributors})
    """
    wmap = dict(weights or DEFAULT_WEIGHTS)
    if enabled:
        wmap = {k: v for k, v in wmap.items() if k in enabled}
    # renormalize
    tot = sum(wmap.values()) or 1.0
    wmap = {k: v / tot for k, v in wmap.items()}

    results: List[SleeveResult] = []
    for sid, w in wmap.items():
        if w <= 0 or sid not in SLEEVE_BUILDERS:
            continue
        cap = float(total_capital) * float(w)
        try:
            r = SLEEVE_BUILDERS[sid](frames, capital=cap, lev_cap=lev_cap)
            r.weight = w
            r.capital = cap
            results.append(r)
        except Exception as e:
            log.exception("sleeve %s failed: %s", sid, e)
            err = SleeveResult(sid, sid, cap, w, notes=f"ERROR: {e}")
            results.append(err)

    # Net book — sum of independent targets (NOT signal mix)
    net: Dict[str, dict] = {}
    for a in _asset_order(frames):
        px = float(frames[a]["close"].iloc[-1])
        coin = 0.0
        contrib = {}
        for r in results:
            pa = r.per_asset.get(a) or {}
            c = float(pa.get("target_coin") or 0.0)
            if c:
                contrib[r.sleeve_id] = {
                    "coin": c,
                    "notional": c * px,
                    "side": pa.get("side"),
                    "detail": pa.get("detail"),
                }
            coin += c
        net[a] = {
            "target_coin": coin,
            "notional_usd": coin * px,
            "price": px,
            "side": 1.0 if coin > 0 else 0.0,
            "contributors": contrib,
            "dt": str(frames[a]["dt"].iloc[-1]),
        }
    return results, net
