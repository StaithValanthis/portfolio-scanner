from fastapi import FastAPI, Depends, UploadFile, File, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
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
from typing import List

app = FastAPI(title="Portfolio Scanner AU+US")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/", StaticFiles(directory="/app/static", html=True), name="static")

Base.metadata.create_all(bind=engine)
CFG = load_config(); SC = Scanner(CFG); PA = PortfolioAnalytics(CFG); BT = Backtester(CFG); EV = EventsProvider(); ASX = ASXAnnouncements()

# --- API ---
@app.get("/api/config")
def get_config(): return CFG.model_dump()

@app.get("/api/holdings", response_model=list[HoldingOut])
def list_holdings(db: Session = Depends(get_db)):
    rows = db.query(HoldingModel).all(); return [HoldingOut(id=r.id, ticker=r.ticker, qty=r.qty, avg_price=r.avg_price) for r in rows]

@app.post("/api/holdings", response_model=HoldingOut)
def add_holding(h: HoldingIn, db: Session = Depends(get_db)):
    row = HoldingModel(ticker=h.ticker.upper(), qty=h.qty, avg_price=h.avg_price); db.add(row); db.commit(); db.refresh(row); return HoldingOut(id=row.id, ticker=row.ticker, qty=row.qty, avg_price=row.avg_price)

@app.delete("/api/holdings/{hid}")
def del_holding(hid: int, db: Session = Depends(get_db)):
    db.query(HoldingModel).filter(HoldingModel.id==hid).delete(); db.commit(); return {"ok": True}

@app.post("/api/holdings/import")
async def import_holdings(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    import io, csv
    f = io.StringIO(content.decode("utf-8")); reader = csv.DictReader(f); count = 0
    for row in reader:
        ticker = row.get("ticker","").upper().strip()
        if not ticker: continue
        qty = float(row.get("qty","0") or 0); avg = float(row.get("avg_price","0") or 0)
        db.add(HoldingModel(ticker=ticker, qty=qty, avg_price=avg)); count += 1
    db.commit(); return {"imported": count}

@app.get("/api/scan", response_model=list[SignalOut])
def run_scan():
    tickers = list({*(CFG.watchlist or []), *[h.ticker for h in (CFG.holdings or [])]})
    res = SC.screen(tickers); return [SignalOut(**r) for r in res]

@app.get("/api/portfolio", response_model=PortfolioSnapshot)
def portfolio_snapshot(db: Session = Depends(get_db)):
    hrows = db.query(HoldingModel).all(); snap = PA.snapshot([(h.ticker, h.qty, h.avg_price) for h in hrows]); return PortfolioSnapshot(**snap)

# Cache endpoints
@app.get("/api/cache")
def cache_list():
    cdir = os.getenv("CACHE_DIR", "/app/.cache"); items = []
    for p in glob.glob(os.path.join(cdir, "*.json")):
        try: st = os.stat(p); items.append({"file": os.path.basename(p), "size": st.st_size, "age_min": round((time.time()-st.st_mtime)/60, 1)})
        except Exception: continue
    return {"dir": cdir, "count": len(items), "items": sorted(items, key=lambda x: x["age_min"])}

@app.post("/api/cache/clear")
def cache_clear():
    cdir = os.getenv("CACHE_DIR", "/app/.cache"); removed = 0
    for p in glob.glob(os.path.join(cdir, "*.json")):
        try: os.remove(p); removed += 1
        except Exception: pass
    return {"cleared": removed}

# Events & announcements
@app.get("/api/events/{ticker}")
def events(ticker: str): return EV.earnings_and_div(ticker.upper())

@app.get("/api/announcements")
def announcements(ticker: str | None = None, db: Session = Depends(get_db)):
    if ticker: return {ticker.upper(): ASX.recent(ticker.upper())}
    hrows = db.query(HoldingModel).all(); res = {}
    for h in hrows:
        if h.ticker.endswith(".AX"): res[h.ticker] = ASX.recent(h.ticker)
    return res

@app.get("/api/upcoming")
def upcoming(days: int = 7, db: Session = Depends(get_db)):
    hrows = db.query(HoldingModel).all(); end = datetime.utcnow() + timedelta(days=int(days)); res = []
    for h in hrows:
        ev = EV.earnings_and_div(h.ticker)
        for kind in ["earnings_date","ex_div_date"]:
            dt = ev.get(kind)
            if not dt: continue
            try: d = datetime.fromisoformat(dt.replace('Z',''))
            except Exception: continue
            if d <= end and d >= datetime.utcnow(): res.append({"ticker": h.ticker, "type": "Earnings" if kind=="earnings_date" else "Ex-Div", "date": d.isoformat()})
    res.sort(key=lambda x: x["date"]); return res

# Backtest
@app.get("/api/backtest")
def backtest(tickers: str, years: int = 5):
    syms = [t.strip().upper() for t in tickers.split(",") if t.strip()]; return BT.run_multi(syms, years)

@app.get("/api/backtest.csv")
def backtest_csv(tickers: str, years: int = 5):
    data = BT.run_multi([t.strip().upper() for t in tickers.split(",") if t.strip()], years)
    import io, csv
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(["ticker","cagr","max_dd","sharpe","trades"])
    for r in data.get("results", []): w.writerow([r["ticker"], r["cagr"], r["max_dd"], r["sharpe"], r["trades"]])
    return Response(content=buf.getvalue(), media_type="text/csv")

@app.get("/api/backtest_equity")
def backtest_equity(ticker: str, years: int = 5): return BT.equity_series(ticker.upper(), years)

# Portfolio breakdown (ticker/sector/region)
@app.get("/api/portfolio_breakdown")
def portfolio_breakdown(db: Session = Depends(get_db), by: str = 'ticker'):
    hrows = db.query(HoldingModel).all(); snap = PA.snapshot([(h.ticker, h.qty, h.avg_price) for h in hrows]); rows = []
    for h in hrows:
        df = SC.yf.history(h.ticker, period="1mo")
        if df is None or df.empty or "close" not in df: continue
        px = float(df["close"].iloc[-1]); info = SC.yf.info(h.ticker)
        sector = info.get('sector') or 'Unknown'; country = info.get('country') or ('Australia' if h.ticker.endswith('.AX') else 'United States')
        region = 'Australia' if h.ticker.endswith('.AX') else (country or 'United States')
        ccy = "AUD" if h.ticker.endswith(".AX") else "USD"; fx = SC.yf.fx("AUDUSD=X") or 0.65; base = CFG.base_currency
        if base == ccy: px_base = px
        elif base == 'AUD' and ccy == 'USD': px_base = px / fx
        elif base == 'USD' and ccy == 'AUD': px_base = px * fx
        else: px_base = px
        val = float(h.qty * px_base)
        rows.append({"label": h.ticker, "ticker": h.ticker, "sector": sector, "region": region, "value": val})
    if by == 'sector':
        agg = {}; 
        for r in rows: agg[r['sector']] = agg.get(r['sector'], 0.0) + r['value']
        items = [{"label": k, "value": v} for k,v in agg.items()]
    elif by == 'region':
        agg = {}; 
        for r in rows: agg[r['region']] = agg.get(r['region'], 0.0) + r['value']
        items = [{"label": k, "value": v} for k,v in agg.items()]
    else:
        items = [{"label": r['ticker'], "ticker": r['ticker'], "value": r['value']} for r in rows]
    total = sum(i['value'] for i in items) or 1.0
    for i in items: i['weight'] = i['value']/total
    items.sort(key=lambda x: x['weight'], reverse=True)
    return {"base_currency": CFG.base_currency, "mode": by, "items": items}

# Rebalance (ticker)
class TargetIn(BaseModel): ticker: str; target_weight: float
class RebalanceRequest(BaseModel):
    targets: List[TargetIn]; cash: float | None = None; min_order_value: float | None = None; lot_size: int | None = 1; seed_source: str | None = "watchlist"

def _apply_constraints(side: str, qty: float, notional: float, lot_size: int, min_order_value: float):
    if lot_size and lot_size > 1: qty = round(qty / lot_size) * lot_size
    if abs(notional) < (min_order_value or 0): return 0.0, 0.0
    return qty, notional

@app.post("/api/rebalance_suggest")
def rebalance_suggest(req: RebalanceRequest, db: Session = Depends(get_db)):
    hrows = db.query(HoldingModel).all()
    def price_base(tk: str) -> float:
        df = SC.yf.history(tk, period="1mo")
        if df is None or df.empty or "close" not in df: return 0.0
        px = float(df["close"].iloc[-1]); ccy = "AUD" if tk.endswith(".AX") else "USD"; fx = SC.yf.fx("AUDUSD=X") or 0.65; base = CFG.base_currency
        if base == ccy: return px
        if base == "AUD" and ccy == "USD": return px / fx
        if base == "USD" and ccy == "AUD": return px * fx
        return px
    vals = {h.ticker: h.qty * price_base(h.ticker) for h in hrows}; prices = {h.ticker: price_base(h.ticker) for h in hrows}
    nav = sum(vals.values()); 
    if req.cash: nav += float(req.cash)
    T = {t.ticker.upper(): float(t.target_weight) for t in req.targets}; tsum = sum(T.values())
    if tsum > 0 and abs(tsum-1.0) > 1e-6: T = {k: v/tsum for k,v in T.items()}
    desired = {tk: nav * T.get(tk, 0.0) for tk in set(list(vals.keys()) + list(T.keys()))}
    lot = int(req.lot_size or 1); mov = float(req.min_order_value or 0)
    out = []
    for tk in sorted(desired.keys()):
        cur = vals.get(tk, 0.0); dval = desired[tk] - cur; px = prices.get(tk) or price_base(tk)
        qty = (dval/px) if px else 0.0; side = "BUY" if dval>0 else ("SELL" if dval<0 else "HOLD")
        qty_adj, not_adj = _apply_constraints(side, qty, dval, lot, mov)
        out.append({"ticker": tk, "side": side, "qty_delta": round(qty_adj, 4), "notional_delta": round(not_adj, 2), "price": round(px, 4)})
    out.sort(key=lambda x: abs(x["notional_delta"]), reverse=True)
    return {"base_currency": CFG.base_currency, "nav_with_cash": nav, "suggestions": out}

# Rebalance by bucket (sector/region) with seeding + constraints
class BucketTargetIn(BaseModel): bucket: str; target_weight: float
class BucketRebalanceReq(BaseModel):
    mode: str; targets: List[BucketTargetIn]; cash: float | None = None; min_order_value: float | None = None; lot_size: int | None = 1; seed_source: str | None = "watchlist"

@app.post("/api/rebalance_by_bucket")
def rebalance_by_bucket(req: BucketRebalanceReq, db: Session = Depends(get_db)):
    mode = (req.mode or "sector").lower(); assert mode in ("sector","region"), "mode must be 'sector' or 'region'"
    def price_base(tk: str) -> float:
        df = SC.yf.history(tk, period="1mo")
        if df is None or df.empty or "close" not in df: return 0.0
        px = float(df["close"].iloc[-1]); ccy = "AUD" if tk.endswith(".AX") else "USD"; fx = SC.yf.fx("AUDUSD=X") or 0.65; base = CFG.base_currency
        if base == ccy: return px
        if base == "AUD" and ccy == "USD": return px / fx
        if base == "USD" and ccy == "AUD": return px * fx
        return px
    hrows = db.query(HoldingModel).all(); rows = []
    for h in hrows:
        info = SC.yf.info(h.ticker); sector = info.get('sector') or 'Unknown'; country = info.get('country') or ('Australia' if h.ticker.endswith('.AX') else 'United States')
        region = 'Australia' if h.ticker.endswith('.AX') else (country or 'United States'); px = price_base(h.ticker); val = h.qty * px
        rows.append({"ticker": h.ticker, "sector": sector, "region": region, "value": float(val), "px": px})
    nav = sum(r["value"] for r in rows); 
    if req.cash: nav += float(req.cash)
    T = {t.bucket: float(t.target_weight) for t in req.targets}; tsum = sum(T.values())
    if tsum > 0 and abs(tsum-1.0) > 1e-6: T = {k: v/tsum for k,v in T.items()}
    from collections import defaultdict
    cur_bucket = defaultdict(float); bucket_members = defaultdict(list)
    for r in rows:
        b = r[mode]; cur_bucket[b] += r["value"]; bucket_members[b].append(r)
    desired_bucket = {b: nav * T.get(b, 0.0) for b in set(list(cur_bucket.keys()) + list(T.keys()))}
    desired_ticker_values = {}
    seed_src = (req.seed_source or 'watchlist').lower()
    scan_results = []
    if seed_src in ('signals','watchlist') and CFG.watchlist:
        try: scan_results = SC.screen([t.upper() for t in (CFG.watchlist or [])])
        except Exception: scan_results = []
    score_map = {r['ticker']: r for r in scan_results}
    for b, dval in desired_bucket.items():
        members = bucket_members.get(b, [])
        if not members:
            if seed_src == 'none': continue
            cands = []
            for tk in (CFG.watchlist or []):
                tkU = tk.upper(); info = SC.yf.info(tkU); sec = info.get('sector') or 'Unknown'
                country = info.get('country') or ('Australia' if tkU.endswith('.AX') else 'United States'); reg = 'Australia' if tkU.endswith('.AX') else (country or 'United States')
                if (mode=='sector' and sec==b) or (mode=='region' and reg==b):
                    if seed_src=='signals':
                        sr = score_map.get(tkU)
                        if sr and sr.get('side')=='BUY': cands.append((tkU, sr.get('score',0)))
                    else: cands.append((tkU, 0))
            cands.sort(key=lambda x: x[1], reverse=True)
            if cands:
                per = dval / len(cands)
                for tkU,_ in cands: desired_ticker_values[tkU] = per
            continue
        cur_total = sum(m["value"] for m in members)
        if cur_total <= 0:
            per = dval / len(members)
            for m in members: desired_ticker_values[m["ticker"]] = per
        else:
            scale = dval / cur_total
            for m in members: desired_ticker_values[m["ticker"]] = m["value"] * scale
    suggestions = []; lot = int(req.lot_size or 1); mov = float(req.min_order_value or 0)
    for r in rows:
        cur = r["value"]; dval = desired_ticker_values.get(r["ticker"], 0.0) - cur; px = r["px"] or price_base(r["ticker"])
        qty = (dval/px) if px else 0.0; side = "BUY" if dval>0 else ("SELL" if dval<0 else "HOLD")
        qty_adj, not_adj = _apply_constraints(side, qty, dval, lot, mov)
        suggestions.append({"ticker": r["ticker"], "bucket": r[mode], "side": side, "qty_delta": round(qty_adj, 4), "notional_delta": round(not_adj, 2), "price": round(px, 4)})
    suggestions.sort(key=lambda x: abs(x["notional_delta"]), reverse=True)
    return {"base_currency": CFG.base_currency, "mode": mode, "nav_with_cash": nav, "suggestions": suggestions}

# --- Scheduler (market hours) ---
sched = BackgroundScheduler(timezone=str(os.getenv("TZ_AU", "Australia/Brisbane")))
def scan_job():
    try:
        sigs = run_scan()
        from fastapi.encoders import jsonable_encoder
        notify_signals(jsonable_encoder(sigs))
        with next(get_db()) as db:
            hrows = db.query(HoldingModel).all(); snap = PA.snapshot([(h.ticker, h.qty, h.avg_price) for h in hrows])
            notify_riskflags(snap.get('risk_flags', [])); db.add(Metric(nav=snap['nav'], cash=snap['cash'], exposures=snap['exposures'])); db.commit()
    except Exception as e:
        print("scan error", e)
sched.add_job(scan_job, CronTrigger(hour='10-16', minute=f"*/{int(os.getenv('SCAN_EVERY_MINS','10'))}", day_of_week='mon-fri', timezone=CFG.markets['au'].timezone))
sched.add_job(scan_job, CronTrigger(hour='9-16', minute=f"*/{int(os.getenv('SCAN_EVERY_MINS','10'))}", day_of_week='mon-fri', timezone=CFG.markets['us'].timezone))
sched.start()


@app.get("/api/news")
def news(ticker: str, days: int = 7, limit: int = 20):
    # Prefer paid provider if configured; fall back to RSS
    try:
        if hasattr(SC.news, 'recent'):
            items = SC.news.recent(ticker.upper(), lookback_days=days, limit=limit)
        else:
            items = []
    except Exception:
        items = []
    return {"ticker": ticker.upper(), "items": items}
