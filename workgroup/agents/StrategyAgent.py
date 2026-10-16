"""
StrategyAgent - v019 5 Fixes
Fix1 Maker/Post-Only, Fix2 Candle Close Gating $15, Fix3 Sizing Guard BTC/ETH, Fix4 Smart TP/SL, Fix5 Async 800ms
"""
class StrategyAgent:
    def __init__(self, settings):
        self.settings = settings
    def check(self):
        assert self.settings.post_only == True, "Fix1 failed"
        assert self.settings.rebalance_threshold_usd == 15.0, "Fix2 failed"
        assert self.settings.capital_usd < 1000 and len(self.settings.effective_assets) == 2, "Fix3 failed"
        assert self.settings.smart_tp_sl_enabled, "Fix4 failed"
        return True
