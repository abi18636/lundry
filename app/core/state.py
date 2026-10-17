"""
Core Module - State Management
Independent: Only state persistence, no strategy dependency
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("state")

def utcnow():
    return datetime.now(timezone.utc).isoformat()

class BotState:
    def __init__(self, path: str = "state/bot_state.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.last_fill_at = None
        self.last_trade_at = {}
        self.position_sl_tp = {}
        self.events = []
        self.orders_log = []
        self.last_loop_at = None
        self.loop_count = 0
        self.load()

    def load(self):
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text())
                self.position_sl_tp = data.get("position_sl_tp", {})
                self.last_trade_at = data.get("last_trade_at", {})
        except Exception as e:
            log.warning(f"State load failed: {e}")

    def save(self):
        try:
            data = {
                "position_sl_tp": self.position_sl_tp,
                "last_trade_at": self.last_trade_at,
                "last_loop_at": utcnow(),
                "loop_count": self.loop_count
            }
            self.path.write_text(json.dumps(data, indent=2))
        except Exception as e:
            log.warning(f"State save failed: {e}")

    def push(self, level: str, msg: str):
        entry = {"ts": utcnow(), "level": level, "msg": msg}
        self.events.append(entry)
        self.events = self.events[-100:]
        log.info(f"[{level}] {msg}")
