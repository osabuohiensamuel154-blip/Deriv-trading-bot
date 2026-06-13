"""
Dual strategy engine:
  - TrendStrategy  → V50, V75, V100
  - ReversalStrategy → BOOM1000, CRASH1000
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from bot import indicators as ind
from bot.config import (
    EMA_FAST, EMA_MID, EMA_SLOW, RSI_PERIOD, ATR_PERIOD,
    RSI_BUY_LOW, RSI_BUY_HIGH, RSI_SELL_LOW, RSI_SELL_HIGH,
    RSI_BOOM_OVERBOUGHT, RSI_CRASH_OVERSOLD,
    SPIKE_ATR_MULTIPLIER, EXHAUSTION_ATR_MULT,
    PULLBACK_EMA_TOLERANCE, TREND_WEIGHTS, REVERSAL_WEIGHTS,
    MIN_SIGNAL_SCORE, TP_SL_RATIO, ATR_PERIOD,
)

log = logging.getLogger(__name__)


@dataclass
class Signal:
    symbol:     str
    strategy:   str            # "TREND" | "REVERSAL"
    direction:  str            # "BUY"  | "SELL"
    score:      float
    entry:      float
    stop_loss:  float
    take_profit: float
    timeframe:  str = "M15"
    valid:      bool = True
    reason:     str = ""


# ---------------------------------------------------------------------------
# Candle data container
# ---------------------------------------------------------------------------

@dataclass
class CandleData:
    open:  np.ndarray
    high:  np.ndarray
    low:   np.ndarray
    close: np.ndarray
    epoch: np.ndarray   # unix timestamps

    def __len__(self) -> int:
        return len(self.close)


# ---------------------------------------------------------------------------
# Trend Strategy (V50, V75, V100)
# ---------------------------------------------------------------------------

class TrendStrategy:
    """EMA + RSI + ATR trend-following system."""

    def __init__(self) -> None:
        self.name = "TREND"

    # ------------------------------------------------------------------
    # Internal indicator computation
    # ------------------------------------------------------------------

    def _compute(self, data: CandleData) -> dict:
        c = data.close
        h = data.high
        l = data.low

        return {
            "ema20":  ind.ema(c, EMA_FAST),
            "ema50":  ind.ema(c, EMA_MID),
            "ema200": ind.ema(c, EMA_SLOW),
            "rsi":    ind.rsi(c, RSI_PERIOD),
            "atr":    ind.atr(h, l, c, ATR_PERIOD),
        }

    # ------------------------------------------------------------------
    # Score a BUY or SELL setup
    # ------------------------------------------------------------------

    def _score_buy(self, indics: dict, data: CandleData, idx: int) -> float:
        ema20  = indics["ema20"][idx]
        ema50  = indics["ema50"][idx]
        ema200 = indics["ema200"][idx]
        rsi_v  = indics["rsi"][idx]
        atr_v  = indics["atr"][idx]

        if any(np.isnan([ema20, ema50, ema200, rsi_v, atr_v])):
            return 0.0

        price      = data.close[idx]
        prev_high  = data.high[idx - 1]
        open_      = data.open[idx]
        close_     = data.close[idx]

        scores: dict[str, float] = {}

        # Trend strength (40%)
        if ind.ema_aligned_bullish(ema20, ema50, ema200):
            spread = (ema20 - ema200) / ema200
            scores["trend_strength"] = min(100.0, spread * 10000)
        else:
            return 0.0   # mandatory

        # Pullback quality (20%)
        if ind.price_in_pullback_zone(price, ema20, ema50, PULLBACK_EMA_TOLERANCE):
            scores["pullback_quality"] = 100.0
        else:
            scores["pullback_quality"] = 0.0

        # RSI alignment (20%)
        if RSI_BUY_LOW <= rsi_v <= RSI_BUY_HIGH:
            scores["rsi_alignment"] = 100.0
        else:
            scores["rsi_alignment"] = 0.0

        # Candle strength (20%)
        bullish_breakout = ind.is_bullish(open_, close_) and close_ > prev_high
        body_vs_atr = ind.candle_body(open_, close_) / atr_v if atr_v > 0 else 0
        scores["candle_strength"] = 100.0 if (bullish_breakout and body_vs_atr >= 0.5) else 0.0

        total = sum(
            scores.get(k, 0.0) * w
            for k, w in TREND_WEIGHTS.items()
        )
        return round(total, 2)

    def _score_sell(self, indics: dict, data: CandleData, idx: int) -> float:
        ema20  = indics["ema20"][idx]
        ema50  = indics["ema50"][idx]
        ema200 = indics["ema200"][idx]
        rsi_v  = indics["rsi"][idx]
        atr_v  = indics["atr"][idx]

        if any(np.isnan([ema20, ema50, ema200, rsi_v, atr_v])):
            return 0.0

        price     = data.close[idx]
        prev_low  = data.low[idx - 1]
        open_     = data.open[idx]
        close_    = data.close[idx]

        scores: dict[str, float] = {}

        # Trend strength (40%)
        if ind.ema_aligned_bearish(ema20, ema50, ema200):
            spread = (ema200 - ema20) / ema200
            scores["trend_strength"] = min(100.0, spread * 10000)
        else:
            return 0.0

        # Pullback quality (20%)
        if ind.price_in_pullback_zone(price, ema20, ema50, PULLBACK_EMA_TOLERANCE):
            scores["pullback_quality"] = 100.0
        else:
            scores["pullback_quality"] = 0.0

        # RSI alignment (20%)
        if RSI_SELL_LOW <= rsi_v <= RSI_SELL_HIGH:
            scores["rsi_alignment"] = 100.0
        else:
            scores["rsi_alignment"] = 0.0

        # Candle strength (20%)
        bearish_breakout = ind.is_bearish(open_, close_) and close_ < prev_low
        body_vs_atr = ind.candle_body(open_, close_) / atr_v if atr_v > 0 else 0
        scores["candle_strength"] = 100.0 if (bearish_breakout and body_vs_atr >= 0.5) else 0.0

        total = sum(
            scores.get(k, 0.0) * w
            for k, w in TREND_WEIGHTS.items()
        )
        return round(total, 2)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def analyze(self, symbol: str, primary: CandleData,
                confirmation: CandleData) -> Optional[Signal]:
        """
        Evaluate the last closed candle on the primary (M15) timeframe,
        then confirm trend direction on the secondary (M30) timeframe.
        Returns a Signal or None.
        """
        if len(primary) < EMA_SLOW + 5:
            log.debug("%s: not enough M15 candles (%d)", symbol, len(primary))
            return None

        indics_m15 = self._compute(primary)
        indics_m30 = self._compute(confirmation)

        idx = -2   # last fully closed candle

        # Check for consolidation
        ema20 = indics_m15["ema20"][idx]
        ema50 = indics_m15["ema50"][idx]
        if np.isnan(ema20) or np.isnan(ema50):
            return None
        if ind.is_consolidating(ema20, ema50):
            log.debug("%s: market consolidating, skipping", symbol)
            return None

        atr_v = indics_m15["atr"][idx]
        price = primary.close[idx]

        buy_score  = self._score_buy(indics_m15, primary, idx)
        sell_score = self._score_sell(indics_m15, primary, idx)

        # Confirm with M30
        m30_idx    = -1
        m30_ema20  = indics_m30["ema20"][m30_idx] if len(confirmation) > EMA_SLOW else np.nan
        m30_ema50  = indics_m30["ema50"][m30_idx] if len(confirmation) > EMA_SLOW else np.nan
        m30_ema200 = indics_m30["ema200"][m30_idx] if len(confirmation) > EMA_SLOW else np.nan

        m30_bull_confirm = not np.isnan(m30_ema20) and ind.ema_aligned_bullish(m30_ema20, m30_ema50, m30_ema200)
        m30_bear_confirm = not np.isnan(m30_ema20) and ind.ema_aligned_bearish(m30_ema20, m30_ema50, m30_ema200)

        # Pick the stronger signal that passes the minimum score
        if buy_score >= MIN_SIGNAL_SCORE and m30_bull_confirm:
            sl = price - atr_v * 1.5
            tp = price + atr_v * 1.5 * TP_SL_RATIO
            log.info("%s TREND BUY signal, score=%.1f", symbol, buy_score)
            return Signal(symbol=symbol, strategy="TREND", direction="BUY",
                          score=buy_score, entry=price, stop_loss=sl,
                          take_profit=tp)

        if sell_score >= MIN_SIGNAL_SCORE and m30_bear_confirm:
            sl = price + atr_v * 1.5
            tp = price - atr_v * 1.5 * TP_SL_RATIO
            log.info("%s TREND SELL signal, score=%.1f", symbol, sell_score)
            return Signal(symbol=symbol, strategy="TREND", direction="SELL",
                          score=sell_score, entry=price, stop_loss=sl,
                          take_profit=tp)

        return None


# ---------------------------------------------------------------------------
# Reversal Strategy (BOOM1000, CRASH1000)
# ---------------------------------------------------------------------------

class ReversalStrategy:
    """Spike + RSI extreme reversal system for boom/crash indices."""

    def __init__(self) -> None:
        self.name = "REVERSAL"

    def _compute(self, data: CandleData) -> dict:
        c = data.close
        h = data.high
        l = data.low
        return {
            "rsi": ind.rsi(c, RSI_PERIOD),
            "atr": ind.atr(h, l, c, ATR_PERIOD),
        }

    # ------------------------------------------------------------------
    # Score a BOOM SELL (fade the bullish spike)
    # ------------------------------------------------------------------

    def _score_boom_sell(self, indics: dict, data: CandleData, idx: int) -> float:
        rsi_v = indics["rsi"][idx]
        atr_v = indics["atr"][idx]

        if np.isnan(rsi_v) or np.isnan(atr_v) or atr_v == 0:
            return 0.0

        o, h, l, c = (data.open[idx], data.high[idx],
                      data.low[idx],  data.close[idx])
        o_prev = data.open[idx - 1]
        c_prev = data.close[idx - 1]

        scores: dict[str, float] = {}

        # Spike strength (40%): prior candle is a large bullish spike
        spike_prev = ind.detect_spike(o_prev, data.high[idx - 1],
                                      data.low[idx - 1], c_prev,
                                      indics["atr"][idx - 1], SPIKE_ATR_MULTIPLIER)
        if not spike_prev or not ind.is_bullish(o_prev, c_prev):
            return 0.0
        body_ratio = ind.candle_body(o_prev, c_prev) / (atr_v * SPIKE_ATR_MULTIPLIER)
        scores["spike_strength"] = min(100.0, body_ratio * 100)

        # RSI extreme (25%)
        if rsi_v >= RSI_BOOM_OVERBOUGHT:
            scores["rsi_extreme"] = min(100.0, (rsi_v - RSI_BOOM_OVERBOUGHT) * 4 + 80)
        else:
            return 0.0

        # Rejection / confirmation candle (25%): current candle is bearish
        if ind.is_bearish(o, c):
            uw = ind.upper_wick(o, h, c)
            body = ind.candle_body(o, c)
            scores["rejection_candle"] = 100.0 if uw >= body * 0.5 else 60.0
        else:
            scores["rejection_candle"] = 0.0

        # ATR expansion (10%)
        prev_atr = indics["atr"][idx - 1]
        if not np.isnan(prev_atr) and prev_atr > 0:
            scores["atr_expansion"] = min(100.0, (atr_v / prev_atr - 1) * 200 + 50)
        else:
            scores["atr_expansion"] = 50.0

        total = sum(
            scores.get(k, 0.0) * w
            for k, w in REVERSAL_WEIGHTS.items()
        )
        return round(total, 2)

    # ------------------------------------------------------------------
    # Score a CRASH BUY (fade the bearish spike)
    # ------------------------------------------------------------------

    def _score_crash_buy(self, indics: dict, data: CandleData, idx: int) -> float:
        rsi_v = indics["rsi"][idx]
        atr_v = indics["atr"][idx]

        if np.isnan(rsi_v) or np.isnan(atr_v) or atr_v == 0:
            return 0.0

        o, h, l, c = (data.open[idx], data.high[idx],
                      data.low[idx],  data.close[idx])
        o_prev = data.open[idx - 1]
        c_prev = data.close[idx - 1]

        scores: dict[str, float] = {}

        # Spike strength (40%)
        spike_prev = ind.detect_spike(o_prev, data.high[idx - 1],
                                      data.low[idx - 1], c_prev,
                                      indics["atr"][idx - 1], SPIKE_ATR_MULTIPLIER)
        if not spike_prev or not ind.is_bearish(o_prev, c_prev):
            return 0.0
        body_ratio = ind.candle_body(o_prev, c_prev) / (atr_v * SPIKE_ATR_MULTIPLIER)
        scores["spike_strength"] = min(100.0, body_ratio * 100)

        # RSI extreme (25%)
        if rsi_v <= RSI_CRASH_OVERSOLD:
            scores["rsi_extreme"] = min(100.0, (RSI_CRASH_OVERSOLD - rsi_v) * 4 + 80)
        else:
            return 0.0

        # Rejection / confirmation candle (25%)
        if ind.is_bullish(o, c):
            lw = ind.lower_wick(o, l, c)
            body = ind.candle_body(o, c)
            scores["rejection_candle"] = 100.0 if lw >= body * 0.5 else 60.0
        else:
            scores["rejection_candle"] = 0.0

        # ATR expansion (10%)
        prev_atr = indics["atr"][idx - 1]
        if not np.isnan(prev_atr) and prev_atr > 0:
            scores["atr_expansion"] = min(100.0, (atr_v / prev_atr - 1) * 200 + 50)
        else:
            scores["atr_expansion"] = 50.0

        total = sum(
            scores.get(k, 0.0) * w
            for k, w in REVERSAL_WEIGHTS.items()
        )
        return round(total, 2)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def analyze(self, symbol: str, primary: CandleData,
                confirmation: CandleData) -> Optional[Signal]:
        if len(primary) < ATR_PERIOD + 5:
            return None

        indics = self._compute(primary)
        idx = -2   # last closed candle
        price = primary.close[idx]
        atr_v = indics["atr"][idx]

        if symbol == "BOOM1000":
            score = self._score_boom_sell(indics, primary, idx)
            if score >= MIN_SIGNAL_SCORE:
                sl = price + atr_v * 1.5
                tp = price - atr_v * 1.5 * TP_SL_RATIO
                log.info("BOOM1000 REVERSAL SELL signal, score=%.1f", score)
                return Signal(symbol=symbol, strategy="REVERSAL", direction="SELL",
                              score=score, entry=price, stop_loss=sl,
                              take_profit=tp)

        elif symbol == "CRASH1000":
            score = self._score_crash_buy(indics, primary, idx)
            if score >= MIN_SIGNAL_SCORE:
                sl = price - atr_v * 1.5
                tp = price + atr_v * 1.5 * TP_SL_RATIO
                log.info("CRASH1000 REVERSAL BUY signal, score=%.1f", score)
                return Signal(symbol=symbol, strategy="REVERSAL", direction="BUY",
                              score=score, entry=price, stop_loss=sl,
                              take_profit=tp)

        return None
