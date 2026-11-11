import json
import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from database import (
    get_user_channels,
    get_channel_by_id,
    add_or_update_channel,
    update_channel_schedule,
    update_channel_caption,
    delete_channel,
    set_active_channel,
    get_last_scheduled_post_time,
    get_pending_posts_count,
    get_channel_queue_preview,
    clear_channel_queue,
)
from keyboards import (
    get_channels_list_keyboard,
    get_channel_management_keyboard,
    get_schedule_actions_keyboard,
    get_caption_actions_keyboard,
    get_queue_actions_keyboard,
    get_cancel_connect_keyboard,
    get_back_keyboard,
    get_main_menu_keyboard,
)
from services.scheduler import parse_time_slots

router = Router()
logger = logging.getLogger(__name__)


class ChannelStates(StatesGroup):
    waiting_for_channel = State()
    waiting_for_schedule = State()
    waiting_for_caption = State()


def format_channel_card(channel: dict, pending_count: int, last_iso: str | None) -> str:
    title = channel.get("channel_title", "Без названия")
    username = f"@{channel['channel_username']}" if channel.get("channel_username") else "ID: " + str(channel.get("tg_channel_id"))
    
    slots_raw = channel.get("schedule_slots", "[]")
    try:
        slots = json.loads(slots_raw)
    except Exception:
        slots = []
    
    slots_str = ", ".join(slots) if slots else "Не задано"
    caption_str = channel.get("caption") or "Отсутствует"

    lines = [
        f"<b>📢 Канал:</b> {title} ({username})",
        f"<b>Расписание:</b> {slots_str}",
        f"<b>Подпись:</b> {caption_str}",
        f"<b>Очередь постов:</b> {pending_count} постов",
    ]
    if last_iso:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(last_iso)
            lines.append(f"<b>Время последнего поста в очереди:</b> {dt.strftime('%d.%m.%Y %H:%M')}")
        except Exception:
            lines.append(f"<b>Время последнего поста в очереди:</b> {last_iso}")

    return "\n".join(lines)


# --- 1. Channel List ---
@router.callback_query(F.data == "menu_channels")
async def cb_channels_list(call: CallbackQuery, state: FSMContext):
    await state.clear()
    channels = await get_user_channels(call.from_user.id)
    if not channels:
        text = (
            "<b>Мои каналы</b>\n\n"
            "У вас пока нет подключенных каналов.\n"
            "Нажмите «Подключить канал», чтобы добавить ваш первый канал!"
        )
    else:
        text = (
            "<b>Мои каналы</b>\n\n"
            "Здесь вы можете посмотреть список подключенных каналов, и подключить новые. "
            "Выберите канал для перехода к управлению каналом."
        )
    await call.message.edit_text(
        text,
        reply_markup=get_channels_list_keyboard(channels),
        parse_mode="HTML"
    )
    await call.answer()


# --- 2. Connect Channel ---
@router.callback_query(F.data == "menu_add_channel")
async def cb_add_channel_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(ChannelStates.waiting_for_channel)
    text = (
        "<b>Подключение канала</b>\n\n"
        "1. Добавьте этого бота в администраторы Вашего канала с правом публикации сообщений.\n"
        "2. Затем напишите сюда <b>@username</b> Вашего канала или его ID (например <code>-1001234567890</code>), "
        "либо сделайте репост любого сообщения из него."
    )
    await call.message.edit_text(
        text,
        reply_markup=get_cancel_connect_keyboard(),
        parse_mode="HTML"
    )
    await call.answer()


@router.message(ChannelStates.waiting_for_channel)
async def process_channel_input(message: Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    target_id_or_username = None

    # Check if forwarded from channel
    if message.forward_from_chat and message.forward_from_chat.type == "channel":
        target_id_or_username = message.forward_from_chat.id
    elif message.text:
        raw = message.text.strip()
        # Clean up t.me links
        if "t.me/" in raw:
            parts = raw.split("t.me/")[-1].strip("/")
            if parts.startswith("+") or parts.startswith("joinchat/"):
                # Private invite link without username
                pass
            else:
                target_id_or_username = "@" + parts.replace("@", "")
        elif raw.startswith("@"):
            target_id_or_username = raw
        elif raw.startswith("-100") or raw.lstrip("-").isdigit():
            target_id_or_username = int(raw)

    if not target_id_or_username:
        await message.answer(
            "❗️ <b>Не найден юзернейм канала или его ID.</b>\n"
            "Пожалуйста, пришлите @username, числовой ID или перешлите пост из канала.",
            parse_mode="HTML"
        )
        return

    try:
        chat = await bot.get_chat(target_id_or_username)
        # Verify bot admin rights in channel
        bot_member = await bot.get_chat_member(chat.id, bot.id)
        if bot_member.status not in ("administrator", "creator"):
            await message.answer(
                f"❗️ <b>Бот не является администратором в канале '{chat.title}'!</b>\n"
                "Сначала сделайте бота админом канала с разрешением на отправку сообщений, затем попробуйте снова.",
                parse_mode="HTML"
            )
            return

        # Add or update in database
        channel_id = await add_or_update_channel(
            user_id=user_id,
            tg_channel_id=chat.id,
            title=chat.title or "Без названия",
            username=chat.username
        )
        await state.clear()

        await message.answer("<b>Канал успешно добавлен.</b>", parse_mode="HTML")

        # Show channel details
        pending_count = await get_pending_posts_count(channel_id)
        last_iso = await get_last_scheduled_post_time(channel_id)
        channel = await get_channel_by_id(channel_id)

        card_text = format_channel_card(channel, pending_count, last_iso)
        await message.answer(
            card_text,
            reply_markup=get_channel_management_keyboard(channel_id, pending_count),
            parse_mode="HTML"
        )

    except Exception as e:
        logger.warning(f"Failed to resolve channel '{target_id_or_username}': {e}")
        await message.answer(
            "❗️ <b>Не найден юзернейм канала или его ID.</b>\n"
            "Убедитесь, что бот добавлен в администраторы канала и юзернейм указан верно.",
            parse_mode="HTML"
        )


# --- 3. Channel Management Card ---
@router.callback_query(F.data.startswith("channel_view:"))
async def cb_channel_view(call: CallbackQuery, state: FSMContext):
    await state.clear()
    channel_id = int(call.data.split(":")[1])
    channel = await get_channel_by_id(channel_id)
    if not channel:
        await call.answer("Канал не найден.", show_alert=True)
        return

    await set_active_channel(call.from_user.id, channel_id)
    pending_count = await get_pending_posts_count(channel_id)
    last_iso = await get_last_scheduled_post_time(channel_id)

    card_text = format_channel_card(channel, pending_count, last_iso)
    await call.message.edit_text(
        card_text,
        reply_markup=get_channel_management_keyboard(channel_id, pending_count),
        parse_mode="HTML"
    )
    await call.answer()


# --- 4. Schedule Settings ---
@router.callback_query(F.data.startswith("channel_sched:"))
async def cb_channel_schedule(call: CallbackQuery, state: FSMContext):
    channel_id = int(call.data.split(":")[1])
    channel = await get_channel_by_id(channel_id)
    if not channel:
        await call.answer("Канал не найден.", show_alert=True)
        return

    await state.set_state(ChannelStates.waiting_for_schedule)
    await state.update_data(channel_id=channel_id)

    slots_raw = channel.get("schedule_slots", "[]")
    try:
        slots = json.loads(slots_raw)
    except Exception:
        slots = []
    slots_str = ", ".join(slots) if slots else "Не задано"

    text = (
        "Отправьте сюда время события, одно или несколько событий, разделенные между собой.\n\n"
        f"<b>Выбранный вами канал:</b> '{channel.get('channel_title')}'\n"
        f"<b>Расписание:</b> {slots_str}\n\n"
        "<i>Примеры:</i>\n"
        "• <code>15,18</code> (посты в 15:00 и 18:00)\n"
        "• <code>14:00, 21:00</code>\n"
        "• <code>9 12 15 18 21</code>"
    )
    # Remove previous inline or send reply button
    await call.message.answer(text, reply_markup=get_back_keyboard(), parse_mode="HTML")
    await call.answer()


@router.message(ChannelStates.waiting_for_schedule)
async def process_schedule_input(message: Message, state: FSMContext):
    data = await state.get_data()
    channel_id = data.get("channel_id")
    raw_text = message.text.strip() if message.text else ""

    if raw_text == "Назад":
        await state.clear()
        await message.answer(
            "<b>Добавление событий в расписания завершено.</b>",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="HTML"
        )
        channel = await get_channel_by_id(channel_id)
        if channel:
            pending_count = await get_pending_posts_count(channel_id)
            last_iso = await get_last_scheduled_post_time(channel_id)
            await message.answer(
                format_channel_card(channel, pending_count, last_iso),
                reply_markup=get_channel_management_keyboard(channel_id, pending_count),
                parse_mode="HTML"
            )
        return

    new_slots = parse_time_slots(raw_text)
    if not new_slots:
        await message.answer(
            "❗️ Не удалось распознать время. Введите часы через запятую или пробел, например: <code>15,18</code> или <code>14:00, 21:00</code>.",
            parse_mode="HTML"
        )
        return

    # Fetch current slots and merge
    channel = await get_channel_by_id(channel_id)
    try:
        current_slots = json.loads(channel.get("schedule_slots", "[]"))
    except Exception:
        current_slots = []

    merged = sorted(list(set(current_slots + new_slots)))
    await update_channel_schedule(channel_id, merged)

    added_str = ", ".join(new_slots)
    full_str = ", ".join(merged)

    await message.answer(f"К расписанию добавлены события: <b>{added_str}</b>.", parse_mode="HTML")
    await message.answer(
        f"Отправьте сюда время события, одно или несколько событий, разделенные между собой.\n\n"
        f"<b>Выбранный вами канал:</b> '{channel.get('channel_title')}'\n"
        f"<b>Расписание:</b> {full_str}",
        reply_markup=get_back_keyboard(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("sched_clear:"))
async def cb_schedule_clear(call: CallbackQuery):
    channel_id = int(call.data.split(":")[1])
    await update_channel_schedule(channel_id, [])
    await call.answer("Расписание очищено.", show_alert=True)
    channel = await get_channel_by_id(channel_id)
    pending_count = await get_pending_posts_count(channel_id)
    last_iso = await get_last_scheduled_post_time(channel_id)
    await call.message.edit_text(
        format_channel_card(channel, pending_count, last_iso),
        reply_markup=get_channel_management_keyboard(channel_id, pending_count),
        parse_mode="HTML"
    )


# --- 5. Caption Settings ---
@router.callback_query(F.data.startswith("channel_caption:"))
async def cb_channel_caption(call: CallbackQuery, state: FSMContext):
    channel_id = int(call.data.split(":")[1])
    channel = await get_channel_by_id(channel_id)
    if not channel:
        await call.answer("Канал не найден.", show_alert=True)
        return

    await state.set_state(ChannelStates.waiting_for_caption)
    await state.update_data(channel_id=channel_id)

    cur_caption = channel.get("caption") or "Отсутствует"
    text = (
        "Отправьте сюда подпись. Заданная подпись будет присутствовать во всех публикациях.\n"
        "Поддерживается форматирование текста, ссылки и эмодзи. Подпись не должна быть пустой.\n\n"
        f"<b>Выбранный вами канал:</b> '{channel.get('channel_title')}'.\n"
        f"<b>Подпись:</b> {cur_caption}"
    )
    await call.message.answer(
        text,
        reply_markup=get_back_keyboard(),
        parse_mode="HTML"
    )
    await call.answer()


@router.message(ChannelStates.waiting_for_caption)
async def process_caption_input(message: Message, state: FSMContext):
    data = await state.get_data()
    channel_id = data.get("channel_id")
    caption_text = message.html_text if message.html_text else message.text

    if caption_text == "Назад":
        await state.clear()
        await message.answer("Редактирование подписи отменено.", reply_markup=ReplyKeyboardRemove())
    else:
        await update_channel_caption(channel_id, caption_text)
        await state.clear()
        await message.answer(
            f"<b>Задана подпись:</b>\n{caption_text}",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="HTML"
        )

    channel = await get_channel_by_id(channel_id)
    if channel:
        pending_count = await get_pending_posts_count(channel_id)
        last_iso = await get_last_scheduled_post_time(channel_id)
        await message.answer(
            format_channel_card(channel, pending_count, last_iso),
            reply_markup=get_channel_management_keyboard(channel_id, pending_count),
            parse_mode="HTML"
        )


@router.callback_query(F.data.startswith("caption_clear:"))
async def cb_caption_clear(call: CallbackQuery):
    channel_id = int(call.data.split(":")[1])
    await update_channel_caption(channel_id, None)
    await call.answer("Подпись удалена.", show_alert=True)
    channel = await get_channel_by_id(channel_id)
    pending_count = await get_pending_posts_count(channel_id)
    last_iso = await get_last_scheduled_post_time(channel_id)
    await call.message.edit_text(
        format_channel_card(channel, pending_count, last_iso),
        reply_markup=get_channel_management_keyboard(channel_id, pending_count),
        parse_mode="HTML"
    )


# --- 6. Queue View & Clear ---
@router.callback_query(F.data.startswith("channel_queue:"))
async def cb_channel_queue(call: CallbackQuery):
    channel_id = int(call.data.split(":")[1])
    channel = await get_channel_by_id(channel_id)
    if not channel:
        await call.answer("Канал не найден.", show_alert=True)
        return

    posts = await get_channel_queue_preview(channel_id, limit=10)
    has_posts = len(posts) > 0

    if not posts:
        text = f"<b>Очередь постов пуста.</b>\nКанал: '{channel.get('channel_title')}'"
    else:
        text = f"<b>📋 Ближайшие запланированные посты ({len(posts)}):</b>\nКанал: '{channel.get('channel_title')}'\n\n"
        for idx, p in enumerate(posts, start=1):
            from datetime import datetime
            dt = datetime.fromisoformat(p["publish_at"]).strftime("%d.%m.%Y %H:%M")
            m_type = p["msg_type"]
            preview = (p.get("caption") or p.get("text") or "")[:35]
            if preview:
                preview = f" — <i>{preview}...</i>"
            text += f"{idx}. <b>[{dt}]</b> {m_type}{preview}\n"

    await call.message.edit_text(
        text,
        reply_markup=get_queue_actions_keyboard(channel_id, has_posts),
        parse_mode="HTML"
    )
    await call.answer()


@router.callback_query(F.data.startswith("queue_clear:"))
async def cb_queue_clear(call: CallbackQuery):
    channel_id = int(call.data.split(":")[1])
    deleted_count = await clear_channel_queue(channel_id)
    await call.answer(f"Очередь очищена. Удалено {deleted_count} постов.", show_alert=True)

    channel = await get_channel_by_id(channel_id)
    pending_count = await get_pending_posts_count(channel_id)
    last_iso = await get_last_scheduled_post_time(channel_id)
    await call.message.edit_text(
        format_channel_card(channel, pending_count, last_iso),
        reply_markup=get_channel_management_keyboard(channel_id, pending_count),
        parse_mode="HTML"
    )


# --- 7. Delete Channel ---
@router.callback_query(F.data.startswith("channel_delete:"))
async def cb_channel_delete(call: CallbackQuery):
    channel_id = int(call.data.split(":")[1])
    channel = await get_channel_by_id(channel_id)
    title = channel.get("channel_title") if channel else "Канал"
    await delete_channel(channel_id, call.from_user.id)
    await call.answer(f"Канал '{title}' удален.", show_alert=True)

    channels = await get_user_channels(call.from_user.id)
    await call.message.edit_text(
        "<b>Мои каналы</b>\n\nВыберите канал для перехода к управлению каналом.",
        reply_markup=get_channels_list_keyboard(channels),
        parse_mode="HTML"
    )
