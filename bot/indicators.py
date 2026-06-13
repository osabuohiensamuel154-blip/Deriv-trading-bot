"""
Technical indicator calculations.
All functions are pure — they take numpy arrays and return numpy arrays or scalars.
"""

from __future__ import annotations

import numpy as np
from typing import Tuple


# ---------------------------------------------------------------------------
# Moving averages
# ---------------------------------------------------------------------------

def ema(prices: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average via Wilder's smoothing (pandas-free)."""
    if len(prices) < period:
        return np.full(len(prices), np.nan)

    result = np.full(len(prices), np.nan)
    k = 2.0 / (period + 1)

    # seed with SMA of first `period` values
    result[period - 1] = np.mean(prices[:period])
    for i in range(period, len(prices)):
        result[i] = prices[i] * k + result[i - 1] * (1 - k)

    return result


# ---------------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------------

def rsi(prices: np.ndarray, period: int = 14) -> np.ndarray:
    """Relative Strength Index using Wilder's smoothing."""
    if len(prices) < period + 1:
        return np.full(len(prices), np.nan)

    deltas = np.diff(prices)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    result = np.full(len(prices), np.nan)

    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            result[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i + 1] = 100.0 - (100.0 / (1.0 + rs))

    return result


# ---------------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------------

def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray,
        period: int = 14) -> np.ndarray:
    """Average True Range."""
    n = len(high)
    if n < period + 1:
        return np.full(n, np.nan)

    tr = np.empty(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i]  - close[i - 1]),
        )

    result = np.full(n, np.nan)
    result[period - 1] = np.mean(tr[:period])
    for i in range(period, n):
        result[i] = (result[i - 1] * (period - 1) + tr[i]) / period

    return result


# ---------------------------------------------------------------------------
# Candle helpers
# ---------------------------------------------------------------------------

def candle_body(open_: float, close: float) -> float:
    return abs(close - open_)


def candle_range(high: float, low: float) -> float:
    return high - low


def upper_wick(open_: float, high: float, close: float) -> float:
    return high - max(open_, close)


def lower_wick(open_: float, low: float, close: float) -> float:
    return min(open_, close) - low


def is_bullish(open_: float, close: float) -> bool:
    return close > open_


def is_bearish(open_: float, close: float) -> bool:
    return close < open_


# ---------------------------------------------------------------------------
# Trend helpers
# ---------------------------------------------------------------------------

def ema_aligned_bullish(ema20: float, ema50: float, ema200: float) -> bool:
    return ema20 > ema50 > ema200


def ema_aligned_bearish(ema20: float, ema50: float, ema200: float) -> bool:
    return ema20 < ema50 < ema200


def price_in_pullback_zone(price: float, ema20: float, ema50: float,
                            tolerance_pct: float = 0.0015) -> bool:
    """True when price is within tolerance_pct of EMA20 or EMA50."""
    tol20 = ema20 * tolerance_pct
    tol50 = ema50 * tolerance_pct
    near_ema20 = abs(price - ema20) <= tol20
    near_ema50 = abs(price - ema50) <= tol50
    return near_ema20 or near_ema50


# ---------------------------------------------------------------------------
# Spike detection
# ---------------------------------------------------------------------------

def detect_spike(open_: float, high: float, low: float, close: float,
                 current_atr: float, multiplier: float = 2.5) -> bool:
    """Returns True when candle body >= multiplier × ATR (strong spike)."""
    body = candle_body(open_, close)
    return current_atr > 0 and body >= multiplier * current_atr


def detect_exhaustion_wick(open_: float, high: float, low: float, close: float,
                            current_atr: float, mult: float = 1.8) -> bool:
    """Long wick relative to ATR signals exhaustion."""
    uw = upper_wick(open_, high, close)
    lw = lower_wick(open_, low, close)
    return current_atr > 0 and max(uw, lw) >= mult * current_atr


# ---------------------------------------------------------------------------
# Market state
# ---------------------------------------------------------------------------

def is_consolidating(ema20: float, ema50: float, threshold_pct: float = 0.002) -> bool:
    """True when EMA20 and EMA50 are too close together (flat market)."""
    return abs(ema20 - ema50) / ema50 < threshold_pct
