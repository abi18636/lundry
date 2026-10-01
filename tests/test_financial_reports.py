import time
from pathlib import Path

from app.config import Settings
from app.engine import TradingEngine
from app.financial_reports import FinancialJournal, FinancialReporting, dec, ZERO
import app.engine as engine_module


class FakeVenueFinancial:
    last_testnet = True
    def __init__(self):
        self.equity = 1000.0
        self.available = 1000.0
        self.positions_data = []
        self.trades = []
        self.logs = []
    def account_summary(self, cur):
        return {"equity": self.equity, "balance": self.equity, "available_funds": self.available}
    def positions(self, cur):
        return self.positions_data
    def user_trades_window(self, start, end):
        return self.trades, False
    def transaction_window(self, start, end):
        return self.logs, True
    def trades_by_order(self, oid):
        return [t for t in self.trades if t.get("order_id") == oid]
    def close(self): pass


def test_fifo_pnl_with_fee_allocation(tmp_path):
    journal = FinancialJournal(tmp_path / "fin.json")
    # Open 1 BTC @ 100, fee 0.1
    journal.register({"order_id": "o1", "instrument": "BTC_USDC-PERPETUAL", "asset": "BTC", "direction": "buy",
                      "filled_amount": 1, "price": 100, "fee": 0.1, "fee_known": True, "fee_currencies": ["USDC"],
                      "trade_ids": ["t1"], "label": "sup_btc_1", "owned": True, "status": "bought", "timestamp": 1000},
                     before={"quantity": 0})
    # Close 0.4 @ 110, fee 0.04
    journal.register({"order_id": "o2", "instrument": "BTC_USDC-PERPETUAL", "asset": "BTC", "direction": "sell",
                      "filled_amount": 0.4, "price": 110, "fee": 0.04, "fee_known": True, "fee_currencies": ["USDC"],
                      "trade_ids": ["t2"], "label": "sup_btc_2", "owned": True, "status": "closed", "reduce_only": True, "timestamp": 2000},
                     before={"quantity": 1, "average_price": 100})
    events = journal.events()
    assert len(events) == 2
    open_ev = events[0]
    assert open_ev["kind"] == "open"
    assert open_ev["quantity_after"] == 1
    close_ev = events[1]
    assert close_ev["kind"] == "reduce"
    assert close_ev["closed_quantity"] == 0.4
    # gross = 0.4*(110-100)=4, fee alloc 0.04, exit fee 0.04 => net 3.92
    assert close_ev["realized_gross_usdc"] == 4.0
    assert close_ev["entry_fee_allocated_usdc"] == 0.04
    assert close_ev["exit_fee_usdc"] == 0.04
    assert close_ev["net_price_fees_usdc"] == 3.92


def test_missing_fee_stays_unknown_not_zero(tmp_path):
    journal = FinancialJournal(tmp_path / "fin.json")
    journal.register({"order_id": "o1", "instrument": "ETH_USDC-PERPETUAL", "asset": "ETH", "direction": "buy",
                      "filled_amount": 1, "price": 100, "fee_currencies": [], "trade_ids": [], "label": "sup_eth_1", "owned": True, "timestamp": 1000},
                     before={"quantity": 0})
    journal.register({"order_id": "o2", "instrument": "ETH_USDC-PERPETUAL", "asset": "ETH", "direction": "sell",
                      "filled_amount": 1, "price": 110, "fee_currencies": [], "trade_ids": [], "label": "sup_eth_2", "owned": True, "timestamp": 2000},
                     before={"quantity": 1, "average_price": 100})
    ev = journal.events()[1]
    assert ev["realized_gross_usdc"] == 10.0
    assert ev["net_price_fees_usdc"] is None
    assert ev["pnl_quality"] == "incomplete_entry_or_fee_evidence"


def test_unknown_basis_close_is_flagged(tmp_path):
    journal = FinancialJournal(tmp_path / "fin.json")
    journal.register({"order_id": "o_close", "instrument": "BTC_USDC-PERPETUAL", "asset": "BTC", "direction": "sell",
                      "filled_amount": 0.1, "price": 80000, "fee": 0.01, "fee_known": True, "fee_currencies": ["USDC"],
                      "trade_ids": ["t1"], "label": "sup_btc_close", "owned": True, "reduce_only": True, "timestamp": 1000},
                     before={"quantity": 0})
    ev = journal.events()[0]
    assert ev["kind"] == "close_unknown_basis"
    assert ev["closed_quantity"] == 0.1
    assert ev["realized_gross_usdc"] is None


def test_financial_reporting_snapshot_includes_capital(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_module, 'get_reporter', lambda: None)
    settings = Settings(_env_file=None, assets="BTC", state_path=str(tmp_path / "state.json"))
    engine = TradingEngine(settings)
    venue = FakeVenueFinancial()
    venue.positions_data = [{"instrument_name": "BTC_USDC-PERPETUAL", "size_currency": 0.001, "direction": "buy", "mark_price": 80000, "average_price": 79000, "floating_profit_loss": 1.0, "initial_margin": 0.08}]
    engine.client = venue
    fr = FinancialReporting(engine)
    fr._read_client = venue
    fr.live = {"account": {"equity": 99982.0, "balance": 99982.0, "available": 99965.0}, "positions": venue.positions_data, "observed_at": "2026-10-01T00:00:00+00:00"}
    # Simulate a filled order
    fr.journal.register({"order_id": "USDC-1", "instrument": "BTC_USDC-PERPETUAL", "asset": "BTC", "direction": "buy",
                         "filled_amount": 0.001, "price": 79000, "fee": 0.0395, "fee_known": True, "fee_currencies": ["USDC"],
                         "trade_ids": ["USDC-1"], "label": "sup_btc_test", "owned": True, "timestamp": int(time.time()*1000)-5000},
                        before={"quantity": 0})
    snap = fr.snapshot()
    assert snap["allocated_capital_usdc"] == 100.0
    assert snap["account"]["equity"] == 99982.0
    assert snap["open_notional_usdc"] > 0
    assert snap["execution_count"] == 1
