import asyncio
import os
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.database.models import Base, Booking, BookingStatus, Service, User
from app.services import bookings, reminders
from app.services.bookings import BookingUnavailable, SlotUnavailable, change_status, create_booking
from app.services.catalog import (
    CatalogError,
    add_service,
    all_services,
    set_service_active,
    set_service_duration,
    set_service_price,
)
from app.services.statistics import booking_counts, top_services


@pytest.fixture
async def db():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL integration tests")
    if not url.startswith("postgresql+asyncpg://"):
        raise ValueError("TEST_DATABASE_URL must point to PostgreSQL")
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA IF EXISTS autobook_test CASCADE"))
        await connection.execute(text("CREATE SCHEMA autobook_test"))
        await connection.execute(text("SET search_path TO autobook_test, public"))
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection.execution_options(schema_translate_map={None: "autobook_test"})
            )
        )
        await connection.execute(
            text("""
            ALTER TABLE autobook_test.bookings ADD CONSTRAINT no_overlapping_active_bookings
            EXCLUDE USING gist (
                tsrange(booking_date + start_time, booking_date + end_time, '[)') WITH &&
            ) WHERE (status IN ('pending', 'confirmed'))
        """)
        )
    await engine.dispose()
    engine = create_async_engine(
        url, connect_args={"server_settings": {"search_path": "autobook_test,public"}}
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        session.add(
            Service(name="Test wash", description="Wash", price_from=1000, duration_minutes=60)
        )
        await session.commit()
    yield maker
    await engine.dispose()
    cleanup = create_async_engine(url)
    async with cleanup.begin() as connection:
        await connection.execute(text("DROP SCHEMA autobook_test CASCADE"))
    await cleanup.dispose()


async def _book(maker, user_id, at):
    async with maker() as session:
        return await create_booking(
            session,
            user_id=user_id,
            username=None,
            customer_name=f"User {user_id}",
            phone="+79991234567",
            car="Test car",
            service_id=1,
            day=datetime.now(ZoneInfo("Europe/Moscow")).date() + timedelta(days=7),
            start_at=at,
            comment=None,
            work_start=time(10),
            work_end=time(20),
            work_days=frozenset(range(7)),
            step=30,
            now=datetime.now(ZoneInfo("Europe/Moscow")),
        )


async def test_double_booking_and_cancellation(db):
    first = await _book(db, 101, time(11))
    assert first.status == BookingStatus.PENDING
    async with db() as session:
        with pytest.raises(BookingUnavailable):
            await change_status(session, first.id, BookingStatus.COMPLETED)
    with pytest.raises(SlotUnavailable):
        await _book(db, 102, time(11, 30))
    async with db() as session:
        cancelled = await change_status(
            session,
            first.id,
            BookingStatus.CANCELLED,
            actor_id=101,
            now=datetime.now(ZoneInfo("Europe/Moscow")),
        )
        assert cancelled.status == BookingStatus.CANCELLED
    second = await _book(db, 102, time(11, 30))
    assert second.id != first.id
    async with db() as session:
        with pytest.raises(BookingUnavailable):
            await change_status(
                session,
                first.id,
                BookingStatus.CANCELLED,
                actor_id=101,
                now=datetime.now(ZoneInfo("Europe/Moscow")),
            )


async def test_concurrent_booking_uses_database_constraint(db, monkeypatch):
    original_slots = bookings.slots_for_service
    ready = asyncio.Event()
    release = asyncio.Event()
    checked = 0

    async def synchronized_slots(*args, **kwargs):
        nonlocal checked
        slots = await original_slots(*args, **kwargs)
        checked += 1
        if checked == 2:
            ready.set()
        await release.wait()
        return slots

    monkeypatch.setattr(bookings, "slots_for_service", synchronized_slots)
    first = asyncio.create_task(_book(db, 201, time(14)))
    second = asyncio.create_task(_book(db, 202, time(14)))
    await asyncio.wait_for(ready.wait(), timeout=5)
    release.set()
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, SlotUnavailable) for result in results) == 1
    conflict = next(result for result in results if isinstance(result, SlotUnavailable))
    assert getattr(conflict.__cause__.orig, "sqlstate", None) in {"23P01", "40P01"}


async def test_repeated_confirmation_by_same_user_is_safe(db):
    results = await asyncio.gather(
        _book(db, 203, time(16)),
        _book(db, 203, time(16)),
        return_exceptions=True,
    )
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, SlotUnavailable) for result in results) == 1


async def test_past_booking_cannot_be_cancelled(db):
    booking = await _book(db, 301, time(15))
    async with db() as session:
        with pytest.raises(BookingUnavailable):
            await change_status(
                session,
                booking.id,
                BookingStatus.CANCELLED,
                actor_id=301,
                now=datetime.combine(booking.booking_date, time(16)),
            )


async def insert_booking(
    db, user_id: int, day: date, hour: int = 12, status: BookingStatus = BookingStatus.CONFIRMED
) -> int:
    async with db() as session:
        session.add(User(id=user_id, username=None, full_name=f"User {user_id}"))
        booking = Booking(
            telegram_user_id=user_id,
            telegram_username=None,
            customer_name=f"User {user_id}",
            phone="+79991234567",
            car="Test car",
            service_id=1,
            booking_date=day,
            start_time=time(hour),
            end_time=time(hour + 1),
            comment=None,
            status=status,
        )
        session.add(booking)
        await session.commit()
        return booking.id


async def test_statistics_count_only_current_periods(db):
    today = date(2026, 9, 30)
    dates = [
        date(2026, 8, 31),
        date(2026, 9, 1),
        date(2026, 9, 29),
        today,
        date(2026, 10, 1),
        date(2026, 10, 5),
    ]
    for user_id, day in enumerate(dates, start=400):
        await insert_booking(db, user_id, day)
    await insert_booking(db, 499, today, hour=15, status=BookingStatus.CANCELLED)
    async with db() as session:
        assert await booking_counts(session, today) == {"today": 1, "week": 3, "month": 3}
        top = await top_services(session, today)
        assert top[0][1] == 3


async def test_admin_service_edits(db):
    async with db() as session:
        service = await set_service_active(session, 1, False, 123)
        assert not service.is_active
        service = await set_service_active(session, 1, False, 123)
        assert not service.is_active
        service = await set_service_price(session, 1, "2500,50", 123)
        assert service.price_from == 2500.50
        service = await set_service_duration(session, 1, "120", 123)
        assert service.duration_minutes == 120
        added = await add_service(
            session,
            name="New polish",
            description="Test",
            raw_price="5000",
            raw_duration="180",
            admin_id=123,
        )
        assert added.is_active
        assert len(await all_services(session)) == 2
        with pytest.raises(CatalogError):
            await add_service(
                session,
                name="New polish",
                description="Test",
                raw_price="5000",
                raw_duration="180",
                admin_id=123,
            )


@pytest.fixture
def reminder_clock():
    return datetime(2026, 9, 23, 12, tzinfo=ZoneInfo("Europe/Moscow"))


async def reminder_booking(db, reminder_clock):
    return await insert_booking(db, 501, date(2026, 9, 24), hour=11)


async def reminder_state(db, booking_id):
    async with db() as session:
        return await session.get(Booking, booking_id)


async def test_reminder_success_is_persisted_after_send(db, monkeypatch, reminder_clock):
    booking_id = await reminder_booking(db, reminder_clock)
    monkeypatch.setattr(reminders, "session_factory", db)
    bot = SimpleNamespace(send_message=AsyncMock(return_value=None))
    await reminders.reminder_cycle(bot, Settings(), now=reminder_clock)
    state = await reminder_state(db, booking_id)
    assert state.reminder_24h_sent
    assert state.reminder_24h_claimed_at is None
    await reminders.reminder_cycle(bot, Settings(), now=reminder_clock)
    bot.send_message.assert_awaited_once()


async def test_reminder_failure_retries(db, monkeypatch, reminder_clock):
    booking_id = await reminder_booking(db, reminder_clock)
    monkeypatch.setattr(reminders, "session_factory", db)
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=OSError("telegram unavailable")))
    await reminders.reminder_cycle(bot, Settings(), now=reminder_clock)
    state = await reminder_state(db, booking_id)
    assert not state.reminder_24h_sent
    assert state.reminder_24h_claimed_at is None
    bot.send_message.side_effect = None
    await reminders.reminder_cycle(bot, Settings(), now=reminder_clock)
    assert (await reminder_state(db, booking_id)).reminder_24h_sent
    assert bot.send_message.await_count == 2


async def test_concurrent_reminder_workers_send_once(db, monkeypatch, reminder_clock):
    booking_id = await reminder_booking(db, reminder_clock)
    monkeypatch.setattr(reminders, "session_factory", db)
    entered = asyncio.Event()
    release = asyncio.Event()
    sends = 0

    async def blocked_send(user_id, text):
        nonlocal sends
        sends += 1
        entered.set()
        await release.wait()

    bot = SimpleNamespace(send_message=blocked_send)
    first = asyncio.create_task(reminders.reminder_cycle(bot, Settings(), now=reminder_clock))
    await asyncio.wait_for(entered.wait(), timeout=5)
    await reminders.reminder_cycle(bot, Settings(), now=reminder_clock)
    assert sends == 1
    assert not (await reminder_state(db, booking_id)).reminder_24h_sent
    release.set()
    await first
    assert (await reminder_state(db, booking_id)).reminder_24h_sent


async def test_expired_reminder_claim_is_retryable(db, monkeypatch, reminder_clock):
    booking_id = await reminder_booking(db, reminder_clock)
    async with db() as session:
        booking = await session.get(Booking, booking_id)
        booking.reminder_24h_claimed_at = datetime.now(UTC) - timedelta(minutes=3)
        await session.commit()
    monkeypatch.setattr(reminders, "session_factory", db)
    bot = SimpleNamespace(send_message=AsyncMock(return_value=None))
    await reminders.reminder_cycle(bot, Settings(), now=reminder_clock)
    assert (await reminder_state(db, booking_id)).reminder_24h_sent
