"""
Shared utility functions used across modules.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def seconds_until_next_candle(granularity: int) -> float:
    """
    Returns the number of seconds remaining until the next candle close,
    given a granularity in seconds (e.g. 900 for M15).
    """
    now = time.time()
    elapsed_in_period = now % granularity
    remaining = granularity - elapsed_in_period
    # Add a small buffer so the candle is fully closed when we fetch
    return remaining + 5.0


def format_duration(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


# ---------------------------------------------------------------------------
# Async retry decorator
# ---------------------------------------------------------------------------

def async_retry(max_attempts: int = 3, backoff: float = 2.0, exceptions: tuple = (Exception,)):
    """Decorator: retry an async function up to max_attempts times."""
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            for attempt in range(1, max_attempts + 1):
                try:
                    return await fn(*args, **kwargs)
                except exceptions as exc:
                    if attempt == max_attempts:
                        raise
                    wait = backoff ** attempt
                    log.warning(
                        "%s failed (attempt %d/%d): %s — retrying in %.1fs",
                        fn.__name__, attempt, max_attempts, exc, wait,
                    )
                    await asyncio.sleep(wait)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Price / math helpers
# ---------------------------------------------------------------------------

def pct_change(old: float, new: float) -> float:
    if old == 0:
        return 0.0
    return (new - old) / old * 100


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def round_to_tick(value: float, tick_size: float) -> float:
    """Round a price to the nearest tick size."""
    if tick_size <= 0:
        return value
    return round(round(value / tick_size) * tick_size, 10)


# ---------------------------------------------------------------------------
# Startup banner
# ---------------------------------------------------------------------------

BANNER = r"""
  _____            _       _____            _   _               ____        _
 |  __ \          (_)     |_   _|          | | (_)             |  _ \      | |
 | |  | | ___ _ __ ___   __ | |_ __ __ _  | |_ _ _ __   __ _  | |_) | ___ | |_
 | |  | |/ _ \ '__| \ \ / / | | '__/ _` | | __| | '_ \ / _` | |  _ < / _ \| __|
 | |__| |  __/ |  | |\ V / _| | | | (_| | | |_| | | | | (_| | | |_) | (_) | |_
 |_____/ \___|_|  |_| \_/ |___/_|  \__,_|  \__|_|_| |_|\__, | |____/ \___/ \__|
                                                          __/ |
                                                         |___/
  Deriv Synthetic Indices — Dual Strategy System
"""


def print_banner(instruments: str = "V50 | V75 | V100 | BOOM1000 | CRASH1000",
                  strategies: str = "Trend (EMA+RSI) | Reversal (Spike+RSI)") -> None:
    print(BANNER)
    print(f"  Started at: {utc_now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Instruments: {instruments}")
    print(f"  Strategies:  {strategies}")
    print("-" * 70)
