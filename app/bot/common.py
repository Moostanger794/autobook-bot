import logging
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiohttp import ClientError

from app.config import Settings
from app.database.models import Booking, BookingStatus

logger = logging.getLogger(__name__)

STATUS_LABELS = {
    BookingStatus.PENDING: "ожидает подтверждения",
    BookingStatus.CONFIRMED: "подтверждена",
    BookingStatus.CANCELLED: "отменена",
    BookingStatus.COMPLETED: "выполнена",
}


def status_label(status: BookingStatus | str) -> str:
    return STATUS_LABELS[BookingStatus(status)]


def brief_html(value: str, limit: int) -> str:
    return escape(value if len(value) <= limit else value[: limit - 1] + "…")


def booking_text(booking: Booking, *, title: str = "Запись") -> str:
    comment = f"\n💬 {brief_html(booking.comment, 250)}" if booking.comment else ""
    return (
        f"<b>{brief_html(title, 80)} #{booking.id}</b>\n\n"
        f"👤 {brief_html(booking.customer_name, 80)}\n"
        f"📱 {escape(booking.phone)}\n"
        f"🚘 {brief_html(booking.car, 80)}\n"
        f"🛠 {brief_html(booking.service.name, 80)}\n"
        f"💰 от {booking.service.price_from:,.0f} ₽\n"
        f"📅 {booking.booking_date:%d.%m.%Y}\n"
        f"🕒 {booking.start_time:%H:%M}\n"
        f"Статус: {status_label(booking.status)}{comment}"
    )


def admin_booking_markup(booking: Booking) -> InlineKeyboardMarkup | None:
    if booking.status == BookingStatus.PENDING:
        actions = [
            ("✅ Подтвердить", BookingStatus.CONFIRMED),
            ("❌ Отменить", BookingStatus.CANCELLED),
        ]
    elif booking.status == BookingStatus.CONFIRMED:
        actions = [
            ("❌ Отменить", BookingStatus.CANCELLED),
            ("🏁 Выполнено", BookingStatus.COMPLETED),
        ]
    else:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"as:{status}:{booking.id}")]
            for label, status in actions
        ]
    )


async def notify_admins(
    bot: Bot, settings: Settings, text: str, booking: Booking | None = None
) -> None:
    markup = admin_booking_markup(booking) if booking is not None else None
    for admin_id in settings.admins:
        try:
            await bot.send_message(admin_id, text, reply_markup=markup)
        except (TelegramAPIError, ClientError, OSError, TimeoutError) as exc:
            logger.error("Admin notification failed for %s: %s", admin_id, type(exc).__name__)
