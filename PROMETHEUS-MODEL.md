# PROMETHEUS — Complete Model & System Documentation

> **Purpose of this file:** read this first, in full. It describes every part of
> this repository: what the system is, why it exists, how each piece works, the
> data contracts between pieces, the scoring math, the promotion ladder, the
> operation commands, and every known limitation. If you are an AI model or a
> new maintainer, this file plus `README.md` (quickstart) and `config/settings.yaml`
> (all knobs) should let you work on the system without reverse-engineering it.
> Everything here was true at last edit; timestamps are noted where relevant.

---

## 0. TL;DR

PROMETHEUS is a self-evolving systematic trading system for Indian index
derivatives (NSE F&O). It has three units:

- **EMBER** — the execution engines that trade (iron-fly VRP on Tuesday expiries,
  gap reversion, overnight drift). Tracked by `src/orchestrator.py`.
- **ANVIL** — the research lab. An evolution engine generates candidate
  strategies from reusable components, backtests them on ~12.6 years of real
  data with the full Indian cost stack, scores them with a "conviction score",
  and promotes survivors to a paper-trading queue while recording every loser
  permanently in a ledger. Tracked by `src/atlas/*`, `scripts/evolve.py`.
- **ARGUS** — the 24/7 watchman. Polls 13 RSS/social/macro sources every 2 min
  (macro every 15 min), classifies headline severity, exposes an HTTP API on
  port 8791 (`/status`, `/pulse`, `/events`, `/webhook`). Tracked by
  `src/news_service.py`.

Plus a Streamlit command center (`src/command_center.py`) that reads everything.

The design philosophy, in one line: **test everything, kill most of it with
evidence, promote slowly, monitor forever** — because SEBI's own data shows 93%
of retail F&O traders lose money, and prop firms win via process, not signals.

---

## 1. The market & data layer

### 1.1 What it trades
NSE index derivatives. Primary instrument: NIFTY, lot size 65 (config
`universe.lot_size`). Universe modeled for index futures and index options
(`NSE_FNO` segment, underlying security id 13 = NIFTY in Dhan's numbering).

### 1.2 Data sources
1. **Yahoo dailies** (fetched by `scripts/download_history.py` → `data/raw/*.parquet`):
   NIFTY `^NSEI`, Sensex `^BSESN`, BankNifty `^NSEBANK`, India VIX `^INDIAVIX`,
   ~2014 → today. Index values, NOT tradable directly — used as signal inputs.
2. **Dhan option-chain history** (`scripts/dhan_backfill.py`, needs broker creds →
   `data/dhan/nifty_opt_*.parquet`): 1-minute bars for ATM call/put and wing
   strikes (ATM±100/150/200 pts) from ~Aug 2021, plus a per-minute underlying
   `spot` column. **This is the only source of real intraday option premiums in
   the repo.** Expiry-day engine uses these directly.
3. **Global proxies** (via yfinance in `src/atlas/data_global.py`, cached to
   `data/raw/global_*.parquet`): SPX, Nasdaq, USDINR, crude, US10Y.

**Important limitation (known & documented):** the Dhan option parquets rotate
strikes each minute around the ATM. A multi-day (non-0DTE) options position
loses real quotes the moment spot moves away from the recorded strikes, so
non-0DTE option backtests are NOT honest with this data. The 0DTE Tuesday
iron-fly engine avoids this by definition (position strikes == that day's panel
strikes). Do not re-open the non-0DTE VRP experiment without per-strike full
chain history.

### 1.3 Data conventions
- Daily parquet indexes carry a **09:15 time component** in `src/atlas` usage but
  are midnight in `data/raw/banknifty_daily.parquet` etc. **Every join between
  daily frames must `.normalize()` both indexes** — a past bug silently zeroed a
  full sweep because of this; the pattern is now enforced by tests.
- Session math: market 09:15–15:30 IST; square-off default 15:18.
- The app clock is pinned to `Asia/Kolkata` (`src/orchestrator.py::ist_now`)
  so a UTC VPS cannot shift entry windows. Dashboard uses IST too.

---

## 2. EMBER — execution engines

### 2.1 Files
- `src/orchestrator.py` — session loop driver: reads config, calls broker,
  one `run_once()` per poll tick (20 s). Gate order per tick:
  1. kill switch (daily −2% ⇒ flat; also weekly via `RiskManager`)
  2. ARGUS consult (`/_news_service_status`, cached 30 s, falls back to local
     `EventRisk` when service down)
  3. gap-reversion window 09:18–09:30
  4. iron-fly entry window (10:05–10:15) on expiry Tuesdays
  5. overnight-drift entry 15:22–15:29 (needs ≥₹20L equity + VIX ≥ 17)
  6. 15:18 square-off (force flat + `flat_for_day` flag)
- `src/strategies/*` — per-engine logic; shared by backtest & live.
- `src/risk.py` — `RiskManager`: daily/weekly circuit breakers, risk-budget sizing.
- `src/exec/paper_broker.py` — simulated fills (slippage + full cost stack),
  SQLite journal `logs/trinity_trades.db` (tables: `fills`, `events`).
- `src/exec/dhan_broker.py` — live adapter. **Refuses to send orders unless
  `DHAN_ALGO_ID` is set** (SEBI retail-algo framework) and `dry_run=False`.

### 2.2 Engines & their verified results (run on real data, OOS = 2024→today)
| Engine | Thesis | OOS numbers |
|---|---|---|
| iron_fly_expiry (`src/strategies/iron_fly_expiry.py`) | sell ATM straddle + buy ±wing strikes (= iron fly) on 0DTE Tuesday expiry, entry 10:05, exit stop 2×credit if keep% target 40% hit at 15:18. Credits from REAL option premiums (BS only fills strike gaps with VIX-mapped IV + calibrated skew). | Sharpe 3.65, win 79%, CAGR 43% (Jan'24→) |
| gap_reversion | fade |gap| ≥ 0.75 ATR opens toward prior close; stop 1 ATR | rare, ≈breakeven-positive |
| overnight_drift | long futures 15:22→09:20 when VIX ≥ 17 | weak OOS (Sharpe 0.39) — watchlist |

Killed & kept dead (documented so nobody re-tests for nostalgia): hourly trend
with EMA/ADX, mid-size gap fades, always-on overnight carry.

### 2.3 Cost stack (must never be understated)
`src/costs.py::CostModel` — brokerage min 20₹/order, STT (options sell 0.15% of
premium — post-Apr-2026 rate, futures sell 0.05%), NSE exchange charges, SEBI fee,
stamp duty, GST 18% on all charges, IPFT, and slippage (options 0.2% of premium
min 0.75₹; futures 1.5 pts/side). Any new strategy MUST use this model.
Rates live in `config/settings.yaml::costs` — re-verify against current rules
before any live deployment.

---

## 3. ANVIL — the evolution engine (the part that never stops researching)

### 3.1 Concept
Strategies are not hand-coded individually anymore; they are **specs** — data:

    spec = (signal_builder, signal_params) × (filter, filter_params) × exit_rule

**Signal builders** (`src/atlas/components.py`) — 25 variants across 18 families:
breakout(N), coil-break, gap continuation/fade, streak reversal, vix_shock,
ma_cross (incl. golden cross 50/200), close-strength momentum, Antonacci
absolute momentum, Connors RSI(2) dip, turn-of-month, 52-week-high momentum,
US-night lag pickup, BankNifty→NIFTY lead-lag, VRP directional harvest
(VIX−RV20 quintile), calm-bounce, range expansion, low-vol melt-up grind.
Each returns `(long_mask, short_mask)` arrays over the daily bars. **Causality
rule: mask[i] uses information ≤ i; entry executes at open[i+1].**

**Filters** — none / vix_low (<15) / vix_high (>18). Gate both masks.

**Exit rules** — hold 1/3/5/10 sessions (close), signal-off (exit when mask
flips), ATR stop+target (2 combos), ATR chandelier trail (2 widths).
Conservative details: on a day where both stop and target are touchable, the
STOP fills first (never assume the favorable fill).

### 3.2 The generic backtester
`src/atlas/engine_daily.py::run_spec` — one position at a time; signal on
close[i] → fill at open[i+1]; exits evaluated session by session; costs charged
as one futures round trip on (entry+exit)/2 notional. Output: trade dicts in the
repo convention `{date, dir, entry, exit, pts, lots, cost, pnl, reason}`.

### 3.3 Scoring — the Conviction Score
`src/research/conviction.py::conviction_score(stats, plateau, quarters_hit)`:

    components (weights): Sharpe .28 · drawdown .16 · profit factor .14
                          · quarterly consistency .12 · param plateau .12
                          · day coverage .18
    each component clipped to [0,1]; tanh-normalized Sharpe; then
    reliability multiplier sqrt(n_trades / 30) — Sharpe from 4 trades must
    NEVER outrank one from 200 (this killed a real 627% CAGR hallucination).

Threshold to promote: **≥ 0.60** (`PROMOTE_THRESHOLD`). Plateau = share of the
same family's combos with positive full-period P&L (averaged in evolve).
IS/OOS split: IS < 2024-01-01, OOS ≥ 2024-01-01 (house line — equals the
"recent regime" used in Ember reports; 2024–26 was a bull run, which flatters
long-biased OOS numbers — the paper phase exists because of this).

### 3.4 The loop
`scripts/evolve.py` → `src/atlas/evolve.py::run_generation`:
1. Enumerate all specs from the component library (currently 18 families ×
   variants × 3 filters × 9 exits ≈ 1,170 spec slots).
2. Deduplicate via `results/atlas/ledger.csv` (SHA1 of spec JSON — non-security
   hash; `usedforsecurity=False`).
3. Evaluate, build daily PnL series, cut IS/OOS, score.
4. Apply family-plateau multiplier, mark `promoted` ≥ 0.60, append to ledger.
5. Write `results/atlas/paper_queue.json` (survivors).
Generation of ~500 new specs ⇒ ~4 s. Ledger is append-only — the system's memory.

### 3.5 Standalone experiments (not in the generic loop)
- `src/atlas/pairs_bn.py` — NIFTY×BankNifty log-spread mean reversion with
  two-leg costs. Killed by evidence (parameter sign-flips). Code kept for reference.
- `scripts/atlas_refine2.py` — NR7 close-confirm cross-instrument test (killed)
  and the 1,380-window time-of-day scan (nothing significant).
- `scripts/experiment_daily_vrp.py` — non-0DTE VRP harvest test (killed by the
  strike-rotation data limitation above, NOT by losses).

### 3.6 Sweep-1 module (older parallel harness)
`src/research/sweep.py` — intraday futures sweeps on the 1-min panel (ORB,
VWAP-fade, trend-day). All negative after costs; evidence recorded in
`results/research/`. The intraday panel/loader lives in `src/data/intraday.py`
(builds 1-min NIFTY spot from the Dhan parquets' `spot` column).

---

## 4. ARGUS — the watchman (port 8791)

### 4.1 Process model
Runs standalone: `python -m src.news_service` (or `bash scripts/argus.sh start`).
Independent process by design — the trading loop only queries it over HTTP;
if the scraper dies, the trading bot is not managed by a dead process.

### 4.2 Pollers (in `src/news_service.py`)
- `Poller` (every 2 min): RSS via feedparser with browser UA — 13 feeds:
  ET Markets, Moneycontrol (latest + economy), Business Standard, Livemint,
  BBC Business, US Fed press releases, 4 Google News query mirrors (RBI policy,
  SEBI actions, Nifty/Sensex circuit moves, election/budget), 2 Reddit feeds
  (r/IndianStockMarket, r/IndiaInvestments). Plus StockTwits JSON streams
  ($NIFTY/$BANKNIFTY/$SENSEX) — best-effort, blocked from datacenter IPs (403),
  silently skips.
- `MacroPoller` (every 15 min): yfinance closes for SPX, Nasdaq, USDINR, crude,
  US10Y, India VIX → last-session % change → `pulse_score` (0–100):
  SPX ≤ −0.8% ⇒ 30 · Nasdaq ≤ −1.2% ⇒ 20 · crude ≥ 2.5% ⇒ 15 ·
  USDINR ≥ 0.4% ⇒ 15 · VIX ≥ 10% ⇒ 20 → labels calm / elevated / risk_off.

### 4.3 Severity model
Keyword classifier `classify(title, summary)`:
- level 3 (STOP, force-flat): "repo rate", "rbi policy", "monetary policy",
  "union budget", "election result", "exit poll", "declares war", "air strike",
  "missile", "military action", "emergency imposed", "circuit breaker",
  "trading halt", "sanctions on india"
- level 2 (PAUSE, block entries): "sebi bans", "imposes tariff",
  "credit rating cut", "downgrade", etc.
- Calendar: `config/events.yaml` with `flat` (force flat) / `no_entry` dates.

### 4.4 The critical ignore-list (REGRESSION THAT ALREADY BURNED US)
The Google-News query mirrors and chatter sources inherently contain trigger
words in nearly every title (e.g. "RBI..." appears constantly in the RBI feed).
**Both the local EventRisk (`src/events.py`, `ignored_sources` config) and
ARGUS (`IGNORE_ALERT_SOURCES` in news_service.py: google news, reddit,
stocktwits) exclude these from block decisions.** Without this, the bot
permanently paused itself — observed live in a paper session and fixed. If you
add a source, decide explicitly: informational-only or alert-arming. Do NOT
add a trigger-word query feed as an arming source.

### 4.5 API
- `GET /status` — `{impact_level, block_entries, force_flat, reason,
  recent_alerts, feeds_monitored, headlines_seen}`
- `GET /pulse` — score, level, components (percent changes), updated
- `GET /events?hours=N&min_impact=M` — journal (in-memory deque, 400 items)
- `POST /webhook` — `{title, summary?, source?}`; optional auth header
  `x-argus-secret` if `events.webhook_secret` set. Journal: `logs/news.db`
  (table `events`: ts, source, title, impact, keyword, seen_at).

### 4.6 Orchestrator integration
`Orchestrator._news_service_status()` GETs `/status?minutes=45` with 2 s timeout,
cached 30 s; failure ⇒ falls back to local `EventRisk` poller (in-process,
market-hours only, keyword list minus the ignored sources).

---

## 5. Scoring & reporting conventions (shared vocabulary)

- `perf_stats(daily_pnl, capital)` (`src/backtest/metrics.py`): avg daily return
  vs capital, annualized Sharpe/Sortino, max drawdown %, profit factor, win rate,
  best/worst day. All P&L in rupees after full costs.
- Trade dict convention everywhere: `{date(datetime.date str), dir(±1), entry,
  exit, pts, lots, cost, pnl, reason}`.
- `quarter_consistency(pnl)` — fraction of calendar quarters positive.
- Reports: `src/backtest/metrics.py::save_report` → `results/*.json`.
  The main Ember report is `results/backtest_report.json` (keys: iron_fly /
  gap / overnight / combined, each with full/is/oos sections + monthly_combined).
  Daily engine PnL: `results/daily_pnl.csv` (columns: date + engine + combined).

---

## 6. Command center (dashboard)

`src/command_center.py` — Streamlit app, `bash scripts/launch.sh` starts it after
ARGUS. Reads ONLY repo-produced files + localhost:8791 (no network calls itself).
Four tabs (NEWS WATCH / MONEY / IDEAS / HOW IT WORKS), designed for a non-quant
reader: three status cards (MONEY / IDEAS / NEWS WATCH) with ON/OFF, live feed
stream from `/events`, source list from settings + live counts, engine trust
scores, equity curve, safety rules, idea-factory stats, plain-language
explanations. `🔄 Refresh` clears st.cache_data. No writes anywhere.

---

## 7. Operations (the repetition that keeps it honest)

| When | Command | Purpose |
|---|---|---|
| market days 09:15–15:30 | `.venv/bin/python scripts/run_paper.py --mode paper` | EMBER session loop; `--once` = single tick (connection check), `--synthetic` = no creds |
| always | `bash scripts/argus.sh start\|stop\|status` | ARGUS lifecycle (pidfile `logs/argus.pid`) |
| weekly (Sun) | `.venv/bin/python scripts/evolve.py` | next ANVIL generation |
| weekly | `.venv/bin/python scripts/download_history.py` (+ `dhan_backfill.py` with creds) | data refresh |
| monthly | `.venv/bin/python scripts/run_backtest.py` | re-validate Ember engines |
| any time | `bash scripts/launch.sh` | dashboard + ARGUS in one command |
| tests | `.venv/bin/python -m pytest tests/ -q` | 39 tests: engine math, fills, costs, kill switch, event gating, conviction, news service, timezone pin, short avg-price, flat_position |

`ops/crontab.example` contains the scheduled versions (Linux/VPS).

---

## 8. Environment variables & secrets

- `DHAN_ACCESS_TOKEN`, `DHAN_CLIENT_ID` — required for real data/quotes/orders.
  Read from env ONLY (never hardcoded, never committed; token visible in shell
  history is your risk).
- `DHAN_ALGO_ID` — required before any LIVE order; `DhanBroker` raises otherwise.
- Secrets hygiene: `.gitignore` excludes `.venv/`, `data/`, `logs/*.db`,
  `logs/*.log`, `logs/*.json`, `.env`. Git history was scanned for JWTs — clean.
- The `logs/trinity_session_state.json` file is the live session book (positions,
  flags, realized P&L); it is gitignored (untracked) and local-only.

---

## 9. Risk model

`config/settings.yaml::risk`:
- `risk_per_trade_pct: 1.0` — max capital risk per trade
- `max_lots_per_10l: 3` — leverage cap
- `daily_loss_kill_pct: 2.0`, `weekly_loss_kill_pct: 4.0` — circuit breakers
- `vix_max_for_theta: 22.0` — iron-fly only trades below this
- Sizing helpers: `src/risk.py::lots_for_max_loss` (budget // per-lot risk,
  capped), `src/backtest/intraday_engine.py::size_lots` (2%-per-lot guardrail,
  floors at 1 lot, `fixed_lots` research override — research only!).
Known limitation: the daily loss counter resets if the session restarts
mid-day (day-start equity re-sampled). Acceptable in paper; persist before live.

---

## 10. Known limitations & honest caveats (read before live)

1. Iron-fly backtest uses BS pricing only for strike gaps (real premiums when
   strikes match). Real expiry-day quotes deviate slightly; validated against
   real Dhan parquets where available.
2. Intraday expiry-day path approximated from minute bars with conservative
   stop-first ordering.
3. Non-0DTE option backtesting is not honest with current data (strike rotation).
4. OOS window (2024–26) was a strong bull run; long-biased strategies look
   better there than their IS. The conviction score + reliability penalty + paper
   tracking-error gate are the countermeasures; honor them.
5. SEBI retail-algo framework: live orders require broker-registered Algo-ID,
   static IP whitelisting, OAuth flow. Dhan on-ramping must be completed first.
6. Kill switches are load-bearing. Do not loosen them "to give the bot room".
7. ARGUS stops whenever the machine sleeps. Unattended 24/7 ops need the VPS
   deployment (roadmap item 4).

---

## 11. Roadmap (in priority order)

1. Paper phase for the current 10 queued specs (tracking error < 30% gate).
2. Expand expiry coverage: Sensex Thursday weeklies + BankNifty (rights/scrip
   migration work in `scripts/dhan_backfill.py`).
3. Full-chain expired-options history → unblock honest non-0DTE research.
4. VPS deployment with systemd units (ember.service, argus.service) + Telegram
   alerting.
5. X/Twitter firehose (budget decision) — free alternatives (Reddit, StockTwits
   best-effort, Google News mirrors) are already wired.
6. Portfolio-level risk overlay across all promoted engines (correlation-aware
   sizing), persistence of day-start equity across restarts.

---

## 12. Repo layout (condensed map)

```
config/settings.yaml      ALL knobs (capital, costs, risk, strategies, events, feeds)
config/events.yaml        calendar events (flat / no_entry dates)
src/orchestrator.py       EMBER session driver + kill switch + ARGUS consult
src/strategies/           iron_fly / gap_reversion / overnight / trend_hourly(dead)
src/risk.py               circuit breakers + sizing
src/costs.py              full Indian charge stack
src/models/black_scholes.py  BS pricer (vectorized)
src/backtest/engine.py, metrics.py, real_engine.py, intraday_engine.py
src/data/calendar.py      NSE holidays + weekly expiry schedule
src/data/ingest_yf.py     Yahoo dailies loader
src/data/dhan_client.py   Dhan v2 REST client (creds from env)
src/data/intraday.py      1-min spot panel from Dhan parquets
src/exec/paper_broker.py  simulated fills + journal
src/exec/dhan_broker.py   live adapter (algo-ID gated)
src/events.py             in-process fallback EventRisk (keywords + calendar)
src/news_service.py       ARGUS: pollers + severity + FastAPI :8791
src/command_center.py     dashboard
src/atlas/*               components.py (signals/filters/exits), engine_daily.py,
                          evolve.py (ledger loop), sweep.py, pairs_bn.py,
                          features.py, cond_orb.py, data_global.py
src/research/*            conviction.py (the gate), sweep.py (intraday sweeps)
scripts/                  run/backtest/paper/evolve/launch/argus + experiments
results/                  backtest_report.json, daily_pnl.csv,
                          atlas/ledger.csv (1080+ rows), paper_queue.json,
                          atlas_report.csv, tod_scan.csv, research/*
ops/crontab.example       scheduled operations
tests/                    39 tests (engine, fills, costs, risk, events, news,
                          conviction, evolution, intraday strategies, timezone)
```

---

## 13. Code conventions (respect these)

- House style: generators return trade dicts with the convention of §5; everyday
  fills simulated next-open or bar-close, stop-first ambiguity rule everywhere.
- No comments unless they explain WHY (risk/behavior), per repo convention.
- New strategy ideas: add a signal builder to `src/atlas/components.py` +
  params, run `scripts/evolve.py`. The ledger decides. Do not hand-tune a spec
  into the paper queue; the gate is the gate.
- All money math in rupees with `round(x,2)` at trade-record boundaries so the
  journal ties out exactly.

---

## 14. Full automation (added Sep 2026)

The system runs itself via macOS launchd agents (`ops/install-automation.sh`):

| Agent | Schedule | Job |
|---|---|---|
| `com.prometheus.argus` | always (KeepAlive) | ARGUS watchman, auto-restart |
| `com.prometheus.tunnel` | always (KeepAlive) | Cloudflare tunnel for remote dashboard |
| `com.prometheus.session.N` | Mon–Fri 09:05 | EMBER paper session (skips weekends + NSE holidays via `scripts/session.sh`) |
| `com.prometheus.data.N` | Mon–Fri 15:40 | Yahoo dailies refresh |
| `com.prometheus.evolve` | Sun 10:00 | ANVIL evolution generation |

- **Secrets:** the single local file `.env` (gitignored) holds `DHAN_ACCESS_TOKEN`,
  `DHAN_CLIENT_ID`, `DHAN_ALGO_ID`, optional Groww slots, `PROMETHEUS_DASH_CODE`.
  `src/config.py::load_dotenv` loads it at import (env vars win if already set).
- **Remote dashboard:** `scripts/tunnel.sh start` runs a Cloudflare quick tunnel
  to Streamlit :8501; public URL written to `logs/tunnel_url.txt`. The dashboard
  requires the `PROMETHEUS_DASH_CODE` access code when exposed. Quick-tunnel URLs
  rotate on restart; a named tunnel (custom domain) is the upgrade path.
- **Token reality:** Dhan access tokens are generated in the Dhan web console and
  expire (24h–30d by plan); TOTP does not automate Dhan token issuance — a human
  refreshes `DHAN_ACCESS_TOKEN` in `.env` when it lapses. Groww's API supports
  TOTP-based programmatic token generation (slots reserved in `.env`; adapter
  build pending — see roadmap item 7).
- **Manual overrides:** everything in `scripts/` still runs standalone; launchd
  only automates the schedule. Uninstall: `launchctl unload ~/Library/LaunchAgents/com.prometheus.*.plist`.

---

## 15. Token self-service API (source of truth, added Sep 2026)

Broker tokens are served by the operator's private token API:

    Base:  https://tokens.example.invalid
    Auth:  X-API-Key: <TOKEN_API_KEY from .env>
    GET   /token/dhan | /token/groww | /token/all -> {"token","expiry"}
    POST  /refresh  -> force regeneration (only on 401/403/token-expired)

Module: `src/brokers/tokens.py` (stdlib only).
`get_token(broker)` is THE point-of-use getter: 6 h cache in
`~/.broker_tokens.json` (chmod 600), legacy manual-token fallback when
`TOKEN_API_KEY` is unset. `refresh_and_get(broker)` = POST /refresh → wait 5 s
→ refetch. `DhanClient._request_with_auth_recovery` retries once on 401/403
using it. Daily 08:45 launchd agent warms the cache before the 09:05 session.
Legacy TOTP minting flows (`groww_auth`, token_renew --legacy) remain as
fallback only. Tokens/API key are never printed, logged, or committed;
.env is gitignored and untracked.
