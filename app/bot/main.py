import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import ErrorEvent

from app.bot.handlers import admin, admin_services, booking, profile, start
from app.config import get_settings, validate_runtime
from app.database.session import engine
from app.logging_config import configure_logging
from app.services.reminders import reminder_loop

logger = logging.getLogger(__name__)


async def main() -> None:
    configure_logging()
    settings = get_settings()
    validate_runtime(settings, bot=True)
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(start.router)
    dp.include_router(admin.router)
    dp.include_router(admin_services.router)
    dp.include_router(booking.router)
    dp.include_router(profile.router)

    @dp.errors()
    async def handle_error(event: ErrorEvent) -> bool:
        logger.error("Bot update failed: %s", type(event.exception).__name__)
        try:
            if event.update.message:
                await event.update.message.answer("Произошла ошибка. Попробуйте позже.")
            elif event.update.callback_query:
                await event.update.callback_query.answer(
                    "Произошла ошибка. Попробуйте позже.", show_alert=True
                )
        except Exception:
            logger.exception("Could not notify user of error")
        return True

    reminder_task = asyncio.create_task(reminder_loop(bot, settings))
    logger.info("Telegram bot starting")
    try:
        await dp.start_polling(bot, handle_signals=True)
    finally:
        reminder_task.cancel()
        await asyncio.gather(reminder_task, return_exceptions=True)
        await bot.session.close()
        await engine.dispose()
        logger.info("Telegram bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
