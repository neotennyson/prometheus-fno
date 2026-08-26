from __future__ import annotations

import re
import threading
from datetime import date, datetime, time as dtime
from pathlib import Path

import yaml

try:
    import feedparser
except ImportError:
    feedparser = None

DEFAULT_FEEDS = [
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://www.moneycontrol.com/rss/latestnews.xml",
    "https://www.business-standard.com/rss/markets-106.rss",
    "http://feeds.bbci.co.uk/news/business/rss.xml",
]


def _parse_hhmm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


class EventRisk:
    def __init__(self, calendar_path: Path | str, cfg: dict, fetcher=None):
        self.cfg = cfg or {}
        self.calendar_path = Path(calendar_path)
        self.keywords = [k.lower() for k in self.cfg.get("keywords", [])]
        self._patterns = [re.compile(rf"\b{re.escape(k)}\b") for k in self.keywords]
        self.feeds = self.cfg.get("feeds", DEFAULT_FEEDS)
        self.poll_secs = int(self.cfg.get("poll_secs", 180))
        self.lookback_min = int(self.cfg.get("alert_lookback_min", 45))
        self.headline_action = str(self.cfg.get("headline_action", "pause")).lower()
        self.market_open = _parse_hhmm(self.cfg.get("poll_window_start", "09:00"))
        self.market_close = _parse_hhmm(self.cfg.get("poll_window_end", "15:35"))
        self._fetcher = fetcher or self._fetch_feed
        self._headlines: list[dict] = []
        self._last_poll: datetime | None = None
        self._calendar: list[dict] = []
        self._calendar_loaded_on: date | None = None
        self._lock = threading.Lock()

    def _load_calendar(self, today: date) -> list[dict]:
        if self._calendar_loaded_on == today or not self.calendar_path.exists():
            return self._calendar
        try:
            data = yaml.safe_load(self.calendar_path.read_text()) or {}
        except Exception:
            data = {}
        cal = []
        for ev in data.get("events", []):
            try:
                if ev.get("date"):
                    d = date.fromisoformat(str(ev["date"]))
                elif ev.get("every_year"):
                    mm, dd = str(ev["every_year"]).split("-")
                    d = date(today.year, int(mm), int(dd))
                else:
                    continue
            except (ValueError, KeyError):
                continue
            cal.append({"date": d, "name": str(ev.get("name", "event")),
                        "action": str(ev.get("action", "no_entry")).lower()})
        self._calendar = cal
        self._calendar_loaded_on = today
        return cal

    def event_for(self, d: date) -> dict | None:
        for ev in self._load_calendar(d):
            if ev["date"] == d:
                return ev
        return None

    @staticmethod
    def _fetch_feed(url: str) -> list[dict]:
        if feedparser is None:
            return []
        try:
            parsed = feedparser.parse(url, etag=None, modified=None)
            out = []
            for e in parsed.entries[:15]:
                title = getattr(e, "title", "") or ""
                summary = getattr(e, "summary", "") or ""
                if title:
                    out.append({"title": title, "summary": summary})
            return out
        except Exception:
            return []

    def refresh(self, now: datetime) -> None:
        if not self.keywords or feedparser is None:
            return
        t = now.time()
        if not (self.market_open <= t <= self.market_close):
            return
        with self._lock:
            if self._last_poll is not None and (now - self._last_poll).total_seconds() < self.poll_secs:
                return
            self._last_poll = now
            for url in self.feeds:
                for item in self._fetcher(url):
                    self._headlines.append({"ts": now,
                                            "source": url.split("/")[2] if url.count("/") >= 2 else url,
                                            "title": item["title"], "summary": item["summary"]})
            cutoff = now.timestamp() - max(self.lookback_min * 3, 240) * 60
            self._headlines = [h for h in self._headlines if h["ts"].timestamp() >= cutoff]

    def headline_alert(self, now: datetime) -> dict | None:
        if not self.keywords:
            return None
        cutoff = now.timestamp() - self.lookback_min * 60
        for h in reversed(self._headlines):
            if h["ts"].timestamp() < cutoff:
                return None
            text = f"{h['title']} {h['summary']}".lower()
            hit = next((self.keywords[i] for i, p in enumerate(self._patterns) if p.search(text)), None)
            if hit:
                return {"source": h["source"], "keyword": hit, "title": h["title"][:120]}
        return None

    def assess(self, now: datetime, refresh: bool = True) -> dict:
        today = now.date()
        ev = self.event_for(today)
        if ev and ev.get("action") == "flat":
            return {"block_entries": True, "force_flat": True, "reason": f"calendar:{ev['name']}"}
        if ev and ev.get("action") == "no_entry":
            return {"block_entries": True, "force_flat": False, "reason": f"calendar:{ev['name']}"}
        if self.headline_action == "ignore":
            return {"block_entries": False, "force_flat": False, "reason": None}
        if refresh:
            self.refresh(now)
        alert = self.headline_alert(now)
        if alert and self.headline_action == "flat":
            return {"block_entries": True, "force_flat": True,
                    "reason": f"headline:{alert['keyword']}:{alert['source']}"}
        if alert:
            return {"block_entries": True, "force_flat": False,
                    "reason": f"headline:{alert['keyword']}:{alert['source']}"}
        return {"block_entries": False, "force_flat": False, "reason": None}
