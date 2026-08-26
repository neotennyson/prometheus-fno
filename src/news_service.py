"""24/7 news & event monitor service (the 'second port').

Runs independently of the trading loop, polls RSS feeds around the clock (not just
market hours), classifies headline severity, accepts manual webhooks, and exposes
/status that the orchestrator consults before allowing entries. Everything it sees
is journaled to logs/news.db for post-trade forensics.

Severity model:
  level 3 = force-flat material (RBI policy shock, war, budget surprise, halt)
  level 2 = pause new entries (regulatory action, tariffs, rating cuts)
  level 1 = noteworthy only
Run:  .venv/bin/python -m src.news_service          (http://127.0.0.1:8791)
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time as _time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

import yaml

try:
    import feedparser
except ImportError:
    feedparser = None

try:
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel
except ImportError as e:
    raise SystemExit("news_service needs fastapi+pydantic: pip install fastapi uvicorn pydantic") from e

REPO = Path(__file__).resolve().parents[1]
DB_PATH = REPO / "logs" / "news.db"

SEVERITY = {
    3: ["repo rate", "rbi policy", "monetary policy", "union budget", "election result",
        "exit poll", "declares war", "air strike", "missile", "military action",
        "emergency imposed", "circuit breaker", "trading halt", "sanctions on india"],
    2: ["sebi bans", "imposes tariff", "credit rating cut", "downgrade", "default on",
        "rupee crashes", "crude supply"],
}
_FLAT_PATTS = {lvl: [re.compile(rf"\b{re.escape(k)}\b") for k in kws]
               for lvl, kws in SEVERITY.items()}

DEFAULT_FEEDS = [
    # -- India market desks -------------------------------------------------
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://www.moneycontrol.com/rss/latestnews.xml",
    "https://www.moneycontrol.com/rss/economy.xml",
    "https://www.business-standard.com/rss/markets-106.rss",
    "https://www.livemint.com/rss/markets",
    # -- global wires & central banks ---------------------------------------
    "http://feeds.bbci.co.uk/news/business/rss.xml",
    "https://www.federalreserve.gov/feeds/press_all.xml",
    # -- targeted query feeds (Google News mirrors thousands of outlets,
    #    catches Reuters/Bloomberg syndicated headlines without their APIs) --
    "https://news.google.com/rss/search?q=RBI+repo+rate+monetary+policy&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=SEBI+regulation+bans&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=Nifty+Sensex+crash+OR+surge+OR+circuit&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=India+election+result+OR+union+budget&hl=en-IN&gl=IN&ceid=IN:en",
    # -- trader chatter (free, twitter-like) ---------------------------------
    "https://www.reddit.com/r/IndianStockMarket/new/.rss",
    "https://www.reddit.com/r/IndiaInvestments/new/.rss",
]

# Social streams polled as JSON (StockTwits = twitter for tickers, free tier)
SOCIAL_SYMBOLS = ["NIFTY", "BANKNIFTY", "SENSEX"]

# Sources that are informational only -- never arm block/flat decisions.
IGNORE_ALERT_SOURCES = ("news.google.com", "reddit.com", "stocktwits")


def _parse_stocktwits_payload(payload: dict) -> list[dict]:
    out = []
    for msg in payload.get("messages", [])[:12]:
        body = (msg.get("body") or "").strip()
        user = (msg.get("user") or {}).get("username", "?")
        if body:
            out.append({"title": f"${user}: {body}", "summary": "",
                        "ts": str(msg.get("created_at") or "")})
    return out


def fetch_stocktwits(symbol: str) -> list[dict]:
    import httpx
    url = f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
    try:
        r = httpx.get(url, timeout=6.0, headers={"User-Agent": "prometheus-research/1.0"})
        if r.status_code != 200:
            return []
        return _parse_stocktwits_payload(r.json())
    except Exception:
        return []

# ---- Global Pulse: overnight macro parameters scored 0-100 ------------------
MACRO_SYMBOLS = {
    "spx": "^GSPC", "nasdaq": "^IXIC", "usdinr": "INR=X",
    "crude": "CL=F", "us10y": "^TNX", "indiavix": "^INDIAVIX",
}


def pulse_score(comps: dict) -> tuple[int, str]:
    """components: {name: last_session_return_fraction}. Returns (0-100, label)."""
    s = 0
    if comps.get("spx", 0) <= -0.008:
        s += 30
    if comps.get("nasdaq", 0) <= -0.012:
        s += 20
    if comps.get("crude", 0) >= 0.025:
        s += 15
    if comps.get("usdinr", 0) >= 0.004:
        s += 15
    if comps.get("indiavix", 0) >= 0.10:
        s += 20
    label = "calm" if s < 25 else ("elevated" if s < 50 else "risk_off")
    return min(s, 100), label


class MacroPoller(threading.Thread):
    """Refreshes overnight macro parameters every ~15 minutes, all day."""

    def __init__(self, store, poll_secs: int = 900):
        super().__init__(daemon=True)
        self.store = store
        self.poll_secs = poll_secs
        self.components: dict[str, float] = {}
        self.updated: str | None = None
        self._stop = threading.Event()

    def _fetch(self):
        try:
            import numpy as _np
            import pandas as _pd
            import yfinance as yf
            for name, sym in MACRO_SYMBOLS.items():
                df = yf.download(sym, period="5d", interval="1d",
                                 progress=False, auto_adjust=True)
                if df is None or len(df) < 2:
                    continue
                if isinstance(df.columns, _pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                closes = _pd.to_numeric(df["Close"], errors="coerce").dropna()
                if len(closes) < 2 or closes.iloc[-2] == 0:
                    continue
                self.components[name] = float(closes.iloc[-1] / closes.iloc[-2] - 1.0)
            self.updated = datetime.now().astimezone().isoformat()
        except Exception as exc:
            logging.warning("macro fetch failed, keeping last values: %s", exc)

    def run(self):
        while not self._stop.is_set():
            self._fetch()
            self._stop.wait(self.poll_secs)


def classify(title: str, summary: str = "") -> tuple[int, str | None]:
    text = f"{title} {summary}".lower()
    for lvl in (3, 2):
        for kw, pat in zip(SEVERITY[lvl], _FLAT_PATTS[lvl]):
            if pat.search(text):
                return lvl, kw
    return 0, None


class NewsStore:
    def __init__(self, db_path: Path = DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()   # poller + API threads share this conn
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS events(
               ts TEXT, source TEXT, title TEXT, impact INTEGER, keyword TEXT,
               seen_at TEXT, PRIMARY KEY(ts, title))""")
        self.conn.commit()
        self.recent: deque = deque(maxlen=400)

    def add(self, ts: str, source: str, title: str, summary: str = "") -> dict:
        lvl, kw = classify(title, summary)
        rec = {"ts": ts, "source": source, "title": title[:300],
               "impact": lvl, "keyword": kw, "seen_at": datetime.now().isoformat()}
        try:
            with self._lock:
                self.conn.execute("INSERT OR REPLACE INTO events VALUES(?,?,?,?,?,?)",
                                  (rec["ts"], rec["source"], rec["title"], lvl, kw, rec["seen_at"]))
                self.conn.commit()
        except Exception:
            pass
        self.recent.appendleft(rec)
        return rec

    def alerts_since(self, minutes: int) -> list[dict]:
        cutoff = datetime.now() - timedelta(minutes=minutes)
        # mirrors/chatter inform the journal but must not arm the block --
        # every query-mirror title contains trigger words by construction.
        return [r for r in self.recent
                if r["impact"] >= 2
                and not any(s in r["source"].lower() for s in IGNORE_ALERT_SOURCES)
                and datetime.fromisoformat(r["seen_at"]) >= cutoff]


class Poller(threading.Thread):
    def __init__(self, store: NewsStore, feeds: list[str], poll_secs: int,
                 social_symbols: list[str] | None = None):
        super().__init__(daemon=True)
        self.store, self.feeds, self.poll_secs = store, feeds, poll_secs
        self.social = list(social_symbols or [])
        self._stop = threading.Event()

    def run(self):
        ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        while not self._stop.is_set():
            if feedparser is not None:
                for url in self.feeds:
                    try:
                        parsed = feedparser.parse(url, agent=ua)
                        src = url.split("/")[2] if url.count("/") >= 2 else url
                        for e in parsed.entries[:12]:
                            title = getattr(e, "title", "")
                            if title:
                                ts = getattr(e, "published", "") or datetime.now().isoformat()
                                self.store.add(str(ts), src, title,
                                               getattr(e, "summary", "") or "")
                    except Exception:
                        continue
            for sym in self.social:
                for item in fetch_stocktwits(sym):
                    self.store.add(item["ts"] or datetime.now().isoformat(),
                                   f"stocktwits:{sym}", item["title"], item["summary"])
            self._stop.wait(self.poll_secs)


def load_calendar_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text()) or {}
        return data.get("events", [])
    except Exception:
        return []


class Alert(BaseModel):
    title: str
    summary: str = ""
    source: str = "webhook"


_state: dict = {}


def create_app(cfg: dict | None = None) -> FastAPI:
    cfg = cfg or {}
    cal_path = REPO / cfg.get("calendar_file", "config/events.yaml")
    store = NewsStore()
    poller = Poller(store, cfg.get("feeds", DEFAULT_FEEDS), int(cfg.get("poll_secs", 120)),
                    social_symbols=cfg.get("social_symbols", SOCIAL_SYMBOLS))
    macro = MacroPoller(store, int(cfg.get("macro_poll_secs", 900)))
    calendar = load_calendar_events(cal_path)
    app = FastAPI(title="LYNX monitor", docs_url=None, redoc_url=None)

    @app.on_event("startup")
    async def _startup():
        poller.start()
        macro.start()

    @app.post("/webhook")
    def webhook(a: Alert, request: Request):
        secret = str(cfg.get("webhook_secret", "") or "")
        if secret and request.headers.get("x-argus-secret", "") != secret:
            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
        rec = store.add(datetime.now().astimezone().isoformat(), a.source, a.title, a.summary)
        return {"ok": True, "impact": rec["impact"], "keyword": rec["keyword"]}

    @app.get("/pulse")
    def pulse():
        score, label = pulse_score(macro.components)
        return {"score": score, "level": label,
                "components": {k: round(v * 100, 2) for k, v in macro.components.items()},
                "updated": macro.updated}

    @app.get("/status")
    def status(minutes: int = 45):
        now = datetime.now().astimezone()
        alerts = store.alerts_since(minutes)
        top = max((a["impact"] for a in alerts), default=0)
        today = now.date().isoformat()
        cal_today = [e for e in calendar if str(e.get("date")) == today or
                     str(e.get("every_year", "")).endswith(today[5:])]
        cal_action = ""
        for e in cal_today:
            act = str(e.get("action", ""))
            if act == "flat":
                cal_action = "flat"
                break
            if act == "no_entry":
                cal_action = "no_entry"
        force_flat = top >= 3 or cal_action == "flat"
        block_entries = top >= 2 or bool(cal_action)
        return {"as_of": now.isoformat(), "impact_level": int(top),
                "block_entries": bool(block_entries), "force_flat": bool(force_flat),
                "reason": (f"headline:{alerts[0]['keyword']}" if alerts and top >= 2
                           else f"calendar:{cal_action}" if cal_action else None),
                "recent_alerts": alerts[:10], "feeds_monitored": len(poller.feeds),
                "headlines_seen": len(store.recent)}

    @app.get("/events")
    def events(hours: int = 24, min_impact: int = 0):
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        rows = [r for r in reversed(store.recent)
                if r["impact"] >= min_impact and r["seen_at"] >= cutoff]
        return {"count": len(rows), "events": rows[:200]}

    return app


def main():
    import uvicorn
    cfg_path = REPO / "config" / "settings.yaml"
    ev_cfg = {}
    if cfg_path.exists():
        all_cfg = yaml.safe_load(cfg_path.read_text()) or {}
        ev_cfg = all_cfg.get("events", {})
    app = create_app(ev_cfg)
    host = ev_cfg.get("service_host", "127.0.0.1")
    port = int(ev_cfg.get("service_port", 8791))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
