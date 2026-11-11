from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


def get_planner_keyboard() -> ReplyKeyboardMarkup:
    """Keyboard active during content scheduling mode."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Готово")],
            [KeyboardButton(text="Отмена")]
        ],
        resize_keyboard=True,
        is_persistent=True
    )


def get_back_keyboard() -> ReplyKeyboardMarkup:
    """Keyboard for sub-steps (entering schedule, captions, etc.)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Назад")]
        ],
        resize_keyboard=True,
        is_persistent=True
    )


def get_doc_rename_keyboard() -> ReplyKeyboardMarkup:
    """Keyboard for document rename setting."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Не переименовывать")],
            [KeyboardButton(text="Назад")]
        ],
        resize_keyboard=True,
        is_persistent=True
    )
