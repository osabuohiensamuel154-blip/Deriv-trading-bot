"""
Central configuration for the Deriv Trading Bot.
All tunable parameters live here — no magic numbers elsewhere.
"""

import os
from dataclasses import dataclass, field
from typing import Dict, List

# ---------------------------------------------------------------------------
# Deriv API
# ---------------------------------------------------------------------------
DERIV_APP_ID: str = os.getenv("DERIV_APP_ID", "1089")          # replace with your app_id
DERIV_API_TOKEN: str = os.getenv("DERIV_API_TOKEN", "")        # set via .env
DERIV_WS_URL: str = "wss://ws.binaryws.com/websockets/v3"

# ---------------------------------------------------------------------------
# Instruments
# ---------------------------------------------------------------------------
SYMBOLS: Dict[str, str] = {
    "V50":       "R_50",
    "V75":       "R_75",
    "V100":      "R_100",
    "BOOM1000":  "BOOM1000",
    "CRASH1000": "CRASH1000",
}

TREND_SYMBOLS:    List[str] = ["V50", "V75", "V100"]
REVERSAL_SYMBOLS: List[str] = ["BOOM1000", "CRASH1000"]

# ---------------------------------------------------------------------------
# Timeframes (Deriv granularity in seconds)
# ---------------------------------------------------------------------------
TIMEFRAME_M15: int = 900    # primary
TIMEFRAME_M30: int = 1800   # confirmation

# Number of candles to fetch per request
CANDLE_COUNT: int = 300

# ---------------------------------------------------------------------------
# Indicator parameters
# ---------------------------------------------------------------------------
EMA_FAST:   int = 20
EMA_MID:    int = 50
EMA_SLOW:   int = 200
RSI_PERIOD: int = 14
ATR_PERIOD: int = 14

# ---------------------------------------------------------------------------
# Trend strategy thresholds
# ---------------------------------------------------------------------------
RSI_BUY_LOW:  float = 50.0
RSI_BUY_HIGH: float = 65.0
RSI_SELL_LOW: float = 35.0
RSI_SELL_HIGH: float = 50.0

PULLBACK_EMA_TOLERANCE: float = 0.0015   # 0.15% of price

# ---------------------------------------------------------------------------
# Reversal strategy thresholds
# ---------------------------------------------------------------------------
RSI_BOOM_OVERBOUGHT:   float = 75.0
RSI_CRASH_OVERSOLD:    float = 25.0
SPIKE_ATR_MULTIPLIER:  float = 2.5      # candle body ≥ 2.5× ATR = spike
EXHAUSTION_ATR_MULT:   float = 1.8

# ---------------------------------------------------------------------------
# Signal scoring
# ---------------------------------------------------------------------------
MIN_SIGNAL_SCORE: float = 80.0

# Trend weights
TREND_WEIGHTS: Dict[str, float] = {
    "trend_strength":  0.40,
    "pullback_quality": 0.20,
    "rsi_alignment":   0.20,
    "candle_strength": 0.20,
}

# Reversal weights
REVERSAL_WEIGHTS: Dict[str, float] = {
    "spike_strength":    0.40,
    "rsi_extreme":       0.25,
    "rejection_candle":  0.25,
    "atr_expansion":     0.10,
}

# ---------------------------------------------------------------------------
# Risk management
# ---------------------------------------------------------------------------
DAILY_LOSS_LIMIT_PCT:      float = 0.03    # 3 % of account equity
MAX_DAILY_TRADES:          int   = 8
MAX_TRADES_PER_SYMBOL:     int   = 2
RISK_PER_TRADE_MIN_PCT:    float = 0.005   # 0.5 %
RISK_PER_TRADE_MAX_PCT:    float = 0.010   # 1.0 %
CONSEC_LOSSES_PAUSE:       int   = 3       # pause for 2 h
CONSEC_LOSSES_STOP:        int   = 5       # stop for the day
PAUSE_DURATION_SECONDS:    int   = 7200    # 2 hours

# Take-profit to stop-loss ratio
TP_SL_RATIO: float = 2.0

# Multipliers per symbol — Deriv enforces different allowed values per instrument
# R_50  accepts: 80, 200, 400, 600, 800
# R_75  accepts: 50, 100, 200, 500, 1000
# R_100 accepts: 50, 100, 200, 500, 1000
# BOOM1000  accepts: 50, 100, 200, 500, 1000
# CRASH1000 accepts: 50, 100, 200, 500, 1000
MULTIPLIER_VALUE: int = 50   # default fallback
MULTIPLIER_PER_SYMBOL: Dict[str, int] = {
    "V50":       80,
    "V75":       50,
    "V100":      50,
    "BOOM1000":  50,
    "CRASH1000": 50,
}

# ---------------------------------------------------------------------------
# Execution loop
# ---------------------------------------------------------------------------
SCAN_INTERVAL_SECONDS: int = 900    # 15 minutes

# API retry settings
MAX_RETRIES:   int = 5
RETRY_BACKOFF: float = 2.0   # exponential base (seconds)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DIR: str = os.path.join(os.path.dirname(__file__), "..", "logs")
TRADE_LOG_FILE: str = os.path.join(LOG_DIR, "trades.csv")
PERF_LOG_FILE:  str = os.path.join(LOG_DIR, "performance.csv")
APP_LOG_FILE:   str = os.path.join(LOG_DIR, "bot.log")
