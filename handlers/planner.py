import json
import uuid
import logging
from datetime import datetime
from typing import List, Optional

from aiogram import Router, F, Bot
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardRemove,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
    InputMediaAudio,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from database import (
    get_channel_by_id,
    get_last_scheduled_post_time,
    get_pending_posts_count,
    add_post,
    cancel_session_posts,
)
from keyboards import (
    get_planner_keyboard,
    get_channel_management_keyboard,
)
from services.scheduler import calculate_next_slot
from handlers.channel import format_channel_card

router = Router()
logger = logging.getLogger(__name__)


class PlannerStates(StatesGroup):
    waiting_for_content = State()


# --- Start Planning Mode ---
@router.callback_query(F.data.startswith("channel_plan:"))
async def cb_start_planning(call: CallbackQuery, state: FSMContext):
    channel_id = int(call.data.split(":")[1])
    channel = await get_channel_by_id(channel_id)
    if not channel:
        await call.answer("Канал не найден.", show_alert=True)
        return

    slots_raw = channel.get("schedule_slots", "[]")
    try:
        slots = json.loads(slots_raw)
    except Exception:
        slots = []
    slots_str = ", ".join(slots) if slots else "Не задано"

    last_post_iso = await get_last_scheduled_post_time(channel_id)
    last_post_str = ""
    if slots:
        if last_post_iso:
            try:
                dt = datetime.fromisoformat(last_post_iso)
                last_post_str = f"\n<b>Время последнего поста в очереди:</b> {dt.strftime('%d.%m.%Y %H:%M')}"
            except Exception:
                pass
    else:
        last_post_str = "\n<i>Все запланированные вами посты будут отправлены в канал немедленно по окончанию планирования.</i>"

    session_id = str(uuid.uuid4())
    await state.set_state(PlannerStates.waiting_for_content)
    await state.update_data(
        channel_id=channel_id,
        session_id=session_id,
        last_slot_iso=last_post_iso,
        counter=1,
    )

    prompt = (
        "<b>Планировщик постов</b>\n\n"
        "Здесь вы можете запланировать посты в ваш канал. Отправляйте сюда контент, "
        "бот в ответе покажет готовый пост, а также время, на которое он будет запланирован.\n\n"
        "По окончании нажмите кнопку <b>Готово</b>, для того, чтобы дать понять боту, "
        "что планирование контента на ваш канал завершено.\n\n"
        f"<b>Выбранный вами канал:</b> '{channel.get('channel_title')}'.\n"
        f"<b>Расписание:</b> {slots_str}"
        f"{last_post_str}"
    )

    await call.message.answer(
        prompt,
        reply_markup=get_planner_keyboard(),
        parse_mode="HTML"
    )
    await call.answer()


# --- Finish / Cancel planning buttons ---
@router.message(PlannerStates.waiting_for_content, F.text == "Готово")
async def process_planner_done(message: Message, state: FSMContext):
    data = await state.get_data()
    channel_id = data.get("channel_id")
    await state.clear()

    await message.answer(
        "Добавление контента завершено.",
        reply_markup=ReplyKeyboardRemove()
    )

    # Show "Мои каналы" as seen in freeman export
    from database import get_user_channels
    from keyboards import get_channels_list_keyboard
    channels = await get_user_channels(message.from_user.id)
    text = (
        "<b>Мои каналы</b>\n\n"
        "Здесь вы можете посмотреть список подключенных каналов, и подключить новые. "
        "Выберите канал для перехода к управлению каналом."
    )
    await message.answer(
        text,
        reply_markup=get_channels_list_keyboard(channels),
        parse_mode="HTML"
    )


@router.message(PlannerStates.waiting_for_content, F.text == "Отмена")
async def process_planner_cancel(message: Message, state: FSMContext):
    data = await state.get_data()
    session_id = data.get("session_id")

    if session_id:
        deleted = await cancel_session_posts(session_id)
        logger.info(f"Session {session_id} cancelled. Removed {deleted} pending posts.")

    await state.clear()

    await message.answer(
        "Добавление контента отменено.",
        reply_markup=ReplyKeyboardRemove()
    )

    # Show "Главное меню" as seen in freeman export
    from keyboards import get_main_menu_keyboard
    welcome_text = (
        "<b>Главное меню</b>\n\n"
        "Для начала работы выберите соответствующий пункт меню."
    )
    await message.answer(
        welcome_text,
        reply_markup=get_main_menu_keyboard(),
        parse_mode="HTML"
    )


# --- Content Intake (Media, Albums/Batches, Documents, Text) ---
@router.message(PlannerStates.waiting_for_content)
async def process_incoming_content(message: Message, state: FSMContext, album: Optional[List[Message]] = None):
    data = await state.get_data()
    channel_id = data.get("channel_id")
    session_id = data.get("session_id")
    counter = data.get("counter", 1)
    current_last_slot_iso = data.get("last_slot_iso")

    channel = await get_channel_by_id(channel_id)
    if not channel:
        await message.answer("Ошибка: канал не найден.")
        await state.clear()
        return

    slots_raw = channel.get("schedule_slots", "[]")
    try:
        schedule_slots = json.loads(slots_raw)
    except Exception:
        schedule_slots = []

    channel_default_caption = channel.get("caption")

    # If messages arrived as a Telegram album/media_group, unpack each file into a separate scheduled post!
    messages_to_process = album if album else [message]
    # Common batch caption if provided with the album
    batch_caption = None
    if album:
        for m in album:
            if m.caption:
                batch_caption = m.caption
                break

    for m in messages_to_process:
        # 1. Determine media type and file
        msg_type = None
        file_id = None
        text_content = None

        if m.photo:
            msg_type = "photo"
            file_id = m.photo[-1].file_id
        elif m.video:
            msg_type = "video"
            file_id = m.video.file_id
        elif m.animation:
            msg_type = "animation"
            file_id = m.animation.file_id
        elif m.document:
            msg_type = "document"
            file_id = m.document.file_id
        elif m.audio:
            msg_type = "audio"
            file_id = m.audio.file_id
        elif m.text:
            msg_type = "text"
            text_content = m.html_text
        else:
            await m.answer("❗️ Неподдерживаемый тип сообщения. Отправьте фото, видео, анимацию, документ или текст.")
            continue

        # 2. Determine caption for this specific item
        item_caption = m.caption or batch_caption or channel_default_caption
        if msg_type == "text":
            item_caption = text_content

        # 3. Calculate next slot for this post
        slot_dt, formatted_slot_time = calculate_next_slot(schedule_slots, current_last_slot_iso)
        slot_iso = slot_dt.isoformat()
        current_last_slot_iso = slot_iso

        # 4. Save to database as an individual post
        await add_post(
            channel_id=channel_id,
            user_id=message.from_user.id,
            session_id=session_id,
            msg_type=msg_type,
            publish_at=slot_iso,
            file_id=file_id,
            caption=item_caption,
            text=text_content,
            album_data=None
        )

        # 5. Send planning notice
        await message.answer(
            f"{counter}. Планируемое время поста: <b>{formatted_slot_time}</b>",
            parse_mode="HTML"
        )

        # 6. Send individual post preview
        try:
            if msg_type == "photo":
                await message.answer_photo(photo=file_id, caption=item_caption, parse_mode="HTML")
            elif msg_type == "video":
                await message.answer_video(video=file_id, caption=item_caption, parse_mode="HTML")
            elif msg_type == "animation":
                await message.answer_animation(animation=file_id, caption=item_caption, parse_mode="HTML")
            elif msg_type == "document":
                await message.answer_document(document=file_id, caption=item_caption, parse_mode="HTML")
            elif msg_type == "audio":
                await message.answer_audio(audio=file_id, caption=item_caption, parse_mode="HTML")
            elif msg_type == "text":
                await message.answer(text_content, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Failed to echo post preview: {e}")
            if item_caption:
                await message.answer(f"Подпись:\n{item_caption}", parse_mode=None)

        counter += 1
        # Small delay between previews to avoid Telegram rate limits
        if len(messages_to_process) > 1:
            import asyncio
            await asyncio.sleep(0.25)

    # Update state with the latest slot time and counter
    await state.update_data(
        last_slot_iso=current_last_slot_iso,
        counter=counter
    )
