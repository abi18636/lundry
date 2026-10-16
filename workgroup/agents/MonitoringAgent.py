"""
MonitoringAgent - Health and UptimeRobot
"""
import httpx
class MonitoringAgent:
    def check_health(self, url="https://zenith-trader-bot.onrender.com/health"):
        try:
            r = httpx.get(url, timeout=10)
            return r.status_code == 200
        except:
            return False
