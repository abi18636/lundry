"""
Execution Module - Order Manager
Independent: Manages order lifecycle, prevents accumulation
Fix: Cancel all >10 orders
"""
from __future__ import annotations
import logging
import httpx

log = logging.getLogger("order_manager")

def cancel_accumulated_orders(base_url: str, api_key: str, api_secret: str, threshold: int = 10) -> int:
    """Cancel all if > threshold to prevent 67 orders issue"""
    try:
        headers = {"X-API-Key": api_key, "X-API-Secret": api_secret}
        r = httpx.get(f"{base_url}/api/orders", headers=headers, timeout=5)
        orders = r.json().get("data", []) if r.status_code == 200 else []
        if len(orders) > threshold:
            log.info(f"Found {len(orders)} orders, canceling all")
            for o in orders:
                try:
                    httpx.post(f"{base_url}/api/cancel", headers=headers, json={"id": o.get("id")}, timeout=5)
                except:
                    pass
            return len(orders)
        return 0
    except Exception as e:
        log.warning(f"Cancel all failed: {e}")
        return 0

def maker_limit_price(book: dict, direction: str) -> float:
    """Fix1: Maker Post-Only Best Bid/Ask"""
    try:
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        if direction == "buy":
            # Buy at best bid (maker)
            return float(bids[0][0]) if bids else 0.0
        else:
            # Sell at best ask (maker)
            return float(asks[0][0]) if asks else 0.0
    except:
        return 0.0
