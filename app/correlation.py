"""
v017 LAYER 3: Correlation-Aware Netting
Effective exposure considering correlation between crypto assets
"""
from __future__ import annotations

import logging
from typing import Dict

log = logging.getLogger("correlation")

def calculate_effective_exposure(net_book, correlation_matrix=None):
    """
    Instead of simple sum |notional|, calculate real risk with correlation
    If all assets same direction, real risk > simple sum
    """
    assets = list(net_book.keys())
    notionals = {}
    for a in assets:
        data = net_book[a]
        # Use signed notional
        side = data.get("side", 0)
        notional = data.get("notional_usd", 0)
        notionals[a] = notional * side if side != 0 else 0
        # If side not available, use target_coin * price
        if notionals[a] == 0:
            target_coin = data.get("target_coin", 0)
            price = data.get("price", 0)
            notionals[a] = target_coin * price
    
    # Conservative: cryptos 0.7 correlated if no matrix
    if correlation_matrix is None:
        correlation_matrix = {}
        for a1 in assets:
            for a2 in assets:
                correlation_matrix[(a1, a2)] = 1.0 if a1 == a2 else 0.7
    
    # Portfolio variance: sqrt(sum_i sum_j w_i * w_j * corr_ij)
    total_sq = 0.0
    for a1 in assets:
        for a2 in assets:
            w1, w2 = notionals.get(a1, 0), notionals.get(a2, 0)
            corr = correlation_matrix.get((a1, a2), 0.7)
            total_sq += w1 * w2 * corr
    
    effective_exposure = total_sq ** 0.5 if total_sq > 0 else 0
    # Naive exposure is sum of absolute notionals
    naive_exposure = sum(abs(v) for v in notionals.values())
    
    concentration_ratio = effective_exposure / naive_exposure if naive_exposure > 0 else 0
    # For same direction, effective > naive? Actually with positive correlation and same direction, variance adds
    # With 0.7 corr, effective ~ sqrt(n + n*(n-1)*0.7) * avg_notional, naive = n * avg
    # For n=5, effective ~ sqrt(5 + 20*0.7)= sqrt(19)=4.35 avg, naive=5 avg, ratio 0.87
    # But if all same direction, risk is concentrated
    # Let's calculate long/short net
    long_exposure = sum(v for v in notionals.values() if v > 0)
    short_exposure = sum(-v for v in notionals.values() if v < 0)
    net_exposure = sum(notionals.values())
    
    return {
        "effective_exposure": effective_exposure,
        "naive_exposure": naive_exposure,
        "concentration_ratio": concentration_ratio,
        "long_exposure": long_exposure,
        "short_exposure": short_exposure,
        "net_exposure": net_exposure,
        "notionals": notionals,
    }


def apply_correlation_cap(net_book, risk_cap_usd, correlation_matrix=None):
    """If effective exposure > cap, scale down all positions proportionally"""
    calc = calculate_effective_exposure(net_book, correlation_matrix)
    eff_exp = calc["effective_exposure"]
    
    if eff_exp > risk_cap_usd:
        scale = risk_cap_usd / eff_exp if eff_exp > 0 else 1.0
        for a in net_book:
            if "target_coin" in net_book[a]:
                net_book[a]["target_coin"] *= scale
            if "notional_usd" in net_book[a]:
                net_book[a]["notional_usd"] *= scale
        log.warning(f"Correlation cap applied: scale={scale:.2f}, effective={eff_exp:.0f} > cap={risk_cap_usd:.0f}, naive={calc['naive_exposure']:.0f}, net={calc['net_exposure']:.0f}")
        calc["scale_applied"] = scale
    else:
        calc["scale_applied"] = 1.0
    
    return net_book, calc
