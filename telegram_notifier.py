"""
Sends messages to Telegram via direct HTTP call to the Bot API.
Kept deliberately simple (no async framework) since the live bot is a
straightforward polling loop, not an interactive bot.
"""
import requests

try:
    from bot_secrets import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
except ImportError:
    raise ImportError(
        "bot_secrets.py not found. Copy bot_secrets_example.py to bot_secrets.py and fill in "
        "your Telegram bot token and chat id."
    )


def send_message(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            print(f"[telegram] Failed to send message: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"[telegram] Error sending message: {e}")


def format_signal_message(sig) -> str:
    emoji = "🟢" if sig.side == "LONG" else "🔴"
    reasons_text = "\n".join(f"  • {r}" for r in sig.reasons)
    return (
        f"{emoji} *{sig.side} SIGNAL — {sig.symbol}*\n\n"
        f"Entry: `{sig.entry:.6f}`\n"
        f"Stop Loss: `{sig.stop_loss:.6f}`\n"
        f"Take Profit: `{sig.take_profit:.6f}`\n"
        f"R:R Ratio: `{sig.rr_ratio}`\n"
        f"Suggested Leverage: `{sig.leverage}x`\n"
        f"Confidence: `{sig.confidence}%`\n\n"
        f"*Reasons:*\n{reasons_text}\n\n"
        f"⚠️ Not financial advice. Use proper risk management."
    )


def format_result_message(symbol: str, side: str, result: str, entry: float, exit_price: float, pnl_pct: float, leverage: int) -> str:
    emoji = "✅" if result == "TP" else "❌"
    result_word = "TAKE PROFIT HIT" if result == "TP" else "STOP LOSS HIT"
    leveraged_pnl = pnl_pct * leverage
    return (
        f"{emoji} *{result_word} — {symbol}*\n\n"
        f"Side: `{side}`\n"
        f"Entry: `{entry:.6f}`\n"
        f"Exit: `{exit_price:.6f}`\n"
        f"Price Move: `{pnl_pct:+.2f}%`\n"
        f"Est. P/L at {leverage}x leverage: `{leveraged_pnl:+.2f}%`\n\n"
        f"_(Estimated — actual P/L depends on your position size and fees)_"
    )


def format_daily_summary(trades: list, for_date) -> str:
    if not trades:
        return (
            f"📊 *Daily Summary — {for_date}*\n\n"
            f"No trades closed today."
        )

    wins = [t for t in trades if t["result"] == "TP"]
    losses = [t for t in trades if t["result"] == "SL"]
    total_leveraged_pnl = sum(t["leveraged_pnl_pct"] for t in trades)

    overall_emoji = "🟢" if total_leveraged_pnl > 0 else ("🔴" if total_leveraged_pnl < 0 else "⚪")
    verdict = "PROFIT" if total_leveraged_pnl > 0 else ("LOSS" if total_leveraged_pnl < 0 else "BREAKEVEN")

    lines = [
        f"{overall_emoji} *Daily Summary — {for_date}*",
        f"Overall: *{verdict}* ({total_leveraged_pnl:+.2f}% combined, leverage-adjusted)",
        "",
        f"Trades: {len(trades)}  |  Wins: {len(wins)}  |  Losses: {len(losses)}",
        "",
    ]

    for t in trades:
        emoji = "✅" if t["result"] == "TP" else "❌"
        lines.append(f"{emoji} {t['symbol']} {t['side']} — {t['leveraged_pnl_pct']:+.2f}%")

    lines.append("\n_(Estimates only — actual results depend on your real position sizing and fees)_")
    return "\n".join(lines)


def format_weekly_summary(trades: list, week_start, week_end) -> str:
    """
    Weekly rollup - sent when a new ISO week begins (naturally lines up with
    forex markets being closed over the weekend, so this lands right when
    there's nothing live to interrupt).
    """
    header_range = f"{week_start} to {week_end}"

    if not trades:
        return (
            f"📅 *Weekly Summary — {header_range}*\n\n"
            f"No trades closed this week."
        )

    wins = [t for t in trades if t["result"] == "TP"]
    losses = [t for t in trades if t["result"] == "SL"]
    total_leveraged_pnl = sum(t["leveraged_pnl_pct"] for t in trades)
    win_rate = (len(wins) / len(trades) * 100) if trades else 0

    overall_emoji = "🟢" if total_leveraged_pnl > 0 else ("🔴" if total_leveraged_pnl < 0 else "⚪")
    verdict = "PROFIT" if total_leveraged_pnl > 0 else ("LOSS" if total_leveraged_pnl < 0 else "BREAKEVEN")

    lines = [
        f"{overall_emoji} *Weekly Summary — {header_range}*",
        f"Overall: *{verdict}* ({total_leveraged_pnl:+.2f}% combined, leverage-adjusted)",
        "",
        f"Trades: {len(trades)}  |  Wins: {len(wins)}  |  Losses: {len(losses)}  |  Win rate: {win_rate:.1f}%",
        "",
        "*By symbol:*",
    ]

    by_symbol = {}
    for t in trades:
        by_symbol.setdefault(t["symbol"], []).append(t)

    for symbol, sym_trades in by_symbol.items():
        sym_pnl = sum(t["leveraged_pnl_pct"] for t in sym_trades)
        sym_wins = sum(1 for t in sym_trades if t["result"] == "TP")
        emoji = "✅" if sym_pnl > 0 else ("❌" if sym_pnl < 0 else "➖")
        lines.append(f"{emoji} {symbol}: {sym_pnl:+.2f}% ({sym_wins}/{len(sym_trades)} wins)")

    lines.append("\n_(Estimates only — actual results depend on your real position sizing and fees)_")
    return "\n".join(lines)
