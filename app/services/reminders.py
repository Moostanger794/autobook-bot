import asyncio
import logging
from datetime import UTC, datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiohttp import ClientError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.database.models import Booking, BookingStatus
from app.database.session import session_factory
from app.services.business import business_values

logger = logging.getLogger(__name__)
DELIVERY_TIMEOUT_SECONDS = 20
CLAIM_TIMEOUT = timedelta(minutes=2)


def reminder_kind(booking: Booking, now: datetime) -> str | None:
    begins = datetime.combine(booking.booking_date, booking.start_time, now.tzinfo)
    hours = (begins.astimezone(UTC) - now.astimezone(UTC)).total_seconds() / 3600
    if 2 < hours <= 24 and not booking.reminder_24h_sent:
        return "24h"
    if 0 < hours <= 2 and not booking.reminder_2h_sent:
        return "2h"
    return None


def reminder_text(booking: Booking, address: str) -> str:
    return (
        "⏰ Напоминаем о записи\n\n"
        f"{booking.booking_date:%d.%m.%Y} в {booking.start_time:%H:%M}\n"
        f"{escape(booking.service.name)}\n{escape(booking.car)}\n"
        f"📍 {escape(address)}"
    )


async def claim_reminder(
    booking_id: int,
    kind: str,
    now: datetime,
    address: str,
) -> tuple[int, str, datetime] | None:
    async with session_factory() as session, session.begin():
        booking = await session.scalar(
            select(Booking)
            .options(selectinload(Booking.service))
            .where(Booking.id == booking_id)
            .with_for_update(skip_locked=True)
        )
        if booking is None or booking.status != BookingStatus.CONFIRMED:
            return None
        if reminder_kind(booking, now) != kind:
            return None
        claimed_field = f"reminder_{kind}_claimed_at"
        token = datetime.now(UTC)
        previous = getattr(booking, claimed_field)
        if previous is not None and previous > token - CLAIM_TIMEOUT:
            return None
        setattr(booking, claimed_field, token)
        return booking.telegram_user_id, reminder_text(booking, address), token


async def finish_claim(booking_id: int, kind: str, token: datetime, delivered: bool) -> None:
    async with session_factory() as session, session.begin():
        booking = await session.scalar(
            select(Booking).where(Booking.id == booking_id).with_for_update()
        )
        if booking is None:
            return
        claimed_field = f"reminder_{kind}_claimed_at"
        if getattr(booking, claimed_field) != token:
            return
        if delivered:
            setattr(booking, f"reminder_{kind}_sent", True)
        setattr(booking, claimed_field, None)


async def deliver_reminder(
    bot: Bot,
    booking_id: int,
    kind: str,
    now: datetime,
    address: str,
) -> None:
    claim = await claim_reminder(booking_id, kind, now, address)
    if claim is None:
        return
    user_id, message, token = claim
    try:
        await asyncio.wait_for(bot.send_message(user_id, message), timeout=DELIVERY_TIMEOUT_SECONDS)
    except (TelegramAPIError, ClientError, OSError, TimeoutError) as exc:
        await finish_claim(booking_id, kind, token, False)
        logger.error(
            "Reminder %s delivery failed for booking %s: %s",
            kind,
            booking_id,
            type(exc).__name__,
        )
    else:
        await finish_claim(booking_id, kind, token, True)
        logger.info("Reminder %s delivered for booking %s", kind, booking_id)


async def reminder_cycle(bot: Bot, settings: Settings, *, now: datetime | None = None) -> None:
    async with session_factory() as session:
        values = await business_values(session, settings)
        current = now or datetime.now(ZoneInfo(values["timezone"]))
        candidates = (
            await session.scalars(
                select(Booking)
                .where(
                    Booking.booking_date.between(
                        current.date(), (current + timedelta(days=1)).date()
                    ),
                    Booking.status == BookingStatus.CONFIRMED,
                )
                .order_by(Booking.booking_date, Booking.start_time)
            )
        ).all()
        due = [
            (booking.id, kind)
            for booking in candidates
            if (kind := reminder_kind(booking, current)) is not None
        ]
    for booking_id, kind in due:
        await deliver_reminder(bot, booking_id, kind, current, values["business_address"])


async def reminder_loop(bot: Bot, settings: Settings) -> None:
    while True:
        try:
            await reminder_cycle(bot, settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Reminder cycle failed")
        await asyncio.sleep(300)
