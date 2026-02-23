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
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import BOT_TOKEN, WEBAPP_URL, API_HOST, API_PORT, BACKEND_PUBLIC_URL
from database import (
    init_db,
    close_db,
    add_habit,
    get_habit_by_id,
    get_habits,
    get_all_users_with_habits,
    save_daily_log,
    update_habit_name,
)
from states import AddingHabit, EditingHabit

# В Python 3.9+ с uvloop в главном потоке ещё нет event loop — создаём его до aiogram
try:
    asyncio.get_running_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = None
dp = Dispatcher(storage=MemoryStorage())
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


def _habit_added_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Inline-клавиатура после успешного добавления привычки: кнопка календаря."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📅 Посмотреть календарь", web_app=WebAppInfo(url=_webapp_url(user_id)))]
        ]
    )


def get_bot_menu(user_id: int) -> ReplyKeyboardMarkup:
    """Меню с URL, содержащим user_id (initData при Reply Keyboard web_app часто пустой)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📅 Мой прогресс", web_app=WebAppInfo(url=_webapp_url(user_id)))],
            [KeyboardButton(text="➕ Добавить привычку")],
            [
                KeyboardButton(text="📋 Список привычек"),
                KeyboardButton(text="✏️ Редактировать привычку"),
            ],
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
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()  # Сброс FSM при старте/отмене
    await message.answer(
        "Привет! Я бот-трекер привычек. Чем могу помочь?\n\n"
        "Используй меню ниже: добавь привычку, смотри прогресс в календаре.\n\n"
        "Каждый день в 21:00 по МСК я буду спрашивать тебя о твоих привычках!",
        reply_markup=get_bot_menu(message.from_user.id),
    )


@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Выход из текущего диалога (добавление/редактирование привычки)."""
    current = await state.get_state()
    if current is None:
        return
    await state.clear()
    await message.answer("Отменено.", reply_markup=get_bot_menu(message.from_user.id))


@dp.message(Command("calendar"))
async def cmd_calendar(message: Message) -> None:
    """Открыть календарь привычек (Web App)"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть трекер", web_app=WebAppInfo(url=_webapp_url(message.from_user.id)))]
    ])
    await message.answer("Календарь привычек:", reply_markup=keyboard)


@dp.message(Command("sethabit"))
async def cmd_set_habit(message: Message, state: FSMContext) -> None:
    """
    [DEPRECATED] Команда для добавления привычки. Оставлена для обратной совместимости.
    Рекомендуется использовать кнопку «➕ Добавить привычку».
    """
    user_id = message.from_user.id
    command_parts = message.text.split(maxsplit=1)
    if len(command_parts) < 2:
        await message.answer(
            "Пожалуйста, укажи текст привычки после команды.\n"
            "Пример: /sethabit Пить 2 литра воды в день\n\n"
            "Или используй кнопку «➕ Добавить привычку» в меню."
        )
        return

    habit_text = command_parts[1].strip()

    if not habit_text or len(habit_text) < 2:
        await message.answer("Текст привычки должен быть не меньше 2 символов!")
        return

    success, err_msg = await add_habit(user_id, habit_text)
    if success:
        await message.answer(
            f"✅ Привычка «{habit_text}» добавлена!\n\n"
            f"Я буду напоминать вам о ней каждый день в 21:00 по Москве 🌙",
            reply_markup=_habit_added_keyboard(user_id),
        )
        await message.answer("Меню:", reply_markup=get_bot_menu(user_id))
    else:
        await message.answer(err_msg or "Не удалось добавить привычку.")


# --- FSM: Добавление привычки (кнопка «➕ Добавить привычку») ---

@dp.message(F.text.in_({"➕ Добавить привычку", "Добавить привычку"}))
async def cmd_menu_add_habit(message: Message, state: FSMContext) -> None:
    """Кнопка меню: запуск FSM добавления привычки."""
    await state.set_state(AddingHabit.waiting_for_name)
    await message.answer("✍️ Напишите привычку, которую хотите отслеживать.")


@dp.message(AddingHabit.waiting_for_name)
async def process_add_habit_name(message: Message, state: FSMContext) -> None:
    """Обработка названия привычки при добавлении."""
    user_id = message.from_user.id
    habit_text = (message.text or "").strip() if message.text else ""

    if not habit_text or len(habit_text) < 2:
        await message.answer("⚠️ Название должно быть не меньше 2 символов. Попробуй ещё раз.")
        return

    success, err_msg = await add_habit(user_id, habit_text)
    await state.clear()

    if success:
        await message.answer(
            f"✅ Привычка «{habit_text}» добавлена!\n\n"
            f"Я буду напоминать вам о ней каждый день в 21:00 по Москве 🌙",
            reply_markup=_habit_added_keyboard(user_id),
        )
        await message.answer("Меню:", reply_markup=get_bot_menu(user_id))
    else:
        await message.answer(err_msg or "Не удалось добавить привычку.", reply_markup=get_bot_menu(user_id))


# --- FSM: Редактирование привычки ---

@dp.message(F.text.in_({"✏️ Редактировать привычку", "Редактировать привычку"}))
async def cmd_menu_edit_habit(message: Message, state: FSMContext) -> None:
    """Кнопка меню: показать список привычек для редактирования."""
    user_id = message.from_user.id
    habits = await get_habits(user_id)

    if not habits:
        await message.answer("У тебя пока нет привычек. Добавь первую кнопкой «➕ Добавить привычку».")
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=name, callback_data=f"edit_habit_{hid}")]
            for hid, name in habits
        ]
    )
    await message.answer("Выбери привычку для редактирования:", reply_markup=keyboard)


@dp.callback_query(F.data.startswith("edit_habit_"))
async def handle_edit_habit_choice(callback: CallbackQuery, state: FSMContext) -> None:
    """Выбор привычки из списка: переход в состояние ожидания нового названия."""
    user_id = callback.from_user.id
    try:
        habit_id = int(callback.data.split("_", 2)[2])
    except (ValueError, IndexError):
        await callback.answer("Ошибка")
        return

    habits = await get_habits(user_id)
    habit_ids = {h[0] for h in habits}
    if habit_id not in habit_ids:
        await callback.answer("Эта привычка недоступна", show_alert=True)
        return

    old_name = next((n for hid, n in habits if hid == habit_id), "")
    await state.update_data(habit_id=habit_id, old_name=old_name)
    await state.set_state(EditingHabit.waiting_for_new_name)
    await callback.message.edit_text(
        f"Введите новое название для привычки «{old_name}»"
    )
    await callback.answer()


@dp.message(EditingHabit.waiting_for_new_name)
async def process_edit_habit_name(message: Message, state: FSMContext) -> None:
    """Обработка нового названия при редактировании привычки."""
    user_id = message.from_user.id
    new_name = (message.text or "").strip() if message.text else ""

    if not new_name or len(new_name) < 2:
        await message.answer("⚠️ Название должно быть не меньше 2 символов. Попробуй ещё раз.")
        return

    data = await state.get_data()
    habit_id = data.get("habit_id")
    await state.clear()

    if habit_id is None:
        await message.answer("Сессия истекла. Выбери привычку заново.", reply_markup=get_bot_menu(user_id))
        return

    success, err_msg = await update_habit_name(habit_id, user_id, new_name)
    if success:
        await message.answer("✅ Название обновлено", reply_markup=get_bot_menu(user_id))
    else:
        await message.answer(err_msg or "Не удалось обновить.", reply_markup=get_bot_menu(user_id))


# --- Остальные кнопки меню ---

@dp.message(F.text.in_({"📋 Список привычек", "Посмотреть список привычек"}))
async def cmd_menu_list_habits(message: Message) -> None:
    """Кнопка меню: показать список привычек пользователя"""
    user_id = message.from_user.id
    habits = await get_habits(user_id)
    if not habits:
        await message.answer(
            "У тебя пока нет привычек.\nИспользуй кнопку «➕ Добавить привычку» в меню."
        )
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
    try:
        await dp.start_polling(bot)
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())