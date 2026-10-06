"""PROMETHEUS Command Center — one-screen trading terminal.

Everything renders as a single custom HTML grid (dense tiles, terminal feed,
SVG charts) — escaping Streamlit's template look entirely. Data from repo
artifacts + ARGUS on localhost:8791.
"""
from __future__ import annotations

import html as _html
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parents[1]
IST = timezone(timedelta(hours=5, minutes=30))

st.set_page_config(page_title="PROMETHEVS", page_icon="🔥", layout="wide")

TORCH_SVG = ('<svg class="torch" viewBox="0 0 24 24">'
             '<path d="M12 2.2c1.9 2.1 3.4 3.9 3.4 6.1 0 1.5-.8 2.6-1.9 3.2.5-1.2.4-2.6-.7-4-.5 2.2-2.3 2.6-2.3 4.6 0 .8.3 1.4.8 1.9-1.7-.3-3-1.8-3-3.9 0-3 2.6-5 3.7-7.9z" fill="#E8833A"/>'
             '<path d="M12 16.6c1.7 0 3 1.3 3 2.9 0 1.4-1 2.3-1.6 2.3h-2.8c-.6 0-1.6-.9-1.6-2.3 0-1.6 1.3-2.9 3-2.9z" fill="#C9A227"/></svg>')


def esc(x) -> str:
    return _html.escape(str(x), quote=True)


def money(x) -> str:
    try:
        return f"₹{float(x):,.0f}"
    except (TypeError, ValueError):
        return "—"


def signed_pct(v) -> str:
    try:
        return f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return "—"


# ------------------------------------------------------------------ loaders
@st.cache_data(ttl=600)
def load_json(rel: str):
    p = ROOT / rel
    return json.loads(p.read_text()) if p.exists() else {}


@st.cache_data(ttl=300)
def load_csv(rel: str) -> pd.DataFrame:
    p = ROOT / rel
    return pd.read_csv(p) if p.exists() else pd.DataFrame()


@st.cache_data(ttl=120)
def load_pnl() -> pd.DataFrame:
    p = ROOT / "results" / "daily_pnl.csv"
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p, parse_dates=["date"]).set_index("date").sort_index()


@st.cache_data(ttl=30)
def load_fills() -> pd.DataFrame:
    db = ROOT / "logs" / "trinity_trades.db"
    if not db.exists():
        return pd.DataFrame()
    try:
        con = sqlite3.connect(db)
        df = pd.read_sql("SELECT * FROM fills ORDER BY ts DESC LIMIT 500", con)
        con.close()
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=30)
def load_session_state() -> dict:
    p = ROOT / "logs" / "trinity_session_state.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


@st.cache_data(ttl=60)
def load_news_events() -> pd.DataFrame:
    db = ROOT / "logs" / "news.db"
    if not db.exists():
        return pd.DataFrame()
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        df = pd.read_sql("SELECT ts, source, title, impact, keyword, seen_at "
                         "FROM events ORDER BY seen_at DESC LIMIT 1500", con)
        con.close()
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=60)
def load_feeds_cfg() -> dict:
    import yaml
    p = ROOT / "config" / "settings.yaml"
    if not p.exists():
        return {}
    try:
        return (yaml.safe_load(p.read_text()) or {}).get("events", {})
    except Exception:
        return {}


@st.cache_data(ttl=45)
def fetch_argus(endpoint: str):
    try:
        import httpx
        r = httpx.get(f"http://127.0.0.1:8791/{endpoint}", timeout=1.5)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def market_status():
    now = datetime.now(IST)
    t = now.hour * 60 + now.minute
    if now.weekday() >= 5:
        return "CLOSED", False, "weekend"
    if 555 <= t < 930:
        return "OPEN", True, "live session"
    return "CLOSED", False, "after hours"


SOURCE_LABELS = {
    "economictimes.indiatimes.com": "Economic Times",
    "www.moneycontrol.com": "Moneycontrol",
    "www.business-standard.com": "Business Standard",
    "www.livemint.com": "Livemint",
    "feeds.bbci.co.uk": "BBC Business",
    "www.federalreserve.gov": "US Federal Reserve",
    "news.google.com": "News mirrors",
    "www.reddit.com": "Reddit chatter",
    "stocktwits": "StockTwits",
    "manual": "Webhook",
}


# ==================================================================
#  SVG charts (pure markup)
# ==================================================================
def svg_equity(pnl: pd.Series, w=640, h=185) -> str:
    eq = 500_000.0 + pnl.cumsum()
    vals = eq.values
    if len(vals) < 2:
        return "<div class='empty'>no equity data yet</div>"
    lo, hi = min(vals.min(), 500_000.0), max(vals.max(), 500_000.0)
    pad = (hi - lo) * 0.06 or 1
    lo, hi = lo - pad, hi + pad
    pts = []
    for i, v in enumerate(vals):
        x = 2 + (w - 4) * i / (len(vals) - 1)
        y = h - 6 - (h - 14) * (v - lo) / (hi - lo)
        pts.append(f"{x:.1f},{y:.1f}")
    base_y = h - 6 - (h - 14) * (500_000.0 - lo) / (hi - lo)
    poly = " ".join(pts)
    area = f"2,{h - 4} " + poly + f" {w - 2},{h - 4}"
    col = "#4CAF7D" if vals[-1] >= vals[0] else "#D4635A"
    return (f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" '
            f'style="width:100%;height:{h}px">'
            f'<line x1="2" y1="{base_y:.1f}" x2="{w - 2}" y2="{base_y:.1f}" '
            f'stroke="#2A2C31" stroke-dasharray="3 4"/>'
            f'<polygon points="{area}" fill="{col}" opacity="0.07"/>'
            f'<polyline points="{poly}" fill="none" stroke="{col}" stroke-width="1.6"/>'
            f'</svg>')


def svg_bars(pairs, w=320, h=170) -> str:
    if not pairs:
        return "<div class='empty'>no engine data</div>"
    mx = max(abs(v) for _, v in pairs) or 1
    rows = []
    bw = 12
    for i, (name, v) in enumerate(pairs):
        y = 16 + i * ((h - 24) // max(len(pairs), 1))
        ln = max(int((w * 0.42) * abs(v) / mx), 3)
        col = "#4CAF7D" if v >= 0 else "#D4635A"
        rows.append(f"<text x='4' y='{y + 9}' class='bl'>{esc(name)}</text>")
        rows.append(f"<rect x='{w * 0.34}' y='{y}' width='{ln}' height='{bw}' "
                    f"rx='2' fill='{col}'/>")
        rows.append(f"<text x='{w * 0.34 + ln + 6}' y='{y + 9}' class='bv'>{money(v)}</text>")
    return f"<svg viewBox='0 0 {w} {h}' style='width:100%'>" + "".join(rows) + "</svg>"


def svg_months(pnl: pd.Series, n=18, w=320, h=168) -> str:
    if not len(pnl):
        return "<div class='empty'>no monthly data</div>"
    m = pnl.groupby([pnl.index.year, pnl.index.month]).sum().tail(n)
    cells = []
    cw = (w - 24) / max(len(m), 1)
    mx = max(abs(v) for v in m.values) or 1
    for i, (k, v) in enumerate(m.items()):
        x = 6 + i * cw
        hh = max(int((h - 42) * abs(v) / mx), 2)
        y = h - 28 - hh
        col = "#4CAF7D" if v >= 0 else "#D4635A"
        cells.append(f"<rect x='{x:.1f}' y='{y}' width='{max(cw - 2, 3):.1f}' "
                     f"height='{hh}' rx='1.5' fill='{col}' opacity='.85'/>")
    years = sorted({k[0] for k in m.index})
    ylab = f"<text x='6' y='{h - 8}' class='bl'>{years[0]} → {years[-1]} · monthly P&L</text>"
    return f"<svg viewBox='0 0 {w} {h}' style='width:100%'>" + "".join(cells) + ylab + "</svg>"


# ==================================================================
#  terminal html
# ==================================================================
def build_terminal(d) -> str:
    feed_rows = ""
    for _, r in d["events"].head(14).iterrows():
        src = str(r.get("source", "")).split(":")[0]
        when = str(r.get("seen_at", ""))[11:16]
        title = esc(str(r.get("title", ""))[:72])
        imp = int(r.get("impact", 0) or 0)
        sev = "s3" if imp >= 3 else ("s2" if imp >= 2 else "s1")
        feed_rows += (f"<div class='frow'><span class='ftm'>{when}</span>"
                      f"<span class='sev {sev}'></span>"
                      f"<span class='fsrc'>{esc(SOURCE_LABELS.get(src, src))[:20]}</span>"
                      f"<span class='fhl'>{title}</span></div>")
    if not feed_rows:
        feed_rows = "<div class='empty'>journal empty — argus starts on run</div>"

    pulse_cells = "".join(
        f"<div class='pc'><span>{esc(k.upper())}</span>"
        f"<b style='color:{'#D4635A' if v <= -1 else ('#E9E4D6' if abs(v) >= .5 else '#4CAF7D')}'>"
        f"{signed_pct(v)}</b></div>"
        for k, v in d["pulse"].get("components", {}).items())

    fills_rows = ""
    for _, r in d["fills"].head(7).iterrows():
        side = str(r.get("side", "")).upper()
        fills_rows += (f"<tr><td>{esc(str(r.get('ts', ''))[5:16])}</td>"
                       f"<td>{esc(str(r.get('strategy', ''))[:14])}</td>"
                       f"<td class='{('pos' if side == 'BUY' else 'neg')}'>{esc(side)}</td>"
                       f"<td>{esc(r.get('qty', ''))}</td>"
                       f"<td>{money(r.get('px', 0))}</td></tr>")
    if not fills_rows:
        fills_rows = ("<tr><td colspan='5' class='empty'>no fills yet — "
                      "first automated session fires next market open</td></tr>")

    qrows = "".join(
        f"<div class='qrow'><span class='qn'>{esc(x.get('family', ''))}</span>"
        f"<span class='qs'>score {esc(x.get('score', '—'))} · OOS {esc(x.get('oos_sharpe', '—'))}</span></div>"
        for x in d["queue"][:3]) or "<div class='empty'>queue empty</div>"

    pulse = d["pulse"]
    return f"""<!doctype html><html><head><style>
@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500;600&display=swap');
* {{ margin:0; box-sizing:border-box }}
body {{ background:#0E0F11; color:#E9E4D6; font-family:'Inter',-apple-system,sans-serif; padding:15px 18px }}
.top {{ display:flex; align-items:center; gap:12px; padding-bottom:11px; border-bottom:1px solid #24262B }}
.torch {{ width:26px; height:26px; flex-shrink:0 }}
.brand {{ font-family:'Cinzel',serif; font-weight:700; font-size:1.04rem; letter-spacing:.32em }}
.mode {{ font-family:'JetBrains Mono',monospace; font-size:.58rem; letter-spacing:.14em;
         color:#8E8A7F; border:1px solid #2C2E33; border-radius:3px; padding:2px 7px }}
.clock {{ margin-left:auto; font-family:'JetBrains Mono',monospace; font-size:.72rem;
          color:#8E8A7F; text-align:right; line-height:1.6 }}
.kpis {{ display:grid; grid-template-columns:repeat(4,1fr); gap:9px; margin-top:12px }}
.k {{ background:#16171B; border:1px solid #24262B; border-radius:9px; padding:12px 14px }}
.k .l {{ font-size:.6rem; letter-spacing:.11em; text-transform:uppercase; color:#8E8A7F; font-weight:600 }}
.k .v {{ font-family:'JetBrains Mono',monospace; font-size:1.28rem; font-weight:600; margin-top:3px }}
.k .s {{ font-size:.66rem; color:#5F5C55; margin-top:2px }}
.g {{ color:#4CAF7D }} .r {{ color:#D4635A }} .n {{ color:#E9E4D6 }} .au {{ color:#C9A227 }}
.grid {{ display:grid; grid-template-columns:repeat(12,1fr); gap:9px; margin-top:9px }}
.t {{ background:#16171B; border:1px solid #24262B; border-radius:9px; padding:12px 14px; min-width:0 }}
.t .h {{ font-size:.6rem; letter-spacing:.12em; text-transform:uppercase; color:#8E8A7F;
         font-weight:600; margin-bottom:8px; display:flex; justify-content:space-between }}
.t .h .x {{ color:#5F5C55; font-family:'JetBrains Mono',monospace; letter-spacing:0; text-transform:none }}
.c8 {{ grid-column:span 8 }} .c7 {{ grid-column:span 7 }} .c6 {{ grid-column:span 6 }}
.c5 {{ grid-column:span 5 }} .c4 {{ grid-column:span 4 }} .c3 {{ grid-column:span 3 }}
.frow {{ display:flex; gap:9px; padding:4px 0; border-bottom:1px solid #1C1E22;
         font-family:'JetBrains Mono',monospace; font-size:.69rem }}
.ftm {{ color:#5F5C55; width:38px }} .fsrc {{ color:#8E8A7F; width:104px; overflow:hidden;
text-overflow:ellipsis; white-space:nowrap }} .fhl {{ color:#E9E4D6; overflow:hidden;
text-overflow:ellipsis; white-space:nowrap }}
.sev {{ width:7px; height:7px; border-radius:2px; margin-top:5px; flex-shrink:0 }}
.s3 {{ background:#D4635A }} .s2 {{ background:#C9A227 }} .s1 {{ background:#33353B }}
.empty {{ color:#5F5C55; font-size:.7rem; padding:5px 0 }}
.pcs {{ display:grid; grid-template-columns:repeat(2,1fr); gap:7px }}
.pc {{ background:#121316; border:1px solid #222428; border-radius:7px; padding:7px 10px }}
.pc span {{ font-size:.54rem; letter-spacing:.1em; text-transform:uppercase; color:#8E8A7F }}
.pc b {{ font-family:'JetBrains Mono',monospace; font-size:.9rem; display:block; margin-top:2px }}
.score {{ font-family:'JetBrains Mono',monospace; font-size:1.9rem; font-weight:600 }}
.bl {{ font-family:'JetBrains Mono',monospace; font-size:.68rem; fill:#8E8A7F }}
.bv {{ font-family:'JetBrains Mono',monospace; font-size:.68rem; fill:#E9E4D6 }}
.srow {{ display:flex; align-items:center; gap:12px; padding:10px 2px; border-bottom:1px solid #1C1E22 }}
.srow:last-child {{ border-bottom:none }}
.srow .nm {{ font-family:'Cinzel',serif; letter-spacing:.2em; font-size:.8rem; width:84px; color:#E9E4D6 }}
.srow .ds {{ color:#8E8A7F; font-size:.73rem; flex:1 }}
.srow .st {{ font-family:'JetBrains Mono',monospace; font-size:.6rem; letter-spacing:.12em;
             display:flex; align-items:center; gap:6px }}
.sdot {{ width:7px; height:7px; border-radius:50%; flex-shrink:0 }}
table {{ width:100%; border-collapse:collapse; font-family:'JetBrains Mono',monospace; font-size:.69rem }}
th {{ color:#8E8A7F; text-transform:uppercase; font-size:.56rem; letter-spacing:.1em;
      text-align:left; padding:4px 6px; border-bottom:1px solid #24262B }}
td {{ padding:5px 6px; border-bottom:1px solid #1C1E22; color:#E9E4D6 }}
.pos {{ color:#4CAF7D }} .neg {{ color:#D4635A }}
.qrow {{ display:flex; justify-content:space-between; padding:5px 0; border-bottom:1px solid #1C1E22 }}
.qn {{ font-family:'Cinzel',serif; letter-spacing:.14em; color:#E9E4D6; font-size:.74rem }}
.qs {{ font-family:'JetBrains Mono',monospace; color:#8E8A7F; font-size:.64rem }}
.rules {{ font-size:.68rem; color:#8E8A7F; line-height:1.95 }}
.sw2 {{ display:inline-block; width:7px; height:7px; border-radius:2px; margin:0 5px 0 8px }}
.foot {{ margin-top:14px; padding-top:12px; border-top:1px solid #24262B; text-align:center;
         font-size:.64rem; color:#5F5C55 }}
.foot b {{ color:#8E8A7F; font-family:'Cinzel',serif; letter-spacing:.24em }}
@media(max-width:1000px) {{ .kpis {{grid-template-columns:repeat(2,1fr)}} .c8,.c7,.c6,.c5,.c4,.c3 {{grid-column:span 12}} }}
</style></head><body>

<div class="top">
  {TORCH_SVG}
  <span class="brand">PROMETHEVS</span>
  <span class="mode">PAPER</span>
  <div class="clock"><span id="clock">--:--:--</span> IST<br>{esc(d["mkt"])} · {esc(d["mkt_sub"])} · {esc(d["date_str"])}</div>
</div>

<div class="kpis">
  <div class="k"><div class="l">Core engine trust</div><div class="v g">{esc(d["trust"])}</div><div class="s">iron fly OOS · from Jan 2024</div></div>
  <div class="k"><div class="l">Book CAGR · OOS</div><div class="v g">{esc(str(d["cagr"]))}%</div><div class="s">combined engines</div></div>
  <div class="k"><div class="l">Simulated P&L</div><div class="v g">{esc(d["totalpnl"])}</div><div class="s">{esc(str(d["years"]))} years · all costs</div></div>
  <div class="k"><div class="l">Ideas tested</div><div class="v au">{esc(d["tested"])}</div><div class="s">{esc(d["killed"])} killed · {esc(d["qcount"])} in queue</div></div>
</div>

<div class="grid">
  <div class="t c8"><div class="h">Equity · ₹5L start <span class="x">simulated · full costs</span></div>{d["eqsvg"]}</div>
  <div class="t c4"><div class="h">Engines · cumulative P&L</div>{d["bars"]}</div>

  <div class="t c6"><div class="h">Argus live feed <span class="x">{esc(d["feedcount"])} items · 13 sources</span></div>{feed_rows}</div>

  <div class="t c3"><div class="h">Global pulse</div>
    <div class="score" style="color:{esc(d["pulse_col"])}">{esc(str(d["pulse_score"]))}<span style="font-size:.85rem;color:#5F5C55">/100</span></div>
    <div style="font-size:.62rem;color:{esc(d["pulse_col"])};letter-spacing:.16em;margin-bottom:8px">{esc(str(d["pulse_level"]).upper())}</div>
    <div class="pcs">{pulse_cells}</div></div>

  <div class="t c3"><div class="h">Monthly P&L</div>{d["months"]}</div>

  <div class="t c7"><div class="h">System status</div>
    <div class="srow"><span class="sdot" style="background:{esc(d["ember_dot"])}"></span><span class="nm">EMBER</span><span class="ds">{esc(d["ember_det"])}</span><span class="st">{esc(d["ember_st"])}</span></div>
    <div class="srow"><span class="sdot" style="background:#4CAF7D"></span><span class="nm">ANVIL</span><span class="ds">{esc(d["anvil_det"])}</span><span class="st">ACTIVE</span></div>
    <div class="srow"><span class="sdot" style="background:{esc(d["argus_dot"])}"></span><span class="nm">ARGVS</span><span class="ds">{esc(d["argus_det"])}</span><span class="st">{esc(d["argus_st"])}</span></div>
    <div class="rules" style="margin-top:10px">
      <span class="sw2" style="background:#D4635A"></span>STOP — war · RBI shock · halt → force-flat
      <span class="sw2" style="background:#C9A227"></span>PAUSE — SEBI · tariffs → no new entries
      <span class="sw2" style="background:#33353B"></span>logged — no action
    </div></div>

  <div class="t c5"><div class="h">Recent paper fills</div>
    <table><tr><th>time</th><th>engine</th><th>side</th><th>qty</th><th>price</th></tr>{fills_rows}</table></div>

  <div class="t c6"><div class="h">Next into the fire <span class="x">live-test queue</span></div>{qrows}</div>
  <div class="t c6"><div class="h">Graveyard <span class="x">killed by evidence</span></div>
    <div class="rules">{esc(d["graveyard"])}</div></div>
</div>

<div class="foot"><b>PROMETHEVS</b> · powered by <b style="color:#8E8A7F">GLM 5.3 Flash</b> ·
simulated results · not financial advice</div>

<script>
setInterval(()=>{{const e=document.getElementById('clock');if(e){{e.textContent=
new Date().toLocaleTimeString('en-GB',{{timeZone:'Asia/Kolkata'}});}}}},1000);
</script>
</body></html>"""


@st.fragment(run_every="45s")
def terminal_fragment():
    report = load_json("results/backtest_report.json")
    pnl_df = load_pnl()
    ledger = load_csv("results/atlas/ledger.csv")
    queue = load_json("results/atlas/paper_queue.json").get("queue", [])
    fills = load_fills()
    state = load_session_state()
    ev_cfg = load_feeds_cfg()
    news = fetch_argus("status")
    pulse = fetch_argus("pulse") or {"score": 0, "level": "calm", "components": {}}
    events_db = load_news_events()
    now = datetime.now(IST)
    mkt, live, mkt_sub = market_status()
    argus_on = news is not None

    ledger_p = ROOT / "results/atlas/ledger.csv"
    ledger_ts = datetime.fromtimestamp(ledger_p.stat().st_mtime).strftime("%d %b %H:%M") \
        if ledger_p.exists() else "—"
    fills_today = 0
    if len(fills):
        try:
            fills_today = int((pd.to_datetime(fills["ts"]).dt.date == now.date()).sum())
        except Exception:
            fills_today = 0
    sess_today = bool(state) and state.get("date") == now.date().isoformat()

    iron = report.get("iron_fly", {}).get("oos", {})
    oos_comb = report.get("combined", {}).get("oos", {})
    comb = report.get("combined", {}).get("full", {})
    shp = float(iron.get("sharpe") or 0.0)

    engine_pairs = []
    for eng, nm in (("iron_fly", "Iron Fly"), ("gap", "Gap"), ("overnight", "Overnight")):
        v = 0.0
        if not pnl_df.empty and eng in pnl_df.columns:
            v = float(pnl_df[eng].cumsum().iloc[-1])
        engine_pairs.append((nm, v))

    graveyard = ("Donchian · NR7 coils · golden cross · pairs stat-arb · US-lag pickup · "
                 "time-of-day calendar · compression ORB · raw streaks · VWAP fade · "
                 "52w-high · calm-bounce · low-vol grind · daily breakout")

    html_out = build_terminal({
        "mkt": mkt, "mkt_sub": mkt_sub, "date_str": now.strftime("%a %d %b %H:%M"),
        "trust": "—" if not iron else f"{min(shp / 1.5, 5.0):.1f} / 5",
        "cagr": oos_comb.get("cagr_pct", "—"),
        "totalpnl": money(comb.get("total_pnl", 0)),
        "years": comb.get("years", "—"),
        "tested": f"{len(ledger):,}", "killed": f"{len(ledger) - len(queue):,}",
        "qcount": len(queue),
        "eqsvg": svg_equity(pnl_df["combined"]) if not pnl_df.empty and "combined" in pnl_df.columns
                 else "<div class='empty'>run scripts/run_backtest.py first</div>",
        "bars": svg_bars(engine_pairs),
        "events": events_db, "feedcount": len(events_db),
        "pulse": pulse,
        "pulse_score": pulse.get("score", 0),
        "pulse_level": pulse.get("level", "calm"),
        "pulse_col": {"calm": "#4CAF7D", "elevated": "#C9A227",
                      "risk_off": "#D4635A"}.get(pulse.get("level", "calm"), "#4CAF7D"),
        "months": svg_months(pnl_df["combined"]) if not pnl_df.empty and "combined" in pnl_df.columns
                  else "<div class='empty'>no data</div>",
        "ember_det": f"Execution engines · {fills_today} trades today · "
                     f"{'in session' if sess_today else 'idle until next open'}",
        "ember_st": "RUNNING" if sess_today else "SCHEDULED",
        "ember_dot": "#4CAF7D" if sess_today else "#C9A227",
        "anvil_det": f"{len(ledger):,} ideas tested · {len(queue)} in live-test queue · ledger {ledger_ts}",
        "argus_det": f"{len(ev_cfg.get('feeds', []))} sources · "
                     f"{news.get('headlines_seen', 0) if news else 0} items in memory",
        "argus_st": "ONLINE" if argus_on else "OFFLINE",
        "argus_dot": "#4CAF7D" if argus_on else "#6E6A5E",
        "fills": fills,
        "queue": queue,
        "graveyard": graveyard,
    })
    components.v1.html(html_out, height=1010, scrolling=False)


def main():
    st.markdown("", unsafe_allow_html=True)
    terminal_fragment()


if __name__ == "__main__":
    main()
