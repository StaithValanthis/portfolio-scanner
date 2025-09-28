from __future__ import annotations
import os, io, time, json, logging, re
import pandas as pd, numpy as np
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
import requests

from yfinance.exceptions import YFRateLimitError

# ---- dynamic Yahoo provider loader (unchanged idea) -------------------------
def _load_yf_provider_instance():
    try:
        from .providers import yf as yfmod
    except Exception as e:
        raise ImportError("Cannot import module app.providers.yf") from e

    for fname in ("get_provider", "make_provider", "provider", "create"):
        fn = getattr(yfmod, fname, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass

    for cname in ("YFProvider", "YF", "YahooProvider", "Provider", "Client"):
        cls = getattr(yfmod, cname, None)
        if cls is not None:
            try:
                return cls()
            except Exception:
                continue

    if all(hasattr(yfmod, n) for n in ("history", "info", "fx")):
        return yfmod  # module acts as provider

    exported = [n for n in dir(yfmod) if not n.startswith("_")]
    raise ImportError(
        "No suitable provider found in app.providers.yf. "
        "Expose get_provider()/make_provider()/provider()/create() or a class "
        "YFProvider/YF/YahooProvider/Provider/Client. "
        f"Exports: {exported}"
    )

# ---- providers --------------------------------------------------------------
HAS_FMP = bool(os.getenv('FMP_KEY'))
HAS_NEWSAPI = bool(os.getenv('NEWSAPI_KEY'))
if HAS_FMP:
    from .providers.fundamentals_fmp import Fundamentals as FundamentalsProvider
else:
    from .providers.fundamentals_yf import FundamentalsYF as FundamentalsProvider

if HAS_NEWSAPI:
    from .providers.news import NewsProvider as NewsProvider
else:
    from .providers.news_rss import NewsRSS as NewsProvider

from .providers.events import EventsProvider
from .config import Cfg

log = logging.getLogger("scanner")
if not log.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
log.setLevel(logging.INFO)


def _ua_headers() -> dict:
    return {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }


class Scanner:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        self.yf = _load_yf_provider_instance()
        self.fund = FundamentalsProvider()
        self.news_provider = NewsProvider()
        self.events = EventsProvider()

        self.max_universe = int(os.getenv("SCAN_MAX_TICKERS", "120"))
        self.sleep_ms     = int(os.getenv("SCAN_SLEEP_MS", "220"))
        self.universe_ttl_hours = int(os.getenv("UNIVERSE_TTL_HOURS", "24"))

    # -------------------------- universe helpers --------------------------

    def _univ_cache_dir(self) -> str:
        cdir = os.getenv("CACHE_DIR", "/app/.cache")
        path = os.path.join(cdir, "universes")
        os.makedirs(path, exist_ok=True)
        return path

    def _univ_cache_path(self, key: str) -> str:
        safe = key.replace(":", "_")
        return os.path.join(self._univ_cache_dir(), f"{safe}.txt")

    def _read_cached_universe(self, key: str, max_age_hours: int) -> Optional[List[str]]:
        p = self._univ_cache_path(key)
        try:
            st = os.stat(p)
            age_h = (time.time() - st.st_mtime) / 3600.0
            if age_h <= max_age_hours:
                with open(p, "r", encoding="utf-8") as f:
                    rows = [ln.strip() for ln in f if ln.strip()]
                return rows or None
        except FileNotFoundError:
            return None
        except Exception:
            return None
        return None

    def _write_cached_universe(self, key: str, syms: List[str]) -> None:
        p = self._univ_cache_path(key)
        try:
            with open(p, "w", encoding="utf-8") as f:
                for s in syms:
                    f.write(s + "\n")
        except Exception:
            pass

    def _read_bundled_universe(self, name: str) -> Optional[List[str]]:
        try:
            here = os.path.dirname(os.path.abspath(__file__))  # .../app
            p = os.path.join(here, "universes", f"{name}.txt")
            with open(p, "r", encoding="utf-8") as f:
                rows = [ln.strip() for ln in f if ln.strip()]
            return rows or None
        except Exception:
            return None

    def _fetch_wikipedia_sp500(self) -> List[str]:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        log.info("Fetching S&P 500 from %s", url)
        r = requests.get(url, headers=_ua_headers(), timeout=20)
        r.raise_for_status()
        # FutureWarning-safe read_html:
        tables = pd.read_html(io.StringIO(r.text))
        syms: List[str] = []
        for df in tables:
            cols = [str(c).lower() for c in df.columns]
            if any("symbol" in c for c in cols):
                idx = [i for i,c in enumerate(cols) if "symbol" in c][0]
                col = df.columns[idx]
                syms = [str(s).strip().upper() for s in df[col].dropna().tolist()]
                break
        syms = [s for s in syms if re.fullmatch(r"[A-Z\.]+", s or "")]
        log.info("Wikipedia S&P500 -> %d symbols", len(syms))
        return syms

    def _fetch_wikipedia_asx200(self) -> List[str]:
        url = "https://en.wikipedia.org/wiki/S%26P/ASX_200"
        log.info("Fetching ASX200 from %s", url)
        r = requests.get(url, headers=_ua_headers(), timeout=20)
        r.raise_for_status()
        tables = pd.read_html(io.StringIO(r.text))
        syms: List[str] = []
        for df in tables:
            lower = [str(c).lower() for c in df.columns]
            pick_idx = None
            for target in ("ticker", "asx code", "symbol"):
                hit = [i for i,c in enumerate(lower) if target in c]
                if hit:
                    pick_idx = hit[0]
                    break
            if pick_idx is None:
                continue
            col = df.columns[pick_idx]
            vals = [str(x).strip().upper() for x in df[col].dropna().tolist()]
            for v in vals:
                v2 = re.sub(r"^ASX[:\s]+", "", v)
                v2 = re.sub(r"[^A-Z0-9\.]", "", v2)
                if v2 and not v2.endswith(".AX"):
                    v2 = v2 + ".AX"
                if re.fullmatch(r"[A-Z0-9]+\.AX", v2):
                    syms.append(v2)
        syms = sorted(set(syms))
        log.info("Wikipedia ASX200 -> %d symbols", len(syms))
        return syms

    def resolve_universe(self, name: str) -> List[str]:
        key = name.strip().lower()
        cached = self._read_cached_universe(key, self.universe_ttl_hours)
        if cached:
            return cached

        syms: List[str] = []
        try:
            if key == "auto:sp500":
                syms = self._fetch_wikipedia_sp500()
            elif key == "auto:asx200":
                syms = self._fetch_wikipedia_asx200()
            elif key.startswith("file:"):
                fname = key.split(":", 1)[1]
                syms = self._read_bundled_universe(fname) or []
            else:
                bname = key.replace("auto:", "")
                syms = self._read_bundled_universe(bname) or []
        except Exception as e:
            log.error("Auto universe fetch failed for %s: %s", name, e)
            bname = key.replace("auto:", "")
            syms = self._read_bundled_universe(bname) or []

        normed: List[str] = []
        for s in syms:
            s = str(s).strip().upper()
            if not s:
                continue
            normed.append(s)

        normed = sorted(set(normed))
        if normed:
            self._write_cached_universe(key, normed)
        return normed

    # -------------------------- scan queue (poll->store->scan 1x) ------------

    def _cache_dir(self) -> str:
        d = os.getenv("CACHE_DIR", "/app/.cache")
        os.makedirs(d, exist_ok=True)
        return d

    def _queue_file(self) -> str:
        return os.path.join(self._cache_dir(), "scan_queue.jsonl")

    def _results_file(self) -> str:
        return os.path.join(self._cache_dir(), "scan_results.jsonl")

    def prepare_queue(self, universes: List[str], max_tickers: Optional[int] = None) -> dict:
        # Resolve universes → dedupe → cap → write queue file
        all_syms: List[str] = []
        for u in universes:
            try:
                syms = self.resolve_universe(u)
                log.info("scan: universe %-12s -> %d symbols", u, len(syms))
                all_syms.extend(syms)
            except Exception as e:
                log.error("scan: universe %s error: %s", u, e)

        base = sorted(set(all_syms))
        cap = int(max_tickers) if (max_tickers and int(max_tickers)>0) else self.max_universe
        if len(base) > cap:
            log.info("Capping queue from %d to %d", len(base), cap)
            base = base[:cap]

        # write queue
        qpath = self._queue_file()
        with open(qpath, "w", encoding="utf-8") as f:
            for tk in base:
                f.write(json.dumps({"ticker": tk}) + "\n")

        # reset results
        rpath = self._results_file()
        try:
            os.remove(rpath)
        except FileNotFoundError:
            pass

        return {"queued": len(base)}

    def _read_jsonl(self, path: str, limit: Optional[int]=None) -> List[dict]:
        rows: List[dict] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for i, line in enumerate(f):
                    if line.strip():
                        try:
                            rows.append(json.loads(line))
                        except Exception:
                            pass
                    if limit and len(rows) >= limit:
                        break
        except FileNotFoundError:
            return []
        return rows

    def _write_jsonl(self, path: str, rows: List[dict]) -> None:
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def queue_status(self, sample: int = 5) -> dict:
        q = self._read_jsonl(self._queue_file(), limit=None)
        r = self._read_jsonl(self._results_file(), limit=None)
        return {
            "remaining": len(q),
            "processed": len(r),
            "sample_results": r[:sample],
            "last_result": (r[-1] if r else None),
        }

    def next_step(self) -> dict:
        qpath = self._queue_file()
        rpath = self._results_file()
        qrows = self._read_jsonl(qpath, limit=None)
        if not qrows:
            return {"done": True, "remaining": 0}

        job = qrows.pop(0)
        ticker = job["ticker"]

        # run a single-ticker screen
        res = self.screen([ticker], max_tickers=1, chunk=None)
        # append result
        rrows = self._read_jsonl(rpath, limit=None)
        if res:
            rrows.append(res[0])

        # persist both
        self._write_jsonl(qpath, qrows)
        self._write_jsonl(rpath, rrows)

        return {
            "done": False,
            "remaining": len(qrows),
            "last": (res[0] if res else {"ticker": ticker, "error": "no-data"}),
        }

    def results_all(self) -> List[dict]:
        return self._read_jsonl(self._results_file(), limit=None)

    def clear_queue_and_results(self) -> dict:
        for p in (self._queue_file(), self._results_file()):
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
        return {"cleared": True}

    # -------------------------- helpers & full scan --------------------------

    def _resolve_ccy(self, ticker: str) -> str:
        return "AUD" if ticker.endswith(".AX") else "USD"

    def _to_base(self, px: float, from_ccy: str) -> float:
        base = self.cfg.base_currency.upper()
        if base == from_ccy:
            return px
        if {base, from_ccy} == {"AUD","USD"}:
            try:
                fx = self.yf.fx("AUDUSD=X") or 0.65
            except Exception:
                fx = 0.65
            return px if base == "USD" else px / fx
        return px

    def _sma(self, close: pd.Series, n: int) -> float:
        return float(close.rolling(n).mean().iloc[-1])

    def _mom_12_1(self, close: pd.Series, skip_last_month: bool=True) -> float:
        if len(close) < 260:
            return np.nan
        r12 = close.iloc[-21] / close.iloc[-252] - 1 if skip_last_month else close.iloc[-1]/close.iloc[-252]-1
        return float(r12)

    def _rsi(self, close: pd.Series, period: int = 14) -> float:
        delta = close.diff()
        up = delta.clip(lower=0)
        down = -delta.clip(upper=0)
        ma_up = up.ewm(alpha=1/period, adjust=False).mean()
        ma_down = down.ewm(alpha=1/period, adjust=False).mean()
        rs = ma_up / (ma_down + 1e-9)
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1])

    def _valuation_ok(self, facts: dict):
        reasons, score = [], 0.0
        v = self.cfg.signals.value
        pe = facts.get('pe_ttm') or facts.get('pe_fwd')
        pb = facts.get('pb')
        peg = facts.get('peg')
        ev_ebitda = facts.get('ev_ebitda')
        if pe and v.max_pe and pe <= v.max_pe:
            reasons.append(f"PE {pe:.1f} ≤ {v.max_pe}")
            score += 0.3
        if pb and v.max_pb and pb <= v.max_pb:
            reasons.append(f"PB {pb:.1f} ≤ {v.max_pb}")
            score += 0.2
        if peg and v.peg_max and peg <= v.peg_max:
            reasons.append(f"PEG {peg:.2f} ≤ {v.peg_max}")
            score += 0.2
        if ev_ebitda and v.ev_ebitda_max and ev_ebitda <= v.ev_ebitda_max:
            reasons.append(f"EV/EBITDA {ev_ebitda:.1f} ≤ {v.ev_ebitda_max}")
            score += 0.2
        return (score>0, reasons, score)

    def _quality_ok(self, facts: dict):
        q = self.cfg.signals.quality
        reasons, score = [], 0.0
        if (roe:=facts.get('roe')) and roe >= q.min_roe:
            reasons.append(f"ROE {roe:.1%} ≥ {q.min_roe:.0%}")
            score += 0.3
        if (gm:=facts.get('gross_margin')) and gm >= q.min_gross_margin:
            reasons.append(f"GM {gm:.1%} ≥ {q.min_gross_margin:.0%}")
            score += 0.2
        if (fcf:=facts.get('fcf_margin')) and fcf >= q.min_fcf_margin:
            reasons.append(f"FCF {fcf:.1%} ≥ {q.min_fcf_margin:.0%}")
            score += 0.2
        if (cagr:=facts.get('rev_cagr_3y')) and cagr >= q.min_rev_cagr_3y:
            reasons.append(f"Rev CAGR {cagr:.1%} ≥ {q.min_rev_cagr_3y:.0%}")
            score += 0.2
        if (nd:=facts.get('net_debt_ebitda')) is not None and nd <= q.max_net_debt_ebitda:
            reasons.append(f"NetDebt/EBITDA {nd:.1f} ≤ {q.max_net_debt_ebitda}")
            score += 0.2
        return (score>0, reasons, score)

    def _apply_chunk(self, arr: List[str], chunk: Optional[str]) -> Tuple[List[str], str]:
        if not chunk:
            return arr, ""
        try:
            start_s, end_s = (chunk.split(":", 1) + [""])[:2]
            start = int(start_s or "0")
            end = int(end_s) if end_s else len(arr)
            start = max(0, min(start, len(arr)))
            end = max(start, min(end, len(arr)))
            return arr[start:end], f" [chunk {start}:{end}]"
        except Exception:
            return arr, ""

    def screen(
        self,
        tickers: List[str],
        max_tickers: Optional[int] = None,
        chunk: Optional[str] = None,
    ) -> List[dict]:
        if not tickers:
            log.info("scan: input set is empty; returning no signals")
            return []

        uniq = sorted(set(tickers))
        uniq, note = self._apply_chunk(uniq, chunk)

        cap = int(max_tickers) if (max_tickers is not None and int(max_tickers) > 0) else self.max_universe
        if len(uniq) > cap:
            log.info("Capping scan universe from %d to %d tickers%s", len(uniq), cap, note)
        tickers = uniq[: cap]

        out: List[Dict[str, Any]] = []

        for idx, tk in enumerate(tickers, 1):
            if idx > 1 and self.sleep_ms > 0:
                time.sleep(self.sleep_ms / 1000.0)

            try:
                df = self.yf.history(tk, period="5y")
            except YFRateLimitError:
                log.warning("Rate-limited by Yahoo on %s; skipping", tk)
                continue
            except Exception as e:
                log.debug("history(%s) failed: %s", tk, e)
                continue

            if df is None or df.empty or "close" not in df:
                continue

            close = df["close"].dropna()
            px = float(close.iloc[-1])
            px_base = self._to_base(px, self._resolve_ccy(tk))
            reasons, score, extras = [], 0.0, {}

            try:
                tcfg = self.cfg.signals.technical_trend
                sma_fast = self._sma(close, tcfg.sma_fast)
                sma_slow = self._sma(close, tcfg.sma_slow)
                extras.update({"sma_fast":sma_fast, "sma_slow":sma_slow})
                if tcfg.enabled and ((not tcfg.require_above_sma200) or (px >= sma_slow*(1-1e-6))):
                    reasons.append("Uptrend intact (≥SMA200)")
                    score += 0.4
            except Exception:
                pass

            if self.cfg.signals.momentum.enabled:
                try:
                    m12 = self._mom_12_1(close, self.cfg.signals.momentum.skip_last_month)
                    extras["mom12"] = m12
                    if np.isfinite(m12) and m12 >= self.cfg.signals.momentum.min_12m_mom:
                        reasons.append(f"12m momentum {m12:.1%} strong")
                        score += 0.6
                except Exception:
                    pass

            if self.cfg.signals.mean_reversion.enabled:
                try:
                    rsi = self._rsi(close, self.cfg.signals.mean_reversion.rsi_period)
                    extras["rsi"] = rsi
                    if rsi <= self.cfg.signals.mean_reversion.rsi_buy_below:
                        reasons.append(f"RSI {rsi:.1f} oversold")
                        score += 0.3
                except Exception:
                    pass

            try:
                facts = self.fund.facts(tk) or {}
            except Exception as e:
                log.debug("facts(%s) failed: %s", tk, e)
                facts = {}
            extras.update({"facts": facts})

            if self.cfg.signals.value.enabled:
                try:
                    _, rs, sc = self._valuation_ok(facts); reasons += rs; score += sc
                except Exception:
                    pass
            if self.cfg.signals.quality.enabled:
                try:
                    _, rs, sc = self._quality_ok(facts); reasons += rs; score += sc
                except Exception:
                    pass

            if self.cfg.signals.dividend.enabled:
                dy = None
                try:
                    dy = facts.get('div_yield_ttm') or self.yf.dividends_ttm(tk)
                except YFRateLimitError:
                    pass
                except Exception:
                    pass
                extras["div_yield_ttm"] = dy
                if dy and dy >= self.cfg.signals.dividend.min_yield:
                    reasons.append(f"Dividend yield {dy:.1%} ≥ {self.cfg.signals.dividend.min_yield:.0%}")
                    score += 0.2
                if dy and self.cfg.signals.dividend.max_payout and (p:=facts.get('payout_ratio')) and p <= self.cfg.signals.dividend.max_payout:
                    reasons.append(f"Payout ratio {p:.0%} sustainable")
                    score += 0.1

            if self.cfg.signals.news_sentiment.enabled:
                try:
                    s_mean = self.news_provider.average_sentiment(
                        tk, self.cfg.signals.news_sentiment.lookback_days
                    )
                except Exception:
                    s_mean = None
                extras["news_sentiment_avg"] = s_mean
                if s_mean is not None and s_mean >= self.cfg.signals.news_sentiment.min_avg_sentiment:
                    reasons.append(f"News sentiment {s_mean:+.2f} supportive")
                    score += 0.2

            if self.cfg.signals.breakout52w.enabled:
                try:
                    hi = float(close[-252:].max()) if len(close) >= 252 else float(close.max())
                    dist = (hi - close.iloc[-1]) / hi if hi else np.nan
                except Exception:
                    dist = np.nan
                extras["near_52w_high"] = 1 - dist if np.isfinite(dist) else None
                if np.isfinite(dist) and dist <= self.cfg.signals.breakout52w.near_high_pct:
                    reasons.append("Near 52w high")
                    score += 0.3

            expected = {
                "pe_ttm","pe_fwd","peg","pb","ev_ebitda","roe",
                "gross_margin","fcf_margin","net_debt_ebitda","div_yield_ttm","payout_ratio"
            }
            present = {k for k,v in (facts or {}).items() if v is not None} & expected
            completeness = len(present) / len(expected)
            extras["facts_provider"] = facts.get("provider","yf" if not HAS_FMP else "fmp")
            extras["facts_completeness"] = round(completeness,2)

            try:
                ev = self.events.earnings_and_div(tk)
            except Exception:
                ev = {}
            extras["events"] = ev

            side = "BUY" if score >= 1.5 else ("HOLD" if score >= 0.5 else "PASS")
            out.append({
                "ticker": tk,
                "side": side,
                "reasons": reasons,
                "score": round(score, 3),
                "px": px_base,
                "asof": datetime.utcnow().isoformat(),
                "extras": extras
            })

        return sorted(out, key=lambda x: x["score"], reverse=True)

    # -------------------------- news passthrough --------------------------

    def news(self, ticker: str, days: int = 7, limit: int = 20) -> dict:
        try:
            items = self.news_provider.recent(ticker, lookback_days=days, limit=limit)
        except Exception:
            items = []
        return {"items": items}

    def news_provider_recent(self, ticker: str, days: int = 7, limit: int = 20) -> list[dict]:
        return self.news_provider.recent(ticker, lookback_days=days, limit=limit)
