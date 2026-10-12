"""
AriaX client - FIXED22 FILE DEBUG
"""
from __future__ import annotations
import time
import logging
import json
import os
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
        debug_path = "/tmp/wallet_debug.json"
        debug_info = {"called_at": time.time()}
        try:
            data = self._request("GET", "/api/wallet", use_v5=False)
            debug_info["request_ok"] = True
            debug_info["data_keys"] = list(data.keys()) if isinstance(data, dict) else str(type(data))
            debug_info["equity_raw"] = data.get("equity") if isinstance(data, dict) else None
        except Exception as e:
            debug_info["request_exception"] = str(e)
            import traceback
            debug_info["request_tb"] = traceback.format_exc()[:1000]
            try:
                with open(debug_path, "w") as f:
                    json.dump(debug_info, f)
            except:
                pass
            return {currency: {"equity": 0, "available": 0, "balance": 0}}
        
        try:
            if not isinstance(data, dict):
                debug_info["not_dict"] = True
                with open(debug_path, "w") as f:
                    json.dump(debug_info, f)
                return {currency: {"equity": 0, "available": 0, "balance": 0}}
            
            equity = data.get("equity")
            debug_info["equity"] = equity
            if equity is not None:
                try:
                    eq = float(equity)
                    debug_info["eq_float"] = eq
                    if eq > 0:
                        free = float(data.get("free_margin") or 0)
                        debug_info["free"] = free
                        fut_bal = 0
                        try:
                            fb = data.get("futures", {}).get("balances", {})
                            if isinstance(fb, dict):
                                fut_bal = float(fb.get("USDT") or 0)
                        except Exception as e:
                            debug_info["fut_bal_exception"] = str(e)
                        debug_info["fut_bal"] = fut_bal
                        available = free if free > 0 else fut_bal if fut_bal > 0 else eq
                        balance = fut_bal if fut_bal > 0 else eq
                        result = {currency: {"equity": eq, "available": available, "balance": balance}}
                        debug_info["returning"] = "equity"
                        debug_info["result"] = result
                        with open(debug_path, "w") as f:
                            json.dump(debug_info, f)
                        return result
                except Exception as e:
                    debug_info["equity_parse_exception"] = str(e)
                    import traceback
                    debug_info["equity_parse_tb"] = traceback.format_exc()[:1000]
            
            # Fallback futures
            try:
                fb = data.get("futures", {}).get("balances", {})
                if isinstance(fb, dict):
                    fut = float(fb.get("USDT") or 0)
                    debug_info["fut_fallback"] = fut
                    if fut > 0:
                        debug_info["returning"] = "fut_fallback"
                        result = {currency: {"equity": fut, "available": fut, "balance": fut}}
                        debug_info["result"] = result
                        with open(debug_path, "w") as f:
                            json.dump(debug_info, f)
                        return result
            except Exception as e:
                debug_info["fut_exception"] = str(e)
            
            debug_info["returning"] = "zero"
            with open(debug_path, "w") as f:
                json.dump(debug_info, f)
            return {currency: {"equity": 0, "available": 0, "balance": 0}}
        except Exception as e:
            debug_info["outer_exception"] = str(e)
            import traceback
            debug_info["outer_tb"] = traceback.format_exc()[:1000]
            try:
                with open(debug_path, "w") as f:
                    json.dump(debug_info, f)
            except:
                pass
            return {currency: {"equity": 0, "available": 0, "balance": 0}}

    def positions(self, currency: str = "USDT") -> List[dict]:
        debug_path = "/tmp/positions_debug.json"
        debug_info = {"called_at": time.time()}
        try:
            data = self._request("GET", "/api/positions", use_v5=False)
            debug_info["request_ok"] = True
            debug_info["data_type"] = str(type(data))
            if isinstance(data, dict):
                debug_info["data_keys"] = list(data.keys())
                if "data" in data and isinstance(data["data"], list):
                    debug_info["data_len"] = len(data["data"])
                    if data["data"]:
                        debug_info["first"] = data["data"][0]
        except Exception as e:
            debug_info["request_exception"] = str(e)
            import traceback
            debug_info["request_tb"] = traceback.format_exc()[:1000]
            try:
                with open(debug_path, "w") as f:
                    json.dump(debug_info, f)
            except:
                pass
            return []
        
        try:
            raw_positions = []
            if isinstance(data, dict) and "data" in data:
                raw_positions = data.get("data", [])
            elif isinstance(data, list):
                raw_positions = data
            else:
                debug_info["unknown_format"] = str(data)[:500]
                with open(debug_path, "w") as f:
                    json.dump(debug_info, f)
                return []
            
            debug_info["raw_count"] = len(raw_positions)
            
            normalized = []
            for p in raw_positions:
                try:
                    symbol = p.get("symbol") or ""
                    asset = symbol.replace("USDT", "").replace("USD", "")
                    instrument = asset + "USDT" if symbol.endswith("USD") and not symbol.endswith("USDT") else symbol
                    if not instrument.endswith("USDT"):
                        instrument = asset + "USDT"
                    
                    size = float(p.get("size") or 0)
                    entry = float(p.get("entry") or 0)
                    mark = float(p.get("mark") or 0)
                    upnl = float(p.get("upnl") or 0)
                    lev = p.get("lev") or 5
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
                    debug_info["pos_parse_exception"] = str(e)
                    continue
            
            debug_info["normalized_count"] = len(normalized)
            if normalized:
                debug_info["first_normalized"] = normalized[0]
            with open(debug_path, "w") as f:
                json.dump(debug_info, f)
            return normalized
        except Exception as e:
            debug_info["outer_exception"] = str(e)
            import traceback
            debug_info["outer_tb"] = traceback.format_exc()[:1000]
            try:
                with open(debug_path, "w") as f:
                    json.dump(debug_info, f)
            except:
                pass
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
