import os
import re
import logging
import requests
from openai import OpenAI
from youtube_transcript_api import YouTubeTranscriptApi
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://ai.externcashpn.cv/v1")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4")
SUPADATA_API_KEY = os.environ.get("SUPADATA_API_KEY")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
PORT = int(os.environ.get("PORT", 8080))

client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)


def get_video_id(url: str) -> str:
    match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
    if match:
        return match.group(1)
    raise ValueError("Не удалось найти ID видео")


def get_transcript(video_id: str) -> str | None:
    # 1) Основной источник: youtube-transcript-api
    text = get_transcript_from_youtube_transcript_api(video_id)
    if text:
        logging.info("Transcript source: youtube-transcript-api")
        return text

    # 2) Если не получилось, используем SUPADATA
    text = get_transcript_from_supadata(video_id)
    if text:
        logging.info("Transcript source: SUPADATA")
    return text


def get_transcript_from_youtube_transcript_api(video_id: str) -> str | None:
    try:
        # Совместимость с разными версиями youtube-transcript-api:
        # - старые: YouTubeTranscriptApi.get_transcript(...)
        # - новые: YouTubeTranscriptApi().fetch(...)
        if hasattr(YouTubeTranscriptApi, "get_transcript"):
            transcript_items = YouTubeTranscriptApi.get_transcript(
                video_id,
                languages=["ru", "en"],
            )
        else:
            transcript = YouTubeTranscriptApi().fetch(video_id, languages=["ru", "en"])
            transcript_items = list(transcript)

        if not transcript_items:
            return None
        return " ".join(
            (item.get("text", "") if isinstance(item, dict) else getattr(item, "text", ""))
            for item in transcript_items
        ).strip()
    except Exception as e:
        logging.warning(f"youtube-transcript-api failed: {e}")
        return None


def get_transcript_from_supadata(video_id: str) -> str | None:
    if not SUPADATA_API_KEY:
        logging.warning("SUPADATA_API_KEY is not set")
        return None

    try:
        url = "https://api.supadata.ai/v1/youtube/transcript"
        headers = {"x-api-key": SUPADATA_API_KEY}
        params = {"videoId": video_id, "text": True}
        response = requests.get(url, headers=headers, params=params, timeout=30)
        if response.status_code != 200:
            logging.error(f"Supadata error: {response.status_code} {response.text}")
            return None
        data = response.json()
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            content = data.get("content") or data.get("transcript") or data.get("text")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(s.get("text", "") for s in content)
    except Exception as e:
        logging.error(f"Supadata request failed: {e}")
    return None


def summarize(text: str) -> str:
    prompt = f"""Ты помощник для анализа видео. Тебе дан текст субтитров видео.

Сделай краткий конспект на русском языке в таком формате:

🎯 **О чём видео** (2-3 предложения)

📌 **Ключевые моменты** (3-7 пунктов)

✅ **Стоит ли смотреть?** (одна фраза — для кого подойдёт это видео)

Текст субтитров:
{text[:12000]}
"""
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1000,
    )
    return response.choices[0].message.content


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Отправь мне ссылку на YouTube видео — я сделаю краткий конспект и скажу, стоит ли его смотреть."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if "youtube.com" not in url and "youtu.be" not in url:
        await update.message.reply_text("Пожалуйста, отправь ссылку на YouTube видео.")
        return

    await update.message.reply_text("⏳ Загружаю субтитры...")

    try:
        video_id = get_video_id(url)
    except ValueError:
        await update.message.reply_text("Не могу найти ID видео. Проверь ссылку.")
        return

    text = get_transcript(video_id)

    if not text or len(text) < 100:
        await update.message.reply_text("😕 Субтитры не найдены для этого видео.")
        return

    await update.message.reply_text("🤖 Анализирую содержание...")

    try:
        summary = summarize(text)
        await update.message.reply_text(summary, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Ошибка summarize: {e}")
        await update.message.reply_text("Ошибка при анализе. Попробуй ещё раз.")


async def error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    logging.error(f"Ошибка: {context.error}")


if __name__ == "__main__":
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook?drop_pending_updates=true")
        logging.info(f"deleteWebhook: {r.json()}")
    except Exception as e:
        logging.warning(f"deleteWebhook failed: {e}")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    if WEBHOOK_URL:
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path="webhook",
            webhook_url=f"{WEBHOOK_URL}/webhook",
        )
    else:
        app.run_polling(drop_pending_updates=True)
