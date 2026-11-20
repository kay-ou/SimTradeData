"""PTrade's confirmed Level-1 snapshot subset; quantities are shares/units."""

import math


def snapshot_from_quotes(quotes):
    result = {}
    prices = {
        "price": "last_px", "last_close": "preclose_px", "open": "open_px",
        "high": "high_px", "low": "low_px", "amount": "business_balance",
    }
    quantities = {
        "vol": "business_amount", "s_vol": "business_amount_in",
        "b_vol": "business_amount_out",
    }
    for row in quotes.to_dict("records"):
        snapshot = {}
        for source, target in prices.items():
            value = row.get(source)
            if value is not None and math.isfinite(float(value)):
                snapshot[target] = float(value)
        for source, target in quantities.items():
            value = row.get(source)
            if value is not None and math.isfinite(float(value)):
                snapshot[target] = int(value * 100)
        for side, group in [("bid", "bid_grp"), ("ask", "offer_grp")]:
            levels = {}
            for level in range(1, 6):
                price = row.get(f"{side}{level}")
                volume = row.get(f"{side}_vol{level}")
                if price is not None and volume is not None and all(
                    math.isfinite(float(value)) for value in [price, volume]
                ):
                    levels[level] = [float(price), int(volume * 100), 0]
                    if level == 1:
                        levels[level].append({})
            snapshot[group] = levels
        previous = snapshot.get("preclose_px", 0)
        if previous > 0 and "last_px" in snapshot:
            snapshot["px_change_rate"] = (snapshot["last_px"] / previous - 1) * 100
        volume = snapshot.get("business_amount", 0)
        if volume > 0 and "business_balance" in snapshot:
            snapshot["wavg_px"] = snapshot["business_balance"] / volume
        result[row["ptrade_code"]] = snapshot
    return result
