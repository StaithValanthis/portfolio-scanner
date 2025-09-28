from pydantic import BaseModel
from pathlib import Path
import yaml

class MarketCfg(BaseModel):
    suffix: str; timezone: str; trading_days: list[str]; open: str; close: str

class MomentumCfg(BaseModel): enabled: bool=True; min_12m_mom: float=0.08; skip_last_month: bool=True
class MeanRevCfg(BaseModel): enabled: bool=True; rsi_period: int=14; rsi_buy_below: int=30; rsi_sell_above: int=70
class ValueCfg(BaseModel): enabled: bool=True; max_pe: float|None=28; max_pb: float|None=5; peg_max: float|None=2.0; ev_ebitda_max: float|None=18
class DividendCfg(BaseModel): enabled: bool=True; min_yield: float=0.03; max_payout: float|None=0.85
class QualityCfg(BaseModel): enabled: bool=True; min_roe: float=0.12; min_gross_margin: float=0.25; min_fcf_margin: float=0.05; min_rev_cagr_3y: float=0.05; max_net_debt_ebitda: float=3.0
class TrendCfg(BaseModel): enabled: bool=True; require_above_sma200: bool=True; sma_fast: int=50; sma_slow: int=200
class NewsCfg(BaseModel): enabled: bool=True; lookback_days: int=7; min_avg_sentiment: float=0.1
class BreakoutCfg(BaseModel): enabled: bool=True; near_high_pct: float=0.05
class RiskCfg(BaseModel): position_cap_pct_nav: float=0.10; sector_cap_pct_nav: float=0.25; daily_new_buy_limit: int=5; stop_loss_pct: float=0.12; rebalance_band_pct: float=0.25
class SellRules(BaseModel): below_sma200_pct: float=0.03; valuation_stretch_pctile: int=90; fundamental_deterioration: dict={"rev_growth_drop_pp":5,"margin_drop_pp":5}; news_shock_sentiment: float=-0.4
class SignalsCfg(BaseModel):
    momentum: MomentumCfg=MomentumCfg(); mean_reversion: MeanRevCfg=MeanRevCfg(); value: ValueCfg=ValueCfg()
    dividend: DividendCfg=DividendCfg(); quality: QualityCfg=QualityCfg(); technical_trend: TrendCfg=TrendCfg()
    news_sentiment: NewsCfg=NewsCfg(); breakout52w: BreakoutCfg=BreakoutCfg()

class Holding(BaseModel): ticker: str; qty: float; avg_price: float
class Cfg(BaseModel):
    base_currency: str="AUD"; markets: dict[str, MarketCfg]; holdings: list[Holding]=[]; watchlist: list[str]=[]
    signals: SignalsCfg=SignalsCfg(); risk: RiskCfg=RiskCfg(); sell_rules: SellRules=SellRules(); ui: dict={}

CFG_PATH = Path("/app/config.yaml")
def load_config() -> "Cfg":
    import yaml
    data = yaml.safe_load(CFG_PATH.read_text())
    return Cfg(**data)
