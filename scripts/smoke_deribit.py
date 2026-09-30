#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.config import get_settings
from app.deribit import DeribitClient
from app.engine import TradingEngine

def main():
    s = get_settings()
    print("SUPER capital", s.capital_usd, "sleeves", s.enabled_sleeves)
    print("weights", s.sleeve_weights)
    c = DeribitClient(s.deribit_base_url, s.deribit_client_id, s.deribit_client_secret)
    print("USDC equity", c.account_summary("USDC").get("equity"))
    eng = TradingEngine(s)
    out = eng.once()
    print("n_sleeves", len(out.get("sleeves") or []))
    for sl in out.get("sleeves") or []:
        print(f"  {sl['id']:18s} w={sl['weight']:.2f} cap={sl['capital']:.1f}")
        for a, pa in (sl.get("per_asset") or {}).items():
            print(f"    {a}: side={pa.get('side')} ntl={pa.get('notional_usd'):.2f} detail={pa.get('detail')}")
    print("NET", {a: round(v.get("notional_usd",0),2) for a,v in (out.get("net_book") or {}).items()})
    print("ACTIONS", [{k:x.get(k) for k in ("asset","status","notional_usd","error")} for x in out.get("actions") or []])
    c.close()
    print("SMOKE_SUPER_OK")

if __name__ == "__main__":
    main()
