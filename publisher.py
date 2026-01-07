# publisher.py
import re
import random
import time
import logging
from typing import Optional
import requests
from aiogram import Bot
from aiogram.types import FSInputFile, URLInputFile
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest, TelegramServerError
from config import (
    DEFAULT_IMAGE_PATH, CAPTION_LIMIT, HF_TOKEN, HF_REWRITE_MODEL, 
    REWRITE_MAX_CHARS, TELEGRAM_RETRY_ATTEMPTS, TELEGRAM_RETRY_DELAY
)

UA = {"User-Agent": "Mozilla/5.0 CryptoAI_Bot/1.0"}
logger = logging.getLogger(__name__)

def strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<.*?>", "", text)
    return " ".join(text.split()).strip()

def remove_urls(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"www\.\S+", "", text)
    return " ".join(text.split()).strip()

def remove_source_refs(text: str) -> str:
    if not text:
        return ""
    patterns = [
        r"по данным\s+\S+", r"источник[:\s]+\S+", r"сообщает\s+\S+",
        r"пишет\s+\S+", r"согласно\s+\S+", r"как сообщает\s+\S+",
        r"reported by\s+\S+", r"according to\s+\S+", r"source[:\s]+\S+",
    ]
    for pattern in patterns:
        text = re.sub(pattern, "", text, flags=re.I)
    return " ".join(text.split()).strip()

def looks_ru(text: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", text or ""))

def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"

def simple_rewrite_ru(title: str, summary: str) -> str:
    title = strip_html(title)
    summary = strip_html(summary)
    title = remove_urls(title)
    summary = remove_urls(summary)
    title = remove_source_refs(title)
    summary = remove_source_refs(summary)
    if looks_ru(title):
        title = re.sub(r'[a-zA-Z]{3,}', '', title)
    if looks_ru(summary):
        summary = re.sub(r'[a-zA-Z]{3,}', '', summary)
    title = " ".join(title.split()).strip()
    summary = " ".join(summary.split()).strip()
    if not title:
        title = "Новость"
    emojis = ["🔥", "💎", "⚡", "🚀", "📊", "💰", "🎯", "⭐"]
    emoji = random.choice(emojis)
    main_text = f"{emoji} {title}"
    if summary:
        if len(summary) > 400:
            summary = summary[:400].rsplit(".", 1)[0] + "."
        main_text += f"\n\n{summary}"
    market_impact = generate_market_impact(title, summary)
    if market_impact:
        main_text += f"\n\n💡 {market_impact}"
    return main_text

def generate_market_impact(title: str, summary: str) -> str:
    text = (title + " " + summary).lower()
    if any(word in text for word in ["рост", "повышение", "подъем", "rally", "bullish", "прибыль"]):
        return "Позитивный сигнал для рынка — возможен рост котировок."
    if any(word in text for word in ["падение", "снижение", "обвал", "crash", "bearish", "убыток"]):
        return "Негативный фактор — возможна коррекция цен."
    if any(word in text for word in ["регулир", "запрет", "ограничение", "санкции", "закон"]):
        return "Регуляторные изменения могут повлиять на волатильность."
    if any(word in text for word in ["обновление", "запуск", "интеграция", "технология", "upgrade"]):
        return "Технологическое развитие — укрепление позиций в долгосрочной перспективе."
    if any(word in text for word in ["инвестиции", "фонд", "институц", "биржа", "listing"]):
        return "Институциональный интерес — сигнал роста доверия к активу."
    return "Рынок наблюдает за развитием событий — возможна повышенная волатильность."

def hf_rewrite_to_ru(title: str, summary: str) -> Optional[str]:
    if not HF_TOKEN:
        return None
    title = remove_urls(strip_html(title))
    summary = remove_urls(strip_html(summary))
    src = title
    if summary:
        src = f"{title}. {summary}" if title else summary
    src = src.strip()
    if not src:
        return None
    src = src[:1400]
    prompt = (
        "Сделай уникальный пересказ на русском языке для Telegram-поста.\n"
        "Правила:\n"
        "1) Только русский язык.\n"
        "2) Без ссылок, без слов 'источник', без названий сайтов.\n"
        "3) Коротко и по делу.\n"
        f"4) Длина до {REWRITE_MAX_CHARS} символов.\n"
        "5) Добавь строку 'Что это значит для рынка' в конце.\n\n"
        f"Текст:\n{src}"
    )
    try:
        api_url = f"https://api-inference.huggingface.co/models/{HF_REWRITE_MODEL}"
        headers = {"Authorization": f"Bearer {HF_TOKEN}"}
        payload = {"inputs": prompt, "parameters": {"max_new_tokens": 300, "temperature": 0.7}}
        r = requests.post(api_url, headers=headers, json=payload, timeout=40)
        if r.status_code != 200:
            return None
        data = r.json()
        out = None
        if isinstance(data, list) and data and isinstance(data[0], dict):
            out = data[0].get("generated_text")
        elif isinstance(data, dict):
            out = data.get("generated_text")
        if not out:
            return None
        out = remove_urls(strip_html(str(out)))
        out = remove_source_refs(out)
        out = re.sub(r"(?is).*?Текст:\s*", "", out).strip()
        if not out or not looks_ru(out):
            return None
        return truncate(out, REWRITE_MAX_CHARS)
    except Exception as e:
        logger.warning(f"HF rewrite failed: {e}")
        return None

async def publish_post_with_retry(
    bot: Bot,
    channel_id: str,
    title: str,
    summary: str,
    image_url: Optional[str],
) -> bool:
    """
    Публикация с retry механизмом.
    Возвращает True если успешно, False если провалилось.
    """
    ru_text = hf_rewrite_to_ru(title, summary)
    if not ru_text:
        ru_text = simple_rewrite_ru(title, summary)
    
    caption = truncate(ru_text, CAPTION_LIMIT)
    
    # Проверка что caption не пустой
    if not caption or len(caption) < 10:
        logger.error("Caption is empty or too short, skipping post")
        return False
    
    if image_url:
        photo = URLInputFile(image_url, headers=UA)
    else:
        photo = FSInputFile(DEFAULT_IMAGE_PATH)
    
    for attempt in range(TELEGRAM_RETRY_ATTEMPTS):
        try:
            await bot.send_photo(chat_id=channel_id, photo=photo, caption=caption)
            logger.info(f"Successfully published post (attempt {attempt + 1})")
            return True
            
        except TelegramRetryAfter as e:
            # Telegram просит подождать
            wait_time = e.retry_after + 5
            logger.warning(f"Rate limit hit, waiting {wait_time}s")
            time.sleep(wait_time)
            
        except TelegramBadRequest as e:
            # Битый запрос (плохая картинка, etc) → не retry
            logger.error(f"Bad request, skipping: {e}")
            return False
            
        except TelegramServerError as e:
            # 502, 503 → retry
            logger.warning(f"Telegram server error (attempt {attempt + 1}): {e}")
            if attempt < TELEGRAM_RETRY_ATTEMPTS - 1:
                time.sleep(TELEGRAM_RETRY_DELAY)
            
        except Exception as e:
            logger.exception(f"Unexpected error publishing post (attempt {attempt + 1}): {e}")
            if attempt < TELEGRAM_RETRY_ATTEMPTS - 1:
                time.sleep(TELEGRAM_RETRY_DELAY)
    
    logger.error(f"Failed to publish post after {TELEGRAM_RETRY_ATTEMPTS} attempts")
    return False

async def publish_post(bot: Bot, channel_id: str, title: str, summary: str, image_url: Optional[str]):
    """Обёртка для обратной совместимости"""
    success = await publish_post_with_retry(bot, channel_id, title, summary, image_url)
    if not success:
        raise Exception("Failed to publish post after retries")
