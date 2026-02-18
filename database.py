import aiosqlite
import logging
from datetime import date
from typing import Optional, List, Tuple

logger = logging.getLogger(__name__)

DB_PATH = "habits.db"
DEFAULT_MAX_HABITS = 2
EXTENDED_MAX_HABITS = 5


async def init_db() -> None:
    """Инициализация базы данных, миграции и создание таблиц"""
    async with aiosqlite.connect(DB_PATH) as db:
        # 1. Таблица users (лимит привычек на пользователя)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                max_habits INTEGER NOT NULL DEFAULT 2
            )
        """)

        # 2. Проверяем нужна ли миграция habits (старая схема с UNIQUE(user_id))
        cursor = await db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='habits'"
        )
        row = await cursor.fetchone()
        if row:
            sql = row[0] or ""
            if "UNIQUE(user_id)" in sql or "UNIQUE (user_id)" in sql:
                # Миграция: пересоздаём habits без UNIQUE(user_id)
                await db.execute("""
                    CREATE TABLE habits_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        habit_text TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                await db.execute(
                    "INSERT INTO habits_new (id, user_id, habit_text, created_at) "
                    "SELECT id, user_id, habit_text, created_at FROM habits"
                )
                await db.execute("DROP TABLE habits")
                await db.execute("ALTER TABLE habits_new RENAME TO habits")
                # Заполняем users из habits
                await db.execute(
                    "INSERT OR IGNORE INTO users (user_id, max_habits) "
                    "SELECT user_id, 2 FROM habits"
                )
                logger.info("Миграция habits выполнена")
        else:
            # Новая установка — создаём habits
            await db.execute("""
                CREATE TABLE IF NOT EXISTS habits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    habit_text TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

        # 3. Проверяем нужна ли миграция daily_logs (старая схема без habit_id)
        cursor = await db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='daily_logs'"
        )
        row = await cursor.fetchone()
        if row:
            sql = row[0] or ""
            if "habit_id" not in sql:
                # Миграция: пересоздаём daily_logs с habit_id
                await db.execute("""
                    CREATE TABLE daily_logs_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        habit_id INTEGER NOT NULL,
                        log_date DATE NOT NULL,
                        efficiency_level TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(habit_id, log_date)
                    )
                """)
                await db.execute("""
                    INSERT INTO daily_logs_new (habit_id, log_date, efficiency_level, created_at)
                    SELECT (SELECT id FROM habits WHERE user_id = daily_logs.user_id LIMIT 1),
                           log_date, efficiency_level, created_at
                    FROM daily_logs
                """)
                await db.execute("DROP TABLE daily_logs")
                await db.execute("ALTER TABLE daily_logs_new RENAME TO daily_logs")
                logger.info("Миграция daily_logs выполнена")
        else:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS daily_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    habit_id INTEGER NOT NULL,
                    log_date DATE NOT NULL,
                    efficiency_level TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(habit_id, log_date)
                )
            """)

        await db.commit()
        logger.info("База данных инициализирована")


async def get_user_max_habits(user_id: int) -> int:
    """Возвращает max_habits для пользователя (2 или 5). Создаёт запись при первом обращении."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT max_habits FROM users WHERE user_id = ?",
            (user_id,)
        )
        row = await cursor.fetchone()
        if row:
            return row[0]
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, max_habits) VALUES (?, ?)",
            (user_id, DEFAULT_MAX_HABITS)
        )
        await db.commit()
        return DEFAULT_MAX_HABITS


async def get_habits_count(user_id: int) -> int:
    """Количество привычек пользователя"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM habits WHERE user_id = ?",
            (user_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def add_habit(user_id: int, habit_text: str) -> Tuple[bool, Optional[str]]:
    """
    Добавляет новую привычку пользователю.
    Возвращает (успех, сообщение_об_ошибке).
    """
    max_habits = await get_user_max_habits(user_id)
    count = await get_habits_count(user_id)
    if count >= max_habits:
        return False, f"У тебя максимум {max_habits} привычек. Чтобы добавить ещё, свяжись с администратором."

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO habits (user_id, habit_text) VALUES (?, ?)",
            (user_id, habit_text)
        )
        await db.commit()
        logger.info(f"Привычка создана для пользователя {user_id}: {habit_text}")
        return True, None


async def get_habits(user_id: int) -> List[Tuple[int, str]]:
    """Возвращает [(habit_id, habit_text), ...]"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, habit_text FROM habits WHERE user_id = ? ORDER BY id",
            (user_id,)
        )
        return await cursor.fetchall()


async def get_habit_by_id(habit_id: int) -> Optional[str]:
    """Текст привычки по id"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT habit_text FROM habits WHERE id = ?",
            (habit_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def get_all_users_with_habits() -> List[Tuple[int, int, str]]:
    """Возвращает (user_id, habit_id, habit_text) для каждой привычки"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT user_id, id, habit_text FROM habits ORDER BY user_id, id"
        )
        return await cursor.fetchall()


async def get_daily_logs_for_user(user_id: int) -> List[Tuple[str, str]]:
    """
    Возвращает все ежедневные логи пользователя (агрегация по всем привычкам).
    При нескольких логах на одну дату берётся «лучший» уровень.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT dl.log_date, dl.efficiency_level
            FROM daily_logs dl
            JOIN habits h ON h.id = dl.habit_id
            WHERE h.user_id = ?
            ORDER BY dl.log_date
        """, (user_id,))
        rows = await cursor.fetchall()
    # Объединяем по дате — берём лучший уровень
    level_rank = {"Хорошо потрудились": 3, "Хорошо": 3, "Да": 3,
                  "Базовый минимум": 2, "Минимум": 2,
                  "Нет": 1, "good": 3, "minimum": 2, "no-data": 1}
    by_date = {}
    for log_date, eff in rows:
        key = str(log_date)[:10]
        rank = level_rank.get((eff or "").strip(), 1)
        if key not in by_date or rank > level_rank.get((by_date[key] or "").strip(), 1):
            by_date[key] = eff
    return [(k, v) for k, v in sorted(by_date.items())]


async def get_daily_logs_for_habit(habit_id: int) -> List[Tuple[str, str]]:
    """Логи по конкретной привычке"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT log_date, efficiency_level FROM daily_logs WHERE habit_id = ? ORDER BY log_date",
            (habit_id,)
        )
        return await cursor.fetchall()


async def save_daily_log(
    user_id: int,
    habit_id: int,
    efficiency_level: str,
    log_date: Optional[date] = None
) -> bool:
    """
    Сохраняет ежедневный лог по привычке.
    Возвращает True если лог создан, False если обновлён.
    """
    if log_date is None:
        log_date = date.today()

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id FROM daily_logs WHERE habit_id = ? AND log_date = ?",
            (habit_id, log_date.isoformat())
        )
        existing = await cursor.fetchone()
        if existing:
            await db.execute(
                "UPDATE daily_logs SET efficiency_level = ?, created_at = CURRENT_TIMESTAMP "
                "WHERE habit_id = ? AND log_date = ?",
                (efficiency_level, habit_id, log_date.isoformat())
            )
            await db.commit()
            logger.info(f"Лог обновлен для habit_id={habit_id} на дату {log_date}")
            return False
        else:
            await db.execute(
                "INSERT INTO daily_logs (habit_id, log_date, efficiency_level) VALUES (?, ?, ?)",
                (habit_id, log_date.isoformat(), efficiency_level)
            )
            await db.commit()
            logger.info(f"Лог сохранен для habit_id={habit_id} на дату {log_date}")
            return True
