from __future__ import annotations

import math

import numpy as np

_ERF_VEC = np.vectorize(math.erf)


def _norm_cdf(x):
    if isinstance(x, np.ndarray):
        return 0.5 * (1.0 + _ERF_VEC(x / np.sqrt(2.0)))
    return 0.5 * (1.0 + math.erf(x / np.sqrt(2.0)))


def _norm_pdf(x):
    return np.exp(-0.5 * x * x) / np.sqrt(2.0 * np.pi)


def bs_price(S, K, T, sigma, r=0.065, q=0.0, kind="CE"):
    is_call = str(kind).upper() in ("CE", "C", "CALL")
    if all(np.isscalar(v) for v in (S, K, T, sigma)):
        return _bs_price_scalar(float(S), float(K), float(T), float(sigma), r, q, is_call)
    S = np.asarray(S, dtype=float)
    K = np.asarray(K, dtype=float)
    T = np.maximum(np.asarray(T, dtype=float), 1e-8)
    sigma = np.maximum(np.asarray(sigma, dtype=float), 1e-6)
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if is_call:
        return S * np.exp(-q * T) * _norm_cdf(d1) - K * np.exp(-r * T) * _norm_cdf(d2)
    return K * np.exp(-r * T) * _norm_cdf(-d2) - S * np.exp(-q * T) * _norm_cdf(-d1)


def _bs_price_scalar(S: float, K: float, T: float, sigma: float, r: float, q: float, is_call: bool) -> float:
    T = max(T, 1e-8)
    sig = max(sigma, 1e-6)
    sq = sig * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sig * sig) * T) / sq
    d2 = d1 - sq
    if is_call:
        return S * math.exp(-q * T) * _ncdf_s(d1) - K * math.exp(-r * T) * _ncdf_s(d2)
    return K * math.exp(-r * T) * _ncdf_s(-d2) - S * math.exp(-q * T) * _ncdf_s(-d1)


def _ncdf_s(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / 1.4142135623730951))


def bs_delta(S, K, T, sigma, r=0.065, q=0.0, kind="CE"):
    S = np.asarray(S, dtype=float)
    K = np.asarray(K, dtype=float)
    T = np.maximum(np.asarray(T, dtype=float), 1e-8)
    sigma = np.maximum(np.asarray(sigma, dtype=float), 1e-6)
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    nd1 = _norm_cdf(d1)
    if str(kind).upper() in ("CE", "C", "CALL"):
        return np.exp(-q * T) * nd1
    return np.exp(-q * T) * (nd1 - 1.0)


def bs_vega(S, K, T, sigma, r=0.065, q=0.0):
    S = np.asarray(S, dtype=float)
    K = np.asarray(K, dtype=float)
    T = np.maximum(np.asarray(T, dtype=float), 1e-8)
    sigma = np.maximum(np.asarray(sigma, dtype=float), 1e-6)
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    return S * np.exp(-q * T) * _norm_pdf(d1) * np.sqrt(T) / 100.0


def implied_vol(price, S, K, T, kind="CE", r=0.065, lo=0.005, hi=5.0, tol=1e-6, iters=100):
    price = float(price)
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        p = float(bs_price(S, K, max(T, 1e-8), mid, r=r, kind=kind))
        if abs(p - price) < tol:
            return mid
        if p > price:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)
