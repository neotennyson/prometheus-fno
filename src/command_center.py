"""PROMETHEUS Command Center — professional trading desk aesthetic.

Design reference: Linear/Stripe surfaces + Bloomberg data density.
One accent, semantic red/green only for money, monospace numerics,
no emoji, no gradients, no glow.
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

# ---- palette (zinc-scale neutrals + one blue accent + semantic money colors)
BG, SURFACE, SURFACE2 = "#0B0C0E", "#141619", "#1A1D21"
BORDER, BORDER2 = "#24272C", "#2E3238"
TEXT, MUTED, FAINT = "#E8EAED", "#9BA1A9", "#5F6670"
ACCENT = "#5B8DEF"
POS, NEG, WARN = "#3DD68C", "#F65A5A", "#E8B04B"

st.set_page_config(page_title="PROMETHEUS", page_icon="●", layout="wide")

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

.stApp {{ background:{BG}; color:{TEXT}; font-family:'Inter',-apple-system,sans-serif; }}
#MainMenu, footer, header {{ visibility:hidden }}
section[data-testid="stMain"] {{ padding-top:.6rem }}

.brand {{ font-weight:700; font-size:1.05rem; letter-spacing:-.01em; color:{TEXT};
         display:flex; align-items:center; gap:10px }}
.brand .mark {{ width:10px; height:10px; background:{ACCENT}; border-radius:3px;
                box-shadow:0 0 0 4px rgba(91,141,239,.12) }}
.brand .env {{ font-family:'JetBrains Mono',monospace; font-size:.62rem; color:{FAINT};
               border:1px solid {BORDER}; border-radius:4px; padding:1px 6px;
               text-transform:uppercase; letter-spacing:.08em }}
.hdr-meta {{ font-family:'JetBrains Mono',monospace; font-size:.7rem; color:{MUTED};
             text-align:right; line-height:1.7 }}

.sec {{ font-size:.68rem; letter-spacing:.1em; text-transform:uppercase; color:{MUTED};
        font-weight:600; margin:1.4rem 0 .55rem 0 }}

.panel {{ background:{SURFACE}; border:1px solid {BORDER}; border-radius:10px;
          padding:16px 18px }}
.panel .lbl {{ font-size:.66rem; letter-spacing:.09em; text-transform:uppercase;
               color:{MUTED}; font-weight:600; margin-bottom:4px }}
.panel .val {{ font-family:'JetBrains Mono',monospace; font-size:1.45rem; font-weight:600;
               letter-spacing:-.02em; font-variant-numeric:tabular-nums }}
.panel .sub {{ font-size:.74rem; color:{FAINT}; margin-top:4px }}
.pos {{ color:{POS} }} .neg {{ color:{NEG} }} .neu {{ color:{TEXT} }}

.strow {{ display:flex; align-items:center; gap:14px; padding:13px 18px;
          background:{SURFACE}; border:1px solid {BORDER}; border-radius:10px }}
.strow + .strow {{ margin-top:8px }}
.strow .nm {{ font-weight:600; font-size:.92rem; width:130px }}
.strow .ds {{ color:{MUTED}; font-size:.8rem; flex:1; line-height:1.5 }}
.strow .st {{ font-family:'JetBrains Mono',monospace; font-size:.68rem; letter-spacing:.1em;
              display:flex; align-items:center; gap:6px }}
.dot {{ width:7px; height:7px; border-radius:50%; display:inline-block }}
.d-on {{ background:{POS} }} .d-off {{ background:{FAINT} }} .d-warn {{ background:{WARN} }}

code {{ color:{ACCENT} !important; background:{SURFACE2} !important;
        padding:1px 6px !important; border-radius:4px !important;
        font-family:'JetBrains Mono',monospace !important; font-size:.82em !important }}

div[data-testid="stDataFrame"] {{ border:1px solid {BORDER}; border-radius:10px; overflow:hidden }}
div[data-testid="stDataFrame"] thead tr th {{
    background:{SURFACE2} !important; color:{MUTED} !important;
    font-size:.7rem !important; letter-spacing:.05em; text-transform:uppercase;
    font-weight:600 !important }}
div[data-testid="stDataFrame"] td {{ font-family:'JetBrains Mono',monospace !important;
                                     font-size:.78rem !important }}

.stTabs [data-baseweb="tab-list"] {{ gap:0; border-bottom:1px solid {BORDER} }}
.stTabs [data-baseweb="tab"] {{ padding:9px 18px; color:{MUTED}; font-size:.8rem;
                               font-weight:500; border-bottom:2px solid transparent }}
.stTabs [aria-selected="true"] {{ color:{TEXT}; border-bottom:2px solid {ACCENT} }}
.stTabs [data-baseweb="tab-highlight"] {{ display:none }}

.feed {{ font-family:'JetBrains Mono',monospace; font-size:.76rem; line-height:1.9 }}
.feed .row {{ padding:5px 0; border-bottom:1px solid #1A1D21; display:flex; gap:10px }}
.feed .tm {{ color:{FAINT}; width:52px; flex-shrink:0 }}
.feed .src {{ color:{MUTED}; width:200px; flex-shrink:0; overflow:hidden;
              text-overflow:ellipsis; white-space:nowrap }}
.feed .hl {{ color:{TEXT}; overflow:hidden; text-overflow:ellipsis; white-space:nowrap }}
.sev {{ width:8px; height:8px; border-radius:2px; margin-top:6px; flex-shrink:0 }}
.s3 {{ background:{NEG} }} .s2 {{ background:{WARN} }} .s1 {{ background:#3A3F46 }}

.legend {{ font-size:.74rem; color:{MUTED}; line-height:1.9 }}
.sw {{ display:inline-block; width:8px; height:8px; border-radius:2px; margin:0 5px 0 12px }}

.foot {{ text-align:center; padding:2.2rem 0 .6rem; color:{FAINT}; font-size:.7rem }}
.foot b {{ color:{MUTED}; letter-spacing:.06em }}
</style>"""


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
        x=eq.index, y=eq.values, line=dict(color=ACCENT, width=1.6),
        hovertemplate="%{x|%b %Y}<br>%{y:,.0f}<extra></extra>"))
    fig.add_hline(y=500_000.0, line_color=BORDER2, line_width=1)
    fig.update_layout(
        height=300, margin=dict(l=52, r=14, t=8, b=28),
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, font=dict(color=MUTED),
        yaxis=dict(gridcolor="#1D2025", tickprefix="₹", tickformat=",.0s",
                   tickfont=dict(family="JetBrains Mono", size=10),
                   zeroline=False),
        xaxis=dict(gridcolor="#15171B", tickfont=dict(family="JetBrains Mono", size=10)),
        showlegend=False, hovermode="x unified")
    return fig


def monthly_cal(pnl: pd.Series):
    m = pnl.groupby([pnl.index.year, pnl.index.month]).sum().unstack()
    txt = [[f"{v/1000:+.0f}k" if v == v else "" for v in row] for row in m.values]
    fig = go.Figure(go.Heatmap(
        z=m.values, x=[f"M{c}" for c in m.columns], y=m.index.astype(str),
        colorscale=[[0, "#7A2B2B"], [0.35, "#3A2224"], [0.49, "#1E2126"],
                    [0.51, "#1B2A24"], [0.75, "#1F5C40"], [1, "#2E8B5F"]],
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
                      yaxis=dict(tickfont=dict(size=11, color=TEXT)))
    return fig


def strow(name, detail, status, dot_cls):
    return (f'<div class="strow"><span class="dot {dot_cls}"></span>'
            f'<span class="nm">{name}</span><span class="ds">{detail}</span>'
            f'<span class="st">{status}</span></div>')


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

    # ---------------------------------------------------------------- header
    hl, hr = st.columns([2, 1])
    with hl:
        st.markdown(f'<div class="brand"><span class="mark"></span>PROMETHEUS'
                    f'<span class="env">paper</span></div>',
                    unsafe_allow_html=True)
    with hr:
        st.markdown(f'<div class="hdr-meta">{mkt} · {mkt_sub}<br>'
                    f'{now.strftime("%a %d %b · %H:%M IST")} · '
                    f'argus {"on" if argus_on else "off"}</div>',
                    unsafe_allow_html=True)

    st.markdown('<div class="sec">System status</div>', unsafe_allow_html=True)
    st.markdown(
        strow("EMBER", f"Execution engines — {fills_today} trades today · "
                       f"{'in session' if sess_today else 'idle until next open'}",
              "RUNNING" if sess_today else "SCHEDULED",
              "d-on" if sess_today else "d-warn")
        + strow("ANVIL", f"Research lab — {len(ledger):,} ideas tested · {len(queue)} in live-test queue",
                "ACTIVE", "d-on")
        + strow("ARGUS", f"24/7 watch — {ev_cfg.get('feeds', []).__len__()} sources · "
                         f"{news.get('headlines_seen', 0) if news else 0} items in memory",
                "ONLINE" if argus_on else "OFFLINE",
                "d-on" if argus_on else "d-off"),
        unsafe_allow_html=True)

    # ------------------------------------------------------------------ KPIs
    st.markdown('<div class="sec">Key numbers</div>', unsafe_allow_html=True)
    comb = report.get("combined", {}).get("full", {})
    oos_comb = report.get("combined", {}).get("oos", {})
    iron_oos = report.get("iron_fly", {}).get("oos", {})
    k1, k2, k3, k4 = st.columns(4)
    panel(k1, "Core engine trust · OOS", f"{iron_oos.get('sharpe', '—')}",
          "iron fly · from Jan 2024", "pos")
    panel(k2, "Book CAGR · OOS", f"{oos_comb.get('cagr_pct', '—')}%",
          "combined engines, recent regime", "pos")
    panel(k3, "Simulated total P&L", money(comb.get("total_pnl", 0)),
          f"{comb.get('years', '—')} years · all costs in", "pos")
    panel(k4, "Ideas tested", f"{len(ledger):,}", f"ledger updated {ledger_ts}", "neu")

    tabs = st.tabs(["NEWS WATCH", "PORTFOLIO", "RESEARCH", "GUIDE"])

    # ------------------------------------------------------------ NEWS WATCH
    with tabs[0]:
        if pulse:
            comps = pulse.get("components", {})
            lvl = pulse.get("level", "calm")
            color = {"calm": POS, "elevated": WARN, "risk_off": NEG}.get(lvl, POS)
            st.markdown(
                f'<div class="panel" style="display:flex;align-items:center;gap:20px">'
                f'<div><div class="lbl">Global pulse · overnight markets</div>'
                f'<div class="val" style="color:{color}">{pulse.get("score")}/100</div></div>'
                f'<div style="display:flex;gap:18px;flex-wrap:wrap;margin-left:auto">'
                + "".join(f'<div><span style="font-size:.62rem;color:{FAINT};'
                          f'text-transform:uppercase;letter-spacing:.08em">{k}</span><br>'
                          f'<span style="font-family:JetBrains Mono,monospace;font-weight:600;'
                          f'color:{NEG if v <= -1 else (TEXT if abs(v) >= .5 else POS)}">{v:+.2f}%</span></div>'
                          for k, v in comps.items())
                + '</div></div>', unsafe_allow_html=True)
        else:
            st.warning("ARGUS is offline — `bash scripts/argus.sh start`")

        st.markdown('<div class="sec">Live feed — latest reads</div>',
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
            st.caption("Journal empty — starts when ARGUS runs.")

        st.markdown('<div class="sec">Sources monitored</div>', unsafe_allow_html=True)
        feeds = ev_cfg.get("feeds", [])
        srows = []
        for url in feeds:
            host = url.split("/")[2] if url.count("/") >= 2 else url
            cnt = int((events_db["source"].str.split(":").str[0] == host).sum()) \
                if len(events_db) else 0
            srows.append({"source": SOURCE_LABELS.get(host, host),
                          "items (6h)": cnt})
        st.dataframe(pd.DataFrame(srows), use_container_width=True, hide_index=True,
                     height=320)

        st.markdown('<div class="sec">Response rules</div>', unsafe_allow_html=True)
        st.markdown(f"""
<div class="legend">
<span class="sw" style="background:{NEG}"></span><b>STOP</b> — war · RBI shock · budget surprise · trading halt → force-flat, stay out<br>
<span class="sw" style="background:{WARN}"></span><b>PAUSE</b> — SEBI action · tariffs · rating cuts → no new entries<br>
<span class="sw" style="background:#3A3F46"></span><b>logged</b> — everything else is journaled, no action
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
            rows.append({"engine": label,
                         "thesis": story,
                         "trust (0–5)": "—" if not blk else f"{min(shp / 1.5, 5.0):.1f}",
                         "win rate": f"{blk.get('win_rate_pct', '—')}%",
                         "worst day": money(blk.get("worst_day", 0)),
                         "trades": blk.get("trade_days", "—")})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption("trust = Sharpe ÷ 1.5 on data the engines never saw during design (Jan 2024 →)")

        if not pnl_df.empty and "combined" in pnl_df.columns:
            st.markdown('<div class="sec">Equity — ₹5L start (simulated, full costs)</div>',
                        unsafe_allow_html=True)
            st.plotly_chart(equity_fig(pnl_df["combined"]), use_container_width=True)
            st.markdown('<div class="sec">Monthly P&L</div>', unsafe_allow_html=True)
            st.plotly_chart(monthly_cal(pnl_df["combined"]), use_container_width=True)

        bars = engine_bars(pnl_df) if not pnl_df.empty else None
        if bars:
            st.markdown('<div class="sec">Per-engine cumulative P&L</div>',
                        unsafe_allow_html=True)
            st.plotly_chart(bars, use_container_width=True)

        st.markdown('<div class="sec">Risk envelope (always armed)</div>',
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
border-radius:10px;padding:12px 18px">
daily loss kill <b style="color:{TEXT}">{risk.get('daily_loss_kill_pct', 2)}%</b>
&nbsp;·&nbsp; weekly loss kill <b style="color:{TEXT}">{risk.get('weekly_loss_kill_pct', 4)}%</b>
&nbsp;·&nbsp; per-trade risk <b style="color:{TEXT}">{risk.get('risk_per_trade_pct', 1)}%</b>
&nbsp;·&nbsp; leverage cap <b style="color:{TEXT}">{risk.get('max_lots_per_10l', 3)} lots / ₹10L</b>
&nbsp;·&nbsp; theta gate VIX <b style="color:{TEXT}">≤ {risk.get('vix_max_for_theta', 22)}</b>
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
<div class="panel" style="display:flex;gap:40px">
<div><div class="lbl">Ideas tested</div><div class="val" style="color:{TEXT}">{len(ledger):,}</div></div>
<div><div class="lbl">Passed (queue)</div><div class="val" style="color:{POS}">{len(queue)}</div></div>
<div><div class="lbl">Killed by evidence</div><div class="val" style="color:{NEG}">{len(ledger)-len(queue):,}</div></div>
<div><div class="lbl">Survival rate</div><div class="val" style="color:{MUTED}">{(len(queue)/max(len(ledger),1)*100):.1f}%</div></div>
</div>""", unsafe_allow_html=True)
        st.caption("Every candidate runs 12+ years of real data with the full Indian cost "
                   "stack. Conviction ≥ 0.60 promotes to paper. Losers are recorded forever.")

        st.markdown('<div class="sec">Live-test queue (paper next)</div>',
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
            st.markdown('<div class="sec">Family leaderboard</div>',
                        unsafe_allow_html=True)
            pick = st.selectbox("family", best["family"].tolist(), label_visibility="collapsed")
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
                                   "n_trades"]], use_container_width=True,
                             hide_index=True)

    # ----------------------------------------------------------------- GUIDE
    with tabs[3]:
        st.markdown(f"""
<div class="panel plain" style="font-size:.86rem;color:{MUTED};line-height:2.0">
<b style="color:{TEXT}">How this bot earns money — the honest version</b><br><br>
1. <b>No guessing.</b> Every trade idea is tested against 12+ years of real NSE data
with every fee, tax, and slippage counted. Most ideas lose. Those are killed
permanently and recorded.<br><br>
2. <b>Survivors rehearse first.</b> Strategies that pass are paper-traded in the
live market for weeks before a single real rupee moves — and the live result must
match the backtest within 30%.<br><br>
3. <b>The engines trade by schedule, not mood.</b> Tuesday expiry premium-selling
(the proven earner), rare gap fades, overnight exposure only when fear is
measurably high.<br><br>
4. <b>The watchman overrides everything.</b> If something big is underway — war,
shock rate hike, halt — the bot closes and waits. Reading far more than it acts
on is the point.<br><br>
5. <b>Losses have hard ceilings.</b> −2% in a day / −4% in a week and the machine
stops itself. The edge only works if the account survives to use it.
</div>""", unsafe_allow_html=True)

        st.markdown('<div class="sec">Operations</div>', unsafe_allow_html=True)
        st.markdown(f"""
<div class="legend" style="background:{SURFACE};border:1px solid {BORDER};
border-radius:10px;padding:14px 18px">
<code>bash scripts/launch.sh</code> — dashboard + watchman, one command<br>
<code>.venv/bin/python scripts/run_paper.py --mode paper</code> — manual session (normally launchd handles this)<br>
<code>.venv/bin/python scripts/evolve.py</code> — force a research generation<br>
<code>bash scripts/argus.sh status</code> — watchman health
</div>""", unsafe_allow_html=True)

    st.markdown('<div class="foot"><b>PROMETHEUS</b> · powered by GLM 5.3 Flash · '
                'simulated results, full cost stack · not financial advice</div>',
                unsafe_allow_html=True)


if __name__ == "__main__":
    main()
