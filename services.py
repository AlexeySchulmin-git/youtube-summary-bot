import logging
import re
import requests
from telegram import Update
from youtube_transcript_api import YouTubeTranscriptApi

from config import (
    CLIENT,
    OPENAI_MODEL,
    ANALYST_MODEL_SMALL,
    ANALYST_MODEL_LARGE,
    SYNTHESIZER_MODEL,
    SUPADATA_API_KEY,
    SUPABASE,
)

logger = logging.getLogger(__name__)


def get_video_id(url: str) -> str:
    match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
    if match:
        return match.group(1)
    raise ValueError("Не удалось найти ID видео")


def get_transcript(video_id: str) -> tuple[str | None, str | None]:
    text = get_transcript_from_youtube_transcript_api(video_id)
    if text:
        logger.info("Transcript source: youtube-transcript-api")
        return text, "youtube-transcript-api"

    text = get_transcript_from_supadata(video_id)
    if text:
        logger.info("Transcript source: SUPADATA")
        return text, "supadata"

    return None, None


def _get_or_create_user_id(update: Update) -> str | None:
    if not SUPABASE or not update.effective_user:
        return None

    try:
        user = update.effective_user
        profile_payload = {
            "telegram_user_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
        }
        SUPABASE.table("user_profiles").upsert(profile_payload, on_conflict="telegram_user_id").execute()

        profile_resp = (
            SUPABASE.table("user_profiles")
            .select("id")
            .eq("telegram_user_id", user.id)
            .limit(1)
            .execute()
        )
        if not profile_resp.data:
            return None
        return profile_resp.data[0]["id"]
    except Exception as exc:
        logger.warning(f"Supabase profile failed: {exc}")
        return None


def get_saved_summary_for_user(update: Update, video_id: str) -> dict | None:
    if not SUPABASE or not update.effective_user:
        return None

    user_id = _get_or_create_user_id(update)
    if not user_id:
        return None

    try:
        summary_resp = (
            SUPABASE.table("summaries")
            .select("id, summary_markdown")
            .eq("user_id", user_id)
            .eq("video_id", video_id)
            .limit(1)
            .execute()
        )
        if summary_resp.data:
            return summary_resp.data[0]
    except Exception as exc:
        logger.warning(f"Supabase fetch existing summary failed: {exc}")
    return None


def save_summary_to_supabase(update: Update, video_id: str, video_url: str, summary: str, chunk_count: int, transcript_source: str) -> str | None:
    user_id = _get_or_create_user_id(update)
    if not SUPABASE or not user_id:
        return None

    try:
        existing_resp = (
            SUPABASE.table("summaries")
            .select("id")
            .eq("user_id", user_id)
            .eq("video_id", video_id)
            .limit(1)
            .execute()
        )
        if existing_resp.data:
            return existing_resp.data[0].get("id")

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
        summary_resp = SUPABASE.table("summaries").insert(summary_payload).execute()
        if summary_resp.data and len(summary_resp.data) > 0:
            return summary_resp.data[0].get("id")
    except Exception as exc:
        logger.warning(f"Supabase save failed: {exc}")
    return None


def save_feedback_to_supabase(update: Update, summary_id: str, liked: bool):
    if not SUPABASE or not update.effective_user:
        return

    try:
        user = update.effective_user
        profile_resp = (
            SUPABASE.table("user_profiles")
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
        SUPABASE.table("summary_feedback").upsert(payload, on_conflict="summary_id,user_id").execute()
    except Exception as exc:
        logger.warning(f"Supabase feedback save failed: {exc}")


def get_transcript_from_youtube_transcript_api(video_id: str) -> str | None:
    try:
        if hasattr(YouTubeTranscriptApi, "get_transcript"):
            transcript_items = YouTubeTranscriptApi.get_transcript(video_id, languages=["ru", "en"])
        else:
            transcript = YouTubeTranscriptApi().fetch(video_id, languages=["ru", "en"])
            transcript_items = list(transcript)

        if not transcript_items:
            return None

        return " ".join(
            (item.get("text", "") if isinstance(item, dict) else getattr(item, "text", ""))
            for item in transcript_items
        ).strip()
    except Exception as exc:
        logger.warning(f"youtube-transcript-api failed: {exc}")
        return None


def get_transcript_from_supadata(video_id: str) -> str | None:
    if not SUPADATA_API_KEY:
        logger.warning("SUPADATA_API_KEY is not set")
        return None

    try:
        url = "https://api.supadata.ai/v1/youtube/transcript"
        headers = {"x-api-key": SUPADATA_API_KEY}
        params = {"videoId": video_id, "text": True}
        response = requests.get(url, headers=headers, params=params, timeout=30)
        if response.status_code != 200:
            logger.error(f"Supadata error: {response.status_code} {response.text}")
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
    except Exception as exc:
        logger.error(f"Supadata request failed: {exc}")
    return None


def estimate_tokens(text: str) -> int:
    words = len(text.split())
    return max(1, int(words * 1.3))


def chunk_transcript(
    text: str,
    target_tokens: int = 2500,
    max_tokens: int = 3000,
    overlap_tokens: int = 200,
) -> list[str]:
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

        back_tokens = 0
        j = i - 1
        while j >= 0 and back_tokens < overlap_tokens:
            back_tokens += estimate_tokens(sentences[j])
            j -= 1
        i = max(0, j + 1)

        if chunks and i < len(sentences):
            last_chunk_tail = chunks[-1][-120:]
            next_preview = " ".join(sentences[i : min(i + 2, len(sentences))])
            if last_chunk_tail and next_preview and last_chunk_tail in next_preview:
                i += 1

    return chunks


def select_analyst_model(chunk_text: str) -> str:
    return ANALYST_MODEL_LARGE if estimate_tokens(chunk_text) > 2200 else ANALYST_MODEL_SMALL


def analyze_chunk(chunk_text: str, chunk_index: int, total_chunks: int) -> str:
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

Сосредоточься на том, что важно именно в контексте этого видео.
Не добавляй оценок типа 'стоит ли смотреть'.

Текст чанка:
{chunk_text}
"""
    model = select_analyst_model(chunk_text)
    response = CLIENT.chat.completions.create(
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
    joined = "\n\n---\n\n".join(analyses)
    system_prompt = (
        "Ты синтезируешь несколько частичных аналитик в единый конспект. "
        "Сосредоточься на самых важных вещах из видео, избегай оценок формата 'стоит ли смотреть'. "
        "Сначала определи тему видео (например: театр, бизнес, наука, спорт, кино, образование и т.д.) "
        "и подстрой лексику/тон итогового конспекта под эту тему, сохраняя фактическую точность. "
        "Выделяй практические выводы, ключевые мысли и важные детали, которые стоит запомнить."
    )
    user_prompt = f"""
Собери итоговый конспект строго в формате:

🎯 **Краткое резюме** (ровно 3 предложения)

📌 **Важные вещи** (5-8 пунктов, каждый пункт должен начинаться с символа ◾)

🧭 **Вывод** (1-2 предложения: итог и зачем это важно в контексте темы)

Материалы для синтеза:
{joined}
"""
    response = CLIENT.chat.completions.create(
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
    chunks = chunk_transcript(text, target_tokens=2500, max_tokens=3000, overlap_tokens=200)
    if not chunks:
        raise ValueError("Не удалось разбить транскрипт на чанки")

    analyses: list[str] = []
    total = len(chunks)
    for idx, chunk in enumerate(chunks, start=1):
        analyses.append(analyze_chunk(chunk, idx, total))

    return synthesize_analyses(analyses), total
