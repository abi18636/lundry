"""
Backward compatibility wrapper - imports from modular reporting
"""
from app.reporting.trade_reporter import format_single_trade, format_position, format_balance
__all__ = ["format_single_trade", "format_position", "format_balance"]
