from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Booking, BookingStatus, Service

COUNTED_STATUSES = (
    BookingStatus.PENDING,
    BookingStatus.CONFIRMED,
    BookingStatus.COMPLETED,
)


def period_bounds(today: date) -> dict[str, tuple[date, date]]:
    monday = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return {
        "today": (today, today + timedelta(days=1)),
        "week": (monday, monday + timedelta(days=7)),
        "month": (month_start, next_month),
    }


async def booking_counts(session: AsyncSession, today: date) -> dict[str, int]:
    result: dict[str, int] = {}
    for label, (start, end) in period_bounds(today).items():
        result[label] = int(
            await session.scalar(
                select(func.count(Booking.id)).where(
                    Booking.booking_date >= start,
                    Booking.booking_date < end,
                    Booking.status.in_(COUNTED_STATUSES),
                )
            )
            or 0
        )
    return result


async def top_services(session: AsyncSession, today: date) -> list[tuple[str, int, Decimal]]:
    start, end = period_bounds(today)["month"]
    rows = (
        await session.execute(
            select(Service.name, func.count(Booking.id), func.sum(Service.price_from))
            .join(Booking, Booking.service_id == Service.id)
            .where(
                Booking.booking_date >= start,
                Booking.booking_date < end,
                Booking.status.in_(COUNTED_STATUSES),
            )
            .group_by(Service.id)
            .order_by(func.count(Booking.id).desc())
            .limit(5)
        )
    ).all()
    return [(name, int(count), revenue) for name, count, revenue in rows]
