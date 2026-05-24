import os
import re
import logging
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from openai import OpenAI
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://ai.externcashpn.cv/v1")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4")
SUPADATA_API_KEY = os.environ.get("SUPADATA_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)


def get_video_id(url: str) -> str:
    match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
    if match:
        return match.group(1)
    raise ValueError("Не удалось найти ID видео")


def get_transcript(video_id: str) -> str | None:
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
        logging.error(e)
        await update.message.reply_text("Ошибка при анализе. Попробуй ещё раз.")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args):
        pass

def run_http():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    threading.Thread(target=run_http, daemon=True).start()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()
