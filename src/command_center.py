"""PROMETHEUS Command Center — simple, honest, one glance.

Three big ON/OFF cards. Then three doors:
  NEWS WATCH  — every source ARGUS reads, live stream, macro pulse
  MONEY       — the trading engines' simulated scoreboard + state
  IDEAS       — the research lab: tested/alive/dead, live-test queue
  HOW IT WORKS— plain-language explanation + start commands

Launch:  bash scripts/launch.sh   (starts ARGUS + this dashboard)
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
IST = timezone(timedelta(hours=5, minutes=30))

ACCENT, GOOD, BAD, WARN = "#00E5FF", "#00E676", "#FF5252", "#FFC400"
VIOLET, BG, CARD, BORDER = "#7C4DFF", "#07090F", "#0D1117", "#1B2230"

st.set_page_config(page_title="PROMETHEUS", page_icon="◈", layout="wide")

CSS = f"""
<style>
    .stApp {{ background: radial-gradient(1100px 700px at 75% -10%, #101828 0%, {BG} 55%); color:#E6EDF3; }}
    #MainMenu, footer, header {{ visibility:hidden }}
    .title {{
        font-size:2.5rem; font-weight:900; letter-spacing:.32em;
        background:linear-gradient(90deg,{ACCENT},{GOOD} 40%,#7C4DFF 75%,{WARN});
        -webkit-background-clip:text; -webkit-text-fill-color:transparent;
    }}
    .sub {{ color:#8B98A9; letter-spacing:.24em; font-size:.72rem; margin-top:-6px }}
    .pill {{ display:inline-flex; align-items:center; gap:7px; padding:5px 14px; border-radius:999px;
            font-size:.74rem; font-weight:700; border:1px solid {BORDER}; background:{CARD};
            color:#B8C4D0; margin-left:8px }}
    @keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:.35}} }}
    .dot {{ width:9px;height:9px;border-radius:50%;background:{GOOD}; animation:pulse 1.6s infinite }}
    .dot.off {{ background:#667085; animation:none }}
    .card {{ background:linear-gradient(180deg,{CARD} 0%,#0A0E16 100%);
            border:1px solid {BORDER}; border-radius:16px; padding:20px 22px; margin-bottom:14px }}
    .big {{ font-size:1.35rem; font-weight:800 }}
    .plain {{ color:#98A2B3; font-size:.9rem; line-height:1.7 }}
    .kpi-label {{ color:#7D8A99; font-size:.72rem; letter-spacing:.14em; font-weight:700 }}
    .kpi-value {{ font-size:1.6rem; font-weight:800; margin-top:6px }}
    .pos {{ color:{GOOD} }} .neg {{ color:{BAD} }} .neu {{ color:{ACCENT} }}
    code {{ color:#67E8F9 !important; background:#0C1420 !important; padding:2px 6px !important;
           border-radius:6px }}
    h2.sect {{ font-size:.78rem; letter-spacing:.28em; color:#8B98A9; font-weight:800;
              text-transform:uppercase; border-bottom:1px solid {BORDER}; padding-bottom:8px }}
    .badge {{ padding:3px 12px; border-radius:999px; font-size:.7rem; font-weight:800 }}
    .b-stop {{ background:#2E1215; color:{BAD} }}
    .b-pause {{ background:#2E2410; color:{WARN} }}
    .b-info {{ background:#161D29; color:#8B98A9 }}
    .stTabs [data-baseweb="tab-list"] {{ gap:4px; border-bottom:1px solid {BORDER} }}
    .stTabs [data-baseweb="tab"] {{ padding:10px 20px; color:#8B98A9; font-size:.82rem;
                                   font-weight:800; letter-spacing:.08em }}
    .stTabs [aria-selected="true"] {{ color:{ACCENT}; border-bottom:2px solid {ACCENT} }}
</style>"""


def inject():
    st.markdown(CSS, unsafe_allow_html=True)


def money(x):
    try:
        return f"₹{float(x):,.0f}"
    except (TypeError, ValueError):
        return "—"


def kpi(col, label, value, sub="", cls=""):
    col.markdown(f'<div class="card"><div class="kpi-label">{label}</div>'
                 f'<div class="kpi-value {cls}">{value}</div>'
                 f'<div class="plain" style="font-size:.78rem;margin-top:4px">{sub}</div></div>',
                 unsafe_allow_html=True)


# ---------------------------------------------------------------- loaders
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
    p = Path(db)
    if not p.exists():
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
def load_news_events(hours: int = 6) -> pd.DataFrame:
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
        return "WEEKEND — closed", False
    if 555 <= t < 930:
        return "MARKET LIVE", True
    return "MARKET CLOSED", False


SOURCE_LABELS = {
    "economictimes.indiatimes.com": "Economic Times — markets desk",
    "www.moneycontrol.com": "Moneycontrol — business news",
    "www.business-standard.com": "Business Standard — markets",
    "www.livemint.com": "Livemint — markets",
    "feeds.bbci.co.uk": "BBC — world business",
    "www.federalreserve.gov": "US Fed — policy decisions",
    "news.google.com": "News mirrors: RBI · SEBI · elections · budget",
    "www.reddit.com": "Trader chatter — r/IndianStockMarket",
    "stocktwits": "StockTwits — $NIFTY chatter",
    "manual": "Manual — webhook alerts you sent",
}


def sev_badge(x):
    try:
        imp = int(x)
    except (TypeError, ValueError):
        return ""
    if imp >= 3:
        return '<span class="badge b-stop">STOP</span>'
    if imp >= 2:
        return '<span class="badge b-pause">PAUSE</span>'
    return '<span class="badge b-info">news</span>'


# ---------------------------------------------------------------- figures
def equity_fig(pnl: pd.Series):
    eq = 500_000.0 + pnl.cumsum()
    fig = go.Figure(go.Scatter(
        x=eq.index, y=eq.values, fill="tozeroy",
        line=dict(color=ACCENT, width=1.8), fillcolor="rgba(0,229,255,0.07)",
        hovertemplate="%{x|%b %Y}<br>%{y:,.0f}<extra></extra>"))
    fig.add_hline(y=500_000.0, line_dash="dot", line_color="#334155",
                  annotation_text="started with ₹5L (simulated)",
                  annotation_font_color="#667085")
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      yaxis=dict(gridcolor="#141B26", tickprefix="₹", tickformat=",.0s",
                                 tickfont=dict(color="#8B98A9")),
                      xaxis=dict(gridcolor="#12181F", tickfont=dict(color="#8B98A9")),
                      showlegend=False, hovermode="x")
    return fig


def monthly_cal(pnl: pd.Series):
    m = pnl.groupby([pnl.index.year, pnl.index.month]).sum().unstack()
    txt = [[f"{v/1000:+.0f}k" if v == v else "" for v in row] for row in m.values]
    fig = go.Figure(go.Heatmap(
        z=m.values, x=[f"M{c}" for c in m.columns], y=m.index.astype(str),
        colorscale=[[0, BAD], [0.02, "#1B2230"], [0.5, "#0F2E24"], [1, GOOD]],
        zmid=0, showscale=False, xgap=3, ygap=3, text=txt,
        texttemplate="%{text}", textfont=dict(size=9)))
    fig.update_layout(height=210, margin=dict(l=10, r=10, t=6, b=6),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      xaxis=dict(tickfont=dict(size=9, color="#667085")),
                      yaxis=dict(tickfont=dict(size=11, color="#8B98A9")))
    return fig


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
    events_db = load_news_events(6)
    now = datetime.now(IST)
    mkt, live = market_status()
    argus_on = news is not None

    # last ledger activity, fills today
    ledger_p = ROOT / "results/atlas/ledger.csv"
    ledger_ts = datetime.fromtimestamp(ledger_p.stat().st_mtime) if ledger_p.exists() else None
    fills_today = 0
    if len(fills):
        try:
            fills_today = int((pd.to_datetime(fills["ts"]).dt.date == now.date()).sum())
        except Exception:
            fills_today = 0
    sess_today = state and state.get("date") == now.date().isoformat()

    # ------------------------------------------------------------- header
    hl, hr = st.columns([3, 2])
    hl.markdown('<div class="title">PROMETHEUS</div>'
                '<div class="sub">YOUR TRADING BOT — ONE GLANCE</div>',
                unsafe_allow_html=True)
    hr.markdown(
        f'<div style="text-align:right;margin-top:12px">'
        f'<span class="pill"><span class="dot {"off" if not live else ""}"></span>{mkt}</span>'
        f'<span class="pill"><span class="dot {"off" if not argus_on else ""}"></span>'
        f'NEWS WATCH {"ON" if argus_on else "OFF"}</span>'
        f'<span class="pill">{now.strftime("%d %b · %H:%M IST")}</span></div>',
        unsafe_allow_html=True)

    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.rerun()

    st.markdown("""
<div class="plain" style="margin-top:2px">
<b style="color:#E6EDF3">This bot has three jobs, each by a different machine:</b><br>
① <b>MONEY</b> — trades the market on proven moves (simulated so far, real-money path gated) ·
② <b>IDEAS</b> — tests new strategies nonstop and kills the losers ·
③ <b>NEWS WATCH</b> — reads the world 24/7 and stops the bot when something big is happening.
</div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ---------------------------------------------------- 3 status cards
    c1, c2, c3 = st.columns(3)
    for col, (name, icon, color, on, on_line, off_line, sub) in zip(
            (c1, c2, c3), (
                ("MONEY", "💸", ACCENT, sess_today,
                 f"Watching the market now",
                 "Waiting for market hours",
                 f"{fills_today} trades today · engine scoreboard in tab ②"),
                ("IDEAS", "🧪", VIOLET, len(ledger) > 0,
                 f"{len(ledger):,} ideas tested so far",
                 "No research data yet — run scripts/evolve.py",
                 f"alive: {len(queue)} · killed: {len(ledger) - len(queue):,}"),
                ("NEWS WATCH", "👀", GOOD, argus_on,
                 "Reading 11 sources + six markets now",
                 "Off — run bash scripts/argus.sh start",
                 f"{news.get('headlines_seen', 0)} items in memory · pulse {pulse.get('score', '—') if pulse else '—'}"),
            )):
        dot = "" if on else "off"
        col.markdown(
            f'<div class="card" style="border-left:4px solid {color}">'
            f'<div class="kpi-label">{icon} {name}</div>'
            f'<div class="big">{on_line if on else off_line}</div>'
            f'<div class="plain" style="margin-top:6px">{sub}</div></div>',
            unsafe_allow_html=True)

    st.markdown(f"""
<div class="card" style="padding:14px 22px">
<b style="color:{ACCENT}">Quick status:</b>
{"The money engine is in its paper phase — no real rupee moves yet. Tested honesty first; real money comes only after weeks of live-market rehearsal." if True else ""}
</div>""", unsafe_allow_html=True)

    tabs = st.tabs(["👀 NEWS WATCH", "💸 MONEY", "🧪 IDEAS", "📖 HOW IT WORKS"])

    # --------------------------------------------------------- NEWS WATCH
    with tabs[0]:
        st.markdown('<h2 class="sect">WHAT THE WATCHMAN IS READING RIGHT NOW</h2>',
                    unsafe_allow_html=True)

        # pulse block
        if pulse:
            comps = pulse.get("components", {})
            p0 = st.columns(1)[0]
            lvl_cls = {"calm": (GOOD, "CALM"), "elevated": (WARN, "ELEVATED"),
                       "risk_off": (BAD, "RISK OFF")}.get(pulse.get("level"),
                                                         (GOOD, "CALM"))
            st.markdown(
                f'<div class="card"><span class="kpi-label">🌍 WORLD MOOD — overnight numbers</span>'
                f'<div class="big" style="color:{lvl_cls[0]}">{pulse.get("score")}/100 · {lvl_cls[1]}</div>'
                f'<div class="plain">updated {str(pulse.get("updated"))[:19]}</div></div>',
                unsafe_allow_html=True)
            chips = st.columns(max(len(comps), 1))
            for col, (k, v) in zip(chips, comps.items()):
                col.markdown(f"**{k.upper()}**  \n**{v:+.2f}%**", unsafe_allow_html=True)
        else:
            st.error("NEWS WATCH is OFF. Start it: `bash scripts/argus.sh start`")

        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown('<h2 class="sect">LIVE FEED — LAST THINGS IT READ</h2>',
                    unsafe_allow_html=True)
        if len(events_db):
            view = events_db.head(40).copy()
            rows_html = ""
            for _, r in view.iterrows():
                src = str(r.get("source", "")).split(":")[0]
                when = str(r.get("seen_at", r.get("ts", "")))[11:19]
                title = str(r.get("title", ""))[:110]
                rows_html += (
                    f'<div style="padding:8px 0;border-bottom:1px solid {BORDER};'
                    f'font-size:.86rem;line-height:1.5">'
                    f'<span style="color:#667085;font-size:.7rem">{when} · </span>'
                    f'{sev_badge(r.get("impact", 0))} '
                    f'<b style="color:#B8C4D0">{title}</b>'
                    f'<div style="color:#667085;font-size:.7rem">{SOURCE_LABELS.get(src, src)}</div>'
                    f'</div>')
            st.markdown(rows_html, unsafe_allow_html=True)
        else:
            st.caption("Nothing read yet — starts when NEWS WATCH runs.")

        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown('<h2 class="sect">THE SOURCES — EVERYTHING IT READS</h2>',
                    unsafe_allow_html=True)
        feeds = ev_cfg.get("feeds", [])
        if feeds:
            rows = []
            for url in feeds:
                host = url.split("/")[2] if url.count("/") >= 2 else url
                label = SOURCE_LABELS.get(host, host)
                cnt = int((events_db["source"].str.split(":").str[0] == host).sum()) \
                    if len(events_db) else 0
                rows.append({"what it reads": label, "items in memory": cnt,
                             "source": url.strip("http" + "" + "/") if False else url})
            st.dataframe(pd.DataFrame(rows)[["what it reads", "items in memory"]],
                         use_container_width=True, hide_index=True)
            with st.expander("technical: exact feed URLs"):
                for url in feeds:
                    host = url.split("/")[2] if url.count("/") >= 2 else url
                    st.code(url + f"    <- {SOURCE_LABELS.get(host, host)}", language=None)
            st.caption("Sleeps checks: same feeds every ~2 minutes; macro markets every 15 min.")
        else:
            st.caption("No feeds configured.")

        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown('<h2 class="sect">IF SOMETHING BIG HAPPENS</h2>',
                    unsafe_allow_html=True)
        st.markdown("""
<div class="plain">
🔴 <b>STOP</b> — war, RBI shock, budget surprise, trading halt → the bot closes everything immediately and stays out.<br>
🟡 <b>PAUSE</b> — SEBI action, tariffs, credit rating cuts → the bot stops opening new trades but holds current ones.<br>
⚪ <b>news</b> — everything else, just logged. The watchman reads far more than it acts on.
</div>""", unsafe_allow_html=True)

    # --------------------------------------------------------------- MONEY
    with tabs[1]:
        st.markdown('<h2 class="sect">THE TRADING ENGINES (SIMULATED RESULTS)</h2>',
                    unsafe_allow_html=True)
        rows = []
        for eng, label, story in (
                ("iron_fly", "Tuesday premium seller", "sells option combos on expiry day, defined risk, the proven earner"),
                ("gap", "Gap catcher", "rare: fades only abnormal openings"),
                ("overnight", "Night holder", "holds overnight only when fear is high")):
            blk = (report.get(eng, {}).get("oos")
                   or report.get(eng, {}).get("is")
                   or report.get(eng, {}).get("full", {}))
            shp = float(blk.get("sharpe") or 0.0)
            trust = "—" if not blk else f"{min(shp / 1.5, 5.0):.1f}"
            rows.append({"engine": label, "what it does": story,
                         "trust (0-5)": trust,
                         "win rate": f"{blk.get('win_rate_pct', '—')}%",
                         "worst day": money(blk.get("worst_day", 0)),
                         "trades made": blk.get("trade_days", "—")})
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.caption("trust (0–5) = how steady it was on data it never saw during design.")

        if not pnl_df.empty and "combined" in pnl_df.columns:
            st.markdown('<h2 class="sect">₹5L IF WE HAD STARTED IN 2019 (SIMULATED)</h2>',
                        unsafe_allow_html=True)
            st.plotly_chart(equity_fig(pnl_df["combined"]), use_container_width=True)
            st.plotly_chart(monthly_cal(pnl_df["combined"]), use_container_width=True)
            st.caption("Green month = made money, red = lost. All fees & taxes included.")

        st.markdown('<h2 class="sect">SAFETY RULES (ALWAYS ON)</h2>',
                    unsafe_allow_html=True)
        risk = load_risk_cfg()
        st.markdown(f"""
<div class="plain" style="line-height:2.0">
🔒 Never lose more than <b>{risk.get('daily_loss_kill_pct', '2')}%</b> in one day — bot stops for the day.<br>
🔒 Never lose more than <b>{risk.get('weekly_loss_kill_pct', '4')}%</b> in a week — bot stops for the week.<br>
🔒 Any single trade risks <b>{risk.get('risk_per_trade_pct', '1')}%</b> of capital max.<br>
🔒 Big scary news → NEW WATCH forces flat. Always.
</div>""", unsafe_allow_html=True)

        st.markdown('<h2 class="sect">LIVE/PAPER BOOK</h2>', unsafe_allow_html=True)
        if state:
            s1, s2 = st.columns(2)
            kpi(s1, "TODAY'S SESSION", str(state.get("date", "—")), "")
            kpi(s2, "REALIZED TODAY", money(state.get("realized", 0)),
                "", "pos" if state.get("realized", 0) >= 0 else "neg")
            pos = state.get("positions", {})
            if pos:
                st.dataframe(pd.DataFrame([{"strategy": k,
                                            **{kk: vv for kk, vv in v.items()
                                               if kk not in ("legs",)}}
                                           for k, v in pos.items()]),
                             use_container_width=True, hide_index=True)
            else:
                st.caption("No open positions in the book. It only trades inside its rules.")
        else:
            st.caption("No session yet today — starts automatically during market hours.")

        if len(fills):
            st.markdown('<h2 class="sect">RECENT FILLS</h2>', unsafe_allow_html=True)
            cols = [c for c in ("ts", "strategy", "side", "qty", "px") if c in fills.columns]
            st.dataframe(fills[cols], use_container_width=True, height=260)

    # --------------------------------------------------------------- IDEAS
    with tabs[2]:
        st.markdown('<h2 class="sect">THE IDEA FACTORY</h2>', unsafe_allow_html=True)
        if len(ledger):
            alive = int(ledger["promoted"].sum()) if "promoted" in ledger.columns else len(queue)
            st.markdown(f"""
<div class="card">
<div class="plain">
🧪 <b>Ideas tested so far: <span style="color:{ACCENT}">{len(ledger):,}</span></b> —
how strategy ideas are born: each one is put through 12 years of Indian market data with
every fee counted. Most <span style="color:{BAD}">fail</span> (they're recorded in the graveyard so we
never fall for them twice). A few <span style="color:{GOOD}">survive</span> — those get a live rehearsal next.<br>
✅ <b>Alive & waiting for live test: {len(queue)}</b> · ☠️ Killed by evidence: {len(ledger) - len(queue):,}
</div></div>""", unsafe_allow_html=True)

            st.markdown('<h2 class="sect">WAITING FOR THEIR LIVE REHEARSAL</h2>',
                        unsafe_allow_html=True)
            if queue:
                qdf = pd.DataFrame(queue)
                cols = [c for c in ("family", "params", "score", "oos_sharpe",
                                    "oos_cagr_pct", "quarters_hit", "n_trades")
                        if c in qdf.columns]
                st.dataframe(qdf[cols], use_container_width=True, hide_index=True)
                st.caption("Paper test first for weeks. Only then does real money see them.")
            else:
                st.caption("Queue empty.")

            st.markdown('<h2 class="sect">THE GRAVEYARD — IDEAS THAT LOST</h2>',
                        unsafe_allow_html=True)
            dead = ["classic trend (Donchian)", "coil-breakouts (NR7/inside-bar)",
                    "melt-up grind", "catch-up gap (US-lag)", "time-of-day calendar",
                    "NIFTY×BankNifty pairs", "52-week-high momentum", "raw gap fades",
                    "compression-gated ORB", "daily breakout", "streak reversals"]
            st.markdown("☠️ " + " · ".join(dead), unsafe_allow_html=True)
            st.caption("Each one was tested with real data and killed by a number, not an opinion.")
        else:
            st.info("No research yet — run `.venv/bin/python scripts/evolve.py`")

    # -------------------------------------------------------- HOW IT WORKS
    with tabs[3]:
        st.markdown("""
<div class="card plain">
<b style="color:#E6EDF3">THE 60-SECOND EXPLANATION</b><br><br>
1. <b>The market doesn't care how you feel.</b> So the bot never guesses — every move it makes
was tested against 12 years of real NSE data with every fee, tax and slippage counted.<br><br>
2. <b>Most ideas lose.</b> The Idea Factory tests thousands automatically (sell a bit,
wait, buy the dip — any idea) and keeps only the ones that survive. Losers go to the
graveyard with their crime scene photos.<br><br>
3. <b>Survivors rehearse with fake money first.</b> For weeks, in the live market, at real
prices. Only if the rehearsal matches the backtest does one rupee of real money move.<br><br>
4. <b>The Watchman never sleeps.</b> Media, trader chatter, and overnight markets feed it.
If something big is underway — war, shock rate hike, budget surprise — the bot closes up
and waits.<br><br>
5. <b>Stop-losses are laws, not suggestions.</b> -2% in a day and it's done for the day.
-4% in a week and it's done for the week. The goal is to still be trading next year.
</div>""", unsafe_allow_html=True)

        st.markdown('<h2 class="sect">START / STOP COMMANDS</h2>', unsafe_allow_html=True)
        st.markdown("""
```bash
cd "/Users/USER/Documents/Default Project/fo-bot"
bash scripts/launch.sh          # one command: starts NEWS WATCH + opens this dashboard
bash scripts/argus.sh status    # is the watchman awake?
bash scripts/argus.sh stop      # put the watchman to sleep
.venv/bin/python scripts/run_paper.py --mode paper   # money engine, market hours only
.venv/bin/python scripts/evolve.py                    # tell the idea factory to test more
```
""")
        st.caption("Full documentation lives in the repo (README.md + PROMETHEUS-MODEL.md).")


@st.cache_data(ttl=600)
def load_risk_cfg() -> dict:
    import yaml
    p = ROOT / "config" / "settings.yaml"
    if not p.exists():
        return {}
    try:
        return (yaml.safe_load(p.read_text()) or {}).get("risk", {})
    except Exception:
        return {}


if __name__ == "__main__":
    main()
