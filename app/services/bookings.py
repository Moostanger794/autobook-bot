import logging
import re
from datetime import date, datetime, time

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import Booking, BookingStatus, Service, User
from app.services.capacity import occupied_intervals
from app.services.schedule import available_slots, end_time

logger = logging.getLogger(__name__)


class SlotUnavailable(Exception):
    __slots__ = ()


class BookingUnavailable(Exception):
    __slots__ = ()


def normalize_phone(value: str) -> str:
    phone = re.sub(r"[\s()\-]", "", value.strip())
    if not re.fullmatch(r"\+?[0-9]{10,15}", phone):
        raise ValueError("Введите номер в формате +79991234567")
    return phone


async def list_services(session: AsyncSession) -> list[Service]:
    return list(
        (await session.scalars(select(Service).where(Service.is_active).order_by(Service.id))).all()
    )


async def get_service(session: AsyncSession, service_id: int) -> Service | None:
    return await session.scalar(select(Service).where(Service.id == service_id, Service.is_active))


async def slots_for_service(
    session: AsyncSession,
    service: Service,
    day: date,
    start: time,
    close: time,
    days: frozenset[int],
    step: int,
    now: datetime,
) -> list[time]:
    occupied = await occupied_intervals(session, day)
    return available_slots(day, service.duration_minutes, start, close, days, step, occupied, now)


async def create_booking(
    session: AsyncSession,
    *,
    user_id: int,
    username: str | None,
    customer_name: str,
    phone: str,
    car: str,
    service_id: int,
    day: date,
    start_at: time,
    comment: str | None,
    work_start: time,
    work_end: time,
    work_days: frozenset[int],
    step: int,
    now: datetime,
) -> Booking:
    service = await session.scalar(
        select(Service)
        .where(Service.id == service_id, Service.is_active)
        .with_for_update(read=True)
    )
    if service is None:
        raise BookingUnavailable("Услуга больше недоступна.")
    if start_at not in await slots_for_service(
        session, service, day, work_start, work_end, work_days, step, now
    ):
        raise SlotUnavailable("Это время уже недоступно. Выберите другое.")
    await session.execute(
        insert(User)
        .values(
            id=user_id,
            username=username,
            full_name=customer_name[:150],
        )
        .on_conflict_do_update(
            index_elements=[User.id],
            set_={"username": username, "full_name": customer_name[:150]},
        )
    )
    booking = Booking(
        telegram_user_id=user_id,
        telegram_username=username,
        customer_name=customer_name[:150],
        phone=normalize_phone(phone),
        car=car[:120],
        service_id=service_id,
        booking_date=day,
        start_time=start_at,
        end_time=end_time(start_at, service.duration_minutes),
        comment=comment,
        status=BookingStatus.CONFIRMED,
    )
    session.add(booking)
    try:
        await session.commit()
    except DBAPIError as exc:
        await session.rollback()
	sqlstate = getattr(exc.orig, "sqlstate", None)
        if sqlstate in {"23P01", "40P01"}:
            raise SlotUnavailable("Это время уже занято. Выберите другое.") from exc
        raise
    await session.refresh(booking)
    booking.service = service
    logger.info("Booking %s created", booking.id)
    return booking


async def user_bookings(session: AsyncSession, user_id: int, today: date) -> list[Booking]:
    return list(
        (
            await session.scalars(
                select(Booking)
                .options(selectinload(Booking.service))
                .where(
                    Booking.telegram_user_id == user_id,
                    Booking.booking_date >= today,
                    Booking.status.in_([BookingStatus.PENDING, BookingStatus.CONFIRMED]),
                )
                .order_by(Booking.booking_date, Booking.start_time)
            )
        ).all()
    )


async def get_booking(session: AsyncSession, booking_id: int) -> Booking | None:
    return await session.scalar(
        select(Booking).options(selectinload(Booking.service)).where(Booking.id == booking_id)
    )


async def change_status(
    session: AsyncSession,
    booking_id: int,
    status: BookingStatus,
    *,
    actor_id: int | None = None,
    now: datetime | None = None,
) -> Booking:
    booking = await session.scalar(
        select(Booking)
        .options(selectinload(Booking.service))
        .where(Booking.id == booking_id)
        .with_for_update()
    )
    if booking is None:
        raise BookingUnavailable("Запись не найдена.")
    if actor_id is not None and booking.telegram_user_id != actor_id:
        raise BookingUnavailable("Запись не найдена.")
    if booking.status == BookingStatus.CANCELLED:
        raise BookingUnavailable("Запись уже отменена.")
    if booking.status == status:
        raise BookingUnavailable("Этот статус уже установлен.")
    if (
        status == BookingStatus.CANCELLED
        and now is not None
        and datetime.combine(booking.booking_date, booking.start_time) <= now.replace(tzinfo=None)
    ):
        raise BookingUnavailable("Прошедшую запись нельзя отменить.")
    if booking.status == BookingStatus.COMPLETED:
        raise BookingUnavailable("Запись уже выполнена.")
    booking.status = status
    await session.commit()
    logger.info("Booking %s changed to %s by %s", booking.id, status, actor_id or "admin")
    return booking
