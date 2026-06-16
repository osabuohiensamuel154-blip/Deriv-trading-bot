"""
Binance USDT-M Futures broker module via ccxt.
Handles authentication, candle fetching, leverage/margin setup, and
market order execution for both long and short positions.

Isolated margin mode is used per symbol so the maximum possible loss on
any position is capped at the margin committed to it — the same
"stake = max loss" guarantee used for Deriv multiplier contracts.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import ccxt.async_support as ccxt
import numpy as np

from bot.config import (
    BINANCE_API_KEY, BINANCE_API_SECRET, BINANCE_TESTNET,
    BINANCE_MARGIN_MODE, BINANCE_LEVERAGE,
)
from bot.strategies import CandleData

log = logging.getLogger(__name__)

_GRANULARITY_TO_TIMEFRAME = {
    900:  "15m",
    1800: "30m",
}


class BinanceAPIError(Exception):
    """Raised when the Binance API returns an error."""


class BinanceBroker:
    """
    Async broker for Binance USDT-M Futures via ccxt.

    Usage pattern:
        async with BinanceBroker() as broker:
            balance = await broker.get_balance()
    """

    def __init__(self) -> None:
        self._exchange: Optional[ccxt.binanceusdm] = None
        self._leverage_set: set = set()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "BinanceBroker":
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.disconnect()

    async def connect(self) -> None:
        if not BINANCE_API_KEY or not BINANCE_API_SECRET:
            raise BinanceAPIError(
                "BINANCE_API_KEY / BINANCE_API_SECRET is not set. Check your .env file."
            )

        self._exchange = ccxt.binanceusdm({
            "apiKey": BINANCE_API_KEY,
            "secret": BINANCE_API_SECRET,
            "enableRateLimit": True,
        })
        if BINANCE_TESTNET:
            self._exchange.set_sandbox_mode(True)

        await self._exchange.load_markets()
        log.info("Connected to Binance Futures%s", " (TESTNET)" if BINANCE_TESTNET else " (LIVE)")

    async def disconnect(self) -> None:
        if self._exchange:
            await self._exchange.close()
        log.info("Binance connection closed")

    # ------------------------------------------------------------------
    # Leverage / margin setup (once per symbol)
    # ------------------------------------------------------------------

    async def _ensure_leverage(self, symbol: str) -> None:
        if symbol in self._leverage_set:
            return
        try:
            await self._exchange.set_margin_mode(BINANCE_MARGIN_MODE.lower(), symbol)
        except Exception as exc:
            # "No need to change margin type" is raised when already set — harmless
            if "no need to change" not in str(exc).lower():
                log.warning("Could not set margin mode for %s: %s", symbol, exc)
        try:
            await self._exchange.set_leverage(BINANCE_LEVERAGE, symbol)
        except Exception as exc:
            log.warning("Could not set leverage for %s: %s", symbol, exc)
        self._leverage_set.add(symbol)

    # ------------------------------------------------------------------
    # Account info
    # ------------------------------------------------------------------

    async def get_balance(self, asset: str = "USDT") -> float:
        try:
            bal = await self._exchange.fetch_balance()
        except Exception as exc:
            raise BinanceAPIError(str(exc)) from exc
        return float(bal.get("free", {}).get(asset, 0.0))

    async def get_open_positions(self) -> List[Dict]:
        """Returns non-zero futures positions currently open on the exchange."""
        try:
            positions = await self._exchange.fetch_positions()
        except Exception as exc:
            raise BinanceAPIError(str(exc)) from exc
        return [p for p in positions if float(p.get("contracts") or 0) != 0]

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    async def get_candles(self, symbol: str, granularity: int, count: int) -> CandleData:
        timeframe = _GRANULARITY_TO_TIMEFRAME.get(granularity, f"{granularity // 60}m")
        try:
            ohlcv = await self._exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=count)
        except Exception as exc:
            raise BinanceAPIError(str(exc)) from exc

        if not ohlcv:
            raise BinanceAPIError(f"No candles returned for {symbol}")

        arr = np.array(ohlcv, dtype=float)
        return CandleData(
            open  = arr[:, 1],
            high  = arr[:, 2],
            low   = arr[:, 3],
            close = arr[:, 4],
            epoch = (arr[:, 0] / 1000).astype(int),
        )

    async def get_price(self, symbol: str) -> float:
        try:
            ticker = await self._exchange.fetch_ticker(symbol)
        except Exception as exc:
            raise BinanceAPIError(str(exc)) from exc
        return float(ticker["last"])

    # ------------------------------------------------------------------
    # Trade execution (futures, long + short)
    # ------------------------------------------------------------------

    async def open_long(self, symbol: str, margin_usdt: float, leverage: int = BINANCE_LEVERAGE) -> Dict:
        """Open a long position, committing `margin_usdt` as isolated margin."""
        await self._ensure_leverage(symbol)
        try:
            price  = await self.get_price(symbol)
            amount = float(self._exchange.amount_to_precision(symbol, margin_usdt * leverage / price))
            log.info("Open LONG %s | margin=%.2f leverage=%dx amount=%s @ ~%.4f",
                      symbol, margin_usdt, leverage, amount, price)
            order = await self._exchange.create_market_buy_order(symbol, amount)
            return order
        except Exception as exc:
            raise BinanceAPIError(str(exc)) from exc

    async def open_short(self, symbol: str, margin_usdt: float, leverage: int = BINANCE_LEVERAGE) -> Dict:
        """Open a short position, committing `margin_usdt` as isolated margin."""
        await self._ensure_leverage(symbol)
        try:
            price  = await self.get_price(symbol)
            amount = float(self._exchange.amount_to_precision(symbol, margin_usdt * leverage / price))
            log.info("Open SHORT %s | margin=%.2f leverage=%dx amount=%s @ ~%.4f",
                      symbol, margin_usdt, leverage, amount, price)
            order = await self._exchange.create_market_sell_order(symbol, amount)
            return order
        except Exception as exc:
            raise BinanceAPIError(str(exc)) from exc

    async def close_long(self, symbol: str, amount: float) -> Dict:
        """Close an open long position by selling `amount` units of the contract."""
        try:
            amount = float(self._exchange.amount_to_precision(symbol, amount))
            log.info("Close LONG %s | amount=%s", symbol, amount)
            order = await self._exchange.create_market_sell_order(
                symbol, amount, params={"reduceOnly": True}
            )
            return order
        except Exception as exc:
            raise BinanceAPIError(str(exc)) from exc

    async def close_short(self, symbol: str, amount: float) -> Dict:
        """Close an open short position by buying back `amount` units of the contract."""
        try:
            amount = float(self._exchange.amount_to_precision(symbol, amount))
            log.info("Close SHORT %s | amount=%s", symbol, amount)
            order = await self._exchange.create_market_buy_order(
                symbol, amount, params={"reduceOnly": True}
            )
            return order
        except Exception as exc:
            raise BinanceAPIError(str(exc)) from exc
