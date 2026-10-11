"""
v016 COMPANY FIXES - All 8 recommendations implemented
- Fix #4: Allow FLAT, MOM_THRESHOLD
- Fix #5: Regime detection
- Fix #6: Adaptive Donchian
- Fix #7: Adaptive cooldown
- Fix #8: Mean reversion 20% weight
- Fix #3: SL/TP real implementation
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Dict, List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # AriaX Exchange
    ariax_base_url: str = Field(default="https://dryclean-app-1.onrender.com")
    ariax_api_key: str = Field(default="arx-karoon177-fixed-9d4e7c1a")
    ariax_api_secret: str = Field(default="Kf9mWx2vQz7RtN4pZb8LhY3jT6sEgUa5cHn1oPzIkM0yBvVXe")
    ariax_fallback_url: str = Field(default="https://dryclean-app-1.onrender.com")

    # Trading
    trading_enabled: bool = True
    dry_run: bool = False
    capital_usd: float = Field(default=200.0, gt=0)
    lev_cap: float = Field(default=2.0, gt=0, le=5.0)  # Conservative 2x for 15% DD
    long_only: bool = False
    assets: str = "BTC,ETH,SOL,AVAX,LINK"

    # v016: 7 strategies with mean_reversion 20% weight for ranging market
    # Reduced trend weights to avoid bias
    sleeve_weights: str = "diversified_5:0.15,zenith_apex:0.15,almasi_primary:0.15,inst_v3_stable:0.15,inst_v3_primary:0.10,zenith_endurance:0.10,mean_reversion:0.20"
    sleeves_enabled: str = "diversified_5,zenith_apex,almasi_primary,inst_v3_stable,inst_v3_primary,zenith_endurance,mean_reversion"

    # Risk - Conservative
    loop_seconds: int = 60
    max_notional_usd: float = 200.0
    min_notional_usd: float = 5.0
    rebalance_notional_usd: float = 5.0
    max_drawdown_pct: float = 0.15
    max_spread_bps: float = 200.0
    max_slippage_bps: float = 100.0

    # Execution - v016 Adaptive
    cooldown_seconds: int = 60  # Base, will be adaptive x3 in high vol
    backoff_seconds: int = 60  # Per-asset, skip small/qty
    market_data_max_age_seconds: int = 180
    max_signal_no_trade_hours: float = 6.0
    candle_lookback_hours: int = 1000

    # SL/TP - v016 REAL implementation
    atr_stop_mult: float = 2.0
    atr_tp_mult: float = 4.0
    use_stop_loss: bool = True
    use_take_profit: bool = True
    sl_tp_enabled: bool = True

    # Regime filter
    regime_filter_enabled: bool = True
    adx_trending_threshold: float = 25.0
    adx_ranging_threshold: float = 20.0
    mom_threshold: float = 0.008  # 0.8% min momentum for signal validity

    # Ops
    ops_review_seconds: int = 3600
    dashboard_token: str = ""
    log_level: str = "INFO"
    state_path: str = "state/bot_state.json"

    # Telegram
    telegram_enabled: bool = True
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_bot_username: str = "TestTraid_bot"
    report_timezone: str = "Europe/Istanbul"

    @property
    def effective_capital(self) -> float:
        return 200.0

    @property
    def effective_lev_cap(self) -> float:
        env_lev = os.getenv("LEV_CAP")
        if env_lev:
            try:
                return min(float(env_lev), 5.0)
            except:
                pass
        return 2.0

    @property
    def asset_list(self) -> List[str]:
        return ["BTC", "ETH", "SOL", "AVAX", "LINK"]

    @property
    def enabled_sleeves(self) -> List[str]:
        return [x.strip() for x in self.sleeves_enabled.split(",") if x.strip()]

    @property
    def effective_sleeve_weights(self) -> str:
        return "diversified_5:0.15,zenith_apex:0.15,almasi_primary:0.15,inst_v3_stable:0.15,inst_v3_primary:0.10,zenith_endurance:0.10,mean_reversion:0.20"

    def instrument_for(self, asset: str) -> str:
        return f"{asset.upper()}USDT"

    @property
    def test_trade_enabled(self) -> bool:
        return False

    @property
    def test_trade_asset(self) -> str:
        return "BTC"

    @property
    def test_trade_hold_seconds(self) -> int:
        return 60

    @property
    def test_trade_max_notional_usd(self) -> float:
        return 10.0

    @property
    def effective_test_trade_enabled(self) -> bool:
        return False


@lru_cache
def get_settings() -> Settings:
    return Settings()
