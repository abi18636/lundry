from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Dict, Tuple

import numpy as np
import pandas as pd

# strategies/ is sibling of app/
ROOT = Path(__file__).resolve().parents[1]
STRAT = ROOT / "strategies"
if str(STRAT) not in sys.path:
    sys.path.insert(0, str(STRAT))

from zenith_apex_rebirth import make_signal, ek  # noqa: E402
from alt_turtle_ns import atr, adx, kaufman_er, rma  # noqa: E402

log = logging.getLogger("signals")

# Bit-exact apex REBIRTH legs (must match zenith_v001.py)
ASSETS_DEFAULT = ("BTC", "ETH", "SOL")

LEG_COMPRESS = dict(
    kind="compress",
    tag="compress_A",
    pct_lo=0.25,
    break_n=96,
    exit_n=48,
    confirm=2,
    adx_min=12.0,
    atr_n=24,
    pct_n=336,
    ek=ek(0.12, cd=8, nf=0.42),
    assets=list(ASSETS_DEFAULT),
)
LEG_IMPULSE = dict(
    kind="impulse",
    tag="impulse_B",
    impulse_atr=2.8,
    ema_n=48,
    hold_atr_trail=2.2,
    max_hold=72,
    confirm=1,
    adx_min=16.0,
    er_min=0.05,
    ek=ek(0.12, cd=4, nf=0.40),
    assets=list(ASSETS_DEFAULT),
)

PROFILE_WEIGHTS = {
    "apex": (("compress", 0.80), ("impulse", 0.20)),
    "crown": (("compress", 0.70), ("impulse", 0.30)),
    "endurance": (("impulse", 1.0),),
    "balanced": (("compress", 0.55), ("impulse", 0.30)),  # thrust skipped live (needs more gates)
}


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
    d = d.set_index("dt", drop=False)
    return d


def prepare_frames(ohlc: Dict[str, pd.DataFrame], assets) -> Dict[str, pd.DataFrame]:
    out = {}
    for a in assets:
        if a not in ohlc or ohlc[a] is None or len(ohlc[a]) < 200:
            log.warning("insufficient candles for %s", a)
            continue
        out[a] = enrich(ohlc[a])
    return out


def last_side(sig: pd.Series) -> float:
    if sig is None or len(sig) == 0:
        return 0.0
    v = float(sig.iloc[-1])
    if not np.isfinite(v):
        return 0.0
    return 1.0 if v > 0 else 0.0  # long_only clamp


def compute_target_sides(frames: Dict[str, pd.DataFrame], profile: str = "apex") -> Dict[str, dict]:
    """
    Returns per-asset desired side in {0, +1} and leg breakdown.
    Blend rule: long if weighted vote >= 0.5 (capital-preservation; no shorts).
    """
    profile = (profile or "apex").lower()
    weights = PROFILE_WEIGHTS.get(profile, PROFILE_WEIGHTS["apex"])
    assets = [a for a in ASSETS_DEFAULT if a in frames]
    if not assets:
        return {}

    legs = {}
    if any(n == "compress" for n, _ in weights):
        cfg = dict(LEG_COMPRESS)
        cfg["assets"] = assets
        legs["compress"] = make_signal(cfg, frames)
    if any(n == "impulse" for n, _ in weights):
        cfg = dict(LEG_IMPULSE)
        cfg["assets"] = assets
        legs["impulse"] = make_signal(cfg, frames)

    out = {}
    for a in assets:
        vote = 0.0
        detail = {}
        for name, w in weights:
            s = legs.get(name, {}).get(a)
            side = last_side(s) if s is not None else 0.0
            detail[name] = side
            vote += w * side
        desired = 1.0 if vote >= 0.50 else 0.0
        px = float(frames[a]["close"].iloc[-1])
        out[a] = {
            "side": desired,
            "vote": vote,
            "legs": detail,
            "price": px,
            "dt": str(frames[a]["dt"].iloc[-1]),
        }
    return out


def vol_target_notional(frames: Dict[str, pd.DataFrame], asset: str, capital: float, vol_target: float = 0.12, lev_cap: float = 1.0) -> float:
    """Simple sleeve notional from realized vol (lab-like)."""
    df = frames[asset]
    rets = np.log(df["close"].astype(float) / df["close"].astype(float).shift(1)).dropna()
    if len(rets) < 48:
        return min(capital * 0.3, capital * lev_cap)
    vol = float(rets.tail(336).std() * np.sqrt(24 * 365))  # annualized from hourly
    if not np.isfinite(vol) or vol <= 1e-6:
        vol = 0.5
    raw = capital * (vol_target / vol)
    return float(max(0.0, min(raw, capital * lev_cap)))
