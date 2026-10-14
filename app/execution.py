"""
v016 COMPANY FIXES - Execution layer
Fix #2: Remove 50% forced close, use real floor with 0.999 safety, retry 95%
Fix #1: Allow real close/reduce/reversal
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Optional

import logging
log = logging.getLogger("execution")


@dataclass
class PlanResult:
    status: str
    reason: str
    target: float
    delta: float
    amount: float
    direction: str
    reduce_only: bool
    intent: str


def floor_amount(amount: float, step: float) -> float:
    """Floor to step, never round up - prevents qty exceeds"""
    if amount <= 0 or step <= 0:
        return 0.0
    try:
        d_amount = Decimal(str(amount))
        d_step = Decimal(str(step))
        # Floor
        floored = (d_amount // d_step) * d_step
        return float(floored)
    except:
        # Fallback
        return math.floor(amount / step) * step


def signed_position(pos: dict) -> float:
    """Extract signed position size"""
    try:
        size = float(pos.get("size") or 0)
        size_ccy = float(pos.get("size_currency") or pos.get("qty") or 0)
        # Use size_currency if available
        raw = size_ccy if abs(size_ccy) > 0 else size
        direction = str(pos.get("direction", "")).lower()
        if direction in ("buy", "long"):
            return abs(raw)
        elif direction in ("sell", "short"):
            return -abs(raw)
        else:
            return raw
    except:
        return 0.0


def ioc_price(book: dict, direction: str, meta: dict, max_slippage_bps: float = 100.0) -> float:
    """Calculate IOC limit price with slippage"""
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    mark = float(book.get("mark_price") or 0)
    
    if not bids and not asks and mark <= 0:
        raise RuntimeError("no liquidity")
    
    if direction == "buy":
        # Buy: use ask or mark + slippage
        if asks:
            best_ask = float(asks[0][0])
            slippage = best_ask * (max_slippage_bps / 10000.0)
            return best_ask + slippage
        elif mark > 0:
            return mark * (1 + max_slippage_bps / 10000.0)
        else:
            raise RuntimeError("no ask liquidity")
    else:
        # Sell: use bid or mark - slippage
        if bids:
            best_bid = float(bids[0][0])
            slippage = best_bid * (max_slippage_bps / 10000.0)
            return best_bid - slippage
        elif mark > 0:
            return mark * (1 - max_slippage_bps / 10000.0)
        else:
            raise RuntimeError("no bid liquidity")


def plan_rebalance(current: float, desired: float, price: float, step: float, minimum: float, rebalance_threshold_usd: float = 1.0) -> PlanResult:
    """
    v016 FIX: Proper rebalance planning without 50% hack
    - Floor amount with step
    - Real close/reduce/reversal allowed
    - Threshold 5$ for min notional
    """
    if price <= 0:
        return PlanResult(
            status="no_price", reason="no price", target=desired,
            delta=0, amount=0, direction="buy", reduce_only=False, intent="none"
        )
    
    # Calculate delta
    delta = desired - current
    abs_delta = abs(delta)
    abs_desired = abs(desired)
    abs_current = abs(current)
    
    # Determine intent
    if abs_current < 1e-9 and abs_desired < 1e-9:
        return PlanResult(
            status="target_reached", reason="no position needed", target=desired,
            delta=0, amount=0, direction="buy", reduce_only=False, intent="none"
        )
    
    if abs_desired < 1e-9:
        # Close - FIXED46: Use reduce_only=True and meet min qty and notional
        intent = "close"
        reduce_only = True  # FIXED46: Use reduce_only to avoid flip, close only up to current
        direction = "sell" if current > 0 else "buy"
        # FIXED46: For close, use max(current, minimum, 5$/price) but with reduce_only
        # Exchange with reduce_only will only close up to current size, even if qty larger
        # So we can send larger qty to meet minimum, it will close current and not flip
        min_qty_for_notional = 5.0 / price if price>0 else minimum
        required_min = max(minimum, min_qty_for_notional, step)
        # Always use at least required_min to meet exchange minimums
        # Even if current < required_min, send required_min with reduce_only=True
        # Exchange should close current position (up to its size) and ignore excess
        amount = max(abs_current, required_min)
        # Floor to step but ensure at least required_min
        if amount >= step:
            floored = floor_amount(amount, step)
            if floored >= required_min * 0.9:
                amount = floored
            else:
                amount = required_min
        target = 0.0
    elif abs_current < 1e-9:
        # Open
        intent = "open"
        reduce_only = False
        direction = "buy" if desired > 0 else "sell"
        amount = floor_amount(abs_desired, step)
        target = desired
    elif current * desired < 0:
        # Reversal - FIXED46: close with reduce_only
        intent = "reverse"
        reduce_only = True
        direction = "sell" if current > 0 else "buy"
        min_qty_for_notional = 5.0 / price if price>0 else minimum
        required_min = max(minimum, min_qty_for_notional, step)
        amount = max(abs_current, required_min)
        if amount >= step:
            floored = floor_amount(amount, step)
            if floored >= required_min * 0.9:
                amount = floored
            else:
                amount = required_min
        target = 0.0
    elif abs_desired < abs_current:
        # Reduction
        intent = "reduce"
        reduce_only = True
        direction = "sell" if current > 0 else "buy"
        amount = floor_amount(abs_current - abs_desired, step)
        # v016 FIX: Don't use 50%, use real reduction with 0.999 safety
        if amount > abs_current * 0.999:
            amount = floor_amount(abs_current * 0.999, step)
        target = desired
    else:
        # Increase
        intent = "increase"
        reduce_only = False
        direction = "buy" if desired > 0 else "sell"
        amount = floor_amount(abs_desired - abs_current, step)
        target = desired
    
    # Check minimums - FIXED46: Allow close even if small, bypass checks for close intent
    if intent in ("close", "reverse"):
        # For close, don't skip for small delta or below minimum - try to close anyway
        # Exchange may reject, but we should attempt
        if amount <= 0:
            return PlanResult(
                status="target_reached", reason="close amount 0", target=desired,
                delta=delta, amount=0, direction=direction, reduce_only=reduce_only, intent=intent
            )
        # Only check if amount is truly 0, otherwise allow close attempt even if <5$
        if amount * price < 1.0 and amount < minimum * 0.5:
            # Still allow close attempt for tiny positions, but log
            pass
    else:
        if amount < minimum * 0.9:  # 10% tolerance
            if abs_delta * price < rebalance_threshold_usd:
                return PlanResult(
                    status="skip_small", reason=f"delta {abs_delta*price:.2f}$ < {rebalance_threshold_usd}$ threshold", target=desired,
                    delta=delta, amount=amount, direction=direction, reduce_only=reduce_only, intent=intent
                )
            if amount * price < 5.0:  # Exchange min notional
                return PlanResult(
                    status="below_exchange_minimum", reason=f"notional {amount*price:.2f}$ < 5$ exchange min", target=desired,
                    delta=delta, amount=amount, direction=direction, reduce_only=reduce_only, intent=intent
                )
    
    if amount <= 0:
        return PlanResult(
            status="target_reached", reason="amount floored to 0", target=desired,
            delta=delta, amount=0, direction=direction, reduce_only=reduce_only, intent=intent
        )
    
    return PlanResult(
        status="planned", reason=f"{intent} {amount} {direction}", target=target,
        delta=delta, amount=amount, direction=direction, reduce_only=reduce_only, intent=intent
    )


def fill_summary(resp: dict) -> dict:
    """Parse fill response"""
    try:
        if isinstance(resp, dict):
            data = resp.get("data") or resp.get("result") or resp
            if isinstance(data, dict):
                return {
                    "order_id": str(data.get("id") or data.get("order_id") or ""),
                    "filled": True,
                }
        return {"order_id": "", "filled": True}
    except:
        return {"order_id": "", "filled": False}
