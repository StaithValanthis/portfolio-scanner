from __future__ import annotations
import os, httpx
BASE = "https://financialmodelingprep.com/api/v3"
class Fundamentals:
    def __init__(self): self.key = os.getenv("FMP_KEY","")
    def _get(self, path: str, params: dict):
        params = dict(params or {}); 
        if self.key: params["apikey"] = self.key
        r = httpx.get(f"{BASE}/{path}", params=params, timeout=20); r.raise_for_status(); return r.json()
    def facts(self, ticker: str) -> dict:
        if not self.key: return {}
        out = {"provider":"fmp"}
        try:
            prof = self._get(f"profile/{ticker}", {}); keym = self._get(f"key-metrics-ttm/{ticker}", {}); rat = self._get(f"ratios-ttm/{ticker}", {})
            def pick(d, k): 
                if isinstance(d, list) and d: return d[0].get(k)
                return d.get(k) if isinstance(d, dict) else None
            out["pe_ttm"] = pick(rat,"priceEarningsRatioTTM"); out["pe_fwd"] = pick(keym,"peRatioForwardTTM")
            out["peg"] = pick(keym,"pegRatioTTM"); out["pb"] = pick(rat,"priceToBookRatioTTM")
            out["ev_ebitda"] = pick(rat,"enterpriseValueOverEBITDATTM")
            price = pick(prof,"price"); div = pick(prof,"lastDiv")
            out["div_yield_ttm"] = (div/price) if price and div else None
            out["payout_ratio"] = pick(rat,"payoutRatioTTM")
            out["gross_margin"] = pick(rat,"grossProfitMarginTTM"); out["roe"] = pick(rat,"returnOnEquityTTM")
            out["fcf_margin"] = pick(keym,"freeCashFlowMarginTTM"); out["net_debt_ebitda"] = pick(keym,"netDebtToEBITDATTM")
        except Exception: pass
        return {k:v for k,v in out.items() if v is not None}
