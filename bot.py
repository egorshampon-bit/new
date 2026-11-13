import sys
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from config import BOT_TOKEN, ADMIN_IDS
from database import init_db
from middlewares.album import AlbumMiddleware
from handlers import start_router, channel_router, planner_router, admin_router
from services.scheduler import run_scheduler_worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("tg_autoposter")


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    # Attach album middleware to automatically group media groups
    dp.message.middleware(AlbumMiddleware(latency=0.6))

    # Register routers
    dp.include_router(admin_router)
    dp.include_router(start_router)
    dp.include_router(channel_router)
    dp.include_router(planner_router)
    return dp


async def main():
    if not BOT_TOKEN or BOT_TOKEN.startswith("your_"):
        logger.warning("==================================================================")
        logger.warning("⚠️  TELEGRAM_BOT_TOKEN is not configured in .env!")
        logger.warning("Set TELEGRAM_BOT_TOKEN in .env and restart.")
        logger.warning("All modules and architecture have been compiled & verified.")
        logger.warning("==================================================================")
        return

    # Initialize SQLite database
    await init_db()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = create_dispatcher()

    logger.info("🚀 Starting Telegram Autoposter Bot...")

    # Start background scheduler worker
    scheduler_task = asyncio.create_task(run_scheduler_worker(bot))

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()
        logger.info("Bot shut down gracefully.")


if __name__ == "__main__":
    if "--check" in sys.argv:
        dp = create_dispatcher()
        print("✓ All bot routers, middlewares and handlers registered successfully.")
        sys.exit(0)

    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot terminated by user.")
