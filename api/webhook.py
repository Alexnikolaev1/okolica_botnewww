#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Vercel serverless function: webhook для Telegram бота.
Получает POST от Telegram, обрабатывает update через python-telegram-bot.
"""

import asyncio
import json
import logging
import sys
from http.server import BaseHTTPRequestHandler

# Добавляем корень проекта в path для импортов
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from telegram import Update
from config import BOT_TOKEN
from bot import build_application

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def process_update(body: bytes) -> None:
    """Обработка одного update от Telegram."""
    data = json.loads(body)
    application = build_application(BOT_TOKEN)
    await application.initialize()
    try:
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
    finally:
        await application.shutdown()


class handler(BaseHTTPRequestHandler):
    """Vercel Python handler для Telegram webhook."""

    def do_GET(self):
        """Проверка здоровья — для Vercel и отладки."""
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK okolica bot webhook")

    def do_POST(self):
        """Приём update от Telegram."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self.send_error(400, "Empty body")
            return

        body = self.rfile.read(content_length)

        try:
            asyncio.run(process_update(body))
        except Exception as e:
            logger.exception("Ошибка обработки webhook: %s", e)
            self.send_response(500)
            self.send_header("Content-type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(str(e).encode("utf-8"))
            return

        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK")
