"""
FastAPI сервер для отдачи данных привычек в JSON.
"""
import logging
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from database import init_db, close_db, get_daily_logs_for_user, get_daily_logs_for_habit, get_habits

logger = logging.getLogger(__name__)

# Маппинг efficiency_level из БД в значения для календаря
# Включаем старые значения для обратной совместимости с уже сохранёнными логами
LEVEL_TO_CALENDAR = {
    "Хорошо потрудились": "good",
    "Хорошо": "good",
    "Да": "good",
    "Базовый минимум": "minimum",
    "Минимум": "minimum",
    "Нет": "no-data",
    "good": "good",
    "minimum": "minimum",
    "no-data": "no-data",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await init_db()
    except Exception as e:
        logger.exception("Ошибка при инициализации БД в lifespan: %s", e)
        raise
    try:
        yield
    finally:
        try:
            await close_db()
        except Exception as e:
            logger.exception("Ошибка при закрытии БД: %s", e)


app = FastAPI(title="Habit Tracker API", lifespan=lifespan)


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Логируем полный traceback при любой необработанной ошибке."""
    logger.exception(
        "Необработанная ошибка при %s %s: %s\n%s",
        request.method if hasattr(request, "method") else "?",
        str(request.url) if hasattr(request, "url") else "?",
        exc,
        traceback.format_exc(),
    )
    raise exc


# Корневая директория проекта (рядом с api.py)
_STATIC_DIR = Path(__file__).resolve().parent


@app.get("/calendar.html")
async def serve_calendar_html():
    """Раздача Mini App — same-origin, избегаем проблем с CORS в Telegram WebView."""
    path = _STATIC_DIR / "calendar.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, media_type="text/html")


@app.get("/calendar.js")
async def serve_calendar_js():
    """Раздача скрипта календаря."""
    path = _STATIC_DIR / "calendar.js"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, media_type="application/javascript")


# allow_credentials=False обязательно при allow_origins=["*"], иначе браузер блокирует
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


@app.get("/api/users/{user_id}/calendar")
async def get_calendar(user_id: int, habit_id: Optional[int] = None) -> dict[str, str]:
    """
    Возвращает данные календаря: дата -> уровень (good | minimum | no-data).
    Если habit_id задан — только для этой привычки; иначе — агрегация по всем.
    """
    try:
        if habit_id is not None:
            habits = await get_habits(user_id)
            if not any(h[0] == habit_id for h in habits):
                raise HTTPException(status_code=404, detail="Habit not found")
            logs = await get_daily_logs_for_habit(habit_id)
        else:
            logs = await get_daily_logs_for_user(user_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка при получении логов для user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="Database error") from e
    result = {}
    for log_date, efficiency_level in logs:
        key = str(log_date)[:10]  # YYYY-MM-DD
        val = (efficiency_level or "").strip()
        result[key] = LEVEL_TO_CALENDAR.get(val, "no-data")
    return result


@app.get("/api/users/{user_id}/habit")
async def get_user_habit(user_id: int):
    """Список привычек пользователя."""
    habits = await get_habits(user_id)
    if not habits:
        raise HTTPException(status_code=404, detail="Habits not set")
    return {
        "user_id": user_id,
        "habits": [{"id": h[0], "text": h[1]} for h in habits],
    }


@app.get("/api/health")
async def health():
    return {"status": "ok"}
