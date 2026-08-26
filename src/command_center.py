"""PROMETHEUS Command Center — umbrella dashboard over the whole organism.

    EMBER   execution engines  (proven money-makers, real-money path)
    ANVIL   research lab       (evolution engine, ledger, paper queue)
    ARGUS   24/7 monitors      (headline severity + Global Pulse, port 8791)
            (internal packages: src/orchestrator.py, src/atlas/, src/news_service.py)

Launch:  .venv/bin/streamlit run src/command_center.py
Data:    repo-produced files only + localhost:8791 when ARGUS runs.
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
    .prometheus-title {{
        font-size:2.6rem; font-weight:900; letter-spacing:.34em;
        background:linear-gradient(90deg,{ACCENT},{GOOD} 40%,#7C4DFF 75%,{WARN});
        -webkit-background-clip:text; -webkit-text-fill-color:transparent;
    }}
    .sub {{ color:#8B98A9; letter-spacing:.26em; font-size:.72rem; margin-top:-6px }}
    .pill {{ display:inline-flex; align-items:center; gap:7px; padding:5px 14px; border-radius:999px;
            font-size:.74rem; font-weight:700; border:1px solid {BORDER}; background:{CARD};
            color:#B8C4D0; margin-left:8px }}
    @keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:.35}} }}
    .dot {{ width:9px;height:9px;border-radius:50%;background:{GOOD}; animation:pulse 1.6s infinite }}
    .dot.off {{ background:#667085; animation:none }}
    .dot.warn {{ background:{WARN}; animation:none }}
    .card {{ background:linear-gradient(180deg,{CARD} 0%,#0A0E16 100%);
            border:1px solid {BORDER}; border-radius:16px; padding:18px 20px }}
    .kpi-label {{ color:#7D8A99; font-size:.72rem; letter-spacing:.14em; font-weight:700 }}
    .kpi-value {{ font-size:1.65rem; font-weight:800; margin-top:6px }}
    .pos {{ color:{GOOD} }} .neg {{ color:{BAD} }} .neu {{ color:{ACCENT} }}
    code {{ color:#67E8F9 !important; background:#0C1420 !important; padding:2px 6px !important;
           border-radius:6px }}
    h2.sect {{ font-size:.78rem; letter-spacing:.28em; color:#8B98A9; font-weight:800;
              text-transform:uppercase; border-bottom:1px solid {BORDER}; padding-bottom:8px }}
    .unit {{ border-left:3px solid {ACCENT}; padding:10px 16px; background:{CARD};
            border-radius:0 12px 12px 0; margin-bottom:8px }}
    .verdict {{ padding:2px 10px; border-radius:999px; font-size:.68rem; font-weight:800 }}
    .v-live {{ background:#0F2E24; color:{GOOD} }} .v-paper {{ background:#0C2A36; color:{ACCENT} }}
    .v-dead {{ background:#2E1215; color:{BAD} }} .v-watch {{ background:#2E2410; color:{WARN} }}
    .stTabs [data-baseweb="tab-list"] {{ gap:4px; border-bottom:1px solid {BORDER} }}
    .stTabs [data-baseweb="tab"] {{ padding:10px 18px; color:#8B98A9; font-size:.8rem;
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
                 f'<div style="color:#667085;font-size:.78rem;margin-top:4px">{sub}</div></div>',
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
def load_fills(days: int = 7) -> pd.DataFrame:
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
def load_news_events(hours: int = 24) -> pd.DataFrame:
    db = ROOT / "logs" / "news.db"
    if not db.exists():
        return pd.DataFrame()
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        df = pd.read_sql("SELECT ts, source, title, impact, keyword, seen_at "
                         "FROM events ORDER BY seen_at DESC LIMIT 2000", con)
        con.close()
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=45)
def fetch_argus(endpoint: str):
    import httpx
    try:
        r = httpx.get(f"http://127.0.0.1:8791/{endpoint}", timeout=1.5)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def market_status():
    now = datetime.now(IST)
    t = now.hour * 60 + now.minute
    if now.weekday() >= 5:
        return "WEEKEND · CLOSED", False
    if 555 <= t < 930:
        return "MARKET LIVE", True
    return "MARKET CLOSED", False


VERDICTS = {
    "vix_shock": ("PAPER QUEUE", "v-paper", "OOS Sharpe ~1.0 stable across 5 exits"),
    "gap": ("PAPER QUEUE", "v-paper", "gap-cont low-vol · small n"),
    "abs_momentum": ("PAPER QUEUE", "v-paper", "126d momentum · small n"),
    "vrp_harvest": ("PAPER QUEUE", "v-paper", "VIX-RV spread · structural idea"),
    "range_expansion": ("PAPER QUEUE", "v-paper", "coil-breakout · reliability-gated"),
    "ma_cross": ("FLAGGED NOISE", "v-watch", "golden-cross n=5 — ignore till n>30"),
    "turn_of_month": ("WATCHLIST", "v-watch", "positive, score below bar"),
    "rsi2_dip": ("WATCHLIST", "v-watch", "Connors RSI(2) · regime-dependent"),
    "bn_lead": ("WATCHLIST", "v-watch", "unstable IS/OOS"),
    "close_str": ("GRAVEYARD", "v-dead", ""),
    "hi52": ("GRAVEYARD", "v-dead", ""),
    "calm_bounce": ("GRAVEYARD", "v-dead", ""),
    "streak": ("GRAVEYARD", "v-dead", ""),
    "breakout": ("GRAVEYARD", "v-dead", ""),
    "us_lag": ("GRAVEYARD", "v-dead", "catch-up trade not real after costs"),
    "coil_break": ("GRAVEYARD", "v-dead", ""),
    "lowvol_grind": ("GRAVEYARD", "v-dead", "melt-up grind: OOS bull-run flattery"),
    "donchian": ("GRAVEYARD", "v-dead", "classic turtle — dead on NIFTY daily"),
    "nr7_bracket": ("GRAVEYARD", "v-dead", "v1 weak · v2 killed cross-instrument"),
    "global_night_fade": ("GRAVEYARD", "v-dead", "inverted-level artifact unmasked it"),
    "cond_orb": ("GRAVEYARD", "v-dead", "compression filter did not save it"),
    "vwap_fade": ("GRAVEYARD", "v-dead", "sweep-1 kill"),
    "orb_intraday": ("GRAVEYARD", "v-dead", "sweep-1 kill"),
    "trend_day": ("GRAVEYARD", "v-dead", "sweep-1 kill"),
}


# ------------------------------------------------------------------ figures
def equity_fig(pnl: pd.Series, capital: float):
    eq = capital + pnl.cumsum()
    fig = go.Figure(go.Scatter(
        x=eq.index, y=eq.values, fill="tozeroy",
        line=dict(color=ACCENT, width=1.8), fillcolor="rgba(0,229,255,0.07)",
        hovertemplate="%{x|%b %Y}<br>%{y:,.0f}<extra></extra>"))
    fig.add_hline(y=capital, line_dash="dot", line_color="#334155")
    fig.update_layout(height=330, margin=dict(l=10, r=10, t=10, b=10),
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
    fig.update_layout(height=230, margin=dict(l=10, r=10, t=6, b=6),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      xaxis=dict(tickfont=dict(size=9, color="#667085")),
                      yaxis=dict(tickfont=dict(size=11, color="#8B98A9")))
    return fig


def engine_cum_bars(pnl_df: pd.DataFrame):
    cols = [c for c in ("iron_fly", "gap", "overnight") if c in pnl_df.columns]
    if not cols:
        return None
    cum = pnl_df[cols].cumsum().iloc[-1].sort_values(ascending=False)
    names = {"iron_fly": "Iron Fly · 0DTE VRP", "gap": "Gap Reversion",
             "overnight": "Overnight Drift"}
    fig = go.Figure(go.Bar(
        y=[names.get(k, k) for k in cum.index], x=cum.values, orientation="h",
        marker=dict(color=[ACCENT, WARN, VIOLET[:7]][:len(cum)]), width=0.5,
        text=[money(v) for v in cum.values], textposition="outside",
        textfont=dict(color="#B8C4D0")))
    fig.update_layout(height=220, margin=dict(l=10, r=90, t=6, b=6),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      xaxis=dict(visible=False),
                      yaxis=dict(tickfont=dict(size=12, color="#E6EDF3")))
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
    news = fetch_argus("status")
    pulse = fetch_argus("pulse")
    events_db = load_news_events(24)
    now = datetime.now(IST)
    mkt, live = market_status()
    argus_on = news is not None

    # --------------------------------------------------------------- header
    hl, hr = st.columns([3, 2])
    hl.markdown('<div class="prometheus-title">PROMETHEUS</div>'
                '<div class="sub">FORETHOUGHT · FIRE · FORTUNE</div>',
                unsafe_allow_html=True)
    hr.markdown(
        f'<div style="text-align:right;margin-top:12px">'
        f'<span class="pill"><span class="dot {"off" if not live else ""}"></span>{mkt}</span>'
        f'<span class="pill"><span class="dot {"off" if not argus_on else ""}"></span>ARGUS '
        f'{"ONLINE" if argus_on else "OFFLINE"}</span>'
        f'<span class="pill">{now.strftime("%d %b · %H:%M IST")}</span></div>',
        unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)

    # --------------------------------------------------- unit connection row
    ledger_mtime = (ROOT / "results/atlas/ledger.csv")
    anvil_last = datetime.fromtimestamp(ledger_mtime.stat().st_mtime).strftime("%d %b %H:%M") \
        if ledger_mtime.exists() else "—"
    fills_today = 0
    if len(fills):
        try:
            fdates = pd.to_datetime(fills["ts"]).dt.date
            fills_today = int((fdates == now.date()).sum())
        except Exception:
            fills_today = 0
    ember_on = bool(state) and state.get("date") == now.date().isoformat()
    anvil_on = len(ledger) > 0

    u1, u2, u3 = st.columns(3)
    for col, (name, color, online, detail) in zip(
            (u1, u2, u3), (
                ("EMBER", ACCENT, ember_on,
                 f"{'session live' if ember_on else 'no session today'} · "
                 f"{fills_today} fills today · mode paper"),
                ("ANVIL", VIOLET, anvil_on,
                 f"{len(ledger):,} specs · queue {len(queue)} · updated {anvil_last}"),
                ("ARGUS", GOOD, argus_on,
                 f"{'watching' if argus_on else 'OFFLINE — start news_service'} · "
                 f"{news.get('feeds_monitored','—')} feeds" if news else "port 8791 closed"))):
        dot = "" if online else "off"
        col.markdown(f'<div class="unit" style="border-left-color:{color}">'
                     f'<b style="letter-spacing:.2em">{name}</b> '
                     f'<span class="pill" style="margin-left:6px">'
                     f'<span class="dot {dot}"></span>{"ONLINE" if online else "IDLE"}</span>'
                     f'<div style="color:#98A2B3;font-size:.8rem;margin-top:4px">{detail}</div>'
                     f'</div>', unsafe_allow_html=True)

    # ------------------------------------------------------------- top KPIs
    oos = report.get("combined", {}).get("oos", {})
    iron = report.get("iron_fly", {}).get("oos", {})
    pulse_comps = (pulse or {}).get("components", {})
    spx = pulse_comps.get("spx")
    k1, k2, k3, k4, k5 = st.columns(5)
    kpi(k1, "CORE ENGINE OOS SHARPE", f"{iron.get('sharpe', '—')}",
        "iron fly · Jan 2024 → today", "pos")
    kpi(k2, "CORE OOS CAGR", f"{oos.get('cagr_pct', '—')}%",
        "combined book · recent regime", "pos")
    kpi(k3, "SPECS TESTED", f"{len(ledger):,}", "every idea ever tried", "neu")
    kpi(k4, "PAPER QUEUE", str(len(queue)), "conviction ≥ 0.60", "neu")
    kpi(k5, "PULSE LEVEL",
        f"{pulse['score']} · {pulse['level'].upper()}" if pulse else "—",
        (f"SPX {spx:+.2f}% overnight" if spx is not None
         else ("live" if pulse else "start ARGUS")),
        "neg" if pulse and pulse.get("score", 0) >= 50 else "neu")

    if st.button("🔄 Refresh all data"):
        st.cache_data.clear()
        st.rerun()

    tabs = st.tabs(["OVERVIEW", "EMBER ENGINES", "ANVIL LAB", "PAPER QUEUE",
                    "ARGUS LIVE", "GRAVEYARD", "OPS RUNBOOK"])

    # -------------------------------------------------------------- OVERVIEW
    with tabs[0]:
        st.markdown('<h2 class="sect">HOW ₹5L WOULD HAVE GROWN (SIMULATION)</h2>',
                    unsafe_allow_html=True)
        if not pnl_df.empty and "combined" in pnl_df.columns:
            st.plotly_chart(equity_fig(pnl_df["combined"], 500_000),
                            use_container_width=True)
            st.plotly_chart(monthly_cal(pnl_df["combined"]), use_container_width=True)
        else:
            st.info("Run scripts/run_backtest.py to populate results/daily_pnl.csv")
        st.caption("Full Indian cost stack applied · past performance ≠ future returns")

    # ---------------------------------------------------------- EMBER ENGINES
    with tabs[1]:
        st.markdown('<h2 class="sect">ENGINE SCOREBOARD · OUT-OF-SAMPLE</h2>',
                    unsafe_allow_html=True)
        rows = []
        for eng, label in (("iron_fly", "Iron Fly · Tuesday 0DTE VRP"),
                           ("gap", "Gap Reversion"),
                           ("overnight", "Overnight Drift")):
            blk = (report.get(eng, {}).get("oos")
                   or report.get(eng, {}).get("is")
                   or report.get(eng, {}).get("full", {}))
            rows.append({"engine": label, "sharpe": blk.get("sharpe"),
                         "cagr_%": blk.get("cagr_pct"), "max_dd_%": blk.get("max_dd_pct"),
                         "win_%": blk.get("win_rate_pct"),
                         "trade_days": blk.get("trade_days")})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        c1, c2 = st.columns(2)
        with c1:
            st.markdown('<h2 class="sect">PER-ENGINE CUMULATIVE P&L</h2>',
                        unsafe_allow_html=True)
            bars = engine_cum_bars(pnl_df) if not pnl_df.empty else None
            if bars:
                st.plotly_chart(bars, use_container_width=True)
            else:
                st.caption("run_backtest output missing")
        with c2:
            st.markdown('<h2 class="sect">RISK LIMITS (LIVE CONFIG)</h2>',
                        unsafe_allow_html=True)
            risk = load_yaml_risk()
            st.markdown(f"""<div class="card" style="line-height:2.0;font-size:.88rem">
              daily kill: <b>{risk.get('daily_loss_kill_pct','—')}%</b> ·
              weekly kill: <b>{risk.get('weekly_loss_kill_pct','—')}%</b><br>
              risk/trade: <b>{risk.get('risk_per_trade_pct','—')}%</b> ·
              max lots/₹10L: <b>{risk.get('max_lots_per_10l','—')}</b><br>
              VIX gate for theta: <b>≤ {risk.get('vix_max_for_theta','—')}</b></div>""",
                        unsafe_allow_html=True)

        st.markdown('<h2 class="sect">SESSION STATE (LIVE/PAPER BOOK)</h2>',
                    unsafe_allow_html=True)
        if state:
            s1, s2, s3 = st.columns(3)
            kpi(s1, "SESSION DATE", str(state.get("date", "—")), "")
            kpi(s2, "REALIZED TODAY", money(state.get("realized", 0)),
                "", "pos" if state.get("realized", 0) >= 0 else "neg")
            flags = state.get("flags", {})
            kpi(s3, "FLAGS", ", ".join(flags) if flags else "clean", "")
            positions = state.get("positions", {})
            if positions:
                st.dataframe(pd.DataFrame(
                    [{"strategy": k, **(v if isinstance(v, dict) else {"detail": v})}
                     for k, v in positions.items()]),
                    use_container_width=True, hide_index=True)
            else:
                st.caption("No open positions in session book.")
        else:
            st.caption("No session yet today — start scripts/run_paper.py during market hours.")

        st.markdown('<h2 class="sect">PAPER FILLS (LAST 7 DAYS)</h2>',
                    unsafe_allow_html=True)
        if len(fills):
            show = [c for c in ("ts", "strategy", "symbol", "side", "qty",
                                "px", "slip", "cash_after") if c in fills.columns]
            st.dataframe(fills[show], use_container_width=True, height=300)
        else:
            st.caption("No fills yet — the engines only enter under their gates.")

    # -------------------------------------------------------------- ANVIL LAB
    with tabs[2]:
        st.markdown('<h2 class="sect">FAMILY LEADERBOARD (BEST SPEC PER FAMILY)</h2>',
                    unsafe_allow_html=True)
        if len(ledger):
            best = ledger.sort_values("score", ascending=False).groupby("family").head(1)
            best = best.sort_values("score", ascending=False).copy()
            show = best[["family", "params", "filter", "exit", "score", "full_sharpe",
                         "oos_sharpe", "oos_cagr_pct", "n_trades", "quarters_hit"]].copy()
            show["status"] = show["family"].map(
                lambda f: VERDICTS.get(f, ("TESTED", "", ""))[0])
            st.dataframe(show, use_container_width=True, hide_index=True, height=420)

            sc = px.scatter(ledger.dropna(subset=["oos_sharpe"]),
                            x="oos_sharpe", y="score", color="family",
                            hover_data=["params", "exit"], height=340)
            sc.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                             font_color="#8B98A9",
                             xaxis=dict(gridcolor="#141B26", title="OOS Sharpe"),
                             yaxis=dict(gridcolor="#141B26", title="Conviction"))
            sc.add_vline(x=0, line_color="#334155", line_dash="dot")
            sc.add_hline(y=0.60, line_color=WARN, line_dash="dot",
                         annotation_text="promotion bar 0.60")
            st.plotly_chart(sc, use_container_width=True)

            st.markdown('<h2 class="sect">FAMILY DRILL-DOWN</h2>', unsafe_allow_html=True)
            fam_order = best["family"].tolist()
            pick = st.selectbox("family", fam_order,
                                format_func=lambda f: f"{f} · {VERDICTS.get(f, ('—',))[0]}")
            drill = ledger[ledger["family"] == pick].sort_values("score", ascending=False)
            dcols = [c for c in ("params", "filter", "exit", "score", "full_sharpe",
                                 "is_sharpe", "oos_sharpe", "oos_cagr_pct",
                                 "oos_max_dd_pct", "oos_pf", "n_trades",
                                 "quarters_hit", "promoted") if c in drill.columns]
            st.dataframe(drill[dcols], use_container_width=True, hide_index=True)

            st.markdown('<h2 class="sect">WATCHLIST (0.45 ≤ SCORE < 0.60)</h2>',
                        unsafe_allow_html=True)
            wl = best[(best["score"] >= 0.45) & (best["score"] < 0.60)]
            if len(wl):
                st.dataframe(wl[["family", "params", "exit", "score", "oos_sharpe",
                                 "n_trades"]], use_container_width=True, hide_index=True)
            else:
                st.caption("Nothing on the bubble right now.")

            with st.expander("EXPERIMENT ARCHIVE · non-promotable research"):
                vr = load_json("results/research/daily_vrp_report.json")
                if vr:
                    st.caption("Daily VRP harvest on non-0DTE contracts — killed by "
                               "data limitation (strike rotation), not by loss:")
                    st.dataframe(pd.DataFrame(vr).T, use_container_width=True)
                tod = load_csv("results/atlas/tod_scan.csv")
                if len(tod):
                    st.caption(f"Time-of-day scan: {len(tod)} windows, "
                               f"0 reached |t|≥3 — calendar alpha absent.")
        else:
            st.info("Ledger empty — run scripts/evolve.py")

    # ------------------------------------------------------------ PAPER QUEUE
    with tabs[3]:
        st.markdown('<h2 class="sect">CONVOY TO PAPER (FORWARD TEST NEXT)</h2>',
                    unsafe_allow_html=True)
        if queue:
            qdf = pd.DataFrame(queue)
            cols = [c for c in ("family", "strategy", "params", "filter", "exit", "score",
                                "oos_sharpe", "oos_cagr_pct", "n_trades",
                                "quarters_hit") if c in qdf.columns]
            st.dataframe(qdf[cols], use_container_width=True, hide_index=True)
            st.warning("Candidates, NOT approved strategies. Paper first; tracking "
                       "error <30% vs model required before any live rupee.")
        else:
            st.info("Queue empty.")

    # -------------------------------------------------------------- ARGUS LIVE
    with tabs[4]:
        st.markdown('<h2 class="sect">GLOBAL PULSE · OVERNIGHT MACRO</h2>',
                    unsafe_allow_html=True)
        if pulse:
            comps = pulse.get("components", {})
            cc = st.columns(max(len(comps), 1))
            for col, (k, v) in zip(cc, comps.items()):
                kpi(col, k.upper(), f"{v:+.2f}%", "",
                    "neg" if v <= -1 else ("neu" if abs(v) >= 0.5 else "pos"))
            lvl_cls = {"calm": "v-live", "elevated": "v-watch"}.get(
                pulse.get("level"), "v-dead")
            st.markdown(f'<span class="pill"><span class="verdict {lvl_cls}">'
                        f'{pulse.get("level","?").upper()} · {pulse.get("score","?")}/100'
                        f'</span> <span class="pill">updated '
                        f'{str(pulse.get("updated"))[:19]}</span></div>',
                        unsafe_allow_html=True)
        else:
            st.error("ARGUS offline — start it: `.venv/bin/python -m src.news_service`")

        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown('<h2 class="sect">HEADLINE SEVERITY · DECISION STATE</h2>',
                    unsafe_allow_html=True)
        if news:
            a1, a2, a3, a4 = st.columns(4)
            kpi(a1, "IMPACT LEVEL", str(news.get("impact_level", 0)), "",
                "neg" if news.get("impact_level", 0) >= 3 else "neu")
            kpi(a2, "BLOCK ENTRIES", str(news.get("block_entries")), "")
            kpi(a3, "FORCE FLAT", str(news.get("force_flat")),
                str(news.get("reason") or ""),
                "neg" if news.get("force_flat") else "pos")
            kpi(a4, "FEEDS MONITORED", str(news.get("feeds_monitored", "—")),
                f"{news.get('headlines_seen', 0)} headlines in memory")
            alerts = news.get("recent_alerts") or []
            if alerts:
                st.dataframe(pd.DataFrame(alerts)[["seen_at", "source", "impact", "title"]],
                             use_container_width=True)
        elif argus_on:
            st.caption("Port answers but status failed — check logs/argus.log")

        st.markdown('<h2 class="sect">EVENT JOURNAL (NEWS.DB, LAST 24H)</h2>',
                    unsafe_allow_html=True)
        if len(events_db):
            srcs = events_db["source"].str.split(":").str[0]
            pick_src = st.multiselect("filter sources", sorted(srcs.unique()))
            view = events_db[srcs.isin(pick_src)] if pick_src else events_db
            dist = srcs.value_counts().reset_index()
            dist.columns = ["source", "items"]
            fig = px.bar(dist.head(12), x="items", y="source", orientation="h",
                         height=240)
            fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                              showlegend=False, font_color="#8B98A9",
                              xaxis=dict(visible=False),
                              yaxis=dict(tickfont=dict(color="#8B98A9", size=10)))
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(view.head(200), use_container_width=True, height=300)
        else:
            st.caption("No journal yet — starts when ARGUS first runs.")

    # -------------------------------------------------------------- GRAVEYARD
    with tabs[5]:
        st.markdown('<h2 class="sect">LEDGER OF TRUTH</h2>', unsafe_allow_html=True)
        dead = [f for f in VERDICTS if VERDICTS[f][0] == "GRAVEYARD"]
        st.markdown("**Executed kills:** " + " · ".join(
            f'<span class="pill"><span class="verdict v-dead">{f}</span></span>'
            for f in dead), unsafe_allow_html=True)
        st.caption("Plus pre-repo kills: hourly trend, mid-gap fade, always-on carry.")
        if len(ledger):
            worst = ledger.nsmallest(8, "score")[
                ["family", "params", "exit", "score", "oos_sharpe", "n_trades"]]
            st.markdown('<h2 class="sect">BOTTOM OF THE BARREL</h2>',
                        unsafe_allow_html=True)
            st.dataframe(worst, use_container_width=True, hide_index=True)

    # -------------------------------------------------------------- OPS RUNBOOK
    with tabs[6]:
        st.markdown('<h2 class="sect">DAILY OPERATIONS</h2>', unsafe_allow_html=True)
        st.markdown(f"""
<div class="card" style="font-size:.9rem;line-height:2.1">
<b>Every trading day</b><br>
&nbsp;<code>.venv/bin/python scripts/run_paper.py --mode paper</code> — session loop (09:15–15:30 IST)<br>
&nbsp;<code>.venv/bin/python -m src.news_service</code> — ARGUS watches 24/7 on :8791<br>
<b>Weekly (cron Sunday)</b><br>
&nbsp;<code>.venv/bin/python scripts/evolve.py</code> — next evolution generation<br>
&nbsp;<code>.venv/bin/python scripts/download_history.py && .venv/bin/python scripts/dhan_backfill.py</code> — refresh data<br>
<b>Monthly</b><br>
&nbsp;<code>.venv/bin/python scripts/run_backtest.py</code> — re-validate EMBER engines<br>
<b>This dashboard</b><br>
&nbsp;<code>.venv/bin/streamlit run src/command_center.py</code>
</div>""", unsafe_allow_html=True)
        ladder = [
            ("BACKTEST", "done", "full cost stack · IS/OOS split · plateau + reliability gates"),
            ("CONVICTION ≥ 0.60", "done" if queue else "next", f"{len(queue)} specs queued"),
            ("PAPER 4–6 WEEKS", "now", "tracking error < 30% gate — no shortcuts"),
            ("LIVE 1 LOT", "next", "SEBI algo-ID · kill switches armed"),
            ("SCALE", "next", "+size only on proven tracking"),
        ]
        for title, state_, desc in ladder:
            icon = {"done": "✓", "now": "▶", "next": "○"}[state_]
            color = {"done": GOOD, "now": ACCENT, "next": "#667085"}[state_]
            st.markdown(f'<div style="display:flex;gap:12px;padding:10px 16px;'
                        f'border:1px solid {BORDER};border-radius:12px;background:{CARD};'
                        f'margin-bottom:8px"><b style="color:{color}">{icon}</b>'
                        f'<div><b>{title}</b> <span style="color:#98A2B3;font-size:.85rem">'
                        f'{desc}</span></div></div>', unsafe_allow_html=True)


@st.cache_data(ttl=600)
def load_yaml_risk() -> dict:
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
