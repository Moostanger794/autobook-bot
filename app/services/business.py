from datetime import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.database.models import BusinessSetting


async def business_values(session: AsyncSession, settings: Settings) -> dict[str, str]:
    values = {
        "business_name": settings.business_name,
        "business_address": settings.business_address,
        "business_phone": settings.business_phone,
        "business_telegram": settings.business_telegram,
        "timezone": settings.timezone,
        "work_start": settings.work_start.strftime("%H:%M"),
        "work_end": settings.work_end.strftime("%H:%M"),
        "work_days": settings.work_days,
        "slot_step_minutes": str(settings.slot_step_minutes),
    }
    for row in (await session.scalars(select(BusinessSetting))).all():
        values[row.key] = row.value
    return values


def working_hours(values: dict[str, str]) -> tuple[time, time, frozenset[int], int]:
    start = time.fromisoformat(values["work_start"])
    end = time.fromisoformat(values["work_end"])
    days = frozenset(int(day) for day in values["work_days"].split(","))
    step = int(values["slot_step_minutes"])
    if start >= end or not days or any(day not in range(7) for day in days) or not 5 <= step <= 120:
        raise ValueError("Invalid business schedule")
    return start, end, days, step
