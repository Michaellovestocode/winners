"""
Simulates the strategy bar-by-bar on historical data, using the SAME
evaluate_bar() function the live bot will use. This matters: if the
backtest logic diverges from the live logic, the backtest is meaningless.

For each symbol:
  - Walk through candles in order.
  - When flat, check for a new signal.
  - When in a trade, check each subsequent bar's high/low to see if
    SL or TP was hit first (intrabar, using worst-case ordering).
  - Track cooldown after a trade closes.
"""
from dataclasses import dataclass, field
import pandas as pd
import config as cfg
from indicators import add_all_indicators
from strategy import evaluate_bar, Signal


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
    result: str = None       # "TP", "SL", "OPEN_AT_END"
    pnl_pct: float = None    # price % move, not leveraged


def simulate_symbol(df: pd.DataFrame, symbol: str) -> list:
    df = add_all_indicators(df, cfg)
    trades = []
    in_position = False
    current: Signal = None
    cooldown_until = 0

    min_lookback = max(cfg.EMA_SLOW, cfg.ATR_PERIOD, cfg.MACD_SLOW) + 5

    for i in range(min_lookback, len(df)):
        row = df.iloc[i]

        if in_position:
            # Check intrabar: did SL or TP get hit? Conservative assumption:
            # if both could have been hit in the same bar, assume SL hit first
            # (worst case, avoids overstating performance).
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

    # if still open at end of data, mark as open (excluded from win-rate stats)
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
    avg_rr = sum(t.rr_ratio for t in closed) / len(closed)

    # Expectancy per trade, in % price move (not leveraged, excludes fees)
    expectancy = (win_rate / 100 * avg_win_pct) + ((1 - win_rate / 100) * avg_loss_pct)

    # Simple equity curve using fixed % risk per trade
    balance = cfg.STARTING_BALANCE
    peak = balance
    max_dd = 0
    equity_curve = [balance]
    for t in closed:
        risk_amount = balance * (cfg.RISK_PER_TRADE_PCT / 100)
        pnl = risk_amount * t.rr_ratio if t.result == "TP" else -risk_amount
        fee = balance * (cfg.TAKER_FEE_PCT / 100) * 2  # entry + exit
        balance = balance + pnl - fee
        equity_curve.append(balance)
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