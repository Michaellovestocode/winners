"""
Backtests the liquidity-sweep reversal strategy bar-by-bar, same discipline
as the main backtester.py: uses the exact evaluate_bar() logic the live bot
would use, walks through candles in order, checks intrabar SL/TP hits.

Separate from the main backtester.py because this strategy has a per-trade
variable R:R (swing-based targets), not a fixed multiple - the summary stats
need to average the ACTUAL rr_ratio per trade rather than assume a constant.
"""
from dataclasses import dataclass
import pandas as pd
import numpy as np
import config_reversal as cfg
from strategy_reversal import evaluate_bar, Signal, find_confirmed_swings

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from indicators import atr as compute_atr


@dataclass
class Trade:
    symbol: str
    side: str
    entry: float
    stop_loss: float
    take_profit: float
    confidence: float
    leverage: int
    rr_ratio: float
    entry_bar: int
    exit_bar: int = None
    exit_price: float = None
    result: str = None
    pnl_pct: float = None


def prepare_df(df: pd.DataFrame) -> pd.DataFrame:
    """Adds ATR (this strategy doesn't need EMA/RSI/MACD) and attaches swing flags."""
    df = df.copy()
    df["atr"] = compute_atr(df, cfg.ATR_PERIOD)
    df["atr_pct"] = (df["atr"] / df["close"]) * 100
    swing_high_flags, swing_low_flags = find_confirmed_swings(df, cfg.SWING_LOOKBACK)
    df.attrs["swing_high_flags"] = swing_high_flags
    df.attrs["swing_low_flags"] = swing_low_flags
    return df


def simulate_symbol(df: pd.DataFrame, symbol: str) -> list:
    df = prepare_df(df)
    trades = []
    in_position = False
    current: Signal = None
    cooldown_until = 0

    min_lookback = cfg.SWING_LOOKBACK * 2 + cfg.SWEEP_LOOKBACK_BARS + cfg.MAX_SWING_AGE_BARS

    for i in range(min_lookback, len(df)):
        row = df.iloc[i]

        if in_position:
            hit_sl = (row["low"] <= current.stop_loss) if current.side == "LONG" else (row["high"] >= current.stop_loss)
            hit_tp = (row["high"] >= current.take_profit) if current.side == "LONG" else (row["low"] <= current.take_profit)

            if hit_sl or hit_tp:
                if hit_sl:
                    exit_price = current.stop_loss
                    result = "SL"
                else:
                    exit_price = current.take_profit
                    result = "TP"

                pnl_pct = ((exit_price - current.entry) / current.entry * 100) if current.side == "LONG" \
                    else ((current.entry - exit_price) / current.entry * 100)

                trades.append(Trade(
                    symbol=symbol, side=current.side, entry=current.entry,
                    stop_loss=current.stop_loss, take_profit=current.take_profit,
                    confidence=current.confidence, leverage=current.leverage,
                    rr_ratio=current.rr_ratio, entry_bar=current.bar_index,
                    exit_bar=i, exit_price=exit_price, result=result, pnl_pct=pnl_pct
                ))
                in_position = False
                current = None
                cooldown_until = i + cfg.COOLDOWN_BARS
            continue

        if i < cooldown_until:
            continue

        sig = evaluate_bar(df, i, symbol)
        if sig:
            in_position = True
            current = sig

    if in_position:
        trades.append(Trade(
            symbol=symbol, side=current.side, entry=current.entry,
            stop_loss=current.stop_loss, take_profit=current.take_profit,
            confidence=current.confidence, leverage=current.leverage,
            rr_ratio=current.rr_ratio, entry_bar=current.bar_index,
            exit_bar=None, exit_price=None, result="OPEN_AT_END", pnl_pct=None
        ))

    return trades


def summarize(trades: list) -> dict:
    closed = [t for t in trades if t.result in ("TP", "SL")]
    if not closed:
        return {"total_trades": 0}

    wins = [t for t in closed if t.result == "TP"]
    losses = [t for t in closed if t.result == "SL"]

    win_rate = len(wins) / len(closed) * 100
    avg_win_pct = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
    avg_loss_pct = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0
    avg_rr = sum(t.rr_ratio for t in closed) / len(closed)  # variable per trade here, unlike main strategy

    expectancy = (win_rate / 100 * avg_win_pct) + ((1 - win_rate / 100) * avg_loss_pct)

    # Equity curve: unlike the main backtester (fixed R:R), this uses each
    # trade's OWN rr_ratio since targets are swing-based, not a fixed multiple.
    balance = cfg.STARTING_BALANCE
    peak = balance
    max_dd = 0
    for t in closed:
        risk_amount = balance * (cfg.RISK_PER_TRADE_PCT / 100)
        pnl = risk_amount * t.rr_ratio if t.result == "TP" else -risk_amount
        fee = balance * (cfg.TAKER_FEE_PCT / 100) * 2
        balance = balance + pnl - fee
        peak = max(peak, balance)
        dd = (peak - balance) / peak * 100
        max_dd = max(max_dd, dd)

    return {
        "total_trades": len(closed),
        "open_at_end": len(trades) - len(closed),
        "win_rate_pct": round(win_rate, 1),
        "avg_win_pct": round(avg_win_pct, 2),
        "avg_loss_pct": round(avg_loss_pct, 2),
        "avg_rr": round(avg_rr, 2),
        "expectancy_pct_per_trade": round(expectancy, 3),
        "final_balance": round(balance, 2),
        "return_pct": round((balance - cfg.STARTING_BALANCE) / cfg.STARTING_BALANCE * 100, 1),
        "max_drawdown_pct": round(max_dd, 1),
    }
