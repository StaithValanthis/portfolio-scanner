from fastapi import FastAPI, Depends, UploadFile, File, Response, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os, glob, time
from datetime import datetime, timedelta

from .config import load_config
from .scanner import Scanner
from .db import Base, engine, get_db
from .models import Holding as HoldingModel, Signal as SignalModel, Metric
from .schemas import HoldingIn, HoldingOut, SignalOut, PortfolioSnapshot
from sqlalchemy.orm import Session
from .analytics import PortfolioAnalytics
from .backtester import Backtester
from .providers.events import EventsProvider
from .providers.announcements_asx import ASXAnnouncements
from .alerts import notify_signals, notify_riskflags
from .utils import cache as cache_util

from pydantic import BaseModel
from typing import List, Iterable, Set, Optional, Dict


app = FastAPI(title="Portfolio Scanner AU+US")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# --- CORE SINGLETONS ---
Base.metadata.create_all(bind=engine)
CFG = load_config()                            # pydantic model (has .model_dump())
SC  = Scanner(CFG)
PA  = PortfolioAnalytics(CFG)
BT  = Backtester(CFG)
EV  = EventsProvider()
ASX = ASXAnnouncements()

# ========= Universes support =========
UNIVERSE_DIR = os.getenv("UNIVERSE_DIR", "/app/data/universe")

def _read_universe_file(name: str) -> list[str]:
    """
    Read universe file: /app/data/universe/<name>.txt
    One ticker per line, allow comments with '#'.
    """
    p = os.path.join(UNIVERSE_DIR, f"{name}.txt")
    if not os.path.exists(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            out = []
            for ln in f:
                s = ln.strip()
                if not s or s.startswith("#"):
                    continue
                out.append(s.upper())
            return out
    except Exception:
        return []

def _universes_from_config() -> list[str]:
    """
    Names from config.yaml, e.g.:
    universes:
      - sp500
      - asx200
    """
    # CFG may be a pydantic model; fall back to dict access if needed
    if hasattr(CFG, "universes"):
        val = getattr(CFG, "universes", None)
        if val:
            return [str(x).strip().lower() for x in val if str(x).strip()]
        return []
    if isinstance(CFG, dict):
        val = (CFG.get("universes") or [])
        return [str(x).strip().lower() for x in val if str(x).strip()]
    return []

def _resolve_universe_names(param: Optional[str]) -> list[str]:
    """Parse query param universes=sp500,asx200 into ['sp500','asx200']"""
    if not param:
        return _universes_from_config()
    return [s.strip().lower() for s in param.split(",") if s.strip()]

def _load_universe_tickers(names: Iterable[str]) -> Set[str]:
    """Union of all tickers from the given universe names."""
    out: Set[str] = set()
    for n in names:
        for tk in _read_universe_file(n):
            out.add(tk)
    return out


# ========= API =========

@app.get("/api/universes")
def list_universes():
    """List configured and available universes."""
    if os.path.isdir(UNIVERSE_DIR):
        avail = sorted([p[:-4] for p in os.listdir(UNIVERSE_DIR) if p.endswith(".txt")])
    else:
        avail = []
    return {"configured": _universes_from_config(), "available": avail}

@app.get("/api/config")
def get_config():
    # pydantic v2 model -> model_dump(); otherwise return as-is
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
    import io, csv
    f = io.StringIO(content.decode("utf-8"))
    reader = csv.DictReader(f)
    count = 0
    for row in reader:
        ticker = (row.get("ticker", "") or "").upper().strip()
        if not ticker:
            continue
        qty = float(row.get("qty", "0") or 0)
        avg = float(row.get("avg_price", "0") or 0)
        db.add(HoldingModel(ticker=ticker, qty=qty, avg_price=avg))
        count += 1
    db.commit()
    return {"imported": count}

# ---- Scanner ----

@app.get("/api/scan", response_model=list[SignalOut])
def run_scan(scope: str = Query("mylist", pattern="^(mylist|all)$"),
             universes: Optional[str] = None):
    """
    scope=mylist  -> holdings ∪ watchlist
    scope=all     -> (holdings ∪ watchlist) ∪ universes (from query or config)
    universes     -> comma-separated names (e.g. sp500,asx200)
    """
    base = set(CFG.watchlist or []) | {h.ticker for h in (CFG.holdings or [])}
    if scope == "all":
        names = _resolve_universe_names(universes)
        if not names:
            names = _universes_from_config()
        base |= _load_universe_tickers(names)

    res = SC.screen(sorted(base))
    return [SignalOut(**r) for r in res]

# ---- Portfolio snapshot / breakdown ----

@app.get("/api/portfolio", response_model=PortfolioSnapshot)
def portfolio_snapshot(db: Session = Depends(get_db)):
    hrows = db.query(HoldingModel).all()
    snap = PA.snapshot([(h.ticker, h.qty, h.avg_price) for h in hrows])
    return PortfolioSnapshot(**snap)

@app.get("/api/portfolio_breakdown")
def portfolio_breakdown(db: Session = Depends(get_db), by: str = "ticker"):
    """
    Returns { base_currency, mode, items:[{label, ticker?, value, weight}, ...] }
    """
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
        fx = SC.yf.fx("AUDUSD=X") or 0.65
        base = CFG.base_currency
        if base == ccy:
            px_base = px
        elif base == "AUD" and ccy == "USD":
            px_base = px / fx
        elif base == "USD" and ccy == "AUD":
            px_base = px * fx
        else:
            px_base = px

        val = float(h.qty * px_base)
        rows.append({"ticker": h.ticker, "sector": sector, "region": region, "value": val})

    if by == "sector":
        agg: Dict[str, float] = {}
        for r in rows:
            agg[r["sector"]] = agg.get(r["sector"], 0.0) + r["value"]
        items = [{"label": k, "value": v} for k, v in agg.items()]
    elif by == "region":
        agg: Dict[str, float] = {}
        for r in rows:
            agg[r["region"]] = agg.get(r["region"], 0.0) + r["value"]
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
    cdir = os.getenv("CACHE_DIR", "/app/.cache")
    items = []
    for p in glob.glob(os.path.join(cdir, "*.json")):
        try:
            st = os.stat(p)
            items.append(
                {"file": os.path.basename(p), "size": st.st_size, "age_min": round((time.time() - st.st_mtime) / 60, 1)}
            )
        except Exception:
            continue
    return {"dir": cdir, "count": len(items), "items": sorted(items, key=lambda x: x["age_min"])}

@app.post("/api/cache/clear")
def cache_clear():
    cdir = os.getenv("CACHE_DIR", "/app/.cache")
    removed = 0
    for p in glob.glob(os.path.join(cdir, "*.json")):
        try:
            os.remove(p)
            removed += 1
        except Exception:
            pass
    return {"cleared": removed}

# ---- Events / news / announcements ----

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
                res.append({"ticker": h.ticker, "type": "Earnings" if kind == "earnings_date" else "Ex-Div", "date": d.isoformat()})
    res.sort(key=lambda x: x["date"])
    return res

@app.get("/api/news")
def news(ticker: str, days: int = 7, limit: int = 20):
    """Pass-through to Scanner's news provider (AU + US)."""
    return SC.news(ticker.upper(), days=days, limit=limit)

# ---- Backtesting ----

@app.get("/api/backtest")
def backtest(tickers: str, years: int = 5):
    syms = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    return BT.run_multi(syms, years)

@app.get("/api/backtest.csv")
def backtest_csv(tickers: str, years: int = 5):
    data = BT.run_multi([t.strip().upper() for t in tickers.split(",") if t.strip()], years)
    import io, csv
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ticker", "cagr", "max_dd", "sharpe", "trades"])
    for r in data.get("results", []):
        w.writerow([r["ticker"], r["cagr"], r["max_dd"], r["sharpe"], r["trades"]])
    return Response(content=buf.getvalue(), media_type="text/csv")

@app.get("/api/backtest_equity")
def backtest_equity(ticker: str, years: int = 5):
    return BT.equity_series(ticker.upper(), years)

# ---- Rebalancing ----

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
        fx = SC.yf.fx("AUDUSD=X") or 0.65
        base = CFG.base_currency
        if base == ccy:
            return px
        if base == "AUD" and ccy == "USD":
            return px / fx
        if base == "USD" and ccy == "AUD":
            return px * fx
        return px

    vals = {h.ticker: h.qty * price_base(h.ticker) for h in hrows}
    prices = {h.ticker: price_base(h.ticker) for h in hrows}
    nav = sum(vals.values())
    if req.cash:
        nav += float(req.cash)

    T = {t.ticker.upper(): float(t.target_weight) for t in req.targets}
    tsum = sum(T.values())
    if tsum > 0 and abs(tsum - 1.0) > 1e-6:
        T = {k: v / tsum for k, v in T.items()}  # normalize to 1.0

    desired = {tk: nav * T.get(tk, 0.0) for tk in set(list(vals.keys()) + list(T.keys()))}
    lot = int(req.lot_size or 1)
    mov = float(req.min_order_value or 0)

    out = []
    for tk in sorted(desired.keys()):
        cur = vals.get(tk, 0.0)
        dval = desired[tk] - cur
        px = prices.get(tk) or price_base(tk)
        qty = (dval / px) if px else 0.0
        side = "BUY" if dval > 0 else ("SELL" if dval < 0 else "HOLD")
        qty_adj, not_adj = _apply_constraints(side, qty, dval, lot, mov)
        out.append({"ticker": tk, "side": side, "qty_delta": round(qty_adj, 4), "notional_delta": round(not_adj, 2), "price": round(px, 4)})
    out.sort(key=lambda x: abs(x["notional_delta"]), reverse=True)
    return {"base_currency": CFG.base_currency, "nav_with_cash": nav, "suggestions": out}

# ---- Rebalance by bucket (sector/region) ----

class BucketTargetIn(BaseModel):
    bucket: str
    target_weight: float

class BucketRebalanceReq(BaseModel):
    mode: str                                  # 'sector' or 'region'
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
        fx = SC.yf.fx("AUDUSD=X") or 0.65
        base = CFG.base_currency
        if base == ccy:
            return px
        if base == "AUD" and ccy == "USD":
            return px / fx
        if base == "USD" and ccy == "AUD":
            return px * fx
        return px

    # Gather current rows with bucket + values
    hrows = db.query(HoldingModel).all()
    rows = []
    for h in hrows:
        info = SC.yf.info(h.ticker)
        sector = info.get("sector") or "Unknown"
        country = info.get("country") or ("Australia" if h.ticker.endswith(".AX") else "United States")
        region = "Australia" if h.ticker.endswith(".AX") else (country or "United States")
        px = price_base(h.ticker)
        val = h.qty * px
        rows.append({"ticker": h.ticker, "sector": sector, "region": region, "value": float(val), "px": px})

    nav = sum(r["value"] for r in rows)
    if req.cash:
        nav += float(req.cash)

    # Normalize target weights to 1.0 if necessary
    T = {t.bucket: float(t.target_weight) for t in req.targets}
    tsum = sum(T.values())
    if tsum > 0 and abs(tsum - 1.0) > 1e-6:
        T = {k: v / tsum for k, v in T.items()}

    # Current value per bucket
    cur_bucket: Dict[str, float] = {}
    for r in rows:
        b = r[mode]
        cur_bucket[b] = cur_bucket.get(b, 0.0) + r["value"]

    # Desired value per bucket
    desired_bucket: Dict[str, float] = {b: nav * T.get(b, 0.0) for b in set(list(cur_bucket.keys()) + list(T.keys()))}

    # For each bucket, spread delta across its tickers proportionally to current value (fallback to equal if zero)
    suggestions = []
    lot = int(req.lot_size or 1)
    mov = float(req.min_order_value or 0)

    # Index tickers by bucket
    idx: Dict[str, list] = {}
    for r in rows:
        idx.setdefault(r[mode], []).append(r)

    for b, d_target in desired_bucket.items():
        cur_val = cur_bucket.get(b, 0.0)
        delta_b = d_target - cur_val
        if abs(delta_b) < 1e-9:
            continue
        tickers = idx.get(b, [])
        if not tickers:
            # no current positions in that bucket → seed: spread equally across watchlist BUYs or evenly
            # For simplicity: spread equally across all tickers we *have* in rows (if any)
            tickers = rows
        total_in_bucket = sum(t["value"] for t in tickers) or 1.0
        for t in tickers:
            share = (t["value"] / total_in_bucket) if total_in_bucket > 0 else (1.0 / max(1, len(tickers)))
            dval = delta_b * share
            px = t["px"] or price_base(t["ticker"])
            qty = (dval / px) if px else 0.0
            side = "BUY" if dval > 0 else ("SELL" if dval < 0 else "HOLD")
            qty_adj, not_adj = _apply_constraints(side, qty, dval, lot, mov)
            if abs(not_adj) > 0:
                suggestions.append({
                    "ticker": t["ticker"],
                    "bucket": b,
                    "side": side,
                    "qty_delta": round(qty_adj, 4),
                    "notional_delta": round(not_adj, 2),
                    "price": round(px, 4),
                })

    suggestions.sort(key=lambda x: abs(x["notional_delta"]), reverse=True)
    return {"mode": mode.upper(), "base_currency": CFG.base_currency, "nav_with_cash": nav, "suggestions": suggestions}

# ---- STATIC (mounted last so API routes win) ----
app.mount("/", StaticFiles(directory="/app/static", html=True), name="static")
