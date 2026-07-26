"""
Config for the liquidity-sweep reversal strategy (inspired by the "failed
breakdown / stop-hunt reversal" idea from the trading video). Isolated from
the main config.py so this experiment can't accidentally break the working
trend-following bot.

Shares symbols/timeframe/history/Deriv app id from the main config.py, but
has its own strategy-specific parameters.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config as main_cfg

# --- Inherited from main config ---
DERIV_APP_ID = main_cfg.DERIV_APP_ID
# Narrowed after full 8-pair backtest: EURUSD, USDCAD, USDCHF showed positive
# expectancy AND positive net return after costs. AUDUSD/GBPUSD/USDJPY/NZDUSD/
# EURGBP were dropped - some had positive raw expectancy but negative real
# return once trading costs were applied (e.g. NZDUSD: +0.047% expectancy,
# the highest of all pairs, but still -3.1% actual return - cost drag from
# trade frequency ate the edge). Interestingly, EURUSD/USDCHF were dropped
# from the TREND strategy but work well here - different strategies suit
# different pairs.
SYMBOLS = [
    "frxEURUSD",
    "frxUSDCAD",
    "frxUSDCHF",
]
TIMEFRAME_SECONDS = main_cfg.TIMEFRAME_SECONDS
HISTORY_LIMIT = main_cfg.HISTORY_LIMIT
ATR_PERIOD = main_cfg.ATR_PERIOD
MIN_ATR_PCT = main_cfg.MIN_ATR_PCT
MAX_ATR_PCT = main_cfg.MAX_ATR_PCT
MAX_LEVERAGE = main_cfg.MAX_LEVERAGE
MIN_LEVERAGE = main_cfg.MIN_LEVERAGE
STARTING_BALANCE = main_cfg.STARTING_BALANCE
RISK_PER_TRADE_PCT = main_cfg.RISK_PER_TRADE_PCT
TAKER_FEE_PCT = main_cfg.TAKER_FEE_PCT
COOLDOWN_BARS = main_cfg.COOLDOWN_BARS

# --- Swing point detection ---
# A candle counts as a confirmed swing high/low if it's the most extreme
# point within this many candles on EACH side. Larger = fewer, more
# significant swings. Smaller = more swings, more noise.
SWING_LOOKBACK = 5

# How far back (in candles) to search for the most recent swing low/high
# when evaluating a potential sweep. Keeps the reference point "recent."
MAX_SWING_AGE_BARS = 100

# --- Sweep (failed breakdown/breakout) detection ---
# Price must break past the swing level by at least this many ATR multiples
# to count as a genuine liquidity sweep, not just noise around the level.
SWEEP_MARGIN_ATR_MULT = 0.1

# How many recent candles (including the current one) to check for the sweep low/high.
SWEEP_LOOKBACK_BARS = 3

# --- Reversal confirmation ---
# The confirming candle's body (close-open) must be at least this many ATR
# multiples to count as a "strong" reversal candle, not a weak wick-back.
MIN_REVERSAL_BODY_ATR_MULT = 0.5

# --- Stop loss / target ---
# Stop placed this many ATR multiples beyond the sweep's extreme point.
STOP_BUFFER_ATR_MULT = 0.2

# If a valid prior swing (opposite direction) exists above/below entry with
# at least this R:R, use it as the target (mirrors "target prior resistance/
# support" from real discretionary trading). Otherwise fall back to a fixed
# R:R multiple.
MIN_RR_FOR_SWING_TARGET = 1.2
FALLBACK_RR_TARGET_MULT = 2.0

LOG_FILE = "live_bot_reversal.log"
STATE_FILE = "bot_state_reversal.json"
