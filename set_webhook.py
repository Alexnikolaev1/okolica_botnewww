#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Установка webhook для Telegram бота.
Запуск: python set_webhook.py <URL>
Пример: python set_webhook.py https://okolica-botnew-xxx.vercel.app
"""

import sys
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from config import BOT_TOKEN


def main():
    if len(sys.argv) < 2:
        print("Использование: python set_webhook.py <BASE_URL>")
        print("Пример: python set_webhook.py https://okolica-botnew-xxx.vercel.app")
        sys.exit(1)

    base_url = sys.argv[1].rstrip("/")
    webhook_url = f"{base_url}/api/webhook"

    if not BOT_TOKEN:
        print("❗ TELEGRAM_BOT_TOKEN не найден в .env")
        sys.exit(1)

    resp = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
        json={"url": webhook_url},
    )
    data = resp.json()

    if data.get("ok"):
        print(f"✅ Webhook установлен: {webhook_url}")
    else:
        print(f"❌ Ошибка: {data.get('description', data)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
