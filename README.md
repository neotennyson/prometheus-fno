# PROMETHEUS

> *Forethought* — the titan who saw tomorrow first.
>
> A self-evolving systematic trading system for Indian index derivatives
> (NSE F&O). Ideas go into the fire. Most burn away in backtests.
> What survives trades — and keeps being tested forever after.

---

## WHAT WE ARE BUILDING

One organism, three organs:

| Unit | Role | What it actually is |
|------|------|---------------------|
| **EMBER** | The stolen fire | Execution engines that trade real strategies during market hours (09:15–15:30 IST) |
| **ANVIL** | Where ideas are hammered | An evolution engine that generates, backtests, scores and kills strategy candidates automatically |
| **ARGUS** | The hundred-eyed watchman | A 24/7 monitor service: news severity, macro parameters, event risk — port 8791 |

Plus a **Command Center** dashboard (Streamlit) over everything, and a permanent
**ledger** of every idea ever tested so nothing is ever fooled over twice.

## WHY WE ARE BUILDING IT

- SEBI's own data: **93% of retail F&O traders lost money** (FY22–24). They lose
  because they trade opinions without evidence, costs without accounting, and
  size without risk limits.
- Prop firms win because of *process*: test everything, kill most of it,
  promote slowly, monitor constantly.
- The goal: **institutional-grade process, run by one person, at ₹0 data cost,
  evolving forever.** Not a signal group. Not a magic bot. A machine for not
  lying to ourselves about edge.

**The goal, stated plainly:** positive expectancy after full Indian cost stack,
drawdown under explicit control, capital deployment growing only as evidence
accumulates — running every trading day without human intervention, with
kill switches when the world goes mad.

## HOW WE ARE BUILDING IT

### The Anvil loop (how strategies are born and die)

```
signal builders × filters × exit rules   →   candidate specs (JSON-addressable)
        ↓
one conservative backtester evaluates ALL specs (~4 s per generation)
        ↓
conviction score ∈ [0,1]  = OOS Sharpe · drawdown · profit factor
                            · quarterly consistency · param-plateau · coverage
        ↓
score ≥ 0.60  →  paper_queue.json     everything else → the graveyard
        ↓
ledger.csv remembers every single spec ever tested (dedupe forever)
```

Strategy ideas come from three pipelines: practitioner canon (Connors RSI(2),
Antonacci momentum, Crabel coils, Donchian), market-structure reasoning (vol
risk premium, flow lead-lags, gap behaviour), and brute enumeration.

### The promotion ladder (how paper money becomes real money)

```
backtest (12y daily / 5y minute data, full Indian cost stack incl. STT/GST/stamp)
  → conviction ≥ 0.60
  → PAPER TRADING 4–6 weeks · tracking error vs model < 30%
  → live, ONE lot · SEBI algo-ID compliant · kill switches armed
  → scale only on proven tracking · decay alarms forever
```

Nothing skips a rung. PF=∞ or a perfect equity curve is treated as a bug until
proven otherwise (this has already caught one fatal bug).

### The Argus watchman (how the system sees)

- 11 RSS sources polled every 2 min: ET Markets, Moneycontrol (latest + economy),
  Business Standard, Livemint, BBC Business, US Fed press, four Google News query
  mirrors (RBI policy / SEBI actions / Nifty-Sensex circuit moves / election-budget)
- Reddit trader communities + StockTwits streams ($NIFTY/$BANKNIFTY/$SENSEX)
- Yahoo Finance macro params every 15 min: SPX, Nasdaq, USDINR, crude, US10Y,
  India VIX → scored 0–100 as the **Global Pulse**
- Severity classifier: level 3 (war/RBI shock/halt) → force-flat; level 2
  (regulatory/tariff) → pause entries; webhook endpoint accepts anything
- Runs as its OWN process on purpose — the trading bot queries it over HTTP;
  if the scraper dies, open positions aren't managed by a dead process

## WHAT IT DOES TODAY (honest scoreboard, Aug 2026)

**Proven & deployed path (Ember core):**

| Engine | Thesis | OOS result (Jan 2024→) |
|--------|--------|------------------------|
| Iron Fly · Tuesday 0DTE | harvest volatility risk premium, defined risk | ⚠️ **REAL-PRICE VERDICT BELOW** — sim Sharpe 3.65 did not survive real quotes |
| Gap reversion | fade ≥0.75-ATR opens | ☠️ **DEAD AND BACKWARDS** on real data: fade = Sharpe −6.5; the *continuation* side survives walk-forward at OOS Sharpe 0.72 |
| Overnight drift | VIX≥17 regime gate | ⚡ survives: walk-forward OOS Sharpe 0.80 (+6.8% over 4.5y, costs incl.); VIX threshold drifts 14–22 by regime |
| Gap-CONT + Overnight portfolio | diversify two small edges | OOS Sharpe 0.77, corr 0.29 — **no Sharpe boost**, deeper DD (−12%), 2026 negative → parked, not promoted |

### Real-price validation (Aug 2026) — the reckoning

5 years of expired-option **minute data from Dhan** (ATM ±4 strikes, ~2.7M rows)
was replayed through a hybrid pricer (real premiums where ticks exist, calibrated
Black-Scholes only for missing wing marks — median error ≈ 6 pts on ~87-pt credits).

| Test | Result |
|------|--------|
| Full-period iron fly (all wings/entries/exits) | **Sharpe −0.55 · ₹−44,939 over 93 trades** — the sim's 3.65 was a vol/path artifact |
| Walk-forward grid, 216 combos × 6 windows × 3 wings | optimized OOS **Sharpe −1.33**, 1/6 windows profitable; params drift, no plateau |
| Entry-filter pass (late entry · IV-richness · chop gate) | cuts bleed **−₹70K → −₹13K**, beats baseline 5/6 windows, MaxDD −16%→−3% — **but still net negative** |

Robust findings inside the wreckage: stops hurt (unanimous "no-stop" across
windows), morning chop is poison (11:35+ entries always win the comparison),
and IV/chop filters genuinely separate good days from bad. What does NOT exist
yet: a configuration with positive after-cost expectancy on real prices.

**Consequence:** the promotion ladder above is load-bearing. Nothing goes near
paper money until it survives *this* engine — `src/backtest/real_engine.py`,
walk-forwarded via `scripts/walk_forward_real.py` and
`scripts/walk_forward_filters.py`.

**Anvil research lab:** 972 specs tested across 16 families. Paper queue holds
19 survivors (vix-shock plateau, gap continuation low-vol, VRP harvest, momentum
variants). The graveyard holds the rest — Donchian, NR7 coils, golden cross
(n=5 noise), pairs stat-arb, US-lag pickup, time-of-day calendar alpha, and more,
each killed with recorded numbers, not opinions.

**Meta-lessons already paid for:** inverted stop levels once faked Sharpe 2.5
(caught by treating PF=∞ as a bug); BankNifty "profits" were regime luck (caught
by cross-instrument validation); the 2024–26 bull run flatters every long-biased
OOS number (why paper trading exists).

## QUICKSTART

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# research: run an evolution generation (adds new specs to the ledger)
.venv/bin/python scripts/evolve.py

# full core backtest report
.venv/bin/python scripts/run_backtest.py

# start the watchman (24/7) and the command center
.venv/bin/python -m src.news_service            # ARGUS, port 8791
.venv/bin/streamlit run src/command_center.py   # dashboard

# paper trading (market hours; needs Dhan API creds in env)
export DHAN_ACCESS_TOKEN="..." DHAN_CLIENT_ID="..."
.venv/bin/python scripts/run_paper.py --mode paper
```

Data refreshers: `scripts/download_history.py` (Yahoo dailies),
`scripts/dhan_backfill.py` (expired option chains),
`scripts/dhan_futures_backfill.py` (Nifty futures daily+minute, for gap/overnight
research — needs Dhan creds). Historical parquets are NOT in this repo —
regenerate them.

## OPERATIONS RUNBOOK

| When | Command | Purpose |
|------|---------|---------|
| Market day |  `scripts/run_paper.py --mode paper` | session loop, kill switches armed |
| Always | `python -m src.news_service` | Argus watches 24/7 on :8791 |
| Weekly cron | `scripts/evolve.py` | next evolution generation |
| Weekly cron | `scripts/download_history.py` (+ `dhan_backfill.py`) | refresh data |
| Monthly | `scripts/run_backtest.py` | re-validate Ember engines |
| Anytime | Streamlit command center | see everything |

## REPO LAYOUT

```
config/settings.yaml      all knobs: capital, costs, risk limits, feeds, engines
src/orchestrator.py       IST session loop: entries, exits, kill switches, EOD flat
src/strategies/           engine logic shared by backtest AND live paths
src/backtest/             engines + metrics + real-price replay engine + conviction-scored intraday tester
src/atlas/                component library, evolution engine, generic spec runner
src/research/             conviction score + bulk sweep harness
src/news_service.py       ARGUS: RSS/social/macro pollers + severity + /pulse API
src/command_center.py     Prometheus dashboard (Streamlit, 7 panels)
scripts/                  CLI entry points for everything above
results/                  reports, ledger.csv, paper_queue.json — the evidence
tests/                    35 tests: engine math, fills, costs, kill switches, APIs
docs/SENTINEL…            architecture essays comparing design philosophies
```

## ROADMAP (in order)

1. Paper phase for the 19 queued specs — 4–6 weeks, tracking-error gate
2. Sensex Thursday weeklies + BankNifty expiry streams → double harvest days
3. Full-chain expired-options backfill → unlock non-0DTE VRP research honestly
4. VPS deployment (systemd units for Ember + Argus), Telegram alerting
5. X/Twitter firehose behind a budget decision (paid tier or self-hosted bridge)
6. Portfolio-level risk overlay across all promoted specs

## RISK & COMPLIANCE (read before going live)

- SEBI retail-algo framework: API orders require your broker's registered
  Algo-ID + static IP whitelisting. `DhanBroker` refuses live orders without it.
- Kill switches are load-bearing: daily −2% / weekly −4% flatten-and-halt.
  Do not raise them to "give it room".
- Past performance ≠ future returns. This repo is research infrastructure,
  not financial advice. Trade your own decisions, at your own size.
