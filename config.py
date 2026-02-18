import os

BOT_TOKEN = os.getenv("BOT_TOKEN")

# URL для Telegram Web App (укажите свой хостинг, например ngrok или GitHub Pages)
WEBAPP_URL = "https://kholkinasa-pixel.github.io/habit-tracker-web/calendar.html"

# FastAPI сервер для API привычек
API_HOST = "0.0.0.0"
API_PORT = 8000

BACKEND_PUBLIC_URL = os.getenv("BACKEND_PUBLIC_URL")
