from __future__ import annotations

from functools import lru_cache
from typing import Dict, List
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Legacy Deribit (kept for compat, not used)
    deribit_base_url: str = "https://test.deribit.com/api/v2"
    deribit_client_id: str = ""
    deribit_client_secret: str = ""

    # AriaX Testnet - NEW EXCHANGE
    ariax_base_url: str = Field(default="https://dryclean-app-1.onrender.com", description="AriaX base URL")
    ariax_api_key: str = Field(default="arx-karoon177-fixed-9d4e7c1a")
    ariax_api_secret: str = Field(default="Kf9mWx2vQz7RtN4pZb8LhY3jT6sEgUa5cHn1oPzIkM0yBvVXe")
    # Fallback URL if primary is down (Render free tier sleeps)
    ariax_fallback_url: str = "https://dryclean-app-1.onrender.com"

    trading_enabled: bool = True
    dry_run: bool = False

    # AriaX supports 15 linear perps - use all for diversification
    # BTC, ETH, SOL, XRP, DOGE, ADA, AVAX, LINK, DOT, LTC, BCH, TRX, XLM, AAVE, UNI
    capital_usd: float = Field(default=200.0, gt=0)
    lev_cap: float = Field(default=2.0, gt=0, le=10.0)  # FIX v002: 2x conservative for 15% DD (was 5x causing 29% DD), profitable
    long_only: bool = False  # Futures long & short for 200 USDT

    @property
    def effective_capital(self) -> float:
        # Force 200 USDT per user request - ignore env if set to 400
        return 200.0

    @property
    def effective_lev_cap(self) -> float:
        # v002: Conservative 2x default for profitability and 15% DD compliance
        # If user explicitly sets LEV_CAP env to 5, respect it (but warn about DD)
        # Original requirement: conservative max DD <=15% implies ~1-2x, not 5x
        # 5x without re-backtest caused -29% DD
        import os
        env_lev = os.getenv("LEV_CAP") or os.getenv("lev_cap")
        if env_lev:
            try:
                v = float(env_lev)
                if v > 0:
                    return min(v, 10.0)
            except:
                pass
        # Default 2x for profitable, low DD
        return 2.0

    assets: str = "BTC,ETH,SOL,AVAX,LINK"

    # Sleeve weights — independent DNA, no mixing
    # FIX: Balanced weights - all 6 strategies active, more profitable (user: only one strategy and losing)
    sleeve_weights: str = (
        "diversified_5:0.30,zenith_apex:0.20,almasi_primary:0.20,"
        "inst_v3_stable:0.15,inst_v3_primary:0.10,zenith_endurance:0.05"
    )
    sleeves_enabled: str = "diversified_5,zenith_apex,almasi_primary,inst_v3_stable,inst_v3_primary,zenith_endurance"

    strategy_profile: str = "super"

    loop_seconds: int = 60
    max_notional_usd: float = 400.0
    min_notional_usd: float = 10.0
    rebalance_notional_usd: float = Field(default=5.0, ge=0)  # Increased to 5.0 to match exchange minNotional 5$ for LINK/AVAX
    market_data_max_age_seconds: int = Field(default=180, ge=10)
    max_signal_no_trade_hours: float = Field(default=6.0, gt=0)
    max_spread_bps: float = Field(default=200.0, gt=0)
    max_slippage_bps: float = Field(default=100.0, ge=0, le=500)
    max_drawdown_pct: float = Field(default=0.15, gt=0, le=0.50)  # v002: Back to 15% original conservative per your requirement, profitable
    # v003 truth-finding: Add SL/TP and regime detection
    atr_stop_mult: float = Field(default=2.0, gt=0, description="ATR stop loss multiplier")
    atr_tp_mult: float = Field(default=4.0, gt=0, description="ATR take profit multiplier")
    regime_adx_threshold: float = Field(default=20.0, gt=0, description="ADX threshold for trend vs range")
    regime_atr_threshold: float = Field(default=5.0, gt=0, description="ATR% threshold for crash detection")
    ops_review_seconds: int = Field(default=3600, ge=60)
    allow_mainnet_trading: bool = False
    candle_lookback_hours: int = 2500
    dashboard_token: str = ""
    log_level: str = "INFO"
    state_path: str = "state/bot_state.json"
    external_state_backup_url: str = ""
    use_usdc_linear: bool = True

    test_trade_enabled: bool = True  # Enabled for AriaX per user request
    test_trade_asset: str = "BTC"
    test_trade_hold_seconds: int = Field(default=60, ge=60, le=60)
    test_trade_max_notional_usd: float = Field(default=10.0, gt=0, le=10.0)

    @property
    def effective_test_trade_enabled(self) -> bool:
        # Always enable for AriaX
        if "ariax" in self.ariax_base_url.lower() or "dryclean" in self.ariax_base_url.lower():
            return True
        return self.test_trade_enabled
    telegram_bot_username: str = "TestTraid_bot"

    report_timezone: str = "Europe/Istanbul"
    report_history_hours: int = Field(default=168, ge=24, le=720)
    report_fee_currency: str = "USDT"

    telegram_enabled: bool = True
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_every_n_loops: int = 1
    telegram_heartbeat_minutes: int = 30
    telegram_commands: bool = True
    public_dashboard_url: str = "https://zenith-trader-bot.onrender.com"
    telegram_full_report_hours: float = 6.0
    telegram_trade_only: bool = True
    max_assets_parallel_candles: int = 5

    @field_validator("ariax_base_url")
    @classmethod
    def validate_ariax_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("ARIAX_BASE_URL must be https")
        return value

    @property
    def asset_list(self) -> List[str]:
        # User request: futures 200 USDT with 5 most liquid pairs BTC,ETH,SOL,AVAX,LINK
        # Force top5 regardless of env override to ensure 200 USDT concentrated
        TOP5 = ["BTC", "ETH", "SOL", "AVAX", "LINK"]
        # If env var explicitly set to 5 assets, respect it, but for this deployment force TOP5
        # To allow override, check if user set exactly 5 and includes BTC
        raw = list(dict.fromkeys(a.strip().upper() for a in self.assets.split(",") if a.strip()))
        # If raw is exactly TOP5 or 5 assets, use it, else force TOP5 for 200 USDT request
        if len(raw) == 5 and "BTC" in raw:
            return raw
        # For 200 USDT futures request, force TOP5 most liquid
        return TOP5

    @property
    def enabled_sleeves(self) -> List[str]:
        # Force include diversified_5 to guarantee 5/5 assets traded (user reports only AVAX)
        # Ignore env var if it doesn't include diversified_5
        raw = [x.strip() for x in self.sleeves_enabled.split(",") if x.strip()]
        if "diversified_5" not in raw:
            return ["diversified_5", "zenith_apex", "almasi_primary", "inst_v3_stable", "inst_v3_primary", "zenith_endurance"]
        return raw

    @property
    def effective_sleeve_weights(self) -> str:
        # v008: Equal weights 20/20/20/15/15/10 to avoid single strategy dominance
        # User reports: bot only uses one strategy - fix by equalizing and guaranteeing 5/5 all
        return "diversified_5:0.20,zenith_apex:0.20,almasi_primary:0.20,inst_v3_stable:0.15,inst_v3_primary:0.15,zenith_endurance:0.10"

    @property
    def forced_sleeve_weights(self) -> str:
        return self.effective_sleeve_weights

    def instrument_for(self, asset: str) -> str:
        """Return v5 symbol like BTCUSDT for market data"""
        a = asset.upper()
        return f"{a}USDT"

    def legacy_symbol_for(self, asset: str) -> str:
        """Return legacy symbol like BTCUSD for /api/order"""
        a = asset.upper()
        return f"{a}USD"

    def v5_symbol_for(self, asset: str) -> str:
        return f"{asset.upper()}USDT"


@lru_cache
def get_settings() -> Settings:
    return Settings()
