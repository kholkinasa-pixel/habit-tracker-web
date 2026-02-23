import asyncio
import logging
import threading
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from texts import REMINDER_TEXTS

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
    get_habits_count,
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

def _weekday_moscow() -> int:
    """День недели по Москве (0=Monday, 6=Sunday)."""
    return datetime.now(ZoneInfo("Europe/Moscow")).weekday()


async def send_daily_reminder():
    """Отправляет ежедневное напоминание всем пользователям с привычками"""
    try:
        rows = await get_all_users_with_habits()
        logger.info(f"Отправка напоминаний. Найдено привычек: {len(rows)}")

        weekday = _weekday_moscow()
        text_template = REMINDER_TEXTS[weekday]["reminder"]

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

                text = text_template.format(habit_name=habit_text)
                await bot.send_message(
                    chat_id=user_id,
                    text=text,
                    reply_markup=keyboard
                )
                logger.info(f"Напоминание отправлено пользователю {user_id} (habit_id={habit_id})")
            except Exception as e:
                logger.error(f"Ошибка при отправке напоминания пользователю {user_id}: {e}")
    except Exception as e:
        logger.error(f"Критическая ошибка в send_daily_reminder: {e}")


@dp.callback_query(F.data == "onboarding_add_habit")
async def handle_onboarding_add_habit(callback: CallbackQuery, state: FSMContext) -> None:
    """Онбординг: при нажатии «➕ Добавить привычку» — переход в FSM добавления привычки."""
    await state.set_state(AddingHabit.waiting_for_name)
    await callback.message.answer(ONBOARDING_PROMPT)
    await callback.answer()


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
        status_key = "fail"
    elif data.startswith("habit_min_"):
        response = "Базовый минимум"
        efficiency_level = "Базовый минимум"
        emoji = "⚡"
        status_key = "partial"
    elif data.startswith("habit_good_"):
        response = "Хорошо потрудились"
        efficiency_level = "Хорошо потрудились"
        emoji = "🌟"
        status_key = "success"
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
        weekday = _weekday_moscow()
        response_text = REMINDER_TEXTS[weekday][status_key].format(habit_name=habit_text)
        await callback.message.edit_text(response_text)


ONBOARDING_TEXT = (
    "Привет! 👋\n"
    "Я помогу вам отслеживать привычки.\n\n"
    "Каждый день в 21:00 я буду спрашивать,\n"
    "как прошёл день.\n\n"
    "Давайте добавим первую привычку?"
)
ONBOARDING_PROMPT = (
    "✍️ Напишите привычку, которую хотите отслеживать.\n\n"
    "Например: «Пить 2 литра воды»"
)


@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()  # Сброс FSM при старте/отмене
    user_id = message.from_user.id
    habits_count = await get_habits_count(user_id)

    if habits_count == 0:
        # Онбординг: без главного меню, только inline-кнопка
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить привычку", callback_data="onboarding_add_habit")]
        ])
        await message.answer(ONBOARDING_TEXT, reply_markup=keyboard)
        return

    # Существующий пользователь: приветствие + главное меню
    await message.answer(
        "Привет! Я бот-трекер привычек. Чем могу помочь?\n\n"
        "Используй меню ниже: добавь привычку, смотри прогресс в календаре.\n\n"
        "Каждый день в 21:00 по МСК я буду спрашивать тебя о твоих привычках!",
        reply_markup=get_bot_menu(user_id),
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
            f"Я буду напоминать