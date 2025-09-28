import yfinance as yf, pandas as pd
from functools import lru_cache
from ..utils import cache

class YFProvider:
    def history(self, ticker: str, period: str = "5y", interval: str = "1d") -> pd.DataFrame:
        t = yf.Ticker(ticker)
        df = t.history(period=period, interval=interval, auto_adjust=True)
        if not isinstance(df, pd.DataFrame) or df.empty: return pd.DataFrame()
        return df.rename(columns=str.lower)

    @lru_cache(maxsize=4096)
    def info(self, ticker: str) -> dict:
        hit = cache.get(f"info:{ticker}")
        if hit is not None: return hit
        t = yf.Ticker(ticker); data = t.info or {}
        cache.set(f"info:{ticker}", data); return data

    def dividends_ttm(self, ticker: str) -> float | None:
        t = yf.Ticker(ticker)
        try: divs = t.dividends
        except Exception: return None
        if divs is None or len(divs) == 0: return None
        last_yr = divs[divs.index > (divs.index.max() - pd.DateOffset(years=1))]
        price = self.history(ticker, period="1mo").close.iloc[-1]
        return float(last_yr.sum()/price) if price and len(last_yr) else None

    def fx(self, pair: str = "AUDUSD=X") -> float | None:
        hit = cache.get(f"fx:{pair}")
        if hit is not None: return float(hit)
        df = yf.Ticker(pair).history(period="5d", interval="1d")
        try:
            val = float(df["Close"].iloc[-1]); cache.set(f"fx:{pair}", val); return val
        except Exception: return None
