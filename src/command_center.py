"""PROMETHEUS Command Center — marble, bronze, fire.

Real public-domain imagery (Pergamon Prometheus group, CC BY 4.0),
inscriptional Cinzel + GFS Didot Greek typography, single bronze accent,
ember fire reserved for the torch mark. Data stays in JetBrains Mono.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
IST = timezone(timedelta(hours=5, minutes=30))

# ---- palette: dark marble / ivory / antique bronze / ember
BG, SURFACE, SURFACE2 = "#131215", "#1B191D", "#232025"
BORDER, BORDER2 = "#2E2B31", "#3A363D"
IVORY, MUTED, FAINT = "#EAE4D8", "#A39B8B", "#6E6A5E"
BRONZE, EMBER = "#C9A227", "#E8833A"
POS, NEG, WARN = "#5FA97C", "#C96A5F", "#C9A227"

st.set_page_config(page_title="PROMETHEUS", page_icon="🔥", layout="wide")

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@400;600;700&family=GFS+Didot&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500;600&display=swap');

.stApp {{ background:{BG}; color:{IVORY}; font-family:'Inter',-apple-system,sans-serif; }}
#MainMenu, footer, header {{ visibility:hidden }}

.brandbar {{ display:flex; align-items:center; gap:12px; padding:14px 4px;
            border-bottom:1px solid {BORDER} }}
.brandbar .name {{ font-family:'Cinzel',serif; font-weight:700; font-size:1.28rem;
                   letter-spacing:.34em; color:{IVORY} }}
.brandbar .tag {{ font-family:'GFS Didot',serif; color:{FAINT}; font-size:.86rem;
                  font-style:italic }}
.brandbar .meta {{ margin-left:auto; font-family:'JetBrains Mono',monospace;
                   font-size:.7rem; color:{MUTED}; text-align:right; line-height:1.7 }}
.pill {{ display:inline-flex; align-items:center; gap:6px; padding:2px 10px;
         border:1px solid {BORDER2}; border-radius:3px; font-size:.62rem;
         letter-spacing:.12em; color:{MUTED}; font-weight:600; margin-left:8px }}
.pill .dot {{ width:6px; height:6px; border-radius:50% }}

.hero {{ display:flex; gap:26px; margin:20px 0 6px; align-items:stretch }}
.hero .txt {{ flex:1.15 }}
.hero .greek {{ font-family:'GFS Didot',serif; font-size:1.9rem; color:{IVORY}; line-height:1.35 }}
.hero .greek .accent {{ color:{EMBER} }}
.hero .motto {{ font-family:'Cinzel',serif; font-size:.72rem; letter-spacing:.42em;
                color:{BRONZE}; margin:10px 0 14px }}
.hero .line {{ color:{MUTED}; font-size:.86rem; line-height:1.75; max-width:560px }}
.hero .img {{ flex:0.85; max-width:340px; display:flex; flex-direction:column }}
.hero .img img {{ border-radius:6px; border:1px solid {BORDER2};
                  filter:sepia(18%) contrast(105%); width:100% }}
.hero .credit {{ font-size:.6rem; color:{FAINT}; margin-top:5px; line-height:1.5 }}

.panel {{ background:{SURFACE}; border:1px solid {BORDER}; border-radius:8px;
          padding:14px 16px }}
.panel .lbl {{ font-size:.62rem; letter-spacing:.12em; text-transform:uppercase;
               color:{MUTED}; font-weight:600; margin-bottom:3px }}
.panel .val {{ font-family:'JetBrains Mono',monospace; font-size:1.32rem; font-weight:600;
               font-variant-numeric:tabular-nums; letter-spacing:-.02em }}
.panel .sub {{ font-size:.72rem; color:{FAINT}; margin-top:3px }}
.pos {{ color:{POS} }} .neg {{ color:{NEG} }} .neu {{ color:{IVORY} }} .au {{ color:{BRONZE} }}

.strow {{ display:flex; align-items:center; gap:14px; padding:12px 18px;
          background:{SURFACE}; border:1px solid {BORDER}; border-radius:8px }}
.strow + .strow {{ margin-top:7px }}
.strow .nm {{ font-family:'Cinzel',serif; font-weight:600; font-size:.9rem;
              letter-spacing:.18em; width:120px; color:{IVORY} }}
.strow .ds {{ color:{MUTED}; font-size:.8rem; flex:1; line-height:1.5 }}
.strow .st {{ font-family:'JetBrains Mono',monospace; font-size:.66rem; letter-spacing:.14em;
              display:flex; align-items:center; gap:6px; color:{MUTED} }}
.dot {{ width:7px; height:7px; border-radius:50%; display:inline-block }}
.d-on {{ background:{POS} }} .d-off {{ background:{FAINT} }} .d-warn {{ background:{BRONZE} }}

.sec {{ font-family:'Cinzel',serif; font-size:.72rem; letter-spacing:.3em;
        text-transform:uppercase; color:{BRONZE}; margin:1.5rem 0 .6rem 0 }}

code {{ color:{BRONZE} !important; background:{SURFACE2} !important;
        padding:1px 6px !important; border-radius:4px !important;
        font-family:'JetBrains Mono',monospace !important; font-size:.82em !important }}

div[data-testid="stDataFrame"] {{ border:1px solid {BORDER}; border-radius:8px; overflow:hidden }}
div[data-testid="stDataFrame"] thead tr th {{
    background:{SURFACE2} !important; color:{MUTED} !important;
    font-size:.68rem !important; letter-spacing:.07em; text-transform:uppercase;
    font-weight:600 !important }}
div[data-testid="stDataFrame"] td {{ font-family:'JetBrains Mono',monospace !important;
                                     font-size:.76rem !important; color:{IVORY} !important }}

.stTabs [data-baseweb="tab-list"] {{ gap:0; border-bottom:1px solid {BORDER} }}
.stTabs [data-baseweb="tab"] {{ padding:9px 18px; color:{MUTED};
    font-family:'Cinzel',serif; font-size:.78rem; letter-spacing:.2em;
    border-bottom:2px solid transparent; font-weight:600 }}
.stTabs [aria-selected="true"] {{ color:{EMBER}; border-bottom:2px solid {EMBER} }}
.stTabs [data-baseweb="tab-highlight"] {{ display:none }}

.feed {{ font-family:'JetBrains Mono',monospace; font-size:.74rem; line-height:1.9 }}
.feed .row {{ padding:5px 0; border-bottom:1px solid #201D22; display:flex; gap:10px }}
.feed .tm {{ color:{FAINT}; width:50px; flex-shrink:0 }}
.feed .src {{ color:{MUTED}; width:190px; flex-shrink:0; overflow:hidden;
              text-overflow:ellipsis; white-space:nowrap }}
.feed .hl {{ color:{IVORY}; overflow:hidden; text-overflow:ellipsis; white-space:nowrap }}
.sev {{ width:8px; height:8px; border-radius:2px; margin-top:6px; flex-shrink:0 }}
.s3 {{ background:{NEG} }} .s2 {{ background:{BRONZE} }} .s1 {{ background:#3B3840 }}

.legend {{ font-size:.78rem; color:{MUTED}; line-height:2.0 }}
.sw {{ display:inline-block; width:8px; height:8px; border-radius:2px; margin:0 6px 0 10px }}

.foot {{ text-align:center; padding:2.4rem 0 .7rem; color:{FAINT}; font-size:.72rem }}
.foot .nm {{ font-family:'Cinzel',serif; letter-spacing:.28em; color:{MUTED}; font-size:.74rem }}
</style>"""

TORCH_SVG = """<svg width="30" height="30" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
<path d="M12 2.2c1.9 2.1 3.4 3.9 3.4 6.1 0 1.5-.8 2.6-1.9 3.2.5-1.2.4-2.6-.7-4-.5 2.2-2.3 2.6-2.3 4.6 0 .8.3 1.4.8 1.9-1.7-.3-3-1.8-3-3.9 0-3 2.6-5 3.7-7.9z" fill="#E8833A"/>
<path d="M12 16.6c1.7 0 3 1.3 3 2.9 0 1.4-1 2.3-1.6 2.3h-2.8c-.6 0-1.6-.9-1.6-2.3 0-1.6 1.3-2.9 3-2.9z" fill="#C9A227"/>
</svg>"""


def inject():
    st.markdown(CSS, unsafe_allow_html=True)


def money(x):
    try:
        return f"₹{float(x):,.0f}"
    except (TypeError, ValueError):
        return "—"


def panel(col, label, value, sub="", cls=""):
    sub_html = f'<div class="sub">{sub}</div>' if sub else ""
    col.markdown(f'<div class="panel"><div class="lbl">{label}</div>'
                 f'<div class="val {cls}">{value}</div>{sub_html}</div>',
                 unsafe_allow_html=True)


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
    "news.google.com": "News mirrors (RBI·SEBI·budget)",
    "www.reddit.com": "Reddit — IndianStockMarket",
    "stocktwits": "StockTwits chatter",
    "manual": "Webhook (manual)",
}


# ------------------------------------------------------------------ figures
def equity_fig(pnl: pd.Series):
    eq = 500_000.0 + pnl.cumsum()
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=eq.index, y=eq.values, line=dict(color=BRONZE, width=1.7),
        hovertemplate="%{x|%b %Y}<br>%{y:,.0f}<extra></extra>"))
    fig.add_hline(y=500_000.0, line_color=BORDER2, line_width=1)
    fig.update_layout(
        height=300, margin=dict(l=52, r=14, t=8, b=28),
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, font=dict(color=MUTED),
        yaxis=dict(gridcolor="#211E24", tickprefix="₹", tickformat=",.0s",
                   tickfont=dict(family="JetBrains Mono", size=10), zeroline=False),
        xaxis=dict(gridcolor="#19171B", tickfont=dict(family="JetBrains Mono", size=10)),
        showlegend=False, hovermode="x unified")
    return fig


def monthly_cal(pnl: pd.Series):
    m = pnl.groupby([pnl.index.year, pnl.index.month]).sum().unstack()
    txt = [[f"{v/1000:+.0f}k" if v == v else "" for v in row] for row in m.values]
    fig = go.Figure(go.Heatmap(
        z=m.values, x=[f"M{c}" for c in m.columns], y=m.index.astype(str),
        colorscale=[[0, "#6E4440"], [0.35, "#3A2A28"], [0.49, "#232126"],
                    [0.51, "#232C25"], [0.75, "#2C5541"], [1, "#3E7A5B"]],
        zmid=0, showscale=False, xgap=2, ygap=2, text=txt,
        texttemplate="%{text}", textfont=dict(family="JetBrains Mono", size=8)))
    fig.update_layout(height=200, margin=dict(l=6, r=6, t=4, b=4),
                      paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
                      xaxis=dict(tickfont=dict(family="JetBrains Mono", size=8, color=MUTED)),
                      yaxis=dict(tickfont=dict(family="JetBrains Mono", size=9, color=MUTED)))
    return fig


def engine_bars(pnl_df: pd.DataFrame):
    cols = [c for c in ("iron_fly", "gap", "overnight") if c in pnl_df.columns]
    if not cols:
        return None
    cum = pnl_df[cols].cumsum().iloc[-1].sort_values()
    names = {"iron_fly": "Iron Fly · Tue 0DTE", "gap": "Gap Reversion",
             "overnight": "Overnight Drift"}
    colors = [POS if v >= 0 else NEG for v in cum.values]
    fig = go.Figure(go.Bar(
        y=[names.get(k, k) for k in cum.index], x=cum.values, orientation="h",
        marker=dict(color=colors), width=0.45,
        text=[money(v) for v in cum.values], textposition="outside",
        textfont=dict(family="JetBrains Mono", color=MUTED, size=10)))
    fig.update_layout(height=150, margin=dict(l=6, r=100, t=4, b=4),
                      paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
                      xaxis=dict(visible=False),
                      yaxis=dict(tickfont=dict(size=11, color=IVORY)))
    return fig


def strow(name, detail, status, dot_cls):
    return (f'<div class="strow"><span class="dot {dot_cls}"></span>'
            f'<span class="nm">{name}</span><span class="ds">{detail}</span>'
            f'<span class="st">{status}</span></div>')


# -------------------------------------------------------------------- main
def main():
    inject()
    report = load_json("results/backtest_report.json")
    pnl_df = load_pnl()
    ledger = load_csv("results/atlas/ledger.csv")
    queue = load_json("results/atlas/paper_queue.json").get("queue", [])
    fills = load_fills()
    state = load_session_state()
    ev_cfg = load_feeds_cfg()
    news = fetch_argus("status")
    pulse = fetch_argus("pulse")
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

    # ---------------------------------------------------------- brand bar
    st.markdown(
        f'<div class="brandbar">{TORCH_SVG}'
        f'<span class="name">PROMETHEVS</span>'
        f'<span class="tag">the one who saw tomorrow first</span>'
        f'<span class="pill"><span class="dot {"#5FA97C" if live else "#6E6A5E"}"></span>{mkt}</span>'
        f'<span class="pill"><span class="dot {"#5FA97C" if argus_on else "#6E6A5E"}"></span>ARGVS '
        f'{"ON" if argus_on else "OFF"}</span>'
        f'<div class="meta">{now.strftime("%a %d %b · %H:%M IST")}</div></div>',
        unsafe_allow_html=True)

    # ------------------------------------------------------------- hero
    iron_oos = report.get("iron_fly", {}).get("oos", {})
    oos_comb = report.get("combined", {}).get("oos", {})
    comb = report.get("combined", {}).get("full", {})
    h1, h2 = st.columns([1.25, 0.75])
    with h1:
        st.markdown(
            f'<div class="hero" style="flex-direction:column">'
            f'<div class="greek">Ideas go into the fire.<br>'
            f'<span class="accent">What remains, trades.</span></div>'
            f'<div class="motto">ΠΡΟΜΗΘΕΥΣ · FORETHOUGHT · FIRE · FORTVNE</div>'
            f'<div class="line">A self-evolving system for NSE index derivatives. '
            f'Every idea is tested against years of real market data with every fee '
            f'counted; losers are killed permanently, survivors rehearse with paper '
            f'money before any rupee moves.</div>'
            f'<div style="display:flex;gap:12px;margin-top:18px">'
            f'<div class="panel" style="flex:1"><div class="lbl">Core engine trust</div>'
            f'<div class="val pos">{iron_oos.get("sharpe","—")}</div>'
            f'<div class="sub">OOS · from Jan 2024</div></div>'
            f'<div class="panel" style="flex:1"><div class="lbl">Book CAGR</div>'
            f'<div class="val pos">{oos_comb.get("cagr_pct","—")}%</div>'
            f'<div class="sub">recent regime</div></div>'
            f'<div class="panel" style="flex:1"><div class="lbl">Simulated P&L</div>'
            f'<div class="val pos">{money(comb.get("total_pnl",0))}</div>'
            f'<div class="sub">{comb.get("years","—")} yrs · all costs in</div></div>'
            f'<div class="panel" style="flex:1"><div class="lbl">Ideas tested</div>'
            f'<div class="val neu">{len(ledger):,}</div>'
            f'<div class="sub">ledger · {ledger_ts}</div></div>'
            f'</div></div>', unsafe_allow_html=True)
    with h2:
        img = ROOT / "assets" / "prometheus_marble.jpg"
        if img.exists():
            st.image(str(img), use_container_width=True)
            st.caption("Marble group — the freeing of Prometheus (Pergamon) · "
                       "photo: Mark Landon, Wikimedia Commons, CC BY 4.0")

    # --------------------------------------------------------- status strip
    st.markdown('<div class="sec">System</div>', unsafe_allow_html=True)
    st.markdown(
        strow("EMBER", f"Execution engines · {fills_today} trades today · "
                       f"{'in session' if sess_today else 'idle until next open'}",
              "RUNNING" if sess_today else "SCHEDULED", "d-on" if sess_today else "d-warn")
        + strow("ANVIL", f"Research lab · {len(ledger):,} ideas tested · "
                         f"{len(queue)} awaiting live rehearsal",
                "ACTIVE", "d-on")
        + strow("ARGUS", f"24/7 watchman · {len(ev_cfg.get('feeds', []))} sources · "
                         f"{news.get('headlines_seen', 0) if news else 0} items in memory",
                "ONLINE" if argus_on else "OFFLINE", "d-on" if argus_on else "d-off"),
        unsafe_allow_html=True)

    if st.button("Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    tabs = st.tabs(["NEWS WATCH", "PORTFOLIO", "RESEARCH", "GUIDE"])

    # ------------------------------------------------------------ NEWS WATCH
    with tabs[0]:
        if pulse:
            comps = pulse.get("components", {})
            lvl = pulse.get("level", "calm")
            color = {"calm": POS, "elevated": WARN, "risk_off": NEG}.get(lvl, POS)
            st.markdown(
                f'<div class="panel" style="display:flex;align-items:center;gap:22px;flex-wrap:wrap">'
                f'<div><div class="lbl">Global pulse · overnight markets</div>'
                f'<div class="val" style="color:{color}">{pulse.get("score")}/100 · {lvl.upper()}</div></div>'
                f'<div style="display:flex;gap:20px;flex-wrap:wrap;margin-left:auto">'
                + "".join(f'<div><span style="font-size:.6rem;color:{FAINT};text-transform:'
                          f'uppercase;letter-spacing:.1em">{k}</span><br>'
                          f'<span style="font-family:JetBrains Mono,monospace;font-weight:600;'
                          f'color:{NEG if v <= -1 else (IVORY if abs(v) >= .5 else POS)}">{v:+.2f}%</span></div>'
                          for k, v in comps.items())
                + '</div></div>', unsafe_allow_html=True)
        else:
            st.error("ARGVS is offline — `bash scripts/argus.sh start`")

        st.markdown('<div class="sec">Live feed · latest reads</div>',
                    unsafe_allow_html=True)
        if len(events_db):
            rows = ""
            for _, r in events_db.head(30).iterrows():
                src = str(r.get("source", "")).split(":")[0]
                when = str(r.get("seen_at", r.get("ts", "")))[11:16]
                title = str(r.get("title", ""))[:100].replace("<", "&lt;")
                imp = int(r.get("impact", 0) or 0)
                sev = "s3" if imp >= 3 else ("s2" if imp >= 2 else "s1")
                rows += (f'<div class="row"><span class="tm">{when}</span>'
                         f'<span class="sev {sev}"></span><span class="src">'
                         f'{SOURCE_LABELS.get(src, src)[:26]}</span>'
                         f'<span class="hl">{title}</span></div>')
            st.markdown(f'<div class="panel feed">{rows}</div>', unsafe_allow_html=True)
        else:
            st.caption("Journal empty — starts when ARGVS runs.")

        st.markdown('<div class="sec">Sources</div>', unsafe_allow_html=True)
        feeds = ev_cfg.get("feeds", [])
        srows = []
        for url in feeds:
            host = url.split("/")[2] if url.count("/") >= 2 else url
            cnt = int((events_db["source"].str.split(":").str[0] == host).sum()) \
                if len(events_db) else 0
            srows.append({"source": SOURCE_LABELS.get(host, host), "items (6h)": cnt})
        st.dataframe(pd.DataFrame(srows), use_container_width=True, hide_index=True,
                     height=330)

        st.markdown('<div class="sec">Response rules</div>', unsafe_allow_html=True)
        st.markdown(f"""
<div class="legend">
<span class="sw" style="background:{NEG}"></span><b>STOP</b> — war · RBI shock · budget surprise · trading halt → force-flat, stay out<br>
<span class="sw" style="background:{BRONZE}"></span><b>PAUSE</b> — SEBI action · tariffs · rating cuts → no new entries<br>
<span class="sw" style="background:#3B3840"></span><b>logged</b> — everything else is journaled, no action
</div>""", unsafe_allow_html=True)

    # ------------------------------------------------------------- PORTFOLIO
    with tabs[1]:
        rows = []
        for eng, label, story in (
                ("iron_fly", "Iron Fly · Tue 0DTE", "sells defined-risk option combos on expiry day"),
                ("gap", "Gap Reversion", "fades abnormal openings toward prior close"),
                ("overnight", "Overnight Drift", "overnight long only when fear is high")):
            blk = (report.get(eng, {}).get("oos")
                   or report.get(eng, {}).get("is")
                   or report.get(eng, {}).get("full", {}))
            shp = float(blk.get("sharpe") or 0.0)
            rows.append({"engine": label, "thesis": story,
                         "trust (0–5)": "—" if not blk else f"{min(shp / 1.5, 5.0):.1f}",
                         "win rate": f"{blk.get('win_rate_pct', '—')}%",
                         "worst day": money(blk.get("worst_day", 0)),
                         "trades": blk.get("trade_days", "—")})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption("trust = Sharpe ÷ 1.5 on data the engines never saw during design (Jan 2024 →)")

        if not pnl_df.empty and "combined" in pnl_df.columns:
            st.markdown('<div class="sec">Equity · ₹5L start (simulated, full costs)</div>',
                        unsafe_allow_html=True)
            st.plotly_chart(equity_fig(pnl_df["combined"]), use_container_width=True)
            st.markdown('<div class="sec">Monthly P&L</div>', unsafe_allow_html=True)
            st.plotly_chart(monthly_cal(pnl_df["combined"]), use_container_width=True)

        bars = engine_bars(pnl_df) if not pnl_df.empty else None
        if bars:
            st.markdown('<div class="sec">Per-engine cumulative P&L</div>',
                        unsafe_allow_html=True)
            st.plotly_chart(bars, use_container_width=True)

        st.markdown('<div class="sec">Risk envelope · always armed</div>',
                    unsafe_allow_html=True)
        import yaml as _yaml
        risk = {}
        cfgp = ROOT / "config" / "settings.yaml"
        if cfgp.exists():
            try:
                risk = (_yaml.safe_load(cfgp.read_text()) or {}).get("risk", {})
            except Exception:
                pass
        st.markdown(f"""
<div class="legend" style="background:{SURFACE};border:1px solid {BORDER};
border-radius:8px;padding:12px 18px">
daily kill <b style="color:{IVORY}">{risk.get('daily_loss_kill_pct', 2)}%</b>
&nbsp;·&nbsp; weekly kill <b style="color:{IVORY}">{risk.get('weekly_loss_kill_pct', 4)}%</b>
&nbsp;·&nbsp; per-trade risk <b style="color:{IVORY}">{risk.get('risk_per_trade_pct', 1)}%</b>
&nbsp;·&nbsp; leverage <b style="color:{IVORY}">{risk.get('max_lots_per_10l', 3)} lots / ₹10L</b>
&nbsp;·&nbsp; theta gate VIX <b style="color:{IVORY}">≤ {risk.get('vix_max_for_theta', 22)}</b>
</div>""", unsafe_allow_html=True)

        st.markdown('<div class="sec">Session book</div>', unsafe_allow_html=True)
        if state:
            s1, s2, s3 = st.columns(3)
            panel(s1, "Session date", str(state.get("date", "—")))
            panel(s2, "Realized today", money(state.get("realized", 0)),
                  cls="pos" if state.get("realized", 0) >= 0 else "neg")
            pos = state.get("positions", {})
            panel(s3, "Open positions", str(len(pos)))
            if pos:
                st.dataframe(pd.DataFrame(
                    [{"strategy": k, **{kk: vv for kk, vv in v.items() if kk != "legs"}}
                     for k, v in pos.items()]),
                    use_container_width=True, hide_index=True)
        else:
            st.caption("No session yet today.")

        if len(fills):
            st.markdown('<div class="sec">Recent fills</div>', unsafe_allow_html=True)
            cols = [c for c in ("ts", "strategy", "side", "qty", "px") if c in fills.columns]
            st.dataframe(fills[cols], use_container_width=True, height=250)

    # -------------------------------------------------------------- RESEARCH
    with tabs[2]:
        st.markdown(f"""
<div class="panel" style="display:flex;gap:40px;flex-wrap:wrap">
<div><div class="lbl">Ideas tested</div><div class="val neu">{len(ledger):,}</div></div>
<div><div class="lbl">Passed</div><div class="val pos">{len(queue)}</div></div>
<div><div class="lbl">Killed</div><div class="val neg">{len(ledger)-len(queue):,}</div></div>
<div><div class="lbl">Survival rate</div><div class="val au">{(len(queue)/max(len(ledger),1)*100):.1f}%</div></div>
</div>""", unsafe_allow_html=True)
        st.caption("Every candidate runs 12+ years of real data with the full Indian cost "
                   "stack. Conviction ≥ 0.60 promotes to paper. Losers are recorded forever.")

        st.markdown('<div class="sec">Live-test queue · paper next</div>',
                    unsafe_allow_html=True)
        if queue:
            qdf = pd.DataFrame(queue)
            cols = [c for c in ("family", "params", "score", "oos_sharpe",
                                "oos_cagr_pct", "n_trades", "quarters_hit")
                    if c in qdf.columns]
            st.dataframe(qdf[cols], use_container_width=True, hide_index=True)
        else:
            st.caption("Queue empty — run scripts/evolve.py for the next generation.")

        if len(ledger):
            best = ledger.sort_values("score", ascending=False).groupby("family").head(1) \
                .sort_values("score", ascending=False)
            st.markdown('<div class="sec">Family leaderboard</div>', unsafe_allow_html=True)
            pick = st.selectbox("family", best["family"].tolist(),
                                label_visibility="collapsed")
            drill = ledger[ledger["family"] == pick].sort_values("score", ascending=False)
            dcols = [c for c in ("params", "filter", "exit", "score", "full_sharpe",
                                 "oos_sharpe", "oos_cagr_pct", "oos_max_dd_pct",
                                 "oos_pf", "n_trades", "promoted") if c in drill.columns]
            st.dataframe(drill[dcols], use_container_width=True, hide_index=True)
            st.markdown('<div class="sec">The graveyard</div>', unsafe_allow_html=True)
            wl = best[(best["score"] >= 0.45) & (best["score"] < 0.60)]
            if len(wl):
                st.caption("On the bubble: " + ", ".join(wl["family"]))
            with st.expander("all killed families + evidence"):
                st.dataframe(best[["family", "params", "exit", "score", "oos_sharpe",
                                   "n_trades"]], use_container_width=True, hide_index=True)

    # ----------------------------------------------------------------- GUIDE
    with tabs[3]:
        st.markdown(f"""
<div class="legend" style="background:{SURFACE};border:1px solid {BORDER};
border-radius:8px;padding:18px 22px;font-size:.85rem">
<b style="color:{IVORY};font-family:Cinzel,serif;letter-spacing:.18em">HOW THE FIRE IS USED</b><br><br>
1. <b style="color:{IVORY}">No guessing.</b> Every idea is tested against 12+ years of real NSE data
with every fee, tax, and slippage counted. Most ideas lose — those are killed permanently.<br><br>
2. <b style="color:{IVORY}">Survivors rehearse first.</b> Weeks of paper trading in the live market
before a single real rupee moves — live results must match the backtest within 30%.<br><br>
3. <b style="color:{IVORY}">Trade by schedule, not mood.</b> Tuesday expiry premium-selling (the
proven earner), rare gap fades, overnight exposure only when fear is measurably high.<br><br>
4. <b style="color:{IVORY}">The watchman overrides everything.</b> War, shock hike, or halt → close and wait.<br><br>
5. <b style="color:{IVORY}">Losses have hard ceilings.</b> −2% a day / −4% a week and the machine stops itself.
</div>""", unsafe_allow_html=True)
        st.markdown('<div class="sec">Operations</div>', unsafe_allow_html=True)
        st.markdown(f"""
<div class="legend" style="background:{SURFACE};border:1px solid {BORDER};
border-radius:8px;padding:14px 18px">
<code>bash scripts/launch.sh</code> — dashboard + watchman, one command<br>
<code>.venv/bin/python scripts/run_paper.py --mode paper</code> — manual session<br>
<code>.venv/bin/python scripts/evolve.py</code> — force a research generation<br>
<code>bash scripts/argus.sh status</code> — watchman health
</div>""", unsafe_allow_html=True)

    st.markdown('<div class="foot"><span class="nm">PROMETHEVS</span> · powered by '
                '<b style="color:{m}">GLM 5.3 Flash</b> · simulated results, full '
                'cost stack · not financial advice</div>'.replace("{m}", MUTED),
                unsafe_allow_html=True)


if __name__ == "__main__":
    main()
