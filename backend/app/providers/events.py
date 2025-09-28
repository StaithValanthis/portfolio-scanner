from __future__ import annotations
import yfinance as yf
from datetime import datetime
from ..utils import cache
class EventsProvider:
    def earnings_and_div(self, ticker: str) -> dict:
        key = f"events:{ticker}"; hit = cache.get(key)
        if hit is not None: return hit
        out = {}
        try:
            t = yf.Ticker(ticker)
            cal = getattr(t, "calendar", None)
            if cal is not None:
                try:
                    if "Earnings Date" in cal.index:
                        val = cal.loc["Earnings Date"].values[0]
                        if hasattr(val, "to_pydatetime"): out["earnings_date"] = val.to_pydatetime().isoformat()
                        else: out["earnings_date"] = str(val)
                except Exception: pass
            info = t.info or {}
            if not out.get("earnings_date") and info.get("earningsDate"): out["earnings_date"] = str(info.get("earningsDate"))
            if info.get("exDividendDate"):
                try:
                    ts = int(info["exDividendDate"]); out["ex_div_date"] = datetime.utcfromtimestamp(ts).isoformat()
                except Exception: out["ex_div_date"] = str(info["exDividendDate"])
        except Exception: pass
        cache.set(key, out); return out
