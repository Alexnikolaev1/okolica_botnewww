#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Телеграм-бот газеты «Сибирская околица».
Новости, поиск, подписки, обратная связь.
"""

# APScheduler требует pytz; stdlib/zoneinfo timezone конвертируем в pytz
import datetime as _dt
import apscheduler.util
import pytz
_orig_astimezone = apscheduler.util.astimezone
def _patched_astimezone(obj):
    if obj is None or obj is _dt.timezone.utc:
        return pytz.UTC
    if hasattr(obj, "zone") and obj.zone == "UTC":
        return pytz.UTC
    # tzinfo без localize/normalize (stdlib, zoneinfo) — считаем UTC-подобными
    if isinstance(obj, _dt.tzinfo) and not (hasattr(obj, "localize") and hasattr(obj, "normalize")):
        if getattr(obj, "utcoffset", lambda _: _dt.timedelta(0))(None) == _dt.timedelta(0):
            return pytz.UTC
    return _orig_astimezone(obj)
apscheduler.util.astimezone = _patched_astimezone

import asyncio
import logging
from datetime import timedelta
from functools import partial
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
import pytz
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    JobQueue,
    filters,
)

from config import (
    BOT_TOKEN,
    SITE_URL,
    OLD_SITE_URL,
    ADMIN_ID,
    ARTICLES_LIMIT_LATEST,
    ARTICLES_LIMIT_SEARCH,
    ARTICLES_LIMIT_ARCHIVE,
    JOB_CHECK_INTERVAL_MINUTES,
    MAX_MESSAGE_LENGTH,
)
from database import (
    init_database,
    add_user,
    set_subscription,
    get_subscribed_users,
    add_article,
    article_exists,
    search_articles,
    get_latest_articles,
)
from parser import (
    get_latest_articles as fetch_latest,
    search_okolica_old,
    search_okolica_news,
    search_okolica_archive,
    get_weather,
)
from utils import format_articles_list, truncate_message, escape_html

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def run_blocking(func, *args, **kwargs):
    """Запуск блокирующей функции в пуле потоков."""
    loop = asyncio.get_event_loop()
    return loop.run_in_executor(None, partial(func, *args, **kwargs))


class OkolicaBot:
    """Основной класс бота."""

    def __init__(self, token: str):
        self.token = token
        init_database()
        tz = pytz.timezone("Europe/Moscow")
        job_queue = JobQueue()
        cfg = {k: v for k, v in job_queue.scheduler_configuration.items() if k != "timezone"}
        job_queue.scheduler.configure(timezone=tz, **cfg)
        self.application = (
            Application.builder()
            .token(token)
            .job_queue(job_queue)
            .build()
        )
        self._setup_handlers()

    def _setup_handlers(self) -> None:
        """Регистрация обработчиков команд и сообщений."""
        self.application.add_handler(CommandHandler("start", self.cmd_start))
        self.application.add_handler(CommandHandler("help", self.cmd_help))
        self.application.add_handler(CommandHandler("latest", self.cmd_latest))
        self.application.add_handler(CommandHandler("weather", self.cmd_weather))
        self.application.add_handler(CommandHandler("search", self.cmd_search))
        self.application.add_handler(CommandHandler("subscribe", self.cmd_subscribe))
        self.application.add_handler(CommandHandler("unsubscribe", self.cmd_unsubscribe))
        self.application.add_handler(CommandHandler("news", self.cmd_news))
        self.application.add_handler(CommandHandler("voice", self.cmd_voice))
        self.application.add_handler(CommandHandler("contacts", self.cmd_contacts))
        self.application.add_handler(CommandHandler("search_old", self.cmd_search_old))
        self.application.add_handler(CommandHandler("search_old_news", self.cmd_search_old_news))
        self.application.add_handler(CommandHandler("search_old_archive", self.cmd_search_old_archive))
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
        )
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
        self.application.add_handler(
            MessageHandler(filters.COMMAND, self.cmd_unknown)
        )
        self.application.add_error_handler(self.error_handler)

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Приветствие и регистрация пользователя."""
        user = update.effective_user
        await run_blocking(
            add_user,
            user.id,
            user.username,
            user.first_name,
            user.last_name,
        )

        name = user.first_name or "друг"
        text = f"""👋 Добро пожаловать, {name}!

Я бот газеты «Сибирская околица». Вот что я умею:

📰 /latest — последние новости
🔍 /search <запрос> — поиск (новый + старый сайт)
📰 /search_old_news <запрос> — поиск новостей на okolica.net
📖 /search_old_archive <запрос> — поиск поэзии и статей в архиве
📚 Архив — выпуски газеты (okolica.net/gazeta/)
🌤️ /weather — погода в Татарске
📝 /news <текст> — предложить новость
📣 /voice <текст> — написать в рубрику «Голос народа»
📞 /contacts — контакты редакции
📢 /subscribe — подписаться на уведомления
🔕 /unsubscribe — отписаться

Используйте /help для подробной справки."""

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📰 Последние новости", callback_data="latest")],
            [InlineKeyboardButton("🔍 Поиск", callback_data="search_prompt")],
            [InlineKeyboardButton("📰 Поиск новостей (старый сайт)", callback_data="search_old_news_prompt")],
            [InlineKeyboardButton("📖 Поиск иной инфы (архив)", callback_data="search_old_archive_prompt")],
            [InlineKeyboardButton("📚 Архив", url=f"{OLD_SITE_URL}/gazeta/")],
            [InlineKeyboardButton("📝 Предложить новость", callback_data="news_prompt")],
            [InlineKeyboardButton("📣 В Голос народа", callback_data="voice_prompt")],
            [InlineKeyboardButton("📞 Контакты", callback_data="contacts")],
            [InlineKeyboardButton("🌤️ Погода", callback_data="weather")],
            [InlineKeyboardButton("🌐 Сайт газеты", url=SITE_URL)],
        ])

        photo_path = Path(__file__).parent / "okolica.jpg"
        if photo_path.exists():
            await update.message.reply_photo(
                photo=photo_path,
                caption=text,
                reply_markup=keyboard,
            )
        else:
            await update.message.reply_text(text, reply_markup=keyboard)

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Справка по командам."""
        text = """🤖 <b>Справка по командам</b>

📰 /latest — последние статьи
🔍 /search &lt;текст&gt; — поиск (okolica.net и sibokolica.ru)
📰 /search_old_news &lt;текст&gt; — поиск новостей на okolica.net
📖 /search_old_archive &lt;текст&gt; — поиск поэзии и статей в архиве
📚 Архив — выпуски газеты (okolica.net/gazeta/)
🌤️ /weather — погода
📝 /news &lt;текст&gt; — предложить новость
📣 /voice &lt;текст&gt; — написать в рубрику «Голос народа»
📞 /contacts — контакты редакции
📢 /subscribe — подписка на уведомления
🔕 /unsubscribe — отписка

🌐 <b>Сайт:</b> https://sibokolica.ru"""

        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_latest(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Последние новости."""
        chat_id = update.effective_chat.id
        await context.bot.send_message(chat_id, "🔄 Загружаю последние новости…")

        # Сначала получаем свежие данные с сайта
        articles = await run_blocking(fetch_latest, ARTICLES_LIMIT_LATEST)

        if not articles:
            articles = await run_blocking(get_latest_articles, ARTICLES_LIMIT_LATEST)

        if articles:
            # Сохраняем новые статьи в БД
            for a in articles:
                if not await run_blocking(article_exists, a["url"]):
                    await run_blocking(add_article, a["title"], a["url"], a.get("summary"))

            text = format_articles_list(
                articles,
                "📰 <b>Последние новости:</b>\n",
            )
            await context.bot.send_message(
                chat_id,
                truncate_message(text),
                parse_mode="HTML",
            )
        else:
            await context.bot.send_message(
                chat_id,
                "😔 Не удалось загрузить новости. Попробуйте позже.",
            )

    async def cmd_weather(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Погода."""
        weather = await run_blocking(get_weather)
        await update.message.reply_text(weather)

    async def cmd_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Поиск по ключевым словам (okolica.net + sibokolica.ru)."""
        if not context.args:
            await update.message.reply_text(
                "🔍 Укажите поисковый запрос:\n/search ваш запрос"
            )
            return

        query = " ".join(context.args)
        chat_id = update.effective_chat.id
        await context.bot.send_message(chat_id, "🔍 Ищу…")

        articles = await run_blocking(search_okolica_old, query, ARTICLES_LIMIT_SEARCH)
        if not articles:
            articles = await run_blocking(search_articles, query, ARTICLES_LIMIT_SEARCH)

        if articles:
            header = f"🔍 <b>Результаты по запросу «{escape_html(query)}»:</b>\n\n"
            text = format_articles_list(articles, header)
            await context.bot.send_message(
                chat_id,
                truncate_message(text),
                parse_mode="HTML",
            )
        else:
            await context.bot.send_message(
                chat_id,
                f"😔 По запросу «{escape_html(query)}» ничего не найдено.",
            )

    async def cmd_search_old(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Поиск на старом сайте okolica.net (новости + архив)."""
        if not context.args:
            await update.message.reply_text(
                "🔎 <b>Поиск на okolica.net</b>\n\n"
                "Используйте:\n"
                "• /search_old_news запрос — новости\n"
                "• /search_old_archive запрос — поэзия и статьи в архиве",
                parse_mode="HTML",
            )
            return

        query = " ".join(context.args)
        chat_id = update.effective_chat.id
        await context.bot.send_message(chat_id, "🔎 Ищу на okolica.net…")

        articles = await run_blocking(search_okolica_news, query, ARTICLES_LIMIT_SEARCH)
        if not articles:
            articles = await run_blocking(search_okolica_archive, query, ARTICLES_LIMIT_ARCHIVE)

        if articles:
            header = f"🔎 <b>Результаты на okolica.net по запросу «{escape_html(query)}»:</b>\n\n"
            text = format_articles_list(articles, header)
            await context.bot.send_message(chat_id, truncate_message(text), parse_mode="HTML")
        else:
            await context.bot.send_message(
                chat_id,
                f"😔 По запросу «{escape_html(query)}» ничего не найдено.",
            )

    async def cmd_search_old_news(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Поиск новостей на okolica.net."""
        if not context.args:
            await update.message.reply_text(
                "📰 <b>Поиск новостей на okolica.net</b>\n\n"
                "Укажите запрос: /search_old_news ваш запрос",
                parse_mode="HTML",
            )
            return

        query = " ".join(context.args)
        chat_id = update.effective_chat.id
        await context.bot.send_message(chat_id, "📰 Ищу новости на okolica.net…")

        articles = await run_blocking(search_okolica_news, query, ARTICLES_LIMIT_SEARCH)

        if articles:
            header = f"📰 <b>Новости okolica.net по запросу «{escape_html(query)}»:</b>\n\n"
            text = format_articles_list(articles, header)
            await context.bot.send_message(chat_id, truncate_message(text), parse_mode="HTML")
        else:
            await context.bot.send_message(
                chat_id,
                f"😔 По запросу «{escape_html(query)}» новостей не найдено.",
            )

    async def cmd_search_old_archive(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Поиск поэзии и статей в архиве газеты."""
        if not context.args:
            await update.message.reply_text(
                "📖 <b>Поиск по архиву</b> (Район, Бизнес, Авторское)\n\n"
                "Укажите запрос: /search_old_archive ваш запрос",
                parse_mode="HTML",
            )
            return

        query = " ".join(context.args)
        chat_id = update.effective_chat.id
        await context.bot.send_message(chat_id, "📖 Ищу в архиве okolica.net…")

        articles = await run_blocking(search_okolica_archive, query, ARTICLES_LIMIT_ARCHIVE)

        if articles:
            header = f"📖 <b>Архив газеты по запросу «{escape_html(query)}»:</b>\n\n"
            text = format_articles_list(articles, header)
            await context.bot.send_message(chat_id, truncate_message(text), parse_mode="HTML")
        else:
            await context.bot.send_message(
                chat_id,
                f"😔 По запросу «{escape_html(query)}» в архиве ничего не найдено.",
            )

    async def cmd_subscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Подписка на уведомления."""
        user_id = update.effective_user.id
        await run_blocking(set_subscription, user_id, True)
        await update.message.reply_text(
            "✅ Вы подписаны на уведомления о новых статьях!\n"
            "Используйте /unsubscribe для отписки."
        )

    async def cmd_unsubscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Отписка от уведомлений."""
        user_id = update.effective_user.id
        await run_blocking(set_subscription, user_id, False)
        await update.message.reply_text(
            "🔕 Вы отписаны от уведомлений.\n"
            "Используйте /subscribe для подписки."
        )

    async def _send_to_admin(
        self, context: ContextTypes.DEFAULT_TYPE, label: str, text: str, user
    ) -> tuple[bool, str]:
        """
        Отправка сообщения администратору.
        Возвращает (успех, подсказка_при_ошибке).
        """
        try:
            user_info = f"{user.first_name or ''} {user.last_name or ''}".strip() or "—"
            username = f"@{user.username}" if user.username else "—"
            msg = (
                f"📩 <b>{label}</b>\n\n"
                f"От: {escape_html(user_info)} (id: {user.id}, {username})\n\n"
                f"{escape_html(text)}"
            )
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=msg,
                parse_mode="HTML",
            )
            return True, ""
        except Exception as e:
            logger.error("Ошибка отправки администратору (ADMIN_ID=%s): %s", ADMIN_ID, e)
            hint = " Администратор должен нажать /start в этом боте — иначе бот не может ему писать."
            return False, hint

    async def cmd_news(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Предложить новость — пересылается администратору."""
        if not context.args:
            await update.message.reply_text(
                "📝 <b>Предложить новость</b>\n\n"
                "Напишите: /news ваш текст новости\n\n"
                "Ваше сообщение будет отправлено в редакцию.",
                parse_mode="HTML",
            )
            return
        text = " ".join(context.args)
        user = update.effective_user
        ok, hint = await self._send_to_admin(
            context, "Предложена новость", text, user
        )
        if ok:
            await update.message.reply_text(
                "✅ Ваше предложение новости передано в редакцию. Спасибо!"
            )
        else:
            await update.message.reply_text(
                f"⚠️ Не удалось отправить.{hint}"
            )

    async def cmd_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Написать в рубрику «Голос народа» — пересылается администратору."""
        if not context.args:
            await update.message.reply_text(
                "📣 <b>Голос народа</b>\n\n"
                "Напишите в нашу рубрику «Голос народа»:\n"
                "/voice ваш текст обращения\n\n"
                "Ваше сообщение будет отправлено в редакцию.",
                parse_mode="HTML",
            )
            return
        text = " ".join(context.args)
        user = update.effective_user
        ok, hint = await self._send_to_admin(
            context, "Обращение в рубрику «Голос народа»", text, user
        )
        if ok:
            await update.message.reply_text(
                "✅ Ваше обращение передано в редакцию. Спасибо!"
            )
        else:
            await update.message.reply_text(
                f"⚠️ Не удалось отправить.{hint}"
            )

    async def cmd_contacts(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Контакты редакции."""
        text = """📞 <b>Контакты редакции</b>

Редакция находится по адресу:
г. Татарск, ул. Ленина, 63а

Телефон: 2-444-6

По любым вопросам: 8-993-011-5384
(писать в мессенджер MAX)"""
        await update.message.reply_text(text, parse_mode="HTML")

    async def handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Обработка текстовых сообщений."""
        await update.message.reply_text(
            "Используйте /news для предложения новости, /voice для рубрики «Голос народа», "
            "/search_old_news или /search_old_archive для поиска на okolica.net, "
            "/contacts для контактов редакции."
        )

    async def handle_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Обработка нажатий inline-кнопок."""
        query = update.callback_query
        await query.answer()

        chat_id = query.message.chat_id

        if query.data == "latest":
            # Эмулируем вызов latest — создаём фейковый update с chat
            await context.bot.send_message(chat_id, "🔄 Загружаю последние новости…")
            articles = await run_blocking(fetch_latest, ARTICLES_LIMIT_LATEST)
            if not articles:
                articles = await run_blocking(get_latest_articles, ARTICLES_LIMIT_LATEST)
            if articles:
                for a in articles:
                    if not await run_blocking(article_exists, a["url"]):
                        await run_blocking(add_article, a["title"], a["url"], a.get("summary"))
                text = format_articles_list(articles, "📰 <b>Последние новости:</b>\n")
                await context.bot.send_message(
                    chat_id, truncate_message(text), parse_mode="HTML"
                )
            else:
                await context.bot.send_message(chat_id, "😔 Не удалось загрузить новости.")

        elif query.data == "weather":
            weather = await run_blocking(get_weather)
            await context.bot.send_message(chat_id, weather)

        elif query.data == "search_prompt":
            await context.bot.send_message(
                chat_id,
                "🔍 Введите поисковый запрос:\n/search ваш запрос",
            )

        elif query.data == "search_old_news_prompt":
            await context.bot.send_message(
                chat_id,
                "📰 <b>Поиск новостей на okolica.net</b>\n\n"
                "Укажите запрос: /search_old_news ваш запрос",
                parse_mode="HTML",
            )

        elif query.data == "search_old_archive_prompt":
            await context.bot.send_message(
                chat_id,
                "📖 <b>Поиск по архиву</b> (Район, Бизнес, Авторское)\n\n"
                "Укажите запрос: /search_old_archive ваш запрос",
                parse_mode="HTML",
            )

        elif query.data == "news_prompt":
            await context.bot.send_message(
                chat_id,
                "📝 <b>Предложить новость</b>\n\n"
                "Напишите: /news ваш текст новости\n\n"
                "Ваше сообщение будет отправлено в редакцию.",
                parse_mode="HTML",
            )

        elif query.data == "voice_prompt":
            await context.bot.send_message(
                chat_id,
                "📣 <b>Голос народа</b>\n\n"
                "Напишите в нашу рубрику «Голос народа»:\n"
                "/voice ваш текст обращения\n\n"
                "Ваше сообщение будет отправлено в редакцию.",
                parse_mode="HTML",
            )

        elif query.data == "contacts":
            text = (
                "📞 <b>Контакты редакции</b>\n\n"
                "Редакция находится по адресу:\n"
                "г. Татарск, ул. Ленина, 63а\n\n"
                "Телефон: 2-444-6\n\n"
                "По любым вопросам: 8-993-011-5384\n"
                "(писать в мессенджер MAX)"
            )
            await context.bot.send_message(chat_id, text, parse_mode="HTML")

    async def cmd_unknown(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Неизвестная команда."""
        await update.message.reply_text(
            "❓ Неизвестная команда. Используйте /help для справки."
        )

    async def error_handler(
        self, update: object, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Обработка необработанных исключений."""
        logger.exception("Ошибка при обработке обновления: %s", context.error)
        if update and isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ Произошла ошибка. Попробуйте позже или /help"
            )

    async def job_check_new_articles(
        self, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Периодическая проверка новых статей и рассылка подписчикам."""
        logger.info("Проверка новых статей…")
        articles = await run_blocking(fetch_latest, 10)
        users = await run_blocking(get_subscribed_users)

        for article in articles:
            if await run_blocking(article_exists, article["url"]):
                continue

            await run_blocking(
                add_article,
                article["title"],
                article["url"],
                article.get("summary"),
            )

            title_safe = escape_html(article["title"])
            summary_safe = escape_html(article.get("summary", "")) if article.get("summary") else ""
            url_safe = article["url"].replace('"', "&quot;")

            msg = f"📰 <b>Новая статья</b>\n\n<b>{title_safe}</b>\n\n"
            if summary_safe:
                msg += f"{summary_safe}\n\n"
            msg += f'🔗 <a href="{url_safe}">Читать</a>'

            for user_id in users:
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=truncate_message(msg),
                        parse_mode="HTML",
                    )
                    await asyncio.sleep(0.1)
                except Exception as e:
                    logger.warning("Не удалось отправить уведомление %s: %s", user_id, e)

    def run(self) -> None:
        """Запуск бота (polling, для локальной разработки)."""
        job_queue = self.application.job_queue
        job_queue.run_repeating(
            self.job_check_new_articles,
            interval=timedelta(minutes=JOB_CHECK_INTERVAL_MINUTES),
            first=15,
        )
        logger.info("Бот запущен (polling)")
        self.application.run_polling()

    def get_application(self):
        """Возвращает Application для использования в webhook."""
        return self.application


def build_application(token: str = None) -> Application:
    """
    Сборка Application с handlers. Используется для webhook (Vercel).
    Token берётся из аргумента или BOT_TOKEN.
    """
    t = token or BOT_TOKEN
    if not t:
        raise ValueError("BOT_TOKEN не задан")
    bot_instance = OkolicaBot(t)
    return bot_instance.get_application()


def main() -> None:
    """Точка входа."""
    if not BOT_TOKEN:
        print("❗ Токен бота не найден!")
        print("Создайте файл .env с TELEGRAM_BOT_TOKEN=ваш_токен")
        return

    bot = OkolicaBot(BOT_TOKEN)
    bot.run()


if __name__ == "__main__":
    main()
