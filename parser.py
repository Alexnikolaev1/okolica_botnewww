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
    ARTICLES_LIMIT_ARCHIVE,
    OKOLICA_HTML_PAGES,
    OKOLICA_CATEGORY_PAGES,
    OKOLICA_GAZETA_PAGES,
    OKOLICA_GAZETA_PAGES_ARCHIVE,
)

# Разделы okolica.net для расширенного поиска (пустая строка = главная лента)
_OKOLICA_CATEGORIES = ["", "rayon", "busines", "gorod", "foto"]

# Стоп-слова (не участвуют в поиске)
_STOP_WORDS = frozenset({
    "и", "в", "на", "с", "по", "из", "к", "от", "для", "о", "об", "а", "но", "у",
    "же", "или", "как", "что", "это", "всё", "все", "его", "её", "их", "он", "она",
    "они", "мы", "вы", "я", "не", "ни", "без", "до", "за", "при", "про", "так",
    "уже", "еще", "тоже", "только", "можно", "быть", "есть", "был", "была",
})

# Синонимы: ключ → дополнительные слова для расширения запроса
_SYNONYMS = {
    "поэзия": ["стихи", "стих", "стихотворение"],
    "стихи": ["поэзия", "стих"],
    "стих": ["поэзия", "стихи"],
    "стихотворение": ["поэзия", "стихи"],
    "рассказ": ["история", "очерк"],
    "очерк": ["рассказ", "статья"],
    "статья": ["очерк", "материал"],
    "победа": ["победитель", "победный"],
    "школа": ["школьник", "школьный"],
    "дети": ["ребенок", "ребята"],
    "праздник": ["праздничный", "празднование"],
    "война": ["военный", "фронт"],
    "татарск": ["татарский"],
}

logger = logging.getLogger(__name__)

_morph_analyzer = None


def _get_morph():
    """Ленивая инициализация морфологического анализатора."""
    global _morph_analyzer
    if _morph_analyzer is None:
        try:
            import pymorphy2
            _morph_analyzer = pymorphy2.MorphAnalyzer()
        except ImportError:
            _morph_analyzer = False
    return _morph_analyzer


def _normalize_word(word: str) -> str:
    """Приведение слова к нормальной форме (лемма) для поиска."""
    morph = _get_morph()
    if not morph:
        return word.lower()
    try:
        parsed = morph.parse(word.lower())
        if parsed:
            return parsed[0].normal_form
    except Exception:
        pass
    return word.lower()


def _expand_with_synonyms(words: list[str]) -> list[str]:
    """Добавляет синонимы к списку слов."""
    seen = set(words)
    result = list(words)
    for w in words:
        if w in _SYNONYMS:
            for s in _SYNONYMS[w]:
                if s not in seen and len(s) >= 2:
                    seen.add(s)
                    result.append(s)
        else:
            for key, syns in _SYNONYMS.items():
                if w in syns and key not in seen:
                    seen.add(key)
                    result.append(key)
                    break
    return result


def _extract_and_expand_query(query: str) -> list[str]:
    """
    Извлечение слов из запроса: токенизация, стоп-слова, стемминг, синонимы.
    Возвращает список нормализованных слов (без дубликатов).
    """
    words = [w for w in re.findall(r"[а-яёa-z0-9]+", query.lower()) if len(w) >= 2]
    words = [w for w in words if w not in _STOP_WORDS]
    if not words:
        return []

    # Нормализация (лемматизация)
    normalized = []
    seen = set()
    for w in words:
        norm = _normalize_word(w)
        if norm not in seen:
            seen.add(norm)
            normalized.append(norm)

    # Расширение синонимами
    return _expand_with_synonyms(normalized)


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


def _fetch_okolica_html_from_path(
    base_path: str, max_pages: int, seen_urls: set
) -> list[dict]:
    """Загрузка статей с одной секции (news, news/rayon, news/busines)."""
    articles = []
    path = f"{OLD_SITE_URL}/news/{base_path}" if base_path else f"{OLD_SITE_URL}/news"
    path = path.rstrip("/")

    for page in range(1, max_pages + 1):
        try:
            url = f"{path}/?page={page}" if page > 1 else f"{path}/"
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
                articles.append({
                    "title": title,
                    "url": full_url_https,
                    "summary": "",
                    "_fulltext": "",
                })

        except Exception as e:
            logger.warning("Ошибка HTML okolica.net %s page %s: %s", base_path or "news", page, e)
            break

    return articles


def _fetch_okolica_html(max_pages: int = None) -> list[dict]:
    """
    Загрузка статей со всех разделов okolica.net: главная лента + rayon, busines.
    Сайт в cp1251.
    """
    seen_urls: set = set()
    all_articles = []

    # Главная лента /news/
    all_articles.extend(
        _fetch_okolica_html_from_path("", OKOLICA_HTML_PAGES, seen_urls)
    )

    # Разделы rayon, busines
    for cat in _OKOLICA_CATEGORIES:
        if not cat:
            continue
        all_articles.extend(
            _fetch_okolica_html_from_path(cat, OKOLICA_CATEGORY_PAGES, seen_urls)
        )

    return all_articles


def _fetch_okolica_gazeta(max_pages: int = None) -> list[dict]:
    """
    Загрузка заголовков статей из архива газеты /gazeta/.
    Формат: «• Заголовок 1 • Заголовок 2» — разбиваем по • и добавляем в поиск.
    URL ведёт на архив (пользователь найдёт выпуск).
    """
    max_pages = max_pages or OKOLICA_GAZETA_PAGES
    all_articles = []
    seen_titles: set = set()

    for page in range(1, max_pages + 1):
        try:
            url = f"{OLD_SITE_URL}/gazeta/" + (f"?page={page}" if page > 1 else "")
            response = _make_request(url)
            response.raise_for_status()
            try:
                text = response.content.decode("cp1251")
            except UnicodeDecodeError:
                text = response.content.decode("utf-8", errors="replace")
            soup = BeautifulSoup(text, "html.parser")

            # Ищем блоки с выпусками: заголовки статей в формате «• Текст • Текст»
            for elem in soup.find_all(["p", "div", "li", "td"]):
                txt = elem.get_text(separator=" ", strip=True)
                if "•" not in txt or len(txt) < 10:
                    continue
                # Разбиваем по • и берём каждую часть как заголовок
                parts = [p.strip() for p in txt.split("•") if len(p.strip()) >= 5]
                for title in parts:
                    title = re.sub(r"\s+", " ", title)
                    if len(title) < 5 or title in seen_titles:
                        continue
                    seen_titles.add(title)
                    all_articles.append({
                        "title": title,
                        "url": f"{OLD_SITE_URL}/gazeta/",
                        "summary": "",
                        "_fulltext": title,
                    })

        except Exception as e:
            logger.warning("Ошибка gazeta okolica.net page %s: %s", page, e)
            break

    return all_articles


_lemma_cache: dict[str, str] = {}
_LEMMA_CACHE_MAX = 5000


def _get_lemma(word: str) -> str:
    """Лемма слова с кэшированием."""
    w = word.lower()
    if w not in _lemma_cache:
        if len(_lemma_cache) >= _LEMMA_CACHE_MAX:
            _lemma_cache.clear()
        _lemma_cache[w] = _normalize_word(w)
    return _lemma_cache[w]


def _word_matches(query_word: str, searchable: str) -> bool:
    """
    Проверка вхождения слова в текст. Учитывает:
    - точное вхождение подстроки (день в деньги)
    - совпадение по лемме (школа/школы/школьник через pymorphy2)
    - совпадение по префиксу 3 символа (fallback)
    """
    searchable_lower = searchable.lower()
    if query_word in searchable_lower:
        return True

    query_lemma = _get_lemma(query_word)
    words_in_text = re.findall(r"[а-яёa-z]{2,}", searchable_lower)

    for w in words_in_text:
        word_lemma = _get_lemma(w)
        if query_lemma == word_lemma:
            return True
        if query_word in word_lemma or word_lemma in query_word:
            return True
        # Префикс 3+ символов
        if len(query_lemma) >= 3 and len(word_lemma) >= 3:
            if query_lemma[:3] == word_lemma[:3]:
                return True

    return False


def _count_matches(query_words: list[str], searchable: str) -> int:
    """Количество совпавших слов запроса (для сортировки по релевантности)."""
    return sum(1 for w in query_words if _word_matches(w, searchable))


def _run_search(articles: list[dict], query_words: list[str], limit: int) -> list[dict]:
    """
    Поиск по списку статей с сортировкой по релевантности.
    Сначала — все слова, затем — любое слово. Внутри — по количеству совпадений.
    """
    if not query_words:
        return []

    # Все слова
    full_match = []
    for a in articles:
        searchable = f"{a['title']} {a.get('summary', '')} {a.get('_fulltext', '')}"
        if all(_word_matches(w, searchable) for w in query_words):
            cnt = _count_matches(query_words, searchable)
            full_match.append((cnt, a))

    if full_match:
        full_match.sort(key=lambda x: (-x[0], x[1]["title"]))
        return [
            {"title": a["title"], "url": a["url"], "summary": a.get("summary", "")}
            for _, a in full_match[:limit]
        ]

    # Любое слово
    any_match = []
    for a in articles:
        searchable = f"{a['title']} {a.get('summary', '')} {a.get('_fulltext', '')}"
        cnt = _count_matches(query_words, searchable)
        if cnt > 0:
            any_match.append((cnt, a))

    any_match.sort(key=lambda x: (-x[0], x[1]["title"]))
    return [
        {"title": a["title"], "url": a["url"], "summary": a.get("summary", "")}
        for _, a in any_match[:limit]
    ]


def _merge_okolica_sources(
    rss_articles: list[dict], html_articles: list[dict], gazeta_articles: list[dict] = None
) -> list[dict]:
    """
    Объединяет RSS, HTML и gazeta. При дубликате по URL приоритет у RSS.
    Gazeta: много статей с одним URL, различаются по title.
    """
    by_url: dict[str, dict] = {}
    for a in rss_articles:
        url = a["url"].replace("http://", "https://", 1).rstrip("/")
        by_url[url] = a
    for a in html_articles:
        url = a["url"].replace("http://", "https://", 1).rstrip("/")
        if url not in by_url:
            by_url[url] = a
    result = list(by_url.values())
    result.extend(gazeta_articles or [])
    return result


def search_okolica_news(query: str, limit: int = None) -> list[dict]:
    """
    Поиск новостей на okolica.net: RSS + разделы новостей (rayon, busines, gorod, foto).
    Без архива газеты.
    """
    limit = limit or ARTICLES_LIMIT_SEARCH
    try:
        rss_articles = _fetch_okolica_rss()
        html_articles = _fetch_okolica_html()
        articles = _merge_okolica_sources(rss_articles, html_articles, gazeta_articles=None)
        if not articles:
            return []
        query_words = _extract_and_expand_query(query)
        return _run_search(articles, query_words, limit)
    except Exception as e:
        logger.error("Ошибка поиска новостей okolica.net: %s", e)
        return []


def search_okolica_archive(query: str, limit: int = None) -> list[dict]:
    """
    Поиск по архиву газеты: поэзия, рассказы, очерки и др.
    Использует расширенный охват страниц архива.
    """
    limit = limit or ARTICLES_LIMIT_ARCHIVE
    try:
        gazeta_articles = _fetch_okolica_gazeta(OKOLICA_GAZETA_PAGES_ARCHIVE)
        if not gazeta_articles:
            return []
        query_words = _extract_and_expand_query(query)
        return _run_search(gazeta_articles, query_words, limit)
    except Exception as e:
        logger.error("Ошибка поиска по архиву okolica.net: %s", e)
        return []


def search_okolica_only(query: str, limit: int = None) -> list[dict]:
    """
    Объединённый поиск на okolica.net (новости + архив).
    Для раздельного поиска используйте search_okolica_news и search_okolica_archive.
    """
    limit = limit or ARTICLES_LIMIT_SEARCH
    try:
        rss_articles = _fetch_okolica_rss()
        html_articles = _fetch_okolica_html()
        gazeta_articles = _fetch_okolica_gazeta()
        articles = _merge_okolica_sources(rss_articles, html_articles, gazeta_articles)
        if not articles:
            return []
        query_words = _extract_and_expand_query(query)
        return _run_search(articles, query_words, limit)
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
