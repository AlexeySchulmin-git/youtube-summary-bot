import os
import re
import logging
import requests
from openai import OpenAI
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://ai.externcashpn.cv/v1")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
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
    # 1) Пытаемся получить субтитры через официальный YouTube Data API (по ключу)
    text = get_transcript_from_youtube_api(video_id)
    if text:
        logging.info("Transcript source: YouTube Data API")
        return text

    # 2) Если не получилось, используем SUPADATA
    text = get_transcript_from_supadata(video_id)
    if text:
        logging.info("Transcript source: SUPADATA")
    return text


def _strip_srt_timestamps(srt_text: str) -> str:
    lines = []
    for line in srt_text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        if cleaned.isdigit():
            continue
        if "-->" in cleaned:
            continue
        lines.append(cleaned)
    return " ".join(lines)


def get_transcript_from_youtube_api(video_id: str) -> str | None:
    if not YOUTUBE_API_KEY:
        logging.warning("YOUTUBE_API_KEY is not set, skipping YouTube API transcript")
        return None

    try:
        list_url = "https://www.googleapis.com/youtube/v3/captions"
        list_params = {
            "part": "snippet",
            "videoId": video_id,
            "key": YOUTUBE_API_KEY,
        }
        list_resp = requests.get(list_url, params=list_params, timeout=30)
        if list_resp.status_code != 200:
            logging.warning(
                "YouTube captions list error: %s %s",
                list_resp.status_code,
                list_resp.text,
            )
            return None

        items = list_resp.json().get("items", [])
        if not items:
            logging.info("YouTube captions list is empty")
            return None

        # Пытаемся сначала взять русские субтитры, потом любые
        selected = None
        for item in items:
            lang = (item.get("snippet") or {}).get("language")
            if lang == "ru":
                selected = item
                break
        if not selected:
            selected = items[0]

        caption_id = selected.get("id")
        if not caption_id:
            return None

        download_url = f"https://www.googleapis.com/youtube/v3/captions/{caption_id}"
        download_params = {
            "tfmt": "srt",
            "key": YOUTUBE_API_KEY,
        }
        download_resp = requests.get(download_url, params=download_params, timeout=30)
        if download_resp.status_code != 200:
            logging.warning(
                "YouTube captions download error: %s %s",
                download_resp.status_code,
                download_resp.text,
            )
            return None

        srt_text = download_resp.text.strip()
        if not srt_text:
            return None
        return _strip_srt_timestamps(srt_text)
    except Exception as e:
        logging.warning(f"YouTube transcript fetch failed: {e}")
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
