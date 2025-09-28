from __future__ import annotations
import pandas as pd
from datetime import datetime
from .providers.yf import YFProvider
from .config import Cfg

class PortfolioAnalytics:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg; self.yf = YFProvider()
    def _resolve_ccy(self, ticker: str) -> str:
        return "AUD" if ticker.endswith(".AX") else "USD"
    def _to_base(self, px: float, from_ccy: str) -> float:
        base = self.cfg.base_currency.upper()
        if base == from_ccy: return px
        if {base, from_ccy} == {"AUD","USD"}:
            fx = self.yf.fx("AUDUSD=X") or 0.65
            return px if base == "USD" else px / fx
        return px
    def snapshot(self, holdings: list[tuple[str, float, float]], cash: float = 0.0) -> dict:
        rows = []
        for tk, qty, avg_px in holdings:
            df = self.yf.history(tk, period="1mo")
            if df is None or df.empty: continue
            last_px = float(df["close"].iloc[-1])
            ccy = self._resolve_ccy(tk)
            last_px_base = self._to_base(last_px, ccy); avg_px_base  = self._to_base(avg_px, ccy)
            val = qty * last_px_base; pnl = (last_px_base - avg_px_base) * qty
            rows.append({"ticker":tk,"qty":qty,"ccy":ccy,"px":last_px_base,"avg":avg_px_base,"value":val,"pnl":pnl})
        df = pd.DataFrame(rows); nav = float(df["value"].sum()) + cash if not df.empty else cash
        pnl_total = float(df["pnl"].sum()) if not df.empty else 0.0
        pnl_pct = (pnl_total / (nav - pnl_total)) if (nav - pnl_total) > 0 else 0.0
        def sector_of(t): return "ASX" if t.endswith(".AX") else "US"
        exp = {} if df.empty else {sec: float(df.loc[df["ticker"].map(sector_of)==sec,"value"].sum()/nav) for sec in set(df["ticker"].map(sector_of))}
        top = [] if df.empty else (df.sort_values("value", ascending=False).head(5)[["ticker","value"]].assign(weight=lambda d: d["value"]/nav).to_dict(orient="records"))
        flags = []
        for r in top:
            if r["weight"] > self.cfg.risk.position_cap_pct_nav * (1 + self.cfg.risk.rebalance_band_pct):
                flags.append(f"{r['ticker']} above position cap ({r['weight']:.1%})")
        return {"asof": datetime.utcnow().isoformat(), "nav": nav, "cash": cash, "pnl_total": pnl_total, "pnl_pct": pnl_pct, "exposures": exp, "top_positions": top, "risk_flags": flags, "base_currency": self.cfg.base_currency}
