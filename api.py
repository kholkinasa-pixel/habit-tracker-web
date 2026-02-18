"""
FastAPI сервер для отдачи данных привычек в JSON.
"""
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from database import init_db, get_daily_logs_for_user, get_daily_logs_for_habit, get_habits

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
    await init_db()
    yield
    # shutdown if needed


app = FastAPI(title="Habit Tracker API", lifespan=lifespan)

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
