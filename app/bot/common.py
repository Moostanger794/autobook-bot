import logging
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiohttp import ClientError

from app.config import Settings
from app.database.models import Booking

logger = logging.getLogger(__name__)


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
        f"Статус: {booking.status}{comment}"
    )


def admin_booking_markup(booking_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить", callback_data=f"as:confirmed:{booking_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменить", callback_data=f"as:cancelled:{booking_id}"
                ),
                InlineKeyboardButton(
                    text="🏁 Выполнено", callback_data=f"as:completed:{booking_id}"
                ),
            ],
        ]
    )


async def notify_admins(
    bot: Bot, settings: Settings, text: str, booking_id: int | None = None
) -> None:
    markup = admin_booking_markup(booking_id) if booking_id is not None else None
    for admin_id in settings.admins:
        try:
            await bot.send_message(admin_id, text, reply_markup=markup)
        except (TelegramAPIError, ClientError, OSError, TimeoutError) as exc:
            logger.error("Admin notification failed for %s: %s", admin_id, type(exc).__name__)
