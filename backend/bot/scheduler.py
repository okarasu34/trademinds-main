from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger
from datetime import datetime
from sqlalchemy import select

from db.database import AsyncSessionLocal
from db.models import BotConfig, BotStatus
from db.redis_client import publish
from data.calendar import calendar_client

scheduler = AsyncIOScheduler(timezone="UTC")

# Global bot registry — shared with api/bot.py
# Imported lazily to avoid circular imports
_bot_registry: dict = {}


def get_bot_registry() -> dict:
    """Returns the shared bot registry from api/bot.py if available."""
    try:
        from api.bot import _bot_registry as registry
        return registry
    except Exception:
        return _bot_registry


def setup_scheduler():
    scheduler.add_job(
        health_check_all_bots,
        IntervalTrigger(seconds=60),
        id="health",
        replace_existing=True,
    )
    scheduler.add_job(
        refresh_calendar,
        IntervalTrigger(minutes=5),
        id="calendar",
        replace_existing=True,
    )
    scheduler.add_job(
        reset_daily_stats,
        "cron",
        hour=0,
        minute=0,
        id="daily_reset",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(">>> SCHEDULER DİNAMİK MODDA BAŞLATILDI <<<")


async def health_check_all_bots():
    """Publish health status for all running bots."""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(BotConfig).where(BotConfig.status == BotStatus.RUNNING)
            )
            configs = result.scalars().all()
            for config in configs:
                await publish(f"health:{config.user_id}", {
                    "status": "healthy",
                    "checked_at": datetime.utcnow().isoformat(),
                })
    except Exception as e:
        logger.error(f"Health check failed: {e}")


async def refresh_calendar():
    """Refresh economic calendar cache."""
    try:
        await calendar_client.get_calendar(hours_ahead=24)
    except Exception:
        pass


async def reset_daily_stats():
    """Reset daily loss and trade counters at midnight UTC."""
    try:
        from sqlalchemy import update
        from db.models import BotConfig
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(BotConfig).values(
                    daily_loss=0.0,
                    daily_trades=0,
                    daily_reset_at=datetime.utcnow(),
                )
            )
            await db.commit()
            logger.info("Daily stats reset at midnight UTC")
    except Exception as e:
        logger.error(f"Daily reset failed: {e}")