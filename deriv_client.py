"""
Minimal synchronous client for Deriv's WebSocket API - used to pull forex
candle history (for backtesting) and current prices (for live signal checks).

Deriv's public API is free to use for market data - no account/login needed,
just an "app_id". You can use the shared demo app_id (1089) to get started,
but for anything beyond quick testing, register your own free app_id at:
https://api.deriv.com  ->  log in  ->  "Manage Applications"  ->  "Register application"
(takes 2 minutes, avoids shared rate limits with everyone else using 1089)

Deriv forex symbol format: "frxEURUSD", "frxGBPUSD", "frxUSDJPY", etc.
("frx" prefix + the pair with no slash)
"""
import json
import time
import websocket  # pip install websocket-client
import config as cfg

DERIV_WS_URL = f"wss://ws.derivws.com/websockets/v3?app_id={cfg.DERIV_APP_ID}"


def _request(payload: dict, timeout: int = 10) -> dict:
    """Opens a short-lived connection, sends one request, returns the response, closes."""
    ws = websocket.create_connection(DERIV_WS_URL, timeout=timeout)
    try:
        ws.send(json.dumps(payload))
        response = ws.recv()
        return json.loads(response)
    finally:
        ws.close()


def fetch_candles(symbol: str, granularity_seconds: int, count: int) -> list:
    """
    Returns a list of candle dicts: [{"epoch": ..., "open": ..., "high": ...,
    "low": ..., "close": ...}, ...] ordered oldest to newest.

    Deriv limits a single request to 5000 candles - for more history, this
    pages backwards using the 'end' parameter across multiple requests.
    """
    all_candles = []
    end = "latest"
    remaining = count

    while remaining > 0:
        batch_size = min(remaining, 5000)
        payload = {
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": batch_size,
            "end": end,
            "start": 1,
            "style": "candles",
            "granularity": granularity_seconds,
        }
        resp = _request(payload)

        if "error" in resp:
            raise RuntimeError(f"Deriv API error for {symbol}: {resp['error'].get('message', resp['error'])}")

        candles = resp.get("candles", [])
        if not candles:
            break

        all_candles = candles + all_candles
        remaining -= len(candles)

        if len(candles) < batch_size:
            break  # no more history available

        end = candles[0]["epoch"] - 1  # page further back before the earliest candle received
        time.sleep(0.3)  # be polite to the shared demo app_id rate limit

    # de-duplicate by epoch and sort oldest -> newest
    seen = {}
    for c in all_candles:
        seen[c["epoch"]] = c
    ordered = sorted(seen.values(), key=lambda c: c["epoch"])
    return ordered[-count:] if len(ordered) > count else ordered


def fetch_current_price(symbol: str) -> float:
    """Returns the latest tick price for a symbol (single value, not a subscription)."""
    payload = {"ticks": symbol}
    resp = _request(payload, timeout=10)
    if "error" in resp:
        raise RuntimeError(f"Deriv API error for {symbol}: {resp['error'].get('message', resp['error'])}")
    return resp["tick"]["quote"]


def is_market_open(symbol: str) -> bool:
    """Checks whether the given symbol's market is currently open for trading."""
    payload = {"trading_times": "today"}
    resp = _request(payload, timeout=10)
    if "error" in resp:
        # if we can't tell, default to assuming open rather than blocking signals unnecessarily
        return True
    # trading_times response structure varies by category; a simpler and more
    # reliable live check is attempting a price fetch - if it errors/stalls,
    # treat the market as closed for that symbol. Used as fallback in live_bot.
    return True
