from __future__ import annotations

import os
import re
import glob
import time
import json
import csv
import io
import logging
import threading
from datetime import datetime, timedelta
from typing import List, Optional, Iterable, Dict, Set, Tuple

import requests
from bs4 import BeautifulSoup

from fastapi import (
    FastAPI, Depends, UploadFile, File, Response, HTTPException, Request
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from pydantic import BaseModel

from .config import load_config
from .scanner import Scanner
from .db import Base, engine, get_db
from .models import Holding as HoldingModel
from .schemas import HoldingIn, HoldingOut, SignalOut, PortfolioSnapshot
from .analytics import PortfolioAnalytics
from .backtester import Backtester
from .providers.events import EventsProvider
from .providers.announcements_asx import ASXAnnouncements

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log = logging.getLogger("scanner")
if not log.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
log.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# App & globals
# ---------------------------------------------------------------------------
app = FastAPI(title="Portfolio Scanner AU+US")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)

CFG = load_config()
SC  = Scanner(CFG)
PA  = PortfolioAnalytics(CFG)
BT  = Backtester(CFG)
EV  = EventsProvider()
ASX = ASXAnnouncements()

# ---------------------------------------------------------------------------
# Universe handling + caching
# ---------------------------------------------------------------------------
UNIVERSE_DIR       = os.getenv("UNIVERSE_DIR", "/app/data/universe")
CACHE_DIR          = os.getenv("CACHE_DIR", "/app/.cache")
UNIVERSE_TTL_MIN   = int(os.getenv("UNIVERSE_TTL_MIN", "1440"))  # 24h
SCAN_BG            = os.getenv("SCAN_BG", "0") == "1"
SCAN_STEP_SEC      = float(os.getenv("SCAN_STEP_SEC", "2"))
SCAN_DEFAULT_AUTOS = ["auto:sp500", "auto:asx200"]

os.makedirs(CACHE_DIR, exist_ok=True)

UA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

# minimal seeds if all else fails
SEED_SP500 = ["AAPL","MSFT","GOOGL","AMZN","NVDA","META","BRK-B","LLY","JPM","V","XOM","UNH","JNJ","MA","HD"]
SEED_ASX200 = ["CBA.AX","BHP.AX","CSL.AX","NAB.AX","WBC.AX","ANZ.AX","WES.AX","WOW.AX","TLS.AX","WDS.AX"]

def _cache_path(key: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", key)
    return os.path.join(CACHE_DIR, f"{safe}.json")

def _read_cache_json(path: str, ttl_min: Optional[int] = None):
    try:
        if ttl_min is not None:
            st = os.stat(path)
            if (time.time() - st.st_mtime) / 60.0 > ttl_min:
                return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _write_cache_json(path: str, data) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        log.warning("Failed to write cache %s: %s", path, e)

def _universes_from_config() -> list[str]:
    if hasattr(CFG, "universes"):
        val = getattr(CFG, "universes", None)
    else:
        val = CFG.get("universes") if isinstance(CFG, dict) else None
    if not val:
        return []
    return [str(x).strip().lower() for x in val if str(x).strip()]

def _resolve_universe_names(param: Optional[str]) -> list[str]:
    if not param:
        return _universes_from_config()
    return [s.strip().lower() for s in param.split(",") if s.strip()]

def _read_universe_file(name: str) -> list[str]:
    p = os.path.join(UNIVERSE_DIR, f"{name}.txt")
    if not os.path.exists(p):
        return []
    out = []
    try:
        with open(p, "r", encoding="utf-8") as f:
            for ln in f:
                s = ln.strip()
                if not s or s.startswith("#"):
                    continue
                out.append(s.upper())
    except Exception as e:
        log.warning("Failed reading universe file %s: %s", p, e)
        return []
    return out

def _fetch_csv_symbols(url: str, symbol_cols: list[str], transform=None) -> list[str]:
    log.info("Trying CSV universe: %s", url)
    r = requests.get(url, headers=UA_HEADERS, timeout=30)
    r.raise_for_status()
    rows = []
    rdr = csv.DictReader(io.StringIO(r.text))
    for row in rdr:
        for col in symbol_cols:
            if col in row and row[col]:
                sym = row[col].strip()
                if transform:
                    sym = transform(sym)
                if sym:
                    rows.append(sym.upper())
                break
    # de-dup keep order
    seen = set(); uniq = []
    for t in rows:
        if t not in seen:
            seen.add(t); uniq.append(t)
    log.info("CSV universe %s -> %d symbols", url, len(uniq))
    return uniq

def _fetch_sp500_from_wikipedia() -> list[str]:
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    log.info("Fetching S&P 500 from %s", url)
    r = requests.get(url, headers=UA_HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    table = soup.find("table", {"id": "constituents"}) or soup.find("table", {"class": "wikitable"})
    out = []
    if table:
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if not tds:
                continue
            tk = (tds[0].get_text(strip=True) or "").upper()
            if not tk:
                continue
            out.append(tk.replace(".", "-"))  # BRK.B -> BRK-B for Yahoo
    seen = set(); uniq = []
    for t in out:
        if t not in seen:
            seen.add(t); uniq.append(t)
    log.info("Wikipedia S&P500 -> %d symbols", len(uniq))
    return uniq

def _fetch_asx200_from_wikipedia() -> list[str]:
    url = "https://en.wikipedia.org/wiki/S%26P/ASX_200"
    log.info("Fetching ASX200 from %s", url)
    r = requests.get(url, headers=UA_HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    out = []
    for table in soup.find_all("table", {"class": "wikitable"}):
        for tr in table.find_all("tr"):
            tds = tr.find_all(["td","th"])
            if len(tds) < 2:
                continue
            cand = None
            for td in tds:
                text = td.get_text(strip=True).upper()
                if re.fullmatch(r"[A-Z0-9]{2,6}", text):
                    cand = text
                    break
            if cand:
                out.append(cand + ".AX")
    seen = set(); uniq = []
    for t in out:
        if t not in seen:
            seen.add(t); uniq.append(t)
    log.info("Wikipedia ASX200 -> %d symbols", len(uniq))
    return uniq

def _auto_universe_fetch(name: str) -> list[str]:
    if not name.startswith("auto:"):
        return []
    cache_file = _cache_path(f"universe_{name}")
    cached = _read_cache_json(cache_file, UNIVERSE_TTL_MIN)
    if cached:
        return [str(x).upper() for x in cached]

    base = name.split("auto:", 1)[1]
    tickers: list[str] = []
    try:
        if base == "sp500":
            tickers = _fetch_sp500_from_wikipedia()
        elif base == "asx200":
            tickers = _fetch_asx200_from_wikipedia()
        else:
            log.warning("Unknown auto universe: %s", name)
            tickers = []

        if not tickers and base == "sp500":
            tickers = _fetch_csv_symbols(
                "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv",
                ["Symbol","symbol"],
                transform=lambda s: s.replace(".", "-"),
            )
        if not tickers and base == "asx200":
            for url in [
                "https://raw.githubusercontent.com/wenboyu2/ASX200-List/master/asx200.csv",
                "https://raw.githubusercontent.com/theeconomistphd/asx200/master/asx200.csv",
            ]:
                try:
                    tickers = _fetch_csv_symbols(
                        url,
                        ["ASX code","ASX Code","Code","Ticker","Symbol"],
                        transform=lambda s: s.upper() + ("" if s.endswith(".AX") else ".AX"),
                    )
                    if tickers:
                        break
                except Exception as e:
                    log.info("ASX200 CSV fallback failed %s: %s", url, e)

        if not tickers and os.path.isdir(UNIVERSE_DIR):
            fn = "sp500" if base == "sp500" else "asx200"
            tickers = _read_universe_file(fn)

        if not tickers:
            tickers = (SEED_SP500 if base == "sp500" else SEED_ASX200)
            log.warning("Auto universe %s falling back to built-in seed (%d symbols)", name, len(tickers))

    except requests.HTTPError as e:
        log.error("Auto universe fetch failed for %s: HTTP %s", name, e)
        tickers = (SEED_SP500 if base == "sp500" else SEED_ASX200)
    except Exception as e:
        log.error("Auto universe fetch failed for %s: %s", name, e)
        tickers = (SEED_SP500 if base == "sp500" else SEED_ASX200)

    if tickers:
        _write_cache_json(cache_file, tickers)
    return [t.upper() for t in tickers]

def _load_universe_tickers(names: Iterable[str]) -> Set[str]:
    out: Set[str] = set()
    for n in names:
        if n.startswith("auto:"):
            out.update(_auto_universe_fetch(n))
        else:
            out.update(_read_universe_file(n))
    return out

# ---------------------------------------------------------------------------
# Background incremental scanner (optional)
# ---------------------------------------------------------------------------
SCAN_LIST_PATH   = _cache_path("universe_scan_list")
SCAN_STATE_PATH  = _cache_path("scan_state")
SIGNALS_PATH     = _cache_path("signals_cache")

def _bg_load_list() -> list[str]:
    lst = _read_cache_json(SCAN_LIST_PATH, None) or []
    return [str(x).upper() for x in lst]

def _bg_save_list(lst: list[str]) -> None:
    _write_cache_json(SCAN_LIST_PATH, lst)

def _bg_load_state() -> dict:
    return _read_cache_json(SCAN_STATE_PATH, None) or {"i": 0, "started": datetime.utcnow().isoformat(), "last_ts": None}

def _bg_save_state(st: dict) -> None:
    _write_cache_json(SCAN_STATE_PATH, st)

def _bg_load_signals() -> dict:
    return _read_cache_json(SIGNALS_PATH, None) or {}

def _bg_save_signals(d: dict) -> None:
    _write_cache_json(SIGNALS_PATH, d)

def _prepare_scan_list():
    # holdings + watchlist + default autos
    base = set(CFG.watchlist or []) | set([h.ticker for h in (CFG.holdings or [])])
    base |= _load_universe_tickers(SCAN_DEFAULT_AUTOS)
    lst = sorted(base)
    if not lst:
        lst = SEED_SP500 + SEED_ASX200
    _bg_save_list(lst)
    st = _bg_load_state()
    st["i"] = 0
    st["started"] = datetime.utcnow().isoformat()
    _bg_save_state(st)
    log.info("bg-scan: prepared list with %d tickers", len(lst))

def _bg_scan_loop():
    # avoid multiple loops when --reload spawns another process
    pid_guard = _cache_path(f"bg_pid_{os.getpid()}")
    if os.path.exists(pid_guard):
        return
    _write_cache_json(pid_guard, {"pid": os.getpid(), "ts": time.time()})

    if not os.path.exists(SCAN_LIST_PATH):
        _prepare_scan_list()

    while True:
        try:
            lst = _bg_load_list()
            if not lst:
                _prepare_scan_list()
                time.sleep(5)
                continue
            st = _bg_load_state()
            i = int(st.get("i", 0))
            if i >= len(lst):
                i = 0
                st["i"] = 0
            tk = lst[i]
            # scan single ticker
            rows = SC.screen([tk])  # returns list[dict]
            sigs = _bg_load_signals()
            if rows:
                sigs[tk] = rows[0]
            else:
                # mark as scanned with neutral record (prevents re-scanning immediately)
                sigs.setdefault(tk, {"ticker": tk, "side": "HOLD", "reasons": [], "score": 0.0, "px": 0.0,
                                     "asof": datetime.utcnow().isoformat(), "extras": {}})
            _bg_save_signals(sigs)
            st["i"] = i + 1
            st["last_ts"] = datetime.utcnow().isoformat()
            _bg_save_state(st)
            time.sleep(SCAN_STEP_SEC)
        except Exception as e:
            log.error("bg-scan error: %s", e)
            time.sleep(5)

if SCAN_BG:
    t = threading.Thread(target=_bg_scan_loop, name="bg-scan", daemon=True)
    t.start()
    log.info("Background incremental scanner started (step %.2fs)", SCAN_STEP_SEC)

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/api/universes")
def list_universes():
    files = []
    if os.path.isdir(UNIVERSE_DIR):
        files = sorted([p[:-4] for p in os.listdir(UNIVERSE_DIR) if p.endswith(".txt")])
    return {"configured": _universes_from_config(),
            "available_files": files,
            "available_auto": SCAN_DEFAULT_AUTOS}

@app.get("/api/universe/peek")
def universe_peek(names: str):
    lst = _resolve_universe_names(names)
    tickers = sorted(_load_universe_tickers(lst))
    return {"names": lst, "count": len(tickers), "sample": tickers[:20]}

@app.post("/api/universe/refresh")
def refresh_universe(names: Optional[str] = None):
    req = _resolve_universe_names(names) or SCAN_DEFAULT_AUTOS
    refreshed: Dict[str, int] = {}
    for n in req:
        if n.startswith("auto:"):
            ticks = _auto_universe_fetch(n)
            refreshed[n] = len(ticks)
    # also refresh bg list if bg running
    if SCAN_BG:
        _prepare_scan_list()
    return {"refreshed": refreshed}

@app.get("/api/scan_queue/status")
def scan_queue_status():
    lst = _bg_load_list()
    st = _bg_load_state()
    return {"total": len(lst), "index": int(st.get("i", 0)),
            "started": st.get("started"), "last_tick": st.get("last_ts"),
            "bg_enabled": SCAN_BG}

@app.post("/api/scan_queue/reset")
def scan_queue_reset():
    _prepare_scan_list()
    _write_cache_json(SIGNALS_PATH, {})
    return {"ok": True}

@app.get("/api/scan_cached")
def scan_cached(limit: int = 200):
    sigs = _bg_load_signals()
    rows = list(sigs.values())
    # try to sort by score desc if present
    rows.sort(key=lambda r: float(r.get("score", 0.0)), reverse=True)
    if limit and limit > 0:
        rows = rows[:limit]
    return rows

@app.get("/api/config")
def get_config():
    return CFG.model_dump() if hasattr(CFG, "model_dump") else CFG

# ---- Holdings CRUD ----

@app.get("/api/holdings", response_model=list[HoldingOut])
def list_holdings(db: Session = Depends(get_db)):
    rows = db.query(HoldingModel).all()
    return [HoldingOut(id=r.id, ticker=r.ticker, qty=r.qty, avg_price=r.avg_price) for r in rows]

@app.post("/api/holdings", response_model=HoldingOut)
def add_holding(h: HoldingIn, db: Session = Depends(get_db)):
    row = HoldingModel(ticker=h.ticker.upper(), qty=h.qty, avg_price=h.avg_price)
    db.add(row); db.commit(); db.refresh(row)
    return HoldingOut(id=row.id, ticker=row.ticker, qty=row.qty, avg_price=row.avg_price)

@app.delete("/api/holdings/{hid}")
def del_holding(hid: int, db: Session = Depends(get_db)):
    db.query(HoldingModel).filter(HoldingModel.id == hid).delete()
    db.commit()
    return {"ok": True}

@app.post("/api/holdings/import")
async def import_holdings(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    f = io.StringIO(content.decode("utf-8"))
    reader = csv.DictReader(f)
    count = 0
    for row in reader:
        ticker = (row.get("ticker","") or "").upper().strip()
        if not ticker:
            continue
        qty = float(row.get("qty","0") or 0)
        avg = float(row.get("avg_price","0") or 0)
        db.add(HoldingModel(ticker=ticker, qty=qty, avg_price=avg))
        count += 1
    db.commit()
    return {"imported": count}

# ---- Scan ----

def _parse_chunk(s: Optional[str]) -> Optional[Tuple[int, int]]:
    if not s:
        return None
    try:
        parts = str(s).split(':', 1)
        if len(parts) != 2:
            return None
        start = int(parts[0].strip())
        end = int(parts[1].strip())
        if start < 0 or end <= start:
            return None
        return (start, end)
    except Exception:
        return None

@app.get("/api/scan", response_model=list[SignalOut] | dict)
def run_scan(
    scope: str = "mylist",
    universes: Optional[str] = None,
    max: Optional[int] = None,
    chunk: Optional[str] = None
):
    log_api = logging.getLogger("uvicorn.error")

    # base set
    base = set()
    if scope in ("mylist", "all"):
        base |= set(CFG.watchlist or [])
        base |= set([h.ticker for h in (CFG.holdings or [])])
        try:
            with next(get_db()) as db:   # type: ignore
                for h in db.query(HoldingModel).all():
                    base.add(h.ticker)
        except Exception as e:
            log_api.warning("scan: db holdings unavailable: %s", e)
        if scope == "all":
            autos = [u.strip() for u in (universes.split(",") if universes else SCAN_DEFAULT_AUTOS)]
            all_auto = []
            for u in autos:
                try:
                    syms = SC.resolve_universe(u)
                    if syms:
                        all_auto.extend(syms)
                        log_api.info("scan: universe %-12s -> %d symbols", u, len(syms))
                    else:
                        log_api.warning("scan: universe %s returned 0 symbols", u)
                except Exception as e:
                    log_api.error("scan: universe %s error: %s", u, e)
            base |= set(all_auto)

    universe = sorted(base)
    total_before = len(universe)

    if max is not None and max > 0 and len(universe) > max:
        log_api.info("scan: capping universe from %d to %d (max)", len(universe), max)
        universe = universe[:max]

    window = _parse_chunk(chunk)
    if window:
        start, end = window
        start = min(start, len(universe))
        end = min(end, len(universe))
        windowed = universe[start:end] if end > start else []
        log_api.info("scan: window %s -> %d symbols (total=%d)", f"{start}:{end}", len(windowed), total_before)
        rows = [SignalOut(**r) for r in SC.screen(windowed)]
        return {"items": [r.model_dump() for r in rows], "total": total_before, "slice": f"{start}:{end}"}

    if not universe:
        log_api.info("scan: input set is empty; returning no signals")
        return []

    log_api.info("scan: running over %d symbols (total before caps/chunks=%d)", len(universe), total_before)
    res = SC.screen(universe)
    return [SignalOut(**r) for r in res]

# ---- Portfolio ----

@app.get("/api/portfolio", response_model=PortfolioSnapshot)
def portfolio_snapshot(db: Session = Depends(get_db)):
    hrows = db.query(HoldingModel).all()
    snap = PA.snapshot([(h.ticker, h.qty, h.avg_price) for h in hrows])
    return PortfolioSnapshot(**snap)

@app.get("/api/portfolio_breakdown")
def portfolio_breakdown(db: Session = Depends(get_db), by: str = "ticker"):
    hrows = db.query(HoldingModel).all()
    rows = []
    for h in hrows:
        df = SC.yf.history(h.ticker, period="1mo")
        if df is None or df.empty or "close" not in df:
            continue
        px = float(df["close"].iloc[-1])
        info = SC.yf.info(h.ticker)
        sector = info.get("sector") or "Unknown"
        country = info.get("country") or ("Australia" if h.ticker.endswith(".AX") else "United States")
        region = "Australia" if h.ticker.endswith(".AX") else (country or "United States")

        ccy = "AUD" if h.ticker.endswith(".AX") else "USD"
        fx  = SC.yf.fx("AUDUSD=X") or 0.65
        base = CFG.base_currency
        if base == ccy: px_base = px
        elif base == "AUD" and ccy == "USD": px_base = px / fx
        elif base == "USD" and ccy == "AUD": px_base = px * fx
        else: px_base = px

        val = float(h.qty * px_base)
        rows.append({"ticker": h.ticker, "sector": sector, "region": region, "value": val})

    if by == "sector":
        agg: Dict[str, float] = {}
        for r in rows: agg[r["sector"]] = agg.get(r["sector"], 0.0) + r["value"]
        items = [{"label": k, "value": v} for k, v in agg.items()]
    elif by == "region":
        agg: Dict[str, float] = {}
        for r in rows: agg[r["region"]] = agg.get(r["region"], 0.0) + r["value"]
        items = [{"label": k, "value": v} for k, v in agg.items()]
    else:
        items = [{"label": r["ticker"], "ticker": r["ticker"], "value": r["value"]} for r in rows]

    total = sum(i["value"] for i in items) or 1.0
    for i in items:
        i["weight"] = i["value"] / total
    items.sort(key=lambda x: x["weight"], reverse=True)
    return {"base_currency": CFG.base_currency, "mode": by, "items": items}

# ---- Cache ----

@app.get("/api/cache")
def cache_list():
    items = []
    for p in glob.glob(os.path.join(CACHE_DIR, "*.json")):
        try:
            st = os.stat(p)
            items.append({"file": os.path.basename(p), "size": st.st_size,
                          "age_min": round((time.time() - st.st_mtime)/60, 1)})
        except Exception:
            continue
    return {"dir": CACHE_DIR, "count": len(items), "items": sorted(items, key=lambda x: x["age_min"])}

@app.post("/api/cache/clear")
def cache_clear():
    removed = 0
    for p in glob.glob(os.path.join(CACHE_DIR, "*.json")):
        try:
            os.remove(p); removed += 1
        except Exception:
            pass
    return {"cleared": removed}

# ---- Events / Announcements / Upcoming / News ----

@app.get("/api/events/{ticker}")
def events(ticker: str):
    return EV.earnings_and_div(ticker.upper())

@app.get("/api/announcements")
def announcements(ticker: Optional[str] = None, db: Session = Depends(get_db)):
    if ticker:
        return {ticker.upper(): ASX.recent(ticker.upper())}
    hrows = db.query(HoldingModel).all()
    res: Dict[str, list] = {}
    for h in hrows:
        if h.ticker.endswith(".AX"):
            res[h.ticker] = ASX.recent(h.ticker)
    return res

@app.get("/api/upcoming")
def upcoming(days: int = 7, db: Session = Depends(get_db)):
    hrows = db.query(HoldingModel).all()
    end = datetime.utcnow() + timedelta(days=int(days))
    res = []
    for h in hrows:
        ev = EV.earnings_and_div(h.ticker)
        for kind in ["earnings_date", "ex_div_date"]:
            dt = ev.get(kind)
            if not dt:
                continue
            try:
                d = datetime.fromisoformat(dt.replace("Z", ""))
            except Exception:
                continue
            if d <= end and d >= datetime.utcnow():
                res.append({"ticker": h.ticker,
                            "type": "Earnings" if kind == "earnings_date" else "Ex-Div",
                            "date": d.isoformat()})
    res.sort(key=lambda x: x["date"])
    return res

@app.get("/api/news")
def news(ticker: str, days: int = 7, limit: int = 20):
    return SC.news(ticker.upper(), days=days, limit=limit)

# ---- Backtest ----

@app.get("/api/backtest")
def backtest(tickers: str, years: int = 5):
    syms = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    return BT.run_multi(syms, years)

@app.get("/api/backtest.csv")
def backtest_csv(tickers: str, years: int = 5):
    data = BT.run_multi([t.strip().upper() for t in tickers.split(",") if t.strip()], years)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ticker","cagr","max_dd","sharpe","trades"])
    for r in data.get("results", []):
        w.writerow([r["ticker"], r["cagr"], r["max_dd"], r["sharpe"], r["trades"]])
    return Response(content=buf.getvalue(), media_type="text/csv")

@app.get("/api/backtest_equity")
def backtest_equity(ticker: str, years: int = 5):
    return BT.equity_series(ticker.upper(), years)

# ---- Rebalance (by ticker) ----

class TargetIn(BaseModel):
    ticker: str
    target_weight: float

class RebalanceRequest(BaseModel):
    targets: List[TargetIn]
    cash: float | None = None
    min_order_value: float | None = None
    lot_size: int | None = 1
    seed_source: str | None = "watchlist"

def _apply_constraints(side: str, qty: float, notional: float, lot_size: int, min_order_value: float):
    if lot_size and lot_size > 1:
        qty = round(qty / lot_size) * lot_size
    if abs(notional) < (min_order_value or 0):
        return 0.0, 0.0
    return qty, notional

@app.post("/api/rebalance_suggest")
def rebalance_suggest(req: RebalanceRequest, db: Session = Depends(get_db)):
    hrows = db.query(HoldingModel).all()

    def price_base(tk: str) -> float:
        df = SC.yf.history(tk, period="1mo")
        if df is None or df.empty or "close" not in df:
            return 0.0
        px = float(df["close"].iloc[-1])
        ccy = "AUD" if tk.endswith(".AX") else "USD"
        fx  = SC.yf.fx("AUDUSD=X") or 0.65
        base = CFG.base_currency
        if base == ccy: return px
        if base == "AUD" and ccy == "USD": return px / fx
        if base == "USD" and ccy == "AUD": return px * fx
        return px

    vals   = {h.ticker: h.qty * price_base(h.ticker) for h in hrows}
    prices = {h.ticker: price_base(h.ticker) for h in hrows}
    nav = sum(vals.values())
    if req.cash:
        nav += float(req.cash)

    T = {t.ticker.upper(): float(t.target_weight) for t in req.targets}
    tsum = sum(T.values())
    if tsum > 0 and abs(tsum - 1.0) > 1e-6:
        T = {k: v/tsum for k, v in T.items()}

    desired = {tk: nav * T.get(tk, 0.0) for tk in set(list(vals.keys()) + list(T.keys()))}
    lot = int(req.lot_size or 1)
    mov = float(req.min_order_value or 0)

    out = []
    for tk in sorted(desired.keys()):
        cur = vals.get(tk, 0.0)
        dval = desired[tk] - cur
        px = prices.get(tk) or price_base(tk)
        qty = (dval/px) if px else 0.0
        side = "BUY" if dval > 0 else ("SELL" if dval < 0 else "HOLD")
        qty_adj, not_adj = _apply_constraints(side, qty, dval, lot, mov)
        out.append({"ticker": tk, "side": side, "qty_delta": round(qty_adj, 4),
                    "notional_delta": round(not_adj, 2), "price": round(px, 4)})
    out.sort(key=lambda x: abs(x["notional_delta"]), reverse=True)
    return {"base_currency": CFG.base_currency, "nav_with_cash": nav, "suggestions": out}

# ---- Rebalance by bucket (sector/region) ----

class BucketTargetIn(BaseModel):
    bucket: str
    target_weight: float

class BucketRebalanceReq(BaseModel):
    mode: str
    targets: List[BucketTargetIn]
    cash: float | None = None
    min_order_value: float | None = None
    lot_size: int | None = 1
    seed_source: str | None = "watchlist"

@app.post("/api/rebalance_by_bucket")
def rebalance_by_bucket(req: BucketRebalanceReq, db: Session = Depends(get_db)):
    mode = (req.mode or "sector").lower()
    assert mode in ("sector", "region"), "mode must be 'sector' or 'region'"

    def price_base(tk: str) -> float:
        df = SC.yf.history(tk, period="1mo")
        if df is None or df.empty or "close" not in df:
            return 0.0
        px = float(df["close"].iloc[-1])
        ccy = "AUD" if tk.endswith(".AX") else "USD"
        fx  = SC.yf.fx("AUDUSD=X") or 0.65
        base = CFG.base_currency
        if base == ccy: return px
        if base == "AUD" and ccy == "USD": return px / fx
        if base == "USD" and ccy == "AUD": return px * fx
        return px

    hrows = db.query(HoldingModel).all()
    rows = []
    for h in hrows:
        info = SC.yf.info(h.ticker)
        sector = info.get("sector") or "Unknown"
        country = info.get("country") or ("Australia" if h.ticker.endswith(".AX") else "United States")
        region = "Australia" if h.ticker.endswith(".AX") else (country or "United States")
        px = price_base(h.ticker)
        val = h.qty * px
        rows.append({"ticker": h.ticker, "sector": sector, "region": region,
                     "value": float(val), "px": px})

    nav = sum(r["value"] for r in rows)
    if req.cash:
        nav += float(req.cash)

    T = {t.bucket: float(t.target_weight) for t in req.targets}
    tsum = sum(T.values())
    if tsum > 0 and abs(tsum - 1.0) > 1e-6:
        T = {k: v/tsum for k, v in T.items()}

    cur_bucket: Dict[str, float] = {}
    for r in rows:
        b = r[mode]
        cur_bucket[b] = cur_bucket.get(b, 0.0) + r["value"]

    desired_bucket: Dict[str, float] = {b: nav * T.get(b, 0.0)
                                        for b in set(list(cur_bucket.keys()) + list(T.keys()))}

    suggestions = []
    lot = int(req.lot_size or 1)
    mov = float(req.min_order_value or 0)

    idx: Dict[str, list] = {}
    for r in rows:
        idx.setdefault(r[mode], []).append(r)

    for b, d_target in desired_bucket.items():
        cur_val = cur_bucket.get(b, 0.0)
        delta_b = d_target - cur_val
        if abs(delta_b) < 1e-9:
            continue
        tickers = idx.get(b, []) or rows
        total_in_bucket = sum(t["value"] for t in tickers) or 1.0
        for t in tickers:
            share = (t["value"]/total_in_bucket) if total_in_bucket > 0 else (1.0/max(1, len(tickers)))
            dval = delta_b * share
            px = t["px"] or price_base(t["ticker"])
            qty = (dval/px) if px else 0.0
            side = "BUY" if dval > 0 else ("SELL" if dval < 0 else "HOLD")
            qty_adj, not_adj = _apply_constraints(side, qty, dval, lot, mov)
            if abs(not_adj) > 0:
                suggestions.append({
                    "ticker": t["ticker"], "bucket": b, "side": side,
                    "qty_delta": round(qty_adj, 4),
                    "notional_delta": round(not_adj, 2),
                    "price": round(px, 4),
                })

    suggestions.sort(key=lambda x: abs(x["notional_delta"]), reverse=True)
    return {"mode": mode.upper(), "base_currency": CFG.base_currency,
            "nav_with_cash": nav, "suggestions": suggestions}

# ---- Friendlier JSON 404 for /api/*
@app.exception_handler(HTTPException)
async def _friendly_404(request: Request, exc: HTTPException):
    if exc.status_code == 404 and request.url.path.startswith("/api/"):
        return Response(content=b'{"detail":"Not Found"}',
                        media_type="application/json", status_code=404)
    raise exc

# ---------------------------------------------------------------------------
# Mount static LAST (so /api/* isn't shadowed)
# ---------------------------------------------------------------------------
app.mount("/", StaticFiles(directory="/app/static", html=True), name="static")
