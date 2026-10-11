"""
v017 LAYER 5: Meta-Allocator - Dynamic weights based on live performance
Most advanced layer - self-adapting system
Weight based on rolling Sharpe and win rate
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Dict
import statistics

log = logging.getLogger("meta_allocator")

class PerformanceTracker:
    """Track rolling Sharpe Ratio of each sleeve in live market"""
    
    def __init__(self, lookback_trades=20):
        self.lookback = lookback_trades
        self.trade_history = {}  # sleeve_id -> deque of pnl_pct
        self.trade_count = {}  # sleeve_id -> count
    
    def record_trade_result(self, sleeve_id: str, pnl_pct: float):
        if sleeve_id not in self.trade_history:
            self.trade_history[sleeve_id] = deque(maxlen=self.lookback)
            self.trade_count[sleeve_id] = 0
        self.trade_history[sleeve_id].append(pnl_pct)
        self.trade_count[sleeve_id] += 1
        log.info(f"MetaAllocator: {sleeve_id} pnl {pnl_pct*100:.2f}% history {len(self.trade_history[sleeve_id])}/{self.lookback}")
    
    def rolling_sharpe(self, sleeve_id: str) -> float:
        history = self.trade_history.get(sleeve_id, [])
        if len(history) < 5:
            return 0.0  # Not enough data, neutral
        try:
            mean_ret = statistics.mean(history)
            std_ret = statistics.stdev(history) if len(history) > 1 else 1e-6
            if std_ret < 1e-9:
                return 0.0
            return mean_ret / std_ret
        except:
            return 0.0
    
    def rolling_win_rate(self, sleeve_id: str) -> float:
        history = self.trade_history.get(sleeve_id, [])
        if not history:
            return 0.5
        wins = sum(1 for p in history if p > 0)
        return wins / len(history)
    
    def avg_pnl(self, sleeve_id: str) -> float:
        history = self.trade_history.get(sleeve_id, [])
        if not history:
            return 0.0
        return statistics.mean(history)
    
    def get_stats(self, sleeve_id: str) -> dict:
        return {
            "sharpe": self.rolling_sharpe(sleeve_id),
            "win_rate": self.rolling_win_rate(sleeve_id),
            "avg_pnl": self.avg_pnl(sleeve_id),
            "count": self.trade_count.get(sleeve_id, 0),
            "history_len": len(self.trade_history.get(sleeve_id, [])),
        }
    
    def get_all_stats(self) -> Dict[str, dict]:
        return {sid: self.get_stats(sid) for sid in self.trade_history.keys()}


def compute_dynamic_weights(base_weights: dict, tracker: PerformanceTracker, 
                              min_weight=0.03, max_weight=0.35):
    """
    Adjust weight of each strategy based on its recent rolling Sharpe
    Strategies that performed well recently (e.g., in current ranging) get more weight,
    strategies that keep losing (trend-following in ranging) go to sidelines
    """
    adjusted = {}
    for sid, base_w in base_weights.items():
        sharpe = tracker.rolling_sharpe(sid)
        win_rate = tracker.rolling_win_rate(sid)
        
        # Adjustment factor: combine Sharpe and win rate, limited to logical range
        # Sharpe 1.0 -> +30%, win_rate 70% -> +8%, total +38% max
        # Sharpe -1.0 -> -30%, win_rate 30% -> -8%, total -38% min, but capped at -60% to +60%
        perf_score = sharpe * 0.3 + (win_rate - 0.5) * 0.4
        perf_score = max(-0.6, min(0.6, perf_score))
        performance_multiplier = 1.0 + perf_score
        
        adjusted[sid] = base_w * performance_multiplier
        log.info(f"MetaAllocator {sid}: base {base_w:.3f} sharpe {sharpe:.2f} win {win_rate:.2f} mult {performance_multiplier:.2f} -> {adjusted[sid]:.3f}")
    
    # Normalize and apply min/max
    total = sum(adjusted.values()) or 1.0
    adjusted = {k: v / total for k, v in adjusted.items()}
    # Apply min/max bounds
    adjusted = {k: max(min_weight, min(max_weight, v)) for k, v in adjusted.items()}
    total2 = sum(adjusted.values()) or 1.0
    final = {k: v / total2 for k, v in adjusted.items()}
    
    log.info(f"MetaAllocator final weights: {final}")
    return final


def compute_dynamic_weights_simple(base_weights: dict, regime: str):
    """
    Simple regime-based dynamic weights if no performance history yet
    """
    if regime == "ranging":
        # In ranging, reduce trend, increase mean-reversion
        adjusted = {}
        for sid, w in base_weights.items():
            if sid == "mean_reversion":
                adjusted[sid] = w * 1.5  # Boost mean reversion
            elif sid in ["zenith_apex", "almasi_primary", "zenith_endurance"]:
                adjusted[sid] = w * 0.5  # Reduce pure trend
            else:
                adjusted[sid] = w
    elif regime == "trending":
        # In trending, boost trend
        adjusted = {}
        for sid, w in base_weights.items():
            if sid == "mean_reversion":
                adjusted[sid] = w * 0.3  # Reduce mean reversion
            elif sid in ["zenith_apex", "almasi_primary"]:
                adjusted[sid] = w * 1.3  # Boost trend
            else:
                adjusted[sid] = w
    else:
        adjusted = base_weights.copy()
    
    total = sum(adjusted.values()) or 1.0
    return {k: v/total for k, v in adjusted.items()}
