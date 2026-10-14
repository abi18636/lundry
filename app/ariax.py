"""
AriaX client - FIXED31 FINAL - robust wallet and positions parsing
"""
from __future__ import annotations
import time
import logging
from typing import List, Optional, Dict
import httpx
import pandas as pd

log = logging.getLogger("ariax")

class AriaXAPIError(Exception):
    def __init__(self, path: str, code, msg: str):
        self.path = path
        self.code = code
        super().__init__(f"{path} {code}: {msg}")

class AriaXClient:
    def __init__(self, base_url: str, api_key: str, api_secret: str, fallback_url: Optional[str] = None):
        self.base_url = (base_url or "").rstrip("/")
        self.fallback_url = (fallback_url or self.base_url).rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self._http = httpx.Client(timeout=15.0)
        self._instrument_cache: Dict[str, tuple] = {}

    def _request(self, method: str, path: str, params: Optional[dict] = None, json_body: Optional[dict] = None, use_v5: bool = False, is_public: bool = False):
        urls = [self.base_url, self.fallback_url] if self.fallback_url != self.base_url else [self.base_url]
        last_exc = None
        for base in urls:
            url = f"{base}{path}"
            try:
                if is_public:
                    if method == "GET":
                        resp = self._http.get(url, params=params)
                    else:
                        resp = self._http.request(method, url, params=params, json=json_body)
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

    def account_summary(self, currency: str = "USDT") -> dict:
        try:
            data = self._request("GET", "/api/wallet", use_v5=False)
        except Exception as e:
            log.warning(f"account_summary _request failed: {e}")
            return {currency: {"equity": 0, "available": 0, "balance": 0}}
        
        try:
            if not isinstance(data, dict):
                return {currency: {"equity": 0, "available": 0, "balance": 0}}
            
            # Try equity directly
            equity = data.get("equity")
            if equity is not None:
                try:
                    eq = float(equity)
                    if eq > 0:
                        free = float(data.get("free_margin") or 0)
                        fut_bal = 0
                        try:
                            fb = data.get("futures", {}).get("balances", {})
                            if isinstance(fb, dict):
                                fut_bal = float(fb.get("USDT") or 0)
                        except:
                            pass
                        available = free if free > 0 else fut_bal if fut_bal > 0 else eq
                        balance = fut_bal if fut_bal > 0 else eq
                        return {currency: {"equity": eq, "available": available, "balance": balance}}
                except:
                    pass
            
            # Fallback futures
            try:
                fb = data.get("futures", {}).get("balances", {})
                if isinstance(fb, dict):
                    fut = float(fb.get("USDT") or 0)
                    if fut > 0:
                        return {currency: {"equity": fut, "available": fut, "balance": fut}}
            except:
                pass
            
            # Fallback spot
            try:
                sb = data.get("balances", {})
                if isinstance(sb, dict):
                    spot = float(sb.get("USDT") or 0)
                    if spot > 0:
                        return {currency: {"equity": spot, "available": spot, "balance": spot}}
            except:
                pass
            
            return {currency: {"equity": 0, "available": 0, "balance": 0}}
        except Exception as e:
            log.warning(f"account_summary parse failed: {e}")
            return {currency: {"equity": 0, "available": 0, "balance": 0}}

    def positions(self, currency: str = "USDT") -> List[dict]:
        try:
            data = self._request("GET", "/api/positions", use_v5=False)
        except Exception as e:
            log.warning(f"positions _request failed: {e}")
            return []
        
        try:
            raw_positions = []
            if isinstance(data, dict) and "data" in data:
                raw_positions = data.get("data", [])
            elif isinstance(data, list):
                raw_positions = data
            else:
                return []
            
            normalized = []
            for p in raw_positions:
                try:
                    symbol = p.get("symbol") or p.get("instrument_name") or ""
                    asset = symbol.replace("USDT", "").replace("USD", "")
                    instrument = asset + "USDT" if symbol.endswith("USD") and not symbol.endswith("USDT") else symbol
                    if not instrument.endswith("USDT"):
                        instrument = asset + "USDT"
                    
                    size = float(p.get("size") or 0)
                    entry = float(p.get("entry") or p.get("average_price") or 0)
                    mark = float(p.get("mark") or p.get("mark_price") or 0)
                    upnl = float(p.get("upnl") or p.get("total_profit_loss") or 0)
                    lev = p.get("lev") or p.get("leverage") or 5
                    direction = "sell" if size < 0 else "buy" if size > 0 else ""
                    
                    normalized.append({
                        "instrument_name": instrument,
                        "symbol": symbol,
                        "size": size,
                        "size_currency": size,
                        "direction": direction,
                        "average_price": entry,
                        "mark_price": mark,
                        "total_profit_loss": upnl,
                        "floating_profit_loss": upnl,
                        "leverage": lev,
                        "entry": entry,
                        "mark": mark,
                        "upnl": upnl,
                        "margin": p.get("margin"),
                        "liq": p.get("liq"),
                        "sl": p.get("sl"),
                        "tp": p.get("tp"),
                    })
                except Exception as e:
                    continue
            
            return normalized
        except Exception as e:
            log.warning(f"positions parse failed: {e}")
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


    def close_position(self, symbol: str) -> dict:
        """Close position - FIXED51: meet min qty and 5$ notional with reduce_only"""
        try:
            import math
            positions = self.positions("USDT")
            target_pos = None
            for p in positions:
                inst = p.get("instrument_name", "")
                if inst == symbol or inst.replace("USDT","") == symbol.replace("USDT","").replace("USD",""):
                    target_pos = p
                    break
                if symbol.replace("USDT","").upper() in inst.upper():
                    target_pos = p
                    break
            if not target_pos:
                for p in positions:
                    if p.get("symbol","").upper() == symbol.replace("USDT","USD").upper() or p.get("instrument_name","").upper() == symbol.upper():
                        target_pos = p
                        break
            if not target_pos:
                return {"ok": False, "error": f"No position found for {symbol}", "positions": len(positions)}
            
            size = float(target_pos.get("size") or target_pos.get("size_currency") or 0)
            if abs(size) < 1e-9:
                return {"ok": False, "error": f"Position size 0 for {symbol}"}
            
            # Get instrument meta for min qty and step
            try:
                meta = self.instrument(symbol)
                step = float(meta.get("contract_size") or meta.get("qty_step") or 0.001)
                minimum = float(meta.get("min_trade_amount") or meta.get("min_qty") or step)
            except:
                step = 0.001
                minimum = 0.001
            
            # Get current price for notional calc
            try:
                book = self.order_book(symbol, 1)
                price = float(book.get("mark_price") or book.get("bids",[[0]])[0][0] or 0)
            except:
                price = float(target_pos.get("mark_price") or target_pos.get("average_price") or 0)
            
            # Calculate required qty to meet 5.1$ notional
            min_qty_notional = 5.1 / price if price>0 else minimum
            if step>0:
                min_qty_notional = math.ceil(min_qty_notional / step) * step
            required_min = max(minimum, min_qty_notional, step)
            
            # For close, use max(current, required_min) with reduce_only=True
            # Exchange with reduce_only should close only up to current size
            qty = max(abs(size), required_min)
            # Ensure qty is multiple of step
            if step>0:
                qty = math.ceil(qty / step) * step
            
            side = "sell" if size > 0 else "buy"
            
            # Try v5 first (might have different min checks), fallback to /api/order
            try:
                # Try v5 order create
                body_v5 = {
                    "symbol": symbol.replace("USDT","USD") if symbol.endswith("USDT") else symbol,
                    "side": side,
                    "orderType": "Market",
                    "qty": str(qty),
                    "reduceOnly": True,
                }
                result = self._request("POST", "/v5/order/create", json_body=body_v5, use_v5=True)
                return {"ok": True, "symbol": symbol, "side": side, "qty": qty, "result": result, "closed_size": size, "via": "v5"}
            except Exception as e_v5:
                # Fallback to /api/order
                try:
                    result = self._place_order(symbol, side, qty, order_type="market", reduce_only=True, label=f"close_{symbol}_fixed48")
                    return {"ok": True, "symbol": symbol, "side": side, "qty": qty, "result": result, "closed_size": size, "via": "api", "v5_error": str(e_v5)[:200]}
                except Exception as e_api:
                    return {"ok": False, "error": f"v5 failed: {e_v5}, api failed: {e_api}", "symbol": symbol, "qty": qty, "size": size}
        except Exception as e:
            import traceback
            return {"ok": False, "error": str(e), "tb": traceback.format_exc()[:500]}

    def _place_order(self, symbol: str, side: str, qty: float, order_type: str = "market", price: Optional[float] = None, reduce_only: bool = False, label: str = "") -> dict:
        body = {
            "symbol": symbol.replace("USDT", "USD") if symbol.endswith("USDT") else symbol,
            "side": side,
            "qty": qty,
            "type": order_type,
            "reduce_only": reduce_only,
        }
        if price is not None:
            body["price"] = price
        if label:
            body["label"] = label
        
        try:
            data = self._request("POST", "/api/order", json_body=body, use_v5=False)
            return data
        except Exception as e:
            raise e
