"""
app/config.py - Zenith Bot v019 STABLE + v018 compatibility
Fixes v019:
- Environment-only secrets
- Maker/Post-only
- Noise filtering $15
- Dynamic Capital Allocation Guard (BTC/ETH if <1000)
- Smart TP/SL Fix 4 no zero-distance
- Async Engine Fix 5
Preserves v018 features for regression check
"""
from __future__ import annotations
import os
from functools import lru_cache
from typing import List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ALLOWED_ASSETS_FULL = ["BTC", "ETH", "SOL", "AVAX", "LINK", "XRP", "DOGE", "ADA", "DOT", "LTC"]
ALLOWED_ASSETS_SMALL_CAP = ["BTC", "ETH"]
ALLOWED_ASSETS_10 = ALLOWED_ASSETS_FULL
ALLOWED_SLEEVES_7 = ["trend", "mean_revert", "breakout", "volatility", "momentum", "confluence", "meta"]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # API Security
    ariax_base_url: str = Field(default="https://dryclean-app-1.onrender.com")
    ariax_api_key: str = Field(default="")
    ariax_api_secret: str = Field(default="")
    ariax_fallback_url: str = Field(default="https://dryclean-app-1.onrender.com")

    # v019 Execution Fix 1 & 2
    post_only: bool = True
    loop_seconds: int = 300
    trade_on_candle_close_only: bool = True
    
    # Financial Sizing Fix 3 - v019
    capital_usd: float = Field(default=200.0, gt=0)
    capital: float = Field(default=2000.0, gt=0)  # legacy compat
    lev_cap: float = Field(default=2.0, gt=0, le=3.0)
    rebalance_notional_usd: float = 15.0
    rebalance_threshold_usd: float = 15.0
    min_notional_usd: float = 5.0
    effective_capital: float = Field(default=2000.0)
    
    # Dynamic Asset Management
    assets: str = "BTC,ETH,SOL,AVAX,LINK,XRP,DOGE,ADA,DOT,LTC"
    
    # Smart TP/SL ATR multipliers (Fix 4) - v019
    smart_tp_sl_enabled: bool = True
    smart_sl_atr: float = 2.0
    smart_be_trigger_atr: float = 1.5
    smart_tp1_atr: float = 2.5
    smart_tp2_atr: float = 4.0
    smart_trailing_atr: float = 1.5

    # v018 preserved
    trading_enabled: bool = True
    dry_run: bool = True
    max_drawdown_pct: float = 0.15
    max_spread_bps: float = 30.0
    confluence_enabled: bool = True
    test_trade_enabled: bool = True
    floor_amount: float = 0.001

    # Telegram & Control
    dashboard_token: str = Field(default="")
    telegram_enabled: bool = True
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    state_path: str = "state/bot_state.json"
    version: str = "v019-STABLE-ASYNC-MAKER-FIXED56"

    @property
    def effective_assets(self) -> List[str]:
        """Fix 3: If capital is under $1000, enforce BTC & ETH only"""
        cap = self.capital_usd if self.capital_usd < 1000 else self.capital
        # Use capital_usd for v019 logic
        if self.capital_usd < 1000.0:
            return ALLOWED_ASSETS_SMALL_CAP.copy()
        return [a.strip() for a in self.assets.split(",") if a.strip() in ALLOWED_ASSETS_FULL]

    def instrument_for(self, asset: str) -> str:
        return f"{asset.upper()}USDT"

@lru_cache
def get_settings() -> Settings:
    return Settings()
