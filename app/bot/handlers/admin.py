import logging
from datetime import datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiohttp import ClientError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.bot.callbacks import parse_callback_id
from app.bot.common import admin_booking_markup, booking_text
from app.bot.keyboards import MAIN_MENU
from app.bot.states import AdminSearch
from app.config import get_settings
from app.database.models import Booking, BookingStatus
from app.database.session import session_factory
from app.services.bookings import BookingUnavailable, change_status, get_booking
from app.services.business import business_values
from app.services.export import export_csv
from app.services.statistics import booking_counts, top_services

router = Router()
logger = logging.getLogger(__name__)


def is_admin(user_id: int) -> bool:
    return user_id in get_settings().admins


MENU = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="📅 Сегодня", callback_data="admin:today"),
            InlineKeyboardButton(text="Завтра", callback_data="admin:tomorrow"),
        ],
        [InlineKeyboardButton(text="📋 Ближайшие записи", callback_data="admin:upcoming")],
        [
            InlineKeyboardButton(text="🔍 Найти запись", callback_data="admin:search"),
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats"),
        ],
        [
            InlineKeyboardButton(text="🛠 Услуги", callback_data="admin:services"),
            InlineKeyboardButton(text="📤 Экспорт", callback_data="admin:export"),
        ],
    ]
)


@router.message(Command("admin"))
async def admin(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещён.")
        return
    await state.clear()
    await message.answer("Панель администратора", reply_markup=MENU)


@router.callback_query(F.data.startswith("admin:"))
async def admin_action(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    action = callback.data.split(":", 1)[1]
    if action not in {"search", "today", "tomorrow", "upcoming", "services", "export", "stats"}:
        await callback.answer("Кнопка устарела.", show_alert=True)
        return
    logger.info("Admin %s selected %s", callback.from_user.id, action)
    await callback.answer()
    if action == "services":
        from app.bot.handlers.admin_services import show_service_menu

        await state.clear()
        await show_service_menu(callback.message)
        return
    async with session_factory() as session:
        values = await business_values(session, get_settings())
        today = datetime.now(ZoneInfo(values["timezone"])).date()
        if action == "search":
            await state.set_state(AdminSearch.enter_id)
            await callback.message.answer("Введите номер записи, например 123:")
        elif action in {"today", "tomorrow", "upcoming"}:
            query = (
                select(Booking)
                .options(selectinload(Booking.service))
                .where(Booking.status.in_([BookingStatus.PENDING, BookingStatus.CONFIRMED]))
            )
            if action == "today":
                query = query.where(Booking.booking_date == today)
            elif action == "tomorrow":
                query = query.where(Booking.booking_date == today + timedelta(days=1))
            else:
                query = query.where(Booking.booking_date >= today)
            rows = (
                await session.scalars(
                    query.order_by(Booking.booking_date, Booking.start_time).limit(10)
                )
            ).all()
            await callback.message.answer(
                "Записей нет."
                if not rows
                else "\n\n".join(
                    f"#{b.id} · {b.booking_date:%d.%m} {b.start_time:%H:%M} · "
                    f"{escape(b.service.name)} · {escape(b.car)}"
                    for b in rows
                )
            )
        elif action == "export":
            content = await export_csv(session)
            await callback.message.answer_document(
                BufferedInputFile(content, filename=f"bookings-{today.isoformat()}.csv")
            )
        elif action == "stats":
            counts = await booking_counts(session, today)
            top = await top_services(session, today)
            await callback.message.answer(
                f"📊 Записи\nСегодня: {counts['today']}\nНеделя: {counts['week']}\nМесяц: {counts['month']}\n\n"
                "Топ услуг за месяц:\n"
                + (
                    "\n".join(
                        f"{escape(name)}: {count} · от {revenue:,.0f} ₽"
                        for name, count, revenue in top
                    )
                    or "Пока нет записей"
                )
            )


@router.message(AdminSearch.enter_id)
async def search_id(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        await state.clear()
        await message.answer("Доступ запрещён.")
        return
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Поиск отменён.", reply_markup=MAIN_MENU)
        return
    try:
        booking_id = parse_callback_id((message.text or "").strip().lstrip("#"), "")
    except ValueError:
        await message.answer("Введите числовой номер записи.")
        return
    await state.clear()
    async with session_factory() as session:
        booking = await get_booking(session, booking_id)
    await message.answer(
        booking_text(booking) if booking else "Запись не найдена.",
        reply_markup=admin_booking_markup(booking) if booking else None,
    )


@router.callback_query(F.data.startswith("as:"))
async def admin_status(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    try:
        _, raw_status, raw_id = callback.data.split(":")
        status = BookingStatus(raw_status)
        booking_id = parse_callback_id(raw_id, "")
        if status not in {
            BookingStatus.CONFIRMED,
            BookingStatus.CANCELLED,
            BookingStatus.COMPLETED,
        }:
            raise ValueError
    except ValueError:
        await callback.answer("Кнопка устарела.", show_alert=True)
        return
    async with session_factory() as session:
        values = await business_values(session, get_settings())
        try:
            booking = await change_status(
                session,
                booking_id,
                status,
                now=datetime.now(ZoneInfo(values["timezone"])),
            )
        except BookingUnavailable as exc:
            await callback.answer(str(exc), show_alert=True)
            return
    await callback.answer("Статус обновлён.")
    await callback.message.edit_text(
        booking_text(booking, title="Статус обновлён"),
        reply_markup=admin_booking_markup(booking),
    )
    try:
        await callback.bot.send_message(
            booking.telegram_user_id,
            booking_text(booking, title="Статус вашей записи изменён"),
        )
    except (TelegramAPIError, ClientError, OSError, TimeoutError) as exc:
        logger.error(
            "Customer notification failed for booking %s: %s", booking.id, type(exc).__name__
        )
