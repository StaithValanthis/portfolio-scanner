from __future__ import annotations
import pandas as pd, numpy as np
from dataclasses import dataclass
from .providers.yf import YFProvider
from .config import Cfg

@dataclass
class BTResult: ticker: str; cagr: float; max_dd: float; sharpe: float; trades: int

class Backtester:
    def __init__(self, cfg: Cfg): self.cfg = cfg; self.yf = YFProvider()
    def _sma(self, s: pd.Series, n: int): return s.rolling(n).mean()
    def run_simple(self, ticker: str, years: int = 5) -> BTResult:
        df = self.yf.history(ticker, period=f"{years}y", interval="1d")
        if df is None or df.empty or "close" not in df: return BTResult(ticker, 0.0, 0.0, 0.0, 0)
        px = df["close"].dropna(); sma200 = self._sma(px, self.cfg.signals.technical_trend.sma_slow)
        mom = px.pct_change(252) - px.pct_change(21); min_mom = self.cfg.signals.momentum.min_12m_mom; below_buf = self.cfg.sell_rules.below_sma200_pct
        pos = (px > sma200) & (mom >= min_mom); exit_mask = (px < (sma200 * (1 - below_buf))); pos = pos & (~exit_mask)
        ret = px.pct_change().fillna(0); strat = (ret * pos.shift(1).fillna(False)).fillna(0)
        cum = (1+strat).cumprod(); days = len(strat); cagr = cum.iloc[-1]**(252/max(1,days)) - 1 if days>0 else 0.0
        dd = (cum / cum.cummax() - 1).min(); sharpe = np.sqrt(252) * strat.mean() / (strat.std()+1e-9); trades = ((pos.astype(int).diff()==1).sum())
        return BTResult(ticker, float(cagr), float(abs(dd)), float(sharpe), int(trades))
    def equity_series(self, ticker: str, years: int = 5) -> dict:
        df = self.yf.history(ticker, period=f"{years}y", interval="1d")
        if df is None or df.empty or "close" not in df: return {"dates": [], "equity": [], "drawdown": [], "bench": {"label": "", "equity": []}}
        px = df["close"].dropna(); sma200 = self._sma(px, self.cfg.signals.technical_trend.sma_slow)
        mom = px.pct_change(252) - px.pct_change(21); min_mom = self.cfg.signals.momentum.min_12m_mom; below_buf = self.cfg.sell_rules.below_sma200_pct
        pos = (px > sma200) & (mom >= min_mom); exit_mask = (px < (sma200 * (1 - below_buf))); pos = pos & (~exit_mask)
        ret = px.pct_change().fillna(0); strat = (ret * pos.shift(1).fillna(False)).fillna(0)
        cum = (1+strat).cumprod(); dd = (cum / cum.cummax() - 1).fillna(0.0); dates = [d.strftime("%Y-%m-%d") for d in cum.index]
        bench = '^AXJO' if ticker.endswith('.AX') else '^GSPC'
        bdf = self.yf.history(bench, period=f"{years}y", interval="1d")
        if bdf is not None and not bdf.empty and 'close' in bdf:
            bpx = bdf['close'].dropna().reindex(px.index).ffill().bfill(); bret = bpx.pct_change().fillna(0); bcum = (1+bret).cumprod()
            bench_equity = [float(x) for x in bcum.values]
        else: bench_equity = []
        return {"dates": dates, "equity": [float(x) for x in cum.values], "drawdown": [float(x) for x in dd.values], "bench": {"label": bench, "equity": bench_equity}}
    def run_multi(self, tickers: list[str], years: int = 5) -> dict:
        rows = []
        for tk in tickers:
            r = self.run_simple(tk, years); rows.append(dict(ticker=tk, cagr=r.cagr, max_dd=r.max_dd, sharpe=r.sharpe, trades=r.trades))
        import pandas as pd
        df = pd.DataFrame(rows)
        if df.empty: return {"summary": {}, "results": []}
        summary = {"avg_cagr": float(df["cagr"].mean()), "avg_max_dd": float(df["max_dd"].mean()), "avg_sharpe": float(df["sharpe"].mean()), "tickers": len(df)}
        return {"summary": summary, "results": rows}
