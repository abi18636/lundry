"""
TelegramAgent - Stable with queue + to_thread
"""
class TelegramAgent:
    def check_file(self, path="app/telegram_bot.py"):
        content = open(path).read()
        assert content.count("def _api(") == 1, "duplicate _api"
        assert content.count("def send_trade_report") == 1, "duplicate trade_report"
        assert content.count("self.stats[\"trades_alerted\"] += 1\n                self.stats[\"failed\"] += 1") <= 1, "duplicate stats"
        return True
