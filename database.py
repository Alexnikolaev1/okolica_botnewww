#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Модуль работы с базой данных"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import List, Dict, Optional

from config import DB_PATH


@contextmanager
def get_connection():
    """Контекстный менеджер для подключения к БД."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database() -> None:
    """Инициализация схемы базы данных."""
    with get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                telegram_id INTEGER UNIQUE,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                is_subscribed BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                url TEXT UNIQUE,
                summary TEXT,
                published_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                article_id INTEGER,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (telegram_id),
                FOREIGN KEY (article_id) REFERENCES articles (id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'new',
                FOREIGN KEY (user_id) REFERENCES users (telegram_id)
            )
        """)


def add_user(
    telegram_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
) -> None:
    """Регистрация или обновление пользователя."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO users (telegram_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name
            """,
            (telegram_id, username, first_name, last_name),
        )


def set_subscription(telegram_id: int, subscribed: bool) -> None:
    """Обновление статуса подписки пользователя."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET is_subscribed = ? WHERE telegram_id = ?",
            (1 if subscribed else 0, telegram_id),
        )


def get_subscribed_users() -> List[int]:
    """Список telegram_id подписанных пользователей."""
    with get_connection() as conn:
        cursor = conn.execute("SELECT telegram_id FROM users WHERE is_subscribed = 1")
        return [row[0] for row in cursor.fetchall()]


def add_article(title: str, url: str, summary: Optional[str] = None) -> Optional[int]:
    """
    Добавление статьи. Возвращает article_id при успехе или None,
    если статья с таким URL уже существует.
    """
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO articles (title, url, summary, published_at)
            VALUES (?, ?, ?, ?)
            """,
            (title, url, summary or "", datetime.now().isoformat()),
        )
        return cursor.lastrowid if cursor.lastrowid else None


def article_exists(url: str) -> bool:
    """Проверка существования статьи по URL."""
    with get_connection() as conn:
        cursor = conn.execute("SELECT 1 FROM articles WHERE url = ? LIMIT 1", (url,))
        return cursor.fetchone() is not None


def search_articles(query: str, limit: int = 10) -> List[Dict]:
    """Поиск статей в локальной базе."""
    pattern = f"%{query}%"
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT title, url, summary, published_at
            FROM articles
            WHERE title LIKE ? OR summary LIKE ?
            ORDER BY published_at DESC
            LIMIT ?
            """,
            (pattern, pattern, limit),
        )
        return [_row_to_article(row) for row in cursor.fetchall()]


def get_latest_articles(limit: int = 5) -> List[Dict]:
    """Последние статьи из базы."""
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT title, url, summary, published_at
            FROM articles
            ORDER BY published_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [_row_to_article(row) for row in cursor.fetchall()]


def add_feedback(user_id: int, message: str) -> None:
    """Сохранение обращения в редакцию."""
    with get_connection() as conn:
        conn.execute("INSERT INTO feedback (user_id, message) VALUES (?, ?)", (user_id, message))


def _row_to_article(row: sqlite3.Row) -> Dict:
    """Преобразование строки БД в словарь статьи."""
    return {
        "title": row[0],
        "url": row[1],
        "summary": row[2] or "",
        "published_at": row[3],
    }
