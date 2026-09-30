from __future__ import annotations

from functools import lru_cache
from typing import Dict, List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    deribit_base_url: str = "https://test.deribit.com/api/v2"
    deribit_client_id: str = ""
    deribit_client_secret: str = ""

    trading_enabled: bool = True
    dry_run: bool = False

    # SUPER BOT total capital across independent sleeves
    capital_usd: float = 100.0
    lev_cap: float = 1.0
    long_only: bool = True
    assets: str = "BTC,ETH,SOL,XRP,DOGE,BNB,ADA,AVAX,LINK,DOT,LTC,BCH,UNI,ATOM,NEAR,APT,ARB,OP,SUI,FIL,INJ,SEI,WLD,TRX,TAO"

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
    max_notional_usd: float = 500.0  # hard cap total bot notional
    min_notional_usd: float = 10.0
    candle_lookback_hours: int = 2500
    dashboard_token: str = ""
    log_level: str = "INFO"
    state_path: str = "state/bot_state.json"
    use_usdc_linear: bool = True

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

    @property
    def asset_list(self) -> List[str]:
        return [a.strip().upper() for a in self.assets.split(",") if a.strip()]

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
