#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Vercel Cron: проверка новых статей и рассылка подписчикам.
Вызывается по расписанию (vercel.json → cron).
Защита: задайте CRON_SECRET в env и передавайте в заголовке Authorization: Bearer <secret>.
"""

import asyncio
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler

from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from config import BOT_TOKEN
from database import (
    init_database,
    add_article,
    article_exists,
    get_subscribed_users,
)
from parser import get_latest_articles as fetch_latest
from utils import escape_html, truncate_message

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def run_check_and_notify() -> dict:
    """Проверка новых статей и отправка подписчикам. Возвращает статистику."""
    from telegram import Bot

    init_database()
    articles = fetch_latest(10)
    users = get_subscribed_users()

    new_count = 0
    sent_count = 0

    bot = Bot(token=BOT_TOKEN)
    try:
        for article in articles:
            if article_exists(article["url"]):
                continue
            add_article(article["title"], article["url"], article.get("summary"))
            new_count += 1

            title_safe = escape_html(article["title"])
            summary_safe = escape_html(article.get("summary", "")) if article.get("summary") else ""
            url_safe = article["url"].replace('"', "&quot;")
            msg = f"📰 <b>Новая статья</b>\n\n<b>{title_safe}</b>\n\n{summary_safe}\n\n🔗 <a href=\"{url_safe}\">Читать</a>"
            if len(msg) > 4096:
                msg = msg[:4090] + "..."

            for user_id in users:
                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text=msg,
                        parse_mode="HTML",
                    )
                    sent_count += 1
                    await asyncio.sleep(0.1)
                except Exception as e:
                    logger.warning("Не удалось отправить %s: %s", user_id, e)
    finally:
        await bot.shutdown()

    return {"new_articles": new_count, "notifications_sent": sent_count, "subscribers": len(users)}


class handler(BaseHTTPRequestHandler):
    """Vercel Cron endpoint."""

    def do_GET(self):
        """Запуск проверки новых статей."""
        # Проверка CRON_SECRET
        secret = os.getenv("CRON_SECRET")
        if secret:
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {secret}":
                self.send_response(403)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"Forbidden"}')
                return

        if not BOT_TOKEN:
            self.send_response(500)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"BOT_TOKEN not set"}')
            return

        try:
            result = asyncio.run(run_check_and_notify())
            body = f'{{"ok":true,"new_articles":{result["new_articles"]},"notifications_sent":{result["notifications_sent"]},"subscribers":{result["subscribers"]}}}'
        except Exception as e:
            logger.exception("Ошибка cron: %s", e)
            self.send_response(500)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(f'{{"error":"{str(e)}"}}'.encode())
            return

        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())
