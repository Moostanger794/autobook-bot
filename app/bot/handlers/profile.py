from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.callbacks import parse_callback_id
from app.bot.common import booking_text, notify_admins, status_label
from app.config import get_settings
from app.database.models import BookingStatus
from app.database.session import session_factory
from app.services.bookings import BookingUnavailable, change_status, get_booking, user_bookings
from app.services.business import business_values

router = Router()


@router.message(F.text == "📅 Мои записи")
async def my_bookings(message: Message) -> None:
    async with session_factory() as session:
        values = await business_values(session, get_settings())
        now = datetime.now(ZoneInfo(values["timezone"]))
        items = await user_bookings(session, message.from_user.id, now.date())
    if not items:
        await message.answer("У вас пока нет будущих записей.")
        return
    for item in items:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Подробнее", callback_data=f"view:{item.id}"),
                    InlineKeyboardButton(text="Отменить запись", callback_data=f"cancel:{item.id}"),
                ]
            ]
        )
        await message.answer(
            f"#{item.id} · {escape(item.service.name)}\n{escape(item.car)}\n"
            f"{item.booking_date:%d.%m.%Y} в {item.start_time:%H:%M}\n"
            f"Статус: {status_label(item.status)}",
            reply_markup=markup,
        )


@router.callback_query(F.data.startswith("view:"))
async def view(callback: CallbackQuery) -> None:
    try:
        booking_id = parse_callback_id(callback.data, "view:")
    except ValueError:
        await callback.answer("Кнопка устарела.", show_alert=True)
        return
    async with session_factory() as session:
        booking = await get_booking(session, booking_id)
    if booking is None or booking.telegram_user_id != callback.from_user.id:
        await callback.answer("Запись не найдена.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(booking_text(booking))


@router.callback_query(F.data.startswith("cancel:"))
async def cancel_booking(callback: CallbackQuery) -> None:
    try:
        booking_id = parse_callback_id(callback.data, "cancel:")
    except ValueError:
        await callback.answer("Кнопка устарела.", show_alert=True)
        return
    async with session_factory() as session:
        values = await business_values(session, get_settings())
        try:
            booking = await change_status(
                session,
                booking_id,
                BookingStatus.CANCELLED,
                actor_id=callback.from_user.id,
                now=datetime.now(ZoneInfo(values["timezone"])),
            )
        except BookingUnavailable as exc:
            await callback.answer(str(exc), show_alert=True)
            return
    await callback.answer("Запись отменена.")
    await callback.message.answer(f"Запись #{booking.id} отменена. Время снова доступно.")
    await notify_admins(
        callback.bot, get_settings(), booking_text(booking, title="❌ КЛИЕНТ ОТМЕНИЛ ЗАПИСЬ")
    )
