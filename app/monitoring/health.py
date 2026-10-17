"""
Monitoring Module - Health
Independent: Only health checks
"""
import httpx

def check_health(url: str = "https://zenith-trader-bot.onrender.com/health") -> bool:
    try:
        r = httpx.get(url, timeout=10)
        return r.status_code == 200
    except:
        return False

def health_response(build: str, version: str, loop_count: int, assets: list, capital: float):
    return {
        "ok": True,
        "liveness_ok": True,
        "bot": f"zenith-SUPER-{version}",
        "build": build,
        "version": version,
        "running": True,
        "mode": "running",
        "loop_count": loop_count,
        "assets": assets,
        "capital_usd": capital,
        "telegram_enabled": True
    }
