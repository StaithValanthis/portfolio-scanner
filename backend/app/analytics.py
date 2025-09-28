from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple, Dict, Any
from datetime import datetime

# ---- Dynamic, resilient Yahoo provider loader (no hardcoded class names) ---
def _load_yf_provider_instance():
    try:
        from .providers import yf as yfmod
    except Exception as e:
        raise ImportError("Cannot import module app.providers.yf") from e

    # Factory-style exports (preferred if present)
    for fname in ("get_provider", "make_provider", "provider", "create"):
        fn = getattr(yfmod, fname, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass  # try the next option

    # Common class names seen in different repos
    for cname in ("YFProvider", "YF", "YahooProvider", "Provider", "Client"):
        cls = getattr(yfmod, cname, None)
        if cls is not None:
            try:
                return cls()
            except Exception:
                continue  # wrong ctor signature? try next

    # Last resort: if the module itself quacks like a provider
    required = ("history", "info", "fx")
    if all(hasattr(yfmod, n) for n in required):
        return yfmod  # type: ignore

    exported = [n for n in dir(yfmod) if not n.startswith("_")]
    raise ImportError(
        "No suitable provider found in app.providers.yf. "
        "Expose one of: get_provider()/make_provider()/provider()/create() "
        "or a class YFProvider/YF/YahooProvider/Provider/Client. "
        f"Currently exported: {exported}"
    )


@dataclass
class PortfolioAnalytics:
    cfg: Any

    def __post_init__(self):
        self.yf = _load_yf_provider_instance()

    def snapshot(self, holdings: List[Tuple[str, float, float]]) -> Dict[str, Any]:
        """
        holdings: list of (ticker, qty, avg_price)
        Returns: {
          asof, nav, cash, pnl_total, pnl_pct, exposures, top_positions, risk_flags
        }
        """
        base = getattr(self.cfg, "base_currency", "AUD")
        nav = 0.0
        pnl_total = 0.0
        rows = []

        for tk, qty, avg in holdings:
            # Be defensive: avoid hard fails if price fetch trips a rate limit
            try:
                df = self.yf.history(tk, period="1mo")
                if df is None or df.empty or "close" not in df:
                    price = avg or 0.0
                else:
                    price = float(df["close"].iloc[-1])
            except Exception:
                price = avg or 0.0

            val = qty * price
            pnl = (price - (avg or 0)) * qty

            nav += val
            pnl_total += pnl
            rows.append((tk, val))

        pnl_pct = (pnl_total / nav) if nav else 0.0
        rows.sort(key=lambda x: x[1], reverse=True)
        top = [{"ticker": tk, "weight": (v / nav if nav else 0.0)} for tk, v in rows[:5]]

        return {
            "asof": datetime.utcnow().isoformat(),
            "nav": nav,
            "cash": 0.0,
            "pnl_total": pnl_total,
            "pnl_pct": pnl_pct,
            "exposures": {},
            "top_positions": top,
            "risk_flags": [],
        }
