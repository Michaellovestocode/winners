# Deriv Multi-Symbol Forex Signal Bot

## What this is

A signal bot for forex majors on Deriv, sending Telegram alerts for entries,
TP/SL hits, and a daily performance summary. It does **not** auto-execute
trades — you place trades yourself based on the signals.

Reuses the same validated architecture from the crypto version: confluence-based
entries (trend + momentum + volatility gate), ATR-based SL/TP, confidence
scoring, leverage suggestion, state persistence across restarts, and a daily
loss circuit breaker.

## Before going live: backtest first

**Do not skip this step.** The config values (ATR thresholds, trend strength
filter, RSI midline) were tuned for crypto's volatility and have been adjusted
for forex's much smaller price swings — but they haven't been validated on
real forex data yet. Run the backtest and check the numbers before trusting
any live signal.

```bash
pip install -r requirements.txt
python run_backtest.py
```

This pulls real historical candles from Deriv (free, no account needed) for
every symbol in `config.py`, runs the exact live strategy logic bar-by-bar,
and reports win rate, expectancy, R:R, and drawdown per symbol.

**The number that matters most: `expectancy_pct_per_trade`.** If it's ≤ 0 for
a symbol, drop it from `config.py`'s `SYMBOLS` list before going live — same
process that got ADA and LINK dropped from the crypto version.

## Setup for live bot

1. Get a Deriv `app_id`: register free at https://api.deriv.com (2 minutes).
   Update `DERIV_APP_ID` in `config.py` (the shared demo id `1089` works for
   testing but has shared rate limits - use your own for anything serious).

2. Copy `bot_secrets_example.py` → `bot_secrets.py`, fill in your Telegram
   bot token and chat ID (see comments in that file for how to get them).

3. Run the live bot:
   ```bash
   python live_bot.py
   ```

## Forex-specific notes

- **Markets close on weekends.** The bot detects fetch failures and logs them
  quietly rather than alerting on every failure - it only flags something as
  worth checking after ~20 consecutive failures (well beyond a normal weekend
  closure). You'll see the bot go quiet Friday evening WAT and resume Monday.
- **Leverage default range is lower than the crypto version** (`MAX_LEVERAGE = 30`
  vs `20` for crypto, but starting conservative). Deriv multipliers can go up
  to 1:1000 - don't raise this without understanding the real risk of doing so.
- **Fee/spread modeling is a rough placeholder** (`TAKER_FEE_PCT` in
  `config.py`). Forex brokers charge via spread, not a flat taker fee - check
  Deriv's actual spread for your chosen pairs and adjust if you want more
  accurate backtest numbers.

## What to tune if backtest results are weak

Same process as before - all in `config.py`:
- `EMA_FAST` / `EMA_SLOW` - trend filter sensitivity
- `TREND_STRENGTH_MIN_PCT` - how strong a trend must be to count (already
  lowered for forex, but re-check against your actual backtest's EMA-gap
  distribution)
- `RSI_MIDLINE`, `RSI_OVERBOUGHT`, `RSI_OVERSOLD` - momentum confirmation strictness
- `ATR_SL_MULT` / `ATR_TP_MULT` - your risk:reward per trade
- `MIN_ATR_PCT` / `MAX_ATR_PCT` - volatility gate (already lowered for forex's
  smaller candle-to-candle moves)
- `TIMEFRAME_SECONDS` - 900 (15m) is the current default

**Honesty note, same as before:** a backtest doesn't model slippage, spread
widening during news events, or requotes - real performance will be somewhat
worse than backtest numbers. Treat this as a filter for "is this obviously
broken," not proof of profitability.
