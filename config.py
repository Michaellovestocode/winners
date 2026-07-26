"""
Central configuration for the bot.
Tune these after seeing backtest results — don't just guess-and-check on live data.
"""

# --- Deriv API settings ---
# Free shared demo app_id for quick testing. For real use, register your own
# (free, 2 minutes) at https://api.deriv.com to avoid shared rate limits.
DERIV_APP_ID = "1089"

# --- Symbols to scan (Deriv forex format: "frx" + pair, no slash) ---
# Narrowed to AUDUSD and USDCAD after two separate backtest runs consistently
# showed these as the only pairs with positive expectancy AND positive net
# return after modeled trading costs. EURUSD/USDJPY/NZDUSD had thin or
# fee-negative edges; GBPUSD/USDCHF/EURGBP were dropped earlier for clearly
# negative expectancy.
SYMBOLS = [
    "frxAUDUSD",
    "frxUSDCAD",
]

# --- Timeframe for signal generation ---
# Granularity in SECONDS for Deriv candles (60=1m, 300=5m, 900=15m, 3600=1h, etc)
TIMEFRAME_SECONDS = 900          # 15-minute candles — adjust for scalp speed
HISTORY_LIMIT = 15000            # candles to pull per backtest (~156 days on 15m) — bigger sample for more confidence

# --- Trend filter (regime detection) ---
EMA_FAST = 50
EMA_SLOW = 200
# Minimum % gap between EMA_FAST and EMA_SLOW (relative to price) required
# to call it a real trend, not just a fresh/weak crossover. Forex majors move
# far less than crypto - this is much lower than the crypto version was.
# Re-check against your backtest's actual EMA-gap distribution and adjust.
TREND_STRENGTH_MIN_PCT = 0.1

# --- Entry trigger ---
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
RSI_MIDLINE = 50   # longs require RSI above this, shorts require RSI below this (momentum confirmation)
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# --- Volatility / risk ---
ATR_PERIOD = 14
ATR_SL_MULT = 1.5        # stop loss = ATR * this
ATR_TP_MULT = 3.0         # take profit = ATR * this (gives ~2:1 R:R baseline)
# Forex majors move far less per-candle than crypto — these thresholds are
# much lower than the crypto version. Re-check against your backtest's
# actual ATR% distribution per symbol and adjust if signals are too rare/frequent.
MIN_ATR_PCT = 0.02        # skip signal if ATR% of price below this (dead market)
MAX_ATR_PCT = 1.0         # skip signal if ATR% of price above this (news spike / unstable)

# --- Leverage suggestion ---
# Suggested leverage scales DOWN as volatility (ATR%) goes UP.
# Forex majors are far less volatile than crypto — Deriv multipliers can go
# up to 1:1000, but that's extreme risk. Keeping this conservative by default;
# raise only if you understand the risk of doing so.
MAX_LEVERAGE = 30
MIN_LEVERAGE = 5

# --- Cooldown / signal throttling ---
COOLDOWN_BARS = 6          # bars to wait after a closed trade on same symbol before re-signaling
MAX_SIGNALS_PER_DAY_PER_SYMBOL = 4   # quality over quantity — tune after backtest

# --- Backtest ---
STARTING_BALANCE = 1000
RISK_PER_TRADE_PCT = 1.0   # % of balance risked per trade (for equity curve simulation)
# Forex brokers typically charge via spread, not a taker fee. This is a rough
# placeholder cost per round-trip trade - check Deriv's actual spread for your
# chosen pairs and adjust. Tighter-spread pairs (EURUSD) will cost less than this.
TAKER_FEE_PCT = 0.02

# --- Live bot settings ---
POLL_INTERVAL_SECONDS = 30      # how often to check price for open-position TP/SL hits
KLINE_FETCH_LIMIT = 300         # candles to fetch each cycle for indicator calculation (needs > EMA_SLOW)
LOG_FILE = "live_bot.log"
STATE_FILE = "bot_state.json"   # persists open positions + daily trade log across restarts
API_CALL_DELAY_SECONDS = 1.5    # pause between each symbol's API call, avoids bursting Bybit's rate limit
RATE_LIMIT_BACKOFF_SECONDS = 60 # extra wait if Bybit's rate limit is hit, before resuming normal polling

# Daily loss circuit breaker: if cumulative leveraged P/L for the day (WAT)
# drops to or below this %, new signals pause until midnight WAT reset.
# Existing open positions are still monitored and closed normally either way.
MAX_DAILY_LOSS_PCT = -3.0
