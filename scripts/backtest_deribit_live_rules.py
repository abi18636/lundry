#!/usr/bin/env python3
"""
Deribit Live-Rules Backtest — 7 liquid assets, real Deribit candles, live execution filters.

Implements proposals from SUPER_SPECIALIST_GROUP_ANALYSIS_FA.md:
- Only 7 liquid assets: BTC,ETH,SOL,DOGE,AVAX,APT,TRX
- Data source: public/get_tradingview_chart_data 2500h (Deribit official)
- Execution rules: floor_amount, min_trade_amount, contract_size, spread filter 200bps, slippage 100bps, rebalance 0.5 USD
- Cost model: taker 5bp + half spread (estimated from live spread ~20bps => 10bps half => 15bps total)
- Walk-forward to 2026-10-02
- Separate P&L tracking with funding placeholder

Usage:
  python scripts/backtest_deribit_live_rules.py --capital 400 --out results/deribit_7_liquid_backtest.json
"""
from __future__ import annotations
import argparse
import json
import math
import sys
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, List
import httpx
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "strategies"))

from app.execution import floor_amount, plan_rebalance
from app.sleeves import prepare_frames, run_all_sleeves, parse_weights

ASSETS = ["BTC", "ETH", "SOL", "DOGE", "AVAX", "APT", "TRX"]
DERIBIT_BASE = "https://www.deribit.com/api/v2"
# Also try testnet for instrument metadata, but mainnet has same specs
DERIBIT_TEST = "https://test.deribit.com/api/v2"

# Execution tuning from group proposal
MAX_SPREAD_BPS = 200.0
MAX_SLIPPAGE_BPS = 100.0
REBALANCE_NOTIONAL_USD = 0.5
TAKER_FEE_BPS = 5.0  # 0.05%
HALF_SPREAD_BPS_EST = 10.0  # estimated half spread for liquid assets
TOTAL_COST_BPS = TAKER_FEE_BPS + HALF_SPREAD_BPS_EST  # 15 bps

def fetch_tradingview_candles(instrument: str, hours: int = 2500, resolution: str = "60") -> pd.DataFrame:
    """Fetch candles via public/get_tradingview_chart_data"""
    end = int(time.time() * 1000)
    start = end - hours * 3600 * 1000
    url = f"{DERIBIT_BASE}/public/get_tradingview_chart_data"
    params = {
        "instrument_name": instrument,
        "start_timestamp": start,
        "end_timestamp": end,
        "resolution": resolution,
    }
    print(f"Fetching {instrument} {hours}h via {url} ...")
    r = httpx.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    if "result" not in data:
        raise RuntimeError(f"Bad response for {instrument}: {data}")
    result = data["result"]
    # result contains ticks, open, high, low, close, volume, cost?
    ticks = result.get("ticks") or []
    opens = result.get("open") or []
    highs = result.get("high") or []
    lows = result.get("low") or []
    closes = result.get("close") or []
    volumes = result.get("volume") or []
    if not ticks:
        raise RuntimeError(f"No ticks for {instrument}")
    # ticks are ms timestamps
    dts = [datetime.fromtimestamp(t/1000, tz=timezone.utc) for t in ticks]
    df = pd.DataFrame({
        "dt": dts,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })
    # Filter zero volume? Keep all, but mark
    df = df.sort_values("dt").reset_index(drop=True)
    print(f"  -> {len(df)} candles, last={df['dt'].iloc[-1]} close={df['close'].iloc[-1]}")
    return df

def fetch_instruments_metadata() -> Dict[str, dict]:
    """Fetch instrument metadata for min_trade_amount, contract_size, tick_size"""
    url = f"{DERIBIT_BASE}/public/get_instruments"
    params = {"currency": "USDC", "kind": "future", "expired": "false"}
    r = httpx.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    instruments = data.get("result") or []
    meta = {}
    for inst in instruments:
        name = inst.get("instrument_name")
        if not name:
            continue
        # Only keep USDC-PERPETUAL
        if not name.endswith("_USDC-PERPETUAL"):
            continue
        asset = name.split("_")[0]
        if asset in ASSETS:
            meta[asset] = {
                "instrument_name": name,
                "min_trade_amount": float(inst.get("min_trade_amount") or inst.get("contract_size") or 1.0),
                "contract_size": float(inst.get("contract_size") or 1.0),
                "tick_size": float(inst.get("tick_size") or 0.01),
            }
    print(f"Fetched metadata for {len(meta)} assets: {list(meta.keys())}")
    return meta

def simulate_walk_forward(frames_full: Dict[str, pd.DataFrame], meta: Dict[str, dict], capital: float, weights_str: str, step_hours: int = 6):
    """
    Walk-forward simulation using live execution rules.
    At each step_hours bar, run sleeves on data up to that bar, compute target, apply plan_rebalance with floor/min, cost model.
    """
    # Align all frames to common timeline
    master = frames_full.get("BTC")
    if master is None:
        master = list(frames_full.values())[0]
    timeline = master["dt"].tolist()
    # Require at least 800 bars for sleeves (max lookback 726)
    min_bars = 800
    if len(timeline) < min_bars:
        raise RuntimeError(f"Not enough bars: {len(timeline)} < {min_bars}")

    # Weights
    weights = parse_weights(weights_str)
    print(f"Weights: {weights} step_hours={step_hours}")

    # State: positions per asset (coin quantity)
    positions = {a: 0.0 for a in ASSETS}
    avg_prices = {a: None for a in ASSETS}
    cash = capital
    equity_curve = []
    trades = []
    gross_pnl = 0.0
    total_fees = 0.0
    total_spread_cost = 0.0

    # For cost: total cost bps applied on notional
    cost_rate = TOTAL_COST_BPS / 10000.0

    # Simulate from min_bars to end, stepping
    for idx in range(min_bars, len(timeline), step_hours):
        current_dt = timeline[idx]
        # Build ohlc up to idx (inclusive)
        ohlc_slice = {}
        for a in ASSETS:
            if a not in frames_full:
                continue
            df = frames_full[a]
            # Slice df where dt <= current_dt
            # Since df may have different lengths, find position of current_dt
            # Use search: df dt <= current_dt
            sliced = df[df["dt"] <= current_dt]
            if len(sliced) < 300:
                continue
            ohlc_slice[a] = sliced

        if len(ohlc_slice) < 1:
            continue

        frames = prepare_frames(ohlc_slice, assets=list(ohlc_slice.keys()))
        if not frames:
            continue

        # Run sleeves
        try:
            results, net_book = run_all_sleeves(frames, total_capital=capital, weights=weights, lev_cap=1.0, enabled=list(weights.keys()))
        except Exception as e:
            print(f"  Sleeves error at {current_dt}: {e}")
            continue

        # For each asset, apply execution rules
        for a in ASSETS:
            if a not in net_book:
                continue
            b = net_book[a]
            desired_coin = float(b.get("target_coin") or 0.0)
            price = float(b.get("price") or 0.0)
            if price <= 0:
                continue
            current = positions.get(a, 0.0)
            # Metadata
            m = meta.get(a) or {"min_trade_amount": 1.0, "contract_size": 1.0}
            step = float(m.get("contract_size") or 1.0)
            minimum = float(m.get("min_trade_amount") or step)

            # Adjust for long_only (as in engine)
            desired = max(0.0, desired_coin)

            try:
                plan = plan_rebalance(current, desired, price, step, minimum, REBALANCE_NOTIONAL_USD)
            except Exception as e:
                continue

            if plan.status != "planned":
                # No trade
                continue

            # Spread filter: we don't have historical spread, assume 20 bps for liquid assets => passes 200 bps filter
            # So always pass for backtest (conservative: liquid assets)
            spread_bps = 20.0  # assumed
            if spread_bps > MAX_SPREAD_BPS:
                continue

            # Slippage: depth-5 VWAP vs top, assume slippage within 100 bps passes
            # Simulate execution price with slippage: buy at price * (1 + slippage), sell at price * (1 - slippage)
            # Use half of max_slippage as realized slippage (50 bps)
            realized_slippage_bps = MAX_SLIPPAGE_BPS * 0.5  # 50 bps
            exec_price = price * (1 + realized_slippage_bps/10000.0) if plan.direction == "buy" else price * (1 - realized_slippage_bps/10000.0)

            # Floor already done in plan_rebalance
            amount = plan.amount
            if amount <= 0:
                continue

            # Execute
            notional = amount * exec_price
            fee = notional * cost_rate  # includes taker + half spread
            # Update position avg
            if plan.direction == "buy":
                # Increase
                if positions[a] == 0:
                    avg_prices[a] = exec_price
                else:
                    # Weighted avg
                    old_q = positions[a]
                    old_avg = avg_prices[a] or exec_price
                    new_q = old_q + amount
                    if new_q != 0:
                        avg_prices[a] = (old_q * old_avg + amount * exec_price) / new_q
                positions[a] += amount
                cash -= notional + fee
            else:
                # Sell / close
                if positions[a] > 0 and avg_prices[a] is not None:
                    # Realized PnL
                    pnl = amount * (exec_price - avg_prices[a])
                    gross_pnl += pnl
                    trades.append({
                        "dt": str(current_dt),
                        "asset": a,
                        "side": "sell",
                        "amount": amount,
                        "entry_price": avg_prices[a],
                        "exit_price": exec_price,
                        "gross_pnl": pnl,
                        "fee": fee,
                        "net_pnl": pnl - fee,
                        "return_pct": (exec_price / avg_prices[a] - 1) * 100 if avg_prices[a] else 0,
                    })
                positions[a] -= amount
                if positions[a] <= 1e-9:
                    positions[a] = 0.0
                    avg_prices[a] = None
                cash += notional - fee

            total_fees += fee
            # For reporting, spread cost part is half spread
            total_spread_cost += notional * (HALF_SPREAD_BPS_EST / 10000.0)

        # Equity = cash + sum(pos * price)
        pos_value = 0.0
        unrealized = 0.0
        for a in ASSETS:
            if a in net_book and positions[a] != 0:
                mp = float(net_book[a].get("price") or 0)
                pos_value += positions[a] * mp
                if avg_prices[a] is not None:
                    unrealized += positions[a] * (mp - avg_prices[a])

        equity = cash + pos_value
        equity_curve.append({
            "dt": str(current_dt),
            "equity": equity,
            "cash": cash,
            "pos_value": pos_value,
            "unrealized": unrealized,
            "gross_pnl_cum": gross_pnl,
            "fees_cum": total_fees,
        })

    # Final stats
    if not equity_curve:
        raise RuntimeError("No equity curve generated")

    start_eq = equity_curve[0]["equity"]
    end_eq = equity_curve[-1]["equity"]
    total_return = (end_eq / capital - 1) * 100 if capital else 0

    # Max drawdown
    peak = -float("inf")
    max_dd = 0.0
    for pt in equity_curve:
        eq = pt["equity"]
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    # Win rate
    wins = sum(1 for t in trades if t["net_pnl"] > 0)
    losses = sum(1 for t in trades if t["net_pnl"] <= 0)
    win_rate = wins / len(trades) * 100 if trades else 0
    total_net = sum(t["net_pnl"] for t in trades) + (equity_curve[-1]["unrealized"] if equity_curve else 0)
    pf = sum(t["net_pnl"] for t in trades if t["net_pnl"] > 0) / abs(sum(t["net_pnl"] for t in trades if t["net_pnl"] < 0)) if any(t["net_pnl"] < 0 for t in trades) else float("inf")

    result = {
        "meta": {
            "assets": ASSETS,
            "capital": capital,
            "weights": weights,
            "hours": len(timeline),
            "start_dt": str(timeline[min_bars]),
            "end_dt": str(timeline[-1]),
            "execution": {
                "max_spread_bps": MAX_SPREAD_BPS,
                "max_slippage_bps": MAX_SLIPPAGE_BPS,
                "rebalance_notional_usd": REBALANCE_NOTIONAL_USD,
                "cost_model": f"taker {TAKER_FEE_BPS}bps + half_spread {HALF_SPREAD_BPS_EST}bps = {TOTAL_COST_BPS}bps",
                "floor": "contract_size",
                "min_trade": "exchange min_trade_amount",
                "depth": "5 VWAP",
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "performance": {
            "start_equity": start_eq,
            "end_equity": end_eq,
            "total_return_pct": total_return,
            "max_drawdown_pct": max_dd * 100,
            "num_trades": len(trades),
            "wins": wins,
            "losses": losses,
            "win_rate_pct": win_rate,
            "profit_factor": pf if math.isfinite(pf) else None,
            "gross_pnl": gross_pnl,
            "total_fees": total_fees,
            "total_spread_cost_est": total_spread_cost,
            "net_pnl": total_net,
            "final_positions": positions,
        },
        "equity_curve": equity_curve[-500:],  # last 500 points for chart
        "trades": trades[-200:],  # last 200 trades
        "notes": "Backtest with Deribit real candles + live execution rules (floor, min, spread, slippage, cost). Spread assumed 20bps liquid, passes 200bps filter. Slippage realized 50bps within 100bps bound. No funding in backtest (added in live PnL).",
    }
    return result

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=400.0)
    ap.add_argument("--hours", type=int, default=2500)
    ap.add_argument("--step", type=int, default=6, help="Walk-forward step in hours (6=every 6h)")
    ap.add_argument("--out", type=str, default="results/deribit_7_liquid_backtest.json")
    ap.add_argument("--weights", type=str, default="zenith_apex:0.25,almasi_primary:0.25,inst_v3_stable:0.30,inst_v3_primary:0.10,zenith_endurance:0.10")
    args = ap.parse_args()

    print(f"=== Deribit Live-Rules Backtest ===")
    print(f"Capital: {args.capital}, Hours: {args.hours}, Step: {args.step}h, Assets: {ASSETS}")

    meta = fetch_instruments_metadata()
    # Ensure all assets have meta, fallback to defaults if missing
    for a in ASSETS:
        if a not in meta:
            print(f"  Warning: no meta for {a}, using defaults")
            meta[a] = {"min_trade_amount": 1.0, "contract_size": 1.0, "tick_size": 0.01, "instrument_name": f"{a}_USDC-PERPETUAL"}

    frames_full = {}
    for a in ASSETS:
        inst = meta[a]["instrument_name"]
        try:
            df = fetch_tradingview_candles(inst, hours=args.hours, resolution="60")
            frames_full[a] = df
            time.sleep(0.2)  # rate limit
        except Exception as e:
            print(f"  Failed to fetch {a}: {e}")
            continue

    if not frames_full:
        print("No data fetched, abort")
        sys.exit(1)

    result = simulate_walk_forward(frames_full, meta, capital=args.capital, weights_str=args.weights, step_hours=args.step)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n=== Results ===")
    perf = result["performance"]
    print(f"Return: {perf['total_return_pct']:.2f}%  DD: {perf['max_drawdown_pct']:.2f}%  Trades: {perf['num_trades']}  Win: {perf['win_rate_pct']:.1f}% PF: {perf['profit_factor']}")
    print(f"Gross PnL: {perf['gross_pnl']:.4f}  Fees: {perf['total_fees']:.4f}  Net: {perf['net_pnl']:.4f}")
    print(f"Final equity: {perf['end_equity']:.2f} from {perf['start_equity']:.2f}")
    print(f"Saved to {out_path}")

if __name__ == "__main__":
    main()
