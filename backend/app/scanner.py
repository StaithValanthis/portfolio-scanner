from __future__ import annotations
import os, pandas as pd, numpy as np
from datetime import datetime
from .providers.yf import YFProvider
HAS_FMP = bool(os.getenv('FMP_KEY')); HAS_NEWSAPI = bool(os.getenv('NEWSAPI_KEY'))
if HAS_FMP: from .providers.fundamentals_fmp import Fundamentals as FundamentalsProvider
else: from .providers.fundamentals_yf import FundamentalsYF as FundamentalsProvider
if HAS_NEWSAPI: from .providers.news import NewsProvider as NewsProvider
else: from .providers.news_rss import NewsRSS as NewsProvider
from .providers.events import EventsProvider
from .config import Cfg

class Scanner:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg; self.yf = YFProvider(); self.fund = FundamentalsProvider(); self.news = NewsProvider(); self.events = EventsProvider()
    def _resolve_ccy(self, ticker: str) -> str: return "AUD" if ticker.endswith(".AX") else "USD"
    def _to_base(self, px: float, from_ccy: str) -> float:
        base = self.cfg.base_currency.upper()
        if base == from_ccy: return px
        if {base, from_ccy} == {"AUD","USD"}:
            fx = self.yf.fx("AUDUSD=X") or 0.65
            return px if base == "USD" else px / fx
        return px
    def _sma(self, close: pd.Series, n: int) -> float: return float(close.rolling(n).mean().iloc[-1])
    def _mom_12_1(self, close: pd.Series, skip_last_month: bool=True) -> float:
        if len(close) < 260: return np.nan
        r12 = close.iloc[-21] / close.iloc[-252] - 1 if skip_last_month else close.iloc[-1]/close.iloc[-252]-1; return float(r12)
    def _rsi(self, close: pd.Series, period: int = 14) -> float:
        delta = close.diff(); up = delta.clip(lower=0); down = -delta.clip(upper=0)
        ma_up = up.ewm(alpha=1/period, adjust=False).mean(); ma_down = down.ewm(alpha=1/period, adjust=False).mean()
        rs = ma_up / (ma_down + 1e-9); rsi = 100 - (100 / (1 + rs)); return float(rsi.iloc[-1])
    def _valuation_ok(self, facts: dict): 
        reasons, score = [], 0.0; v = self.cfg.signals.value
        pe = facts.get('pe_ttm') or facts.get('pe_fwd'); pb = facts.get('pb'); peg = facts.get('peg'); ev_ebitda = facts.get('ev_ebitda')
        if pe and v.max_pe and pe <= v.max_pe: reasons.append(f"PE {pe:.1f} ≤ {v.max_pe}"); score += 0.3
        if pb and v.max_pb and pb <= v.max_pb: reasons.append(f"PB {pb:.1f} ≤ {v.max_pb}"); score += 0.2
        if peg and v.peg_max and peg <= v.peg_max: reasons.append(f"PEG {peg:.2f} ≤ {v.peg_max}"); score += 0.2
        if ev_ebitda and v.ev_ebitda_max and ev_ebitda <= v.ev_ebitda_max: reasons.append(f"EV/EBITDA {ev_ebitda:.1f} ≤ {v.ev_ebitda_max}"); score += 0.2
        return (score>0, reasons, score)
    def _quality_ok(self, facts: dict):
        q = self.cfg.signals.quality; reasons, score = [], 0.0
        if (roe:=facts.get('roe')) and roe >= q.min_roe: reasons.append(f"ROE {roe:.1%} ≥ {q.min_roe:.0%}"); score += 0.3
        if (gm:=facts.get('gross_margin')) and gm >= q.min_gross_margin: reasons.append(f"GM {gm:.1%} ≥ {q.min_gross_margin:.0%}"); score += 0.2
        if (fcf:=facts.get('fcf_margin')) and fcf >= q.min_fcf_margin: reasons.append(f"FCF {fcf:.1%} ≥ {q.min_fcf_margin:.0%}"); score += 0.2
        if (cagr:=facts.get('rev_cagr_3y')) and cagr >= q.min_rev_cagr_3y: reasons.append(f"Rev CAGR {cagr:.1%} ≥ {q.min_rev_cagr_3y:.0%}"); score += 0.2
        if (nd:=facts.get('net_debt_ebitda')) is not None and nd <= q.max_net_debt_ebitda: reasons.append(f"NetDebt/EBITDA {nd:.1f} ≤ {q.max_net_debt_ebitda}"); score += 0.2
        return (score>0, reasons, score)
    def screen(self, tickers: list[str]) -> list[dict]:
        out = []; expected_facts = {"pe_ttm","pe_fwd","peg","pb","ev_ebitda","roe","gross_margin","fcf_margin","net_debt_ebitda","div_yield_ttm","payout_ratio"}
        for tk in tickers:
            df = self.yf.history(tk, period="5y"); 
            if df is None or df.empty or "close" not in df: continue
            close = df["close"].dropna(); px = float(close.iloc[-1]); px_base = self._to_base(px, self._resolve_ccy(tk))
            reasons, score, extras = [], 0.0, {}
            tcfg = self.cfg.signals.technical_trend; sma_fast = self._sma(close, tcfg.sma_fast); sma_slow = self._sma(close, tcfg.sma_slow)
            extras.update({"sma_fast":sma_fast, "sma_slow":sma_slow})
            if tcfg.enabled and ((not tcfg.require_above_sma200) or (px >= sma_slow*(1-1e-6))): reasons.append("Uptrend intact (≥SMA200)"); score += 0.4
            if self.cfg.signals.momentum.enabled:
                m12 = self._mom_12_1(close, self.cfg.signals.momentum.skip_last_month); extras["mom12"] = m12
                if np.isfinite(m12) and m12 >= self.cfg.signals.momentum.min_12m_mom: reasons.append(f"12m momentum {m12:.1%} strong"); score += 0.6
            if self.cfg.signals.mean_reversion.enabled:
                rsi = self._rsi(close, self.cfg.signals.mean_reversion.rsi_period); extras["rsi"] = rsi
                if rsi <= self.cfg.signals.mean_reversion.rsi_buy_below: reasons.append(f"RSI {rsi:.1f} oversold"); score += 0.3
            facts = self.fund.facts(tk) or {}; extras.update({"facts": facts})
            if self.cfg.signals.value.enabled: _, rs, sc = self._valuation_ok(facts); reasons += rs; score += sc
            if self.cfg.signals.quality.enabled: _, rs, sc = self._quality_ok(facts); reasons += rs; score += sc
            if self.cfg.signals.dividend.enabled:
                dy = facts.get('div_yield_ttm') or self.yf.dividends_ttm(tk); extras["div_yield_ttm"] = dy
                if dy and dy >= self.cfg.signals.dividend.min_yield: reasons.append(f"Dividend yield {dy:.1%} ≥ {self.cfg.signals.dividend.min_yield:.0%}"); score += 0.2
                if dy and self.cfg.signals.dividend.max_payout and (p:=facts.get('payout_ratio')) and p <= self.cfg.signals.dividend.max_payout: reasons.append(f"Payout ratio {p:.0%} sustainable"); score += 0.1
            if self.cfg.signals.news_sentiment.enabled:
                s_mean = self.news.average_sentiment(tk, self.cfg.signals.news_sentiment.lookback_days); extras["news_sentiment_avg"] = s_mean
                if s_mean is not None and s_mean >= self.cfg.signals.news_sentiment.min_avg_sentiment: reasons.append(f"News sentiment {s_mean:+.2f} supportive"); score += 0.2
            if self.cfg.signals.breakout52w.enabled:
                hi = float(close[-252:].max()) if len(close) >= 252 else float(close.max()); dist = (hi - close.iloc[-1]) / hi if hi else np.nan
                extras["near_52w_high"] = 1 - dist if np.isfinite(dist) else None
                if np.isfinite(dist) and dist <= self.cfg.signals.breakout52w.near_high_pct: reasons.append("Near 52w high"); score += 0.3
            present = set(k for k,v in facts.items() if v is not None) & expected_facts; completeness = len(present) / len(expected_facts)
            extras["facts_provider"] = facts.get("provider","yf" if not HAS_FMP else "fmp"); extras["facts_completeness"] = round(completeness,2)
            ev = self.events.earnings_and_div(tk); extras["events"] = ev
            side = "BUY" if score >= 1.5 else ("HOLD" if score >= 0.5 else "PASS")
            out.append({"ticker": tk, "side": side, "reasons": reasons, "score": round(score, 3), "px": px_base, "asof": datetime.utcnow().isoformat(), "extras": extras})
        return sorted(out, key=lambda x: x["score"], reverse=True)
