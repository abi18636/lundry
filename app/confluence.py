"""
v017 LAYER 1: Confluence Filter - Highest Priority
Require agreement of N strategies before execution
Filters noise from single-strategy random signals
"""
from __future__ import annotations

import logging
from typing import Dict, List

log = logging.getLogger("confluence")

def apply_confluence_filter(sleeve_results, min_agreement=3, min_confidence_avg=0.55):
    """
    Instead of simple sum of target_coin, only valid when at least N strategies agree
    This removes random noise from single strategy fallback
    """
    assets = set()
    for r in sleeve_results:
        assets.update(r.per_asset.keys())
    
    filtered_net = {}
    confluence_info = {}
    
    for asset in assets:
        votes_long = []
        votes_short = []
        votes_flat = []
        
        for r in sleeve_results:
            pa = r.per_asset.get(asset)
            if not pa:
                continue
            side = pa.get("side", 0)
            conf = pa.get("detail", {}).get("confidence", 0.5)
            if side > 0:
                votes_long.append((r.sleeve_id, conf))
            elif side < 0:
                votes_short.append((r.sleeve_id, conf))
            else:
                votes_flat.append((r.sleeve_id, conf))
        
        n_long, n_short, n_flat = len(votes_long), len(votes_short), len(votes_flat)
        avg_conf_long = sum(c for _, c in votes_long) / n_long if n_long else 0
        avg_conf_short = sum(c for _, c in votes_short) / n_short if n_short else 0
        
        # Condition: at least min_agreement same direction + avg confidence enough
        if n_long >= min_agreement and avg_conf_long >= min_confidence_avg and n_long > n_short:
            filtered_net[asset] = {"side": 1.0, "strength": n_long, "avg_conf": avg_conf_long, "votes_long": votes_long, "votes_short": votes_short}
            confluence_info[asset] = f"Confluence LONG {n_long}/{len(sleeve_results)} conf {avg_conf_long:.2f}"
        elif n_short >= min_agreement and avg_conf_short >= min_confidence_avg and n_short > n_long:
            filtered_net[asset] = {"side": -1.0, "strength": n_short, "avg_conf": avg_conf_short, "votes_long": votes_long, "votes_short": votes_short}
            confluence_info[asset] = f"Confluence SHORT {n_short}/{len(sleeve_results)} conf {avg_conf_short:.2f}"
        else:
            filtered_net[asset] = {"side": 0.0, "strength": 0, "avg_conf": 0, "votes_long": votes_long, "votes_short": votes_short}
            confluence_info[asset] = f"No confluence {n_long}L/{n_short}S/{n_flat}F need {min_agreement} agreement -> FLAT"
    
    return filtered_net, confluence_info


def estimate_signal_edge_bps(confluence_strength: int, avg_confidence: float) -> float:
    """Estimate edge based on confluence strength"""
    base_edge = 15  # Minimum assumed from historical backtest
    strength_bonus = confluence_strength * 8
    confidence_bonus = (avg_confidence - 0.5) * 40
    return base_edge + strength_bonus + confidence_bonus


def should_trade_after_costs(notional_usd, expected_edge_bps, fee_bps=10, slippage_bps=15):
    """Check if expected edge > round-trip costs"""
    round_trip_cost_bps = 2 * (fee_bps + slippage_bps)  # entry + exit
    net_edge_bps = expected_edge_bps - round_trip_cost_bps
    
    if net_edge_bps <= 0:
        return False, f"Edge {expected_edge_bps}bps <= cost {round_trip_cost_bps}bps, skip"
    return True, f"Net edge {net_edge_bps:.1f}bps after costs"
