# config.py

# === TELEGRAM ===
BOT_TOKEN = "8579566583:AAFMaacwVjnGMhQu0xnAMaZVZz6uDDUP-QM"
CHANNEL_ID = "@aicriptoai"

# === RSS ИСТОЧНИКИ ===
RSS_FEEDS = [
    "https://bits.media/rss/",
    "https://forklog.com/feed/",
    "https://cointelegraph.com/rss",
]

# === ЛИМИТЫ ===
MAX_POSTS_PER_DAY = 10
MAX_POSTS_PER_CHECK = 2
CHECK_INTERVAL_SECONDS = 3600  # 1 час

# === RETRY НАСТРОЙКИ ===
TELEGRAM_RETRY_ATTEMPTS = 3  # сколько попыток при ошибке Telegram
TELEGRAM_RETRY_DELAY = 30    # пауза между попытками (секунды)
RSS_RETRY_ATTEMPTS = 2       # сколько попыток при ошибке RSS
RSS_BACKOFF_TIME = 300       # на сколько "забанить" источник при ошибке

# === МЕДИА ===
DEFAULT_IMAGE_PATH = "media/default.jpg"
CAPTION_LIMIT = 1000
MIN_TITLE_LENGTH = 10        # минимальная длина заголовка
MIN_SUMMARY_LENGTH = 20      # минимальная длина описания

# === ЛОГИРОВАНИЕ ===
LOG_FILE = "bot.log"
LOG_MAX_BYTES = 50 * 1024 * 1024  # 50 MB
LOG_BACKUP_COUNT = 3              # хранить 3 старых файла
LOG_LEVEL = "INFO"                # DEBUG, INFO, WARNING, ERROR

# === HEALTHCHECK ===
HEALTHCHECK_PORT = 8080      # HTTP порт для проверки статуса
HEALTHCHECK_ENABLED = True

# === AI (НЕОБЯЗАТЕЛЬНО) ===
HF_TOKEN = ""
HF_REWRITE_MODEL = "google/flan-t5-large"
REWRITE_MAX_CHARS = 750
