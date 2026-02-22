import os


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"❌ Переменная окружения {name} не найдена!")
    return value


# Обязательные переменные
BOT_TOKEN = _require_env("BOT_TOKEN")
BACKEND_PUBLIC_URL = _require_env("BACKEND_PUBLIC_URL")
DATABASE_URL = _require_env("DATABASE_URL")

# WebApp: по умолчанию — тот же домен что и API (same-origin, без CORS-проблем в Telegram)
WEBAPP_URL = os.getenv("WEBAPP_URL", f"{BACKEND_PUBLIC_URL.rstrip('/')}/calendar.html")

# API
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", 8000))
