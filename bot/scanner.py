"""
Multi-symbol market scanner.
Fetches candles for all configured instruments every cycle and produces scored signals.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from bot.config import (
    SYMBOLS, TREND_SYMBOLS, REVERSAL_SYMBOLS,
    TIMEFRAME_M15, TIMEFRAME_M30, CANDLE_COUNT,
    MIN_SIGNAL_SCORE,
)
from bot.strategies import CandleData, Signal, TrendStrategy, ReversalStrategy

log = logging.getLogger(__name__)


class MarketScanner:
    """
    Scans all configured instruments simultaneously.
    Runs the appropriate strategy per symbol and returns qualified signals.

    Defaults to the Deriv SYMBOLS/TREND_SYMBOLS/REVERSAL_SYMBOLS, but accepts
    overrides so the same scanner works for other brokers (e.g. crypto).
    """

    def __init__(self, broker: Any,
                 symbols: Optional[Dict[str, str]] = None,
                 trend_symbols: Optional[List[str]] = None,
                 reversal_symbols: Optional[List[str]] = None) -> None:
        self._broker = broker
        self._symbols          = symbols          if symbols          is not None else SYMBOLS
        self._trend_symbols    = trend_symbols    if trend_symbols    is not None else TREND_SYMBOLS
        self._reversal_symbols = reversal_symbols if reversal_symbols is not None else REVERSAL_SYMBOLS
        self._trend_strat    = TrendStrategy()
        self._reversal_strat = ReversalStrategy()

    # ------------------------------------------------------------------
    # Candle fetching
    # ------------------------------------------------------------------

    async def _fetch_candles(self, symbol: str, granularity: int) -> Optional[CandleData]:
        broker_symbol = self._symbols[symbol]
        try:
            data = await self._broker.get_candles(broker_symbol, granularity, CANDLE_COUNT)
            log.debug("Fetched %d candles for %s @ %ds", len(data), symbol, granularity)
            return data
        except Exception as exc:
            log.error("Failed to fetch candles for %s: %s", symbol, exc)
            return None

    # ------------------------------------------------------------------
    # Per-symbol analysis
    # ------------------------------------------------------------------

    async def _analyze_symbol(self, symbol: str) -> Optional[Signal]:
        # Fetch both timeframes concurrently
        m15_task = asyncio.create_task(self._fetch_candles(symbol, TIMEFRAME_M15))
        m30_task = asyncio.create_task(self._fetch_candles(symbol, TIMEFRAME_M30))
        m15_data, m30_data = await asyncio.gather(m15_task, m30_task)

        if m15_data is None or m30_data is None:
            return None

        if symbol in self._trend_symbols:
            signal = self._trend_strat.analyze(symbol, m15_data, m30_data)
        elif symbol in self._reversal_symbols:
            signal = self._reversal_strat.analyze(symbol, m15_data, m30_data)
        else:
            log.warning("Unknown symbol category: %s", symbol)
            return None

        if signal and signal.score >= MIN_SIGNAL_SCORE:
            log.info(
                "Signal qualified: %s %s %s score=%.1f",
                signal.symbol, signal.strategy, signal.direction, signal.score,
            )
            return signal

        return None

    # ------------------------------------------------------------------
    # Full scan
    # ------------------------------------------------------------------

    async def scan(self) -> List[Signal]:
        """
        Run a full scan across all 5 instruments concurrently.
        Returns list of qualifying signals sorted by score descending.
        """
        log.info("=== Market scan started ===")
        tasks = [
            asyncio.create_task(self._analyze_symbol(sym))
            for sym in self._symbols.keys()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        signals: List[Signal] = []
        for sym, result in zip(self._symbols.keys(), results):
            if isinstance(result, Exception):
                log.error("Scan error for %s: %s", sym, result)
            elif result is not None:
                signals.append(result)

        signals.sort(key=lambda s: s.score, reverse=True)
        log.info("=== Scan complete: %d qualifying signal(s) ===", len(signals))
        return signals
