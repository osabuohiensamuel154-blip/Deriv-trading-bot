"""
Force a single test trade to verify the full execution pipeline.
Bypasses all signal scoring — places a real multiplier contract on the demo account.

Usage:  python test_trade.py
"""

import asyncio
import os
from dotenv import load_dotenv
load_dotenv()

from bot.broker_deriv import DerivBroker, DerivAPIError
from bot.strategies import Signal


# ── Configure your test trade here ──────────────────────────────────────────
TEST_SYMBOL     = "V75"          # friendly name (must match config.py SYMBOLS)
TEST_DIRECTION  = "BUY"          # "BUY" or "SELL"
TEST_STAKE      = 1.00           # $1 stake (minimum on demo)
SL_POINTS       = 50.0           # stop loss distance in price points
TP_POINTS       = 100.0          # take profit distance in price points
# ────────────────────────────────────────────────────────────────────────────


async def run_test():
    print("\n" + "="*55)
    print("  FORCE TRADE TEST")
    print("="*55)

    async with DerivBroker() as broker:
        # 1. Authorize
        print("\n[1] Connecting to Deriv …")
        acc = await broker.authorize()
        print(f"    Account : {acc.get('loginid')}")
        print(f"    Currency: {acc.get('currency')}")

        # 2. Get current balance
        print("\n[2] Fetching balance …")
        balance = await broker.get_balance()
        print(f"    Balance : {balance:.2f}")

        # 3. Get current price
        print(f"\n[3] Fetching live price for {TEST_SYMBOL} …")
        from bot.config import SYMBOLS, TIMEFRAME_M15
        deriv_sym = SYMBOLS[TEST_SYMBOL]
        candles = await broker.get_candles(deriv_sym, TIMEFRAME_M15, count=5)
        current_price = float(candles.close[-1])
        print(f"    Current price: {current_price:.5f}")

        # 4. Build a Signal with SL/TP
        if TEST_DIRECTION == "BUY":
            sl = current_price - SL_POINTS
            tp = current_price + TP_POINTS
        else:
            sl = current_price + SL_POINTS
            tp = current_price - TP_POINTS

        signal = Signal(
            symbol      = TEST_SYMBOL,
            strategy    = "TEST",
            direction   = TEST_DIRECTION,
            score       = 100.0,
            entry       = current_price,
            stop_loss   = sl,
            take_profit = tp,
        )

        from bot.config import MULTIPLIER_VALUE
        sl_usd = round(TEST_STAKE * MULTIPLIER_VALUE * (SL_POINTS / current_price), 2)
        tp_usd = round(TEST_STAKE * MULTIPLIER_VALUE * (TP_POINTS / current_price), 2)

        print(f"\n[4] Trade details:")
        print(f"    Symbol   : {TEST_SYMBOL} ({deriv_sym})")
        print(f"    Direction: {TEST_DIRECTION}")
        print(f"    Entry    : {current_price:.5f}")
        print(f"    SL price : {sl:.5f}  → auto-close if loss ≥ ${sl_usd}")
        print(f"    TP price : {tp:.5f}  → auto-close if profit ≥ ${tp_usd}")
        print(f"    Stake    : ${TEST_STAKE:.2f}  (x{MULTIPLIER_VALUE} multiplier)")

        confirm = input("\n  >>> Press ENTER to place trade, or type 'n' to cancel: ").strip()
        if confirm.lower() == 'n':
            print("  Cancelled.")
            return

        # 5. Place the trade
        print("\n[5] Placing trade …")
        try:
            result = await broker.place_trade(signal, TEST_STAKE)
            contract_id  = result.get("contract_id")
            longcode     = result.get("longcode", "")
            buy_price    = result.get("buy_price", TEST_STAKE)
            print(f"\n  ✓ TRADE PLACED SUCCESSFULLY!")
            print(f"    Contract ID : {contract_id}")
            print(f"    Description : {longcode}")
            print(f"    Cost        : ${buy_price}")
        except DerivAPIError as exc:
            print(f"\n  ✗ Trade failed: {exc}")
            return

        # 6. Verify it appears in open positions
        print("\n[6] Checking open positions …")
        positions = await broker.get_open_positions()
        ids = [str(p.get("contract_id")) for p in positions]
        if str(contract_id) in ids:
            print(f"  ✓ Contract {contract_id} confirmed in open positions")
        else:
            print(f"  ⚠ Contract not yet in portfolio (may take a moment)")

        print("\n" + "="*55)
        print("  TEST COMPLETE — check your Deriv demo account")
        print("  to see the live position with SL and TP set.")
        print("="*55 + "\n")


if __name__ == "__main__":
    asyncio.run(run_test())
