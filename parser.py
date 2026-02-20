#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Парсер контента с сайтов газеты"""

import logging
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from config import (
    SITE_URL,
    OLD_SITE_URL,
    USER_AGENT,
    REQUEST_TIMEOUT,
    ARTICLES_LIMIT_SEARCH,
    OKOLICA_HTML_PAGES,
)

logger = logging.getLogger(__name__)


def _make_request(url: str, params: dict = None, retries: int = 2) -> requests.Response:
    """Выполнение HTTP-запроса с общими настройками и повтором при 5xx."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }
    last_error = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 503 and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            return resp
        except requests.RequestException as e:
            last_error = e
            if attempt < retries:
                time.sleep(1.0)
    raise last_error


def get_latest_articles(limit: int = 10) -> list[dict]:
    """Получение последних статей с sibokolica.ru."""
    try:
        response = _make_request(SITE_URL)
        response.encoding = "utf-8"
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        articles = []

        for h2 in soup.find_all("h2"):
            link = h2.find("a")
            if not link or not link.get("href") or ".html" not in link.get("href", ""):
                continue

            href = link.get("href", "")
            if not href.startswith("http"):
                href = SITE_URL + (href if href.startswith("/") else "/" + href)

            title = h2.get_text(strip=True)
            if not title:
                continue

            summary = _extract_summary(h2, title, max_len=200)

            articles.append({"title": title, "url": href, "summary": summary})

            if len(articles) >= limit:
                break

        return articles

    except Exception as e:
        logger.error("Ошибка парсинга sibokolica.ru: %s", e)
        return []


def _extract_summary(anchor_element, exclude_text: str, max_len: int = 200) -> str:
    """Извлечение краткого описания из родительских элементов."""
    parent = anchor_element.parent
    for _ in range(5):
        if not parent:
            break
        for elem in parent.find_all(["p", "div"]):
            txt = elem.get_text(strip=True)
            if (
                txt
                and txt != exclude_text
                and 30 < len(txt) < 300
                and not txt.startswith("http")
                and elem != anchor_element
            ):
                return txt[:max_len] + ("..." if len(txt) > max_len else "")
        parent = parent.parent
    return ""


def _elem_text(el) -> str:
    """Извлечение текста из XML-элемента (включая CDATA)."""
    if el is None:
        return ""
    return "".join(el.itertext()).strip()


def _fetch_okolica_rss() -> list[dict]:
    """
    Загрузка статей из RSS okolica.net.
    RSS содержит title, description, fulltext — идеально для поиска.
    """
    try:
        url = f"{OLD_SITE_URL}/news/rss.xml"
        response = _make_request(url)
        response.raise_for_status()
        try:
            text = response.content.decode("cp1251")
        except UnicodeDecodeError:
            text = response.content.decode("utf-8", errors="replace")
        root = ET.fromstring(text)
        items = root.findall(".//item")

        articles = []
        for item in items:
            link_el = item.find("link")
            if link_el is None or not link_el.text or "/news/" not in link_el.text:
                continue

            url_str = link_el.text.strip()
            if not url_str.startswith("http"):
                url_str = f"{OLD_SITE_URL}{url_str}" if url_str.startswith("/") else url_str
            url_str = url_str.replace("http://", "https://", 1)

            title_el = item.find("title")
            title = _elem_text(title_el)
            if not title:
                continue

            desc_el = item.find("description")
            summary = _elem_text(desc_el)[:200] if desc_el is not None else ""

            full_el = item.find("fulltext")
            fulltext = _elem_text(full_el) if full_el is not None else ""

            articles.append({
                "title": title,
                "url": url_str,
                "summary": summary,
                "_fulltext": fulltext,
            })

        return articles

    except ET.ParseError as e:
        logger.error("Ошибка парсинга RSS okolica.net: %s", e)
        return []
    except Exception as e:
        logger.error("Ошибка загрузки RSS okolica.net: %s", e)
        return []


def _fetch_okolica_html(max_pages: int = None) -> list[dict]:
    """
    Загрузка статей со страниц HTML okolica.net (расширение пула для поиска).
    Сайт в cp1251, парсим ссылки из ленты новостей.
    """
    max_pages = max_pages or OKOLICA_HTML_PAGES
    all_articles = []
    seen_urls = set()

    for page in range(1, max_pages + 1):
        try:
            url = f"{OLD_SITE_URL}/news/" + (f"?page={page}" if page > 1 else "")
            response = _make_request(url)
            response.raise_for_status()
            try:
                text = response.content.decode("cp1251")
            except UnicodeDecodeError:
                text = response.content.decode("utf-8", errors="replace")
            soup = BeautifulSoup(text, "html.parser")

            for a in soup.find_all("a", href=True):
                href = a.get("href", "")
                if "/news/" not in href or ".html" not in href or "rss" in href:
                    continue
                if "/top.html" in href or "/last.html" in href:
                    continue
                if not re.search(r"/news/[^/]+/\d+\.html", href):
                    continue
                if href in seen_urls:
                    continue

                full_url = OLD_SITE_URL + href if not href.startswith("http") else href
                title = a.get_text(strip=True)
                title = re.sub(r"\s*\[\.\.\.\]\s*$", "", title)
                if not title or len(title) < 5:
                    continue

                seen_urls.add(href)
                full_url_https = full_url.replace("http://", "https://", 1)
                all_articles.append({
                    "title": title,
                    "url": full_url_https,
                    "summary": "",
                    "_fulltext": "",
                })

        except Exception as e:
            logger.warning("Ошибка HTML okolica.net page %s: %s", page, e)
            break

    return all_articles


def _merge_okolica_sources(rss_articles: list[dict], html_articles: list[dict]) -> list[dict]:
    """
    Объединяет RSS и HTML: RSS даёт fulltext/summary (~10 статей), HTML — широкий охват (сотни).
    При дубликате по URL приоритет у RSS.
    """
    by_url: dict[str, dict] = {}
    for a in rss_articles:
        url = a["url"].replace("http://", "https://", 1).rstrip("/")
        by_url[url] = a
    for a in html_articles:
        url = a["url"].replace("http://", "https://", 1).rstrip("/")
        if url not in by_url:
            by_url[url] = a
    return list(by_url.values())


def search_okolica_only(query: str, limit: int = None) -> list[dict]:
    """
    Поиск на okolica.net по ключевым словам.
    Всегда объединяет RSS (title, description, fulltext ~10 шт.) и HTML (много страниц).
    """
    limit = limit or ARTICLES_LIMIT_SEARCH

    try:
        rss_articles = _fetch_okolica_rss()
        html_articles = _fetch_okolica_html()
        articles = _merge_okolica_sources(rss_articles, html_articles)

        if not articles:
            return []

        query_words = [w.strip().lower() for w in query.split() if w.strip()]
        if not query_words:
            return [
                {"title": a["title"], "url": a["url"], "summary": a.get("summary", "")}
                for a in articles[:limit]
            ]

        matched = []
        for a in articles:
            searchable = f"{a['title']} {a.get('summary', '')} {a.get('_fulltext', '')}".lower()
            if all(w in searchable for w in query_words):
                matched.append({"title": a["title"], "url": a["url"], "summary": a.get("summary", "")})
                if len(matched) >= limit:
                    break

        if not matched:
            for a in articles:
                searchable = f"{a['title']} {a.get('summary', '')} {a.get('_fulltext', '')}".lower()
                if any(w in searchable for w in query_words):
                    matched.append({"title": a["title"], "url": a["url"], "summary": a.get("summary", "")})
                    if len(matched) >= limit:
                        break

        return matched

    except Exception as e:
        logger.error("Ошибка поиска okolica.net: %s", e)
        return []


def search_okolica_old(query: str, limit: int = None) -> list[dict]:
    """
    Поиск по ключевым словам на okolica.net.
    При отсутствии результатов — fallback на sibokolica.ru.
    """
    articles = search_okolica_only(query, limit)
    if not articles:
        articles = _search_sibokolica(query, limit or ARTICLES_LIMIT_SEARCH)
    return articles


def _search_sibokolica(query: str, limit: int) -> list[dict]:
    """Поиск на sibokolica.ru."""
    try:
        encoded = quote(query, safe="")
        url = f"{SITE_URL}/index.php?do=search&subaction=search&story={encoded}"
        response = _make_request(url)
        response.encoding = "utf-8"
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        articles = []

        for h2 in soup.find_all("h2"):
            link = h2.find("a")
            if not link or not link.get("href") or ".html" not in link.get("href", ""):
                continue

            href = link.get("href", "")
            if not href.startswith("http"):
                href = SITE_URL + (href if href.startswith("/") else "/" + href)

            title = h2.get_text(strip=True)
            summary = _extract_summary(h2, title, max_len=150)

            articles.append({"title": title, "url": href, "summary": summary})
            if len(articles) >= limit:
                break

        return articles

    except Exception as e:
        logger.error("Ошибка поиска sibokolica.ru: %s", e)
        return []


# WMO коды погоды Open-Meteo → описание на русском
_WEATHER_CODE_RU = {
    0: "ясно",
    1: "преимущественно ясно",
    2: "переменная облачность",
    3: "пасмурно",
    45: "туман",
    48: "изморозь",
    51: "слабая морось",
    53: "морось",
    55: "сильная морось",
    56: "слабая ледяная морось",
    57: "ледяная морось",
    61: "небольшой дождь",
    63: "дождь",
    65: "сильный дождь",
    66: "слабый ледяной дождь",
    67: "ледяной дождь",
    71: "небольшой снег",
    73: "снег",
    75: "сильный снег",
    77: "снежная крупа",
    80: "небольшой ливень",
    81: "ливень",
    82: "сильный ливень",
    85: "снегопад",
    86: "сильный снегопад",
    95: "гроза",
    96: "гроза с небольшим градом",
    99: "гроза с градом",
}


def _weather_desc(code: int) -> str:
    """Преобразование WMO-кода в текст."""
    return _WEATHER_CODE_RU.get(int(code), "без осадков")


def get_weather() -> str:
    """Получение погоды через Open-Meteo API (бесплатно, без API-ключа)."""
    from config import WEATHER_CITY, WEATHER_LAT, WEATHER_LON, WEATHER_TIMEZONE

    try:
        url = "https://api.open-meteo.com/v1/forecast"
        response = requests.get(
            url,
            params={
                "latitude": WEATHER_LAT,
                "longitude": WEATHER_LON,
                "current": "temperature_2m,weather_code",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min",
                "timezone": WEATHER_TIMEZONE,
                "forecast_days": 1,
            },
            timeout=REQUEST_TIMEOUT,
        )
        data = response.json()

        if "error" in data:
            logger.warning("Open-Meteo error: %s", data.get("reason", data))
            return "🌡️ Не удалось получить данные о погоде"

        curr = data.get("current", {})
        daily = data.get("daily", {})

        temp = curr.get("temperature_2m")
        code = curr.get("weather_code", 0)
        desc = _weather_desc(code)

        parts = [f"🌡️ {WEATHER_CITY}: {temp:+.0f}°C, {desc}"]

        times = daily.get("time", [])
        if times:
            t_max = daily.get("temperature_2m_max", [None])[0]
            t_min = daily.get("temperature_2m_min", [None])[0]
            if t_max is not None and t_min is not None:
                parts.append(f"Днём: {t_max:+.0f}°C, ночью: {t_min:+.0f}°C")

        return "\n".join(parts)

    except Exception as e:
        logger.error("Ошибка получения погоды: %s", e)
        return "🌡️ Ошибка при получении погоды"
