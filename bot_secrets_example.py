"""
Fill in your own values below, then rename this file to bot_secrets.py
(bot_secrets.py is what telegram_notifier.py actually imports).

IMPORTANT: this file must NOT be named "secrets.py" - that collides with
Python's built-in "secrets" standard library module and causes confusing
import errors elsewhere (numpy/pandas use it internally).

To get TELEGRAM_BOT_TOKEN:
  1. Message @BotFather on Telegram
  2. /newbot -> follow prompts -> it gives you a token like "123456:ABC-DEF..."

To get TELEGRAM_CHAT_ID:
  1. Message your new bot at least once (anything, e.g. "hi")
  2. Visit: https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
  3. Look for "chat":{"id": ...} in the response - that number is your chat id
     (if using a group, add the bot to the group first, send a message there instead)

NEVER share this file or commit it to a public repo once filled in.
"""

TELEGRAM_BOT_TOKEN = "PUT_YOUR_BOT_TOKEN_HERE"
TELEGRAM_CHAT_ID = "PUT_YOUR_CHAT_ID_HERE"
