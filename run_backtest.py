"""
Entry point: run this to backtest the current strategy config across
all forex symbols in config.py, using real historical Deriv data.

Usage:
    python run_backtest.py
"""
import os
import traceback
import pandas as pd
import config as cfg
import deriv_client
from backtester import simulate_symbol, summarize


def candles_to_df(candles: list) -> pd.DataFrame:
    """Converts Deriv's candle format into the OHLCV dataframe shape the strategy expects."""
    df = pd.DataFrame(candles)
    df = df.rename(columns={"epoch": "timestamp"})
    df["open"] = df["open"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["close"] = df["close"].astype(float)
    df["volume"] = 0  # forex candles from Deriv don't include volume - not used by current strategy logic
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="s")
    return df[["timestamp", "open", "high", "low", "close", "volume", "datetime"]]


def main():
    os.makedirs("results", exist_ok=True)

    all_results = {}
    all_trades = []

    for symbol in cfg.SYMBOLS:
        print(f"Fetching {symbol}...")
        try:
            candles = deriv_client.fetch_candles(symbol, cfg.TIMEFRAME_SECONDS, cfg.HISTORY_LIMIT)
            print(f"  Requested {cfg.HISTORY_LIMIT} candles, received {len(candles)}")
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
    print("SUMMARY ACROSS ALL SYMBOLS")
    print("=" * 70)

    results_df = pd.DataFrame(all_results).T
    print(results_df.to_string())

    results_df.to_csv("results/backtest_summary.csv")

    trades_df = pd.DataFrame([t.__dict__ for t in all_trades])
    trades_df.to_csv("results/all_trades.csv", index=False)

    print(f"\nSaved detailed results to results/backtest_summary.csv and results/all_trades.csv")

    if not results_df.empty and "win_rate_pct" in results_df.columns:
        overall_wr = results_df["win_rate_pct"].mean()
        overall_expectancy = results_df["expectancy_pct_per_trade"].mean()
        print(f"\nAverage win rate across symbols: {overall_wr:.1f}%")
        print(f"Average expectancy per trade: {overall_expectancy:.3f}%")
        if overall_expectancy <= 0:
            print("\n⚠️  Negative/flat expectancy — this config is NOT ready for live signals.")
            print("    Try adjusting EMA/RSI/MACD periods, ATR thresholds, or ATR multipliers in config.py and re-run.")


if __name__ == "__main__":
    main()
