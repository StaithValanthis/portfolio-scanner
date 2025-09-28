from __future__ import annotations

import time
import math
import threading
from functools import lru_cache
from typing import Optional, Dict, Any

import yfinance as yfi
from yfinance.exceptions import YFRateLimitError

# Simple polite throttle: default ~5 requests/sec
_REQS_PER_SEC = float(int(__import__("os").getenv("YF_REQS_PER_SEC", "5")))
_MIN_DELAY = 1.0 / max(_REQS_PER_SEC, 1.0)

# backoff parameters
_MAX_RETRIES = int(__import__("os").getenv("YF_MAX_RETRIES", "4"))
_BASE_SLEEP = float(__import__("os").getenv("YF_BASE_SLEEP", "1.5"))  # seconds

# TTL (seconds) for hot in-memory caches
_TTL_PRICE = int(__import__("os").getenv("YF_TTL_PRICE_SEC", "900"))  # 15 min
_TTL_INFO  = int(__import__("os").getenv("YF_TTL_INFO_SEC", "3600"))  # 1 h
_TTL_FX    = int(__import__("os").getenv("YF_TTL_FX_SEC", "600"))     # 10 min

_lock = threading.Lock()
_last_ts = 0.0

def _throttle():
    global _last_ts
    with _lock:
        now = time.time()
        wait = max(0.0, _MIN_DELAY - (now - _last_ts))
        if wait > 0:
            time.sleep(wait)
        _last_ts = time.time()

class _TTLCache:
    def __init__(self, ttl_sec: int):
        self.ttl = ttl_sec
        self.data: Dict[str, tuple[float, Any]] = {}
        self.lock = threading.Lock()

    def get(self, key: str):
        with self.lock:
            v = self.data.get(key)
            if not v: return None
            ts, payload = v
            if time.time() - ts > self.ttl:
                self.data.pop(key, None)
                return None
            return payload

    def set(self, key: str, payload: Any):
        with self.lock:
            self.data[key] = (time.time(), payload)

_price_cache = _TTLCache(_TTL_PRICE)
_info_cache  = _TTLCache(_TTL_INFO)
_fx_cache    = _TTLCache(_TTL_FX)

def _retrying(call, *, key: str, cache: _TTLCache):
    # cache first
    cached = cache.get(key)
    if cached is not None:
        return cached

    last_err = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            _throttle()
            out = call()
            cache.set(key, out)
            return out
        except YFRateLimitError as e:
            last_err = e
            sleep = _BASE_SLEEP * (2 ** attempt)
            time.sleep(sleep)
            continue
        except Exception as e:
            # network errors etc.; retry a couple of times
            last_err = e
            sleep = _BASE_SLEEP * (1.5 ** attempt)
            time.sleep(sleep)
            continue

    # give up – return None; callers should handle None gracefully
    return None

class YFClient:
    """Thin wrapper around yfinance with retries, throttling, and TTL cache."""

    @staticmethod
    def history(ticker: str, period: str = "5y", interval: str = "1d"):
        key = f"h:{ticker}:{period}:{interval}"
        def _do():
            t = yfi.Ticker(ticker)
            df = t.history(period=period, interval=interval, auto_adjust=True)
            # yfinance returns empty DF for invalid symbols – normalize to None
            if df is None or getattr(df, "empty", False):
                return None
            # normalize columns we use
            cols = {c.lower(): c for c in df.columns}
            # for safety, construct a dict with lowercase keys -> series
            want = {}
            for c in ("Close", "Adj Close", "Open", "High", "Low", "Volume"):
                if c in df.columns:
                    want[c.lower()] = df[c]
            return {"frame": df, "cols": want}
        return _retrying(_do, key=key, cache=_price_cache)

    @staticmethod
    def info(ticker: str) -> dict:
        key = f"i:{ticker}"
        def _do():
            t = yfi.Ticker(ticker)
            info = getattr(t, "fast_info", None)
            # fall back to .info if fast_info missing
            if info is None or not hasattr(info, "__dict__"):
                d = getattr(t, "info", {}) or {}
            else:
                d = dict(info.__dict__)
            # ensure dict
            return dict(d or {})
        return _retrying(_do, key=key, cache=_info_cache) or {}

    @staticmethod
    def fx(pair: str = "AUDUSD=X") -> Optional[float]:
        key = f"fx:{pair}"
        def _do():
            t = yfi.Ticker(pair)
            df = t.history(period="7d", interval="1d", auto_adjust=True)
            if df is None or getattr(df, "empty", False):
                return None
            return float(df["Close"].iloc[-1])
        return _retrying(_do, key=key, cache=_fx_cache)

# default instance used by Scanner
# --- Back-compat shim -------------------------------------------------
# If you already provide one of these names, this is harmless.

# A simple factory that returns your provider instance
try:
    def get_provider():
        # CHANGE the right-hand side to your actual class/factory:
        # e.g. return YahooProvider()
        return YF()  # or YahooProvider(), or Provider(), etc.
except NameError:
    pass

# Stable alias used by older modules
try:
    YFProvider = YF  # or: YFProvider = YahooProvider
except NameError:
    pass

