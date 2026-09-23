from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.callbacks import parse_callback_id
from app.bot.handlers.admin import is_admin
from app.bot.keyboards import CANCEL_MENU, MAIN_MENU
from app.bot.states import AdminServiceFlow
from app.database.models import Service
from app.database.session import session_factory
from app.services.catalog import (
    CatalogError,
    add_service,
    all_services,
    set_service_active,
    set_service_duration,
    set_service_price,
    validate_description,
    validate_duration,
    validate_name,
    validate_price,
)

router = Router()


def service_markup(service: Service) -> InlineKeyboardMarkup:
    toggle_label = "Выключить" if service.is_active else "Включить"
    toggle_action = "disable" if service.is_active else "enable"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=toggle_label, callback_data=f"svc:{toggle_action}:{service.id}"
                ),
                InlineKeyboardButton(text="Цена", callback_data=f"svc:price:{service.id}"),
                InlineKeyboardButton(
                    text="Длительность", callback_data=f"svc:duration:{service.id}"
                ),
            ],
        ]
    )


async def show_service_menu(message: Message) -> None:
    async with session_factory() as session:
        rows = await all_services(session)
    await message.answer("🛠 Услуги. Выберите действие для нужной услуги:")
    for service in rows:
        status = "включена" if service.is_active else "выключена"
        await message.answer(
            f"#{service.id} · <b>{escape(service.name)}</b> ({status})\n"
            f"от {service.price_from:,.0f} ₽ · {service.duration_minutes} мин.",
            reply_markup=service_markup(service),
        )
    await message.answer(
        "Добавить новую услугу:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➕ Добавить услугу", callback_data="svc:add")],
            ]
        ),
    )


@router.callback_query(F.data.startswith("svc:"))
async def service_action(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    parts = callback.data.split(":")
    if parts == ["svc", "add"]:
        await state.clear()
        await state.set_state(AdminServiceFlow.add_name)
        await callback.answer()
        await callback.message.answer(
            "Введите название новой услуги (2–120 символов):", reply_markup=CANCEL_MENU
        )
        return
    if len(parts) != 3 or parts[1] not in {"enable", "disable", "price", "duration"}:
        await callback.answer("Кнопка устарела.", show_alert=True)
        return
    try:
        service_id = parse_callback_id(parts[2], "", 2147483647)
    except ValueError:
        await callback.answer("Кнопка устарела.", show_alert=True)
        return
    async with session_factory() as session:
        service = await session.get(Service, service_id)
        if service is None:
            await callback.answer("Услуга больше не существует.", show_alert=True)
            return
        if parts[1] in {"enable", "disable"}:
            try:
                service = await set_service_active(
                    session,
                    service_id,
                    parts[1] == "enable",
                    callback.from_user.id,
                )
            except CatalogError as exc:
                await callback.answer(str(exc), show_alert=True)
                return
    await state.clear()
    await callback.answer()
    if parts[1] in {"enable", "disable"}:
        status = "включена" if service.is_active else "выключена"
        await callback.message.answer(
            f"Услуга «{escape(service.name)}» {status}.", reply_markup=service_markup(service)
        )
        return
    await state.update_data(service_id=service_id)
    if parts[1] == "price":
        await state.set_state(AdminServiceFlow.price)
        await callback.message.answer(
            f"Новая цена «от» для «{escape(service.name)}» в рублях:", reply_markup=CANCEL_MENU
        )
    else:
        await state.set_state(AdminServiceFlow.duration)
        await callback.message.answer(
            f"Новая длительность «{escape(service.name)}» в минутах:", reply_markup=CANCEL_MENU
        )


async def check_message(message: Message, state: FSMContext) -> bool:
    if not is_admin(message.from_user.id):
        await state.clear()
        await message.answer("Доступ запрещён.")
        return False
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Изменение отменено.", reply_markup=MAIN_MENU)
        return False
    return True


async def finished(message: Message, state: FSMContext, service: Service) -> None:
    await state.clear()
    await message.answer(
        f"Сохранено: {escape(service.name)} — от {service.price_from:,.0f} ₽, "
        f"{service.duration_minutes} мин.",
        reply_markup=MAIN_MENU,
    )


@router.message(AdminServiceFlow.price)
async def edit_price(message: Message, state: FSMContext) -> None:
    if not await check_message(message, state):
        return
    data = await state.get_data()
    try:
        async with session_factory() as session:
            service = await set_service_price(
                session,
                data["service_id"],
                message.text or "",
                message.from_user.id,
            )
    except CatalogError as exc:
        await message.answer(str(exc))
        return
    await finished(message, state, service)


@router.message(AdminServiceFlow.duration)
async def edit_duration(message: Message, state: FSMContext) -> None:
    if not await check_message(message, state):
        return
    data = await state.get_data()
    try:
        async with session_factory() as session:
            service = await set_service_duration(
                session,
                data["service_id"],
                message.text or "",
                message.from_user.id,
            )
    except CatalogError as exc:
        await message.answer(str(exc))
        return
    await finished(message, state, service)


@router.message(AdminServiceFlow.add_name)
async def add_name(message: Message, state: FSMContext) -> None:
    if not await check_message(message, state):
        return
    try:
        name = validate_name(message.text or "")
    except CatalogError as exc:
        await message.answer(str(exc))
        return
    await state.update_data(name=name)
    await state.set_state(AdminServiceFlow.add_description)
    await message.answer("Введите описание (до 500 символов) или отправьте «-» без описания:")


@router.message(AdminServiceFlow.add_description)
async def add_description(message: Message, state: FSMContext) -> None:
    if not await check_message(message, state):
        return
    try:
        description = validate_description("" if message.text == "-" else message.text or "")
    except CatalogError as exc:
        await message.answer(str(exc))
        return
    await state.update_data(description=description)
    await state.set_state(AdminServiceFlow.add_price)
    await message.answer("Введите начальную цену в рублях:")


@router.message(AdminServiceFlow.add_price)
async def add_price(message: Message, state: FSMContext) -> None:
    if not await check_message(message, state):
        return
    try:
        price = validate_price(message.text or "")
    except CatalogError as exc:
        await message.answer(str(exc))
        return
    await state.update_data(price=str(price))
    await state.set_state(AdminServiceFlow.add_duration)
    await message.answer("Введите длительность в минутах:")


@router.message(AdminServiceFlow.add_duration)
async def add_duration(message: Message, state: FSMContext) -> None:
    if not await check_message(message, state):
        return
    try:
        validate_duration(message.text or "")
        data = await state.get_data()
        async with session_factory() as session:
            service = await add_service(
                session,
                name=data["name"],
                description=data["description"],
                raw_price=data["price"],
                raw_duration=message.text or "",
                admin_id=message.from_user.id,
            )
    except CatalogError as exc:
        await message.answer(str(exc))
        return
    await finished(message, state, service)
