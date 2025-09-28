from __future__ import annotations
import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from datetime import datetime, timedelta
from urllib.parse import quote_plus
from ..utils import cache

an = SentimentIntensityAnalyzer()

class NewsRSS:
    def _query_urls(self, ticker: str) -> list[str]:
        queries = [f"{ticker} stock"]
        if ticker.endswith(".AX"):
            code = ticker.replace(".AX","")
            queries += [f"{code} ASX announcement", f"site:asx.com.au {code} announcement"]
        return [f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-US&gl=US&ceid=US:en" for q in queries]

    def average_sentiment(self, ticker: str, lookback_days: int = 7) -> float | None:
        key = f"news_rss:{ticker}:{lookback_days}"; hit = cache.get(key)
        if hit is not None: return hit
        cutoff = datetime.utcnow() - timedelta(days=lookback_days)
        scores = []
        for url in self._query_urls(ticker):
            feed = feedparser.parse(url)
            if not feed or not getattr(feed, "entries", None): continue
            for e in feed.entries:
                title = e.get('title',''); descr = e.get('summary','')
                txt = f"{title}. {descr}".strip()
                if not txt: continue
                s = an.polarity_scores(txt)['compound']
                scores.append(s)
        val = (sum(scores)/len(scores)) if scores else None
        cache.set(key, val); return val


def recent(self, ticker: str, lookback_days: int = 7, limit: int = 20) -> list[dict]:
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)
    items = []
    for url in self._query_urls(ticker):
        feed = feedparser.parse(url)
        if not feed or not getattr(feed, "entries", None): continue
        for e in feed.entries:
            title = e.get('title',''); link = e.get('link','')
            descr = e.get('summary','') or ''
            published = e.get('published_parsed') or e.get('updated_parsed')
            try: dt = datetime(*published[:6]) if published else None
            except Exception: dt = None
            if dt and dt < cutoff: continue
            txt = (title + '. ' + descr).strip()
            sent = an.polarity_scores(txt)['compound'] if txt else 0.0
            items.append({'title': title, 'link': link, 'published': dt.isoformat() if dt else None, 'sentiment': sent})
    # de-dup by title, keep most recent
    seen = {}
    for it in items:
        k = it['title']
        if k not in seen or (it.get('published') or '') > (seen[k].get('published') or ''):
            seen[k] = it
    out = list(seen.values())
    out.sort(key=lambda x: x.get('published') or '', reverse=True)
    return out[:limit]
