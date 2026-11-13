import asyncio
import logging
import time
from typing import Optional
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

import config
from database import (
    is_user_admin,
    set_user_admin,
    get_admin_stats,
    get_recent_users,
    get_all_broadcast_users,
    mark_user_blocked,
    add_or_update_user,
)
from keyboards import (
    get_admin_main_keyboard,
    get_admin_stats_keyboard,
    get_admin_broadcast_confirm_keyboard,
    get_admin_back_keyboard,
    get_main_menu_keyboard,
)

router = Router()
logger = logging.getLogger(__name__)


class AdminStates(StatesGroup):
    waiting_for_password = State()
    waiting_for_broadcast_content = State()
    confirm_broadcast = State()


def format_admin_dashboard() -> str:
    return (
        "🔐 <b>Панель администратора</b>\n\n"
        "Добро пожаловать в центр управления ботом!\n"
        "Выберите действие ниже:"
    )


# --- 1. /admin command entrypoint ---
@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    await state.clear()
    await add_or_update_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name
    )

    if await is_user_admin(message.from_user.id):
        await message.answer(
            format_admin_dashboard(),
            reply_markup=get_admin_main_keyboard(),
            parse_mode="HTML"
        )
    else:
        await state.set_state(AdminStates.waiting_for_password)
        await message.answer(
            "🔒 <b>Доступ ограничен</b>\n\n"
            "Введите пароль администратора для входа в панель управления:",
            parse_mode="HTML"
        )


# --- 2. Password verification ---
@router.message(AdminStates.waiting_for_password)
async def process_admin_password(message: Message, state: FSMContext):
    entered_password = (message.text or "").strip()

    # Delete message with password for security
    try:
        await message.delete()
    except Exception:
        pass

    if entered_password == config.ADMIN_PASSWORD:
        await set_user_admin(message.from_user.id, 1)
        await state.clear()
        await message.answer(
            "✅ <b>Пароль верный!</b> Доступ предоставлен.\n\n" + format_admin_dashboard(),
            reply_markup=get_admin_main_keyboard(),
            parse_mode="HTML"
        )
    else:
        await state.clear()
        await message.answer(
            "❌ <b>Неверный пароль.</b> В доступе отказано.",
            reply_markup=get_main_menu_keyboard(),
            parse_mode="HTML"
        )


# --- 3. Navigation to Admin Menu ---
@router.callback_query(F.data == "admin_menu")
async def cb_admin_menu(call: CallbackQuery, state: FSMContext):
    if not await is_user_admin(call.from_user.id):
        await call.answer("Доступ запрещен.", show_alert=True)
        return

    await state.clear()
    await call.message.edit_text(
        format_admin_dashboard(),
        reply_markup=get_admin_main_keyboard(),
        parse_mode="HTML"
    )
    await call.answer()


# --- 4. Statistics ---
@router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(call: CallbackQuery):
    if not await is_user_admin(call.from_user.id):
        await call.answer("Доступ запрещен.", show_alert=True)
        return

    stats = await get_admin_stats()
    text = (
        "📊 <b>Статистика бота:</b>\n\n"
        "👥 <b>Пользователи:</b>\n"
        f"• Всего в базе: <b>{stats['total_users']}</b>\n"
        f"• Новых за сегодня: <b>{stats['new_today']}</b>\n"
        f"• Новых за 7 дней: <b>{stats['new_week']}</b>\n"
        f"• Активных за 24 часа: <b>{stats['active_24h']}</b>\n\n"
        "📢 <b>Каналы:</b>\n"
        f"• Подключено каналов: <b>{stats['total_channels']}</b>\n\n"
        "📮 <b>Посты:</b>\n"
        f"• Опубликовано: <b>{stats['posts_published']}</b>\n"
        f"• В очереди: <b>{stats['posts_pending']}</b>"
    )

    try:
        await call.message.edit_text(
            text,
            reply_markup=get_admin_stats_keyboard(),
            parse_mode="HTML"
        )
    except Exception:
        pass
    await call.answer()


# --- 5. Users List ---
@router.callback_query(F.data == "admin_users")
async def cb_admin_users(call: CallbackQuery):
    if not await is_user_admin(call.from_user.id):
        await call.answer("Доступ запрещен.", show_alert=True)
        return

    users = await get_recent_users(15)
    if not users:
        text = "👥 В базе пока нет зарегистрированных пользователей."
    else:
        lines = ["👥 <b>Последние активные пользователи:</b>\n"]
        for idx, u in enumerate(users, 1):
            uname = f"@{u['username']}" if u.get("username") else "Без юзернейма"
            name = u.get("first_name") or "Пользователь"
            admin_mark = " 👑" if u.get("is_admin") else ""
            lines.append(f"{idx}. {name} ({uname}){admin_mark} — ID: <code>{u['user_id']}</code>")
        text = "\n".join(lines)

    await call.message.edit_text(
        text,
        reply_markup=get_admin_back_keyboard(),
        parse_mode="HTML"
    )
    await call.answer()


# --- 6. Broadcast (Рассылка) ---
@router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(call: CallbackQuery, state: FSMContext):
    if not await is_user_admin(call.from_user.id):
        await call.answer("Доступ запрещен.", show_alert=True)
        return

    users = await get_all_broadcast_users()
    await state.set_state(AdminStates.waiting_for_broadcast_content)
    text = (
        "📢 <b>Рассылка сообщений</b>\n\n"
        f"Получателей в базе: <b>{len(users)} чел.</b>\n\n"
        "Отправьте сообщение (текст, фото, видео или документ), которое хотите разослать.\n"
        "<i>Вы увидите предпросмотр перед началом отправки.</i>"
    )
    await call.message.edit_text(
        text,
        reply_markup=get_admin_back_keyboard(),
        parse_mode="HTML"
    )
    await call.answer()


@router.message(AdminStates.waiting_for_broadcast_content)
async def process_broadcast_content(message: Message, state: FSMContext):
    await state.update_data(
        broadcast_chat_id=message.chat.id,
        broadcast_message_id=message.message_id
    )
    await state.set_state(AdminStates.confirm_broadcast)

    await message.answer("👀 <b>Предпросмотр сообщения для рассылки:</b>", parse_mode="HTML")
    # Echo back a copy
    try:
        await message.copy_to(chat_id=message.chat.id)
    except Exception as e:
        logger.warning(f"Could not copy preview: {e}")

    users = await get_all_broadcast_users()
    await message.answer(
        f"Вы действительно хотите разослать это сообщение <b>{len(users)}</b> пользователям?",
        reply_markup=get_admin_broadcast_confirm_keyboard(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "admin_bc_cancel", AdminStates.confirm_broadcast)
async def cb_broadcast_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text(
        "❌ <b>Рассылка отменена.</b>\n\n" + format_admin_dashboard(),
        reply_markup=get_admin_main_keyboard(),
        parse_mode="HTML"
    )
    await call.answer()


@router.callback_query(F.data == "admin_bc_start", AdminStates.confirm_broadcast)
async def cb_broadcast_start(call: CallbackQuery, state: FSMContext, bot: Bot):
    if not await is_user_admin(call.from_user.id):
        await call.answer("Доступ запрещен.", show_alert=True)
        return

    data = await state.get_data()
    from_chat_id = data.get("broadcast_chat_id")
    msg_id = data.get("broadcast_message_id")
    await state.clear()

    recipients = await get_all_broadcast_users()
    total = len(recipients)

    status_msg = await call.message.edit_text(
        f"⏳ <b>Рассылка запущена...</b>\nВсего получателей: {total}",
        parse_mode="HTML"
    )
    await call.answer()

    sent = 0
    blocked = 0
    errors = 0
    start_time = time.time()

    for user_id in recipients:
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=from_chat_id,
                message_id=msg_id
            )
            sent += 1
        except TelegramForbiddenError:
            blocked += 1
            await mark_user_blocked(user_id)
        except Exception as e:
            logger.warning(f"Failed to broadcast to {user_id}: {e}")
            errors += 1

        # Smooth throttle ~25 msgs/sec to stay strictly within Telegram limits
        await asyncio.sleep(0.04)

    duration = round(time.time() - start_time, 1)

    result_text = (
        "📢 <b>Рассылка завершена!</b>\n\n"
        f"✅ Успешно доставлено: <b>{sent}</b>\n"
        f"🚫 Заблокировали бота: <b>{blocked}</b>\n"
        f"⚠️ Ошибок: <b>{errors}</b>\n"
        f"⏱ Заняло времени: <b>{duration} сек.</b>"
    )

    await status_msg.edit_text(
        result_text,
        reply_markup=get_admin_main_keyboard(),
        parse_mode="HTML"
    )


# --- 7. Exit Admin Panel ---
@router.callback_query(F.data == "admin_exit")
async def cb_admin_exit(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text(
        "Вы вышли из панели администратора.",
        reply_markup=get_main_menu_keyboard()
    )
    await call.answer()
