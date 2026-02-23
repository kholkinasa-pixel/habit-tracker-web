"""
FSM-состояния для интерактивного добавления и редактирования привычек.
"""
from aiogram.fsm.state import State, StatesGroup


class AddingHabit(StatesGroup):
    """Состояния при добавлении новой привычки."""

    waiting_for_name = State()


class EditingHabit(StatesGroup):
    """Состояния при редактировании названия привычки."""

    waiting_for_new_name = State()
