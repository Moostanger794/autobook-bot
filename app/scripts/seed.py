import asyncio

from sqlalchemy import select

from app.config import get_settings
from app.database.models import BusinessSetting, Service
from app.database.session import session_factory

DEFAULT_SERVICES = [
    ("Комплексная мойка", "Мойка кузова и салона", 2500, 90),
    ("Полировка кузова", "Восстановление блеска кузова", 15000, 240),
    ("Керамическое покрытие", "Защитное покрытие кузова", 30000, 360),
    ("Химчистка салона", "Глубокая очистка салона", 12000, 300),
    ("Детейлинг салона", "Комплексный уход за салоном", 18000, 360),
]


async def seed() -> None:
    settings = get_settings()
    defaults = {
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
    async with session_factory() as session:
        names = set((await session.scalars(select(Service.name))).all())
        for name, description, price, duration in DEFAULT_SERVICES:
            if name not in names:
                session.add(
                    Service(
                        name=name,
                        description=description,
                        price_from=price,
                        duration_minutes=duration,
                    )
                )
        keys = set((await session.scalars(select(BusinessSetting.key))).all())
        for key, value in defaults.items():
            if key not in keys:
                session.add(BusinessSetting(key=key, value=value))
        await session.commit()


if __name__ == "__main__":
    asyncio.run(seed())
