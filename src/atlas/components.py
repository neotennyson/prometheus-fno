"""Component library for the evolution engine.

Signals, filters and aux arrays are plain numpy so thousands of candidate specs
can be evaluated per minute. Everything is causal: an array value at index i uses
information from <= i only. Entries execute at NEXT session's open (handled by
the engine, never here).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def build_aux(daily: pd.DataFrame, vix: pd.Series | None = None,
              spx_on: pd.Series | None = None,
              banknifty: pd.DataFrame | None = None) -> dict[str, np.ndarray]:
    o = daily["open"].to_numpy(dtype=float, copy=True)
    h = daily["high"].to_numpy(dtype=float, copy=True)
    l = daily["low"].to_numpy(dtype=float, copy=True)
    c = daily["close"].to_numpy(dtype=float, copy=True)
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))))
    tr[0] = h[0] - l[0]
    atr = pd.Series(tr).ewm(alpha=1 / 14, adjust=False).mean().to_numpy()
    ret1 = pd.Series(c).pct_change().to_numpy()
    prev_c = np.roll(c, 1)
    gap = np.where(prev_c > 0, o / prev_c - 1.0, 0.0)

    aux: dict[str, np.ndarray] = {
        "o": o, "h": h, "l": l, "c": c, "atr": atr, "ret1": ret1, "gap": gap,
        "dates": daily.index,
    }

    # rolling extremes / stats for several lookbacks
    cs = pd.Series(c)
    hs, ls = pd.Series(h), pd.Series(l)
    for n in (10, 20, 40, 60):
        aux[f"hh_{n}"] = hs.rolling(n).max().shift(1).to_numpy()
        aux[f"ll_{n}"] = ls.rolling(n).min().shift(1).to_numpy()
        aux[f"rp_{n}"] = ((hs - ls) / pd.Series(atr)).rolling(60).rank(pct=True).to_numpy()

    rng = pd.Series((h - l) / atr)
    aux["nr7"] = (rng == rng.rolling(7).min()).to_numpy()
    aux["inside"] = ((hs < hs.shift(1)) & (ls > ls.shift(1))).to_numpy()

    # consecutive down / up closes
    dn = np.zeros(len(c), dtype=int)
    up = np.zeros(len(c), dtype=int)
    for i in range(1, len(c)):
        dn[i] = dn[i - 1] + 1 if c[i] < c[i - 1] else 0
        up[i] = up[i - 1] + 1 if c[i] > c[i - 1] else 0
    aux["dn_streak"], aux["up_streak"] = dn, up

    for f, s in ((10, 30), (20, 50), (50, 200)):
        fma = cs.rolling(f).mean()
        sma = cs.rolling(s).mean()
        aux[f"macross_{f}_{s}"] = (fma > sma).to_numpy()
        aux[f"macross_prev_{f}_{s}"] = (fma > sma).shift(1).fillna(False).to_numpy()

    aux["cpct_60"] = cs.rolling(60).rank(pct=True).to_numpy()
    aux["cpct_252"] = cs.rolling(252).rank(pct=True).to_numpy()

    # Cutler RSI(2) for Connors-style dips
    delta = cs.diff()
    up_m = delta.clip(lower=0).rolling(2).mean()
    dn_m = (-delta.clip(upper=0)).rolling(2).mean()
    rs = up_m / dn_m.replace(0, np.nan)
    aux["rsi2"] = (100 - 100 / (1 + rs)).fillna(50).to_numpy()
    aux["above_ma200"] = (cs.rolling(200).mean() < cs).to_numpy()

    # turn-of-month flags: salary/SIP inflow cycle
    dom = pd.Series(daily.index.day)
    last_dom = pd.Series(daily.index).apply(
        lambda ts: pd.Timestamp(ts).days_in_month).to_numpy()
    is_cd = np.arange(len(c))
    month_key = daily.index.year * 12 + daily.index.month
    rank_from_end = np.array([last_dom[i] - daily.index.day[i] for i in range(len(c))])
    new_month_ahead = np.r_[month_key[1:] != month_key[:-1], True]
    prev_new_month = np.r_[True, month_key[1:] != month_key[:-1]]
    sess_in_month = np.zeros(len(c), dtype=int)
    for i in range(len(c)):
        sess_in_month[i] = 0 if prev_new_month[i] else sess_in_month[i - 1] + 1
    sess_to_month_end = np.zeros(len(c), dtype=int)
    for i in range(len(c) - 1, -1, -1):
        sess_to_month_end[i] = 0 if new_month_ahead[i] else sess_to_month_end[i + 1] + 1
    aux["tom"] = ((sess_to_month_end <= 3) | (sess_in_month <= 2))
    aux["is_first_week"] = sess_in_month <= 5

    if vix is not None:
        va = vix.reindex(daily.index).ffill()
        aux["vix"] = va.to_numpy(dtype=float)
        chg = va.pct_change()
        aux["vix_rank"] = chg.rolling(252, min_periods=60).rank(pct=True).to_numpy()
        # variance-risk-premium proxy: annualized RV20 (pct) vs VIX level
        rv20 = pd.Series(ret1).rolling(20).std() * np.sqrt(252) * 100
        vrp = va.to_numpy(dtype=float) - rv20.to_numpy()
        vr = pd.Series(vrp)
        aux["vrp_rank"] = vr.rolling(252, min_periods=60).rank(pct=True).to_numpy()
    if spx_on is not None:
        aux["spx_on"] = spx_on.reindex(daily.index).to_numpy(dtype=float)
    if banknifty is not None:
        bn_close = banknifty["close"].copy()
        bn_close.index = pd.to_datetime(bn_close.index).normalize()   # dailies mix 09:15 / midnight stamps
        nf_ret = pd.Series(c).pct_change()
        bn_ret = bn_close.pct_change().reindex(daily.index.normalize()).to_numpy()
        aux["bn_excess"] = pd.Series(bn_ret - nf_ret.to_numpy()).shift(1).to_numpy()
    return aux


# ------------------------------------------------------------------ signals
# Each builder returns (long_mask, short_mask) evaluated on bar i; the engine
# enters at open[i+1]. Mirrored families produce both masks from one spec.
def sig_breakout(aux, p):
    n = int(p["n"])
    long_m = aux["c"] > aux[f"hh_{n}"]
    short_m = aux["c"] < aux[f"ll_{n}"]
    return long_m, short_m


def sig_coil_break(aux, p):
    buf = float(p["buf"]) * aux["atr"]
    coil = aux["nr7"] | aux["inside"]
    # prior session's band; today's close confirms beyond it -> enter next open
    prev_h = np.roll(aux["h"], 1)
    prev_l = np.roll(aux["l"], 1)
    prev_h[0] = np.nan
    prev_l[0] = np.nan
    long_m = coil & (aux["c"] > prev_h + buf)
    short_m = coil & (aux["c"] < prev_l - buf)
    return long_m, short_m


def sig_gap(aux, p):
    thr = float(p["thr"]) * aux["atr"] / aux["c"]
    cont = p.get("mode", "cont") == "cont"
    up = aux["gap"] >= thr
    dn = aux["gap"] <= -thr
    if cont:
        return up, dn                            # ride the gap direction
    return dn, up                                # fade it


def sig_streak(aux, p):
    k = int(p["k"])
    return aux["dn_streak"] >= k, aux["up_streak"] >= k


def sig_vix_shock(aux, p):
    q, rmin = float(p["q"]), float(p["rmin"])
    cond = (aux.get("vix_rank", np.zeros(len(aux["c"]))) >= q) & \
           (aux["ret1"] <= rmin)
    return cond, np.zeros(len(aux["c"]), dtype=bool)


def sig_ma_cross(aux, p):
    key = f"macross_{p['fast']}_{p['slow']}"
    prev = f"macross_prev_{p['fast']}_{p['slow']}"
    golden = aux[key] & ~aux[prev].astype(bool)
    dead = ~aux[key].astype(bool) & aux[prev]
    return golden, dead


def sig_close_strength(aux, p):
    hi = float(p["pct_hi"])
    lo = float(p["pct_lo"])
    cp = aux["cpct_60"]
    return cp >= hi, cp <= lo


# ---- classic brand-name entries -------------------------------------------
def sig_abs_momentum(aux, p):
    """Antonacci dual momentum, absolute leg only (12-1): long if index above
    its 12m-ago level and 12m return positive; else flat."""
    look = int(p.get("look", 252))
    skip = int(p.get("skip", 21))
    c = aux["c"]
    past = np.full(len(c), np.nan)
    if len(c) > look + skip:
        past[look + skip:] = c[:len(c) - look - skip]
    mom = c / past - 1.0
    long_m = (mom > float(p.get("thr", 0.0))) & ~np.isnan(mom)
    return long_m, np.zeros(len(c), dtype=bool)


def sig_rsi2_dip(aux, p):
    """Connors RSI(2) pullback buy -- only above the 200d MA (his regime rule)."""
    lo = float(p.get("rsi_lo", 10))
    ok = aux["above_ma200"] & (aux["rsi2"] < lo)
    short_ok = (~aux["above_ma200"]) & (aux["rsi2"] > 100 - lo)
    return ok, short_ok


def sig_turn_of_month(aux, p):
    m = aux["tom"].copy()
    return m, np.zeros(len(m), dtype=bool)


def sig_hi52(aux, p):
    thr = float(p.get("thr", 0.95))
    return aux["cpct_252"] >= thr, aux["cpct_252"] <= (1.0 - thr)


# ---- my own structural ideas ------------------------------------------------
def sig_us_lag(aux, p):
    """US-night momentum transfer: SPX moved big overnight but NIFTY's open gap
    badly lagged it -> India plays catch-up intraday. Trade in SPX direction.
    Structural read: NIFTY opens before US futures finish pricing; laggard
    opens get marked up/down by late arbitrage."""
    min_spx = float(p.get("min_spx_ret", 0.0075))
    lag_k = float(p.get("lag_k", 0.35))
    so = aux.get("spx_on")
    if so is None:
        z = np.zeros(len(aux["c"]), dtype=bool)
        return z, z
    with np.errstate(invalid="ignore"):
        lagging = np.abs(so) >= min_spx
        # gap captured too little of the US move
        up = lagging & (so > 0) & (aux["gap"] < lag_k * so)
        dn = lagging & (so < 0) & (aux["gap"] > lag_k * so)
    return up, dn


def sig_bn_lead(aux, p):
    """BankNifty -> NIFTY lead-lag: when BankNifty diverged from NIFTY yesterday,
    NIFTY tends to close the divergence next session (FII hedging flows hit banks
    first). Enter NIFTY in the direction of BN's excess move."""
    be = aux.get("bn_excess")
    if be is None:
        z = np.zeros(len(aux["c"]), dtype=bool)
        return z, z
    thr = float(p.get("excess_thr", 0.002))
    with np.errstate(invalid="ignore"):
        long_m = be >= thr      # banks outperformed -> NIFTY catches up
        short_m = be <= -thr
    return long_m, short_m


def sig_vrp_harvest(aux, p):
    """Variance-risk-premium directional harvest: when implied (VIX) far exceeds
    trailing realized vol, option sellers demand too much insurance -- that state
    historically pays a drift premium to long index exposure."""
    q = float(p.get("q", 0.80))
    vr = aux.get("vrp_rank")
    if vr is None:
        z = np.zeros(len(aux["c"]), dtype=bool)
        return z, z
    long_m = vr >= q
    short_m = vr <= (1.0 - q) if p.get("mirror") else np.zeros(len(vr), dtype=bool)
    return long_m, short_m


def sig_calm_bounce(aux, p):
    """Forced-flow bounce: k down-days WITHOUT panic (VIX change rank < cap).
    Streak reversals fail when the selling is informational; gating on calm VIX
    keeps only liquidation-style drops."""
    k = int(p.get("k", 3))
    cap = float(p.get("vix_rank_cap", 0.70))
    calm = aux.get("vix_rank", np.ones(len(aux["c"]))) < cap
    return (aux["dn_streak"] >= k) & calm, \
           (aux["up_streak"] >= k) & calm


def sig_range_expansion(aux, p):
    """Vol-expansion onset: quietest session in a month, then today expands to
    k× ATR. Expansion begets expansion -- ride toward the close direction."""
    idle_n = int(p.get("idle_n", 20))
    k = float(p.get("k_atr", 1.6))
    tr = np.abs(aux["c"] - np.roll(aux["c"], 1))
    idle = aux["atr"] * idle_n * 0 + pd.Series(tr).rolling(idle_n).min().to_numpy()
    expanded = tr >= k * aux["atr"]
    was_quiet = tr <= idle * float(p.get("quiet_k", 1.15))
    up = expanded & was_quiet & (aux["c"] > aux["o"])
    dn = expanded & was_quiet & (aux["c"] < aux["o"])
    return up, dn


def sig_lowvol_grind(aux, p):
    """Low-vol grind: VIX < threshold + index above its 50d mean -> the
    melt-up regime. Slow, high-hit-rate drift capture while it lasts."""
    vthr = float(p.get("vix_thr", 14.0))
    above = aux["c"] > pd.Series(aux["c"]).rolling(50).mean().to_numpy()
    if "vix" in aux:
        m = above & (aux["vix"] < vthr)
    else:
        m = above
    return m, ~above & (aux.get("vix", above * 0 + 99) > 22)


SIGNALS = {
    "range_expansion": (sig_range_expansion,
                        [{"idle_n": 20, "k_atr": 1.5}, {"idle_n": 20, "k_atr": 2.0}]),
    "lowvol_grind": (sig_lowvol_grind, [{"vix_thr": 14.0}, {"vix_thr": 16.0}]),
}

SIGNALS.update({
    "breakout": (sig_breakout, [{"n": n} for n in (10, 20, 40, 60)]),
    "coil_break": (sig_coil_break, [{"buf": b} for b in (0.05, 0.15)]),
    "gap": (sig_gap, [{"mode": m, "thr": t} for m in ("cont", "fade")
                      for t in (0.5, 1.0)]),
    "streak": (sig_streak, [{"k": k} for k in (3, 4)]),
    "vix_shock": (sig_vix_shock, [{"q": q, "rmin": r} for q in (0.90, 0.95)
                                  for r in (-0.010, -0.020)]),
    "ma_cross": (sig_ma_cross, [{"fast": f, "slow": s}
                                for f, s in ((10, 30), (20, 50), (50, 200))]),
    "close_str": (sig_close_strength, [{"pct_hi": 0.90, "pct_lo": 0.05},
                                       {"pct_hi": 0.95, "pct_lo": 0.02}]),
    # classics
    "abs_momentum": (sig_abs_momentum, [{"look": 252, "skip": 21},
                                        {"look": 126, "skip": 21}]),
    "rsi2_dip": (sig_rsi2_dip, [{"rsi_lo": 10}, {"rsi_lo": 15}]),
    "turn_of_month": (sig_turn_of_month, [{}]),
    "hi52": (sig_hi52, [{"thr": 0.95}, {"thr": 0.90}]),
    # mine
    "us_lag": (sig_us_lag, [{"min_spx_ret": 0.0075, "lag_k": 0.35},
                            {"min_spx_ret": 0.010, "lag_k": 0.50}]),
    "bn_lead": (sig_bn_lead, [{"excess_thr": 0.002}, {"excess_thr": 0.0035}]),
    "vrp_harvest": (sig_vrp_harvest, [{"q": 0.80}, {"q": 0.65, "mirror": True}]),
    "calm_bounce": (sig_calm_bounce, [{"k": 3, "vix_rank_cap": 0.70},
                                      {"k": 4, "vix_rank_cap": 0.60}]),
})


# ------------------------------------------------------------------ filters
def filt_none(aux, p):
    return np.ones(len(aux["c"]), dtype=bool)


def filt_vix_low(aux, p):
    return aux["vix"] < float(p.get("level", 15))


def filt_vix_high(aux, p):
    return aux["vix"] > float(p.get("level", 18))


FILTERS = {"none": filt_none, "vix_low": filt_vix_low, "vix_high": filt_vix_high}


# -------------------------------------------------------------------- exits
EXITS = [("so", 0),
         ("hold", 1), ("hold", 3), ("hold", 5), ("hold", 10),
         ("sltp", 1.0, 2.0), ("sltp", 2.0, 4.0),
         ("trail", 1.0), ("trail", 2.0)]
