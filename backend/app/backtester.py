from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, List
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

# ---------- dynamic provider loader (no hardcoded class names) ----------
def _load_yf_provider_instance():
    try:
        from .providers import yf as yfmod
    except Exception as e:
        raise ImportError("Cannot import module app.providers.yf") from e

    # Prefer factory-style exports if present
    for fname in ("get_provider", "make_provider", "provider", "create"):
        fn = getattr(yfmod, fname, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass

    # Try common class names seen in different repos
    for cname in ("YFProvider", "YF", "YahooProvider", "Provider", "Client"):
        cls = getattr(yfmod, cname, None)
        if cls is not None:
            try:
                return cls()
            except Exception:
                continue

    # Last resort: module itself quacks like a provider
    if all(hasattr(yfmod, n) for n in ("history", "info", "fx")):
        return yfmod  # type: ignore

    exported = [n for n in dir(yfmod) if not n.startswith("_")]
    raise ImportError(
        "No suitable provider in app.providers.yf. "
        "Expose get_provider()/make_provider()/provider()/create() or a class "
        "YFProvider/YF/YahooProvider/Provider/Client. "
        f"Exports: {exported}"
    )


# ---------------------------- helpers ---------------------------------
def _safe_history(yf, ticker: str, period: str = "5y", interval: str = "1d") -> pd.DataFrame:
    try:
        df = yf.history(ticker, period=period, interval=interval, auto_adjust=True)
        if df is None or df.empty:
            return pd.DataFrame()
        # Normalize column name to 'close'
        if "close" not in df.columns and "Close" in df.columns:
            df = df.rename(columns={"Close": "close"})
        return df
    except Exception:
        return pd.DataFrame()


def _stats_from_equity(eq: pd.Series) -> Dict[str, float]:
    """Compute daily-based CAGR, MaxDD, Sharpe(252), trades ~ number of position switches."""
    if eq.empty or eq.iloc[0] == 0:
        return dict(cagr=0.0, max_dd=0.0, sharpe=0.0, trades=0)

    ret = eq.pct_change().fillna(0.0)
    # CAGR
    n_days = max(1, len(eq))
    yrs = n_days / 252.0
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1 if eq.iloc[0] > 0 else 0.0
    # Max drawdown
    roll_max = eq.cummax()
    dd = eq / roll_max - 1.0
    max_dd = float(dd.min())
    # Sharpe with daily mean/std
    mu = ret.mean() * 252.0
    sigma = ret.std(ddof=0) * np.sqrt(252.0)
    sharpe = float(mu / sigma) if sigma > 1e-12 else 0.0
    return dict(cagr=float(cagr), max_dd=float(max_dd), sharpe=float(sharpe), trades=0)


def _sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n).mean()


def _trend_mom_strategy(close: pd.Series) -> Dict[str, Any]:
    """
    Simple daily strategy:
      - Invested if price >= SMA(200) AND 12-month momentum (skip last month) > 0.
      - Otherwise hold cash (0% daily return).
    """
    if close.empty:
        return {"equity": pd.Series(dtype=float), "trades": 0}

    sma200 = _sma(close, 200)
    # 12-1 momentum: close[-21] / close[-252] - 1
    mom = pd.Series(index=close.index, dtype=float)
    mom.iloc[:] = np.nan
    if len(close) > 252:
        mom.iloc[252:] = (close.shift(21).iloc[252:] / close.shift(252).iloc[252:] - 1).values

    invested = (close >= sma200) & (mom > 0)
    daily_ret = close.pct_change().fillna(0.0)
    strat_ret = daily_ret.where(invested, 0.0)
    equity = (1.0 + strat_ret).cumprod()

    # trades = number of flips in invested bool series
    flips = invested.astype(int).diff().fillna(0).abs()
    trades = int(flips.sum())

    return {"equity": equity, "invested": invested, "trades": trades}


# ---------------------------- Backtester --------------------------------
@dataclass
class Backtester:
    cfg: Any

    def __post_init__(self):
        self.yf = _load_yf_provider_instance()

    def run_multi(self, tickers: List[str], years: int = 5) -> Dict[str, Any]:
        """
        For each ticker: run trend+momentum filter and compute metrics.
        Returns:
          {
            "summary": {avg_cagr, avg_max_dd, avg_sharpe, tickers: N},
            "results": [{ticker, cagr, max_dd, sharpe, trades}, ...]
          }
        """
        results = []

        # Convert years -> period string for Yahoo
        period = "max" if years >= 30 else f"{years}y"

        for tk in sorted(set(tickers)):
            df = _safe_history(self.yf, tk, period=period, interval="1d")
            if df.empty or "close" not in df:
                continue
            close = df["close"].astype(float)
            strat = _trend_mom_strategy(close)
            equity = strat["equity"]
            m = _stats_from_equity(equity)
            m["trades"] = strat.get("trades", 0)
            results.append({
                "ticker": tk,
                "cagr": round(m["cagr"], 6),
                "max_dd": round(m["max_dd"], 6),
                "sharpe": round(m["sharpe"], 4),
                "trades": int(m["trades"]),
            })

        if results:
            avg_cagr = float(np.mean([r["cagr"] for r in results]))
            avg_dd = float(np.mean([r["max_dd"] for r in results]))
            avg_sharpe = float(np.mean([r["sharpe"] for r in results]))
        else:
            avg_cagr = avg_dd = avg_sharpe = 0.0

        return {
            "summary": {
                "avg_cagr": avg_cagr,
                "avg_max_dd": avg_dd,
                "avg_sharpe": avg_sharpe,
                "tickers": len(results),
            },
            "results": results,
        }

    def equity_series(self, ticker: str, years: int = 5) -> Dict[str, Any]:
        """
        Returns equity/drawdown series for a single ticker (strategy curve).
        {
          dates: [...], equity: [...], drawdown: [...],
          bench: {label: 'Buy&Hold', equity: [...]}
        }
        """
        period = "max" if years >= 30 else f"{years}y"
        df = _safe_history(self.yf, ticker, period=period, interval="1d")
        if df.empty or "close" not in df:
            return {"dates": [], "equity": [], "drawdown": [], "bench": {"label": "Buy&Hold", "equity": []}}

        close = df["close"].astype(float)
        strat = _trend_mom_strategy(close)
        eq = strat["equity"]
        # drawdown
        roll_max = eq.cummax()
        dd = eq / roll_max - 1.0

        # simple buy & hold benchmark (normalized)
        bh = (close / close.iloc[0]).fillna(0.0)

        return {
            "dates": [d.strftime("%Y-%m-%d") for d in eq.index],
            "equity": [float(x) for x in eq.values],
            "drawdown": [float(x) for x in dd.values],
            "bench": {"label": "Buy&Hold", "equity": [float(x) for x in bh.values]},
        }
