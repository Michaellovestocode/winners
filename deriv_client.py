"""
Minimal synchronous client for Deriv's WebSocket API - used to pull forex
candle history (for backtesting) and current prices (for live signal checks).

Deriv's public API is free to use for market data - no account/login needed,
just an "app_id". Use the shared demo app_id (1089) - this must be a NUMERIC
id for the classic wss://ws.derivws.com WebSocket endpoint. Note: Deriv's
newer developer portal (developers.deriv.com) issues alphanumeric app IDs
for a different, newer API system - those do NOT work with this endpoint
and will fail with "InvalidAppID". Stick with the numeric 1089 (or register
your own numeric app_id via the classic api.deriv.com flow) for this client.

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
            break

        end = candles[0]["epoch"] - 1
        time.sleep(0.3)

    seen = {}
    for c in all_candles:
        seen[c["epoch"]] = c
    ordered = sorted(seen.values(), key=lambda c: c["epoch"])
    return ordered[-count:] if len(ordered) > count else ordered


def fetch_current_price(symbol: str) -> float:
    """
    Returns the latest tick price for a symbol.

    FIX (2nd attempt): the plain {"ticks": symbol} endpoint proved unreliable
    with this app_id - both omitting "subscribe" and setting "subscribe": 0
    were rejected by Deriv's validation. Sidestepping this entirely by reusing
    the SAME "ticks_history" request type that already works reliably for
    fetch_candles() (proven throughout all backtesting), just asking for a
    single most-recent tick instead of a full candle history.
    """
    payload = {
        "ticks_history": symbol,
        "adjust_start_time": 1,
        "count": 1,
        "end": "latest",
        "start": 1,
        "style": "ticks",
    }
    resp = _request(payload, timeout=10)
    if "error" in resp:
        raise RuntimeError(f"Deriv API error for {symbol}: {resp['error'].get('message', resp['error'])}")

    history = resp.get("history", {})
    prices = history.get("prices", [])
    if not prices:
        raise RuntimeError(f"Deriv API returned no price data for {symbol}: {resp}")
    return prices[-1]
