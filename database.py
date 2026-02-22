"""
Модуль работы с PostgreSQL (Supabase) через asyncpg.
Использует пул соединений, совместим с Railway.
"""
import asyncio
import logging
from datetime import date
from typing import Optional, List, Tuple

import asyncpg

from config import DATABASE_URL

logger = logging.getLogger(__name__)

# Константы лимитов
DEFAULT_MAX_HABITS = 2
EXTENDED_MAX_HABITS = 5

# Пул соединений: один на каждый event loop (main и api могут работать в разных циклах)
_pools: dict[asyncio.AbstractEventLoop, asyncpg.Pool] = {}


def _get_pool() -> asyncpg.Pool:
    """Возвращает пул для текущего event loop. Вызывать только после init_db()."""
    loop = asyncio.get_running_loop()
    pool = _pools.get(loop)
    if pool is None:
        raise RuntimeError(
            "База данных не инициализирована. Вызовите init_db() перед работой с БД."
        )
    return pool


async def init_db() -> None:
    """
    Инициализация пула соединений и создание таблиц в PostgreSQL.
    Вызывать при старте приложения (main.py и api lifespan).
    """
    loop = asyncio.get_running_loop()
    if loop in _pools:
        # Пул уже создан для этого event loop
        return

    # Supabase требует SSL; если в URL нет sslmode, добавляем
    db_url = DATABASE_URL
    if "sslmode" not in db_url and "postgres" in db_url:
        sep = "&" if "?" in db_url else "?"
        db_url = f"{db_url}{sep}sslmode=require"

    pool = await asyncpg.create_pool(
        db_url,
        min_size=1,
        max_size=10,
        command_timeout=60,
    )
    _pools[loop] = pool
    logger.info("Пул соединений PostgreSQL создан")

    async with pool.acquire() as conn:
        # 1. Таблица users (лимит привычек на пользователя)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                max_habits INTEGER NOT NULL DEFAULT 2
            )
        """)

        # 2. Таблица habits
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS habits (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                habit_text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 3. Таблица daily_logs
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_logs (
                id SERIAL PRIMARY KEY,
                habit_id INTEGER NOT NULL,
                log_date DATE NOT NULL,
                efficiency_level TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(habit_id, log_date)
            )
        """)

    logger.info("База данных PostgreSQL инициализирована")


async def close_db() -> None:
    """Закрытие пула соединений. Вызывать при завершении приложения."""
    loop = asyncio.get_running_loop()
    pool = _pools.pop(loop, None)
    if pool:
        await pool.close()
        logger.info("Пул соединений PostgreSQL закрыт")


async def get_user_max_habits(user_id: int) -> int:
    """Возвращает max_habits для пользователя (2 или 5). Создаёт запись при первом обращении."""
    pool = _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT max_habits FROM users WHERE user_id = $1",
            user_id,
        )
        if row:
            return row["max_habits"]

        # ON CONFLICT DO NOTHING — аналог INSERT OR IGNORE в SQLite
        await conn.execute(
            """
            INSERT INTO users (user_id, max_habits) VALUES ($1, $2)
            ON CONFLICT (user_id) DO NOTHING
            """,
            user_id,
            DEFAULT_MAX_HABITS,
        )
        return DEFAULT_MAX_HABITS


async def get_habits_count(user_id: int) -> int:
    """Количество привычек пользователя"""
    pool = _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) AS cnt FROM habits WHERE user_id = $1",
            user_id,
        )
        return row["cnt"] if row else 0


async def add_habit(user_id: int, habit_text: str) -> Tuple[bool, Optional[str]]:
    """
    Добавляет новую привычку пользователю.
    Возвращает (успех, сообщение_об_ошибке).
    """
    max_habits = await get_user_max_habits(user_id)
    count = await get_habits_count(user_id)
    if count >= max_habits:
        return False, f"У тебя максимум {max_habits} привычек. Чтобы добавить ещё, свяжись с администратором."

    pool = _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO habits (user_id, habit_text) VALUES ($1, $2)",
            user_id,
            habit_text,
        )
    logger.info(f"Привычка создана для пользователя {user_id}: {habit_text}")
    return True, None


async def get_habits(user_id: int) -> List[Tuple[int, str]]:
    """Возвращает [(habit_id, habit_text), ...]"""
    pool = _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, habit_text FROM habits WHERE user_id = $1 ORDER BY id",
            user_id,
        )
        return [(r["id"], r["habit_text"]) for r in rows]


async def get_habit_by_id(habit_id: int) -> Optional[str]:
    """Текст привычки по id"""
    pool = _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT habit_text FROM habits WHERE id = $1",
            habit_id,
        )
        return row["habit_text"] if row else None


async def get_all_users_with_habits() -> List[Tuple[int, int, str]]:
    """Возвращает (user_id, habit_id, habit_text) для каждой привычки"""
    pool = _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_id, id, habit_text FROM habits ORDER BY user_id, id"
        )
        return [(r["user_id"], r["id"], r["habit_text"]) for r in rows]


async def get_daily_logs_for_user(user_id: int) -> List[Tuple[str, str]]:
    """
    Возвращает все ежедневные логи пользователя (агрегация по всем привычкам).
    При нескольких логах на одну дату берётся «лучший» уровень.
    """
    pool = _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT dl.log_date, dl.efficiency_level
            FROM daily_logs dl
            JOIN habits h ON h.id = dl.habit_id
            WHERE h.user_id = $1
            ORDER BY dl.log_date
        """, user_id)

    # Объединяем по дате — берём лучший уровень
    level_rank = {
        "Хорошо потрудились": 3,
        "Хорошо": 3,
        "Да": 3,
        "Базовый минимум": 2,
        "Минимум": 2,
        "Нет": 1,
        "good": 3,
        "minimum": 2,
        "no-data": 1,
    }
    by_date = {}
    for r in rows:
        log_date, eff = r["log_date"], r["efficiency_level"]
        key = str(log_date)[:10]
        rank = level_rank.get((eff or "").strip(), 1)
        if key not in by_date or rank > level_rank.get((by_date[key] or "").strip(), 1):
            by_date[key] = eff
    return [(k, v) for k, v in sorted(by_date.items())]


async def get_daily_logs_for_habit(habit_id: int) -> List[Tuple[str, str]]:
    """Логи по конкретной привычке"""
    pool = _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT log_date, efficiency_level FROM daily_logs WHERE habit_id = $1 ORDER BY log_date",
            habit_id,
        )
        return [(str(r["log_date"]), r["efficiency_level"]) for r in rows]


async def save_daily_log(
    user_id: int,
    habit_id: int,
    efficiency_level: str,
    log_date: Optional[date] = None,
) -> bool:
    """
    Сохраняет ежедневный лог по привычке.
    Возвращает True если лог создан, False если обновлён.
    """
    if log_date is None:
        log_date = date.today()

    pool = _get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id FROM daily_logs WHERE habit_id = $1 AND log_date = $2",
            habit_id,
            log_date,
        )
        if existing:
            await conn.execute(
                """
                UPDATE daily_logs
                SET efficiency_level = $1, created_at = CURRENT_TIMESTAMP
                WHERE habit_id = $2 AND log_date = $3
                """,
                efficiency_level,
                habit_id,
                log_date,
            )
            logger.info(f"Лог обновлен для habit_id={habit_id} на дату {log_date}")
            return False
        else:
            await conn.execute(
                """
                INSERT INTO daily_logs (habit_id, log_date, efficiency_level)
                VALUES ($1, $2, $3)
                """,
                habit_id,
                log_date,
                efficiency_level,
            )
            logger.info(f"Лог сохранен для habit_id={habit_id} на дату {log_date}")
            return True
