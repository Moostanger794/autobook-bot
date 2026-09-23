from datetime import date, datetime, time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.bot.callbacks import parse_callback_id
from app.bot.common import admin_booking_markup, booking_text
from app.bot.handlers import admin_services
from app.bot.handlers.admin import is_admin
from app.config import get_settings
from app.database.session import get_session
from app.services.bookings import normalize_phone
from app.services.catalog import CatalogError, validate_duration, validate_price
from app.services.export import excel_safe
from app.services.reminders import reminder_kind, reminder_text
from app.services.schedule import available_slots, end_time
from app.services.statistics import period_bounds


def test_slots_exclude_overlaps_and_closing_time():
    slots = available_slots(
        date(2026, 9, 28),
        60,
        time(10),
        time(13),
        frozenset({0}),
        30,
        [(time(11), time(12))],
        datetime(2026, 9, 28, 9, tzinfo=ZoneInfo("Europe/Moscow")),
    )
    assert slots == [time(10), time(12)]


def test_non_working_day_and_past_time():
    assert (
        available_slots(
            date(2026, 9, 27),
            30,
            time(10),
            time(20),
            frozenset({0}),
            30,
            [],
            datetime(2026, 9, 26, 9, tzinfo=ZoneInfo("Europe/Moscow")),
        )
        == []
    )
    assert available_slots(
        date(2026, 9, 28),
        30,
        time(10),
        time(11),
        frozenset({0}),
        30,
        [],
        datetime(2026, 9, 28, 10, 10, tzinfo=ZoneInfo("Europe/Moscow")),
    ) == [time(10, 30)]


def test_duration():
    assert end_time(time(15), 90) == time(16, 30)
    with pytest.raises(ValueError):
        end_time(time(23), 120)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+7 (999) 123-45-67", "+79991234567"),
        ("89991234567", "89991234567"),
    ],
)
def test_phone(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["hello", "123", "+7 999 abc", "1" * 20, "１２３４５６７８９０"])
def test_invalid_phone(raw):
    with pytest.raises(ValueError):
        normalize_phone(raw)


def test_admin_access(monkeypatch):
    monkeypatch.setenv("ADMIN_IDS", "123,456")
    get_settings.cache_clear()
    try:
        assert is_admin(123)
        assert is_admin(456)
        assert not is_admin(789)
    finally:
        get_settings.cache_clear()


def test_admin_callback_fits_telegram_limit():
    markup = admin_booking_markup(9223372036854775807)
    assert all(
        len(button.callback_data.encode("utf-8")) <= 64
        for row in markup.inline_keyboard
        for button in row
    )


@pytest.mark.parametrize("raw", ["", "0", "-1", "９", "9x", "9" * 40])
def test_invalid_callback_id(raw):
    with pytest.raises(ValueError):
        parse_callback_id(raw, "")


def test_service_callback_fits_telegram_limit():
    service = SimpleNamespace(id=9223372036854775807, is_active=True)
    markup = admin_services.service_markup(service)
    assert all(
        len(button.callback_data.encode("utf-8")) <= 64
        for row in markup.inline_keyboard
        for button in row
    )


async def test_non_admin_cannot_edit_services(monkeypatch):
    monkeypatch.setattr(admin_services, "is_admin", lambda user_id: False)
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=999),
        data="svc:disable:1",
        answer=AsyncMock(),
    )
    await admin_services.service_action(callback, None)
    callback.answer.assert_awaited_once_with("Доступ запрещён.", show_alert=True)


def test_catalog_validation():
    assert validate_price("15000,50") == 15000.50
    assert validate_duration("90") == 90
    with pytest.raises(CatalogError):
        validate_price("NaN")
    with pytest.raises(CatalogError):
        validate_duration("1.5")


def test_statistics_period_boundaries():
    bounds = period_bounds(date(2026, 9, 30))
    assert bounds == {
        "today": (date(2026, 9, 30), date(2026, 10, 1)),
        "week": (date(2026, 9, 28), date(2026, 10, 5)),
        "month": (date(2026, 9, 1), date(2026, 10, 1)),
    }
    assert period_bounds(date(2024, 2, 29))["month"] == (
        date(2024, 2, 1),
        date(2024, 3, 1),
    )


def test_reminder_uses_elapsed_time_across_dst():
    booking = SimpleNamespace(
        booking_date=date(2026, 3, 29),
        start_time=time(12),
        reminder_24h_sent=False,
        reminder_2h_sent=False,
    )
    now = datetime(2026, 3, 28, 11, 30, tzinfo=ZoneInfo("Europe/Berlin"))
    assert reminder_kind(booking, now) == "24h"


def test_html_and_csv_injection_are_escaped():
    booking = SimpleNamespace(
        booking_date=date(2026, 9, 24),
        start_time=time(11),
        service=SimpleNamespace(name="<bad>"),
        car="<car>",
    )
    rendered = reminder_text(booking, "<address>")
    assert "<bad>" not in rendered and "&lt;bad&gt;" in rendered
    assert "<car>" not in rendered and "&lt;address&gt;" in rendered
    assert excel_safe(' =HYPERLINK("evil")') == '\' =HYPERLINK("evil")'


def test_booking_message_fits_telegram_limit():
    booking = SimpleNamespace(
        id=1,
        customer_name='"' * 150,
        phone="+79991234567",
        car='"' * 120,
        service=SimpleNamespace(name='"' * 120, price_from=1000),
        booking_date=date(2026, 9, 24),
        start_time=time(11),
        status="confirmed",
        comment='"' * 500,
    )
    assert len(booking_text(booking)) <= 4096


def test_health_and_api_key(monkeypatch):
    monkeypatch.setenv("API_KEY", "test-key")
    get_settings.cache_clear()

    class EmptySession:
        async def scalars(self, query):
            class EmptyResult:
                def all(self):
                    return []

            return EmptyResult()

    app.dependency_overrides[get_session] = lambda: EmptySession()
    try:
        with TestClient(app) as client:
            assert client.get("/health").json() == {"status": "ok"}
            assert client.get("/api/bookings").status_code == 401
            assert client.get("/api/bookings", headers={"X-API-Key": "wrong"}).status_code == 401
            assert client.get("/api/bookings", headers={"X-API-Key": "test-key"}).json() == []
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
