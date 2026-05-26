import logging
import os
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.error import Conflict

from config import TELEGRAM_TOKEN, WEB_APP_BASE_URL, BOT_PROCESS_LOCK_PATH
from services import (
    get_video_id,
    get_video_title,
    generate_ai_title,
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
    [["📚 Мои конспекты"], ["📖 Справка"]],
    resize_keyboard=True,
    is_persistent=True,
)


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
    user_id = update.effective_user.id if update.effective_user else None

    if text == "📚 Мои конспекты":
        return await my_summaries(update, context)

    if text == "📖 Справка":
        return await update.message.reply_text(
            "Я делаю конспекты YouTube-видео по ссылке.\n"
            "1) Получаю субтитры\n"
            "2) Разбиваю на чанки\n"
            "3) Собираю итоговый конспект\n"
            "4) Сохраняю в твою историю\n\n"
            "Нажми «📚 Мои конспекты», чтобы открыть список.",
            reply_markup=MAIN_MENU,
        )

    if "youtube.com" not in text and "youtu.be" not in text:
        return await update.message.reply_text("Пожалуйста, отправь ссылку на YouTube видео.")

    if user_id is not None:
        async with _ACTIVE_USERS_LOCK:
            if user_id in _ACTIVE_USERS:
                return await update.message.reply_text("⏳ Уже обрабатываю предыдущее видео. Дождись завершения.")
            _ACTIVE_USERS.add(user_id)

    try:
        progress_msg = await update.message.reply_text("⏳ Загружаю текст...")

        try:
            video_id = get_video_id(text)
        except ValueError:
            return await update.message.reply_text("Не могу найти ID видео. Проверь ссылку.")

        saved = get_saved_summary_for_user(update, video_id)
        if saved:
            try:
                await progress_msg.delete()
            except Exception:
                pass
            await update.message.reply_text(
                "📌 Этот ролик уже сохранён в твоих конспектах. Вот существующий конспект:",
                parse_mode="Markdown",
            )
            await update.message.reply_text(saved.get("summary_markdown", ""), parse_mode="Markdown")
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
                return await update.message.reply_text("Оцени конспект:", reply_markup=keyboard)
            return

        transcript, source = get_transcript(video_id)
        if not transcript or len(transcript) < 100:
            try:
                await progress_msg.delete()
            except Exception:
                pass
            return await update.message.reply_text("😕 Субтитры не найдены для этого видео.")

        video_title = get_video_title(text)

        await progress_msg.edit_text("🧩 Разбиваю текст на части...")
        chunks = chunk_transcript(transcript, target_tokens=2500, max_tokens=3000, overlap_tokens=200)
        if not chunks:
            try:
                await progress_msg.delete()
            except Exception:
                pass
            return await update.message.reply_text("Ошибка при обработке текста. Попробуй ещё раз.")

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

        await update.message.reply_text(summary, parse_mode="Markdown")
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
                return await update.message.reply_text("Оцени конспект:", reply_markup=keyboard)
    except Exception as exc:
        logger.error(f"Ошибка summarize: {exc}")
        return await update.message.reply_text("Ошибка при анализе. Попробуй ещё раз.")
    finally:
        if user_id is not None:
            async with _ACTIVE_USERS_LOCK:
                _ACTIVE_USERS.discard(user_id)


async def error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, Conflict):
        logger.warning("Conflict in getUpdates: another bot instance is using the same token.")
        return
    logger.error(f"Ошибка: {err}")


def create_application():
    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .connect_timeout(20)
        .read_timeout(25)
        .write_timeout(25)
        .pool_timeout(15)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("my", my_summaries))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
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
