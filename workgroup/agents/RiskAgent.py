"""
RiskAgent - Conservative max DD 15%
"""
class RiskAgent:
    def __init__(self, settings):
        self.settings = settings
    def check(self):
        assert self.settings.max_drawdown_pct <= 0.15
        assert self.settings.lev_cap <= 3.0
        assert self.settings.min_notional_usd >= 5.0
        return True
