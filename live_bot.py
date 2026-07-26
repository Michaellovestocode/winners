"""
Live multi-symbol forex signal bot, powered by Deriv's WebSocket API.

Reuses strategy.py / indicators.py UNCHANGED from the backtester — this
matters, it's what makes the backtest results meaningful for what actually
runs live. If you tune strategy.py or config.py later, re-run run_backtest.py
first to validate the change before letting it run live.

State (open positions, daily trade log, circuit breaker status) is saved to
bot_state.json after every change, so a crash or VPS restart picks up right
where it left off instead of silently losing track of an open trade.

Forex markets close on weekends - the bot handles fetch failures during
closed hours gracefully (logs and skips, doesn't crash or spam alerts).

Run with:
    python live_bot.py

Recommended on a VPS: run inside `screen` or `tmux`, or set up as a systemd
service, so it keeps running after you disconnect SSH. Example with screen:
    screen -S signalbot
    python live_bot.py
    (Ctrl+A then D to detach, `screen -r signalbot` to reattach)
"""
import time
import datetime
import traceback
import pandas as pd

import config as cfg
import state_store
import deriv_client
from indicators import add_all_indicators
from strategy import evaluate_bar, Signal
from telegram_notifier import send_message, format_signal_message, format_result_message, format_daily_summary

WAT = datetime.timezone(datetime.timedelta(hours=1))  # West Africa Time, UTC+1, no DST

# Trades closed today, across all symbols - reset after each daily summary is sent
daily_trades = []

# Tracks consecutive fetch failures per symbol, to distinguish "market closed
# for the weekend" (expected, quiet) from "something is actually broken" (alert-worthy)
consecutive_failures = {}
MARKET_CLOSED_ALERT_THRESHOLD = 20  # only alert after this many consecutive failures


class SymbolState:
    def __init__(self, symbol):
        self.symbol = symbol
        self.active_signal: Signal = None
        self.last_checked_bar_time = None
        self.cooldown_until_ts = 0
        self.signals_today = 0
        self.day_marker = datetime.datetime.now(WAT).date()


class DayState:
    """Tracks the current WAT day for the daily summary + circuit breaker."""
    def __init__(self):
        self.date = None
        self.breaker_tripped = False


def log(msg: str):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    try:
        with open(cfg.LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass  # don't crash the bot over a logging issue


def candles_to_df(candles: list) -> pd.DataFrame:
    """Converts Deriv's candle format into the OHLCV dataframe shape the strategy expects."""
    df = pd.DataFrame(candles)
    df = df.rename(columns={"epoch": "timestamp"})
    df["open"] = df["open"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["close"] = df["close"].astype(float)
    df["volume"] = 0  # Deriv forex candles don't include volume - unused by current strategy logic
    return df[["timestamp", "open", "high", "low", "close", "volume"]]


def restore_state(states: dict, day_state: DayState):
    """Loads bot_state.json if present and repopulates in-memory state."""
    found, data = state_store.load_state()
    if not found:
        log("No previous state file found - starting fresh.")
        return

    global daily_trades
    daily_trades = data.get("daily_trades", [])

    last_summary_date_str = data.get("last_summary_date")
    day_state.date = datetime.date.fromisoformat(last_summary_date_str) if last_summary_date_str else None
    day_state.breaker_tripped = data.get("breaker_tripped", False)

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
    state_store.save_state(states, daily_trades, day_state)


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
        log(f"{symbol}: still failing after {count} attempts - this is longer than a normal market closure, worth checking.")
    # deliberately not spamming Telegram for every failure - weekends are expected and frequent


def check_active_signal(exchange_unused, state: SymbolState, symbol: str) -> bool:
    """If in a position, check current price against SL/TP. Returns True if state changed."""
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
        log(f"{state.symbol}: {result} hit. PnL {pnl_pct:+.2f}% (price move, pre-leverage)")

        daily_trades.append({
            "symbol": state.symbol,
            "side": sig.side,
            "result": result,
            "pnl_pct": pnl_pct,
            "leveraged_pnl_pct": pnl_pct * sig.leverage,
        })

        state.active_signal = None
        state.cooldown_until_ts = time.time() + (cfg.COOLDOWN_BARS * cfg.TIMEFRAME_SECONDS)
        return True

    return False


def current_daily_loss_pct() -> float:
    """Sum of leveraged P/L across all trades closed today (negative if net loss)."""
    return sum(t["leveraged_pnl_pct"] for t in daily_trades)


def check_circuit_breaker(day_state: DayState) -> bool:
    """
    Returns True if new signals should be paused for the rest of the day.
    Existing open positions are NEVER blocked by this - they always get
    monitored through to TP/SL regardless of breaker status.
    """
    if day_state.breaker_tripped:
        return True

    loss_pct = current_daily_loss_pct()
    if loss_pct <= cfg.MAX_DAILY_LOSS_PCT:
        day_state.breaker_tripped = True
        send_message(
            f"🛑 *Daily loss limit reached* ({loss_pct:+.2f}% vs {cfg.MAX_DAILY_LOSS_PCT}% limit).\n"
            f"New signals paused until midnight WAT reset. Open positions (if any) still being monitored."
        )
        log(f"Circuit breaker tripped. Daily loss: {loss_pct:+.2f}%")
        return True

    return False


def check_for_new_signal(exchange_unused, state: SymbolState, symbol: str, day_state: DayState) -> bool:
    """Returns True if a new signal was fired (state changed)."""
    reset_daily_count_if_needed(state)

    if check_circuit_breaker(day_state):
        return False
    if time.time() < state.cooldown_until_ts:
        return False
    if state.signals_today >= cfg.MAX_SIGNALS_PER_DAY_PER_SYMBOL:
        return False

    try:
        candles = deriv_client.fetch_candles(symbol, cfg.TIMEFRAME_SECONDS, cfg.KLINE_FETCH_LIMIT)
        note_fetch_success(symbol)
    except Exception as e:
        note_fetch_failure(symbol, e)
        return False

    df = candles_to_df(candles)
    if len(df) < max(cfg.EMA_SLOW, cfg.MACD_SLOW) + 10:
        log(f"{state.symbol}: not enough candles yet ({len(df)})")
        return False

    df = add_all_indicators(df, cfg)

    # Evaluate the last FULLY CLOSED candle, not the currently-forming one
    last_closed_idx = len(df) - 2
    last_closed_time = df.iloc[last_closed_idx]["timestamp"]

    if state.last_checked_bar_time == last_closed_time:
        return False  # already evaluated this closed candle, nothing new yet
    state.last_checked_bar_time = last_closed_time

    sig = evaluate_bar(df, last_closed_idx, state.symbol)
    if sig:
        msg = format_signal_message(sig)
        send_message(msg)
        log(f"{state.symbol}: {sig.side} signal fired. Confidence {sig.confidence}%, R:R {sig.rr_ratio}")
        state.active_signal = sig
        state.signals_today += 1
        return True

    return False


def send_daily_summary_if_needed(day_state: DayState) -> bool:
    """Returns True if a summary was sent (state changed)."""
    now_wat = datetime.datetime.now(WAT)
    current_date = now_wat.date()

    if day_state.date is None:
        day_state.date = current_date
        return False

    if current_date != day_state.date:
        msg = format_daily_summary(daily_trades, day_state.date)
        send_message(msg)
        log(f"Daily summary sent for {day_state.date}: {len(daily_trades)} trades")
        daily_trades.clear()
        day_state.date = current_date
        day_state.breaker_tripped = False  # fresh day, fresh loss budget
        return True

    return False


def main():
    states = {symbol: SymbolState(symbol) for symbol in cfg.SYMBOLS}
    day_state = DayState()

    restore_state(states, day_state)

    send_message(
        f"🤖 Forex signal bot started (Deriv).\nWatching: {', '.join(cfg.SYMBOLS)}\n"
        f"Timeframe: {cfg.TIMEFRAME_SECONDS // 60}m | Max signals/day/symbol: {cfg.MAX_SIGNALS_PER_DAY_PER_SYMBOL}\n"
        f"Daily loss limit: {cfg.MAX_DAILY_LOSS_PCT}% | Summary sent at 12am West Africa Time.\n"
        f"Note: forex markets close on weekends - the bot will go quiet then, that's expected."
    )
    log(f"Bot started. Watching {cfg.SYMBOLS}")
    persist_state(states, day_state)

    while True:
        for symbol in cfg.SYMBOLS:
            state = states[symbol]
            changed = False
            try:
                if state.active_signal:
                    changed = check_active_signal(None, state, symbol)
                else:
                    changed = check_for_new_signal(None, state, symbol, day_state)
            except Exception as e:
                log(f"{symbol}: unexpected error: {e}")
                traceback.print_exc()

            if changed:
                persist_state(states, day_state)

            time.sleep(cfg.API_CALL_DELAY_SECONDS)  # spread out requests

        try:
            if send_daily_summary_if_needed(day_state):
                persist_state(states, day_state)
        except Exception as e:
            log(f"Daily summary check failed: {e}")
            traceback.print_exc()

        time.sleep(cfg.POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
