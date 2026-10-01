"""Pure, testable execution rules. No network access and no strategy optimisation."""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Optional


def floor_amount(value: float, step: float) -> float:
    """Round magnitude DOWN to exchange lots; never inflate a strategy allocation."""
    if not math.isfinite(value) or not math.isfinite(step) or step <= 0:
        raise ValueError("invalid quantity or contract_size")
    units = (Decimal(str(abs(value))) / Decimal(str(step))).to_integral_value(rounding=ROUND_DOWN)
    result = float(units * Decimal(str(step)))
    return -result if value < 0 else result


def signed_position(position: Optional[dict]) -> float:
    p = position or {}
    if not p:
        return 0.0
    # Linear Deribit futures: size is USD, size_currency is base-coin quantity.
    if p.get("size_currency") is None:
        raise ValueError("linear position missing size_currency; refusing to guess units")
    q = float(p["size_currency"] or 0)
    if not math.isfinite(q):
        raise ValueError("non-finite position")
    direction = str(p.get("direction") or "").lower()
    if direction in ("sell", "short"):
        return -abs(q)
    if direction in ("buy", "long"):
        return abs(q)
    if q == 0:
        return 0.0
    raise ValueError("position direction missing; refusing to guess sign")


@dataclass
class RebalancePlan:
    current: float
    desired: float
    target: float
    delta: float
    amount: float = 0.0
    direction: str = ""
    reduce_only: bool = False
    intent: str = ""
    status: str = ""
    reason: str = ""


def plan_rebalance(current: float, desired: float, price: float, step: float,
                   minimum: float, rebalance_threshold: float = 1.0) -> RebalancePlan:
    if not all(math.isfinite(x) for x in (current, desired, price, step, minimum)) or price <= 0 or minimum <= 0:
        raise ValueError("invalid execution input")
    target = floor_amount(desired, step)
    if abs(target) + 1e-12 < minimum:
        target = 0.0
    p = RebalancePlan(current=current, desired=desired, target=target, delta=target-current)
    if abs(p.delta) < step * 1e-8:
        p.status = "no_signal" if target == 0 else "target_reached"
        p.reason = "No active target" if target == 0 else "Position already matches target"
        if desired != 0 and target == 0 and current == 0:
            p.status = "below_exchange_minimum"
            p.reason = "Allocation is smaller than one tradable lot; allocation is NOT rounded up"
        return p
    reversal = current * target < 0
    reduction = current != 0 and (target == 0 or reversal or abs(target) < abs(current))
    if reduction:
        # Reverse in TWO cycles: close and reconcile first, then consider opposite entry.
        raw_amount = abs(current) if target == 0 or reversal else abs(p.delta)
        p.amount = abs(floor_amount(raw_amount, step))
        p.direction = "sell" if current > 0 else "buy"
        p.reduce_only = True
        p.intent = "close" if target == 0 or reversal else "reduce"
    else:
        p.amount = abs(floor_amount(p.delta, step))
        p.direction = "buy" if p.delta > 0 else "sell"
        p.intent = "open" if current == 0 else "increase"
    if p.amount + 1e-12 < minimum:
        p.status = "below_exchange_minimum"
        p.reason = "Rebalance delta is smaller than exchange minimum"
    elif p.intent in ("increase", "reduce") and p.amount * price < max(0, rebalance_threshold):
        p.status = "skip_small"
        p.reason = "Rebalance hysteresis; no forced up-sizing"
    else:
        p.status = "planned"
        p.reason = "Reduce-only exit" if p.reduce_only else "Signal-backed entry"
    return p


def ioc_price(book: dict, direction: str, meta: dict, slippage_bps: float) -> float:
    """Marketable LIMIT IOC, with an explicit maximum slippage relative to top of book."""
    levels = book.get("asks" if direction == "buy" else "bids") or []
    if not levels or not levels[0] or float(levels[0][0]) <= 0:
        raise ValueError("no executable liquidity")
    reference = Decimal(str(levels[0][0]))
    sign = 1 if direction == "buy" else -1
    bound = reference * (Decimal(1) + sign * Decimal(str(slippage_bps)) / Decimal(10000))
    tick = Decimal(str(meta.get("tick_size") or 0))
    for item in sorted(meta.get("tick_size_steps") or [], key=lambda x: float(x.get("above_price", 0))):
        if float(bound) >= float(item.get("above_price", 0)):
            tick = Decimal(str(item["tick_size"]))
    if tick <= 0:
        raise ValueError("missing price tick size")
    # Round INSIDE the slippage bound, not beyond it.
    rounding = ROUND_DOWN if direction == "buy" else ROUND_UP
    price = (bound / tick).to_integral_value(rounding=rounding) * tick
    if direction == "buy" and book.get("max_price"):
        cap = Decimal(str(book["max_price"]))
        price = min(price, (cap/tick).to_integral_value(rounding=ROUND_DOWN)*tick)
    if direction == "sell" and book.get("min_price"):
        cap = Decimal(str(book["min_price"]))
        price = max(price, (cap/tick).to_integral_value(rounding=ROUND_UP)*tick)
    if price <= 0:
        raise ValueError("invalid IOC price")
    return float(price)


def fill_summary(result: dict) -> dict:
    """Order acknowledgement is NOT a fill. Only filled_amount/trades prove execution."""
    result = result or {}
    order = result.get("order") or {}
    trades = result.get("trades") or []
    amount = float(order.get("filled_amount") or 0)
    if not amount and trades:
        amount = sum(float(t.get("amount") or 0) for t in trades)
    average = float(order.get("average_price") or 0)
    if not average and amount and trades:
        average = sum(float(t.get("amount") or 0) * float(t.get("price") or 0) for t in trades) / amount
    return {
        "order_id": order.get("order_id"),
        "order_state": order.get("order_state") or "unknown",
        "filled_amount": amount,
        "average_price": average or None,
        "trade_ids": [t.get("trade_id") for t in trades if t.get("trade_id")],
        "fee": sum(float(t.get("fee") or 0) for t in trades),
        "fee_currencies": sorted({str(t.get("fee_currency")) for t in trades if t.get("fee_currency")}),
    }
