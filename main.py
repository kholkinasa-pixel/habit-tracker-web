import asyncio
import logging
import threading
from urllib.parse import quote

import uvicorn
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from aiogram.filters import Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import BOT_TOKEN, WEBAPP_URL, API_HOST, API_PORT, BACKEND_PUBLIC_URL
from database import (
    init_db,
    add_habit,
    get_habit_by_id,
    get_habits,
    get_all_users_with_habits,
    save_daily_log,
)

# В Python 3.9+ с uvloop в главном потоке ещё нет event loop — создаём его до aiogram
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = None
dp = Dispatcher()
scheduler = AsyncIOScheduler()


def _webapp_url(user_id=None) -> str:
    """URL Web App с api_url и опционально user_id (fallback для initData при Reply Keyboard)."""
    base = WEBAPP_URL
    params = []
    if BACKEND_PUBLIC_URL:
        params.append(f"api_url={quote(BACKEND_PUBLIC_URL.rstrip('/'))}")
    if user_id is not None:
        params.append(f"user_id={user_id}")
    if params:
        sep = "&" if "?" in base else "?"
        base = f"{base}{sep}{'&'.join(params)}"
    return base


def get_bot_menu(user_id: int) -> ReplyKeyboardMarkup:
    """Меню с URL, содержащим user_id (initData при Reply Keyboard web_app часто пустой)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Посмотреть трекер привычек", web_app=WebAppInfo(url=_webapp_url(user_id)))],
            [KeyboardButton(text="Добавить привычку"), KeyboardButton(text="Посмотреть список привычек")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )

async def send_daily_reminder():
    """Отправляет ежедневное напоминание всем пользователям с привычками"""
    try:
        rows = await get_all_users_with_habits()
        logger.info(f"Отправка напоминаний. Найдено привычек: {len(rows)}")

        for user_id, habit_id, habit_text in rows:
            try:
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="Нет", callback_data=f"habit_no_{habit_id}"),
                        InlineKeyboardButton(text="Базовый минимум", callback_data=f"habit_min_{habit_id}")
                    ],
                    [
                        InlineKeyboardButton(text="Хорошо потрудились", callback_data=f"habit_good_{habit_id}")
                    ]
                ])

                await bot.send_message(
                    chat_id=user_id,
                    text=f"📅 Ежедневная проверка привычки!\n\n📝 Твоя привычка: {habit_text}\n\nКак дела сегодня?",
                    reply_markup=keyboard
                )
                logger.info(f"Напоминание отправлено пользователю {user_id} (habit_id={habit_id})")
            except Exception as e:
                logger.error(f"Ошибка при отправке напоминания пользователю {user_id}: {e}")
    except Exception as e:
        logger.error(f"Критическая ошибка в send_daily_reminder: {e}")


@dp.callback_query(F.data.startswith("habit_"))
async def handle_habit_callback(callback: CallbackQuery):
    """Обработчик нажатий на кнопки"""
    data = callback.data
    user_id = callback.from_user.id

    # Извлекаем habit_id и тип ответа из callback_data (habit_no_123, habit_min_123, habit_good_123)
    parts = data.split("_", 2)
    if len(parts) < 3:
        await callback.answer("Неизвестная команда")
        return
    try:
        habit_id = int(parts[2])
    except ValueError:
        await callback.answer("Неизвестная команда")
        return

    if data.startswith("habit_no_"):
        response = "Нет"
        efficiency_level = "Нет"
        emoji = "❌"
    elif data.startswith("habit_min_"):
        response = "Базовый минимум"
        efficiency_level = "Базовый минимум"
        emoji = "⚡"
    elif data.startswith("habit_good_"):
        response = "Хорошо потрудились"
        efficiency_level = "Хорошо потрудились"
        emoji = "🌟"
    else:
        await callback.answer("Неизвестная команда")
        return

    try:
        await save_daily_log(user_id, habit_id, efficiency_level)
        logger.info(f"Ответ пользователя {user_id} для habit_id={habit_id} сохранен: {efficiency_level}")
    except Exception as e:
        logger.error(f"Ошибка при сохранении ответа пользователя {user_id}: {e}")

    await callback.answer(f"{emoji} Записал: {response}")

    habit_text = await get_habit_by_id(habit_id)
    if habit_text:
        await callback.message.edit_text(
            f"📅 Ежедневная проверка привычки!\n\n"
            f"📝 Твоя привычка: {habit_text}\n\n"
            f"{emoji} Твой ответ: {response}\n\n"
            f"Спасибо за ответ! До завтра! 👋"
        )


@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Привет! Я бот-трекер привычек. Чем могу помочь?\n\n"
        "Используй меню ниже или /sethabit <текст привычки> чтобы добавить привычку (максимум 2).\n\n"
        "Каждый день в 21:00 по МСК я буду спрашивать тебя о твоих привычках!",
        reply_markup=get_bot_menu(message.from_user.id),
    )


@dp.message(Command("calendar"))
async def cmd_calendar(message: Message) -> None:
    """Открыть календарь привычек (Web App)"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть трекер", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])
    await message.answer("Календарь привычек:", reply_markup=keyboard)


@dp.message(Command("sethabit"))
async def cmd_set_habit(message: Message) -> None:
    """Команда для сохранения или обновления привычки"""
    user_id = message.from_user.id
    command_parts = message.text.split(maxsplit=1)
    if len(command_parts) < 2:
        await message.answer("Пожалуйста, укажи текст привычки после команды.\n"
                           "Пример: /sethabit Пить 2 литра воды в день")
        return
    
    habit_text = command_parts[1].strip()
    
    if not habit_text:
        await message.answer("Текст привычки не может быть пустым!")
        return

    success, err_msg = await add_habit(user_id, habit_text)
    if success:
        await message.answer(f"✅ Привычка сохранена!\n\n📝 Твоя привычка: {habit_text}\n\n"
                            f"Каждый день в 21:00 по МСК я буду спрашивать тебя о твоей привычке!")
    else:
        await message.answer(err_msg or "Не удалось добавить привычку.")


@dp.message(F.text == "Добавить привычку")
async def cmd_menu_add_habit(message: Message) -> None:
    """Кнопка меню: подсказка как добавить привычку"""
    await message.answer(
        "Чтобы добавить привычку, отправь команду:\n"
        "/sethabit <текст привычки>\n\n"
        "Например: /sethabit Пить 2 литра воды в день"
    )


@dp.message(F.text == "Посмотреть список привычек")
async def cmd_menu_list_habits(message: Message) -> None:
    """Кнопка меню: показать список привычек пользователя"""
    user_id = message.from_user.id
    habits = await get_habits(user_id)
    if not habits:
        await message.answer("У тебя пока нет привычек.\nИспользуй /sethabit <текст привычки> чтобы добавить первую.")
        return
    lines = [f"📝 Твои привычки ({len(habits)}):\n"]
    for i, (habit_id, habit_text) in enumerate(habits, 1):
        lines.append(f"{i}. {habit_text}")
    await message.answer("\n".join(lines))


@dp.message()
async def catch_all_handler(message: Message) -> None:
    """Игнорируем необработанные сообщения (меню и команды обрабатываются выше)."""
    pass


def run_api():
    """Запуск FastAPI в Railway-совместимом режиме."""
    import os
    from api import app

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


async def main() -> None:
    global bot
    bot = Bot(token=BOT_TOKEN)
    await init_db()
    # Запускаем FastAPI сервер в фоновом потоке
    api_thread = threading.Thread(target=run_api, daemon=True)
    api_thread.start()
    logger.info("FastAPI сервер запущен на http://%s:%s", API_HOST, API_PORT)

    # Настраиваем планировщик на ежедневную отправку в 21:00 по МСК
    scheduler.add_job(
        send_daily_reminder,
        trigger="cron",
        hour=20,
        minute=20,
        timezone="Europe/Moscow"
    )
    scheduler.start()
    logger.info("Планировщик запущен. Напоминания будут отправляться каждый день в 21:00 по МСК")
    
    logger.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())