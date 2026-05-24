import os
import re
import subprocess
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import google.generativeai as genai
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
YOUTUBE_COOKIES = os.environ.get("YOUTUBE_COOKIES")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

COOKIES_PATH = "/tmp/cookies.txt"

# Записываем куки в файл при старте
if YOUTUBE_COOKIES:
    with open(COOKIES_PATH, "w", encoding="utf-8") as f:
        f.write(YOUTUBE_COOKIES)
    logging.info("Куки загружены")
else:
    logging.warning("YOUTUBE_COOKIES не задан")


def get_video_id(url: str) -> str:
    match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
    if match:
        return match.group(1)
    raise ValueError("Не удалось найти ID видео")


def download_subtitles(video_id: str) -> str | None:
    tmp = f"/tmp/sub_{video_id}"
    cmd = [
        "yt-dlp",
        "--skip-download",
        "--write-auto-sub",
        "--write-sub",
        "--sub-langs", "ru,en,uk,de,fr,es,it,pl,pt,tr,ja,ko,zh-Hans",
        "--convert-subs", "vtt",
        "--output", tmp,
        f"https://www.youtube.com/watch?v={video_id}"
    ]

    if YOUTUBE_COOKIES and os.path.exists(COOKIES_PATH):
        cmd += ["--cookies", COOKIES_PATH]

    result = subprocess.run(cmd, capture_output=True, text=True)
    logging.info(result.stdout[-500:] if result.stdout else "no stdout")
    logging.info(result.stderr[-500:] if result.stderr else "no stderr")

    for f in os.listdir("/tmp"):
        if f.startswith(f"sub_{video_id}") and f.endswith(".vtt"):
            path = f"/tmp/{f}"
            with open(path, encoding="utf-8") as file:
                raw = file.read()
            os.remove(path)
            return parse_vtt(raw)
    return None


def parse_vtt(raw: str) -> str:
    lines = raw.splitlines()
    text_lines = []
    prev = ""
    for line in lines:
        line = line.strip()
        if not line or line.startswith("WEBVTT") or "-->" in line or re.match(r"^\d+$", line):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        line = re.sub(r"&amp;", "&", line)
        if line and line != prev:
            text_lines.append(line)
            prev = line
    return " ".join(text_lines)


def summarize(text: str) -> str:
    prompt = f"""Ты помощник для анализа видео. Тебе дан текст субтитров видео.

Сделай краткий конспект на русском языке в таком формате:

🎯 **О чём видео** (2-3 предложения)

📌 **Ключевые моменты** (3-7 пунктов)

✅ **Стоит ли смотреть?** (одна фраза — для кого подойдёт это видео)

Текст субтитров:
{text[:12000]}
"""
    response = model.generate_content(prompt)
    return response.text


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

    text = download_subtitles(video_id)

    if not text or len(text) < 100:
        await update.message.reply_text("😕 Субтитры не найдены. Проверь логи на Render.")
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
