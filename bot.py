# bot.py
import asyncio
import logging
import signal
import sys
from logging.handlers import RotatingFileHandler
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command

from config import (
    BOT_TOKEN, CHANNEL_ID, RSS_FEEDS, MAX_POSTS_PER_DAY, MAX_POSTS_PER_CHECK,
    CHECK_INTERVAL_SECONDS, LOG_FILE, LOG_MAX_BYTES, LOG_BACKUP_COUNT, LOG_LEVEL,
    HEALTHCHECK_PORT, HEALTHCHECK_ENABLED
)
from db import (
    init_db, is_posted, mark_posted, reset_db,
    increment_today_posts, get_today_posts_count, cleanup_old_stats
)
from rss_fetcher import fetch_items
from publisher import publish_post_with_retry

# === ЛОГИРОВАНИЕ ===
def setup_logging():
    handlers = [
        RotatingFileHandler(
            LOG_FILE,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding='utf-8'
        ),
        logging.StreamHandler(sys.stdout)
    ]
    
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers
    )

setup_logging()
logger = logging.getLogger(__name__)

# === ГЛОБАЛЬНОЕ СОСТОЯНИЕ ===
bot = Bot(BOT_TOKEN)
dp = Dispatcher()
shutdown_event = asyncio.Event()
last_check_time = None
last_check_status = "OK"
posts_today = 0

# === HEALTHCHECK ===
async def healthcheck_handler(request):
    """HTTP эндпоинт для проверки статуса"""
    global last_check_time, last_check_status, posts_today
    
    status = {
        "status": "healthy",
        "last_check": str(last_check_time) if last_check_time else "never",
        "last_check_status": last_check_status,
        "posts_today": posts_today,
        "max_posts_per_day": MAX_POSTS_PER_DAY
    }
    return web.json_response(status)

async def start_healthcheck_server():
    """Запуск HTTP сервера для healthcheck"""
    if not HEALTHCHECK_ENABLED:
        return
    
    app = web.Application()
    app.router.add_get('/health', healthcheck_handler)
    app.router.add_get('/healthz', healthcheck_handler)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', HEALTHCHECK_PORT)
    await site.start()
    logger.info(f"Healthcheck server started on port {HEALTHCHECK_PORT}")

# === ОСНОВНАЯ ЛОГИКА ===
async def post_cycle() -> int:
    """Одна проверка RSS и публикация"""
    global last_check_time, last_check_status, posts_today
    
    try:
        last_check_time = asyncio.get_event_loop().time()
        
        today_count = get_today_posts_count()
        posts_today = today_count
        
        if today_count >= MAX_POSTS_PER_DAY:
            logger.info(f"Daily limit reached: {today_count}/{MAX_POSTS_PER_DAY}")
            last_check_status = "LIMIT_REACHED"
            return 0
        
        remaining_today = MAX_POSTS_PER_DAY - today_count
        limit_this_run = min(MAX_POSTS_PER_CHECK, remaining_today)
        
        logger.info(f"Checking RSS (limit: {limit_this_run}, today: {today_count}/{MAX_POSTS_PER_DAY})")
        
        items = fetch_items(RSS_FEEDS, limit_total=30)
        logger.info(f"Found {len(items)} items")
        
        posted_count = 0
        
        for it in items:
            if posted_count >= limit_this_run:
                break
            
            uid = it["uid"]
            if is_posted(uid):
                continue
            
            try:
                logger.info(f"Publishing: {it['title'][:60]}...")
                
                success = await publish_post_with_retry(
                    bot=bot,
                    channel_id=CHANNEL_ID,
                    title=it["title"],
                    summary=it["summary"],
                    image_url=it["image_url"]
                )
                
                if success:
                    mark_posted(uid, it["title"], it["link"])
                    increment_today_posts()
                    posted_count += 1
                    posts_today = get_today_posts_count()
                    logger.info(f"✅ Published ({posted_count}/{limit_this_run})")
                    await asyncio.sleep(2)
                else:
                    logger.warning(f"Failed to publish: {it['title'][:60]}")
                    
            except Exception as e:
                logger.exception(f"Error publishing post: {e}")
        
        if posted_count > 0:
            logger.info(f"✅ Total published: {posted_count} (today: {get_today_posts_count()}/{MAX_POSTS_PER_DAY})")
            last_check_status = "OK"
        else:
            logger.info("No new posts")
            last_check_status = "NO_NEW_POSTS"
        
        return posted_count
        
    except Exception as e:
        logger.exception(f"Critical error in post_cycle: {e}")
        last_check_status = "ERROR"
        return 0

# === КОМАНДЫ БОТА ===
@dp.message(Command("start"))
async def start_cmd(message: Message):
    today = get_today_posts_count()
    await message.answer(
        f"🤖 Бот активен\n\n"
        f"📊 Сегодня: {today}/{MAX_POSTS_PER_DAY}\n\n"
        f"Команды:\n"
        f"/post_now — проверить RSS\n"
        f"/stats — статистика\n"
        f"/reset_db — сброс\n"
        f"/test — тест"
    )

@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    today = get_today_posts_count()
    await message.answer(
        f"📊 Статистика\n\n"
        f"Опубликовано сегодня: {today}/{MAX_POSTS_PER_DAY}\n"
        f"Осталось: {MAX_POSTS_PER_DAY - today}\n"
        f"Последняя проверка: {last_check_status}"
    )

@dp.message(Command("test"))
async def test_cmd(message: Message):
    try:
        await bot.send_message(CHANNEL_ID, "✅ Тест: бот работает")
        await message.answer("✅ Тест отправлен")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

@dp.message(Command("reset_db"))
async def reset_db_cmd(message: Message):
    reset_db()
    await message.answer("✅ База сброшена")

@dp.message(Command("post_now"))
async def post_now_cmd(message: Message):
    await message.answer("⏳ Проверяю RSS...")
    try:
        n = await post_cycle()
        today = get_today_posts_count()
        await message.answer(f"✅ Готово\n\nОпубликовано: {n}\nСегодня всего: {today}/{MAX_POSTS_PER_DAY}")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

# === ПЛАНИРОВЩИК ===
async def scheduler():
    """Автоматическая проверка RSS"""
    while not shutdown_event.is_set():
        try:
            cleanup_old_stats(days_to_keep=30)
            await post_cycle()
        except Exception as e:
            logger.exception(f"Scheduler error: {e}")
        
        logger.info(f"Next check in {CHECK_INTERVAL_SECONDS // 60} minutes")
        
        # Используем wait вместо sleep для быстрой реакции на shutdown
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=CHECK_INTERVAL_SECONDS)
            break  # shutdown_event был установлен
        except asyncio.TimeoutError:
            pass  # таймаут истёк, продолжаем

# === GRACEFUL SHUTDOWN ===
def signal_handler(signum, frame):
    """Обработчик сигналов для graceful shutdown"""
    logger.info(f"Received signal {signum}, initiating shutdown...")
    shutdown_event.set()

async def shutdown():
    """Корректное завершение работы"""
    logger.info("Shutting down bot...")
    
    # Закрываем соединение с Telegram
    await bot.session.close()
    
    # Останавливаем dispatcher
    await dp.stop_polling()
    
    logger.info("Bot stopped")

# === MAIN ===
async def main():
    # Установка обработчиков сигналов
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    logger.info("🚀 Starting bot...")
    
    try:
        # Инициализация БД
        init_db()
        logger.info("✅ Database ready")
        
        # Запуск healthcheck сервера
        await start_healthcheck_server()
        
        # Запуск планировщика
        scheduler_task = asyncio.create_task(scheduler())
        logger.info("✅ Scheduler started")
        
        # Проверка подключения к Telegram
        me = await bot.get_me()
        logger.info(f"✅ Bot connected: @{me.username}")
        logger.info(f"✅ Channel: {CHANNEL_ID}")
        
        # Запуск polling
        logger.info("✅ Starting polling...")
        await dp.start_polling(bot)
        
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
    finally:
        await shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.exception(f"Unhandled exception: {e}")
        sys.exit(1)
