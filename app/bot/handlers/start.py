from html import escape

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.keyboards import MAIN_MENU
from app.config import get_settings
from app.database.session import session_factory
from app.services.bookings import list_services
from app.services.business import business_values

router = Router()


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with session_factory() as session:
        values = await business_values(session, get_settings())
    await message.answer(
        f"🚘 Добро пожаловать в {escape(values['business_name'])}!\n\n"
        "Здесь можно посмотреть услуги, выбрать время и управлять записями.\n"
        "Выберите действие 👇",
        reply_markup=MAIN_MENU,
    )


@router.message(F.text == "📋 Услуги и цены")
async def services(message: Message) -> None:
    async with session_factory() as session:
        items = await list_services(session)
    if not items:
        await message.answer("Сейчас нет доступных услуг.")
        return
    chunks = ["<b>Услуги и цены</b>"]
    current = chunks.pop()
    for item in items:
        entry = (
            f"<b>{escape(item.name)}</b> — от {item.price_from:,.0f} ₽\n"
            f"{escape(item.description[:500])}\n⏱ {item.duration_minutes} мин."
        )
        if len(current) + len(entry) > 3000:
            chunks.append(current)
            current = entry
        else:
            current += "\n\n" + entry
    chunks.append(current)
    for chunk in chunks:
        await message.answer(chunk)


@router.message(F.text == "📍 Адрес")
async def address(message: Message) -> None:
    async with session_factory() as session:
        values = await business_values(session, get_settings())
    await message.answer(f"📍 {escape(values['business_address']) or 'Адрес уточняется'}")


@router.message(F.text == "☎️ Контакты")
async def contacts(message: Message) -> None:
    async with session_factory() as session:
        values = await business_values(session, get_settings())
    await message.answer(
        f"☎️ Телефон: {escape(values['business_phone'])}\n"
        f"Telegram: {escape(values['business_telegram'])}"
    )
