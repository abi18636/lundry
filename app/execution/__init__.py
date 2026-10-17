"""
Execution Module - Modular exports
Fixes import for engine.py
"""
from __future__ import annotations
from app.execution.order_manager import maker_limit_price
# Re-use plan_rebalance from legacy execution.py file
try:
    from app.execution import plan_rebalance as _plan  # will fail, fallback
except:
    pass

# Import plan_rebalance from legacy file app/execution.py (old file)
import importlib.util
import pathlib
import sys

# Try to load legacy execution.py as module
legacy_path = pathlib.Path(__file__).parent.parent / "execution.py"
if legacy_path.exists():
    spec = importlib.util.spec_from_file_location("app.execution_legacy", legacy_path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        plan_rebalance = mod.plan_rebalance
    except Exception as e:
        # Fallback minimal implementation
        from dataclasses import dataclass
        import math
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
        def plan_rebalance(current: float, desired: float, price: float, contract_step: float, min_trade_amount: float, rebalance_threshold_usd: float = 15.0):
            if price <= 0:
                return PlanResult("no_price", "Price unavailable", desired, 0, 0, "buy", False, 0.0)
            delta = desired - current
            notional_delta = abs(delta) * price
            if notional_delta < rebalance_threshold_usd and abs(desired) > 1e-9:
                return PlanResult("skip_small", f"Delta ${notional_delta:.2f} < threshold ${rebalance_threshold_usd}", desired, delta, 0, "buy", False, price)
            direction = "buy" if delta > 0 else "sell"
            reduce_only = (current > 0 and direction == "sell") or (current < 0 and direction == "buy")
            steps = math.floor(abs(delta) / contract_step)
            amount = steps * contract_step
            if amount < min_trade_amount:
                if reduce_only:
                    amount = abs(current)
                    min_notional = 5.1
                    if amount * price < min_notional:
                        required = math.ceil(min_notional / price / contract_step) * contract_step
                        amount = max(amount, required)
                else:
                    return PlanResult("below_min", "Amount below lot", desired, delta, 0, direction, reduce_only, price)
            return PlanResult("planned", f"Valid {direction} {amount:.4f}", desired, delta, amount, direction, reduce_only, price)
else:
    # Fallback if legacy file missing
    from dataclasses import dataclass
    import math
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
    def plan_rebalance(current: float, desired: float, price: float, contract_step: float, min_trade_amount: float, rebalance_threshold_usd: float = 15.0):
        if price <= 0:
            return PlanResult("no_price", "Price unavailable", desired, 0, 0, "buy", False, 0.0)
        delta = desired - current
        notional_delta = abs(delta) * price
        if notional_delta < rebalance_threshold_usd and abs(desired) > 1e-9:
            return PlanResult("skip_small", f"Delta ${notional_delta:.2f} < threshold", desired, delta, 0, "buy", False, price)
        direction = "buy" if delta > 0 else "sell"
        reduce_only = (current > 0 and direction == "sell") or (current < 0 and direction == "buy")
        steps = math.floor(abs(delta) / contract_step)
        amount = steps * contract_step
        if amount < min_trade_amount:
            if reduce_only:
                amount = abs(current)
            else:
                return PlanResult("below_min", "Below min", desired, delta, 0, direction, reduce_only, price)
        return PlanResult("planned", f"Valid {direction}", desired, delta, amount, direction, reduce_only, price)

__all__ = ["maker_limit_price", "plan_rebalance"]
