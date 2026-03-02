"""
Модуль работы с PostgreSQL (Supabase) через asyncpg.
Использует пул соединений, совместим с Railway.
Поддерживает переопределение через PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE.
"""
import asyncio
import logging
import os
import ssl
from datetime import date
from typing import Optional, List, Tuple
from urllib.parse import urlparse, unquote

import asyncpg

from config import DATABASE_URL

logger = logging.getLogger(__name__)

# Константы лимитов
DEFAULT_MAX_HABITS = 2
EXTENDED_MAX_HABITS = 5

# Пул соединений: один на каждый event loop (main и api могут работать в разных циклах)
_pools: dict[asyncio.AbstractEventLoop, asyncpg.Pool] = {}


def _parse_database_url(url: str) -> dict:
    """
    Парсит DATABASE_URL на отдельные параметры.
    Избегает ошибки asyncpg при пароле с символами :, @ и т.д.
    """
    parsed = urlparse(url)

    user, password = None, None
    host, port = "localhost", 5432

    if parsed.netloc and "@" in parsed.netloc:
        userinfo, hostport = parsed.netloc.rsplit("@", 1)
        # user:password — только первый ':' разделяет (пароль может содержать : )
        if ":" in userinfo:
            user, password = userinfo.split(":", 1)
            user, password = unquote(user), unquote(password)
        else:
            user = unquote(userinfo) if userinfo else None
        # host:port (или host1:port1,host2:port2 — берём первый хост)
        hostport_part = hostport.split(",")[0]
        if ":" in hostport_part:
            host, port_str = hostport_part.rsplit(":", 1)
            port = int(port_str) if port_str.isdigit() else 5432
        else:
            host = hostport_part
    elif parsed.netloc:
        hostport = parsed.netloc.split(",")[0]
        if ":" in hostport:
            host, port_str = hostport.rsplit(":", 1)
            port = int(port_str) if port_str.isdigit() else 5432
        else:
            host = hostport

    database = (parsed.path or "/postgres").lstrip("/").split("?")[0] or "postgres"
    options = parsed.query or ""

    # Убираем пробелы и лишние символы (могут попасть при копировании)
    host = (host or "").strip()
    if not host:
        host = "localhost"

    result = {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": database,
        "options": options,
    }

    # Переопределение из переменных (PGHOST или DATABASE_HOST для Supabase/Railway)
    if os.getenv("PGHOST"):
        result["host"] = os.getenv("PGHOST", "").strip()
    elif os.getenv("DATABASE_HOST"):
        result["host"] = os.getenv("DATABASE_HOST", "").strip()
    if os.getenv("PGPORT"):
        try:
            result["port"] = int(os.getenv("PGPORT", "5432"))
        except ValueError:
            pass
    if os.getenv("PGUSER"):
        result["user"] = os.getenv("PGUSER")
    if os.getenv("PGPASSWORD"):
        result["password"] = os.getenv("PGPASSWORD")
    if os.getenv("PGDATABASE"):
        result["database"] = os.getenv("PGDATABASE", "").strip()

    return result


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

    # Парсим URL на компоненты — избегаем ошибки при пароле с ':' и др.
    params = _parse_database_url(DATABASE_URL)

    # Supabase требует SSL; для pooler отключаем проверку hostname (TargetServerAttributeNotMatched)
    options = params["options"] or ""
    if "sslmode" not in options.lower():
        options = f"{options}&sslmode=require" if options else "sslmode=require"
    use_ssl = "require" in options.lower() or "verify" in options.lower() or params["host"] != "localhost"

    ssl_ctx = None
    if use_ssl:
        # Supabase pooler: TargetServerAttributeNotMatched — отключаем проверку сертификата
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

    host, port, user, db_name = params["host"], params["port"], params["user"], params["database"]
    logger.info("Подключение к PostgreSQL: host=%s, port=%s, database=%s", host, port, db_name)

    try:
        pool = await asyncpg.create_pool(
            host=host,
            port=port,
            user=user,
            password=params["password"],
            database=db_name,
            ssl=ssl_ctx if use_ssl else False,
            target_session_attrs="any",  # Supabase pooler: избегаем TargetServerAttributeNotMatched
            min_size=1,
            max_size=10,
            command_timeout=60,
            statement_cache_size=0,  # pgbouncer (transaction/statement) не поддерживает prepared statements
        )
    except Exception as e:
        logger.error(
            "Ошибка подключения к PostgreSQL (host=%s, port=%s). "
            "Проверьте DATABASE_URL и доступность хоста. Ошибка: %s",
            host, port, e,
        )
        raise RuntimeError(
            f"Не удалось подключиться к PostgreSQL (host={host}, port={port}). "
            "Проверьте DATABASE_URL. Если host=postgres — задайте PGHOST или DATABASE_HOST "
            "на нужный хост (напр. aws-0-xxx.pooler.supabase.com для Supabase)."
        ) from e
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

        # 3. Таблица daily_logs (UNIQUE habit_id+log_date, каскадное удаление при удалении привычки)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_logs (
                id SERIAL PRIMARY KEY,
                habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
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
        return False, (
            f"У тебя максимум {max_habits} привычек. "
            "Много планов — мало дела! Постарайся сфокусироваться на существующих привычках для начала. "
            "Если уверен, что нужно ещё, свяжись с администратором @LanaAlexNa"
        )

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


async def update_habit_name(habit_id: int, user_id: int, new_name: str) -> Tuple[bool, Optional[str]]:
    """
    Обновляет название привычки. Проверяет, что habit_id принадлежит user_id.
    Возвращает (успех, сообщение_об_ошибке).
    """
    pool = _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM habits WHERE id = $1 AND user_id = $2",
            habit_id,
            user_id,
        )
        if not row:
            return False, "Привычка не найдена или не принадлежит тебе."

        await conn.execute(
            "UPDATE habits SET habit_text = $1 WHERE id = $2 AND user_id = $3",
            new_name.strip(),
            habit_id,
            user_id,
        )
    logger.info(f"Привычка {habit_id} обновлена пользователем {user_id}: {new_name}")
    return True, None


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


async def has_daily_log(habit_id: int, log_date: date) -> bool:
    """Проверяет, есть ли запись в daily_logs по привычке на дату."""
    pool = _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM daily_logs WHERE habit_id = $1 AND log_date = $2",
            habit_id,
            log_date,
        )
        return row is not None


async def save_daily_log(
    user_id: int,
    habit_id: int,
    efficiency_level: str,
    log_date: Optional[date] = None,
) -> Tuple[bool, bool]:
    """
    Сохраняет ежедневный лог по привычке (только если записи ещё нет).
    Возвращает (создан: bool, уже_был: bool).
    - (True, False): лог создан
    - (False, True): запись уже существовала, не изменяем
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
            logger.info(f"Лог уже существует для habit_id={habit_id} на дату {log_date}")
            return False, True
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
        return True, False


async def get_unmarked_habits_for_reminder(log_date: date) -> List[Tuple[int, int, str]]:
    """
    Возвращает (user_id, habit_id, habit_text) для привычек без записи в daily_logs на дату.
    Используется для отправки напоминаний только по неотмеченным привычкам.
    """
    pool = _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT h.user_id, h.id, h.habit_text
            FROM habits h
            LEFT JOIN daily_logs dl ON dl.habit_id = h.id AND dl.log_date = $1
            WHERE dl.id IS NULL
            ORDER BY h.user_id, h.id
            """,
            log_date,
        )
        return [(r["user_id"], r["id"], r["habit_text"]) for r in rows]


async def delete_habit(habit_id: int, user_id: int) -> Tuple[bool, Optional[str]]:
    """
    Удаляет привычку и все связанные записи из daily_logs.
    Проверяет, что habit_id принадлежит user_id.
    Возвращает (успех, сообщение_об_ошибке).
    """
    pool = _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM habits WHERE id = $1 AND user_id = $2",
            habit_id,
            user_id,
        )
        if not row:
            return False, "Привычка не найдена или не принадлежит тебе."

        await conn.execute("DELETE FROM daily_logs WHERE habit_id = $1", habit_id)
        await conn.execute("DELETE FROM habits WHERE id = $1 AND user_id = $2", habit_id, user_id)
        logger.info(f"Привычка {habit_id} удалена пользователем {user_id}")
    return True, None
