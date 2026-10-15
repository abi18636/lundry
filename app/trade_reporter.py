def format_single_trade(action: dict, version: str = "v019") -> str:
    try:
        asset = str(action.get("asset","?"))[:10]
        direction = str(action.get("direction","?"))[:10]
        amount = action.get("filled_amount") or action.get("amount",0)
        price = action.get("price") or 0
        try:
            amount_str = f"{float(amount):.4f}"
        except:
            amount_str = str(amount)[:10]
        try:
            price_str = f"{float(price):.2f}"
        except:
            price_str = str(price)[:10]
        return f"TRADE {version} {asset} {direction} {amount_str} @ {price_str}"
    except:
        return f"TRADE {action.get('asset','?')} {action.get('direction','?')}"
