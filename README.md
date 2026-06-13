# Deriv Trading Bot — Dual Strategy System

A production-ready automated trading bot for **Deriv Synthetic Indices** using a dual-strategy engine with strict risk management.

---

## Instruments

| Symbol | Deriv ID | Strategy |
|--------|----------|----------|
| Volatility 50 Index | `R_50` | Trend |
| Volatility 75 Index | `R_75` | Trend |
| Volatility 100 Index | `R_100` | Trend |
| Boom 1000 | `BOOM1000` | Reversal |
| Crash 1000 | `CRASH1000` | Reversal |

---

## Strategies

### Trend Strategy (V50 · V75 · V100)
- **Indicators:** EMA 20 / 50 / 200 · RSI 14 · ATR 14
- **BUY:** EMAs aligned bullish + price near EMA20/50 pullback + RSI 50–65 + bullish breakout candle
- **SELL:** EMAs aligned bearish + price near EMA20/50 retracement + RSI 35–50 + bearish breakdown candle
- **Confirmation:** M30 EMA alignment must agree with M15 signal

### Reversal Strategy (BOOM1000 · CRASH1000)
- **Boom 1000 SELL:** Large bullish spike → RSI > 75 → bearish rejection candle
- **Crash 1000 BUY:** Large bearish spike → RSI < 25 → bullish rejection candle

### Signal Scoring
Only signals scoring **≥ 80 / 100** are traded.

---

## Risk Management

| Rule | Setting |
|------|---------|
| Daily loss limit | −3% of account equity |
| Max trades per day | 8 total · 2 per symbol |
| Max open positions | 1 per symbol |
| Risk per trade | 0.5% – 1.0% of equity |
| Consecutive losses pause | 3 losses → 2-hour pause |
| Consecutive losses stop | 5 losses → halt for the day |

No martingale. No grid. Every trade has SL and TP enforced by the Deriv API.

---

## Project Structure

```
/bot
  __init__.py      # package init
  main.py          # execution loop & orchestration
  scanner.py       # multi-symbol concurrent scanner
  strategies.py    # TrendStrategy & ReversalStrategy
  risk_manager.py  # RiskManager & VolatilityFilter
  broker_deriv.py  # Deriv WebSocket API client
  indicators.py    # EMA, RSI, ATR (pure numpy)
  config.py        # all parameters in one place
  logger.py        # CSV trade logging & analytics
  utils.py         # shared helpers

/logs              # auto-created
  trades.csv       # per-trade record
  performance.csv  # daily summary
  bot.log          # rotating application log
```

---

## Quick Start

### 1. Clone & install dependencies

```bash
git clone https://github.com/your-username/Deriv-trading-bot.git
cd Deriv-trading-bot
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
# Edit .env and set DERIV_API_TOKEN and DERIV_APP_ID
```

Get your API token at <https://app.deriv.com/account/api-token>  
Create an App ID at <https://api.deriv.com/>

### 3. Run

```bash
python -m bot.main
```

### Termux (Android)

```bash
pkg install python git
pip install -r requirements.txt
python -m bot.main
```

---

## Configuration

All parameters live in `bot/config.py`. Key settings:

```python
DAILY_LOSS_LIMIT_PCT   = 0.03     # 3%
MAX_DAILY_TRADES       = 8
RISK_PER_TRADE_MAX_PCT = 0.01     # 1%
MIN_SIGNAL_SCORE       = 80.0
MULTIPLIER_VALUE       = 50       # x50 multiplier contracts
SCAN_INTERVAL_SECONDS  = 900      # 15 minutes
```

---

## Logs

After the first trade cycle:

```
logs/trades.csv       → timestamp, symbol, strategy, entry, exit, SL, TP, P&L, streak, score
logs/performance.csv  → daily win rate, total P&L, equity drift
logs/bot.log          → application events (rotating, 10 MB × 5)
```

---

## Disclaimer

This bot is for **educational purposes**. Automated trading involves significant financial risk. Never trade with funds you cannot afford to lose. Past performance does not guarantee future results. Always test on a **demo account** before going live.
