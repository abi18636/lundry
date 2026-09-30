from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import httpx
import pandas as pd

log = logging.getLogger("deribit")


class DeribitClient:
    """Minimal Deribit REST client (testnet/mainnet). Secrets only via constructor/env."""

    def __init__(self, base_url: str, client_id: str, client_secret: str, timeout: float = 30.0):
        self.base = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: Optional[str] = None
        self._token_exp: float = 0.0
        self._http = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._http.close()

    # ── auth ──────────────────────────────────────────────────────────────
    def _auth(self, force: bool = False) -> str:
        if not force and self._token and time.time() < self._token_exp - 60:
            return self._token
        r = self._http.get(
            f"{self.base}/public/auth",
            params={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(f"auth error: {data['error']}")
        res = data["result"]
        self._token = res["access_token"]
        self._token_exp = time.time() + float(res.get("expires_in", 800))
        return self._token

    def _private(self, method: str, params: Optional[dict] = None) -> Any:
        token = self._auth()
        r = self._http.get(
            f"{self.base}/private/{method}",
            params=params or {},
            headers={"Authorization": f"Bearer {token}"},
        )
        if r.status_code == 401:
            token = self._auth(force=True)
            r = self._http.get(
                f"{self.base}/private/{method}",
                params=params or {},
                headers={"Authorization": f"Bearer {token}"},
            )
        data = r.json() if r.content else {}
        if "error" in data:
            err = data["error"]
            raise RuntimeError(f"private/{method}: {err}")
        if r.status_code >= 400:
            raise RuntimeError(f"private/{method} HTTP {r.status_code}: {r.text[:300]}")
        return data.get("result")

    def _public(self, method: str, params: Optional[dict] = None) -> Any:
        r = self._http.get(f"{self.base}/public/{method}", params=params or {})
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(f"public/{method}: {data['error']}")
        return data.get("result")

    # ── market data ───────────────────────────────────────────────────────
    def ticker(self, instrument: str) -> dict:
        return self._public("ticker", {"instrument_name": instrument})

    def market_state(self, instrument: str) -> str:
        """open | halted | closed | unknown"""
        try:
            return str((self.ticker(instrument) or {}).get("state") or "unknown")
        except Exception:
            return "unknown"


    def instrument(self, instrument: str) -> dict:
        return self._public("get_instrument", {"instrument_name": instrument})

    def candles(self, instrument: str, hours: int = 5000, resolution: str = "60") -> pd.DataFrame:
        """OHLCV via tradingview chart endpoint (1h). Deribit caps ~5000 bars."""
        end = int(time.time() * 1000)
        hours = int(min(max(hours, 48), 5000))
        start = end - int(hours * 3600 * 1000)
        res = self._public(
            "get_tradingview_chart_data",
            {
                "instrument_name": instrument,
                "start_timestamp": start,
                "end_timestamp": end,
                "resolution": resolution,
            },
        )
        if not res or res.get("status") != "ok" or not res.get("ticks"):
            return pd.DataFrame(columns=["dt", "open", "high", "low", "close", "volume"])
        vol = res.get("volume") or [0] * len(res["ticks"])
        df = pd.DataFrame(
            {
                "dt": pd.to_datetime(res["ticks"], unit="ms", utc=True),
                "open": res["open"],
                "high": res["high"],
                "low": res["low"],
                "close": res["close"],
                "volume": vol,
            }
        ).sort_values("dt").drop_duplicates("dt").reset_index(drop=True)
        return df

    # ── account / trading ─────────────────────────────────────────────────
    def account_summary(self, currency: str) -> dict:
        return self._private("get_account_summary", {"currency": currency})

    def positions(self, currency: str = "USDC") -> List[dict]:
        return self._private("get_positions", {"currency": currency}) or []

    def open_orders(self, instrument: Optional[str] = None) -> List[dict]:
        params = {}
        if instrument:
            params["instrument_name"] = instrument
            return self._private("get_open_orders_by_instrument", params) or []
        return self._private("get_open_orders", params) or []

    @staticmethod
    def _fmt_amount(amount: float) -> str:
        # avoid float junk; strip trailing zeros
        s = f"{float(amount):.10f}".rstrip("0").rstrip(".")
        return s if s else "0"

    def buy_market(self, instrument: str, amount: float, label: str = "zenith") -> dict:
        return self._private(
            "buy",
            {
                "instrument_name": instrument,
                "amount": self._fmt_amount(amount),
                "type": "market",
                "label": label[:32],
            },
        )

    def sell_market(self, instrument: str, amount: float, label: str = "zenith") -> dict:
        return self._private(
            "sell",
            {
                "instrument_name": instrument,
                "amount": self._fmt_amount(amount),
                "type": "market",
                "label": label[:32],
            },
        )

    def close_position(self, instrument: str) -> dict:
        return self._private("close_position", {"instrument_name": instrument, "type": "market"})
