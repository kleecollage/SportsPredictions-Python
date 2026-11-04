import os

# Falsas antes de que config cargue .env (load_dotenv no pisa variables ya puestas):
# un test que llegue a send_telegram nunca usa el bot real.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test-chat")


import pytest  # noqa: E402  (las variables de arriba van antes de cualquier import de value_bot)


@pytest.fixture
def ctx():
    """RunContext nuevo por test: el estado de una corrida nunca se comparte."""
    from value_bot.run_context import RunContext
    return RunContext()
