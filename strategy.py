"""
Core signal generation logic.

Design principle: a signal only fires when MULTIPLE independent conditions
agree (trend + momentum + volatility gate). This is what separates a
confluence-based signal from "RSI hit 30, fire a buy" — the latter is what
usually produces the bad, noisy signals from a single-indicator bot.
"""
from dataclasses import dataclass
from typing import Optional
import config as cfg


@dataclass
class Signal:
    symbol: str
    side: str              # "LONG" or "SHORT"
    entry: float
    stop_loss: float
    take_profit: float
    confidence: float       # 0-100
    leverage: int
    rr_ratio: float
    reasons: list
    bar_index: int


def suggest_leverage(atr_pct: float) -> int:
    """Higher volatility -> lower suggested leverage. Simple linear scale."""
    if atr_pct <= cfg.MIN_ATR_PCT:
        return cfg.MAX_LEVERAGE
    if atr_pct >= cfg.MAX_ATR_PCT:
        return cfg.MIN_LEVERAGE

    span = cfg.MAX_ATR_PCT - cfg.MIN_ATR_PCT
    pos = (atr_pct - cfg.MIN_ATR_PCT) / span
    leverage = cfg.MAX_LEVERAGE - pos * (cfg.MAX_LEVERAGE - cfg.MIN_LEVERAGE)
    return max(cfg.MIN_LEVERAGE, min(cfg.MAX_LEVERAGE, round(leverage)))


def evaluate_bar(df, i: int, symbol: str) -> Optional[Signal]:
    """
    Evaluate a single bar (index i) for a signal.
    df must already have indicators from indicators.add_all_indicators().
    """
    row = df.iloc[i]
    prev = df.iloc[i - 1]

    atr_pct = row["atr_pct"]

    if atr_pct < cfg.MIN_ATR_PCT or atr_pct > cfg.MAX_ATR_PCT:
        return None

    ema_gap_pct = abs(row["ema_fast"] - row["ema_slow"]) / row["close"] * 100
    trend_is_strong = ema_gap_pct >= cfg.TREND_STRENGTH_MIN_PCT
    uptrend = row["ema_fast"] > row["ema_slow"] and trend_is_strong
    downtrend = row["ema_fast"] < row["ema_slow"] and trend_is_strong

    macd_cross_up = prev["macd"] <= prev["macd_signal"] and row["macd"] > row["macd_signal"]
    macd_cross_down = prev["macd"] >= prev["macd_signal"] and row["macd"] < row["macd_signal"]

    reasons = []
    side = None
    confidence = 0.0

    if uptrend and macd_cross_up and cfg.RSI_MIDLINE < row["rsi"] < cfg.RSI_OVERBOUGHT:
        side = "LONG"
        reasons.append(f"Strong uptrend (EMA gap {ema_gap_pct:.2f}%)")
        reasons.append("Bullish MACD crossover")
        reasons.append(f"RSI {row['rsi']:.1f} (not overbought)")
        confidence += 40
        confidence += 30
        rsi_room = max(0, (cfg.RSI_OVERBOUGHT - row["rsi"]) / cfg.RSI_OVERBOUGHT)
        confidence += 20 * rsi_room
        if row["macd_hist"] > prev["macd_hist"]:
            confidence += 10
            reasons.append("MACD histogram expanding")

    elif downtrend and macd_cross_down and cfg.RSI_OVERSOLD < row["rsi"] < cfg.RSI_MIDLINE:
        side = "SHORT"
        reasons.append(f"Strong downtrend (EMA gap {ema_gap_pct:.2f}%)")
        reasons.append("Bearish MACD crossover")
        reasons.append(f"RSI {row['rsi']:.1f} (not oversold)")
        confidence += 40
        confidence += 30
        rsi_room = max(0, (row["rsi"] - cfg.RSI_OVERSOLD) / (100 - cfg.RSI_OVERSOLD))
        confidence += 20 * rsi_room
        if row["macd_hist"] < prev["macd_hist"]:
            confidence += 10
            reasons.append("MACD histogram expanding (bearish)")

    if side is None:
        return None

    confidence = round(min(100, max(0, confidence)), 1)

    entry = row["close"]
    atr_val = row["atr"]
    lev = suggest_leverage(atr_pct)

    if side == "LONG":
        sl = entry - atr_val * cfg.ATR_SL_MULT
        tp = entry + atr_val * cfg.ATR_TP_MULT
    else:
        sl = entry + atr_val * cfg.ATR_SL_MULT
        tp = entry - atr_val * cfg.ATR_TP_MULT

    risk = abs(entry - sl)
    reward = abs(tp - entry)
    rr = round(reward / risk, 2) if risk > 0 else 0

    reasons.append(f"ATR {atr_pct:.2f}% of price (volatility gate passed)")

    # TEST MODE: reverse direction, force 1:1 R:R
    risk = abs(entry - sl)
    if side == "LONG":
        side = "SHORT"
        sl = entry + risk
        tp = entry - risk
    else:
        side = "LONG"
        sl = entry - risk
        tp = entry + risk
    rr = 1.0
    reasons.append("⚠️ TEST MODE: signal reversed, forced 1:1 R:R")

    return Signal(
        symbol=symbol,
        side=side,
        entry=entry,
        stop_loss=sl,
        take_profit=tp,
        confidence=confidence,
        leverage=lev,
        rr_ratio=rr,
        reasons=reasons,
        bar_index=i,
    )