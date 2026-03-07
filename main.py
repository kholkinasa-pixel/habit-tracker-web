# -*- coding: utf-8 -*-
import asyncio
import logging
import threading
from datetime import date, datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

# Названия месяцев в родительном падеже для формата «23 февраля»
MONTH_NAMES_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
    7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


def format_date_ru(d: date) -> str:
    """Форматирует дату как «23 февраля»."""
    return f"{d.day} {MONTH_NAMES_RU[d.month]}"

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
    delete_habit,
    get_habit_by_id,
    get_habits,
    get_habits_count,
    get_unmarked_habits_for_reminder,
    has_daily_log,
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
    """Главное меню: Мой прогресс, Привычки."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📈 Мой прогресс", web_app=WebAppInfo(url=_webapp_url(user_id)))],
            [KeyboardButton(text="📋 Привычки")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )

def _weekday_moscow() -> int:
    """День недели по Москве (0=Monday, 6=Sunday)."""
    return datetime.now(ZoneInfo("Europe/Moscow")).weekday()


async def send_daily_reminder():
    """
    Отправляет напоминания только по привычкам без записи в daily_logs на текущую дату.
    Если все привычки уже отмечены — ничего не отправляет.
    """
    try:
        today = date.today()
        rows = await get_unmarked_habits_for_reminder(today)
        logger.info(f"Отправка напоминаний. Неотмеченных привычек: {len(rows)}")

        if not rows:
            return

        weekday = _weekday_moscow()
        text_template = REMINDER_TEXTS[weekday]["reminder"]

        for user_id, habit_id, habit_text in rows:
            try:
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="❌ Нет", callback_data=f"habit_no_{habit_id}"),
                        InlineKeyboardButton(text="🟦 Базовый минимум", callback_data=f"habit_min_{habit_id}")
                    ],
                    [
                        InlineKeyboardButton(text="🔷 Хорошо потрудились", callback_data=f"habit_good_{habit_id}")
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


# --- Отметить прогресс (выбор привычки) ---

def _get_mark_progress_keyboard(habits: list, date_str: str) -> tuple[str, InlineKeyboardMarkup]:
    """Формирует текст и клавиатуру для выбора привычки при отметке прогресса."""
    text = f"Выберите привычку для отметки прогресса за {date_str}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=name, callback_data=f"mark_select_{hid}")]
        for hid, name in habits
    ])
    return text, keyboard


@dp.message(F.text.startswith("✅ Отметить прогресс"))
async def cmd_mark_progress(message: Message, state: FSMContext) -> None:
    """Шаг 1: выбор привычки для отметки прогресса."""
    await state.clear()
    user_id = message.from_user.id
    today = date.today()
    date_str = format_date_ru(today)

    habits = await get_habits(user_id)
    if not habits:
        await message.answer(
            "У тебя пока нет привычек. Добавь первую в разделе «📋 Привычки»."
        )
        return

    text, keyboard = _get_mark_progress_keyboard(habits, date_str)
    await message.answer(text, reply_markup=keyboard)


@dp.callback_query(F.data.startswith("mark_select_"))
async def handle_mark_select_habit(callback: CallbackQuery) -> None:
    # Шаг 2: если привычка уже отмечена сегодня — сразу сообщение без кнопок
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

    today = date.today()
    if await has_daily_log(habit_id, today):
        await callback.message.edit_text(
            "За сегодня эта привычка уже отмечена 💙",
            reply_markup=None
        )
        await callback.answer()
        return

    habit_text = next((n for hid, n in habits if hid == habit_id), "")
    text = f'«{habit_text}» — как сегодня?'
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="❌ Нет", callback_data=f"habit_no_{habit_id}"),
            InlineKeyboardButton(text="🟦 Базовый минимум", callback_data=f"habit_min_{habit_id}")
        ],
        [InlineKeyboardButton(text="🔷 Хорошо потрудились", callback_data=f"habit_good_{habit_id}")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data == "onboarding_add_habit")
async def handle_onboarding_add_habit(callback: CallbackQuery, state: FSMContext) -> None:
    """Онбординг: при нажатии «➕ Добавить привычку» — переход в FSM добавления привычки."""
    await state.set_state(AddingHabit.waiting_for_name)
    await callback.message.answer(ONBOARDING_PROMPT, parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data.startswith("habit_"))
async def handle_habit_callback(callback: CallbackQuery):
    """
    Обработчик кнопок статуса (Нет / Базовый минимум / Хорошо потрудились).
    Повторная отметка запрещена — если запись уже есть, показываем дружелюбное сообщение.
    """
    data = callback.data
    user_id = callback.from_user.id

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
        efficiency_level = "Нет"
    elif data.startswith("habit_min_"):
        efficiency_level = "Базовый минимум"
    elif data.startswith("habit_good_"):
        efficiency_level = "Хорошо потрудились"
    else:
        await callback.answer("Неизвестная команда")
        return

    try:
        created, already = await save_daily_log(user_id, habit_id, efficiency_level)
    except Exception as e:
        logger.error(f"Ошибка при сохранении ответа пользователя {user_id}: {e}")
        await callback.answer("Ошибка сохранения")
        return

    if already:
        await callback.answer("Уже отмечено")
        await callback.message.edit_text(
            "За сегодня эта привычка уже отмечена 💙",
            reply_markup=None
        )
        return

    await callback.answer("Прогресс сохранён ✅")
    await callback.message.edit_text(
        "Прогресс сохранён ✅",
        reply_markup=None
    )


ONBOARDING_TEXT = (
    "Привет! 👋\n"
    "Я помогу вам отслеживать привычки.\n\n"
    "Каждый день в 21:00 я буду спрашивать,\n"
    "как прошёл день.\n\n"
    "Давайте добавим первую привычку?"
)
ONBOARDING_PROMPT = (
    "✍️ Напишите привычку, которую хотите развить.\n\n"
    "Например: «Тренироваться 30 минут в день» 💪\n\n"
    "О том, как организовать привычку и не забросить её через неделю, можно прочитать в канале <a href=\"https://t.me/keepgoingtoday/17\">Сегодня лучше</a>"
)


@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()  # Сброс FSM при старте/отмене
    user_id = message.from_user.id
    logger.info("start: user_id=%s", user_id)
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
        text = f"✅ Привычка «{habit_text}» добавлена!\n\nЯ буду напоминать вам о ней каждый день в 21:00 по Москве 🌙"
        await message.answer(text, reply_markup=get_bot_menu(user_id))
    else:
        await message.answer(err_msg or "Не удалось добавить привычку.")


# --- FSM: Добавление привычки (кнопка «➕ Добавить привычку») ---

@dp.message(F.text.in_({"➕ Добавить привычку", "Добавить привычку"}))
async def cmd_menu_add_habit(message: Message, state: FSMContext) -> None:
    """Кнопка меню: запуск FSM добавления привычки."""
    await state.set_state(AddingHabit.waiting_for_name)
    await message.answer(ONBOARDING_PROMPT, parse_mode="HTML")


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
        count = await get_habits_count(user_id)
        if count == 1:
            # Первая привычка (онбординг): специальное сообщение
            text = f"✅ Привычка «{habit_text}» добавлена!\n\nТеперь я буду спрашивать о ней каждый день в 21:00, а прогресс отмечать в календаре"
        else:
            text = f"✅ Привычка «{habit_text}» добавлена!\n\nЯ буду напоминать вам о ней каждый день в 21:00 по Москве 🌙"
        await message.answer(text, reply_markup=get_bot_menu(user_id))
    else:
        await message.answer(err_msg or "Не удалось добавить привычку.", reply_markup=get_bot_menu(user_id))


# --- 📋 Привычки (главное меню) ---

@dp.message(F.text.in_({"📋 Привычки", "Привычки"}))
async def cmd_habits(message: Message, state: FSMContext) -> None:
    """Открывает меню привычек: Отметить прогресс, Список, Добавить, Редактировать, Удалить."""
    await state.clear()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отметить прогресс за сегодня", callback_data="settings_mark_progress")],
        [InlineKeyboardButton(text="📋 Список привычек", callback_data="settings_list")],
        [InlineKeyboardButton(text="✏️ Редактировать привычку", callback_data="settings_edit")],
        [InlineKeyboardButton(text="➕ Добавить привычку", callback_data="settings_add")],
        [InlineKeyboardButton(text="🗑 Удалить привычку", callback_data="settings_delete")],
    ])
    await message.answer("📋 Привычки", reply_markup=keyboard)


@dp.callback_query(F.data == "settings_mark_progress")
async def handle_settings_mark_progress(callback: CallbackQuery, state: FSMContext) -> None:
    """Кнопка «Отметить прогресс за сегодня» из меню Привычки."""
    await state.clear()
    user_id = callback.from_user.id
    today = date.today()
    date_str = format_date_ru(today)

    habits = await get_habits(user_id)
    if not habits:
        await callback.message.edit_text(
            "У тебя пока нет привычек. Добавь первую.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Добавить привычку", callback_data="settings_add")]
            ])
        )
    else:
        text, keyboard = _get_mark_progress_keyboard(habits, date_str)
        await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data == "settings_add")
async def handle_settings_add(callback: CallbackQuery, state: FSMContext) -> None:
    # Добавить привычку: переход в FSM
    await state.set_state(AddingHabit.waiting_for_name)
    await callback.message.edit_text(ONBOARDING_PROMPT, reply_markup=None, parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "settings_list")
async def handle_settings_list(callback: CallbackQuery) -> None:
    # Показать список привычек из настроек
    user_id = callback.from_user.id
    habits = await get_habits(user_id)
    show_add_button = len(habits) < 2
    if not habits:
        text = "У тебя пока нет привычек."
    else:
        lines = [f"📝 Твои привычки ({len(habits)}):\n"]
        for i, (_, habit_text) in enumerate(habits, 1):
            lines.append(f"{i}. {habit_text}")
        text = "\n".join(lines)
    keyboard = None
    if show_add_button:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="➕ Добавить привычку", callback_data="settings_add")]]
        )
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data == "settings_edit")
async def handle_settings_edit(callback: CallbackQuery) -> None:
    # Редактировать привычку: показать список
    user_id = callback.from_user.id
    habits = await get_habits(user_id)
    if not habits:
        await callback.message.edit_text(
            "У тебя пока нет привычек. Добавь первую.",
            reply_markup=None
        )
    else:
        rows = [[InlineKeyboardButton(text=name, callback_data="edit_habit_{}".format(hid))]
                for hid, name in habits]
        keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
        await callback.message.edit_text("Выбери привычку для редактирования:", reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data == "settings_delete")
async def handle_settings_delete(callback: CallbackQuery) -> None:
    # Удалить привычку: показать список
    user_id = callback.from_user.id
    habits = await get_habits(user_id)
    if not habits:
        await callback.message.edit_text(
            "У тебя пока нет привычек.",
            reply_markup=None
        )
    else:
        rows = [[InlineKeyboardButton(text=name, callback_data="delete_habit_{}".format(hid))]
                for hid, name in habits]
        keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
        await callback.message.edit_text("Выбери привычку для удаления:", reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data.startswith("delete_habit_"))
async def handle_delete_habit_choice(callback: CallbackQuery) -> None:
    """Выбор привычки для удаления — запрос подтверждения."""
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

    habit_name = next((n for hid, n in habits if hid == habit_id), "")
    text = (
        f'Вы уверены, что хотите удалить привычку „{habit_name}"?\n'
        "Вся история выполнения будет удалена без возможности восстановления."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data="delete_cancel"),
            InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"delete_confirm_{habit_id}"),
        ]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data == "delete_cancel")
async def handle_delete_cancel(callback: CallbackQuery) -> None:
    """Отмена удаления привычки."""
    await callback.message.edit_text("Отменено.", reply_markup=None)
    await callback.answer()


@dp.callback_query(F.data.startswith("delete_confirm_"))
async def handle_delete_confirm(callback: CallbackQuery) -> None:
    """Подтверждение удаления — удаляем привычку и все daily_logs."""
    user_id = callback.from_user.id
    try:
        habit_id = int(callback.data.split("_", 2)[2])
    except (ValueError, IndexError):
        await callback.answer("Ошибка")
        return

    success, err_msg = await delete_habit(habit_id, user_id)
    if success:
        await callback.message.edit_text("Привычка удалена 🗑", reply_markup=None)
    else:
        await callback.message.edit_text(err_msg or "Не удалось удалить.", reply_markup=None)
    await callback.answer()


# --- FSM: Редактирование привычки ---

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
        hour=21,
        minute=0,
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