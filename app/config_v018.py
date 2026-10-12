"""
v018 FIXED28-HARDCODED-ALL"""
from __future__ import annotations
from functools import lru_cache
from typing import List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ALLOWED_ASSETS_10 = ["BTC", "ETH", "SOL", "AVAX", "LINK", "XRP", "DOGE", "ADA", "DOT", "LTC"]
ALLOWED_SLEEVES_7 = ["diversified_5", "zenith_apex", "almasi_primary", "inst_v3_stable", "inst_v3_primary", "zenith_endurance", "mean_reversion"]
ALLOWED_WEIGHTS = "diversified_5:0.15,zenith_apex:0.15,almasi_primary:0.15,inst_v3_stable:0.15,inst_v3_primary:0.10,zenith_endurance:0.10,mean_reversion:0.20"

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    ariax_base_url: str = Field(default="https://dryclean-app-1.onrender.com")
    ariax_api_key: str = Field(default="arx-karoon177-fixed-9d4e7c1a")
    ariax_api_secret: str = Field(default="Kf9mWx2vQz7RtN4pZb8LhY3jT6sEgUa5cHn1oPzIkM0yBvVXe")
    ariax_fallback_url: str = Field(default="https://dryclean-app-1.onrender.com")
    trading_enabled: bool = True
    dry_run: bool = False
    capital_usd: float = Field(default=200.0, gt=0)
    lev_cap: float = Field(default=2.0, gt=0, le=5.0)
    long_only: bool = False
    assets: str = "BTC,ETH,SOL,AVAX,LINK,XRP,DOGE,ADA,DOT,LTC"
    sleeve_weights: str = "diversified_5:0.15,zenith_apex:0.15,almasi_primary:0.15,inst_v3_stable:0.15,inst_v3_primary:0.10,zenith_endurance:0.10,mean_reversion:0.20"
    sleeves_enabled: str = "diversified_5,zenith_apex,almasi_primary,inst_v3_stable,inst_v3_primary,zenith_endurance,mean_reversion"
    loop_seconds: int = 60
    max_notional_usd: float = 200.0
    min_notional_usd: float = 5.0
    rebalance_notional_usd: float = 1.0
    max_drawdown_pct: float = 0.15
    max_spread_bps: float = 200.0
    max_slippage_bps: float = 100.0
    cooldown_seconds: int = 60
    backoff_seconds: int = 60
    market_data_max_age_seconds: int = 180
    max_signal_no_trade_hours: float = 6.0
    candle_lookback_hours: int = 1000
    atr_stop_mult: float = 2.0
    atr_tp_mult: float = 4.0
    use_stop_loss: bool = True
    use_take_profit: bool = True
    sl_tp_enabled: bool = True
    confluence_enabled: bool = True
    confluence_min_agreement: int = 2
    confluence_min_confidence: float = 0.50
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
    fee_bps: float = 5.0
    slippage_bps: float = 10.0
    min_edge_bps: float = 2.0
    regime_filter_enabled: bool = True
    adx_trending_threshold: float = 25.0
    adx_ranging_threshold: float = 20.0
    mom_threshold: float = 0.008
    test_trade_enabled: bool = True
    test_trade_asset: str = "BTC"
    test_trade_hold_seconds: int = 60
    test_trade_max_notional_usd: float = 10.0
    ops_review_seconds: int = 3600
    dashboard_token: str = ""
    log_level: str = "INFO"
    state_path: str = "state/bot_state.json"
    telegram_enabled: bool = True
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_bot_username: str = "TestTraid_bot"
    report_timezone: str = "Europe/Istanbul"
    version: str = "v018-10pairs-7sleeves-testtrade-close-FIXED28-HARDCODED-ALL"

    @property
    def effective_capital(self) -> float:
        return 200.0
    @property
    def effective_lev_cap(self) -> float:
        return 2.0
    @property
    def asset_list(self) -> List[str]:
        return ALLOWED_ASSETS_10.copy()
    @property
    def enabled_sleeves(self) -> List[str]:
        return ALLOWED_SLEEVES_7.copy()
    @property
    def effective_sleeve_weights(self) -> str:
        return ALLOWED_WEIGHTS
    def instrument_for(self, asset: str) -> str:
        return f"{asset.upper()}USDT"
    @property
    def effective_test_trade_enabled(self) -> bool:
        return True

@lru_cache
def get_settings() -> Settings:
    return Settings()
