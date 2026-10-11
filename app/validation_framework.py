"""
v017 LAYER 7: Multi-Regime Validation Framework
Prevent repeating the incident - correct backtest methodology
"""
from __future__ import annotations

import logging
from typing import Dict, Tuple
import pandas as pd

log = logging.getLogger("validation")

def multi_regime_backtest(strategy_func, historical_data: pd.DataFrame, regime_windows: Dict[str, Tuple[str, str]]):
    """
    Instead of one backtest on whole history,
    test separately on:
    1. Strong uptrend (e.g., 2020-2021)
    2. Strong downtrend (e.g., 2022)
    3. Long ranging (e.g., mid 2023, mid 2019)
    Report results per regime separately
    """
    results = {}
    for regime_name, (start, end) in regime_windows.items():
        try:
            window_data = historical_data[(historical_data.index >= start) & (historical_data.index <= end)] if hasattr(historical_data, 'index') else historical_data
            if len(window_data) < 100:
                log.warning(f"Regime {regime_name} window too small {len(window_data)}")
                continue
            # Placeholder - actual backtest would call strategy
            # sharpe, max_dd, win_rate, total_return = run_backtest(strategy_func, window_data)
            # For now, just count
            results[regime_name] = {
                "n_candles": len(window_data),
                "start": start,
                "end": end,
                "note": "Implement run_backtest for real metrics"
            }
        except Exception as exc:
            log.warning(f"Regime {regime_name} backtest failed: {exc}")
            continue
    return results


def walk_forward_validation(strategy_func, data: pd.DataFrame, train_window=720, test_window=168, step=168):
    """
    Parameters (e.g., Donchian period) must be tested with walk-forward:
    - Find optimal param on train_window
    - Measure performance on test_window (strategy hasn't seen)
    - Move window forward and repeat
    Prevents overfitting like Almasi 504h
    """
    results = []
    start = 0
    total = len(data)
    while start + train_window + test_window <= total:
        train = data.iloc[start : start + train_window]
        test = data.iloc[start + train_window : start + train_window + test_window]
        # Optimize on train, test on test (out-of-sample)
        # params = optimize_on_window(strategy_func, train)
        # perf = evaluate_on_window(strategy_func, test, params)
        results.append({
            "train_start": start,
            "train_end": start + train_window,
            "test_start": start + train_window,
            "test_end": start + train_window + test_window,
            "train_len": len(train),
            "test_len": len(test),
        })
        start += step
    return results


def acceptance_criteria(results: Dict[str, dict]) -> bool:
    """
    Strategy only goes live if in all three regimes (up, down, range)
    Sharpe Ratio positive or near zero - not excellent in one and disaster in others
    """
    required_regimes = ["uptrend", "downtrend", "ranging"]
    for regime in required_regimes:
        if regime not in results:
            log.warning(f"Missing regime {regime} for acceptance")
            return False
        # Check Sharpe > -0.2 (near zero) for each regime
        sharpe = results[regime].get("sharpe", 0)
        if sharpe < -0.2:
            log.warning(f"Regime {regime} Sharpe {sharpe} < -0.2 fails acceptance")
            return False
    return True


# Example regime windows for crypto (adjust based on actual data)
EXAMPLE_REGIME_WINDOWS = {
    "uptrend_2020_2021": ("2020-10-01", "2021-11-30"),
    "downtrend_2022": ("2022-01-01", "2022-12-31"),
    "ranging_mid_2023": ("2023-04-01", "2023-09-30"),
    "ranging_mid_2019": ("2019-05-01", "2019-10-31"),
    "uptrend_2023_2024": ("2023-10-01", "2024-03-31"),
}
