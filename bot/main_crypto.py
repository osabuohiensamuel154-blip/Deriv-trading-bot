"""
Crypto bot entry point — Binance Spot, long-only, same Trend strategy as Deriv bot.
Run:  python -m bot.main_crypto
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from bot.broker_binance import BinanceBroker, BinanceAPIError
from bot.config import (
    CRYPTO_SYMBOLS, CRYPTO_TREND_SYMBOLS, CRYPTO_SCAN_INTERVAL_SECONDS,
    CRYPTO_MIN_NOTIONAL_USDT, CRYPTO_TRADE_LOG_FILE, CRYPTO_PERF_LOG_FILE,
    CRYPTO_APP_LOG_FILE, CRYPTO_POSITIONS_FILE,
)
from bot.logger import TradeLogger, setup_logging
from bot.risk_manager import RiskManager, VolatilityFilter
from bot.scanner import MarketScanner
from bot.strategies import Signal
from bot.utils import format_duration, print_banner, seconds_until_next_candle, utc_now

setup_logging(log_file=CRYPTO_APP_LOG_FILE)
log = logging.getLogger(__name__)

_SHUTDOWN = False
_shutdown_count = 0


def _handle_signal(signum: int, frame: object) -> None:
    global _SHUTDOWN, _shutdown_count
    _shutdown_count += 1
    if _shutdown_count == 1:
        log.warning("Ctrl+C received — stopping cleanly. Press Ctrl+C again to force quit.")
        _SHUTDOWN = True
    else:
        log.warning("Force quit.")
        os._exit(1)


# ---------------------------------------------------------------------------
# Position persistence (Binance spot has no native "open position" concept)
# ---------------------------------------------------------------------------

def _load_positions() -> dict:
    if not os.path.isfile(CRYPTO_POSITIONS_FILE):
        return {}
    try:
        with open(CRYPTO_POSITIONS_FILE) as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not load positions file: %s", exc)
        return {}


def _save_positions(positions: dict) -> None:
    os.makedirs(os.path.dirname(CRYPTO_POSITIONS_FILE), exist_ok=True)
    with open(CRYPTO_POSITIONS_FILE, "w") as fh:
        json.dump(positions, fh, indent=2)


# ---------------------------------------------------------------------------
# Closing a position (market sell)
# ---------------------------------------------------------------------------

async def close_position(
    symbol: str,
    positions: dict,
    broker: BinanceBroker,
    risk_mgr: RiskManager,
    trade_logger: TradeLogger,
    note: str,
) -> None:
    pos = positions.get(symbol)
    if not pos:
        return

    broker_symbol = CRYPTO_SYMBOLS[symbol]
    try:
        order = await broker.market_sell(broker_symbol, pos["qty"])
    except BinanceAPIError as exc:
        log.error("Failed to close %s position: %s", symbol, exc)
        return

    exit_price = float(order.get("average") or order.get("price") or 0)
    if exit_price <= 0:
        try:
            exit_price = await broker.get_price(broker_symbol)
        except BinanceAPIError:
            exit_price = pos["entry"]

    profit = (exit_price - pos["entry"]) * pos["qty"]

    trade_logger.log_trade(
        symbol      = symbol,
        strategy    = "TREND",
        direction   = "BUY",
        entry_price = pos["entry"],
        exit_price  = exit_price,
        stop_loss   = pos["stop_loss"],
        take_profit = pos["take_profit"],
        stake       = pos["stake"],
        profit      = profit,
        score       = 0.0,
        note        = note,
    )
    risk_mgr.record_trade_result(symbol, profit)
    del positions[symbol]
    _save_positions(positions)
    log.info("Closed %s | entry=%.4f exit=%.4f P&L=%.2f (%s)",
              symbol, pos["entry"], exit_price, profit, note)


# ---------------------------------------------------------------------------
# Trade execution pipeline
# ---------------------------------------------------------------------------

async def execute_signal(
    signal: Signal,
    broker: BinanceBroker,
    risk_mgr: RiskManager,
    trade_logger: TradeLogger,
    positions: dict,
) -> None:
    if signal.direction == "SELL":
        if signal.symbol not in positions:
            log.debug("SELL signal for %s but no open position — ignoring (long-only)", signal.symbol)
            return
        await close_position(signal.symbol, positions, broker, risk_mgr, trade_logger, note="signal_exit")
        return

    # BUY: open a new long position
    decision = risk_mgr.check(signal)
    if not decision.allowed:
        log.info("Trade BLOCKED [%s %s]: %s", signal.symbol, signal.direction, decision.reason)
        return

    stake = max(decision.stake, CRYPTO_MIN_NOTIONAL_USDT)
    if stake > risk_mgr._state.current_equity:
        log.warning("Insufficient USDT balance for %s: need %.2f, have %.2f",
                    signal.symbol, stake, risk_mgr._state.current_equity)
        return

    broker_symbol = CRYPTO_SYMBOLS[signal.symbol]
    log.info("Executing trade | %s %s | score=%.1f stake=%.2f USDT",
              signal.symbol, signal.strategy, signal.score, stake)

    try:
        order = await broker.market_buy(broker_symbol, stake)
    except BinanceAPIError as exc:
        log.error("Trade placement failed for %s: %s", signal.symbol, exc)
        return

    filled_qty = float(order.get("filled") or order.get("amount") or 0)
    avg_price  = float(order.get("average") or order.get("price") or signal.entry)
    if filled_qty <= 0:
        log.error("No fill info returned for %s buy — not tracking position", signal.symbol)
        return

    positions[signal.symbol] = {
        "entry":       avg_price,
        "stop_loss":   signal.stop_loss,
        "take_profit": signal.take_profit,
        "qty":         filled_qty,
        "stake":       stake,
    }
    risk_mgr.record_trade_opened(signal.symbol)
    _save_positions(positions)
    log.info("Position opened | %s qty=%s entry=%.4f SL=%.4f TP=%.4f",
              signal.symbol, filled_qty, avg_price, signal.stop_loss, signal.take_profit)


# ---------------------------------------------------------------------------
# Background position monitor — checks SL/TP against live price every 60s
# ---------------------------------------------------------------------------

async def position_monitor(
    broker: BinanceBroker,
    risk_mgr: RiskManager,
    trade_logger: TradeLogger,
    positions: dict,
) -> None:
    while not _SHUTDOWN:
        await asyncio.sleep(60)
        if not positions:
            continue
        for symbol in list(positions.keys()):
            pos = positions.get(symbol)
            if not pos:
                continue
            broker_symbol = CRYPTO_SYMBOLS[symbol]
            try:
                price = await broker.get_price(broker_symbol)
            except BinanceAPIError as exc:
                log.debug("Position monitor: price fetch failed for %s: %s", symbol, exc)
                continue

            if price <= pos["stop_loss"]:
                log.info("%s hit stop loss (%.4f <= %.4f)", symbol, price, pos["stop_loss"])
                await close_position(symbol, positions, broker, risk_mgr, trade_logger, note="stop_loss")
            elif price >= pos["take_profit"]:
                log.info("%s hit take profit (%.4f >= %.4f)", symbol, price, pos["take_profit"])
                await close_position(symbol, positions, broker, risk_mgr, trade_logger, note="take_profit")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def run_bot() -> None:
    global _SHUTDOWN

    print_banner(instruments=" | ".join(CRYPTO_SYMBOLS.keys()),
                 strategies="Trend (EMA+RSI) only — Binance spot, long-only")
    log.info("Crypto bot starting up …")

    risk_mgr     = RiskManager()
    vol_filter   = VolatilityFilter()
    trade_logger = TradeLogger(trade_log_file=CRYPTO_TRADE_LOG_FILE, perf_log_file=CRYPTO_PERF_LOG_FILE)
    positions    = _load_positions()

    async with BinanceBroker() as broker:
        equity = await broker.get_balance("USDT")
        risk_mgr.force_reset_day(equity)
        trade_logger.set_equity_start(equity)
        log.info("Starting equity: %.2f USDT", equity)

        for symbol in positions:
            risk_mgr.register_open(symbol)
            log.info("Restored open position from disk: %s", symbol)

        scanner = MarketScanner(
            broker,
            symbols=CRYPTO_SYMBOLS,
            trend_symbols=CRYPTO_TREND_SYMBOLS,
            reversal_symbols=[],
        )

        monitor_task = asyncio.create_task(
            position_monitor(broker, risk_mgr, trade_logger, positions)
        )
        log.info("Position monitor started (60s interval)")

        _last_day = utc_now().date()
        _cycle = 0

        while not _SHUTDOWN:
            _cycle += 1
            cycle_start = utc_now()
            log.info("─── Cycle #%d | %s ───", _cycle, cycle_start.strftime("%H:%M:%S UTC"))

            try:
                equity = await broker.get_balance("USDT")
                risk_mgr.update_equity(equity)
            except BinanceAPIError as exc:
                log.warning("Balance fetch failed: %s", exc)

            today = utc_now().date()
            if today != _last_day:
                trade_logger.flush_daily_summary(equity)
                _last_day = today

            log.info(risk_mgr.summary())

            if not vol_filter.is_safe_to_trade():
                log.warning("Volatility filter active — skipping scan")
            else:
                try:
                    signals = await scanner.scan()
                except Exception as exc:
                    log.error("Scanner error: %s", exc)
                    signals = []

                for signal in signals:
                    if _SHUTDOWN:
                        break
                    await execute_signal(signal, broker, risk_mgr, trade_logger, positions)

            if not _SHUTDOWN:
                wait = seconds_until_next_candle(CRYPTO_SCAN_INTERVAL_SECONDS)
                log.info("Next scan in %s", format_duration(wait))
                try:
                    elapsed = 0.0
                    while not _SHUTDOWN and elapsed < wait:
                        await asyncio.sleep(1)
                        elapsed += 1
                except asyncio.CancelledError:
                    break

        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass

        log.info("Crypto bot shutting down …")
        try:
            final_equity = await broker.get_balance("USDT")
            trade_logger.flush_daily_summary(final_equity)
        except Exception:
            pass

    log.info("Goodbye.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import time as _time
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    while not _SHUTDOWN:
        try:
            asyncio.run(run_bot())
        except KeyboardInterrupt:
            log.info("Interrupted by user")
            break
        except Exception as exc:
            if _SHUTDOWN:
                break
            log.critical("Fatal error — restarting in 30s: %s", exc, exc_info=True)
            _time.sleep(30)


if __name__ == "__main__":
    main()
