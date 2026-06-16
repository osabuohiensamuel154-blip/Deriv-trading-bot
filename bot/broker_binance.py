"""
Binance Spot broker module via ccxt.
Handles authentication, candle fetching, and spot market order execution.

Spot trading is long-only: BUY opens a position, SELL closes one.
There is no native "open position" concept (just account balances), so
position tracking (entry/SL/TP) is managed by the caller (bot/main_crypto.py).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import ccxt.async_support as ccxt
import numpy as np

from bot.config import BINANCE_API_KEY, BINANCE_API_SECRET, BINANCE_TESTNET
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
    Async broker for Binance Spot via ccxt.

    Usage pattern:
        async with BinanceBroker() as broker:
            balance = await broker.get_balance()
    """

    def __init__(self) -> None:
        self._exchange: Optional[ccxt.binance] = None

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

        self._exchange = ccxt.binance({
            "apiKey": BINANCE_API_KEY,
            "secret": BINANCE_API_SECRET,
            "enableRateLimit": True,
        })
        if BINANCE_TESTNET:
            self._exchange.set_sandbox_mode(True)

        await self._exchange.load_markets()
        log.info("Connected to Binance%s", " (TESTNET)" if BINANCE_TESTNET else " (LIVE)")

    async def disconnect(self) -> None:
        if self._exchange:
            await self._exchange.close()
        log.info("Binance connection closed")

    # ------------------------------------------------------------------
    # Account info
    # ------------------------------------------------------------------

    async def get_balance(self, asset: str = "USDT") -> float:
        try:
            bal = await self._exchange.fetch_balance()
        except Exception as exc:
            raise BinanceAPIError(str(exc)) from exc
        return float(bal.get("free", {}).get(asset, 0.0))

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
    # Trade execution (spot, long-only)
    # ------------------------------------------------------------------

    async def market_buy(self, symbol: str, quote_amount: float) -> Dict:
        """Spend `quote_amount` of quote currency (e.g. USDT) buying `symbol`."""
        try:
            price  = await self.get_price(symbol)
            amount = float(self._exchange.amount_to_precision(symbol, quote_amount / price))
            log.info("Market BUY %s | quote=%.2f amount=%s @ ~%.4f",
                      symbol, quote_amount, amount, price)
            order = await self._exchange.create_market_buy_order(symbol, amount)
            return order
        except Exception as exc:
            raise BinanceAPIError(str(exc)) from exc

    async def market_sell(self, symbol: str, base_amount: float) -> Dict:
        """Sell `base_amount` units of the base asset (e.g. BTC in BTC/USDT)."""
        try:
            amount = float(self._exchange.amount_to_precision(symbol, base_amount))
            log.info("Market SELL %s | amount=%s", symbol, amount)
            order = await self._exchange.create_market_sell_order(symbol, amount)
            return order
        except Exception as exc:
            raise BinanceAPIError(str(exc)) from exc

    async def get_asset_balance(self, asset: str) -> float:
        try:
            bal = await self._exchange.fetch_balance()
        except Exception as exc:
            raise BinanceAPIError(str(exc)) from exc
        return float(bal.get("free", {}).get(asset, 0.0))
