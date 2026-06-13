"""
Deriv WebSocket API broker module.
Handles authentication, candle fetching, trade execution, and position management.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from bot.config import (
    DERIV_APP_ID, DERIV_API_TOKEN, DERIV_WS_URL,
    CANDLE_COUNT, MULTIPLIER_VALUE,
    MAX_RETRIES, RETRY_BACKOFF,
)
from bot.strategies import CandleData, Signal

import numpy as np

log = logging.getLogger(__name__)


class DerivAPIError(Exception):
    """Raised when the Deriv API returns an error response."""


class DerivBroker:
    """
    Async broker for Deriv WebSocket API v3.

    Usage pattern:
        async with DerivBroker() as broker:
            await broker.authorize()
            balance = await broker.get_balance()
    """

    def __init__(self) -> None:
        self._ws: Optional[Any] = None
        self._request_map: Dict[str, asyncio.Future] = {}
        self._listener_task: Optional[asyncio.Task] = None
        self._authorized = False

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "DerivBroker":
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.disconnect()

    async def connect(self) -> None:
        url = f"{DERIV_WS_URL}?app_id={DERIV_APP_ID}"
        log.info("Connecting to Deriv API: %s", url)
        self._ws = await websockets.connect(url, ping_interval=30, ping_timeout=10)
        self._listener_task = asyncio.create_task(self._listener())
        log.info("WebSocket connected")

    async def disconnect(self) -> None:
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()
        log.info("WebSocket disconnected")

    # ------------------------------------------------------------------
    # Message routing
    # ------------------------------------------------------------------

    async def _listener(self) -> None:
        """Continuously read frames and route responses to waiting callers."""
        try:
            async for raw in self._ws:
                msg: dict = json.loads(raw)
                req_id = str(msg.get("req_id", ""))
                fut = self._request_map.pop(req_id, None)
                if fut and not fut.done():
                    if "error" in msg:
                        fut.set_exception(DerivAPIError(msg["error"]["message"]))
                    else:
                        fut.set_result(msg)
        except (ConnectionClosed, WebSocketException) as exc:
            log.warning("WebSocket closed in listener: %s", exc)
        except asyncio.CancelledError:
            pass

    async def _send(self, payload: dict) -> dict:
        """Send a request and await its response with retry logic."""
        for attempt in range(1, MAX_RETRIES + 1):
            req_id = str(uuid.uuid4().int)[:8]
            payload["req_id"] = int(req_id)

            fut: asyncio.Future = asyncio.get_event_loop().create_future()
            self._request_map[req_id] = fut

            try:
                await self._ws.send(json.dumps(payload))
                response = await asyncio.wait_for(fut, timeout=30)
                return response
            except DerivAPIError:
                raise
            except (ConnectionClosed, asyncio.TimeoutError, WebSocketException) as exc:
                log.warning("Request failed (attempt %d/%d): %s", attempt, MAX_RETRIES, exc)
                self._request_map.pop(req_id, None)
                if attempt < MAX_RETRIES:
                    wait = RETRY_BACKOFF ** attempt
                    log.info("Retrying in %.1f s …", wait)
                    await asyncio.sleep(wait)
                    # Attempt reconnect
                    try:
                        await self.connect()
                    except Exception as reconn_exc:
                        log.error("Reconnect failed: %s", reconn_exc)
                else:
                    raise

        raise DerivAPIError("Max retries exceeded")

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def authorize(self) -> Dict:
        if not DERIV_API_TOKEN:
            raise DerivAPIError("DERIV_API_TOKEN is not set. Check your .env file.")
        resp = await self._send({"authorize": DERIV_API_TOKEN})
        self._authorized = True
        log.info("Authorized as: %s", resp["authorize"].get("loginid"))
        return resp["authorize"]

    # ------------------------------------------------------------------
    # Account info
    # ------------------------------------------------------------------

    async def get_balance(self) -> float:
        resp = await self._send({"balance": 1, "subscribe": 0})
        return float(resp["balance"]["balance"])

    async def get_open_positions(self) -> List[Dict]:
        """Return list of open multiplier contracts."""
        resp = await self._send({"portfolio": 1})
        return resp.get("portfolio", {}).get("contracts", [])

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    async def get_candles(self, deriv_symbol: str,
                          granularity: int,
                          count: int = CANDLE_COUNT) -> CandleData:
        """
        Fetch OHLC history for a symbol.

        deriv_symbol: e.g. "R_75", "BOOM1000"
        granularity:  seconds per candle (900 = M15, 1800 = M30)
        """
        payload = {
            "ticks_history": deriv_symbol,
            "adjust_start_time": 1,
            "count": count,
            "end": "latest",
            "granularity": granularity,
            "style": "candles",
        }
        resp = await self._send(payload)
        candles: list = resp.get("candles", [])

        if not candles:
            raise DerivAPIError(f"No candles returned for {deriv_symbol}")

        opens  = np.array([float(c["open"])  for c in candles])
        highs  = np.array([float(c["high"])  for c in candles])
        lows   = np.array([float(c["low"])   for c in candles])
        closes = np.array([float(c["close"]) for c in candles])
        epochs = np.array([int(c["epoch"])   for c in candles])

        return CandleData(open=opens, high=highs, low=lows, close=closes, epoch=epochs)

    # ------------------------------------------------------------------
    # Trade execution
    # ------------------------------------------------------------------

    async def place_trade(self, signal: Signal, stake: float) -> Dict:
        """
        Place a Multiplier contract via the Deriv API.

        Returns the buy response dict with contract_id, etc.
        Raises DerivAPIError on failure.
        """
        from bot.config import SYMBOLS

        deriv_symbol = SYMBOLS[signal.symbol]

        # Determine contract type
        contract_type = "MULTUP" if signal.direction == "BUY" else "MULTDOWN"

        # Build stop-loss and take-profit limits (absolute values for multipliers)
        sl_distance = abs(signal.entry - signal.stop_loss)
        tp_distance = abs(signal.take_profit - signal.entry)

        payload = {
            "buy": 1,
            "price": stake,          # stake amount
            "parameters": {
                "amount":          stake,
                "basis":           "stake",
                "contract_type":   contract_type,
                "currency":        "USD",
                "duration":        None,      # no expiry for multipliers
                "multiplier":      MULTIPLIER_VALUE,
                "product_type":    "basic",
                "symbol":          deriv_symbol,
                "limit_order": {
                    "stop_loss":   round(sl_distance, 5),
                    "take_profit": round(tp_distance, 5),
                },
            },
        }

        log.info(
            "Placing %s %s on %s | stake=%.2f SL=%.5f TP=%.5f",
            signal.direction, contract_type, deriv_symbol,
            stake, sl_distance, tp_distance,
        )
        resp = await self._send(payload)
        result = resp.get("buy", {})
        log.info("Trade placed: contract_id=%s", result.get("contract_id"))
        return result

    async def close_position(self, contract_id: int) -> Dict:
        """Market-sell an open contract."""
        log.info("Closing contract %s", contract_id)
        resp = await self._send({"sell": contract_id, "price": 0})
        return resp.get("sell", {})

    async def close_all_positions(self) -> None:
        """Emergency: close every open position."""
        positions = await self.get_open_positions()
        for pos in positions:
            cid = pos.get("contract_id")
            if cid:
                try:
                    await self.close_position(cid)
                except DerivAPIError as exc:
                    log.error("Failed to close contract %s: %s", cid, exc)

    # ------------------------------------------------------------------
    # Profit / trade history
    # ------------------------------------------------------------------

    async def get_trade_history(self, limit: int = 50) -> List[Dict]:
        resp = await self._send({"profit_table": 1, "limit": limit, "sort": "DESC"})
        return resp.get("profit_table", {}).get("transactions", [])
