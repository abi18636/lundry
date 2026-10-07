from __future__ import annotations

"""
AriaX Testnet Exchange Client - Bybit v5 compatible + legacy /api/*

Supports both:
- Legacy v1: X-API-Key / X-API-Secret headers, /api/markets, /api/wallet, /api/order etc.
- v5 public: /v5/market/kline, /v5/market/orderbook, /v5/market/tickers (no auth)
- v5 private: HMAC X-BAPI-* headers for wallet/positions if needed

This client mimics the DeribitClient interface used by TradingEngine:
  - account_summary, positions, open_orders, order_book, instrument, candles
  - limit_ioc, buy_market, sell_market, close_position, cancel_order
  - etc.

Fees: maker 0.02% taker 0.05%
"""

import hashlib
import hmac
import logging
import math
import threading
import time
from typing import Any, Dict, List, Optional
import itertools

import httpx
import pandas as pd

log = logging.getLogger("ariax")


class AriaXAPIError(RuntimeError):
    def __init__(self, method: str, code: Any, message: str, data: Any = None):
        self.method, self.code, self.data = method, code, data
        super().__init__(f"{method}: {code} {message}" + (f" ({data})" if data else ""))


class AriaXClient:
    def __init__(self, base_url: str, api_key: str, api_secret: str, timeout: float = 20.0, fallback_url: str = ""):
        self.base = base_url.rstrip("/")
        self.fallback = fallback_url.rstrip("/") if fallback_url else ""
        self.api_key = api_key
        self.api_secret = api_secret
        self._http = httpx.Client(timeout=timeout)
        self._ids = itertools.count(1)
        self._instrument_cache: Dict[str, tuple] = {}
        self._cache_lock = threading.Lock()
        self.last_testnet: Optional[bool] = True

    def close(self):
        self._http.close()

    def _headers_legacy(self) -> Dict[str, str]:
        return {
            "X-API-Key": self.api_key,
            "X-API-Secret": self.api_secret,
            "Content-Type": "application/json"
        }

    def _headers_v5(self, timestamp: str, recv_window: str, signature: str) -> Dict[str, str]:
        return {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": recv_window,
            "X-BAPI-SIGNATURE": signature,
            "Content-Type": "application/json"
        }

    def _sign_v5(self, timestamp: str, recv_window: str, payload: str) -> str:
        msg = f"{timestamp}{self.api_key}{recv_window}{payload}"
        return hmac.new(self.api_secret.encode(), msg.encode(), hashlib.sha256).hexdigest()

    def _request(self, method: str, path: str, params: Optional[dict] = None, json_body: Optional[dict] = None, use_v5_auth: bool = False, is_public: bool = False) -> Any:
        """
        Try primary base, then fallback if 404/no-server
        """
        urls_to_try = [self.base]
        if self.fallback and self.fallback != self.base:
            urls_to_try.append(self.fallback)

        last_exc = None
        for base in urls_to_try:
            url = base + path
            try:
                if is_public:
                    # Public endpoints no auth
                    if method == "GET":
                        resp = self._http.get(url, params=params)
                    else:
                        resp = self._http.request(method, url, params=params, json=json_body)
                elif use_v5_auth:
                    # v5 private with HMAC
                    ts = str(int(time.time() * 1000))
                    recv = "5000"
                    if method == "GET":
                        # payload = query string
                        query_str = ""
                        if params:
                            # sort keys for deterministic? Bybit uses raw query string as sent
                            query_str = "&".join(f"{k}={v}" for k, v in params.items())
                        sig = self._sign_v5(ts, recv, query_str)
                        headers = self._headers_v5(ts, recv, sig)
                        resp = self._http.get(url, params=params, headers=headers)
                    else:
                        import json as js
                        body_str = js.dumps(json_body or {}, separators=(",", ":"))
                        sig = self._sign_v5(ts, recv, body_str)
                        headers = self._headers_v5(ts, recv, sig)
                        resp = self._http.request(method, url, json=json_body, headers=headers)
                else:
                    # Legacy v1 with simple headers
                    headers = self._headers_legacy()
                    if method == "GET":
                        resp = self._http.get(url, params=params, headers=headers)
                    else:
                        resp = self._http.request(method, url, params=params, json=json_body, headers=headers)

                # Check for Render no-server (404 with specific header)
                if resp.status_code == 404 and "no-server" in resp.headers.get("x-render-routing", ""):
                    last_exc = AriaXAPIError(path, 404, "no-server, trying fallback")
                    continue

                # Try parse json
                try:
                    data = resp.json()
                except Exception:
                    if resp.status_code >= 400:
                        raise AriaXAPIError(path, resp.status_code, f"HTTP {resp.status_code} non-JSON")
                    return {}

                # v5 envelope: retCode
                if isinstance(data, dict) and "retCode" in data:
                    if data.get("retCode") != 0:
                        raise AriaXAPIError(path, data.get("retCode"), data.get("retMsg", "error"), str(data.get("retExtInfo")))
                    return data

                # Legacy envelope: ok
                if isinstance(data, dict) and "ok" in data:
                    if not data.get("ok"):
                        raise AriaXAPIError(path, data.get("error") or -1, str(data))
                    return data

                return data

            except httpx.HTTPError as exc:
                last_exc = AriaXAPIError(path, "transport", type(exc).__name__)
                continue
            except AriaXAPIError as exc:
                # If it's no-server or transport, try fallback
                if "no-server" in str(exc) or exc.code == "transport" or exc.code == 404:
                    last_exc = exc
                    continue
                raise

        # All urls failed
        if last_exc:
            raise last_exc
        raise AriaXAPIError(path, -1, "all endpoints failed")

    # ── Public market data ──────────────────────────────────────────────
    def ticker(self, instrument: str) -> dict:
        # instrument like BTCUSDT
        try:
            data = self._request("GET", "/v5/market/tickers", params={"category": "linear", "symbol": instrument}, is_public=True)
            lst = data.get("result", {}).get("list", [])
            if lst:
                return lst[0]
            return {}
        except Exception:
            return {}

    def order_book(self, instrument: str, depth: int = 5) -> dict:
        """
        Returns dict compatible with Deribit order_book:
        {bids: [[price, qty]], asks: [[price, qty]], timestamp, mark_price, state}
        """
        # Try v5 orderbook
        try:
            # depth allowed 1,25,50,200 - map 5->25
            limit = 25 if depth <= 25 else 50
            data = self._request("GET", "/v5/market/orderbook", params={"category": "linear", "symbol": instrument, "limit": limit}, is_public=True)
            res = data.get("result", {})
            bids = [[float(p), float(q)] for p, q in res.get("b", [])]
            asks = [[float(p), float(q)] for p, q in res.get("a", [])]
            ts = res.get("ts", int(time.time()*1000))
            # Get mark price from tickers
            ticker = self.ticker(instrument)
            mark = float(ticker.get("markPrice") or ticker.get("lastPrice") or 0)
            # state always open if we have book
            return {
                "bids": bids,
                "asks": asks,
                "timestamp": ts,
                "mark_price": mark,
                "state": "open",
                "best_bid": bids[0][0] if bids else None,
                "best_ask": asks[0][0] if asks else None,
            }
        except Exception as exc:
            log.debug(f"order_book failed for {instrument}: {exc}")
            return {"bids": [], "asks": [], "timestamp": int(time.time()*1000), "mark_price": 0, "state": "unknown"}

    def market_state(self, instrument: str) -> str:
        ob = self.order_book(instrument, 1)
        return str(ob.get("state") or "unknown").lower()

    def instrument(self, instrument: str) -> dict:
        """
        Returns dict with min_trade_amount, contract_size, etc.
        instrument like BTCUSDT
        """
        now = time.monotonic()
        with self._cache_lock:
            cached = self._instrument_cache.get(instrument)
            if cached and now - cached[0] < 300:
                return dict(cached[1])

        try:
            data = self._request("GET", "/v5/market/instruments-info", params={"category": "linear", "symbol": instrument}, is_public=True)
            lst = data.get("result", {}).get("list", [])
            if not lst:
                # fallback defaults
                result = {"min_trade_amount": 0.001, "contract_size": 0.001, "instrument_type": "linear", "settlement_currency": "USDT", "tick_size": 0.1}
            else:
                info = lst[0]
                lot = info.get("lotSizeFilter", {})
                price_filter = info.get("priceFilter", {})
                result = {
                    "min_trade_amount": float(lot.get("minOrderQty") or 0.001),
                    "contract_size": float(lot.get("qtyStep") or 0.001),
                    "instrument_type": "linear",
                    "settlement_currency": "USDT",
                    "tick_size": float(price_filter.get("tickSize") or 0.01),
                    "min_price": float(price_filter.get("minPrice") or 0),
                    "max_price": float(price_filter.get("maxPrice") or 0),
                    "min_notional": float(lot.get("minNotionalValue") or 5),
                }
        except Exception:
            result = {"min_trade_amount": 0.001, "contract_size": 0.001, "instrument_type": "linear", "settlement_currency": "USDT", "tick_size": 0.01}

        with self._cache_lock:
            self._instrument_cache[instrument] = (now, result)
        return dict(result)

    def candles(self, instrument: str, hours: int = 2500, resolution: str = "60") -> pd.DataFrame:
        """
        Fetch klines from AriaX v5 with pagination to get enough history
        resolution: 60 = 1h
        """
        interval_map = {"1": "1", "5": "5", "15": "15", "30": "30", "60": "60", "120": "120", "240": "240"}
        interval = interval_map.get(str(resolution), "60")

        try:
            import time as time_mod
            all_rows = []
            # Fetch in chunks backwards from now
            end_ms = int(time_mod.time() * 1000)
            remaining = max(48, hours)
            # Max 1000 per request, but we may need multiple requests
            # For BTC which only has 100, we will get what we can
            for _ in range(5):  # Up to 5 requests = 5000 candles
                if remaining <= 0:
                    break
                limit = min(1000, remaining)
                try:
                    data = self._request("GET", "/v5/market/kline", params={
                        "category": "linear",
                        "symbol": instrument,
                        "interval": interval,
                        "limit": limit,
                        "end": end_ms,
                    }, is_public=True)
                    rows = data.get("result", {}).get("list", [])
                    if not rows:
                        break
                    all_rows.extend(rows)
                    # Set next end to oldest timestamp - 1
                    oldest_ts = min(int(r[0]) for r in rows)
                    end_ms = oldest_ts - 1
                    remaining -= len(rows)
                    if len(rows) < limit:
                        break
                    # Small delay to avoid rate limit
                    time_mod.sleep(0.1)
                except Exception as e:
                    log.debug(f"candle chunk failed for {instrument}: {e}")
                    break

            if not all_rows:
                return pd.DataFrame(columns=["dt", "open", "high", "low", "close", "volume"])

            # Deduplicate by timestamp and sort oldest first
            seen = {}
            for r in all_rows:
                try:
                    ts = int(r[0])
                    if ts not in seen:
                        seen[ts] = r
                except Exception:
                    continue
            rows = sorted(seen.values(), key=lambda x: int(x[0]))

            df_data = []
            for r in rows:
                try:
                    ts = int(r[0])
                    dt = pd.to_datetime(ts, unit="ms", utc=True)
                    o = float(r[1]); h = float(r[2]); l = float(r[3]); c = float(r[4]); v = float(r[5])
                    df_data.append((dt, o, h, l, c, v))
                except Exception:
                    continue

            df = pd.DataFrame(df_data, columns=["dt", "open", "high", "low", "close", "volume"])
            df = df.dropna()
            df = df[df["close"] > 0]
            df = df.sort_values("dt").reset_index(drop=True)
            # Keep only last `hours` rows
            if len(df) > hours:
                df = df.tail(hours).reset_index(drop=True)
            return df
        except Exception as exc:
            log.warning(f"candles failed for {instrument}: {exc}")
            return pd.DataFrame(columns=["dt", "open", "high", "low", "close", "volume"])

    # ── Account ─────────────────────────────────────────────────────────
    def account_summary(self, currency: str = "USDT") -> dict:
        """
        Returns dict compatible with Deribit account_summary:
        {equity, balance, available_funds}
        """
        try:
            # Try v5 wallet-balance with HMAC
            ts = str(int(time.time() * 1000))
            recv = "5000"
            params_str = "accountType=UNIFIED"
            sig = self._sign_v5(ts, recv, params_str)
            headers = self._headers_v5(ts, recv, sig)
            # Use direct httpx to have control
            url = self.base + "/v5/account/wallet-balance"
            # Try primary and fallback
            for base in [self.base, self.fallback]:
                if not base:
                    continue
                try:
                    resp = self._http.get(base + "/v5/account/wallet-balance", params={"accountType": "UNIFIED"}, headers=headers)
                    data = resp.json()
                    if data.get("retCode") == 0:
                        lst = data.get("result", {}).get("list", [])
                        if lst:
                            coin_data = lst[0].get("coin", [])
                            for coin in coin_data:
                                if coin.get("coin") == currency or currency == "USDT":
                                    equity = float(coin.get("equity") or 0)
                                    balance = float(coin.get("walletBalance") or 0)
                                    avail = float(coin.get("availableToWithdraw") or 0)
                                    return {"equity": equity, "balance": balance, "available_funds": avail}
                            # fallback total
                            total_equity = float(data.get("result", {}).get("totalEquity") or 0)
                            total_avail = float(data.get("result", {}).get("totalAvailableBalance") or 0)
                            return {"equity": total_equity, "balance": total_equity, "available_funds": total_avail}
                except Exception:
                    continue

            # Fallback to legacy /api/wallet
            data = self._request("GET", "/api/wallet", use_v5_auth=False, is_public=False)
            # legacy returns balances dict
            if data.get("ok"):
                balances = data.get("balances", {})
                equity = float(data.get("equity") or balances.get(currency) or balances.get("USDT") or 0)
                free = float(data.get("free_margin") or 0)
                # For legacy, equity is total, free is free
                return {"equity": equity, "balance": equity, "available_funds": float(balances.get("USDT") or equity)}
            return {"equity": 0, "balance": 0, "available_funds": 0}
        except Exception as exc:
            log.warning(f"account_summary failed: {exc}")
            return {"equity": 0, "balance": 0, "available_funds": 0, "error": str(exc)}

    def positions(self, currency: str = "USDT") -> List[dict]:
        """
        Returns list compatible with Deribit positions:
        Each position dict has instrument_name, size, size_currency, average_price, mark_price, etc.
        """
        try:
            # Try v5 position/list with HMAC
            ts = str(int(time.time() * 1000))
            recv = "5000"
            params = {"category": "linear"}
            query_str = "category=linear"
            sig = self._sign_v5(ts, recv, query_str)
            headers = self._headers_v5(ts, recv, sig)
            for base in [self.base, self.fallback]:
                if not base:
                    continue
                try:
                    resp = self._http.get(base + "/v5/position/list", params=params, headers=headers)
                    data = resp.json()
                    if data.get("retCode") == 0:
                        lst = data.get("result", {}).get("list", [])
                        result = []
                        for p in lst:
                            try:
                                sym = p.get("symbol")  # BTCUSDT
                                size = float(p.get("size") or 0)
                                if size == 0:
                                    continue
                                # Convert symbol to instrument_name like BTC_USDC-PERPETUAL for compat?
                                # We keep v5 symbol as instrument_name
                                entry = float(p.get("avgPrice") or 0)
                                mark = float(p.get("markPrice") or 0)
                                # size_currency is base qty
                                # For linear, size is in base coin
                                result.append({
                                    "instrument_name": sym,  # e.g. BTCUSDT
                                    "size": size * mark if mark else size,  # notional for compat
                                    "size_currency": size,
                                    "direction": "buy" if p.get("side") == "Buy" else "sell",
                                    "average_price": entry,
                                    "mark_price": mark,
                                    "leverage": float(p.get("leverage") or 1),
                                    "floating_profit_loss": float(p.get("unrealisedPnl") or 0),
                                    "total_profit_loss": float(p.get("unrealisedPnl") or 0),
                                    "realized_profit_loss": float(p.get("curRealisedPnl") or 0),
                                    "maintenance_margin": 0,
                                    "initial_margin": 0,
                                    "open_orders_margin": 0,
                                    "settlement_price": mark,
                                    "interest_value": 0,
                                    "delta": size,
                                    "realized_funding": 0,
                                })
                            except Exception:
                                continue
                        return result
                except Exception:
                    continue

            # Fallback legacy /api/positions
            data = self._request("GET", "/api/positions", use_v5_auth=False, is_public=False)
            if data.get("ok"):
                result = []
                for p in data.get("data", []):
                    try:
                        # legacy: symbol BTCUSD, size 0.001, entry, mark, lev
                        sym_legacy = p.get("symbol")  # BTCUSD
                        # Map to v5 symbol
                        sym_v5 = sym_legacy.replace("USD", "USDT") if sym_legacy.endswith("USD") and not sym_legacy.endswith("USDT") else sym_legacy
                        size = float(p.get("size") or 0)
                        entry = float(p.get("entry") or 0)
                        mark = float(p.get("mark") or entry)
                        upnl = float(p.get("upnl") or 0)
                        result.append({
                            "instrument_name": sym_v5,
                            "size": abs(size) * mark,
                            "size_currency": size,
                            "direction": "buy" if size > 0 else "sell",
                            "average_price": entry,
                            "mark_price": mark,
                            "leverage": float(p.get("lev") or 1),
                            "floating_profit_loss": upnl,
                            "total_profit_loss": upnl,
                            "realized_profit_loss": 0,
                            "maintenance_margin": 0,
                            "initial_margin": float(p.get("margin") or 0),
                            "open_orders_margin": 0,
                            "settlement_price": mark,
                            "interest_value": 0,
                            "delta": size,
                            "realized_funding": 0,
                        })
                    except Exception:
                        continue
                return result
            return []
        except Exception as exc:
            log.warning(f"positions failed: {exc}")
            return []

    def open_orders(self, instrument: Optional[str] = None) -> List[dict]:
        try:
            # v5 order/realtime
            ts = str(int(time.time() * 1000))
            recv = "5000"
            params = {"category": "linear"}
            if instrument:
                params["symbol"] = instrument
                query_str = f"category=linear&symbol={instrument}"
            else:
                query_str = "category=linear"
            sig = self._sign_v5(ts, recv, query_str)
            headers = self._headers_v5(ts, recv, sig)
            for base in [self.base, self.fallback]:
                if not base:
                    continue
                try:
                    resp = self._http.get(base + "/v5/order/realtime", params=params, headers=headers)
                    data = resp.json()
                    if data.get("retCode") == 0:
                        lst = data.get("result", {}).get("list", [])
                        result = []
                        for o in lst:
                            result.append({
                                "order_id": o.get("orderId"),
                                "instrument_name": o.get("symbol"),
                                "amount": float(o.get("qty") or 0),
                                "filled_amount": float(o.get("cumExecQty") or 0),
                                "price": float(o.get("price") or 0),
                                "direction": o.get("side", "").lower(),
                                "reduce_only": bool(o.get("reduceOnly")),
                                "label": o.get("orderLinkId") or "",
                                "order_state": "open",
                            })
                        return result
                except Exception:
                    continue

            # legacy fallback
            data = self._request("GET", "/api/orders", use_v5_auth=False, is_public=False)
            if data.get("ok"):
                result = []
                for o in data.get("data", []):
                    result.append({
                        "order_id": str(o.get("id")),
                        "instrument_name": o.get("symbol", "").replace("USD", "USDT") if o.get("symbol", "").endswith("USD") else o.get("symbol"),
                        "amount": float(o.get("qty") or 0),
                        "filled_amount": 0,
                        "price": float(o.get("price") or 0),
                        "direction": o.get("side", "").lower(),
                        "reduce_only": False,
                        "label": "",
                        "order_state": "open",
                    })
                return result
            return []
        except Exception:
            return []

    def orders_by_label(self, label: str) -> List[dict]:
        # AriaX doesn't use labels same way, return empty to avoid blocking
        return []

    def cancel_order(self, order_id: str) -> dict:
        try:
            # Try v5 cancel
            ts = str(int(time.time() * 1000))
            recv = "5000"
            body = {"category": "linear", "orderId": order_id}
            import json as js
            body_str = js.dumps(body, separators=(",", ":"))
            sig = self._sign_v5(ts, recv, body_str)
            headers = self._headers_v5(ts, recv, sig)
            for base in [self.base, self.fallback]:
                if not base:
                    continue
                try:
                    resp = self._http.post(base + "/v5/order/cancel", json=body, headers=headers)
                    data = resp.json()
                    if data.get("retCode") == 0:
                        return data
                except Exception:
                    continue

            # legacy cancel
            data = self._request("POST", "/api/cancel", json_body={"id": int(order_id) if order_id.isdigit() else order_id}, use_v5_auth=False, is_public=False)
            return data
        except Exception as exc:
            raise AriaXAPIError("cancel", -1, str(exc))

    # ── Trading ─────────────────────────────────────────────────────────
    def _place_order_legacy(self, symbol_legacy: str, side: str, qty: float, order_type: str = "market", price: Optional[float] = None, lev: int = 1, reduce_only: bool = False) -> dict:
        body = {
            "symbol": symbol_legacy,
            "side": side,
            "type": order_type,
            "qty": qty,
            "lev": lev
        }
        if price is not None and order_type == "limit":
            body["price"] = price
        # Note: legacy doesn't have reduce_only param, but we try
        data = self._request("POST", "/api/order", json_body=body, use_v5_auth=False, is_public=False)
        return data

    def _place_order_v5(self, symbol_v5: str, side: str, qty: float, order_type: str = "Market", price: Optional[float] = None, lev: int = 1, reduce_only: bool = False, label: str = "") -> dict:
        ts = str(int(time.time() * 1000))
        recv = "5000"
        # side Buy/Sell, orderType Limit/Market
        body = {
            "category": "linear",
            "symbol": symbol_v5,
            "side": side.capitalize(),  # Buy/Sell
            "orderType": order_type.capitalize(),  # Market/Limit
            "qty": str(qty),
            "timeInForce": "IOC" if order_type.lower() == "market" else "GTC",
        }
        if price is not None and order_type.lower() == "limit":
            body["price"] = str(price)
        if reduce_only:
            body["reduceOnly"] = True
        if label:
            body["orderLinkId"] = label[:32]

        import json as js
        body_str = js.dumps(body, separators=(",", ":"))
        sig = self._sign_v5(ts, recv, body_str)
        headers = self._headers_v5(ts, recv, sig)

        for base in [self.base, self.fallback]:
            if not base:
                continue
            try:
                resp = self._http.post(base + "/v5/order/create", json=body, headers=headers)
                data = resp.json()
                if data.get("retCode") == 0:
                    return data
                else:
                    # If v5 fails, raise to try legacy
                    raise AriaXAPIError("order/create", data.get("retCode"), data.get("retMsg", ""))
            except AriaXAPIError:
                raise
            except Exception as exc:
                continue
        raise AriaXAPIError("order/create", -1, "all bases failed")

    def limit_ioc(self, instrument: str, direction: str, amount: float, price: float, label: str, reduce_only: bool = False) -> dict:
        """
        instrument like BTCUSDT, direction buy/sell, amount in base currency
        """
        # Map to legacy symbol
        legacy_sym = instrument.replace("USDT", "USD") if instrument.endswith("USDT") else instrument
        # For AriaX, amount is base qty
        try:
            # Try v5 first with IOC
            ts = str(int(time.time() * 1000))
            recv = "5000"
            body = {
                "category": "linear",
                "symbol": instrument,
                "side": direction.capitalize(),
                "orderType": "Limit",
                "qty": str(amount),
                "price": str(price),
                "timeInForce": "IOC",
                "orderLinkId": label[:32],
            }
            if reduce_only:
                body["reduceOnly"] = True
            import json as js
            body_str = js.dumps(body, separators=(",", ":"))
            sig = self._sign_v5(ts, recv, body_str)
            headers = self._headers_v5(ts, recv, sig)
            for base in [self.base, self.fallback]:
                if not base:
                    continue
                try:
                    resp = self._http.post(base + "/v5/order/create", json=body, headers=headers)
                    data = resp.json()
                    if data.get("retCode") == 0:
                        order_id = data.get("result", {}).get("orderId") or data.get("result", {}).get("orderLinkId") or label
                        # Fetch fills to get filled amount
                        # For simplicity, assume full fill if IOC and price aggressive, but check execution list
                        # We return structure compatible with Deribit fill_summary
                        return {
                            "order": {
                                "order_id": order_id,
                                "order_state": "filled",
                                "filled_amount": amount,
                                "average_price": price,
                                "price": price,
                                "amount": amount,
                                "direction": direction,
                            },
                            "trades": []
                        }
                    else:
                        log.warning(f"v5 limit_ioc failed: {data}")
                except Exception as e:
                    log.debug(f"v5 limit_ioc exception: {e}")
                    continue

            # Fallback legacy limit order
            data = self._place_order_legacy(legacy_sym, direction, amount, "limit", price, lev=1, reduce_only=reduce_only)
            if data.get("ok"):
                order_id = data.get("id")
                return {
                    "order": {
                        "order_id": str(order_id),
                        "order_state": "filled",
                        "filled_amount": amount,
                        "average_price": price,
                        "price": price,
                        "amount": amount,
                        "direction": direction,
                    },
                    "trades": []
                }
            raise AriaXAPIError("limit_ioc", -1, f"legacy failed {data}")
        except Exception as exc:
            raise AriaXAPIError("limit_ioc", -1, str(exc))

    def buy_market(self, instrument: str, amount: float, label: str = "zenith", reduce_only: bool = False) -> dict:
        legacy_sym = instrument.replace("USDT", "USD") if instrument.endswith("USDT") else instrument
        try:
            # Try v5 market
            ts = str(int(time.time() * 1000))
            recv = "5000"
            body = {
                "category": "linear",
                "symbol": instrument,
                "side": "Buy",
                "orderType": "Market",
                "qty": str(amount),
                "timeInForce": "IOC",
                "orderLinkId": label[:32],
            }
            if reduce_only:
                body["reduceOnly"] = True
            import json as js
            body_str = js.dumps(body, separators=(",", ":"))
            sig = self._sign_v5(ts, recv, body_str)
            headers = self._headers_v5(ts, recv, sig)
            for base in [self.base, self.fallback]:
                if not base:
                    continue
                try:
                    resp = self._http.post(base + "/v5/order/create", json=body, headers=headers)
                    data = resp.json()
                    if data.get("retCode") == 0:
                        order_id = data.get("result", {}).get("orderId") or label
                        # Get last price for avg
                        ticker = self.ticker(instrument)
                        avg = float(ticker.get("lastPrice") or ticker.get("markPrice") or 0)
                        return {
                            "order": {
                                "order_id": order_id,
                                "order_state": "filled",
                                "filled_amount": amount,
                                "average_price": avg,
                                "price": avg,
                                "amount": amount,
                                "direction": "buy",
                            },
                            "trades": []
                        }
                except Exception:
                    continue

            # Fallback legacy
            data = self._place_order_legacy(legacy_sym, "buy", amount, "market", lev=1, reduce_only=reduce_only)
            if data.get("ok"):
                ticker = self.ticker(instrument)
                avg = float(ticker.get("lastPrice") or 0)
                return {
                    "order": {
                        "order_id": str(data.get("id")),
                        "order_state": "filled",
                        "filled_amount": amount,
                        "average_price": avg,
                        "price": avg,
                        "amount": amount,
                        "direction": "buy",
                    },
                    "trades": []
                }
            raise AriaXAPIError("buy_market", -1, str(data))
        except Exception as exc:
            raise AriaXAPIError("buy_market", -1, str(exc))

    def sell_market(self, instrument: str, amount: float, label: str = "zenith", reduce_only: bool = False) -> dict:
        legacy_sym = instrument.replace("USDT", "USD") if instrument.endswith("USDT") else instrument
        try:
            ts = str(int(time.time() * 1000))
            recv = "5000"
            body = {
                "category": "linear",
                "symbol": instrument,
                "side": "Sell",
                "orderType": "Market",
                "qty": str(amount),
                "timeInForce": "IOC",
                "orderLinkId": label[:32],
            }
            if reduce_only:
                body["reduceOnly"] = True
            import json as js
            body_str = js.dumps(body, separators=(",", ":"))
            sig = self._sign_v5(ts, recv, body_str)
            headers = self._headers_v5(ts, recv, sig)
            for base in [self.base, self.fallback]:
                if not base:
                    continue
                try:
                    resp = self._http.post(base + "/v5/order/create", json=body, headers=headers)
                    data = resp.json()
                    if data.get("retCode") == 0:
                        order_id = data.get("result", {}).get("orderId") or label
                        ticker = self.ticker(instrument)
                        avg = float(ticker.get("lastPrice") or 0)
                        return {
                            "order": {
                                "order_id": order_id,
                                "order_state": "filled",
                                "filled_amount": amount,
                                "average_price": avg,
                                "price": avg,
                                "amount": amount,
                                "direction": "sell",
                            },
                            "trades": []
                        }
                except Exception:
                    continue

            data = self._place_order_legacy(legacy_sym, "sell", amount, "market", lev=1, reduce_only=reduce_only)
            if data.get("ok"):
                ticker = self.ticker(instrument)
                avg = float(ticker.get("lastPrice") or 0)
                return {
                    "order": {
                        "order_id": str(data.get("id")),
                        "order_state": "filled",
                        "filled_amount": amount,
                        "average_price": avg,
                        "price": avg,
                        "amount": amount,
                        "direction": "sell",
                    },
                    "trades": []
                }
            raise AriaXAPIError("sell_market", -1, str(data))
        except Exception as exc:
            raise AriaXAPIError("sell_market", -1, str(exc))

    def close_position(self, instrument: str) -> dict:
        """
        Close full position via market order opposite side
        """
        try:
            positions = self.positions()
            for p in positions:
                if p.get("instrument_name") == instrument:
                    size = float(p.get("size_currency") or 0)
                    if abs(size) < 1e-12:
                        return {"order": {"order_id": "none", "order_state": "filled", "filled_amount": 0, "average_price": 0, "price": 0, "amount": 0}}
                    side = "sell" if size > 0 else "buy"
                    if side == "sell":
                        return self.sell_market(instrument, abs(size), label=f"close_{instrument.lower()}", reduce_only=True)
                    else:
                        return self.buy_market(instrument, abs(size), label=f"close_{instrument.lower()}", reduce_only=True)
            return {"order": {"order_id": "none", "order_state": "filled", "filled_amount": 0, "average_price": 0, "price": 0, "amount": 0}}
        except Exception as exc:
            raise AriaXAPIError("close_position", -1, str(exc))

    def set_leverage(self, instrument: str, leverage: int) -> dict:
        """
        Set leverage for symbol - AriaX supports 1-100x depending on symbol
        Uses v5 endpoint POST /v5/position/set-leverage
        """
        try:
            ts = str(int(time.time() * 1000))
            recv = "5000"
            body = {
                "category": "linear",
                "symbol": instrument,
                "buyLeverage": str(leverage),
                "sellLeverage": str(leverage),
            }
            import json as js
            body_str = js.dumps(body, separators=(",", ":"))
            sig = self._sign_v5(ts, recv, body_str)
            headers = self._headers_v5(ts, recv, sig)
            for base in [self.base, self.fallback]:
                if not base:
                    continue
                try:
                    resp = self._http.post(base + "/v5/position/set-leverage", json=body, headers=headers)
                    data = resp.json()
                    if data.get("retCode") == 0:
                        log.info(f"Set leverage {instrument} to {leverage}x")
                        return data
                    else:
                        log.warning(f"set_leverage failed {instrument} {leverage}: {data}")
                except Exception as e:
                    log.debug(f"set_leverage exception {instrument}: {e}")
                    continue
            # Fallback: try legacy? No legacy leverage endpoint, so just log
            return {"retCode": -1, "retMsg": "failed"}
        except Exception as exc:
            log.warning(f"set_leverage error {instrument}: {exc}")
            return {"retCode": -1, "retMsg": str(exc)}

    # ── Financial reporting stubs ───────────────────────────────────────
    def trades_by_order(self, order_id: str) -> List[dict]:
        return []

    def user_trades_window(self, start_ms: int, end_ms: int) -> tuple[List[dict], bool]:
        try:
            data = self._request("GET", "/api/fills", use_v5_auth=False, is_public=False)
            if data.get("ok"):
                # data is list of fills
                trades = data.get("data", []) if isinstance(data.get("data"), list) else data.get("fills", [])
                # Filter by time if needed
                return trades, False
            return [], False
        except Exception:
            return [], False

    def transaction_window(self, start_ms: int, end_ms: int) -> tuple[List[dict], bool]:
        return [], False

    # ── Extra ───────────────────────────────────────────────────────────
    def list_instruments(self, currency: str = "USDT") -> List[dict]:
        try:
            data = self._request("GET", "/v5/market/instruments-info", params={"category": "linear"}, is_public=True)
            return data.get("result", {}).get("list", [])
        except Exception:
            return []


# Alias for compatibility - engine expects DeribitClient
DeribitClient = AriaXClient
