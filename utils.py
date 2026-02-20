#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Утилиты форматирования сообщений"""

import html
from typing import List, Dict

from config import MAX_MESSAGE_LENGTH


def escape_html(text: str) -> str:
    """Экранирование для HTML-режима Telegram."""
    if not text:
        return ""
    return html.escape(str(text))


def format_articles_list(
    articles: List[Dict],
    header: str,
    max_length: int = MAX_MESSAGE_LENGTH,
    use_html: bool = True,
) -> str:
    """Форматирование списка статей в одно сообщение."""
    lines = [header, ""]
    for i, a in enumerate(articles, 1):
        title = escape_html(a["title"])
        summary = escape_html(a.get("summary", "")) if a.get("summary") else ""
        url = a["url"]

        if use_html:
            # В href экранируем только кавычки для безопасности
            url_safe = url.replace('"', "&quot;") if '"' in url else url
            block = f'{i}. <b>{title}</b>\n'
            if summary:
                block += f"{summary}\n"
            block += f'🔗 <a href="{url_safe}">Читать</a>\n\n'
        else:
            block = f"{i}. **{a['title']}**\n"
            if summary:
                block += f"{summary}\n"
            block += f"🔗 [Читать]({url})\n\n"

        if len("\n".join(lines) + block) > max_length - 50:
            lines.append("… (сообщение обрезано)")
            break
        lines.append(block)

    return "\n".join(lines).strip()


def truncate_message(text: str, max_len: int = MAX_MESSAGE_LENGTH) -> str:
    """Обрезка сообщения до лимита Telegram."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 20] + "\n\n… (продолжение обрезано)"
