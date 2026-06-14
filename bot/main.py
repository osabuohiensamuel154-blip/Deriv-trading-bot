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


_shutdown_count = 0

def _handle_signal(signum: int, frame: object) -> None:
    global _SHUTDOWN, _shutdown_count
    _shutdown_count += 1
    if _shutdown_count == 1:
        log.warning("Ctrl+C received — stopping cleanly. Press Ctrl+C again to force quit.")
        _SHUTDOWN = True
    else:
        log.warning("Force quit.")
        import os
        os._exit(1)


# ---------------------------------------------------------------------------
# Trade execution pipeline
# ---------------------------------------------------------------------------

async def execute_signal(
    signal: Signal,
    broker: DerivBroker,
    risk_mgr: RiskManager,
    trade_logger: TradeLogger,
    session_contract_ids: set,
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
    session_contract_ids.add(str(contract_id))   # track for reconciliation
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
    session_contract_ids: set,
) -> set:
    """
    Check only contracts opened in this session. Log any that have now closed.
    Ignores all pre-existing trade history.
    Returns updated set of known IDs.
    """
    if not session_contract_ids:
        return known_contract_ids

    try:
        history = await broker.get_trade_history(limit=50)
    except DerivAPIError as exc:
        log.warning("Could not fetch trade history: %s", exc)
        return known_contract_ids

    for tx in history:
        cid = str(tx.get("contract_id", ""))

        # Only process contracts opened in this session, not old history
        if cid not in session_contract_ids:
            continue
        if cid in known_contract_ids:
            continue

        shortcode  = tx.get("shortcode", "")
        buy_price  = float(tx.get("buy_price", 0) or 0)
        sell_price = float(tx.get("sell_price", 0) or 0)

        # sell_price=0 means trade is still OPEN — skip it, don't record as loss
        if sell_price <= 0:
            continue

        profit = sell_price - buy_price

        # Parse symbol from shortcode e.g. "MULTUP_R_75_..." → "R_75"
        parts = shortcode.split("_", 1)
        raw_sym = parts[1] if len(parts) > 1 else shortcode
        # Map back to friendly name
        from bot.config import SYMBOLS
        sym_map = {v: k for k, v in SYMBOLS.items()}
        symbol = sym_map.get(raw_sym.split("_")[0] + "_" + raw_sym.split("_")[1]
                             if "_" in raw_sym else raw_sym, raw_sym)

        direction = "BUY" if "MULTUP" in shortcode else "SELL"

        trade_logger.log_trade(
            symbol      = symbol,
            strategy    = "BOT",
            direction   = direction,
            entry_price = buy_price,
            exit_price  = sell_price,
            stop_loss   = 0.0,
            take_profit = 0.0,
            stake       = buy_price,
            profit      = round(profit, 2),
            score       = 0.0,
            note        = "reconciled",
        )
        risk_mgr.record_trade_result(symbol, profit)
        known_contract_ids.add(cid)
        log.info("Reconciled closed trade: %s %s P&L=%.2f", symbol, direction, profit)

    return known_contract_ids


# ---------------------------------------------------------------------------
# Background position monitor — runs every 60 s independent of scan cycle
# ---------------------------------------------------------------------------

async def position_monitor(broker: DerivBroker, risk_mgr: RiskManager) -> None:
    """
    Polls Deriv every 60 seconds for actual open positions.
    Clears any symbol the risk manager thinks is open but Deriv no longer shows.
    This ensures the bot reacts within 1 minute of a position closing.
    """
    from bot.config import SYMBOLS
    sym_map = {v: k for k, v in SYMBOLS.items()}

    while not _SHUTDOWN:
        await asyncio.sleep(60)
        try:
            live_positions = await broker.get_open_positions()
            live_symbols = {
                sym_map.get(p.get("symbol", ""), p.get("symbol", ""))
                for p in live_positions
            }
            for sym in list(risk_mgr._state.open_symbols):
                if sym not in live_symbols:
                    log.info("Position monitor: %s closed on Deriv — clearing state", sym)
                    risk_mgr.record_trade_result(sym, 0.0)
        except Exception as exc:
            log.debug("Position monitor error (will retry): %s", exc)


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

        # Seed equity and force-reset day counters on every startup
        equity = await broker.get_balance()
        risk_mgr.force_reset_day(equity)
        trade_logger.set_equity_start(equity)
        log.info("Starting equity: %.2f", equity)

        scanner = MarketScanner(broker)

        # Sync open positions from Deriv so the risk manager is accurate
        # even after a mid-session restart
        try:
            from bot.config import SYMBOLS
            sym_map = {v: k for k, v in SYMBOLS.items()}
            open_positions = await broker.get_open_positions()
            for pos in open_positions:
                raw = pos.get("symbol", "")
                sym = sym_map.get(raw, raw)
                risk_mgr.register_open(sym)
                log.info("Restored open position from Deriv: %s", sym)
        except Exception as exc:
            log.warning("Could not sync open positions on startup: %s", exc)

        # Pre-populate known_ids with existing trade history so the
        # reconciler only logs trades opened by THIS session, not old ones.
        try:
            existing = await broker.get_trade_history(limit=50)
            known_ids: set = {str(t.get("contract_id", "")) for t in existing}
            log.info("Pre-loaded %d existing trade IDs (will not be re-logged)", len(known_ids))
        except Exception:
            known_ids: set = set()

        # Track contract IDs opened in this session for reconciliation
        session_contract_ids: set = set()

        # Start background position monitor (checks every 60 s)
        monitor_task = asyncio.create_task(position_monitor(broker, risk_mgr))
        log.info("Position monitor started (60s interval)")

        _last_day = utc_now().date()
        _cycle = 0

        while not _SHUTDOWN:
            _cycle += 1
            cycle_start = utc_now()
            log.info("─── Cycle #%d | %s ───", _cycle, cycle_start.strftime("%H:%M:%S UTC"))

            # ── Update equity & risk state (re-auth if session expired) ────
            try:
                equity = await broker.get_balance()
                risk_mgr.update_equity(equity)
            except DerivAPIError as exc:
                if "log in" in str(exc).lower() or "token" in str(exc).lower():
                    log.warning("Session expired — re-authorizing …")
                    try:
                        await broker.authorize()
                        equity = await broker.get_balance()
                        risk_mgr.update_equity(equity)
                        log.info("Re-authorized. Equity: %.2f", equity)
                    except DerivAPIError as reauth_exc:
                        log.error("Re-auth failed: %s", reauth_exc)
                else:
                    log.warning("Balance fetch failed: %s", exc)

            # ── Daily roll-over ─────────────────────────────────────────
            today = utc_now().date()
            if today != _last_day:
                summary = trade_logger.flush_daily_summary(equity)
                _last_day = today

            log.info(risk_mgr.summary())

            # ── Sync open positions with Deriv (catches externally closed trades) ─
            try:
                from bot.config import SYMBOLS
                sym_map = {v: k for k, v in SYMBOLS.items()}
                live_positions = await broker.get_open_positions()
                live_symbols = {
                    sym_map.get(p.get("symbol", ""), p.get("symbol", ""))
                    for p in live_positions
                }
                # Any symbol the risk manager thinks is open but Deriv says isn't
                for sym in list(risk_mgr._state.open_symbols):
                    if sym not in live_symbols:
                        log.info("Position closed externally: %s — updating state", sym)
                        risk_mgr.record_trade_result(sym, 0.0)
            except Exception as exc:
                log.warning("Position sync failed: %s", exc)

            # ── Ensure session is alive before scanning ─────────────────
            try:
                await broker.ensure_authorized()
            except DerivAPIError as exc:
                log.error("Cannot re-authorize: %s — skipping cycle", exc)
                continue

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
                    await execute_signal(signal, broker, risk_mgr, trade_logger,
                                         session_contract_ids)

                # ── Reconcile broker history ────────────────────────────
                known_ids = await reconcile_closed_trades(
                    broker, risk_mgr, trade_logger, known_ids, session_contract_ids
                )

            # ── Sleep until next M15 candle (interruptible in 1s chunks) ──
            if not _SHUTDOWN:
                wait = seconds_until_next_candle(SCAN_INTERVAL_SECONDS)
                log.info("Next scan in %s", format_duration(wait))
                try:
                    elapsed = 0.0
                    while not _SHUTDOWN and elapsed < wait:
                        await asyncio.sleep(1)
                        elapsed += 1
                except asyncio.CancelledError:
                    break

    # ── Shutdown ──────────────────────────────────────────────────────────
    monitor_task.cancel()
    try:
        await monitor_task
    except asyncio.CancelledError:
        pass
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
