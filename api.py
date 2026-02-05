"""
FastAPI сервер для отдачи данных привычек в JSON.
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from database import init_db, get_daily_logs_for_user, get_habit

logger = logging.getLogger(__name__)

# Маппинг efficiency_level из БД в значения для календаря
LEVEL_TO_CALENDAR = {
    "Да": "good",
    "Хорошо": "good",
    "Минимум": "minimum",
    "Нет": "no-data",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    # shutdown if needed


app = FastAPI(title="Habit Tracker API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/users/{user_id}/calendar")
async def get_calendar(user_id: int) -> dict[str, str]:
    """
    Возвращает данные календаря для пользователя: дата -> уровень (good | minimum | no-data).
    """
    try:
        logs = await get_daily_logs_for_user(user_id)
    except Exception as e:
        logger.exception("Ошибка при получении логов для user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="Database error") from e
    result = {}
    for log_date, efficiency_level in logs:
        result[log_date] = LEVEL_TO_CALENDAR.get(efficiency_level, "no-data")
    return result


@app.get("/api/users/{user_id}/habit")
async def get_user_habit(user_id: int):
    """Текст привычки пользователя."""
    habit_text = await get_habit(user_id)
    if habit_text is None:
        raise HTTPException(status_code=404, detail="Habit not set")
    return {"user_id": user_id, "habit_text": habit_text}


@app.get("/api/health")
async def health():
    return {"status": "ok"}
