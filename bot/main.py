"""
Main entry point — orchestrates the infinite trading loop.
Run:  python -m bot.main
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import datetime

# Load .env before any config import reads os.getenv()
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv optional; set env vars manually if not installed

from bot.broker_deriv import DerivBroker, DerivAPIError
from bot.config import SCAN_INTERVAL_SECONDS, SYMBOLS
from bot.logger import TradeLogger, setup_logging
from bot.risk_manager import RiskManager, VolatilityFilter
from bot.scanner import MarketScanner
from bot.strategies import Signal
from bot.utils import (
    format_duration, print_banner, seconds_until_next_candle, utc_now
)

setup_logging()
log = logging.getLogger(__name__)

_SHUTDOWN = False


def _handle_signal(signum: int, frame: object) -> None:
    global _SHUTDOWN
    log.warning("Shutdown signal received (%s) — stopping after current cycle", signum)
    _SHUTDOWN = True


# ---------------------------------------------------------------------------
# Trade execution pipeline
# ---------------------------------------------------------------------------

async def execute_signal(
    signal: Signal,
    broker: DerivBroker,
    risk_mgr: RiskManager,
    trade_logger: TradeLogger,
) -> None:
    """Run risk checks and execute a single trade."""
    decision = risk_mgr.check(signal)

    if not decision.allowed:
        log.info("Trade BLOCKED [%s %s]: %s", signal.symbol, signal.direction, decision.reason)
        return

    log.info(
        "Executing trade | %s %s %s | score=%.1f stake=%.2f",
        signal.symbol, signal.strategy, signal.direction,
        signal.score, decision.stake,
    )

    try:
        result = await broker.place_trade(signal, decision.stake)
    except DerivAPIError as exc:
        log.error("Trade placement failed for %s: %s", signal.symbol, exc)
        return

    contract_id = result.get("contract_id")
    if not contract_id:
        log.error("No contract_id returned — trade may not have been placed")
        return

    risk_mgr.record_trade_opened(signal.symbol)
    log.info("Trade active | contract_id=%s | SL=%.5f TP=%.5f",
             contract_id, signal.stop_loss, signal.take_profit)

    # NOTE: For multiplier contracts Deriv manages the SL/TP automatically.
    # The position will be closed by the exchange; we reconcile results
    # in the next cycle via get_trade_history().


# ---------------------------------------------------------------------------
# Reconcile closed trades from the broker's history
# ---------------------------------------------------------------------------

async def reconcile_closed_trades(
    broker: DerivBroker,
    risk_mgr: RiskManager,
    trade_logger: TradeLogger,
    known_contract_ids: set,
) -> set:
    """
    Compare the broker's recent profit_table against known open contracts.
    Record newly closed trades in the logger and risk manager.
    Returns updated set of known IDs.
    """
    try:
        history = await broker.get_trade_history(limit=20)
    except DerivAPIError as exc:
        log.warning("Could not fetch trade history: %s", exc)
        return known_contract_ids

    for tx in history:
        cid = str(tx.get("contract_id", ""))
        if cid in known_contract_ids:
            continue

        profit = float(tx.get("profit", 0))
        buy_price = float(tx.get("buy_price", 0))
        sell_price = float(tx.get("sell_price", 0))

        # We don't have full signal data here — log what we have
        trade_logger.log_trade(
            symbol      = tx.get("shortcode", "UNKNOWN").split("_")[0],
            strategy    = "UNKNOWN",
            direction   = "BUY" if "MULTUP" in tx.get("shortcode", "") else "SELL",
            entry_price = buy_price,
            exit_price  = sell_price,
            stop_loss   = 0.0,
            take_profit = 0.0,
            stake       = buy_price,
            profit      = profit,
            score       = 0.0,
            note        = "reconciled",
        )
        risk_mgr.record_trade_result("RECONCILED", profit)
        known_contract_ids.add(cid)

    return known_contract_ids


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def run_bot() -> None:
    global _SHUTDOWN

    print_banner()
    log.info("Bot starting up …")

    risk_mgr     = RiskManager()
    vol_filter   = VolatilityFilter()
    trade_logger = TradeLogger()
    known_ids: set = set()

    async with DerivBroker() as broker:
        # Authenticate
        account = await broker.authorize()
        log.info("Account: %s | Currency: %s", account.get("loginid"), account.get("currency"))

        # Seed equity
        equity = await broker.get_balance()
        risk_mgr.update_equity(equity)
        trade_logger.set_equity_start(equity)
        log.info("Starting equity: %.2f", equity)

        scanner = MarketScanner(broker)

        _last_day = utc_now().date()
        _cycle = 0

        while not _SHUTDOWN:
            _cycle += 1
            cycle_start = utc_now()
            log.info("─── Cycle #%d | %s ───", _cycle, cycle_start.strftime("%H:%M:%S UTC"))

            # ── Update equity & risk state ──────────────────────────────
            try:
                equity = await broker.get_balance()
                risk_mgr.update_equity(equity)
            except DerivAPIError as exc:
                log.warning("Balance fetch failed: %s", exc)

            # ── Daily roll-over ─────────────────────────────────────────
            today = utc_now().date()
            if today != _last_day:
                summary = trade_logger.flush_daily_summary(equity)
                _last_day = today

            log.info(risk_mgr.summary())

            # ── Volatility filter ───────────────────────────────────────
            if not vol_filter.is_safe_to_trade():
                log.warning("Volatility filter active — skipping scan")
            else:
                # ── Scan markets ────────────────────────────────────────
                try:
                    signals = await scanner.scan()
                except Exception as exc:
                    log.error("Scanner error: %s", exc)
                    signals = []

                # ── Execute qualifying signals ──────────────────────────
                for signal in signals:
                    if _SHUTDOWN:
                        break
                    await execute_signal(signal, broker, risk_mgr, trade_logger)

                # ── Reconcile broker history ────────────────────────────
                known_ids = await reconcile_closed_trades(
                    broker, risk_mgr, trade_logger, known_ids
                )

            # ── Sleep until next M15 candle ─────────────────────────────
            if not _SHUTDOWN:
                wait = seconds_until_next_candle(SCAN_INTERVAL_SECONDS)
                log.info("Next scan in %s", format_duration(wait))
                try:
                    await asyncio.sleep(wait)
                except asyncio.CancelledError:
                    break

    # ── Shutdown ──────────────────────────────────────────────────────────
    log.info("Bot shutting down …")
    try:
        final_equity = await broker.get_balance()
        trade_logger.flush_daily_summary(final_equity)
    except Exception:
        pass
    log.info("Goodbye.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        log.info("Interrupted by user")
    except Exception as exc:
        log.critical("Fatal error: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
