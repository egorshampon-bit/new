import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext

from database import get_user_channels, add_or_update_user
from keyboards import get_main_menu_keyboard, get_channels_list_keyboard

router = Router()
logger = logging.getLogger(__name__)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await add_or_update_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name
    )
    welcome_text = (
        "<b>Добро пожаловать!</b>\n\n"
        "Этот бот предназначен для автоматизации постинга контента в каналы и чаты "
        "по настраиваемому расписанию.\n\n"
        "<b>Главное меню:</b>\n"
        "Для начала работы выберите соответствующий пункт меню ниже."
    )
    await message.answer(
        welcome_text,
        reply_markup=get_main_menu_keyboard(),
        parse_mode="HTML"
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    help_text = (
        "<b>📖 Как пользоваться ботом:</b>\n\n"
        "1. <b>Подключите канал</b>: добавьте бота администратором в ваш канал с правом публикации, "
        "затем пришлите @username или перешлите любой пост из канала.\n\n"
        "2. <b>Настройте расписание</b>: укажите часы публикации (например <code>15,18</code> или <code>14:00, 21:00</code>).\n\n"
        "3. <b>Задайте подпись</b> (по желанию): текст/ссылки, которые автоматически будут прикрепляться ко всем постам.\n\n"
        "4. <b>Запланируйте посты</b>: зайдите в «Планировщик постов» и отправьте пачку медиафайлов "
        "(фото, видео, гифки, альбомы, документы). Бот автоматически распределит их по вашим интервалам: "
        "сегодня в 15 и 18, завтра в 15 и 18, послезавтра и т.д.\n\n"
        "5. По завершении нажмите <b>«Готово»</b>."
    )
    await message.answer(
        help_text,
        reply_markup=get_main_menu_keyboard(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "menu_main")
async def cb_main_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    welcome_text = (
        "<b>Главное меню</b>\n\n"
        "Для начала работы выберите соответствующий пункт меню."
    )
    await call.message.edit_text(
        welcome_text,
        reply_markup=get_main_menu_keyboard(),
        parse_mode="HTML"
    )
    await call.answer()


@router.callback_query(F.data == "menu_help")
async def cb_help(call: CallbackQuery):
    help_text = (
        "<b>📖 Как пользоваться ботом:</b>\n\n"
        "1. <b>Подключите канал</b>: добавьте бота администратором в ваш канал с правом публикации, "
        "затем отправьте @username или перешлите любой пост из него.\n\n"
        "2. <b>Настройте расписание</b>: укажите часы публикаций (например: <code>15,18</code>).\n\n"
        "3. <b>Задайте подпись</b>: текст, который будет автоматически подставляться к постам.\n\n"
        "4. <b>Запланируйте контент</b>: отправьте пачку файлов — бот разложит их по календарной очереди "
        "строго по вашим слотам и покажет готовые превью.\n\n"
        "5. Нажмите <b>«Готово»</b> для фиксации очереди."
    )
    await call.message.edit_text(
        help_text,
        reply_markup=get_main_menu_keyboard(),
        parse_mode="HTML"
    )
    await call.answer()
