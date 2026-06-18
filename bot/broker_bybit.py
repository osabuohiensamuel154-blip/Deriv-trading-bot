"""
Bybit USDT Linear Perpetuals broker module via ccxt.
Supports both long and short positions with isolated margin.

Isolated margin caps the maximum loss on any position at the margin
committed to it — the same "stake = max loss" guarantee used for
Deriv multiplier contracts.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import ccxt.async_support as ccxt
import numpy as np

from bot.config import (
    BYBIT_API_KEY, BYBIT_API_SECRET, BYBIT_TESTNET, BYBIT_DEMO,
    BYBIT_MARGIN_MODE, BYBIT_LEVERAGE,
)
from bot.strategies import CandleData

log = logging.getLogger(__name__)

_GRANULARITY_TO_TIMEFRAME = {
    900:  "15m",
    1800: "30m",
}


class BybitAPIError(Exception):
    """Raised when the Bybit API returns an error."""


class BybitBroker:
    """
    Async broker for Bybit USDT Linear Perpetuals via ccxt.

    Usage pattern:
        async with BybitBroker() as broker:
            balance = await broker.get_balance()
    """

    def __init__(self) -> None:
        self._exchange: Optional[ccxt.bybit] = None
        self._leverage_set: set = set()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "BybitBroker":
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.disconnect()

    async def connect(self) -> None:
        if not BYBIT_API_KEY or not BYBIT_API_SECRET:
            raise BybitAPIError(
                "BYBIT_API_KEY / BYBIT_API_SECRET is not set. Check your .env file."
            )

        self._exchange = ccxt.bybit({
            "apiKey": BYBIT_API_KEY,
            "secret": BYBIT_API_SECRET,
            "enableRateLimit": True,
            "options": {
                "defaultType": "linear",   # USDT Linear Perpetuals
            },
        })
        if BYBIT_TESTNET:
            self._exchange.set_sandbox_mode(True)
        elif BYBIT_DEMO:
            # Demo Trading account uses api-demo.bybit.com, not api.bybit.com
            for section in ("public", "private"):
                urls = self._exchange.urls.get("api", {})
                if isinstance(urls, dict) and section in urls:
                    urls[section] = urls[section].replace(
                        "api.bybit.com", "api-demo.bybit.com"
                    )

        # Skip fetch_currencies() inside load_markets — it calls
        # /v5/asset/coin/query-info which requires Asset permission.
        # We only need Contract (trade) permission to place orders.
        self._exchange.has["fetchCurrencies"] = False
        await self._exchange.load_markets()
        mode = " (TESTNET)" if BYBIT_TESTNET else " (DEMO)" if BYBIT_DEMO else " (LIVE)"
        log.info("Connected to Bybit Linear Perpetuals%s", mode)

    async def disconnect(self) -> None:
        if self._exchange:
            await self._exchange.close()
        log.info("Bybit connection closed")

    # ------------------------------------------------------------------
    # Leverage / margin setup (once per symbol per session)
    # ------------------------------------------------------------------

    async def _ensure_leverage(self, symbol: str) -> None:
        if symbol in self._leverage_set:
            return
        try:
            await self._exchange.set_margin_mode(BYBIT_MARGIN_MODE, symbol)
        except Exception as exc:
            msg = str(exc).lower()
            # Bybit raises "margin mode is not modified" when already set — harmless
            if "not modified" not in msg and "same margin" not in msg:
                log.warning("Could not set margin mode for %s: %s", symbol, exc)
        try:
            await self._exchange.set_leverage(BYBIT_LEVERAGE, symbol)
        except Exception as exc:
            log.warning("Could not set leverage for %s: %s", symbol, exc)
        self._leverage_set.add(symbol)

    # ------------------------------------------------------------------
    # Account info
    # ------------------------------------------------------------------

    async def get_balance(self, asset: str = "USDT") -> float:
        # Call Bybit V5 wallet endpoint directly to avoid ccxt's
        # is_unified_enabled() check, which requires extra permissions.
        # Try UNIFIED first (new UTA accounts), fall back to CONTRACT.
        try:
            for acct_type in ("UNIFIED", "CONTRACT"):
                try:
                    resp = await self._exchange.privateGetV5AccountWalletBalance(
                        {"accountType": acct_type}
                    )
                    for account in (resp.get("result", {}).get("list") or []):
                        for coin in (account.get("coin") or []):
                            if coin.get("coin") == asset:
                                return float(coin.get("walletBalance") or 0)
                except Exception:
                    continue
            return 0.0
        except Exception as exc:
            raise BybitAPIError(str(exc)) from exc

    async def get_open_positions(self) -> List[Dict]:
        """Returns non-zero positions currently open on the exchange."""
        try:
            positions = await self._exchange.fetch_positions()
        except Exception as exc:
            raise BybitAPIError(str(exc)) from exc
        return [p for p in positions if float(p.get("contracts") or 0) != 0]

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    async def get_candles(self, symbol: str, granularity: int, count: int) -> CandleData:
        timeframe = _GRANULARITY_TO_TIMEFRAME.get(granularity, f"{granularity // 60}m")
        try:
            ohlcv = await self._exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=count)
        except Exception as exc:
            raise BybitAPIError(str(exc)) from exc

        if not ohlcv:
            raise BybitAPIError(f"No candles returned for {symbol}")

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
            raise BybitAPIError(str(exc)) from exc
        return float(ticker["last"])

    # ------------------------------------------------------------------
    # Trade execution (linear perpetuals, long + short)
    # ------------------------------------------------------------------

    async def open_long(self, symbol: str, margin_usdt: float,
                        leverage: int = BYBIT_LEVERAGE) -> Dict:
        """Open a long position, committing `margin_usdt` as isolated margin."""
        await self._ensure_leverage(symbol)
        try:
            price  = await self.get_price(symbol)
            amount = float(self._exchange.amount_to_precision(
                symbol, margin_usdt * leverage / price
            ))
            log.info("Open LONG %s | margin=%.2f leverage=%dx amount=%s @ ~%.4f",
                      symbol, margin_usdt, leverage, amount, price)
            order = await self._exchange.create_market_buy_order(symbol, amount)
            return order
        except Exception as exc:
            raise BybitAPIError(str(exc)) from exc

    async def open_short(self, symbol: str, margin_usdt: float,
                         leverage: int = BYBIT_LEVERAGE) -> Dict:
        """Open a short position, committing `margin_usdt` as isolated margin."""
        await self._ensure_leverage(symbol)
        try:
            price  = await self.get_price(symbol)
            amount = float(self._exchange.amount_to_precision(
                symbol, margin_usdt * leverage / price
            ))
            log.info("Open SHORT %s | margin=%.2f leverage=%dx amount=%s @ ~%.4f",
                      symbol, margin_usdt, leverage, amount, price)
            order = await self._exchange.create_market_sell_order(symbol, amount)
            return order
        except Exception as exc:
            raise BybitAPIError(str(exc)) from exc

    async def close_long(self, symbol: str, amount: float) -> Dict:
        """Close an open long by selling `amount` contracts (reduceOnly)."""
        try:
            amount = float(self._exchange.amount_to_precision(symbol, amount))
            log.info("Close LONG %s | amount=%s", symbol, amount)
            order = await self._exchange.create_market_sell_order(
                symbol, amount, params={"reduceOnly": True}
            )
            return order
        except Exception as exc:
            raise BybitAPIError(str(exc)) from exc

    async def close_short(self, symbol: str, amount: float) -> Dict:
        """Close an open short by buying back `amount` contracts (reduceOnly)."""
        try:
            amount = float(self._exchange.amount_to_precision(symbol, amount))
            log.info("Close SHORT %s | amount=%s", symbol, amount)
            order = await self._exchange.create_market_buy_order(
                symbol, amount, params={"reduceOnly": True}
            )
            return order
        except Exception as exc:
            raise BybitAPIError(str(exc)) from exc
