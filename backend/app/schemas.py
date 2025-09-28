from pydantic import BaseModel
from datetime import datetime

class HoldingIn(BaseModel): ticker: str; qty: float; avg_price: float
class HoldingOut(HoldingIn): id: int
class SignalOut(BaseModel):
    ticker: str; side: str; reasons: list[str]; score: float; px: float; asof: datetime; extras: dict
class PortfolioSnapshot(BaseModel):
    asof: datetime; nav: float; cash: float; pnl_total: float; pnl_pct: float; exposures: dict; top_positions: list[dict]; risk_flags: list[str]
