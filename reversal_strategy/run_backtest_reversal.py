"""
Backtests the liquidity-sweep reversal strategy against real Deriv forex
history. Separate from the main run_backtest.py - doesn't touch or affect
the working trend-following bot in the parent folder.

Usage (run from inside the reversal_strategy folder):
    python run_backtest_reversal.py
"""
import os
import sys
import traceback
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import deriv_client

import config_reversal as cfg
from backtester_reversal import simulate_symbol, summarize


def candles_to_df(candles: list) -> pd.DataFrame:
    df = pd.DataFrame(candles)
    df = df.rename(columns={"epoch": "timestamp"})
    df["open"] = df["open"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["close"] = df["close"].astype(float)
    df["volume"] = 0
    return df[["timestamp", "open", "high", "low", "close", "volume"]]


def main():
    os.makedirs("results", exist_ok=True)

    all_results = {}
    all_trades = []

    for symbol in cfg.SYMBOLS:
        print(f"Fetching {symbol}...")
        try:
            candles = deriv_client.fetch_candles(symbol, cfg.TIMEFRAME_SECONDS, cfg.HISTORY_LIMIT)
            print(f"  Received {len(candles)} candles")
            df = candles_to_df(candles)
        except Exception as e:
            print(f"  Failed to fetch {symbol}: {type(e).__name__}: {e}")
            traceback.print_exc()
            continue

        if len(df) < 250:
            print(f"  Not enough data for {symbol} ({len(df)} candles), skipping.")
            continue

        trades = simulate_symbol(df, symbol)
        stats = summarize(trades)
        all_results[symbol] = stats
        all_trades.extend(trades)
        print(f"  {symbol}: {stats}")

    print("\n" + "=" * 70)
    print("REVERSAL STRATEGY - SUMMARY ACROSS ALL SYMBOLS")
    print("=" * 70)

    results_df = pd.DataFrame(all_results).T
    print(results_df.to_string())

    results_df.to_csv("results/reversal_backtest_summary.csv")

    trades_df = pd.DataFrame([t.__dict__ for t in all_trades])
    trades_df.to_csv("results/reversal_all_trades.csv", index=False)

    print(f"\nSaved results to results/reversal_backtest_summary.csv and results/reversal_all_trades.csv")

    if not results_df.empty and "win_rate_pct" in results_df.columns:
        overall_wr = results_df["win_rate_pct"].mean()
        overall_expectancy = results_df["expectancy_pct_per_trade"].mean()
        overall_avg_rr = results_df["avg_rr"].mean()
        print(f"\nAverage win rate across symbols: {overall_wr:.1f}%")
        print(f"Average R:R across symbols: {overall_avg_rr:.2f}")
        print(f"Average expectancy per trade: {overall_expectancy:.3f}%")
        if overall_expectancy <= 0:
            print("\n⚠️  Negative/flat expectancy - this reversal config is NOT ready for live signals.")


if __name__ == "__main__":
    main()
