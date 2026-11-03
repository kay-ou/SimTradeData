"""Smoke test: verify SmartRouter works with real data sources.

Run manually: uv run python scripts/test_smart_router_live.py
"""

import sys
from datetime import datetime, timedelta

import pandas as pd


def main():
    from simtradedata.router import SmartRouter

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    test_symbol = "600000.SS"

    passed = 0
    failed = 0

    def check(name, result):
        nonlocal passed, failed
        if isinstance(result, pd.DataFrame):
            ok = not result.empty
            detail = f"{len(result)} rows" if ok else "EMPTY"
        elif isinstance(result, list):
            ok = len(result) > 0
            detail = f"{len(result)} items" if ok else "EMPTY"
        else:
            ok = result is not None
            detail = str(type(result))

        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"  [{status}] {name}: {detail}")

    print("=" * 60)
    print("SmartRouter Live Integration Test")
    print(f"Symbol: {test_symbol}  Range: {start_date} ~ {end_date}")
    print("=" * 60)

    with SmartRouter() as router:

        # 1. stock_list
        print("\n--- Stock List ---")
        try:
            stocks = router.get_stock_list(market="cn")
            check("get_stock_list(cn)", stocks)
        except Exception as e:
            print(f"  [FAIL] get_stock_list: {e}")
            failed += 1

        # 2. daily_bars
        print("\n--- Daily Bars ---")
        try:
            df = router.get_daily_bars(test_symbol, start_date, end_date)
            check("get_daily_bars", df)
            if not df.empty:
                print(f"         columns: {list(df.columns)}")
        except Exception as e:
            print(f"  [FAIL] get_daily_bars: {e}")
            failed += 1

        # 3. xdxr
        print("\n--- XDXR ---")
        try:
            df = router.get_xdxr(test_symbol)
            check("get_xdxr", df)
        except Exception as e:
            print(f"  [FAIL] get_xdxr: {e}")
            failed += 1

        # 5. trade_calendar
        print("\n--- Trade Calendar ---")
        try:
            df = router.get_trade_calendar(start_date, end_date)
            check("get_trade_calendar", df)
        except Exception as e:
            print(f"  [FAIL] get_trade_calendar: {e}")
            failed += 1

        # 6. index_data
        print("\n--- Index Data ---")
        try:
            df = router.get_index_data("000300.SS", start_date, end_date)
            check("get_index_data(CSI300)", df)
        except Exception as e:
            print(f"  [FAIL] get_index_data: {e}")
            failed += 1

        # 7. money_flow (eastmoney)
        print("\n--- Money Flow ---")
        try:
            df = router.get_money_flow(test_symbol, start_date, end_date)
            check("get_money_flow", df)
        except Exception as e:
            print(f"  [FAIL] get_money_flow: {e}")
            failed += 1

        # 8. lhb (eastmoney)
        print("\n--- LHB ---")
        try:
            df = router.get_lhb(start_date, end_date)
            check("get_lhb", df)
        except Exception as e:
            print(f"  [FAIL] get_lhb: {e}")
            failed += 1

        # 9. margin (eastmoney)
        print("\n--- Margin ---")
        try:
            df = router.get_margin(test_symbol, start_date, end_date)
            check("get_margin", df)
        except Exception as e:
            print(f"  [FAIL] get_margin: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
