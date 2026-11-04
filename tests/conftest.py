import os

# Falsas antes de que config cargue .env (load_dotenv no pisa variables ya puestas):
# un test que llegue a send_telegram nunca usa el bot real.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test-chat")
