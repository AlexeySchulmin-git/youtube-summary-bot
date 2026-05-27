import logging
import os
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, BotCommand, MenuButtonCommands
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.error import Conflict

from config import TELEGRAM_TOKEN, WEB_APP_BASE_URL, BOT_PROCESS_LOCK_PATH
from services import (
    get_video_id,
    get_video_title,
    generate_ai_title,
    search_youtube_videos,
    get_youtube_query_suggestions,
    get_saved_summary_for_user,
    get_transcript,
    chunk_transcript,
    analyze_chunk,
    synthesize_analyses,
    save_summary_to_supabase,
    save_feedback_to_supabase,
)
from quality_agent import evaluate_and_evolve

logger = logging.getLogger(__name__)
_ACTIVE_USERS: set[int] = set()
_ACTIVE_USERS_LOCK = asyncio.Lock()
_GLOBAL_CONCURRENCY_LIMIT = 2
_GLOBAL_WORKERS = asyncio.Semaphore(_GLOBAL_CONCURRENCY_LIMIT)
_WAITING_COUNT = 0
_WAITING_LOCK = asyncio.Lock()
_CONFLICT_EXIT_TRIGGERED = False


class BotProcessLock:
    def __init__(self, path: str):
        self.path = path
        self.fd = None

    def acquire(self) -> bool:
        # Cleanup stale lock file (e.g. previous crash/redeploy)
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    raw_pid = f.read().strip()
                stale = True
                if raw_pid.isdigit():
                    pid = int(raw_pid)
                    try:
                        os.kill(pid, 0)
                        stale = False
                    except OSError:
                        stale = True
                if stale:
                    os.remove(self.path)
        except Exception as exc:
            logger.warning(f"Failed to validate existing lock file: {exc}")

        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(self.fd, str(os.getpid()).encode("utf-8"))
            os.fsync(self.fd)
            return True
        except FileExistsError:
            return False

    def release(self):
        try:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            if os.path.exists(self.path):
                os.remove(self.path)
        except Exception as exc:
            logger.warning(f"Failed to release bot process lock: {exc}")

MAIN_MENU = ReplyKeyboardMarkup(
    [["📚 Мои конспекты"], ["🔎 Поиск YouTube"], ["💡 Предложения и обратная связь"], ["💙 Поддержать проект"], ["📖 Справка"]],
    resize_keyboard=True,
    is_persistent=True,
)

SEARCH_MENU_TEXT = "🔎 Поиск YouTube"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await update.message.reply_text(
        "👋 Привет! Отправь мне ссылку на YouTube видео — я сделаю краткий конспект и выделю важные вещи из видео.",
        reply_markup=MAIN_MENU,
    )


async def my_summaries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return await update.message.reply_text("История пока недоступна.")

    try:
        user = update.effective_user
        page_base = WEB_APP_BASE_URL.rstrip("/") if WEB_APP_BASE_URL else None
        page_url = f"{page_base}/u/{user.id}" if page_base else None

        if page_url:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🌐 Открыть страницу конспектов", url=page_url)]])
            return await update.message.reply_text(
                "🗂 Твоя страница конспектов готова. Открывай:",
                reply_markup=kb,
            )

        return await update.message.reply_text(
            "Укажи WEB_APP_BASE_URL, чтобы открывать страницу конспектов.",
            reply_markup=MAIN_MENU,
        )
    except Exception as exc:
        logger.warning(f"My summaries failed: {exc}")
        return await update.message.reply_text("Не удалось загрузить историю.")


async def support_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = update.effective_message
    if not target:
        return

    support_url = "https://yoomoney.ru/fundraise/1HVB3RU66AC.260524"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("💙 Поддержать проект", url=support_url)]])
    return await target.reply_text(
        "Проект будет улучшаться дальше: новые функции, лучшее качество конспектов и удобство сервиса.\n"
        "Для этого нужны расходы на серверы и ИИ-модели.\n"
        "Если хотите помочь развитию — поддержите проект:",
        reply_markup=kb,
    )


async def feedback_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = update.effective_message
    if not target:
        return

    personal_contact = "https://t.me/alexeyshulmin"
    feedback_channel = "https://t.me/notes_youtube"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ Написать в личку", url=personal_contact)],
        [InlineKeyboardButton("📢 Канал с новостями", url=feedback_channel)],
    ])

    return await target.reply_text(
        "Есть идея или ошибка? Напишите в личку — так быстрее разберём конкретный кейс.\n"
        "Канал лучше использовать для общих анонсов и сбора реакций.",
        reply_markup=kb,
    )


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    query = " ".join(context.args).strip() if context.args else ""
    if not query:
        context.user_data["awaiting_search_query"] = True
        return await update.message.reply_text("Напиши поисковый запрос для YouTube (можно неполный, покажу подсказки).")

    try:
        result = search_youtube_videos(query, page_token=None, max_results=5)
    except Exception as exc:
        logger.warning(f"YouTube search failed: {exc}")
        return await update.message.reply_text("Поиск временно недоступен. Проверь YOUTUBE_API_KEY и попробуй позже.")

    items = result.get("items") or []
    if not items:
        return await update.message.reply_text("Ничего не найдено. Попробуй изменить запрос.")

    await update.message.reply_text(f"🔎 Результаты по запросу: {query}")
    for i, item in enumerate(items, start=1):
        title = item.get("title") or "Без названия"
        channel = item.get("channel") or "Неизвестный канал"
        published = (item.get("published_at") or "")[:10]
        duration = item.get("duration") or "—"
        url = item.get("url") or ""
        video_id = item.get("video_id") or ""

        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("📝 Сделать конспект", callback_data=f"sg:{video_id}")]]
        )
        await update.message.reply_text(
            f"{i}. {title}\n👤 {channel}\n⏱ {duration}\n📅 {published}\n{url}",
            reply_markup=kb,
        )


async def search_generate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    data = query.data or ""
    parts = data.split(":", 1)
    if len(parts) != 2 or parts[0] != "sg":
        return

    video_id = parts[1].strip()
    if not video_id:
        return

    await query.answer("Запускаю конспект...")
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    fake_url = f"https://www.youtube.com/watch?v={video_id}"
    if query.message:
        await query.message.reply_text(f"Принято, обрабатываю: {fake_url}")
    await _process_video_request(update, context, fake_url)


async def _process_video_request(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    user_id = update.effective_user.id if update.effective_user else None

    if user_id is not None:
        async with _ACTIVE_USERS_LOCK:
            if user_id in _ACTIVE_USERS:
                target = update.effective_message
                if target:
                    await target.reply_text("⏳ Уже обрабатываю предыдущее видео. Дождись завершения.")
                return
            _ACTIVE_USERS.add(user_id)

    try:
        worker_acquired = False
        queue_position = 1
        async with _WAITING_LOCK:
            global _WAITING_COUNT
            _WAITING_COUNT += 1
            queue_position = _WAITING_COUNT

        target = update.effective_message
        if not target:
            return

        progress_msg = await target.reply_text(
            f"⏳ Ожидание очереди: позиция {queue_position}."
        )

        await _GLOBAL_WORKERS.acquire()
        worker_acquired = True
        async with _WAITING_LOCK:
            _WAITING_COUNT = max(0, _WAITING_COUNT - 1)

        await progress_msg.edit_text("⏳ Загружаю текст...")

        try:
            video_id = get_video_id(text)
        except ValueError:
            return await target.reply_text("Не могу найти ID видео. Проверь ссылку.")

        saved = get_saved_summary_for_user(update, video_id)
        if saved:
            try:
                await progress_msg.delete()
            except Exception:
                pass
            await target.reply_text(
                "📌 Этот ролик уже сохранён в твоих конспектах. Вот существующий конспект:",
                parse_mode="Markdown",
            )
            await target.reply_text(saved.get("summary_markdown", ""), parse_mode="Markdown")
            summary_id = saved.get("id")
            if summary_id:
                keyboard = InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton("1", callback_data=f"fb:{summary_id}:1"),
                        InlineKeyboardButton("2", callback_data=f"fb:{summary_id}:2"),
                        InlineKeyboardButton("3", callback_data=f"fb:{summary_id}:3"),
                        InlineKeyboardButton("4", callback_data=f"fb:{summary_id}:4"),
                        InlineKeyboardButton("5", callback_data=f"fb:{summary_id}:5"),
                    ]]
                )
                return await target.reply_text("Оцени конспект:", reply_markup=keyboard)
            return

        transcript, source = get_transcript(video_id)
        if not transcript or len(transcript) < 100:
            try:
                await progress_msg.delete()
            except Exception:
                pass
            return await target.reply_text(
                "😕 Не удалось получить текст видео сейчас. Возможен временный лимит источников (429). Попробуй через 2–3 минуты."
            )

        video_title = get_video_title(text)

        await progress_msg.edit_text("🧩 Разбиваю текст на части...")
        chunks = chunk_transcript(transcript, target_tokens=2500, max_tokens=3000, overlap_tokens=200)
        if not chunks:
            try:
                await progress_msg.delete()
            except Exception:
                pass
            return await target.reply_text("Ошибка при обработке текста. Попробуй ещё раз.")

        total = len(chunks)
        analyses: list[str] = []
        await progress_msg.edit_text(f"🤖 Анализирую содержание: 0/{total}")

        for idx, chunk in enumerate(chunks, start=1):
            await progress_msg.edit_text(f"🤖 Анализирую содержание: {idx}/{total}")
            analyses.append(analyze_chunk(chunk, idx, total, video_title=video_title))

        await progress_msg.edit_text("🧠 Собираю итоговый конспект...")
        summary = synthesize_analyses(analyses, video_title=video_title)
        chunk_count = total
        ai_title = generate_ai_title(transcript, summary, fallback_title=video_title)

        await target.reply_text(summary, parse_mode="Markdown")
        try:
            await progress_msg.delete()
        except Exception:
            pass

        try:
            quality_result = evaluate_and_evolve(video_id=video_id, transcript=transcript, summary=summary)
            if quality_result.get("enabled"):
                item = quality_result.get("item") or {}
                score = (item.get("scores") or {}).get("overall")
                logger.info(f"Quality eval completed. overall={score}")
        except Exception as q_exc:
            logger.warning(f"Quality evaluation failed: {q_exc}")

        if source:
            summary_id = save_summary_to_supabase(
                update,
                video_id,
                text,
                summary,
                chunk_count,
                source,
                ai_title=ai_title,
            )
            if summary_id:
                keyboard = InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton("1", callback_data=f"fb:{summary_id}:1"),
                        InlineKeyboardButton("2", callback_data=f"fb:{summary_id}:2"),
                        InlineKeyboardButton("3", callback_data=f"fb:{summary_id}:3"),
                        InlineKeyboardButton("4", callback_data=f"fb:{summary_id}:4"),
                        InlineKeyboardButton("5", callback_data=f"fb:{summary_id}:5"),
                    ]]
                )
                return await target.reply_text("Оцени конспект:", reply_markup=keyboard)
    except Exception as exc:
        logger.error(f"Ошибка summarize: {exc}")
        target = update.effective_message
        if target:
            return await target.reply_text("Ошибка при анализе. Попробуй ещё раз.")
    finally:
        if 'worker_acquired' in locals() and worker_acquired:
            try:
                _GLOBAL_WORKERS.release()
            except Exception:
                pass
        if user_id is not None:
            async with _ACTIVE_USERS_LOCK:
                _ACTIVE_USERS.discard(user_id)


async def feedback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer("Спасибо за оценку!")
    data = query.data or ""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "fb":
        return

    summary_id, rating_raw = parts[1], parts[2]
    try:
        rating = max(1, min(5, int(rating_raw)))
    except Exception:
        return

    save_feedback_to_supabase(update, summary_id, rating)
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception as exc:
        logger.warning(f"Feedback markup edit failed: {exc}")
    if query.message:
        await query.message.reply_text(f"Спасибо! Вы оценили конспект на {rating}/5 ✅")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip() if update.message and update.message.text else ""

    if text == "📚 Мои конспекты":
        return await my_summaries(update, context)

    if text == SEARCH_MENU_TEXT:
        context.user_data["awaiting_search_query"] = True
        return await update.message.reply_text("Напиши поисковый запрос для YouTube (можно неполный, покажу подсказки).")

    if text == "💡 Предложения и обратная связь":
        return await feedback_entry(update, context)

    if text == "💙 Поддержать проект":
        return await support_project(update, context)

    if text == "↩ Назад":
        context.user_data.pop("awaiting_search_query", None)
        return await update.message.reply_text("Главное меню", reply_markup=MAIN_MENU)

    if text == "📖 Справка":
        context.user_data.pop("awaiting_search_query", None)
        return await update.message.reply_text(
            "Я делаю конспекты YouTube-видео по ссылке.\n"
            "1) Получаю субтитры\n"
            "2) Разбиваю на чанки\n"
            "3) Собираю итоговый конспект\n"
            "4) Сохраняю в твою историю\n"
            "5) Могу найти видео, команда /search или пункт в меню.\n\n"
            "Нажми «📚 Мои конспекты», чтобы открыть список.",
            reply_markup=MAIN_MENU,
        )

    if context.user_data.get("awaiting_search_query"):
        query = text.strip()
        if not query:
            return await update.message.reply_text("Пустой запрос. Введи текст для поиска.")

        suggestions = get_youtube_query_suggestions(query, limit=5)
        logger.info(f"Search query received: '{query}', suggestions_count={len(suggestions)}")
        if suggestions:
            kb_rows = [
                [InlineKeyboardButton(s[:64], callback_data=f"sq:{idx}")]
                for idx, s in enumerate(suggestions)
            ]
            context.user_data["search_suggestions"] = suggestions
            await update.message.reply_text(
                "Подсказки по запросу (можно выбрать):",
                reply_markup=InlineKeyboardMarkup(kb_rows),
            )
        else:
            logger.warning(f"No suggestions returned for query='{query}'")

        context.user_data["awaiting_search_query"] = False
        try:
            result = search_youtube_videos(query, page_token=None, max_results=5)
        except Exception as exc:
            logger.warning(f"YouTube search failed: {exc}")
            return await update.message.reply_text("Поиск временно недоступен. Проверь YOUTUBE_API_KEY и попробуй позже.")

        items = result.get("items") or []
        if not items:
            return await update.message.reply_text("Ничего не найдено. Попробуй изменить запрос.")

        await update.message.reply_text(f"🔎 Результаты по запросу: {query}")
        for i, item in enumerate(items, start=1):
            title = item.get("title") or "Без названия"
            channel = item.get("channel") or "Неизвестный канал"
            published = (item.get("published_at") or "")[:10]
            duration = item.get("duration") or "—"
            url = item.get("url") or ""
            video_id = item.get("video_id") or ""

            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("📝 Сделать конспект", callback_data=f"sg:{video_id}")]]
            )
            await update.message.reply_text(
                f"{i}. {title}\n👤 {channel}\n⏱ {duration}\n📅 {published}\n{url}",
                reply_markup=kb,
            )
        return

    if "youtube.com" not in text and "youtu.be" not in text:
        return await update.message.reply_text("Пожалуйста, отправь ссылку на YouTube видео.")

    return await _process_video_request(update, context, text)


async def error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    global _CONFLICT_EXIT_TRIGGERED
    err = context.error
    if isinstance(err, Conflict):
        if not _CONFLICT_EXIT_TRIGGERED:
            _CONFLICT_EXIT_TRIGGERED = True
            logger.error("Conflict in getUpdates: another bot instance is using the same token. Exiting this process.")
            raise SystemExit(0)
        return
    logger.error(f"Ошибка: {err}")


async def _post_init(application):
    try:
        await application.bot.set_my_commands([
            BotCommand("start", "Запустить бота"),
            BotCommand("search", "Поиск видео YouTube"),
            BotCommand("my", "Мои конспекты"),
            BotCommand("feedback", "Предложения и обратная связь"),
            BotCommand("support", "Поддержать проект"),
        ])
        await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception as exc:
        logger.warning(f"Failed to configure bot menu: {exc}")


async def search_suggestion_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    data = query.data or ""
    parts = data.split(":", 1)
    if len(parts) != 2 or parts[0] != "sq":
        return

    try:
        idx = int(parts[1])
    except Exception:
        return

    suggestions = context.user_data.get("search_suggestions") or []
    if not (0 <= idx < len(suggestions)):
        return

    selected = (suggestions[idx] or "").strip()
    if not selected:
        return

    await query.answer(f"Выбрано: {selected}")
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    try:
        result = search_youtube_videos(selected, page_token=None, max_results=5)
    except Exception as exc:
        logger.warning(f"YouTube search failed from suggestion: {exc}")
        if query.message:
            await query.message.reply_text("Поиск временно недоступен. Проверь YOUTUBE_API_KEY и попробуй позже.")
        return

    items = result.get("items") or []
    if query.message:
        if not items:
            await query.message.reply_text("Ничего не найдено. Попробуй изменить запрос.")
            return

        await query.message.reply_text(f"🔎 Результаты по запросу: {selected}")
        for i, item in enumerate(items, start=1):
            title = item.get("title") or "Без названия"
            channel = item.get("channel") or "Неизвестный канал"
            published = (item.get("published_at") or "")[:10]
            duration = item.get("duration") or "—"
            url = item.get("url") or ""
            video_id = item.get("video_id") or ""
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("📝 Сделать конспект", callback_data=f"sg:{video_id}")]]
            )
            await query.message.reply_text(
                f"{i}. {title}\n👤 {channel}\n⏱ {duration}\n📅 {published}\n{url}",
                reply_markup=kb,
            )


def create_application():
    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .connect_timeout(20)
        .read_timeout(25)
        .write_timeout(25)
        .pool_timeout(15)
        .post_init(_post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("my", my_summaries))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("feedback", feedback_entry))
    app.add_handler(CommandHandler("support", support_project))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(search_suggestion_callback, pattern=r"^sq:"))
    app.add_handler(CallbackQueryHandler(search_generate_callback, pattern=r"^sg:"))
    app.add_handler(CallbackQueryHandler(feedback_callback, pattern=r"^fb:"))
    app.add_error_handler(error_handler)
    return app


def run_telegram_bot():
    lock = BotProcessLock(BOT_PROCESS_LOCK_PATH)
    if not lock.acquire():
        logger.warning("Telegram polling is skipped: another process lock is active.")
        return

    app = create_application()
    try:
        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
            close_loop=False,
            stop_signals=None,
            bootstrap_retries=3,
        )
    except Conflict:
        logger.error("Bot polling stopped: Conflict (another instance is already running).")
    finally:
        lock.release()
