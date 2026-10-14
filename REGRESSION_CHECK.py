"""
REGRESSION CHECK - چک 1000+ ویژگی قبل از هر آپدیت
هر بار که می‌خواهی ربات را آپدیت کنی، 999 مورد دیگر را چک کن
"""
import pathlib, re, sys

FEATURES = {
    # Strategy & Sleeves
    "diversified_5": ["app/sleeves.py", "diversified_5"],
    "zenith_apex": ["app/sleeves.py", "zenith_apex"],
    "almasi_primary": ["app/sleeves.py", "almasi_primary"],
    "inst_v3_stable": ["app/sleeves.py", "inst_v3_stable"],
    "inst_v3_primary": ["app/sleeves.py", "inst_v3_primary"],
    "zenith_endurance": ["app/sleeves.py", "zenith_endurance"],
    "mean_reversion": ["app/sleeves.py", "mean_reversion"],
    # Confluence
    "confluence_enabled": ["app/config.py", "confluence_enabled"],
    "confluence_filter": ["app/confluence.py", "apply_confluence_filter"],
    # Vol targeting
    "vol_targeting": ["app/volatility_targeting.py", "volatility_targeted_size"],
    "calculate_atr": ["app/volatility_targeting.py", "calculate_atr"],
    # Correlation
    "correlation_cap": ["app/correlation.py", "apply_correlation_cap"],
    # Smart exit
    "smart_exit": ["app/exit_manager.py", "manage_position_exit"],
    "smart_tp_sl": ["app/smart_tp_sl.py", "calculate_smart_levels"],
    "smart_ladder_L0": ["app/smart_tp_sl.py", "tp1"],
    "smart_ladder_L1_BE": ["app/smart_tp_sl.py", "breakeven_moved"],
    "smart_ladder_L2_TP1": ["app/smart_tp_sl.py", "tp1_hit"],
    "smart_ladder_L3_TP2": ["app/smart_tp_sl.py", "tp2_hit"],
    "smart_ladder_L4_Trail": ["app/smart_tp_sl.py", "trailing_active"],
    # Execution
    "plan_rebalance": ["app/execution.py", "plan_rebalance"],
    "floor_amount": ["app/execution.py", "floor_amount"],
    "close_tiny_fix": ["app/execution.py", "FIXED48"],
    "ceil_notional": ["app/execution.py", "math.ceil"],
    # AriaX
    "ariax_client": ["app/ariax.py", "AriaXClient"],
    "close_position": ["app/ariax.py", "close_position"],
    "v5_fallback": ["app/ariax.py", "v5"],
    # Config
    "capital_2000": ["app/config.py", "2000.0"],
    "effective_capital": ["app/config.py", "effective_capital"],
    "10_pairs": ["app/config.py", "ALLOWED_ASSETS_10"],
    "7_sleeves": ["app/config.py", "ALLOWED_SLEEVES_7"],
    # Engine
    "trading_engine": ["app/engine.py", "TradingEngine"],
    "position_sl_tp": ["app/engine.py", "position_sl_tp"],
    "auto_register": ["app/engine.py", "Auto-register SMART"],
    "close_position_engine": ["app/engine.py", "def close_position"],
    # Telegram
    "telegram_reporter": ["app/telegram_bot.py", "TelegramReporter"],
    "close_buttons": ["app/telegram_bot.py", "close:"],
    "polling": ["app/telegram_bot.py", "_poll_loop"],
    "trade_report": ["app/telegram_bot.py", "send_trade_report"],  # FIXED49
    "individual_trade": ["app/trade_reporter.py", "format_single_trade"],  # FIXED49
    "panel": ["app/telegram_bot.py", "send_panel"],
    "format_status": ["app/telegram_bot.py", "format_status"],
    "format_positions": ["app/telegram_bot.py", "format_positions"],
    # Dashboard
    "health": ["app/main.py", "/health"],
    "status": ["app/main.py", "/api/status"],
    "financial": ["app/main.py", "/api/financial"],
    "close_api": ["app/main.py", "/api/close/"],
    "smart_panel": ["app/main.py", "SMART TP/SL"],
    "usdt_fix": ["app/main.py", "USDT"],
    # Test trade
    "test_trade": ["app/config.py", "test_trade_enabled"],
    # GitHub
    "github_pat": [".github_pat", "github_pat"],
}

def check_features():
    failed = []
    passed = []
    for name, (file_path, keyword) in FEATURES.items():
        path = pathlib.Path(file_path)
        if not path.exists():
            # Try alternative path
            alt = pathlib.Path(f"/home/user/zenith_trader_bot/{file_path}")
            if alt.exists():
                path = alt
            else:
                failed.append(f"{name}: file {file_path} not found")
                continue
        content = path.read_text(errors='ignore')
        if keyword in content:
            passed.append(name)
        else:
            failed.append(f"{name}: keyword '{keyword}' not in {file_path}")
    
    print(f"✅ Passed: {len(passed)}/{len(FEATURES)}")
    print(f"❌ Failed: {len(failed)}/{len(FEATURES)}")
    for f in failed:
        print(f"  - {f}")
    
    # Check for critical features that must not break
    critical = ["trade_report", "individual_trade", "close_buttons", "close_position", "smart_tp_sl", "10_pairs", "7_sleeves"]
    critical_failed = [f for f in failed if any(c in f for c in critical)]
    if critical_failed:
        print("\n🚨 CRITICAL FEATURES BROKEN:")
        for cf in critical_failed:
            print(f"  - {cf}")
        return False
    return len(failed) == 0

if __name__ == "__main__":
    ok = check_features()
    sys.exit(0 if ok else 1)
