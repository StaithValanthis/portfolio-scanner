from __future__ import annotations
import feedparser
from datetime import datetime, timedelta
from urllib.parse import quote_plus
from ..utils import cache

class ASXAnnouncements:
    def _feeds(self, ticker: str) -> list[str]:
        code = ticker.replace(".AX","")
        queries = [f"site:asx.com.au {code} announcement", f"{code} ASX announcement"]
        return [f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-AU&gl=AU&ceid=AU:en" for q in queries]
    def recent(self, ticker: str, lookback_days: int = 14, limit: int = 12) -> list[dict]:
        key = f"asx_ann:{ticker}:{lookback_days}:{limit}"; hit = cache.get(key)
        if hit is not None: return hit
        out = []; cutoff = datetime.utcnow() - timedelta(days=lookback_days)
        for url in self._feeds(ticker):
            feed = feedparser.parse(url)
            if not feed or not getattr(feed, "entries", None): continue
            for e in feed.entries:
                title = e.get("title",""); link = e.get("link",""); published = e.get("published_parsed") or e.get("updated_parsed")
                try: dt = datetime(*published[:6]) if published else None
                except Exception: dt = None
                if dt and dt < cutoff: continue
                out.append({"title": title, "link": link, "published": dt.isoformat() if dt else None})
        seen, uniq = set(), []
        for item in out:
            if item["title"] in seen: continue
            seen.add(item["title"]); uniq.append(item)
        uniq.sort(key=lambda x: x.get("published") or "", reverse=True)
        res = uniq[:limit]; cache.set(key, res); return res
