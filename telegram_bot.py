import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.error import Conflict

from config import TELEGRAM_TOKEN, WEB_APP_BASE_URL, BOT_PROCESS_LOCK_PATH
from services import (
    get_video_id,
    get_saved_summary_for_user,
    get_transcript,
    summarize_with_multi_agent_pipeline,
    save_summary_to_supabase,
    save_feedback_to_supabase,
)

logger = logging.getLogger(__name__)


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

    summary_id, vote = parts[1], parts[2]
    liked = vote == "up"
    save_feedback_to_supabase(update, summary_id, liked)
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception as exc:
        logger.warning(f"Feedback markup edit failed: {exc}")
    if query.message:
        await query.message.reply_text("Спасибо за оценку! ✅")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip() if update.message and update.message.text else ""

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

    await update.message.reply_text("⏳ Загружаю субтитры...")

    try:
        video_id = get_video_id(text)
    except ValueError:
        return await update.message.reply_text("Не могу найти ID видео. Проверь ссылку.")

    saved = get_saved_summary_for_user(update, video_id)
    if saved:
        await update.message.reply_text(
            "📌 Этот ролик уже сохранён в твоих конспектах. Вот существующий конспект:",
            parse_mode="Markdown",
        )
        return await update.message.reply_text(saved.get("summary_markdown", ""), parse_mode="Markdown")

    transcript, source = get_transcript(video_id)
    if not transcript or len(transcript) < 100:
        return await update.message.reply_text("😕 Субтитры не найдены для этого видео.")

    await update.message.reply_text("🤖 Анализирую содержание...")

    try:
        summary, chunk_count = summarize_with_multi_agent_pipeline(transcript)
        await update.message.reply_text(summary, parse_mode="Markdown")
        if source:
            summary_id = save_summary_to_supabase(update, video_id, text, summary, chunk_count, source)
            if summary_id:
                keyboard = InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton("👍 Полезно", callback_data=f"fb:{summary_id}:up"),
                        InlineKeyboardButton("👎 Слабо", callback_data=f"fb:{summary_id}:down"),
                    ]]
                )
                return await update.message.reply_text("Оцени конспект:", reply_markup=keyboard)
    except Exception as exc:
        logger.error(f"Ошибка summarize: {exc}")
        return await update.message.reply_text("Ошибка при анализе. Попробуй ещё раз.")


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
