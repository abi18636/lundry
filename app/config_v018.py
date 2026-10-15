"""
app/config.py - Zenith Bot v019 Fixed Configuration
Fixes applied:
- Environment-only secrets (No hardcoded API keys)
- Maker/Post-only order routing
- Noise filtering threshold increased to $15
- Dynamic Capital Allocation Guard
"""
from __future__ import annotations
import os
from functools import lru_cache
from typing import List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ALLOWED_ASSETS_FULL = ["BTC", "ETH", "SOL", "AVAX", "LINK", "XRP", "DOGE", "ADA", "DOT", "LTC"]
ALLOWED_ASSETS_SMALL_CAP = ["BTC", "ETH"]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # API Security: Fallback to empty string, forces reading from .env
    ariax_base_url: str = Field(default="https://dryclean-app-1.onrender.com")
    ariax_api_key: str = Field(default="")
    ariax_api_secret: str = Field(default="")
    ariax_fallback_url: str = Field(default="https://dryclean-app-1.onrender.com")

    # Execution Fix 1 & 2: Maker orders and hourly candle boundary
    post_only: bool = True
    loop_seconds: int = 300  # Evaluate every 5 minutes (acts on hourly closes)
    trade_on_candle_close_only: bool = True
    
    # Financial Sizing Fix 3
    capital_usd: float = Field(default=200.0, gt=0)
    lev_cap: float = Field(default=2.0, gt=0, le=3.0)
    rebalance_notional_usd: float = 15.0  # Increased from $1 to eliminate micro-churn
    min_notional_usd: float = 5.0
    
    # Dynamic Asset Management
    assets: str = "BTC,ETH,SOL,AVAX,LINK,XRP,DOGE,ADA,DOT,LTC"
    
    # Smart TP/SL ATR multipliers (Fix 4)
    smart_tp_sl_enabled: bool = True
    smart_sl_atr: float = 2.0
    smart_be_trigger_atr: float = 1.5   # Move to BE only after 1.5x ATR
    smart_tp1_atr: float = 2.5          # Increased from 1.5
    smart_tp2_atr: float = 4.0
    smart_trailing_atr: float = 1.5

    trading_enabled: bool = True
    dry_run: bool = False
    max_drawdown_pct: float = 0.15
    max_spread_bps: float = 30.0        # Reduced from 200 bps to reject illiquid books

    # Telegram & Control
    dashboard_token: str = Field(default="")
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    state_path: str = "state/bot_state.json"
    version: str = "v019-STABLE-ASYNC-MAKER-FIXED56"

    @property
    def effective_assets(self) -> List[str]:
        """Fix 3: If capital is under $1000, enforce BTC & ETH only to prevent lot errors"""
        if self.capital_usd < 1000.0:
            return ALLOWED_ASSETS_SMALL_CAP.copy()
        return [a.strip() for a in self.assets.split(",") if a.strip() in ALLOWED_ASSETS_FULL]

    def instrument_for(self, asset: str) -> str:
        return f"{asset.upper()}USDT"

@lru_cache
def get_settings() -> Settings:
    return Settings()
