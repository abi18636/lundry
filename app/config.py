"""
v018 UPGRADE - 4 New Features:
1. Increase trading pairs from 5 to 10 (or 12)
2. Activate test trade on BTC
3. Telegram close button for each individual trade
4. Version in telegram report
+ Keep all v017 7 layers
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import List

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
    lev_cap: float = Field(default=2.0, gt=0, le=5.0)
    long_only: bool = False
    # v018: Increase from 5 to 10 pairs - TOP 10 liquid
    assets: str = "BTC,ETH,SOL,AVAX,LINK,XRP,DOGE,ADA,DOT,LTC"

    # v018: 7 strategies with mean_reversion 20%
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

    # Execution
    cooldown_seconds: int = 60
    backoff_seconds: int = 60
    market_data_max_age_seconds: int = 180
    max_signal_no_trade_hours: float = 6.0
    candle_lookback_hours: int = 1000

    # SL/TP
    atr_stop_mult: float = 2.0
    atr_tp_mult: float = 4.0
    use_stop_loss: bool = True
    use_take_profit: bool = True
    sl_tp_enabled: bool = True

    # v017 Layers
    confluence_enabled: bool = True
    confluence_min_agreement: int = 3
    confluence_min_confidence: float = 0.55
    vol_targeting_enabled: bool = True
    target_vol_annual: float = 0.40
    correlation_enabled: bool = True
    correlation_default: float = 0.7
    correlation_cap_enabled: bool = True
    smart_exit_enabled: bool = True
    trailing_stop_atr: float = 1.5
    breakeven_atr: float = 1.0
    partial_take_atr: float = 2.0
    partial_take_fraction: float = 0.5
    meta_allocator_enabled: bool = True
    meta_lookback_trades: int = 20
    meta_min_weight: float = 0.03
    meta_max_weight: float = 0.35
    cost_filter_enabled: bool = True
    fee_bps: float = 10.0
    slippage_bps: float = 15.0
    min_edge_bps: float = 5.0
    regime_filter_enabled: bool = True
    adx_trending_threshold: float = 25.0
    adx_ranging_threshold: float = 20.0
    mom_threshold: float = 0.008

    # v018: Test Trade - ACTIVE on BTC
    test_trade_enabled: bool = True  # Was False, now True
    test_trade_asset: str = "BTC"  # BTC existing currency
    test_trade_hold_seconds: int = 60  # 60s exit
    test_trade_max_notional_usd: float = 10.0

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

    # v018: Version tracking
    version: str = "v018-10pairs-testtrade-telegram-close"

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
        # v018: 10 pairs TOP liquid
        return [x.strip().upper() for x in self.assets.split(",") if x.strip()]

    @property
    def enabled_sleeves(self) -> List[str]:
        return [x.strip() for x in self.sleeves_enabled.split(",") if x.strip()]

    @property
    def effective_sleeve_weights(self) -> str:
        return "diversified_5:0.15,zenith_apex:0.15,almasi_primary:0.15,inst_v3_stable:0.15,inst_v3_primary:0.10,zenith_endurance:0.10,mean_reversion:0.20"

    def instrument_for(self, asset: str) -> str:
        return f"{asset.upper()}USDT"

    @property
    def effective_test_trade_enabled(self) -> bool:
        # v018: Active
        env_enabled = os.getenv("TEST_TRADE_ENABLED")
        if env_enabled is not None:
            return env_enabled.lower() in ("true", "1", "yes")
        return self.test_trade_enabled


@lru_cache
def get_settings() -> Settings:
    return Settings()
