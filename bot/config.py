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
    # --- Standard Volatility (1 tick every 2 seconds) ---
    "V5":        "R_5",
    "V10":       "R_10",
    "V15":       "R_15",
    "V25":       "R_25",
    "V30":       "R_30",
    "V50":       "R_50",
    "V75":       "R_75",
    "V90":       "R_90",
    "V100":      "R_100",
    # --- 1-Second Volatility (1 tick every 1 second) ---
    "V5_1S":     "1HZ5V",
    "V10_1S":    "1HZ10V",
    "V15_1S":    "1HZ15V",
    "V25_1S":    "1HZ25V",
    "V30_1S":    "1HZ30V",
    "V50_1S":    "1HZ50V",
    "V75_1S":    "1HZ75V",
    "V90_1S":    "1HZ90V",
    "V100_1S":   "1HZ100V",
    # --- Reversal instruments ---
    "BOOM1000":  "BOOM1000",
    "CRASH1000": "CRASH1000",
}

TREND_SYMBOLS: List[str] = [
    # Standard 2s
    "V5", "V10", "V15", "V25", "V30", "V50", "V75", "V90", "V100",
    # 1-second
    "V5_1S", "V10_1S", "V15_1S", "V25_1S", "V30_1S",
    "V50_1S", "V75_1S", "V90_1S", "V100_1S",
]
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
MAX_DAILY_TRADES:          int   = 20
MAX_TRADES_PER_SYMBOL:     int   = 5
RISK_PER_TRADE_MIN_PCT:    float = 0.005   # 0.5 %
RISK_PER_TRADE_MAX_PCT:    float = 0.010   # 1.0 %
CONSEC_LOSSES_PAUSE:       int   = 3       # pause for 2 h
CONSEC_LOSSES_STOP:        int   = 5       # stop for the day
PAUSE_DURATION_SECONDS:    int   = 7200    # 2 hours

# Take-profit to stop-loss ratio
TP_SL_RATIO: float = 2.0

# Multipliers per symbol — Deriv enforces different allowed values per instrument
# If a trade fails with "Multiplier not in acceptable range", update the value here.
# R_50  confirmed: 80, 200, 400, 600, 800
# R_75  confirmed: 50, 100, 200, 500, 1000
# R_100 confirmed: 50, 100, 200, 500, 1000
MULTIPLIER_VALUE: int = 50   # default fallback
MULTIPLIER_PER_SYMBOL: Dict[str, int] = {
    # Standard 2s — confirmed
    "V50":       80,
    "V75":       50,
    "V100":      50,
    # Standard 2s — unconfirmed, adjust if trade fails
    "V5":        100,
    "V10":       100,
    "V15":       100,
    "V25":       100,
    "V30":       50,
    "V90":       50,
    # 1-second variants — confirmed range: 40,100,200,300,400 → use 100
    "V5_1S":     100,
    "V10_1S":    100,
    "V15_1S":    100,
    "V25_1S":    100,
    "V30_1S":    100,
    "V50_1S":    100,
    "V75_1S":    100,
    "V90_1S":    100,
    "V100_1S":   100,
    # Reversal
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

# ---------------------------------------------------------------------------
# Bybit / Crypto (USDT Linear Perpetuals — supports both long and short)
# ---------------------------------------------------------------------------
BYBIT_API_KEY:    str  = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET: str  = os.getenv("BYBIT_API_SECRET", "")
# Defaults to testnet so an unconfigured .env can never touch real funds.
BYBIT_TESTNET:    bool = os.getenv("BYBIT_TESTNET", "true").lower() == "true"
# Set BYBIT_DEMO=true when using Bybit's Demo Trading account (api-demo.bybit.com).
BYBIT_DEMO:       bool = os.getenv("BYBIT_DEMO", "false").lower() == "true"

# Bybit USDT Linear Perpetual symbol format (ccxt unified: "BASE/USDT:USDT")
CRYPTO_SYMBOLS: Dict[str, str] = {
    "BTC": "BTC/USDT:USDT",
    "ETH": "ETH/USDT:USDT",
    "SOL": "SOL/USDT:USDT",
    "XRP": "XRP/USDT:USDT",
}
CRYPTO_TREND_SYMBOLS: List[str] = list(CRYPTO_SYMBOLS.keys())

# Minimum margin per position in USDT (Bybit min is ~1 USDT but keep 10 conservative).
CRYPTO_MIN_NOTIONAL_USDT: float = 10.0

# Isolated margin: max loss on a position is capped at the margin committed,
# the same "stake = max loss" guarantee used for Deriv multiplier contracts.
BYBIT_MARGIN_MODE: str = "isolated"
BYBIT_LEVERAGE:    int = int(os.getenv("BYBIT_LEVERAGE", "3"))

CRYPTO_SCAN_INTERVAL_SECONDS: int = SCAN_INTERVAL_SECONDS   # same M15 cadence

CRYPTO_TRADE_LOG_FILE:  str = os.path.join(LOG_DIR, "crypto_trades.csv")
CRYPTO_PERF_LOG_FILE:   str = os.path.join(LOG_DIR, "crypto_performance.csv")
CRYPTO_APP_LOG_FILE:    str = os.path.join(LOG_DIR, "crypto_bot.log")
CRYPTO_POSITIONS_FILE:  str = os.path.join(LOG_DIR, "crypto_positions.json")
