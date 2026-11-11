from typing import List, Dict, Any
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Мои каналы", callback_data="menu_channels")],
            [InlineKeyboardButton(text="➕ Подключить канал", callback_data="menu_add_channel")],
            [InlineKeyboardButton(text="ℹ️ Помощь и инструкция", callback_data="menu_help")]
        ]
    )


def get_channels_list_keyboard(channels: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    keyboard = []
    for ch in channels:
        title = ch.get("channel_title") or "Канал"
        keyboard.append([
            InlineKeyboardButton(text=f"📢 {title}", callback_data=f"channel_view:{ch['id']}")
        ])
    keyboard.append([
        InlineKeyboardButton(text="➕ Подключить канал", callback_data="menu_add_channel")
    ])
    keyboard.append([
        InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu_main")
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_channel_management_keyboard(channel_id: int, pending_count: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📝 Запланировать посты", callback_data=f"channel_plan:{channel_id}")],
            [
                InlineKeyboardButton(text="⏰ Расписание", callback_data=f"channel_sched:{channel_id}"),
                InlineKeyboardButton(text="✍️ Подпись", callback_data=f"channel_caption:{channel_id}")
            ],
            [
                InlineKeyboardButton(text=f"📋 Очередь ({pending_count})", callback_data=f"channel_queue:{channel_id}"),
                InlineKeyboardButton(text="🗑 Удалить канал", callback_data=f"channel_delete:{channel_id}")
            ],
            [InlineKeyboardButton(text="◀️ К списку каналов", callback_data="menu_channels")]
        ]
    )


def get_schedule_actions_keyboard(channel_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧹 Очистить расписание", callback_data=f"sched_clear:{channel_id}")],
            [InlineKeyboardButton(text="◀️ Назад в меню канала", callback_data=f"channel_view:{channel_id}")]
        ]
    )


def get_caption_actions_keyboard(channel_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Удалить подпись", callback_data=f"caption_clear:{channel_id}")],
            [InlineKeyboardButton(text="◀️ Назад в меню канала", callback_data=f"channel_view:{channel_id}")]
        ]
    )


def get_queue_actions_keyboard(channel_id: int, has_posts: bool) -> InlineKeyboardMarkup:
    keyboard = []
    if has_posts:
        keyboard.append([
            InlineKeyboardButton(text="🧹 Очистить всю очередь", callback_data=f"queue_clear:{channel_id}")
        ])
    keyboard.append([
        InlineKeyboardButton(text="◀️ Назад в меню канала", callback_data=f"channel_view:{channel_id}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_cancel_connect_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Отмена", callback_data="menu_channels")]
        ]
    )
