"""
Liquidity sweep reversal strategy.

Core idea (from real, observable market behavior - price often sweeps past
an obvious swing high/low, triggering stops clustered there, then reverses):

1. Find the most recent CONFIRMED swing low/high (confirmed = it's the most
   extreme point within SWING_LOOKBACK candles on both sides - this means
   confirmation lags by SWING_LOOKBACK candles, which is realistic/causal,
   not lookahead bias).
2. Check if price has swept past that swing level by a meaningful margin
   in the last few candles (a "failed breakdown" for longs, "failed
   breakout" for shorts).
3. Check the current candle is a strong reversal candle closing back
   beyond the swept level.
4. If so, signal a trade: stop beyond the sweep's extreme, target the most
   recent PRIOR opposite swing (a real, already-known resistance/support
   level) if it gives good R:R, otherwise a fixed R:R fallback.
"""
from dataclasses import dataclass
from typing import Optional
import numpy as np
import config_reversal as cfg

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from strategy import suggest_leverage  # reuse the same leverage logic as the main strategy


@dataclass
class Signal:
    symbol: str
    side: str
    entry: float
    stop_loss: float
    take_profit: float
    confidence: float
    leverage: int
    rr_ratio: float
    reasons: list
    bar_index: int


def find_confirmed_swings(df, lookback: int):
    """
    Returns (swing_high_flags, swing_low_flags) - boolean arrays same length
    as df. swing_high_flags[i] is True if candle i is a confirmed swing high
    (only knowable once we've seen `lookback` candles after it).
    """
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)
    swing_high = np.zeros(n, dtype=bool)
    swing_low = np.zeros(n, dtype=bool)

    for i in range(lookback, n - lookback):
        window_high = highs[i - lookback: i + lookback + 1]
        if highs[i] == window_high.max():
            swing_high[i] = True
        window_low = lows[i - lookback: i + lookback + 1]
        if lows[i] == window_low.min():
            swing_low[i] = True

    return swing_high, swing_low


def most_recent_confirmed_swing(swing_flags, up_to_index: int, max_age: int):
    """
    Finds the index of the most recent confirmed swing at or before
    up_to_index - lookback (confirmation lag already baked into swing_flags).
    Returns None if nothing found within max_age candles.
    """
    start = max(0, up_to_index - max_age)
    for i in range(up_to_index, start - 1, -1):
        if swing_flags[i]:
            return i
    return None


def evaluate_bar(df, i: int, symbol: str) -> Optional[Signal]:
    """
    Evaluate bar i for a liquidity-sweep reversal signal.
    df must have 'atr' column already computed (from indicators.add_all_indicators
    or an equivalent ATR calc - this strategy only needs ATR, not the trend/
    momentum indicators the main strategy uses).
    """
    if i < cfg.SWING_LOOKBACK * 2 + cfg.SWEEP_LOOKBACK_BARS:
        return None

    row = df.iloc[i]
    atr_val = row["atr"]
    atr_pct = row["atr_pct"]

    if atr_pct < cfg.MIN_ATR_PCT or atr_pct > cfg.MAX_ATR_PCT or atr_val <= 0 or np.isnan(atr_val):
        return None

    swing_high_flags = df.attrs.get("swing_high_flags")
    swing_low_flags = df.attrs.get("swing_low_flags")
    if swing_high_flags is None or swing_low_flags is None:
        raise RuntimeError("df.attrs must contain 'swing_high_flags' and 'swing_low_flags' - "
                            "call find_confirmed_swings() and attach them before evaluating bars.")

    # Confirmed swings must be at least SWING_LOOKBACK candles old relative to i
    confirm_cutoff = i - cfg.SWING_LOOKBACK
    if confirm_cutoff < 0:
        return None

    recent_lows = df["low"].values[max(0, i - cfg.SWEEP_LOOKBACK_BARS + 1): i + 1]
    recent_highs = df["high"].values[max(0, i - cfg.SWEEP_LOOKBACK_BARS + 1): i + 1]
    close = row["close"]
    open_ = row["open"]

    reasons = []
    side = None
    entry = close
    stop = None
    target = None

    # --- LONG setup: swept below a recent swing low, strong bullish reversal candle ---
    swing_low_idx = most_recent_confirmed_swing(swing_low_flags, confirm_cutoff, cfg.MAX_SWING_AGE_BARS)
    if swing_low_idx is not None:
        swing_low_price = df["low"].values[swing_low_idx]
        sweep_low = recent_lows.min()
        swept = sweep_low < swing_low_price - (cfg.SWEEP_MARGIN_ATR_MULT * atr_val)
        closed_back_above = close > swing_low_price
        body = close - open_
        strong_bullish = (body > 0) and (body >= cfg.MIN_REVERSAL_BODY_ATR_MULT * atr_val)

        if swept and closed_back_above and strong_bullish:
            side = "LONG"
            stop = sweep_low - (cfg.STOP_BUFFER_ATR_MULT * atr_val)
            reasons.append(f"Swept below swing low from {i - swing_low_idx} candles ago")
            reasons.append(f"Strong bullish reversal candle (body {body:.5f} vs ATR {atr_val:.5f})")

            # target: most recent PRIOR swing high before the swept swing low (causal, no lookahead)
            prior_swing_high_idx = most_recent_confirmed_swing(swing_high_flags, swing_low_idx - 1, cfg.MAX_SWING_AGE_BARS)
            risk = entry - stop
            if prior_swing_high_idx is not None:
                candidate_target = df["high"].values[prior_swing_high_idx]
                candidate_rr = (candidate_target - entry) / risk if risk > 0 else 0
                if candidate_target > entry and candidate_rr >= cfg.MIN_RR_FOR_SWING_TARGET:
                    target = candidate_target
                    reasons.append(f"Target: prior swing high ({candidate_rr:.2f}R)")
            if target is None:
                target = entry + risk * cfg.FALLBACK_RR_TARGET_MULT
                reasons.append(f"Target: fallback {cfg.FALLBACK_RR_TARGET_MULT}R (no valid prior swing high)")

    # --- SHORT setup: swept above a recent swing high, strong bearish reversal candle ---
    if side is None:
        swing_high_idx = most_recent_confirmed_swing(swing_high_flags, confirm_cutoff, cfg.MAX_SWING_AGE_BARS)
        if swing_high_idx is not None:
            swing_high_price = df["high"].values[swing_high_idx]
            sweep_high = recent_highs.max()
            swept = sweep_high > swing_high_price + (cfg.SWEEP_MARGIN_ATR_MULT * atr_val)
            closed_back_below = close < swing_high_price
            body = open_ - close
            strong_bearish = (body > 0) and (body >= cfg.MIN_REVERSAL_BODY_ATR_MULT * atr_val)

            if swept and closed_back_below and strong_bearish:
                side = "SHORT"
                stop = sweep_high + (cfg.STOP_BUFFER_ATR_MULT * atr_val)
                reasons.append(f"Swept above swing high from {i - swing_high_idx} candles ago")
                reasons.append(f"Strong bearish reversal candle (body {body:.5f} vs ATR {atr_val:.5f})")

                prior_swing_low_idx = most_recent_confirmed_swing(swing_low_flags, swing_high_idx - 1, cfg.MAX_SWING_AGE_BARS)
                risk = stop - entry
                if prior_swing_low_idx is not None:
                    candidate_target = df["low"].values[prior_swing_low_idx]
                    candidate_rr = (entry - candidate_target) / risk if risk > 0 else 0
                    if candidate_target < entry and candidate_rr >= cfg.MIN_RR_FOR_SWING_TARGET:
                        target = candidate_target
                        reasons.append(f"Target: prior swing low ({candidate_rr:.2f}R)")
                if target is None:
                    target = entry - risk * cfg.FALLBACK_RR_TARGET_MULT
                    reasons.append(f"Target: fallback {cfg.FALLBACK_RR_TARGET_MULT}R (no valid prior swing low)")

    if side is None:
        return None

    risk = abs(entry - stop)
    reward = abs(target - entry)
    rr = round(reward / risk, 2) if risk > 0 else 0

    # Confidence: based on sweep depth (relative to ATR) and R:R quality
    sweep_depth_atr = (abs((sweep_low if side == "LONG" else sweep_high) -
                           (swing_low_price if side == "LONG" else swing_high_price)) / atr_val)
    confidence = min(100, 40 + sweep_depth_atr * 20 + min(rr, 5) * 8)
    confidence = round(confidence, 1)

    leverage = suggest_leverage(atr_pct)
    reasons.append(f"ATR {atr_pct:.3f}% of price (volatility gate passed)")

    # TEST MODE: reverse direction, force 1:1 R:R
    risk = abs(entry - stop)
    if side == "LONG":
        side = "SHORT"
        stop = entry + risk
        target = entry - risk
    else:
        side = "LONG"
        stop = entry - risk
        target = entry + risk
    rr = 1.0
    reasons.append("⚠️ TEST MODE: signal reversed, forced 1:1 R:R")

    return Signal(
        symbol=symbol, side=side, entry=entry, stop_loss=stop, take_profit=target,
        confidence=confidence, leverage=leverage, rr_ratio=rr, reasons=reasons, bar_index=i
    )
