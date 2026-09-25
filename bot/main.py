import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from app.config import get_settings
from bot.handlers.admin import router
from bot.worker import worker


async def main():
    settings = get_settings()
    if not settings.bot_token:
        raise RuntimeError("Заполните BOT_TOKEN перед запуском Telegram-бота.")
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    bot = Bot(settings.bot_token)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)
    stop = asyncio.Event()
    task = asyncio.create_task(worker(bot, stop))
    try:
        # A polling process must be unique per token. Never discard pending updates.
        await bot.delete_webhook(drop_pending_updates=False)
        await dispatcher.start_polling(bot, close_bot_session=False)
    finally:
        stop.set()
        await task
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
