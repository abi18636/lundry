from __future__ import annotations
from functools import lru_cache
from typing import List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
ALLOWED_ASSETS_FULL = ["BTC", "ETH", "SOL", "AVAX", "LINK", "XRP", "DOGE", "ADA", "DOT", "LTC"]
ALLOWED_ASSETS_SMALL_CAP = ["BTC", "ETH"]
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    ariax_base_url: str = Field(default="https://dryclean-app-1.onrender.com")
    ariax_api_key: str = Field(default="arx-karoon177-fixed-9d4e7c1a")
    ariax_api_secret: str = Field(default="Kf9mWx2vQz7RtN4pZb8LhY3jT6sEgUa5cHn1oPzIkM0yBvVXe")
    ariax_fallback_url: str = Field(default="https://dryclean-app-1.onrender.com")
    deribit_base_url: str = Field(default="https://test.deribit.com")
    post_only: bool = True
    _loop_seconds: int = 90
    _trade_on_candle_close_only: bool = True
    _capital_usd: float = 400.0
    _capital: float = 400.0
    _lev_cap: float = 1.0
    _rebalance_notional_usd: float = 25.0
    _rebalance_threshold_usd: float = 25.0
    loop_seconds: int = Field(default=90)
    trade_on_candle_close_only: bool = Field(default=True)
    capital_usd: float = Field(default=400.0, gt=0)
    capital: float = Field(default=400.0, gt=0)
    lev_cap: float = Field(default=1.0, gt=0, le=3.0)
    rebalance_notional_usd: float = Field(default=25.0)
    rebalance_threshold_usd: float = Field(default=25.0)
    min_notional_usd: float = 5.0
    effective_capital: float = Field(default=400.0)
    assets: str = "BTC,ETH"
    smart_tp_sl_enabled: bool = True
    smart_sl_atr: float = 2.0
    smart_be_trigger_atr: float = 1.5
    smart_tp1_atr: float = 2.5
    smart_tp2_atr: float = 4.0
    smart_trailing_atr: float = 1.5
    trading_enabled: bool = True
    dry_run: bool = False
    max_drawdown_pct: float = 0.15
    max_spread_bps: float = 30.0
    confluence_enabled: bool = True
    test_trade_enabled: bool = True
    floor_amount: float = 0.001
    dashboard_token: str = Field(default="")
    telegram_enabled: bool = True
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    state_path: str = "state/bot_state.json"
    version: str = "v021-MODULAR-12PHASES-FIXED-FORCE-BTC-ETH"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Phase11: FORCE values regardless of ENV to fix Render dashboard override
        object.__setattr__(self, 'capital_usd', 400.0)
        object.__setattr__(self, 'capital', 400.0)
        object.__setattr__(self, 'lev_cap', 1.0)
        object.__setattr__(self, 'rebalance_notional_usd', 25.0)
        object.__setattr__(self, 'rebalance_threshold_usd', 25.0)
        object.__setattr__(self, 'loop_seconds', 90)
        object.__setattr__(self, 'trade_on_candle_close_only', True)
        object.__setattr__(self, 'assets', "BTC,ETH")

    @property
    def effective_assets(self) -> List[str]:
        return ["BTC", "ETH"]
    @property
    def asset_list(self) -> List[str]:
        return self.effective_assets
    @property
    def effective_test_trade_enabled(self) -> bool:
        return True
    @property
    def effective_dry_run(self) -> bool:
        if not self.ariax_api_key or not self.ariax_api_secret:
            return True
        return self.dry_run
    def instrument_for(self, asset: str) -> str:
        return f"{asset.upper()}USDT"
    def order_symbol_for(self, asset: str) -> str:
        return f"{asset.upper()}USD"
    def asset_from_symbol(self, symbol: str) -> str:
        s = str(symbol or "").upper()
        return s.replace("USDT", "").replace("USD", "").replace("/", "").replace("-", "")
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
    def market_data_max_age_seconds(self) -> float:
        return 10.0
    @property
    def max_slippage_bps(self) -> float:
        return 30.0
@lru_cache
def get_settings() -> Settings:
    return Settings()
