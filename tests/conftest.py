import os
import shutil
import tempfile

# Falsas antes de que config cargue .env (load_dotenv no pisa variables ya puestas):
# un test que llegue a send_telegram nunca usa el bot real.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test-chat")
# Respaldo de la BD a una carpeta temporal: un test que corra main() nunca escribe en iCloud
_BACKUP_TMP = tempfile.mkdtemp(prefix="valuebot-tests-")
os.environ["DB_BACKUP_DIR"] = os.path.join(_BACKUP_TMP, "ValueBot")


import pytest  # noqa: E402  (las variables de arriba van antes de cualquier import de value_bot)


@pytest.fixture(scope="session", autouse=True)
def _remove_backup_tmp():
    """La carpeta temporal de los respaldos se borra al terminar la sesión."""
    yield
    shutil.rmtree(_BACKUP_TMP, ignore_errors=True)


@pytest.fixture
def ctx():
    """RunContext nuevo por test: el estado de una corrida nunca se comparte."""
    from value_bot.run_context import RunContext
    return RunContext()
