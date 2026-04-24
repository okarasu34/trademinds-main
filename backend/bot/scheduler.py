from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from loguru import logger
from datetime import datetime
from sqlalchemy import select, update

from db.database import AsyncSessionLocal
from db.models import BotConfig, BotStatus, BotHealthLog, Trade, OrderStatus
from db.redis_client import publish
from data.calendar import calendar_client
from bot.trading_bot import TradingBot

scheduler = AsyncIOScheduler(timezone="UTC")

# TradingBot bir user_id beklediği için, scheduler içinde 
# her botu kendi config'i ile dinamik olarak yöneteceğiz.
# Bu yüzden global bir bot_instance yerine fonksiyon içinde başlatacağız.

async def run_all_bots():
    """Veritabanında RUNNING olan tüm botları tara ve çalıştır."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(BotConfig).where(BotConfig.status == BotStatus.RUNNING)
        )
        active_configs = result.scalars().all()
        
        for config in active_configs:
            try:
                # Her aktif kullanıcı için bir bot motoru oluştur
                bot = TradingBot(user_id=config.user_id)
                await bot._scan_and_execute()
            except Exception as e:
                logger.error(f"Bot execution failed for user {config.user_id}: {e}")

def setup_scheduler():
    # ANA ANALİZ MOTORU: Artık run_all_bots fonksiyonunu çağırıyoruz
    scheduler.add_job(
        run_all_bots,
        IntervalTrigger(seconds=60),
        id="main_trading_engine",
        replace_existing=True,
    )

    scheduler.add_job(health_check_all_bots, IntervalTrigger(seconds=60), id="health")
    scheduler.add_job(refresh_calendar, IntervalTrigger(minutes=5), id="calendar")
    
    scheduler.start()
    logger.info(">>> SCHEDULER DİNAMİK MODDA BAŞLATILDI <<<")

# --- Diğer fonksiyonlar (health_check_all_bots, refresh_calendar vb.) aynen kalsın ---
async def health_check_all_bots():
    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(select(BotConfig).where(BotConfig.status == BotStatus.RUNNING))
            configs = result.scalars().all()
            for config in configs:
                await publish(f"health:{config.user_id}", {"status": "healthy", "checked_at": datetime.utcnow().isoformat()})
        except Exception as e:
            logger.error(f"Health check failed: {e}")

async def refresh_calendar():
    try: await calendar_client.get_calendar(hours_ahead=24)
    except: pass
