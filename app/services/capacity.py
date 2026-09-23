from datetime import date, time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Booking, BookingStatus

# One shared capacity unit for the whole business. The database exclusion
# constraint in the initial migration enforces the same rule during races.
OCCUPYING_STATUSES = (BookingStatus.PENDING, BookingStatus.CONFIRMED)


async def occupied_intervals(session: AsyncSession, day: date) -> list[tuple[time, time]]:
    rows = (
        await session.execute(
            select(Booking.start_time, Booking.end_time).where(
                Booking.booking_date == day,
                Booking.status.in_(OCCUPYING_STATUSES),
            )
        )
    ).all()
    return [(start, end) for start, end in rows]
