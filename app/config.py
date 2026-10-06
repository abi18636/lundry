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
    ariax_api_key: str = Field(default="arx-fca61a10ad29397189fcc749bac3285f")
    ariax_api_secret: str = Field(default="GpkapEL6kntBB9iA7d_w5Fi5gyuB5rh0qMR8eB1zzj8")
    # Fallback URL if primary is down (Render free tier sleeps)
    ariax_fallback_url: str = "https://dryclean-app-1.onrender.com"

    trading_enabled: bool = True
    dry_run: bool = False

    # AriaX supports 15 linear perps - use all for diversification
    # BTC, ETH, SOL, XRP, DOGE, ADA, AVAX, LINK, DOT, LTC, BCH, TRX, XLM, AAVE, UNI
    capital_usd: float = Field(default=200.0, gt=0)
    lev_cap: float = Field(default=5.0, gt=0, le=10.0)
    long_only: bool = False  # Futures long & short for 200 USDT
    assets: str = "BTC,ETH,SOL,AVAX,LINK"

    # Sleeve weights — independent DNA, no mixing
    sleeve_weights: str = (
        "zenith_apex:0.25,almasi_primary:0.25,"
        "inst_v3_stable:0.30,inst_v3_primary:0.10,zenith_endurance:0.10"
    )
    sleeves_enabled: str = "zenith_apex,almasi_primary,inst_v3_stable,inst_v3_primary,zenith_endurance"

    strategy_profile: str = "super"

    loop_seconds: int = 60
    max_notional_usd: float = 400.0
    min_notional_usd: float = 10.0
    rebalance_notional_usd: float = Field(default=1.0, ge=0)
    market_data_max_age_seconds: int = Field(default=180, ge=10)
    max_signal_no_trade_hours: float = Field(default=6.0, gt=0)
    max_spread_bps: float = Field(default=200.0, gt=0)
    max_slippage_bps: float = Field(default=100.0, ge=0, le=500)
    max_drawdown_pct: float = Field(default=0.15, gt=0, le=0.50)
    ops_review_seconds: int = Field(default=3600, ge=60)
    allow_mainnet_trading: bool = False
    candle_lookback_hours: int = 2500
    dashboard_token: str = ""
    log_level: str = "INFO"
    state_path: str = "state/bot_state.json"
    external_state_backup_url: str = ""
    use_usdc_linear: bool = True

    test_trade_enabled: bool = False
    test_trade_asset: str = "BTC"
    test_trade_hold_seconds: int = Field(default=60, ge=60, le=60)
    test_trade_max_notional_usd: float = Field(default=10.0, gt=0, le=10.0)
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
        # AriaX 15 liquid perps
        ARIAX_15 = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT", "LTC", "BCH", "TRX", "XLM", "AAVE", "UNI"]
        raw = list(dict.fromkeys(a.strip().upper() for a in self.assets.split(",") if a.strip()))
        # Filter to only supported
        filtered = [a for a in raw if a in ARIAX_15]
        return filtered if filtered else ARIAX_15

    @property
    def enabled_sleeves(self) -> List[str]:
        return [x.strip() for x in self.sleeves_enabled.split(",") if x.strip()]

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
