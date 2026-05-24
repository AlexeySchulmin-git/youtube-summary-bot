import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

from config import TELEGRAM_TOKEN, WEB_APP_BASE_URL
from services import (
    get_video_id,
    get_saved_summary_for_user,
    get_transcript,
    summarize_with_multi_agent_pipeline,
    save_summary_to_supabase,
    save_feedback_to_supabase,
)

logger = logging.getLogger(__name__)

MAIN_MENU = ReplyKeyboardMarkup(
    [["📚 Мои конспекты"], ["📖 Справка"]],
    resize_keyboard=True,
    is_persistent=True,
)


def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return update.message.reply_text(
        "👋 Привет! Отправь мне ссылку на YouTube видео — я сделаю краткий конспект и выделю важные вещи из видео.",
        reply_markup=MAIN_MENU,
    )


def my_summaries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return update.message.reply_text("История пока недоступна.")

    try:
        user = update.effective_user
        page_base = WEB_APP_BASE_URL.rstrip("/") if WEB_APP_BASE_URL else None
        page_url = f"{page_base}/u/{user.id}" if page_base else None

        if page_url:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🌐 Открыть страницу конспектов", url=page_url)]])
            return update.message.reply_text(
                "🗂 Твоя страница конспектов готова. Открывай:",
                reply_markup=kb,
            )

        return update.message.reply_text(
            "Укажи WEB_APP_BASE_URL, чтобы открывать страницу конспектов.",
            reply_markup=MAIN_MENU,
        )
    except Exception as exc:
        logger.warning(f"My summaries failed: {exc}")
        return update.message.reply_text("Не удалось загрузить историю.")


def feedback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    query.answer("Спасибо за оценку!")
    data = query.data or ""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "fb":
        return

    summary_id, vote = parts[1], parts[2]
    liked = vote == "up"
    save_feedback_to_supabase(update, summary_id, liked)
    try:
        query.edit_message_reply_markup(reply_markup=None)
    except Exception as exc:
        logger.warning(f"Feedback markup edit failed: {exc}")
    if query.message:
        query.message.reply_text("Спасибо за оценку! ✅")


def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip() if update.message and update.message.text else ""

    if text == "📚 Мои конспекты":
        return my_summaries(update, context)

    if text == "📖 Справка":
        return update.message.reply_text(
            "Я делаю конспекты YouTube-видео по ссылке.\n"
            "1) Получаю субтитры\n"
            "2) Разбиваю на чанки\n"
            "3) Собираю итоговый конспект\n"
            "4) Сохраняю в твою историю\n\n"
            "Нажми «📚 Мои конспекты», чтобы открыть список.",
            reply_markup=MAIN_MENU,
        )

    if "youtube.com" not in text and "youtu.be" not in text:
        return update.message.reply_text("Пожалуйста, отправь ссылку на YouTube видео.")

    update.message.reply_text("⏳ Загружаю субтитры...")

    try:
        video_id = get_video_id(text)
    except ValueError:
        return update.message.reply_text("Не могу найти ID видео. Проверь ссылку.")

    saved = get_saved_summary_for_user(update, video_id)
    if saved:
        update.message.reply_text(
            "📌 Этот ролик уже сохранён в твоих конспектах. Вот существующий конспект:",
            parse_mode="Markdown",
        )
        return update.message.reply_text(saved.get("summary_markdown", ""), parse_mode="Markdown")

    transcript, source = get_transcript(video_id)
    if not transcript or len(transcript) < 100:
        return update.message.reply_text("😕 Субтитры не найдены для этого видео.")

    update.message.reply_text("🤖 Анализирую содержание...")

    try:
        summary, chunk_count = summarize_with_multi_agent_pipeline(transcript)
        update.message.reply_text(summary, parse_mode="Markdown")
        if source:
            summary_id = save_summary_to_supabase(update, video_id, text, summary, chunk_count, source)
            if summary_id:
                keyboard = InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton("👍 Полезно", callback_data=f"fb:{summary_id}:up"),
                        InlineKeyboardButton("👎 Слабо", callback_data=f"fb:{summary_id}:down"),
                    ]]
                )
                return update.message.reply_text("Оцени конспект:", reply_markup=keyboard)
    except Exception as exc:
        logger.error(f"Ошибка summarize: {exc}")
        return update.message.reply_text("Ошибка при анализе. Попробуй ещё раз.")


def error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Ошибка: {context.error}")


def create_application():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("my", my_summaries))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(feedback_callback, pattern=r"^fb:"))
    app.add_error_handler(error_handler)
    return app


def run_telegram_bot():
    app = create_application()
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
