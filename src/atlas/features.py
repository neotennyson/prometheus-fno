"""ATLAS feature layer: regime classifiers computed from daily bars.

Regimes:
  EXPANSION   -- range expansion / trend day candidates (Donchian breaks, wide TR)
  COMPRESSION -- NR7 / inside bars: coiled spring, two-sided breakout prep
  SHOCK       -- VIX spikes, large overnight US moves: dislocation fades
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    return pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)


def nr7_flag(df: pd.DataFrame, lookback: int = 7) -> pd.Series:
    rng = df["high"] - df["low"]
    return rng == rng.rolling(lookback).min()


def inside_bar_flag(df: pd.DataFrame) -> pd.Series:
    return (df["high"] < df["high"].shift(1)) & (df["low"] > df["low"].shift(1))


def range_pctile(df: pd.DataFrame, lookback: int = 60) -> pd.Series:
    rng = df["high"] - df["low"]
    return rng.rolling(lookback).rank(pct=True)


def donchian_hi(df: pd.DataFrame, n: int, skip_last: bool = True) -> pd.Series:
    """Highest high of the prior n sessions (today excluded when skip_last)."""
    end = -1 if skip_last else None
    return df["high"].rolling(n).max().shift(1 if skip_last else 0)


def donchian_lo(df: pd.DataFrame, n: int, skip_last: bool = True) -> pd.Series:
    end = -1 if skip_last else None
    return df["low"].rolling(n).min().shift(1 if skip_last else 0)


def vix_spike(vix_close: pd.Series, window: int = 252) -> pd.Series:
    """1-day VIX % change, ranked against the trailing year."""
    chg = vix_close.pct_change()
    return chg.rolling(window, min_periods=60).rank(pct=True)


def gap_atr(daily: pd.DataFrame, atr_s: pd.Series) -> pd.Series:
    prev_c = daily["close"].shift(1)
    return (daily["open"] / prev_c - 1.0) / (atr_s / prev_c)
