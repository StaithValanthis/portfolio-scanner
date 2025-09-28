from __future__ import annotations
import os, httpx
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

an = SentimentIntensityAnalyzer()

class NewsProvider:
    def __init__(self): self.key = os.getenv("NEWSAPI_KEY","")
    def average_sentiment(self, ticker: str, lookback_days: int = 7) -> float | None:
        if not self.key: return None
        url = "https://newsapi.org/v2/everything"
        params = {"q": f"{ticker} stock", "language":"en", "pageSize":50, "sortBy":"publishedAt", "apiKey": self.key}
        try:
            r = httpx.get(url, params=params, timeout=15); r.raise_for_status(); data = r.json()
            arts = data.get("articles", []); 
            if not arts: return None
            scores = [an.polarity_scores((a.get('title','') + '. ' + a.get('description','')).strip())['compound'] for a in arts]
            return sum(scores)/len(scores) if scores else None
        except Exception: return None


def recent(self, ticker: str, lookback_days: int = 7, limit: int = 20) -> list[dict]:
    if not self.key: return []
    url = "https://newsapi.org/v2/everything"
    params = {"q": f"{ticker} stock", "language":"en", "pageSize":limit, "sortBy":"publishedAt", "apiKey": self.key}
    try:
        r = httpx.get(url, params=params, timeout=15); r.raise_for_status(); data = r.json()
        arts = data.get("articles", [])
        out = []
        for a in arts:
            title = a.get('title',''); descr = a.get('description','') or ''
            link = a.get('url',''); txt = (title + '. ' + descr).strip()
            sent = an.polarity_scores(txt)['compound'] if txt else 0.0
            out.append({'title': title, 'link': link, 'published': a.get('publishedAt'), 'sentiment': sent})
        out.sort(key=lambda x: x.get('published') or '', reverse=True)
        return out[:limit]
    except Exception:
        return []
