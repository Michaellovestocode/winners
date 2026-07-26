"""
Persists live bot state (open positions per symbol, daily trade log,
circuit breaker status) to a JSON file, so a crash or VPS restart doesn't
silently lose track of an open trade or the day's running total.

Called after every meaningful state change - it's a small file, writing
it every cycle is cheap and safer than trying to be clever about when to save.
"""
import json
import os
import dataclasses
import config as cfg
from strategy import Signal


def _signal_to_dict(sig: Signal):
    if sig is None:
        return None
    return dataclasses.asdict(sig)


def _signal_from_dict(d):
    if d is None:
        return None
    return Signal(**d)


def save_state(states: dict, daily_trades: list, day_state) -> None:
    data = {
        "daily_trades": daily_trades,
        "last_summary_date": day_state.date.isoformat() if day_state.date else None,
        "breaker_tripped": day_state.breaker_tripped,
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

    tmp_path = cfg.STATE_FILE + ".tmp"
    try:
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, cfg.STATE_FILE)  # atomic on POSIX and Windows
    except Exception as e:
        print(f"[state_store] Failed to save state: {e}")


def load_state():
    """
    Returns (found: bool, data: dict or None).
    Caller is responsible for reconstructing SymbolState/DayState objects
    from the raw dict, since this module doesn't import live_bot (avoids
    circular import).
    """
    if not os.path.exists(cfg.STATE_FILE):
        return False, None
    try:
        with open(cfg.STATE_FILE, "r") as f:
            data = json.load(f)
        return True, data
    except Exception as e:
        print(f"[state_store] Failed to load state, starting fresh: {e}")
        return False, None
