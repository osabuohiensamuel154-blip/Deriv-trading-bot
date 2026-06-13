"""
Trade logging and performance analytics.
Writes to CSV files and provides daily summary statistics.
"""

from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, List, Optional

from bot.config import TRADE_LOG_FILE, PERF_LOG_FILE, APP_LOG_FILE, LOG_DIR

log = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    timestamp:    str
    symbol:       str
    strategy:     str
    direction:    str
    entry_price:  float
    exit_price:   float
    stop_loss:    float
    take_profit:  float
    stake:        float
    profit:       float
    win:          bool
    win_streak:   int
    loss_streak:  int
    score:        float
    note:         str = ""


@dataclass
class DailyPerformance:
    date:         str
    total_trades: int
    wins:         int
    losses:       int
    total_profit: float
    win_rate:     float
    max_win:      float
    max_loss:     float
    equity_start: float
    equity_end:   float


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

_TRADE_FIELDS = list(TradeRecord.__dataclass_fields__.keys())
_PERF_FIELDS  = list(DailyPerformance.__dataclass_fields__.keys())


def _ensure_dir() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)


def _write_row(filepath: str, fields: list, row: dict) -> None:
    _ensure_dir()
    file_exists = os.path.isfile(filepath)
    with open(filepath, "a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


# ---------------------------------------------------------------------------
# TradeLogger
# ---------------------------------------------------------------------------

class TradeLogger:
    """
    Records every trade and maintains running win/loss streaks.
    Generates daily performance summaries.
    """

    def __init__(self) -> None:
        self._win_streak:  int = 0
        self._loss_streak: int = 0
        self._daily_records: List[TradeRecord] = []
        self._equity_start: float = 0.0

    def set_equity_start(self, equity: float) -> None:
        self._equity_start = equity

    # ------------------------------------------------------------------
    # Log a completed trade
    # ------------------------------------------------------------------

    def log_trade(
        self,
        symbol:      str,
        strategy:    str,
        direction:   str,
        entry_price: float,
        exit_price:  float,
        stop_loss:   float,
        take_profit: float,
        stake:       float,
        profit:      float,
        score:       float,
        note:        str = "",
    ) -> TradeRecord:
        won = profit > 0

        if won:
            self._win_streak += 1
            self._loss_streak = 0
        else:
            self._loss_streak += 1
            self._win_streak  = 0

        record = TradeRecord(
            timestamp   = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            symbol      = symbol,
            strategy    = strategy,
            direction   = direction,
            entry_price = round(entry_price, 5),
            exit_price  = round(exit_price, 5),
            stop_loss   = round(stop_loss, 5),
            take_profit = round(take_profit, 5),
            stake       = round(stake, 2),
            profit      = round(profit, 2),
            win         = won,
            win_streak  = self._win_streak,
            loss_streak = self._loss_streak,
            score       = round(score, 1),
            note        = note,
        )

        _write_row(TRADE_LOG_FILE, _TRADE_FIELDS, asdict(record))
        self._daily_records.append(record)

        log.info(
            "TRADE LOG | %s %s %s | entry=%.5f exit=%.5f | P&L=%.2f | %s",
            symbol, strategy, direction,
            entry_price, exit_price, profit,
            "WIN" if won else "LOSS",
        )
        return record

    # ------------------------------------------------------------------
    # Daily summary
    # ------------------------------------------------------------------

    def flush_daily_summary(self, equity_end: float) -> Optional[DailyPerformance]:
        if not self._daily_records:
            return None

        wins   = sum(1 for r in self._daily_records if r.win)
        losses = len(self._daily_records) - wins
        profits = [r.profit for r in self._daily_records]

        perf = DailyPerformance(
            date         = datetime.utcnow().strftime("%Y-%m-%d"),
            total_trades = len(self._daily_records),
            wins         = wins,
            losses       = losses,
            total_profit = round(sum(profits), 2),
            win_rate     = round(wins / len(self._daily_records) * 100, 1),
            max_win      = round(max(profits), 2),
            max_loss     = round(min(profits), 2),
            equity_start = round(self._equity_start, 2),
            equity_end   = round(equity_end, 2),
        )

        _write_row(PERF_LOG_FILE, _PERF_FIELDS, asdict(perf))
        self._daily_records.clear()

        log.info(
            "DAILY SUMMARY | trades=%d wins=%d losses=%d net=%.2f win_rate=%.1f%%",
            perf.total_trades, wins, losses, perf.total_profit, perf.win_rate,
        )
        return perf

    # ------------------------------------------------------------------
    # Current streak info
    # ------------------------------------------------------------------

    @property
    def win_streak(self) -> int:
        return self._win_streak

    @property
    def loss_streak(self) -> int:
        return self._loss_streak


# ---------------------------------------------------------------------------
# Application-level logging setup
# ---------------------------------------------------------------------------

def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logger → console + rotating file."""
    import logging.handlers

    _ensure_dir()

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # Rotating file handler (10 MB × 5 backups)
    fh = logging.handlers.RotatingFileHandler(
        APP_LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5
    )
    fh.setLevel(level)
    fh.setFormatter(fmt)
    root.addHandler(fh)
