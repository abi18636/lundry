"""
v016 COMPANY FIXES - AriaX Client
Fix #2 ROOT: Close position with live fetch + 0.999 safety + 95% retry, not 50% hack
Fix #1: Allow real close/reduce/reversal
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any, Dict, List, Optional

import httpx
import pandas as pd

log = logging.getLogger("ariax")


class AriaXAPIError(RuntimeError):
    def __init__(self, method: str, code: Any, message: str, data: Any = None):
        self.method, self.code, self.data = method, code, data
        super().__init__(f"{method}: {code} {message}")


class AriaXClient:
    def __init__(self, base_url: str, api_key: str, api_secret: str, timeout: float = 20.0, fallback_url: str = ""):
        self.base = base_url.rstrip("/")
        self.fallback = fallback_url.rstrip("/") if fallback_url else ""
        self.api_key = api_key
        self.api_secret = api_secret
        self._http = httpx.Client(timeout=timeout)
        self._instrument_cache: Dict[str, tuple] = {}

    def close(self):
        self._http.close()

    def _request(self, method: str, path: str, params: Optional[dict] = None, json_body: Optional[dict] = None, use_v5: bool = False, is_public: bool = False) -> Any:
        urls = [self.base]
        if self.fallback and self.fallback != self.base:
            urls.append(self.fallback)
        
        last_exc = None
        for base in urls:
            url = base + path
            try:
                if is_public:
                    if method == "GET":
                        resp = self._http.get(url, params=params)
                    else:
                        resp = self._http.request(method, url, params=params, json=json_body)
                elif use_v5:
                    import json as js
                    ts = str(int(time.time() * 1000))
                    recv = "10000"
                    if method == "GET":
                        query = "&".join(f"{k}={v}" for k, v in (params or {}).items())
                        payload = f"{ts}{self.api_key}{recv}{query}"
                        sig = hmac.new(self.api_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
                        headers = {
                            "X-BAPI-API-KEY": self.api_key,
                            "X-BAPI-TIMESTAMP": ts,
                            "X-BAPI-RECV-WINDOW": recv,
                            "X-BAPI-SIGNATURE": sig,
                        }
                        resp = self._http.get(url, params=params, headers=headers)
                    else:
                        body_str = js.dumps(json_body or {}, separators=(",", ":"))
                        payload = f"{ts}{self.api_key}{recv}{body_str}"
                        sig = hmac.new(self.api_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
                        headers = {
                            "X-BAPI-API-KEY": self.api_key,
                            "X-BAPI-TIMESTAMP": ts,
                            "X-BAPI-RECV-WINDOW": recv,
                            "X-BAPI-SIGNATURE": sig,
                            "Content-Type": "application/json"
                        }
                        resp = self._http.request(method, url, content=body_str.encode(), headers=headers)
                else:
                    headers = {"X-API-Key": self.api_key, "X-API-Secret": self.api_secret}
                    if method == "GET":
                        resp = self._http.get(url, params=params, headers=headers)
                    else:
                        resp = self._http.request(method, url, params=params, json=json_body, headers=headers)
                
                if resp.status_code == 404 and "no-server" in resp.headers.get("x-render-routing", ""):
                    last_exc = AriaXAPIError(path, 404, "no-server")
                    continue
                
                try:
                    data = resp.json()
                except:
                    if resp.status_code >= 400:
                        raise AriaXAPIError(path, resp.status_code, f"HTTP {resp.status_code}")
                    return {}
                
                if isinstance(data, dict) and "retCode" in data:
                    if data.get("retCode") != 0:
                        raise AriaXAPIError(path, data.get("retCode"), data.get("retMsg", "error"))
                    return data
                
                if isinstance(data, dict) and "ok" in data:
                    if not data.get("ok"):
                        raise AriaXAPIError(path, data.get("error") or -1, str(data))
                    return data
                
                return data
            
            except AriaXAPIError as exc:
                if exc.code in (404, "transport") or "no-server" in str(exc):
                    last_exc = exc
                    continue
                raise
            except httpx.HTTPError as exc:
                last_exc = AriaXAPIError(path, "transport", type(exc).__name__)
                continue
        
        if last_exc:
            raise last_exc
        raise AriaXAPIError(path, -1, "all endpoints failed")

    # Public
    def ticker(self, instrument: str) -> dict:
        try:
            data = self._request("GET", "/v5/market/tickers", params={"category": "linear", "symbol": instrument}, is_public=True)
            lst = data.get("result", {}).get("list", [])
            return lst[0] if lst else {}
        except:
            return {}

    def order_book(self, instrument: str, depth: int = 5) -> dict:
        try:
            limit = 25
            data = self._request("GET", "/v5/market/orderbook", params={"category": "linear", "symbol": instrument, "limit": limit}, is_public=True)
            res = data.get("result", {})
            bids = [[float(p), float(q)] for p, q in res.get("b", [])]
            asks = [[float(p), float(q)] for p, q in res.get("a", [])]
            ticker = self.ticker(instrument)
            mark = float(ticker.get("markPrice") or ticker.get("lastPrice") or 0)
            return {
                "bids": bids, "asks": asks,
                "timestamp": res.get("ts", int(time.time()*1000)),
                "mark_price": mark,
                "state": "open" if bids or asks else "unknown",
            }
        except Exception as exc:
            return {"bids": [], "asks": [], "timestamp": int(time.time()*1000), "mark_price": 0, "state": "unknown"}

    def instrument(self, instrument: str) -> dict:
        import time as tm
        now = tm.monotonic()
        cached = self._instrument_cache.get(instrument)
        if cached and now - cached[0] < 300:
            return dict(cached[1])
        
        try:
            data = self._request("GET", "/v5/market/instruments-info", params={"category": "linear", "symbol": instrument}, is_public=True)
            lst = data.get("result", {}).get("list", [])
            if not lst:
                result = {"min_trade_amount": 0.001, "contract_size": 0.001, "tick_size": 0.01, "min_notional": 5.0}
            else:
                info = lst[0]
                lot = info.get("lotSizeFilter", {})
                price_filter = info.get("priceFilter", {})
                result = {
                    "min_trade_amount": float(lot.get("minOrderQty") or 0.001),
                    "contract_size": float(lot.get("qtyStep") or 0.001),
                    "tick_size": float(price_filter.get("tickSize") or 0.01),
                    "min_notional": float(lot.get("minNotionalValue") or 5.0),
                }
        except:
            result = {"min_trade_amount": 0.001, "contract_size": 0.001, "tick_size": 0.01, "min_notional": 5.0}
        
        self._instrument_cache[instrument] = (now, result)
        return dict(result)

    def candles(self, instrument: str, hours: int = 1000, resolution: str = "60") -> pd.DataFrame:
        try:
            import time as tm
            all_rows = []
            end_ms = int(tm.time() * 1000)
            remaining = max(48, hours)
            
            for _ in range(3):
                if remaining <= 0:
                    break
                limit = min(1000, remaining)
                try:
                    data = self._request("GET", "/v5/market/kline", params={
                        "category": "linear", "symbol": instrument, "interval": "60", "limit": limit, "end": end_ms
                    }, is_public=True)
                    rows = data.get("result", {}).get("list", [])
                    if not rows:
                        break
                    all_rows.extend(rows)
                    oldest_ts = min(int(r[0]) for r in rows)
                    end_ms = oldest_ts - 1
                    remaining -= len(rows)
                    if len(rows) < limit:
                        break
                    tm.sleep(0.1)
                except:
                    break
            
            if not all_rows:
                return pd.DataFrame()
            
            df = pd.DataFrame(all_rows, columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
            df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df["dt"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df = df.sort_values("dt").reset_index(drop=True)
            return df[["dt", "open", "high", "low", "close", "volume"]]
        except Exception as exc:
            log.warning(f"candles failed for {instrument}: {exc}")
            return pd.DataFrame()

    # Private
    def account_summary(self, currency: str = "USDT") -> dict:
        try:
            data = self._request("GET", "/api/wallet", use_v5=False)
            if data.get("ok"):
                d = data.get("data", {})
                return {
                    "USDT": {
                        "equity": float(d.get("equity") or d.get("balance") or 0),
                        "available": float(d.get("available") or d.get("free") or 0),
                        "balance": float(d.get("balance") or 0),
                    }
                }
            return {"USDT": {"equity": 0, "available": 0, "balance": 0}}
        except:
            return {"USDT": {"equity": 0, "available": 0, "balance": 0}}

    def positions(self, currency: str = "USDT") -> List[dict]:
        try:
            data = self._request("GET", "/api/positions", use_v5=False)
            result = []
            if data.get("ok"):
                for p in data.get("data", []):
                    result.append({
                        "instrument_name": p.get("symbol", "").replace("USD", "USDT") if p.get("symbol","").endswith("USD") else p.get("symbol"),
                        "size": float(p.get("size") or 0),
                        "size_currency": float(p.get("size_currency") or p.get("qty") or 0),
                        "direction": p.get("direction") or ("buy" if float(p.get("size") or 0)>0 else "sell"),
                        "average_price": float(p.get("avg_price") or p.get("entry_price") or 0),
                        "mark_price": float(p.get("mark_price") or 0),
                        "floating_profit_loss": float(p.get("upnl") or p.get("pnl") or 0),
                    })
            return result
        except:
            return []

    def open_orders(self, currency: str = "USDT") -> List[dict]:
        return []

    def orders_by_label(self, label: str) -> List[dict]:
        return []

    def cancel_order(self, order_id: str) -> dict:
        try:
            data = self._request("POST", "/api/cancel", json_body={"id": order_id}, use_v5=False)
            return data
        except:
            return {}

    # Trading - v016 FIX: No 50% hack, real floor with 0.999 safety
    def _place_order(self, symbol: str, side: str, qty: float, order_type: str = "market", price: Optional[float] = None, reduce_only: bool = False, label: str = "") -> dict:
        # v016 FIX: No 50% forced reduction - use real qty with floor
        # Safety: 0.999 already applied in execution.py plan_rebalance
        body = {
            "symbol": symbol.replace("USDT", "USD") if symbol.endswith("USDT") else symbol,
            "side": side,
            "type": order_type,
            "qty": qty,
            "lev": 5
        }
        if price is not None and order_type == "limit":
            body["price"] = price
        
        data = self._request("POST", "/api/order", json_body=body, use_v5=False)
        return data

    def limit_ioc(self, instrument: str, direction: str, amount: float, price: float, label: str, reduce_only: bool = False) -> dict:
        legacy_sym = instrument.replace("USDT", "USD") if instrument.endswith("USDT") else instrument
        try:
            return self._place_order(legacy_sym, direction, amount, "limit", price, reduce_only=reduce_only, label=label)
        except AriaXAPIError as exc:
            if "qty exceeds" in str(exc).lower() and reduce_only:
                log.warning(f"limit_ioc qty exceeds with reduceOnly, retrying without flag for {instrument}")
                return self._place_order(legacy_sym, direction, amount, "limit", price, reduce_only=False, label=label)
            raise

    def buy_market(self, instrument: str, amount: float, label: str = "zenith", reduce_only: bool = False) -> dict:
        legacy_sym = instrument.replace("USDT", "USD") if instrument.endswith("USDT") else instrument
        try:
            return self._place_order(legacy_sym, "buy", amount, "market", reduce_only=reduce_only, label=label)
        except AriaXAPIError as exc:
            if "qty exceeds" in str(exc).lower() and reduce_only:
                return self._place_order(legacy_sym, "buy", amount, "market", reduce_only=False, label=label)
            raise

    def sell_market(self, instrument: str, amount: float, label: str = "zenith", reduce_only: bool = False) -> dict:
        legacy_sym = instrument.replace("USDT", "USD") if instrument.endswith("USDT") else instrument
        try:
            return self._place_order(legacy_sym, "sell", amount, "market", reduce_only=reduce_only, label=label)
        except AriaXAPIError as exc:
            if "qty exceeds" in str(exc).lower() and reduce_only:
                return self._place_order(legacy_sym, "sell", amount, "market", reduce_only=False, label=label)
            raise

    def close_position(self, instrument: str) -> dict:
        """
        v016 FIX ROOT: Always fetch LIVE position from exchange (not cached state)
        and close with exact floored size + 0.999 safety, not 50% hack.
        Retry gradually 95% if qty exceeds.
        """
        try:
            # Step 1: LIVE fetch (not cached)
            pos = self.positions()
            target_pos = None
            for p in pos:
                if p.get("instrument_name") == instrument:
                    target_pos = p
                    break
            
            if not target_pos:
                raise AriaXAPIError("close_position", -1, "position not found")
            
            size = float(target_pos.get("size_currency") or target_pos.get("size") or 0)
            direction = target_pos.get("direction", "")
            if abs(size) <= 0:
                return {"ok": True, "message": "no position to close"}
            
            # Step 2: Get step size and floor with 0.999 safety (never round up)
            meta = self.instrument(instrument)
            step = float(meta.get("contract_size") or 0.001)
            
            from app.execution_v016 import floor_amount
            safe_size = floor_amount(abs(size) * 0.999, step)
            
            if safe_size <= 0:
                return {"ok": True, "message": "position below minimum, already effectively closed"}
            
            # Step 3: Try full close (not 50%)
            try:
                if direction.lower() in ("buy", "long"):
                    return self.sell_market(instrument, safe_size, label=f"close_{instrument.lower()}", reduce_only=True)
                else:
                    return self.buy_market(instrument, safe_size, label=f"close_{instrument.lower()}", reduce_only=True)
            except AriaXAPIError as exc:
                if "qty exceeds" in str(exc).lower():
                    # Step 4: Real fallback - gradual 95% retry, not random 50%
                    retry_size = safe_size
                    for attempt in range(5):
                        retry_size = floor_amount(retry_size * 0.95, step)
                        if retry_size <= 0:
                            break
                        try:
                            if direction.lower() in ("buy", "long"):
                                return self.sell_market(instrument, retry_size, label=f"close_retry_{instrument.lower()}", reduce_only=True)
                            else:
                                return self.buy_market(instrument, retry_size, label=f"close_retry_{instrument.lower()}", reduce_only=True)
                        except AriaXAPIError:
                            continue
                    raise AriaXAPIError("close_position", -1, f"failed to close after 5 retries, size={safe_size}")
                raise
        except Exception as exc:
            if isinstance(exc, AriaXAPIError):
                raise
            raise AriaXAPIError("close_position", -1, str(exc))
