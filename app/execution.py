"""
app/execution.py - Passive Maker Execution & Rebalance Logic (Fix 1 & 3)
Eliminates IOC/Market orders and enforces rigid minimum lot constraints.
v019 + FIXED56 compatibility
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Optional

@dataclass
class PlanResult:
    status: str
    reason: str
    target: float
    delta: float
    amount: float
    direction: str
    reduce_only: bool
    price: float

def maker_limit_price(book: dict, direction: str, tick_size: float = 0.01) -> float:
    """
    Fix 1: Provides a passive Maker price at the top of the book.
    Ensures the order does not cross the spread.
    """
    best_bid = book.get("best_bid", 0.0)
    best_ask = book.get("best_ask", 0.0)
    
    if best_bid <= 0 or best_ask <= 0:
        # Fallback to mark price if book unavailable
        mark = book.get("mark_price") or book.get("mark") or 0.0
        if mark > 0:
            return round(mark, 4)
        raise RuntimeError("No liquid book available for maker pricing")

    if direction == "buy":
        # Join Best Bid (Maker)
        return round(best_bid, 4)
    else:
        # Join Best Ask (Maker)
        return round(best_ask, 4)

def plan_rebalance(
    current: float,
    desired: float,
    price: float,
    contract_step: float,
    min_trade_amount: float,
    rebalance_threshold_usd: float = 15.0
) -> PlanResult:
    """
    Fix 2 & 3: Filter noise below $15 threshold and enforce step precision.
    v019: Increased threshold to $15 to eliminate micro-churn
    """
    if price <= 0:
        return PlanResult("no_price", "Price unavailable", desired, 0, 0, "buy", False, 0.0)

    delta = desired - current
    notional_delta = abs(delta) * price

    # Filter out micro-churn (Fix 2) - v019 $15 threshold
    if notional_delta < rebalance_threshold_usd and abs(desired) > 1e-9:
        return PlanResult("skip_small", f"Delta ${notional_delta:.2f} < threshold ${rebalance_threshold_usd}", desired, delta, 0, "buy", False, price)

    direction = "buy" if delta > 0 else "sell"
    reduce_only = (current > 0 and direction == "sell") or (current < 0 and direction == "buy")
    
    # Quantize amount to exchange contract step
    steps = math.floor(abs(delta) / contract_step)
    amount = steps * contract_step

    if amount < min_trade_amount:
        if reduce_only:
            # If closing remainder, close exactly what is left - v019 FIXED56 close tiny fix
            amount = abs(current)
            # Ensure meets $5 min notional with ceil logic
            min_notional = 5.1
            if amount * price < min_notional:
                # Calculate required amount to meet min notional
                required = math.ceil(min_notional / price / contract_step) * contract_step
                amount = max(amount, required)
        else:
            return PlanResult("below_min", "Amount below exchange lot size", desired, delta, 0, direction, reduce_only, price)

    return PlanResult(
        status="planned",
        reason=f"Valid rebalance {direction} {amount:.4f}",
        target=desired,
        delta=delta,
        amount=amount,
        direction=direction,
        reduce_only=reduce_only,
        price=price
    )

# Backward compatibility with old execution functions
def calculate_target_position(*args, **kwargs):
    """Legacy wrapper"""
    return 0.0

def check_spread(*args, **kwargs):
    return True

# v018 compat
floor_amount = 0.001
