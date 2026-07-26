"""
Combined live signal bot: routes each symbol to whichever strategy showed a
real backtested edge for it specifically.

  - frxAUDUSD    -> trend-following strategy (strategy.py)
  - frxEURUSD    -> liquidity-sweep reversal strategy (reversal_strategy/strategy_reversal.py)
  - frxUSDCAD    -> reversal strategy
  - frxUSDCHF    -> reversal strategy

Both strategies were backtested independently on real Deriv data before
being combined here - this file doesn't introduce new logic, it just runs
the two already-validated strategies side by side, sharing the same
Telegram notifications, state persistence, and daily loss circuit breaker
(applied across the WHOLE combined bot, not per-strategy, since it's
protecting total account risk).

Run with:
    python live_bot_combined.py

Recommended on a VPS: run inside `screen` or `tmux`, same as before.
"""
import time
import datetime
import traceback
import sys
import os

import pandas as pd

import config as trend_cfg
import deriv_client
import dataclasses
import json
from indicators import add_all_indicators
from strategy import evaluate_bar as evaluate_bar_trend, Signal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "reversal_strategy"))
import config_reversal as rev_cfg
from strategy_reversal import evaluate_bar as evaluate_bar_reversal, find_confirmed_swings
from indicators import atr as compute_atr

from telegram_notifier import send_message, format_signal_message, format_result_message, format_daily_summary, format_weekly_summary

WAT = datetime.timezone(datetime.timedelta(hours=1))

# --- Symbol -> strategy routing, based on backtested results ---
SYMBOL_STRATEGY = {
    "frxAUDUSD": "trend",
    "frxEURUSD": "reversal",
    "frxUSDCAD": "reversal",
    "frxUSDCHF": "reversal",
}
SYMBOLS = list(SYMBOL_STRATEGY.keys())

# Portfolio-level settings (shared across both strategies, not per-strategy)
MAX_DAILY_LOSS_PCT = trend_cfg.MAX_DAILY_LOSS_PCT
MAX_SIGNALS_PER_DAY_PER_SYMBOL = trend_cfg.MAX_SIGNALS_PER_DAY_PER_SYMBOL
POLL_INTERVAL_SECONDS = trend_cfg.POLL_INTERVAL_SECONDS
API_CALL_DELAY_SECONDS = trend_cfg.API_CALL_DELAY_SECONDS
KLINE_FETCH_LIMIT = trend_cfg.KLINE_FETCH_LIMIT
TIMEFRAME_SECONDS = trend_cfg.TIMEFRAME_SECONDS  # shared: both configs use the same 900s (15m)
COOLDOWN_BARS = trend_cfg.COOLDOWN_BARS
LOG_FILE = "live_bot_combined.log"
STATE_FILE = "bot_state_combined.json"

daily_trades = []
weekly_trades = []
consecutive_failures = {}
MARKET_CLOSED_ALERT_THRESHOLD = 20


class SymbolState:
    def __init__(self, symbol):
        self.symbol = symbol
        self.active_signal: Signal = None
        self.last_checked_bar_time = None
        self.cooldown_until_ts = 0
        self.signals_today = 0
        self.day_marker = datetime.datetime.now(WAT).date()


class DayState:
    def __init__(self):
        self.date = None
        self.breaker_tripped = False
        self.week_id = None  # (iso_year, iso_week) tuple, tracks when to send weekly summary


def log(msg: str):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def candles_to_df(candles: list) -> pd.DataFrame:
    df = pd.DataFrame(candles)
    df = df.rename(columns={"epoch": "timestamp"})
    df["open"] = df["open"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["close"] = df["close"].astype(float)
    df["volume"] = 0
    return df[["timestamp", "open", "high", "low", "close", "volume"]]


def _signal_to_dict(sig):
    return dataclasses.asdict(sig) if sig else None


def restore_state(states: dict, day_state: DayState):
    if not os.path.exists(STATE_FILE):
        log("No previous state file found - starting fresh.")
        return
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
    except Exception as e:
        log(f"Failed to load state, starting fresh: {e}")
        return

    global daily_trades, weekly_trades
    daily_trades = data.get("daily_trades", [])
    weekly_trades = data.get("weekly_trades", [])

    last_summary_date_str = data.get("last_summary_date")
    day_state.date = datetime.date.fromisoformat(last_summary_date_str) if last_summary_date_str else None
    day_state.breaker_tripped = data.get("breaker_tripped", False)
    week_id_raw = data.get("week_id")
    day_state.week_id = tuple(week_id_raw) if week_id_raw else None

    symbols_data = data.get("symbols", {})
    for symbol, state in states.items():
        sdata = symbols_data.get(symbol)
        if not sdata:
            continue
        active_signal_dict = sdata.get("active_signal")
        state.active_signal = Signal(**active_signal_dict) if active_signal_dict else None
        state.last_checked_bar_time = sdata.get("last_checked_bar_time")
        state.cooldown_until_ts = sdata.get("cooldown_until_ts", 0)
        state.signals_today = sdata.get("signals_today", 0)
        day_marker_str = sdata.get("day_marker")
        state.day_marker = datetime.date.fromisoformat(day_marker_str) if day_marker_str else datetime.datetime.now(WAT).date()

    open_positions = [s for s, st in states.items() if st.active_signal]
    log(f"Restored previous state. {len(daily_trades)} trades logged today. "
        f"Open positions: {open_positions if open_positions else 'none'}.")


def persist_state(states: dict, day_state: DayState):
    data = {
        "daily_trades": daily_trades,
        "weekly_trades": weekly_trades,
        "last_summary_date": day_state.date.isoformat() if day_state.date else None,
        "breaker_tripped": day_state.breaker_tripped,
        "week_id": list(day_state.week_id) if day_state.week_id else None,
        "symbols": {},
    }
    for symbol, state in states.items():
        data["symbols"][symbol] = {
            "active_signal": _signal_to_dict(state.active_signal),
            "last_checked_bar_time": state.last_checked_bar_time,
            "cooldown_until_ts": state.cooldown_until_ts,
            "signals_today": state.signals_today,
            "day_marker": state.day_marker.isoformat() if state.day_marker else None,
        }
    tmp_path = STATE_FILE + ".tmp"
    try:
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, STATE_FILE)
    except Exception as e:
        log(f"Failed to save state: {e}")


def reset_daily_count_if_needed(state: SymbolState):
    today = datetime.datetime.now(WAT).date()
    if state.day_marker != today:
        state.day_marker = today
        state.signals_today = 0


def note_fetch_success(symbol: str):
    if consecutive_failures.get(symbol, 0) >= MARKET_CLOSED_ALERT_THRESHOLD:
        log(f"{symbol}: data flowing again after {consecutive_failures[symbol]} failed attempts.")
    consecutive_failures[symbol] = 0


def note_fetch_failure(symbol: str, error: Exception):
    consecutive_failures[symbol] = consecutive_failures.get(symbol, 0) + 1
    count = consecutive_failures[symbol]
    if count == 1:
        log(f"{symbol}: fetch failed ({error}). Likely market closed (weekend/holiday) - will keep retrying quietly.")
    elif count == MARKET_CLOSED_ALERT_THRESHOLD:
        log(f"{symbol}: still failing after {count} attempts - longer than a normal closure, worth checking.")


def check_active_signal(state: SymbolState, symbol: str) -> bool:
    sig = state.active_signal
    try:
        last_price = deriv_client.fetch_current_price(symbol)
        note_fetch_success(symbol)
    except Exception as e:
        note_fetch_failure(symbol, e)
        return False

    hit_sl = (last_price <= sig.stop_loss) if sig.side == "LONG" else (last_price >= sig.stop_loss)
    hit_tp = (last_price >= sig.take_profit) if sig.side == "LONG" else (last_price <= sig.take_profit)

    if hit_sl or hit_tp:
        result = "SL" if hit_sl else "TP"
        exit_price = sig.stop_loss if hit_sl else sig.take_profit
        pnl_pct = ((exit_price - sig.entry) / sig.entry * 100) if sig.side == "LONG" \
            else ((sig.entry - exit_price) / sig.entry * 100)

        msg = format_result_message(state.symbol, sig.side, result, sig.entry, exit_price, pnl_pct, sig.leverage)
        send_message(msg)
        log(f"{state.symbol} [{SYMBOL_STRATEGY[symbol]}]: {result} hit. PnL {pnl_pct:+.2f}% (price move, pre-leverage)")

        daily_trades.append({
            "symbol": state.symbol,
            "side": sig.side,
            "result": result,
            "pnl_pct": pnl_pct,
            "leveraged_pnl_pct": pnl_pct * sig.leverage,
        })

        state.active_signal = None
        state.cooldown_until_ts = time.time() + (COOLDOWN_BARS * TIMEFRAME_SECONDS)
        return True

    return False


def current_daily_loss_pct() -> float:
    return sum(t["leveraged_pnl_pct"] for t in daily_trades)


def check_circuit_breaker(day_state: DayState) -> bool:
    if day_state.breaker_tripped:
        return True
    loss_pct = current_daily_loss_pct()
    if loss_pct <= MAX_DAILY_LOSS_PCT:
        day_state.breaker_tripped = True
        send_message(
            f"🛑 *Daily loss limit reached* ({loss_pct:+.2f}% vs {MAX_DAILY_LOSS_PCT}% limit).\n"
            f"New signals paused (both strategies) until midnight WAT reset. Open positions still monitored."
        )
        log(f"Circuit breaker tripped. Daily loss: {loss_pct:+.2f}%")
        return True
    return False


def check_for_new_signal_trend(state: SymbolState, symbol: str) -> bool:
    df_raw = candles_to_df(deriv_client.fetch_candles(symbol, TIMEFRAME_SECONDS, KLINE_FETCH_LIMIT))
    if len(df_raw) < max(trend_cfg.EMA_SLOW, trend_cfg.MACD_SLOW) + 10:
        log(f"{symbol}: not enough candles yet ({len(df_raw)})")
        return False

    df = add_all_indicators(df_raw, trend_cfg)
    last_closed_idx = len(df) - 2
    last_closed_time = df.iloc[last_closed_idx]["timestamp"]

    if state.last_checked_bar_time == last_closed_time:
        return False
    state.last_checked_bar_time = last_closed_time

    sig = evaluate_bar_trend(df, last_closed_idx, symbol)
    if sig:
        msg = format_signal_message(sig)
        send_message(msg)
        log(f"{symbol} [trend]: {sig.side} signal fired. Confidence {sig.confidence}%, R:R {sig.rr_ratio}")
        state.active_signal = sig
        state.signals_today += 1
        return True
    return False


def check_for_new_signal_reversal(state: SymbolState, symbol: str) -> bool:
    df_raw = candles_to_df(deriv_client.fetch_candles(symbol, TIMEFRAME_SECONDS, KLINE_FETCH_LIMIT))
    min_needed = rev_cfg.SWING_LOOKBACK * 2 + rev_cfg.SWEEP_LOOKBACK_BARS + rev_cfg.MAX_SWING_AGE_BARS + 10
    if len(df_raw) < min_needed:
        log(f"{symbol}: not enough candles yet ({len(df_raw)}, need {min_needed})")
        return False

    df = df_raw.copy()
    df["atr"] = compute_atr(df, rev_cfg.ATR_PERIOD)
    df["atr_pct"] = (df["atr"] / df["close"]) * 100
    swing_high_flags, swing_low_flags = find_confirmed_swings(df, rev_cfg.SWING_LOOKBACK)
    df.attrs["swing_high_flags"] = swing_high_flags
    df.attrs["swing_low_flags"] = swing_low_flags

    last_closed_idx = len(df) - 2
    last_closed_time = df.iloc[last_closed_idx]["timestamp"]

    if state.last_checked_bar_time == last_closed_time:
        return False
    state.last_checked_bar_time = last_closed_time

    sig = evaluate_bar_reversal(df, last_closed_idx, symbol)
    if sig:
        msg = format_signal_message(sig)
        send_message(msg)
        log(f"{symbol} [reversal]: {sig.side} signal fired. Confidence {sig.confidence}%, R:R {sig.rr_ratio}")
        state.active_signal = sig
        state.signals_today += 1
        return True
    return False


def check_for_new_signal(state: SymbolState, symbol: str, day_state: DayState) -> bool:
    reset_daily_count_if_needed(state)
    if check_circuit_breaker(day_state):
        return False
    if time.time() < state.cooldown_until_ts:
        return False
    if state.signals_today >= MAX_SIGNALS_PER_DAY_PER_SYMBOL:
        return False

    strategy = SYMBOL_STRATEGY[symbol]
    try:
        if strategy == "trend":
            return check_for_new_signal_trend(state, symbol)
        else:
            return check_for_new_signal_reversal(state, symbol)
    except Exception as e:
        note_fetch_failure(symbol, e)
        return False


def send_daily_summary_if_needed(day_state: DayState) -> bool:
    now_wat = datetime.datetime.now(WAT)
    current_date = now_wat.date()

    if day_state.date is None:
        day_state.date = current_date
        day_state.week_id = current_date.isocalendar()[:2]
        return False

    if current_date != day_state.date:
        msg = format_daily_summary(daily_trades, day_state.date)
        send_message(msg)
        log(f"Daily summary sent for {day_state.date}: {len(daily_trades)} trades")

        # Roll today's trades into the running weekly total BEFORE clearing them
        weekly_trades.extend(daily_trades)
        daily_trades.clear()

        day_state.date = current_date
        day_state.breaker_tripped = False

        # Check if we've crossed into a new ISO week - if so, the week that
        # just ended is complete, send its summary and start fresh.
        new_week_id = current_date.isocalendar()[:2]
        if day_state.week_id is not None and new_week_id != day_state.week_id:
            iso_year, iso_week = day_state.week_id
            week_start = datetime.date.fromisocalendar(iso_year, iso_week, 1)  # Monday
            week_end = datetime.date.fromisocalendar(iso_year, iso_week, 7)    # Sunday
            weekly_msg = format_weekly_summary(weekly_trades, week_start, week_end)
            send_message(weekly_msg)
            log(f"Weekly summary sent for {week_start} to {week_end}: {len(weekly_trades)} trades")
            weekly_trades.clear()

        day_state.week_id = new_week_id
        return True

    return False


def main():
    states = {symbol: SymbolState(symbol) for symbol in SYMBOLS}
    day_state = DayState()

    restore_state(states, day_state)

    strategy_summary = ", ".join(f"{s} ({SYMBOL_STRATEGY[s]})" for s in SYMBOLS)
    send_message(
        f"🤖 Combined signal bot started.\n{strategy_summary}\n"
        f"Timeframe: {TIMEFRAME_SECONDS // 60}m | Max signals/day/symbol: {MAX_SIGNALS_PER_DAY_PER_SYMBOL}\n"
        f"Daily loss limit: {MAX_DAILY_LOSS_PCT}% | Summary sent at 12am West Africa Time."
    )
    log(f"Bot started. Symbol->strategy: {SYMBOL_STRATEGY}")
    persist_state(states, day_state)

    while True:
        for symbol in SYMBOLS:
            state = states[symbol]
            changed = False
            try:
                if state.active_signal:
                    changed = check_active_signal(state, symbol)
                else:
                    changed = check_for_new_signal(state, symbol, day_state)
            except Exception as e:
                log(f"{symbol}: unexpected error: {e}")
                traceback.print_exc()

            if changed:
                persist_state(states, day_state)

            time.sleep(API_CALL_DELAY_SECONDS)

        try:
            if send_daily_summary_if_needed(day_state):
                persist_state(states, day_state)
        except Exception as e:
            log(f"Daily summary check failed: {e}")
            traceback.print_exc()

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
