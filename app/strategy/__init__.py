"""
Strategy Module - Simple exports
"""
try:
    from app.strategy.ema_strategy import ema_signal
    calculate_ema_signal = ema_signal
except:
    def ema_signal(df):
        return 0
    calculate_ema_signal = ema_signal

try:
    from app.strategy.zenith_apex_rebirth import zenith_apex_rebirth_signal, calculate_position_size_v025
except:
    def zenith_apex_rebirth_signal(df_1h, df_4h=None):
        return {"direction": 0, "strength": 0, "reasons": ["No strategy"]}
    def calculate_position_size_v025(entry, sl, capital, risk_pct=0.005, max_notional_pct=0.10, leverage=5):
        return 0, 0

__all__ = ["ema_signal", "calculate_ema_signal", "zenith_apex_rebirth_signal", "calculate_position_size_v025"]
