#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Конфигурация бота газеты «Сибирская околица»"""

import os
from pathlib import Path

# Загрузка переменных окружения
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Сайты
SITE_URL = "https://sibokolica.ru"
OLD_SITE_URL = "https://okolica.net"

# База данных
# На Vercel: /tmp — единственная записываемая директория (данные не сохраняются между cold start)
# Для продакшена: используйте внешнюю БД (Turso, Neon, Vercel Postgres)
DB_PATH = os.getenv("DB_PATH") or (
    "/tmp/okolica_bot.db" if os.getenv("VERCEL") else "okolica_bot.db"
)

# Telegram
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7018731177"))  # ID для новостей и обращений

# Погода (Open-Meteo, бесплатно, без API-ключа)
WEATHER_CITY = "Татарск"  # Город для отображения
WEATHER_LAT = float(os.getenv("WEATHER_LAT", "55.2213"))
WEATHER_LON = float(os.getenv("WEATHER_LON", "75.9815"))
WEATHER_TIMEZONE = os.getenv("WEATHER_TIMEZONE", "Asia/Novosibirsk")

# Ограничения
MAX_MESSAGE_LENGTH = 4096  # Лимит Telegram
ARTICLES_LIMIT_LATEST = 5
ARTICLES_LIMIT_SEARCH = 10
OKOLICA_HTML_PAGES = 12  # кол-во страниц /news/?page=N
OKOLICA_CATEGORY_PAGES = 5  # страниц на каждый раздел (rayon, busines и т.д.)
OKOLICA_GAZETA_PAGES = 8  # страниц архива для общего поиска
OKOLICA_GAZETA_PAGES_ARCHIVE = 20  # страниц архива для поиска поэзии/статей (больше охват)
OKOLICA_ARCHIVE_CATEGORY_PAGES = 10  # страниц для разделов gorod, foto в поиске по архиву
ARTICLES_LIMIT_ARCHIVE = 15  # лимит результатов для поиска по архиву
JOB_CHECK_INTERVAL_MINUTES = 30

# HTTP
REQUEST_TIMEOUT = 10
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
