from sqlalchemy import Integer, String, Float, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base
from datetime import datetime

class Holding(Base):
    __tablename__ = "holdings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    qty: Mapped[float] = mapped_column(Float)
    avg_price: Mapped[float] = mapped_column(Float)

class Signal(Base):
    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    side: Mapped[str] = mapped_column(String)
    reasons: Mapped[str] = mapped_column(String)
    score: Mapped[float] = mapped_column(Float)
    px: Mapped[float] = mapped_column(Float)
    asof: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    extras: Mapped[dict] = mapped_column(JSON, default={})

class Metric(Base):
    __tablename__ = "metrics"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asof: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    nav: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float, default=0.0)
    exposures: Mapped[dict] = mapped_column(JSON, default={})
