from datetime import date, datetime, time, timedelta
from html import escape
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)

from app.bot.callbacks import parse_callback_id
from app.bot.common import booking_text, notify_admins
from app.bot.keyboards import CANCEL_MENU, MAIN_MENU, PHONE_MENU
from app.bot.states import BookingFlow
from app.config import get_settings
from app.database.session import session_factory
from app.services.bookings import (
    BookingUnavailable,
    SlotUnavailable,
    create_booking,
    get_service,
    list_services,
    normalize_phone,
    slots_for_service,
)
from app.services.business import business_values, working_hours

router = Router()
WEEKDAYS_RU = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def buttons(items: list[tuple[str, str]], width: int = 2) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text=label, callback_data=data)
            for label, data in items[i : i + width]
        ]
        for i in range(0, len(items), width)
    ]
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="bcancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text == "❌ Отмена")
async def cancel_message(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=MAIN_MENU)


@router.callback_query(F.data == "bcancel")
async def cancel_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await callback.message.answer("Запись отменена.", reply_markup=MAIN_MENU)


@router.message(F.text == "🚘 Записаться")
async def begin(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with session_factory() as session:
        services = await list_services(session)
    if not services:
        await message.answer("Сейчас нет доступных услуг.")
        return
    await state.set_state(BookingFlow.select_service)
    await message.answer(
        "Выберите услугу:",
        reply_markup=buttons([(s.name[:60], f"bs:{s.id}") for s in services], width=1),
    )


@router.callback_query(BookingFlow.select_service, F.data.startswith("bs:"))
async def select_service(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        service_id = parse_callback_id(callback.data, "bs:", 2147483647)
    except ValueError:
        await callback.answer("Кнопка устарела.", show_alert=True)
        return
    async with session_factory() as session:
        service = await get_service(session, service_id)
    if service is None:
        await callback.answer("Услуга больше недоступна.", show_alert=True)
        return
    await state.update_data(service_id=service_id)
    await state.set_state(BookingFlow.enter_car)
    await callback.answer()
    await callback.message.answer(
        "Введите марку и модель автомобиля. Например: BMW 320d", reply_markup=CANCEL_MENU
    )


@router.message(BookingFlow.enter_car)
async def enter_car(message: Message, state: FSMContext) -> None:
    car = (message.text or "").strip()
    if not 2 <= len(car) <= 120:
        await message.answer("Введите марку и модель автомобиля текстом (2–120 символов).")
        return
    await state.update_data(car=car)
    async with session_factory() as session:
        values = await business_values(session, get_settings())
    _, _, weekdays, _ = working_hours(values)
    today = datetime.now(ZoneInfo(values["timezone"])).date()
    dates = [today + timedelta(days=i) for i in range(14)]
    dates = [day for day in dates if day.weekday() in weekdays]
    await state.set_state(BookingFlow.select_date)
    await message.answer(
        "Выберите дату:",
        reply_markup=buttons(
            [
                (f"{day:%d.%m} ({WEEKDAYS_RU[day.weekday()]})", f"bd:{day.isoformat()}")
                for day in dates
            ]
        ),
    )


@router.callback_query(BookingFlow.select_date, F.data.startswith("bd:"))
async def select_date(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        day = date.fromisoformat(callback.data[3:])
    except ValueError:
        await callback.answer("Кнопка устарела.", show_alert=True)
        return
    data = await state.get_data()
    async with session_factory() as session:
        values = await business_values(session, get_settings())
        start, close, days, step = working_hours(values)
        now = datetime.now(ZoneInfo(values["timezone"]))
        if not now.date() <= day < now.date() + timedelta(days=14):
            await callback.answer("Дата больше недоступна.", show_alert=True)
            return
        service = await get_service(session, data["service_id"])
        if service is None:
            await callback.answer("Услуга больше недоступна.", show_alert=True)
            return
        slots = await slots_for_service(session, service, day, start, close, days, step, now)
    if not slots:
        await callback.answer("На эту дату свободного времени нет.", show_alert=True)
        return
    await state.update_data(day=day.isoformat())
    await state.set_state(BookingFlow.select_time)
    await callback.answer()
    await callback.message.answer(
        "Выберите время:",
        reply_markup=buttons(
            [(slot.strftime("%H:%M"), f"bt:{slot:%H%M}") for slot in slots], width=3
        ),
    )


@router.callback_query(BookingFlow.select_time, F.data.startswith("bt:"))
async def select_time(callback: CallbackQuery, state: FSMContext) -> None:
    raw = callback.data[3:]
    if (
        len(raw) != 4
        or not raw.isascii()
        or not raw.isdecimal()
        or int(raw[:2]) > 23
        or int(raw[2:]) > 59
    ):
        await callback.answer("Кнопка устарела.", show_alert=True)
        return
    data = await state.get_data()
    async with session_factory() as session:
        values = await business_values(session, get_settings())
        start, close, days, step = working_hours(values)
        service = await get_service(session, data["service_id"])
        if service is None:
            await callback.answer("Услуга больше недоступна.", show_alert=True)
            return
        now = datetime.now(ZoneInfo(values["timezone"]))
        slots = await slots_for_service(
            session, service, date.fromisoformat(data["day"]), start, close, days, step, now
        )
    selected = time.fromisoformat(f"{raw[:2]}:{raw[2:]}")
    if selected not in slots:
        await callback.answer("Время уже недоступно. Выберите другое.", show_alert=True)
        return
    await state.update_data(start=selected.strftime("%H:%M"))
    await state.set_state(BookingFlow.enter_phone)
    await callback.answer()
    await callback.message.answer(
        "Отправьте номер кнопкой или введите вручную (+79991234567).", reply_markup=PHONE_MENU
    )


@router.message(BookingFlow.enter_phone)
async def enter_phone(message: Message, state: FSMContext) -> None:
    if message.contact and message.contact.user_id != message.from_user.id:
        await message.answer("Отправьте свой номер или введите его вручную.")
        return
    try:
        phone = normalize_phone(
            message.contact.phone_number if message.contact else (message.text or "")
        )
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await state.update_data(phone=phone)
    await state.set_state(BookingFlow.enter_comment)
    await message.answer("Номер сохранён.", reply_markup=ReplyKeyboardRemove())
    await message.answer(
        "Добавьте комментарий или пропустите.",
        reply_markup=buttons([("💬 Добавить комментарий", "bcomment"), ("Пропустить", "bskip")]),
    )


@router.callback_query(BookingFlow.enter_comment, F.data == "bcomment")
async def ask_comment(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        "Напишите комментарий (до 500 символов).", reply_markup=CANCEL_MENU
    )


@router.callback_query(BookingFlow.enter_comment, F.data == "bskip")
async def skip_comment(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await show_summary(callback.message, state, None)


@router.message(BookingFlow.enter_comment)
async def enter_comment(message: Message, state: FSMContext) -> None:
    comment = (message.text or "").strip()
    if not 1 <= len(comment) <= 500:
        await message.answer("Комментарий должен содержать от 1 до 500 символов.")
        return
    await show_summary(message, state, comment)


async def show_summary(message: Message, state: FSMContext, comment: str | None) -> None:
    await state.update_data(comment=comment)
    data = await state.get_data()
    async with session_factory() as session:
        service = await get_service(session, data["service_id"])
    if service is None:
        await state.clear()
        await message.answer("Услуга больше недоступна. Начните заново.", reply_markup=MAIN_MENU)
        return
    await state.set_state(BookingFlow.confirm)
    await message.answer(
        f"<b>Проверьте запись</b>\n\n🚘 {escape(data['car'])}\n"
        f"🛠 {escape(service.name)}\n💰 от {service.price_from:,.0f} ₽\n"
        f"📅 {date.fromisoformat(data['day']):%d.%m.%Y}\n🕒 {data['start']}\n"
        f"📱 {escape(data['phone'])}" + (f"\n💬 {escape(comment)}" if comment else ""),
        reply_markup=buttons([("✅ Подтвердить", "bconfirm")], width=1),
    )


@router.callback_query(BookingFlow.confirm, F.data == "bconfirm")
async def confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await callback.answer()
    async with session_factory() as session:
        values = await business_values(session, get_settings())
        start, close, days, step = working_hours(values)
        try:
            booking = await create_booking(
                session,
                user_id=callback.from_user.id,
                username=callback.from_user.username,
                customer_name=callback.from_user.full_name,
                phone=data["phone"],
                car=data["car"],
                service_id=data["service_id"],
                day=date.fromisoformat(data["day"]),
                start_at=time.fromisoformat(data["start"]),
                comment=data.get("comment"),
                work_start=start,
                work_end=close,
                work_days=days,
                step=step,
                now=datetime.now(ZoneInfo(values["timezone"])),
            )
        except (SlotUnavailable, BookingUnavailable) as exc:
            await state.clear()
            await callback.message.answer(f"{exc} Начните запись заново.", reply_markup=MAIN_MENU)
            return
    await state.clear()
    await callback.message.answer(
        booking_text(booking, title="✅ Запись создана"), reply_markup=MAIN_MENU
    )
    await notify_admins(
        callback.bot, get_settings(), booking_text(booking, title="🔥 НОВАЯ ЗАПИСЬ"), booking.id
    )


@router.callback_query(F.data.regexp(r"^(bs:|bd:|bt:|bconfirm$|bcomment$|bskip$)"))
async def stale_booking_button(callback: CallbackQuery) -> None:
    await callback.answer("Кнопка устарела. Начните запись заново.", show_alert=True)


@router.message(BookingFlow.select_service)
@router.message(BookingFlow.select_date)
@router.message(BookingFlow.select_time)
@router.message(BookingFlow.confirm)
async def use_buttons(message: Message) -> None:
    await message.answer("Пожалуйста, используйте кнопки выше или нажмите «❌ Отмена».")
