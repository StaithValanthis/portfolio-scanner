from __future__ import annotations
import yfinance as yf, pandas as pd
from ..utils import cache

class FundamentalsYF:
    def _safe(self, s, i):
        try: return float(s.get(i)) if s is not None and i in s else None
        except Exception: return None

    def facts(self, ticker: str) -> dict:
        hit = cache.get(f"facts_yf:{ticker}")
        if hit is not None: return hit
        t = yf.Ticker(ticker)
        info = t.info or {}
        fin = t.financials or pd.DataFrame()
        bs  = t.balance_sheet or pd.DataFrame()
        cf  = t.cashflow or pd.DataFrame()
        out = {'provider':'yf'}
        out['pe_ttm'] = info.get('trailingPE'); out['pe_fwd'] = info.get('forwardPE'); out['peg'] = info.get('pegRatio')
        out['pb'] = info.get('priceToBook'); out['ev_ebitda'] = info.get('enterpriseToEbitda')
        out['div_yield_ttm'] = info.get('dividendYield'); out['payout_ratio'] = info.get('payoutRatio')
        try:
            fy = fin.columns[0] if len(fin.columns) else None
            bscol = bs.columns[0] if len(bs.columns) else None
            cfcol = cf.columns[0] if len(cf.columns) else None
            def getrow(df, *names):
                for n in names:
                    if n in df.index: return df.loc[n]
                return None
            revenue = self._safe(getrow(fin,'Total Revenue','TotalRevenue'), fy)
            gross_profit = self._safe(getrow(fin,'Gross Profit','GrossProfit'), fy)
            net_income = self._safe(getrow(fin,'Net Income','NetIncome'), fy)
            ebitda = self._safe(getrow(fin,'Ebitda','EBITDA'), fy)
            total_equity = self._safe(getrow(bs,'Total Stockholder Equity','TotalStockholderEquity'), bscol)
            total_debt = self._safe(getrow(bs,'Total Debt','TotalDebt'), bscol)
            cash = self._safe(getrow(bs,'Cash And Cash Equivalents','CashAndCashEquivalents'), bscol)
            fcf = None
            try:
                cfo = self._safe(getrow(cf,'Total Cash From Operating Activities','TotalCashFromOperatingActivities'), cfcol)
                capex = self._safe(getrow(cf,'Capital Expenditures','CapitalExpenditures'), cfcol)
                if cfo is not None and capex is not None: fcf = cfo - capex
            except Exception: pass
            if revenue and gross_profit: out['gross_margin'] = gross_profit / revenue
            if total_equity and net_income: out['roe'] = net_income / total_equity
            if revenue and fcf is not None: out['fcf_margin'] = fcf / revenue
            if ebitda and total_debt is not None and cash is not None and ebitda != 0:
                out['net_debt_ebitda'] = (total_debt - cash) / ebitda
        except Exception: pass
        out = {k:v for k,v in out.items() if v is not None}
        cache.set(f"facts_yf:{ticker}", out)
        return out
