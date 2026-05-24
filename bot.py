import os
import re
import logging
import requests
from openai import OpenAI
from youtube_transcript_api import YouTubeTranscriptApi
from supabase import create_client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://ai.externcashpn.cv/v1")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4")
SUPADATA_API_KEY = os.environ.get("SUPADATA_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
PORT = int(os.environ.get("PORT", 8080))

client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY) if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY else None

# Pipeline settings
CHUNK_TARGET_TOKENS = int(os.environ.get("CHUNK_TARGET_TOKENS", 2500))
CHUNK_MAX_TOKENS = int(os.environ.get("CHUNK_MAX_TOKENS", 3000))
CHUNK_OVERLAP_TOKENS = int(os.environ.get("CHUNK_OVERLAP_TOKENS", 200))

ANALYST_MODEL_SMALL = os.environ.get("ANALYST_MODEL_SMALL", OPENAI_MODEL)
ANALYST_MODEL_LARGE = os.environ.get("ANALYST_MODEL_LARGE", OPENAI_MODEL)
SYNTHESIZER_MODEL = os.environ.get("SYNTHESIZER_MODEL", OPENAI_MODEL)

MAIN_MENU = ReplyKeyboardMarkup(
    [["📚 Мои конспекты"], ["ℹ️ Помощь"]],
    resize_keyboard=True,
    is_persistent=True,
)


def get_video_id(url: str) -> str:
    match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
    if match:
        return match.group(1)
    raise ValueError("Не удалось найти ID видео")


def get_transcript(video_id: str) -> tuple[str | None, str | None]:
    # 1) Основной источник: youtube-transcript-api
    text = get_transcript_from_youtube_transcript_api(video_id)
    if text:
        logging.info("Transcript source: youtube-transcript-api")
        return text, "youtube-transcript-api"

    # 2) Если не получилось, используем SUPADATA
    text = get_transcript_from_supadata(video_id)
    if text:
        logging.info("Transcript source: SUPADATA")
        return text, "supadata"
    return None, None


def save_summary_to_supabase(update: Update, video_id: str, video_url: str, summary: str, chunk_count: int, transcript_source: str) -> str | None:
    if not supabase or not update.effective_user:
        return None

    try:
        user = update.effective_user
        profile_payload = {
            "telegram_user_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
        }
        supabase.table("user_profiles").upsert(profile_payload, on_conflict="telegram_user_id").execute()

        profile_resp = (
            supabase.table("user_profiles")
            .select("id")
            .eq("telegram_user_id", user.id)
            .limit(1)
            .execute()
        )
        if not profile_resp.data:
            return None

        user_id = profile_resp.data[0]["id"]
        summary_payload = {
            "user_id": user_id,
            "video_id": video_id,
            "video_url": video_url,
            "transcript_source": transcript_source,
            "summary_markdown": summary,
            "chunk_count": chunk_count,
            "model_analyst_small": ANALYST_MODEL_SMALL,
            "model_analyst_large": ANALYST_MODEL_LARGE,
            "model_synthesizer": SYNTHESIZER_MODEL,
        }
        summary_resp = supabase.table("summaries").insert(summary_payload).execute()
        if summary_resp.data and len(summary_resp.data) > 0:
            return summary_resp.data[0].get("id")
    except Exception as e:
        logging.warning(f"Supabase save failed: {e}")
    return None


def save_feedback_to_supabase(update: Update, summary_id: str, liked: bool):
    if not supabase or not update.effective_user:
        return

    try:
        user = update.effective_user
        profile_resp = (
            supabase.table("user_profiles")
            .select("id")
            .eq("telegram_user_id", user.id)
            .limit(1)
            .execute()
        )
        if not profile_resp.data:
            return

        user_id = profile_resp.data[0]["id"]
        payload = {
            "summary_id": summary_id,
            "user_id": user_id,
            "liked": liked,
        }
        supabase.table("summary_feedback").upsert(payload, on_conflict="summary_id,user_id").execute()
    except Exception as e:
        logging.warning(f"Supabase feedback save failed: {e}")


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


def estimate_tokens(text: str) -> int:
    # Быстрая оценка без доп. библиотек: ~1.3 токена на слово для RU/EN смешанного текста
    words = len(text.split())
    return max(1, int(words * 1.3))


def chunk_transcript(
    text: str,
    target_tokens: int = CHUNK_TARGET_TOKENS,
    max_tokens: int = CHUNK_MAX_TOKENS,
    overlap_tokens: int = CHUNK_OVERLAP_TOKENS,
) -> list[str]:
    """Агент Chunker (без LLM): режет по предложениям с overlap."""
    sentences = re.split(r"(?<=[.!?…])\s+", text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return []

    chunks: list[str] = []
    i = 0
    while i < len(sentences):
        current: list[str] = []
        current_tokens = 0

        while i < len(sentences):
            s = sentences[i]
            s_tokens = estimate_tokens(s)
            if current and current_tokens + s_tokens > max_tokens:
                break
            current.append(s)
            current_tokens += s_tokens
            i += 1
            if current_tokens >= target_tokens:
                break

        chunk_text = " ".join(current).strip()
        if chunk_text:
            chunks.append(chunk_text)

        if i >= len(sentences):
            break

        # overlap: возвращаемся назад по предложениям примерно на overlap_tokens
        back_tokens = 0
        j = i - 1
        while j >= 0 and back_tokens < overlap_tokens:
            back_tokens += estimate_tokens(sentences[j])
            j -= 1
        i = max(0, j + 1)

        # защита от бесконечного цикла
        if chunks and i < len(sentences):
            last_chunk_tail = chunks[-1][-120:]
            next_preview = " ".join(sentences[i : min(i + 2, len(sentences))])
            if last_chunk_tail and next_preview and last_chunk_tail in next_preview:
                i += 1

    return chunks


def select_analyst_model(chunk_text: str) -> str:
    return ANALYST_MODEL_LARGE if estimate_tokens(chunk_text) > 2200 else ANALYST_MODEL_SMALL


def analyze_chunk(chunk_text: str, chunk_index: int, total_chunks: int) -> str:
    """Агент Analyst: извлекает суть чанка."""
    system_prompt = (
        "Ты эксперт в извлечении принципиальных идей. "
        "Выдай только то, без чего человек не поймёт суть. "
        "Не добавляй фактов, которых нет в тексте."
    )
    user_prompt = f"""
Чанк {chunk_index}/{total_chunks}.

Верни результат в формате:
1) Главная мысль (1-2 предложения)
2) Ключевые идеи (3-6 пунктов)
3) Важные оговорки/ограничения (если есть)

Текст чанка:
{chunk_text}
"""
    model = select_analyst_model(chunk_text)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=900,
    )
    return (response.choices[0].message.content or "").strip()


def synthesize_analyses(analyses: list[str]) -> str:
    """Агент Synthesizer: объединяет аналитики чанков в финальный конспект."""
    joined = "\n\n---\n\n".join(analyses)
    system_prompt = (
        "Ты синтезируешь несколько частичных аналитик в единый конспект. "
        "Убирай повторы, объединяй близкие идеи, сохраняй только суть."
    )
    user_prompt = f"""
Собери итоговый конспект строго в формате:

🎯 **Краткое резюме** (ровно 3 предложения)

📌 **Ключевые идеи** (5-8 пунктов)

✅ **Вывод** (1-2 предложения: кому и зачем это полезно)

Материалы для синтеза:
{joined}
"""
    response = client.chat.completions.create(
        model=SYNTHESIZER_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=1100,
    )
    return (response.choices[0].message.content or "").strip()


def summarize_with_multi_agent_pipeline(text: str) -> tuple[str, int]:
    chunks = chunk_transcript(text)
    if not chunks:
        raise ValueError("Не удалось разбить транскрипт на чанки")

    analyses: list[str] = []
    total = len(chunks)
    for idx, chunk in enumerate(chunks, start=1):
        analyses.append(analyze_chunk(chunk, idx, total))

    return synthesize_analyses(analyses), total


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Отправь мне ссылку на YouTube видео — я сделаю краткий конспект и скажу, стоит ли его смотреть.",
        reply_markup=MAIN_MENU,
    )


async def my_summaries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not supabase or not update.effective_user:
        await update.message.reply_text("История пока недоступна.")
        return

    try:
        user = update.effective_user
        profile_resp = (
            supabase.table("user_profiles")
            .select("id")
            .eq("telegram_user_id", user.id)
            .limit(1)
            .execute()
        )
        if not profile_resp.data:
            await update.message.reply_text("У тебя пока нет сохранённых конспектов.")
            return

        user_id = profile_resp.data[0]["id"]
        summaries_resp = (
            supabase.table("summaries")
            .select("video_url, created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(5)
            .execute()
        )
        items = summaries_resp.data or []
        if not items:
            await update.message.reply_text("У тебя пока нет сохранённых конспектов.")
            return

        lines = ["🗂 **Твои последние конспекты**", ""]
        for idx, item in enumerate(items, start=1):
            lines.append(f"**{idx}.** {item.get('video_url', '')}")

        bot_username = (await context.bot.get_me()).username
        back_button = InlineKeyboardMarkup(
            [[InlineKeyboardButton("↩️ Вернуться в бота", url=f"https://t.me/{bot_username}")]]
        )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=back_button)
    except Exception as e:
        logging.warning(f"My summaries failed: {e}")
        await update.message.reply_text("Не удалось загрузить историю.")


async def feedback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer("Спасибо за оценку!")
    data = query.data or ""
    # format: fb:<summary_id>:up|down
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "fb":
        return

    summary_id, vote = parts[1], parts[2]
    liked = vote == "up"
    save_feedback_to_supabase(update, summary_id, liked)
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception as e:
        logging.warning(f"Feedback markup edit failed: {e}")
    if query.message:
        await query.message.reply_text("Спасибо за оценку! ✅")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if url == "📚 Мои конспекты":
        await my_summaries(update, context)
        return

    if url == "ℹ️ Помощь":
        await update.message.reply_text(
            "Отправь ссылку на YouTube, либо нажми «📚 Мои конспекты».",
            reply_markup=MAIN_MENU,
        )
        return

    if "youtube.com" not in url and "youtu.be" not in url:
        await update.message.reply_text("Пожалуйста, отправь ссылку на YouTube видео.")
        return

    await update.message.reply_text("⏳ Загружаю субтитры...")

    try:
        video_id = get_video_id(url)
    except ValueError:
        await update.message.reply_text("Не могу найти ID видео. Проверь ссылку.")
        return

    text, transcript_source = get_transcript(video_id)

    if not text or len(text) < 100:
        await update.message.reply_text("😕 Субтитры не найдены для этого видео.")
        return

    await update.message.reply_text("🤖 Анализирую содержание...")

    try:
        summary, chunk_count = summarize_with_multi_agent_pipeline(text)
        await update.message.reply_text(summary, parse_mode="Markdown")
        if transcript_source:
            summary_id = save_summary_to_supabase(update, video_id, url, summary, chunk_count, transcript_source)
            if summary_id:
                keyboard = InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton("👍 Полезно", callback_data=f"fb:{summary_id}:up"),
                        InlineKeyboardButton("👎 Слабо", callback_data=f"fb:{summary_id}:down"),
                    ]]
                )
                await update.message.reply_text("Оцени конспект:", reply_markup=keyboard)
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
    app.add_handler(CommandHandler("my", my_summaries))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(feedback_callback, pattern=r"^fb:"))
    app.add_error_handler(error_handler)

    if WEBHOOK_URL:
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path="webhook",
            webhook_url=f"{WEBHOOK_URL}/webhook",
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
