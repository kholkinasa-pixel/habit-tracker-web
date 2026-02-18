import os


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"❌ Переменная окружения {name} не найдена!")
    return value


# Обязательные переменные
BOT_TOKEN = _require_env("BOT_TOKEN")
BACKEND_PUBLIC_URL = _require_env("BACKEND_PUBLIC_URL")

# WebApp (можно оставить в коде, можно тоже в env)
WEBAPP_URL = os.getenv(
    "WEBAPP_URL",
    "https://kholkinasa-pixel.github.io/habit-tracker-web/calendar.html"
)

# API
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", 8000))
