import csv
from io import StringIO

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import Booking


def excel_safe(value: object) -> object:
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


async def export_csv(session: AsyncSession) -> bytes:
    bookings = (
        await session.scalars(
            select(Booking).options(selectinload(Booking.service)).order_by(Booking.id)
        )
    ).all()
    output = StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(
        [
            "id",
            "customer",
            "phone",
            "telegram",
            "car",
            "service",
            "date",
            "time",
            "price",
            "status",
            "comment",
        ]
    )
    for row in bookings:
        writer.writerow(
            [
                excel_safe(value)
                for value in [
                    row.id,
                    row.customer_name,
                    row.phone,
                    row.telegram_username or row.telegram_user_id,
                    row.car,
                    row.service.name,
                    row.booking_date.isoformat(),
                    row.start_time.strftime("%H:%M"),
                    row.service.price_from,
                    row.status,
                    row.comment or "",
                ]
            ]
        )
    return output.getvalue().encode("utf-8-sig")
