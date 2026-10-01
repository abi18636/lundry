from __future__ import annotations

import itertools
import logging
import math
import threading
import time
from typing import Any, Dict, List, Optional

import httpx
import pandas as pd

log = logging.getLogger("deribit")


class DeribitAPIError(RuntimeError):
    def __init__(self, method: str, code: Any, message: str, data: Any = None):
        self.method, self.code, self.data = method, code, data
        super().__init__(f"{method}: {code} {message}" + (f" ({data})" if data else ""))


class DeribitClient:
    """REST JSON-RPC. Credentials never appear in URLs or exception messages.

    Read-only requests may be retried by the caller. Orders are NEVER blindly retried.
    """
    def __init__(self, base_url: str, client_id: str, client_secret: str, timeout: float = 20.0):
        self.base = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: Optional[str] = None
        self._token_exp = 0.0
        self._auth_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._instrument_cache: Dict[str, tuple] = {}
        self._ids = itertools.count(1)
        self._http = httpx.Client(timeout=timeout)
        self.last_testnet: Optional[bool] = None

    def close(self):
        self._http.close()

    def _redact(self, value: Any) -> str:
        text = str(value)
        for secret in (self.client_secret, self.client_id, self._token):
            if secret:
                text = text.replace(secret, "<redacted>")
        return text[:1000]

    def _rpc(self, method: str, params: Optional[dict] = None, token: Optional[str] = None) -> Any:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            response = self._http.post(self.base, headers=headers, json={
                "jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params or {},
            })
        except httpx.HTTPError as exc:
            # Do not echo the request, auth parameters, or token.
            raise DeribitAPIError(method, "transport", type(exc).__name__) from None
        try:
            data = response.json()
        except Exception:
            raise DeribitAPIError(method, response.status_code, "non-JSON exchange response") from None
        if not isinstance(data, dict):
            raise DeribitAPIError(method, "protocol", "invalid JSON-RPC response")
        if isinstance(data.get("testnet"), bool):
            self.last_testnet = data["testnet"]
        error = data.get("error")
        if error:
            if isinstance(error, dict):
                raise DeribitAPIError(method, error.get("code"), self._redact(error.get("message")),
                                      self._redact(error.get("data")) if error.get("data") else None)
            raise DeribitAPIError(method, response.status_code, self._redact(error))
        if response.status_code >= 400:
            raise DeribitAPIError(method, response.status_code, "HTTP failure")
        if "result" not in data:
            raise DeribitAPIError(method, "protocol", "missing result")
        return data["result"]

    def _auth(self, force: bool = False) -> str:
        with self._auth_lock:
            if not force and self._token and time.time() < self._token_exp - 60:
                return self._token
            result = self._rpc("public/auth", {
                "grant_type": "client_credentials", "client_id": self.client_id,
                "client_secret": self.client_secret,
            })
            if not result or not result.get("access_token"):
                raise DeribitAPIError("public/auth", "protocol", "missing access token")
            self._token = result["access_token"]
            self._token_exp = time.time() + float(result.get("expires_in", 800))
            return self._token

    def _private(self, method: str, params: Optional[dict] = None) -> Any:
        token = self._auth()
        try:
            return self._rpc(f"private/{method}", params, token)
        except DeribitAPIError as exc:
            # These are explicit rejections, not ambiguous execution timeouts.
            if exc.code in (13009, 13004, 401):
                return self._rpc(f"private/{method}", params, self._auth(force=True))
            raise

    def _public(self, method: str, params: Optional[dict] = None) -> Any:
        return self._rpc(f"public/{method}", params)

    def ticker(self, instrument: str) -> dict:
        return self._public("ticker", {"instrument_name": instrument}) or {}

    def order_book(self, instrument: str, depth: int = 5) -> dict:
        return self._public("get_order_book", {"instrument_name": instrument, "depth": depth}) or {}

    def market_state(self, instrument: str) -> str:
        # A failed API call must not be silently labelled as a venue halt.
        return str(self.order_book(instrument, 1).get("state") or "unknown").lower()

    def instrument(self, instrument: str) -> dict:
        now = time.monotonic()
        with self._cache_lock:
            cached = self._instrument_cache.get(instrument)
            if cached and now - cached[0] < 300:
                return dict(cached[1])
        result = self._public("get_instrument", {"instrument_name": instrument}) or {}
        with self._cache_lock:
            self._instrument_cache[instrument] = (now, result)
        return dict(result)

    def list_instruments(self, currency: str = "USDC") -> List[dict]:
        return self._public("get_instruments", {"currency": currency, "kind": "future", "expired": False}) or []

    def candles(self, instrument: str, hours: int = 2500, resolution: str = "60") -> pd.DataFrame:
        end = int(time.time() * 1000)
        hours = int(min(max(hours, 48), 5000))
        result = self._public("get_tradingview_chart_data", {
            "instrument_name": instrument, "start_timestamp": end - hours * 3600000,
            "end_timestamp": end, "resolution": resolution,
        })
        columns = ["dt", "open", "high", "low", "close", "volume"]
        if not result or result.get("status") != "ok" or not result.get("ticks"):
            return pd.DataFrame(columns=columns)
        ticks = result["ticks"]
        frame = pd.DataFrame({
            "dt": pd.to_datetime(ticks, unit="ms", utc=True),
            **{key: result[key] for key in ("open", "high", "low", "close")},
            "volume": result.get("volume") or [0] * len(ticks),
        }).sort_values("dt").drop_duplicates("dt").reset_index(drop=True)
        numeric = frame[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce")
        frame[numeric.columns] = numeric
        valid = numeric.notna().all(axis=1) & (numeric[["open", "high", "low", "close"]] > 0).all(axis=1)
        valid &= numeric.apply(lambda s: s.map(math.isfinite)).all(axis=1)
        minutes = int(resolution)
        # Only COMPLETED bars. Live strategy calls use lag=0 where the source has lag=1,
        # i.e. the same confirmed raw decision that the backtest executes next bar.
        completed = frame["dt"] + pd.Timedelta(minutes=minutes) <= pd.Timestamp(end, unit="ms", tz="UTC")
        return frame.loc[valid & completed, columns].reset_index(drop=True)

    def account_summary(self, currency: str) -> dict:
        return self._private("get_account_summary", {"currency": currency}) or {}

    def positions(self, currency: str = "USDC") -> List[dict]:
        return self._private("get_positions", {"currency": currency, "kind": "future"}) or []

    def open_orders(self, instrument: Optional[str] = None) -> List[dict]:
        if instrument:
            return self._private("get_open_orders_by_instrument", {"instrument_name": instrument}) or []
        return self._private("get_open_orders", {"currency": "USDC", "kind": "future"}) or []

    def orders_by_label(self, label: str) -> List[dict]:
        return self._private("get_order_state_by_label", {"currency": "USDC", "label": label}) or []

    def cancel_order(self, order_id: str) -> dict:
        return self._private("cancel", {"order_id": order_id}) or {}

    @staticmethod
    def _fmt_amount(amount: float) -> str:
        return f"{float(amount):.10f}".rstrip("0").rstrip(".") or "0"

    def limit_ioc(self, instrument: str, direction: str, amount: float, price: float,
                  label: str, reduce_only: bool = False) -> dict:
        if direction not in ("buy", "sell") or amount <= 0 or price <= 0:
            raise ValueError("invalid IOC order")
        return self._private(direction, {
            "instrument_name": instrument, "amount": float(self._fmt_amount(amount)),
            "price": float(price), "type": "limit", "time_in_force": "immediate_or_cancel",
            "label": label[:32], "reduce_only": bool(reduce_only),
            "post_only": False, "valid_until": int(time.time() * 1000) + 20000,
        })

    def buy_market(self, instrument: str, amount: float, label: str = "zenith", reduce_only: bool = False) -> dict:
        return self._private("buy", {
            "instrument_name": instrument, "amount": float(self._fmt_amount(amount)),
            "type": "market", "label": label[:32], "reduce_only": bool(reduce_only),
        })

    def sell_market(self, instrument: str, amount: float, label: str = "zenith", reduce_only: bool = False) -> dict:
        return self._private("sell", {
            "instrument_name": instrument, "amount": float(self._fmt_amount(amount)),
            "type": "market", "label": label[:32], "reduce_only": bool(reduce_only),
        })

    def close_position(self, instrument: str) -> dict:
        return self._private("close_position", {"instrument_name": instrument, "type": "market"}) or {}
