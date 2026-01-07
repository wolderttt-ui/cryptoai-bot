# rss_fetcher.py
import hashlib
import re
import logging
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
import feedparser
import requests
from bs4 import BeautifulSoup
from config import RSS_RETRY_ATTEMPTS, RSS_BACKOFF_TIME, MIN_TITLE_LENGTH, MIN_SUMMARY_LENGTH
from db import mark_source_failed, is_source_available, clear_available_sources

UA = {"User-Agent": "Mozilla/5.0 CryptoAI_Bot/1.0"}
logger = logging.getLogger(__name__)

def clean_url(url: str) -> str:
    if not url:
        return ""
    try:
        parts = urlparse(url)
        q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
        new_query = urlencode(q)
        return urlunparse((parts.scheme, parts.netloc, parts.path, parts.params, new_query, parts.fragment))
    except Exception:
        return url

def strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<.*?>", "", text)
    return " ".join(text.split()).strip()

def make_uid(source: str, link: str, title: str) -> str:
    base = f"{source}|{clean_url(link)}|{title}".strip()
    return hashlib.sha256(base.encode("utf-8")).hexdigest()

def is_valid_item(title: str, summary: str) -> bool:
    """Проверка что новость не пустая"""
    if not title or len(title) < MIN_TITLE_LENGTH:
        return False
    if not summary or len(summary) < MIN_SUMMARY_LENGTH:
        return False
    return True

def try_get_image_from_entry(entry) -> str:
    for key in ("media_content", "media_thumbnail"):
        if key in entry:
            arr = entry.get(key) or []
            if arr and isinstance(arr, list):
                url = arr[0].get("url")
                if url:
                    return url
    enclosures = entry.get("enclosures") or []
    if enclosures and isinstance(enclosures, list):
        for e in enclosures:
            href = e.get("href")
            if href and any(href.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
                return href
    return ""

def try_get_og_image(link: str) -> str:
    if not link:
        return ""
    try:
        r = requests.get(link, headers=UA, timeout=10)
        if r.status_code != 200:
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        tag = soup.find("meta", property="og:image")
        if tag and tag.get("content"):
            return tag["content"].strip()
        tag = soup.find("meta", attrs={"name": "twitter:image"})
        if tag and tag.get("content"):
            return tag["content"].strip()
        return ""
    except Exception as e:
        logger.warning(f"Failed to get og:image from {link}: {e}")
        return ""

def fetch_single_feed(feed_url: str, limit_total: int) -> list[dict]:
    """Получить новости из одного RSS с retry"""
    items = []
    
    # Проверяем не "забанен" ли источник
    if not is_source_available(feed_url):
        logger.info(f"Source {feed_url} is temporarily unavailable, skipping")
        return []
    
    for attempt in range(RSS_RETRY_ATTEMPTS):
        try:
            logger.debug(f"Fetching {feed_url} (attempt {attempt + 1}/{RSS_RETRY_ATTEMPTS})")
            d = feedparser.parse(feed_url)
            
            if d.bozo:  # feedparser нашел ошибку
                raise Exception(f"Feed parsing error: {d.bozo_exception}")
            
            source = (d.feed.get("title") or feed_url).strip()
            
            for entry in d.entries[:limit_total]:
                title = strip_html(entry.get("title", "")).strip()
                summary = strip_html(entry.get("summary", "") or entry.get("description", "")).strip()
                link = clean_url(entry.get("link", "")).strip()
                
                if not is_valid_item(title, summary):
                    logger.debug(f"Skipping invalid item: {title[:50]}")
                    continue
                
                image_url = try_get_image_from_entry(entry)
                if not image_url:
                    image_url = try_get_og_image(link)
                
                uid = make_uid(source, link, title)
                
                items.append({
                    "uid": uid,
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "source": source,
                    "image_url": image_url,
                })
            
            logger.info(f"Successfully fetched {len(items)} items from {feed_url}")
            return items
            
        except Exception as e:
            logger.warning(f"Failed to fetch {feed_url} (attempt {attempt + 1}): {e}")
            if attempt == RSS_RETRY_ATTEMPTS - 1:
                # Последняя попытка провалилась → "банить" источник
                logger.error(f"All attempts failed for {feed_url}, marking as unavailable for {RSS_BACKOFF_TIME}s")
                mark_source_failed(feed_url, RSS_BACKOFF_TIME)
    
    return []

def fetch_items(feed_urls: list[str], limit_total: int = 20) -> list[dict]:
    """Получить новости из всех RSS"""
    clear_available_sources()  # Разбанить источники, у которых прошло время
    
    all_items = []
    for url in feed_urls:
        items = fetch_single_feed(url, limit_total)
        all_items.extend(items)
    
    # Убираем дубли
    uniq = {}
    for it in all_items:
        uniq[it["uid"]] = it
    
    logger.info(f"Total unique items: {len(uniq)}")
    return list(uniq.values())
