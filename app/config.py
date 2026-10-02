from __future__ import annotations

from functools import lru_cache
from typing import Dict, List
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    deribit_base_url: str = "https://test.deribit.com/api/v2"
    deribit_client_id: str = ""
    deribit_client_secret: str = ""

    trading_enabled: bool = True
    dry_run: bool = False

    # SUPER BOT total capital across independent sleeves — increased to 400 for more transparent P&L/fees
    capital_usd: float = Field(default=400.0, gt=0)
    lev_cap: float = Field(default=1.0, gt=0, le=1.0)
    long_only: bool = True
    # Blacklisted to 7 liquid assets on Deribit testnet (Data Agent finding: only 7/25 have two-sided liquidity)
    assets: str = "BTC,ETH,SOL,DOGE,AVAX,APT,TRX"

    # Sleeve weights — independent DNA, no mixing
    # Format: id:weight,id:weight,...
    sleeve_weights: str = (
        "zenith_apex:0.25,almasi_primary:0.25,"
        "inst_v3_stable:0.30,inst_v3_primary:0.10,zenith_endurance:0.10"
    )
    sleeves_enabled: str = "zenith_apex,almasi_primary,inst_v3_stable,inst_v3_primary,zenith_endurance"

    # legacy single-profile kept for back-compat (ignored if sleeves_enabled set)
    strategy_profile: str = "super"

    loop_seconds: int = 60
    max_notional_usd: float = 400.0  # engine also caps at allocated equity * lev_cap — increased to match capital 400
    min_notional_usd: float = 10.0  # legacy only; NEVER forces target size upward
    # Execution tuning for testnet low liquidity (Execution Agent)
    rebalance_notional_usd: float = Field(default=0.5, ge=0)  # reduced from 1.0 to 0.5 for less skip_small
    market_data_max_age_seconds: int = Field(default=180, ge=10)
    max_signal_no_trade_hours: float = Field(default=6.0, gt=0)
    max_spread_bps: float = Field(default=200.0, gt=0)  # increased from 100 to 200 for testnet wide spreads
    max_slippage_bps: float = Field(default=100.0, ge=0, le=200)  # increased from 50 to 100 for testnet slippage
    max_drawdown_pct: float = Field(default=0.15, gt=0, le=0.15)
    ops_review_seconds: int = Field(default=3600, ge=60)
    allow_mainnet_trading: bool = False
    candle_lookback_hours: int = 2500
    dashboard_token: str = ""
    log_level: str = "INFO"
    state_path: str = "state/bot_state.json"
    external_state_backup_url: str = ""  # optional external backup webhook/storage URL for free-tier ephemeral FS
    use_usdc_linear: bool = True

    # Owner-confirmed manual minimum-lot test; testnet only, never a strategy signal.
    test_trade_enabled: bool = True
    test_trade_asset: str = "BTC"
    test_trade_hold_seconds: int = Field(default=60, ge=60, le=60)
    test_trade_max_notional_usd: float = Field(default=10.0, gt=0, le=10.0)
    telegram_bot_username: str = "TestTraid_bot"

    # Financial reporting — separate read-only accounting
    report_timezone: str = "Europe/Istanbul"
    report_history_hours: int = Field(default=168, ge=24, le=720)
    report_fee_currency: str = "USDC"

    # Telegram ops (token/chat via env only)
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

    @field_validator("deribit_base_url")
    @classmethod
    def trusted_exchange_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        parsed = urlparse(value)
        if (parsed.scheme != "https" or parsed.hostname not in {"test.deribit.com", "www.deribit.com", "deribit.com"}
                or parsed.path != "/api/v2" or parsed.query or parsed.fragment or parsed.username
                or parsed.port not in (None, 443)):
            raise ValueError("DERIBIT_BASE_URL must be an official HTTPS Deribit /api/v2 endpoint")
        return value

    @property
    def asset_list(self) -> List[str]:
        return list(dict.fromkeys(a.strip().upper() for a in self.assets.split(",") if a.strip()))

    @property
    def enabled_sleeves(self) -> List[str]:
        return [x.strip() for x in self.sleeves_enabled.split(",") if x.strip()]

    def instrument_for(self, asset: str) -> str:
        a = asset.upper()
        if self.use_usdc_linear:
            return {
                "BTC": "BTC_USDC-PERPETUAL",
                "ETH": "ETH_USDC-PERPETUAL",
                "SOL": "SOL_USDC-PERPETUAL",
            }.get(a, f"{a}_USDC-PERPETUAL")
        return {"BTC": "BTC-PERPETUAL", "ETH": "ETH-PERPETUAL"}.get(a, f"{a}-PERPETUAL")


@lru_cache
def get_settings() -> Settings:
    return Settings()
