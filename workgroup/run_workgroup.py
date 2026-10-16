import sys
sys.path.append("..")
from agents.StrategyAgent import StrategyAgent
from agents.ExecutionAgent import ExecutionAgent
from agents.RiskAgent import RiskAgent
from agents.TelegramAgent import TelegramAgent
from agents.MonitoringAgent import MonitoringAgent
from app.config import get_settings

settings = get_settings()
print("=== Workgroup Check ===")
print(f"Version: {settings.version}")
print(f"Capital: {settings.capital_usd} Effective assets: {settings.effective_assets}")

# Strategy
sa = StrategyAgent(settings)
print(f"StrategyAgent: {sa.check()}")

# Risk
ra = RiskAgent(settings)
print(f"RiskAgent: {ra.check()}")

# Telegram
ta = TelegramAgent()
try:
    print(f"TelegramAgent file check: {ta.check_file()}")
except Exception as e:
    print(f"TelegramAgent failed: {e}")

# Monitoring
ma = MonitoringAgent()
print(f"MonitoringAgent health: {ma.check_health()}")

# Execution
# Mock engine
class MockEngine: pass
ea = ExecutionAgent(settings, MockEngine())
print(f"ExecutionAgent status: {ea.status()}")

print("=== All Agents OK ===")
