import os

# value_bot.config valida estas variables al importarse; en tests no se envía nada.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test-chat")
