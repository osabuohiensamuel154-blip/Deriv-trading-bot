"""
Risk management engine.
Every trade decision must pass through RiskManager.check() before execution.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from bot.config import (
    DAILY_LOSS_LIMIT_PCT,
    MAX_DAILY_TRADES,
    MAX_TRADES_PER_SYMBOL,
    RISK_PER_TRADE_MIN_PCT,
    RISK_PER_TRADE_MAX_PCT,
    CONSEC_LOSSES_PAUSE,
    CONSEC_LOSSES_STOP,
    PAUSE_DURATION_SECONDS,
)
from bot.strategies import Signal

log = logging.getLogger(__name__)


@dataclass
class RiskDecision:
    allowed:    bool
    stake:      float = 0.0
    reason:     str   = ""


@dataclass
class _DayState:
    date:               str   = ""
    starting_equity:    float = 0.0
    current_equity:     float = 0.0
    trades_today:       int   = 0
    trades_per_symbol:  Dict[str, int] = field(default_factory=dict)
    open_symbols:       List[str]      = field(default_factory=list)
    consecutive_losses: int   = 0
    day_stopped:        bool  = False
    pause_until:        float = 0.0    # unix timestamp


class RiskManager:
    """
    Stateful risk manager.  Call update_equity() each cycle and
    record_trade_result() after every closed trade.
    """

    def __init__(self) -> None:
        self._state = _DayState()

    # ------------------------------------------------------------------
    # Daily reset
    # ------------------------------------------------------------------

    def _today(self) -> str:
        from datetime import date
        return date.today().isoformat()

    def force_reset_day(self, equity: float) -> None:
        """Manually reset all daily counters (e.g. after a restart mid-day)."""
        today = self._today()
        log.info("Day counters force-reset for %s", today)
        self._state = _DayState(
            date=today,
            starting_equity=equity,
            current_equity=equity,
        )

    def _maybe_reset_day(self, equity: float) -> None:
        today = self._today()
        if self._state.date != today:
            log.info("New trading day: resetting risk counters")
            self._state = _DayState(
                date=today,
                starting_equity=equity,
                current_equity=equity,
            )

    # ------------------------------------------------------------------
    # Equity update
    # ------------------------------------------------------------------

    def update_equity(self, equity: float) -> None:
        self._maybe_reset_day(equity)
        self._state.current_equity = equity

    # ------------------------------------------------------------------
    # Position tracking
    # ------------------------------------------------------------------

    def register_open(self, symbol: str) -> None:
        if symbol not in self._state.open_symbols:
            self._state.open_symbols.append(symbol)

    def register_close(self, symbol: str) -> None:
        if symbol in self._state.open_symbols:
            self._state.open_symbols.remove(symbol)

    # ------------------------------------------------------------------
    # Trade result recording
    # ------------------------------------------------------------------

    def record_trade_result(self, symbol: str, profit: float) -> None:
        self.register_close(symbol)

        if profit < 0:
            self._state.consecutive_losses += 1
            log.warning("Consecutive losses: %d", self._state.consecutive_losses)

            if self._state.consecutive_losses >= CONSEC_LOSSES_STOP:
                self._state.day_stopped = True
                log.error("5 consecutive losses — trading halted for today")

            elif self._state.consecutive_losses >= CONSEC_LOSSES_PAUSE:
                resume_at = time.time() + PAUSE_DURATION_SECONDS
                self._state.pause_until = resume_at
                log.warning("3 consecutive losses — pausing for 2 hours")
        else:
            self._state.consecutive_losses = 0

    def record_trade_opened(self, symbol: str) -> None:
        self._state.trades_today += 1
        self._state.trades_per_symbol[symbol] = (
            self._state.trades_per_symbol.get(symbol, 0) + 1
        )
        self.register_open(symbol)

    # ------------------------------------------------------------------
    # Core check
    # ------------------------------------------------------------------

    def check(self, signal: Signal) -> RiskDecision:
        """
        Evaluate all risk rules for the given signal.
        Returns RiskDecision with allowed=True and computed stake if safe.
        """
        eq  = self._state.current_equity
        sym = signal.symbol

        # 1. Daily stop
        if self._state.day_stopped:
            return RiskDecision(False, reason="Day stopped: 5+ consecutive losses")

        # 2. Pause window
        if time.time() < self._state.pause_until:
            remaining = int(self._state.pause_until - time.time())
            return RiskDecision(False, reason=f"Paused after losses — resumes in {remaining}s")

        # 3. Daily loss limit
        if eq > 0:
            daily_loss = (self._state.starting_equity - eq) / self._state.starting_equity
            if daily_loss >= DAILY_LOSS_LIMIT_PCT:
                self._state.day_stopped = True
                return RiskDecision(False,
                    reason=f"Daily loss limit hit: {daily_loss*100:.2f}%")

        # 4. Max daily trades
        if self._state.trades_today >= MAX_DAILY_TRADES:
            return RiskDecision(False,
                reason=f"Max daily trades reached ({MAX_DAILY_TRADES})")

        # 5. Max trades per symbol
        sym_count = self._state.trades_per_symbol.get(sym, 0)
        if sym_count >= MAX_TRADES_PER_SYMBOL:
            return RiskDecision(False,
                reason=f"Max trades for {sym} reached ({MAX_TRADES_PER_SYMBOL})")

        # 6. No duplicate open position on same symbol
        if sym in self._state.open_symbols:
            return RiskDecision(False,
                reason=f"Position already open on {sym}")

        # 7. Compute stake
        stake = self._compute_stake(signal, eq)
        if stake <= 0:
            return RiskDecision(False, reason="Computed stake is zero")

        return RiskDecision(True, stake=stake,
                            reason=f"OK — stake={stake:.2f}")

    # ------------------------------------------------------------------
    # Dynamic position sizing
    # ------------------------------------------------------------------

    def _compute_stake(self, signal: Signal, equity: float) -> float:
        """
        Risk a fixed percentage of equity, scaled by SL distance.
        Clamps between RISK_PER_TRADE_MIN_PCT and RISK_PER_TRADE_MAX_PCT.
        """
        if equity <= 0:
            return 0.0

        sl_distance = abs(signal.entry - signal.stop_loss)
        if sl_distance == 0:
            return 0.0

        risk_amount = equity * RISK_PER_TRADE_MAX_PCT   # start at upper bound

        # Proportional stake for multiplier contracts: stake = risk_amount
        # (For multipliers the stake IS the maximum loss, so we just cap it.)
        stake = min(risk_amount, equity * RISK_PER_TRADE_MAX_PCT)
        stake = max(stake, equity * RISK_PER_TRADE_MIN_PCT)

        return round(stake, 2)

    # ------------------------------------------------------------------
    # Status summary (useful for logging)
    # ------------------------------------------------------------------

    def summary(self) -> str:
        s = self._state
        daily_loss_pct = 0.0
        if s.starting_equity > 0:
            daily_loss_pct = (s.starting_equity - s.current_equity) / s.starting_equity * 100

        return (
            f"[RiskMgr] date={s.date} | equity={s.current_equity:.2f} "
            f"| daily_loss={daily_loss_pct:.2f}% "
            f"| trades={s.trades_today}/{MAX_DAILY_TRADES} "
            f"| consec_losses={s.consecutive_losses} "
            f"| open={s.open_symbols} "
            f"| day_stopped={s.day_stopped}"
        )


# ---------------------------------------------------------------------------
# Placeholder: News / Volatility Filter
# ---------------------------------------------------------------------------

class VolatilityFilter:
    """
    Placeholder for filtering high-impact news / volatility events.
    Connect this to an economic calendar API (e.g. Forex Factory, Investing.com)
    and set _high_impact_active = True during restricted windows.
    """

    def __init__(self) -> None:
        self._high_impact_active: bool = False

    def is_safe_to_trade(self) -> bool:
        """Returns False when high-impact conditions are detected."""
        return not self._high_impact_active

    def set_high_impact(self, active: bool) -> None:
        self._high_impact_active = active
        if active:
            log.warning("VolatilityFilter: high-impact event active — trades blocked")
