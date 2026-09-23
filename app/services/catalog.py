import logging
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Service

logger = logging.getLogger(__name__)


class CatalogError(ValueError):
    __slots__ = ()


def validate_name(value: str) -> str:
    name = value.strip()
    if not 2 <= len(name) <= 120:
        raise CatalogError("Название должно содержать от 2 до 120 символов.")
    return name


def validate_description(value: str) -> str:
    description = value.strip()
    if len(description) > 500:
        raise CatalogError("Описание должно быть не длиннее 500 символов.")
    return description


def validate_price(value: str) -> Decimal:
    try:
        price = Decimal(value.strip().replace(",", "."))
    except InvalidOperation as exc:
        raise CatalogError("Введите цену числом, например 15000 или 15000,50.") from exc
    if not price.is_finite() or price < 0 or price > Decimal("9999999999.99"):
        raise CatalogError("Цена должна быть от 0 до 9 999 999 999,99 ₽.")
    if price.as_tuple().exponent < -2:
        raise CatalogError("Укажите не более двух знаков после запятой.")
    return price


def validate_duration(value: str) -> int:
    raw = value.strip()
    if not raw.isascii() or not raw.isdecimal():
        raise CatalogError("Введите длительность целым числом минут.")
    minutes = int(raw)
    if not 15 <= minutes <= 1440:
        raise CatalogError("Длительность должна быть от 15 до 1440 минут.")
    return minutes


async def all_services(session: AsyncSession) -> list[Service]:
    return list((await session.scalars(select(Service).order_by(Service.id))).all())


async def service_for_update(session: AsyncSession, service_id: int) -> Service:
    service = await session.scalar(
        select(Service).where(Service.id == service_id).with_for_update()
    )
    if service is None:
        raise CatalogError("Услуга не найдена. Откройте список заново.")
    return service


async def set_service_active(
    session: AsyncSession,
    service_id: int,
    active: bool,
    admin_id: int,
) -> Service:
    service = await service_for_update(session, service_id)
    service.is_active = active
    await session.commit()
    logger.info("Admin %s set service %s active=%s", admin_id, service.id, active)
    return service


async def set_service_price(
    session: AsyncSession,
    service_id: int,
    raw_price: str,
    admin_id: int,
) -> Service:
    price = validate_price(raw_price)
    service = await service_for_update(session, service_id)
    service.price_from = price
    await session.commit()
    logger.info("Admin %s changed price of service %s", admin_id, service.id)
    return service


async def set_service_duration(
    session: AsyncSession,
    service_id: int,
    raw_duration: str,
    admin_id: int,
) -> Service:
    duration = validate_duration(raw_duration)
    service = await service_for_update(session, service_id)
    service.duration_minutes = duration
    await session.commit()
    logger.info("Admin %s changed duration of service %s", admin_id, service.id)
    return service


async def add_service(
    session: AsyncSession,
    *,
    name: str,
    description: str,
    raw_price: str,
    raw_duration: str,
    admin_id: int,
) -> Service:
    service = Service(
        name=validate_name(name),
        description=validate_description(description),
        price_from=validate_price(raw_price),
        duration_minutes=validate_duration(raw_duration),
        is_active=True,
    )
    session.add(service)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise CatalogError("Услуга с таким названием уже существует.") from exc
    logger.info("Admin %s added service %s", admin_id, service.id)
    return service
