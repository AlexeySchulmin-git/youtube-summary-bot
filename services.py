import logging
import re
import requests
import time
from datetime import timedelta
from telegram import Update
from youtube_transcript_api import YouTubeTranscriptApi

from config import (
    CLIENT,
    OPENAI_MODEL,
    ANALYST_MODEL_SMALL,
    ANALYST_MODEL_LARGE,
    SYNTHESIZER_MODEL,
    SUPADATA_API_KEY,
    YOUTUBE_API_KEY,
    SUPABASE,
)
from quality_agent import get_quality_guidelines_text

logger = logging.getLogger(__name__)
_YTA_BACKOFF_UNTIL = 0.0
_SUPADATA_BACKOFF_UNTIL = 0.0
_YT_SEARCH_CACHE: dict[str, tuple[float, dict]] = {}


def _parse_iso8601_duration_to_text(value: str) -> str:
    """Convert ISO8601 duration (e.g. PT1H2M9S) to human text (HH:MM:SS / MM:SS)."""
    if not value or not isinstance(value, str):
        return "—"

    m = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", value.strip())
    if not m:
        return "—"

    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    seconds = int(m.group(3) or 0)

    total = int(timedelta(hours=hours, minutes=minutes, seconds=seconds).total_seconds())
    if total <= 0:
        return "00:00"

    h = total // 3600
    rem = total % 3600
    mm = rem // 60
    ss = rem % 60
    if h > 0:
        return f"{h:02d}:{mm:02d}:{ss:02d}"
    return f"{mm:02d}:{ss:02d}"


def _fetch_video_durations(video_ids: list[str]) -> dict[str, str]:
    if not YOUTUBE_API_KEY or not video_ids:
        return {}

    unique_ids = [v for v in dict.fromkeys(video_ids) if v]
    if not unique_ids:
        return {}

    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "key": YOUTUBE_API_KEY,
        "part": "contentDetails",
        "id": ",".join(unique_ids[:50]),
    }
    response = requests.get(url, params=params, timeout=25)
    if response.status_code != 200:
        return {}

    data = response.json() if response.content else {}
    out: dict[str, str] = {}
    for it in (data.get("items") if isinstance(data, dict) else []) or []:
        vid = (it.get("id") or "").strip()
        iso = ((it.get("contentDetails") or {}).get("duration") or "").strip()
        if vid:
            out[vid] = _parse_iso8601_duration_to_text(iso)
    return out


def get_youtube_query_suggestions(query: str, limit: int = 5) -> list[str]:
    q = (query or "").strip()
    if not q:
        return []
    try:
        logger.info(f"YT suggest request: query='{q}'")
        resp = requests.get(
            "https://suggestqueries.google.com/complete/search",
            params={"client": "firefox", "ds": "yt", "q": q},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning(f"YT suggest failed: status={resp.status_code}, query='{q}'")
            return []
        data = resp.json() if resp.content else []
        suggestions = data[1] if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list) else []
        clean = []
        seen = set()
        for s in suggestions:
            if not isinstance(s, str):
                continue
            text = s.strip()
            key = text.lower()
            if text and key not in seen:
                seen.add(key)
                clean.append(text)
            if len(clean) >= max(1, min(10, int(limit))):
                break
        logger.info(f"YT suggest response: query='{q}', count={len(clean)}, items={clean}")
        return clean
    except Exception:
        logger.exception(f"YT suggest exception for query='{q}'")
        return []


def search_youtube_videos(query: str, page_token: str | None = None, max_results: int = 5) -> dict:
    """Search videos via YouTube Data API v3 search.list.

    Returns dict with keys: items(list), next_page_token(str|None), prev_page_token(str|None)
    """
    if not YOUTUBE_API_KEY:
        raise RuntimeError("YOUTUBE_API_KEY is not set")

    q = (query or "").strip()
    if not q:
        return {"items": [], "next_page_token": None, "prev_page_token": None}

    token = page_token or ""
    cache_key = f"{q.lower()}::{token}::{max_results}"
    now = time.time()
    cached = _YT_SEARCH_CACHE.get(cache_key)
    if cached and now - cached[0] < 900:
        return cached[1]

    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "key": YOUTUBE_API_KEY,
        "part": "snippet",
        "type": "video",
        "q": q,
        "maxResults": max(1, min(10, int(max_results))),
        "safeSearch": "moderate",
        "relevanceLanguage": "ru",
        "order": "relevance",
    }
    if token:
        params["pageToken"] = token

    response = requests.get(url, params=params, timeout=25)
    if response.status_code != 200:
        raise RuntimeError(f"YouTube search failed: {response.status_code} {response.text}")

    data = response.json() if response.content else {}
    items_raw = data.get("items") if isinstance(data, dict) else []
    video_ids: list[str] = []
    items = []
    for it in items_raw or []:
        vid = (((it or {}).get("id") or {}).get("videoId") or "").strip()
        sn = (it or {}).get("snippet") or {}
        if not vid:
            continue
        video_ids.append(vid)
        items.append(
            {
                "video_id": vid,
                "title": (sn.get("title") or "").strip(),
                "channel": (sn.get("channelTitle") or "").strip(),
                "published_at": (sn.get("publishedAt") or "").strip(),
                "url": f"https://www.youtube.com/watch?v={vid}",
            }
        )

    durations = _fetch_video_durations(video_ids)
    for item in items:
        vid = item.get("video_id") or ""
        item["duration"] = durations.get(vid, "—")

    result = {
        "items": items,
        "next_page_token": data.get("nextPageToken") if isinstance(data, dict) else None,
        "prev_page_token": data.get("prevPageToken") if isinstance(data, dict) else None,
    }
    _YT_SEARCH_CACHE[cache_key] = (now, result)
    return result


def get_video_id(url: str) -> str:
    match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
    if match:
        return match.group(1)
    raise ValueError("Не удалось найти ID видео")


def get_video_title(video_url: str) -> str | None:
    """Fetch YouTube title via oEmbed without API key."""
    try:
        resp = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": video_url, "format": "json"},
            timeout=20,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        title = data.get("title") if isinstance(data, dict) else None
        if isinstance(title, str) and title.strip():
            return title.strip()
    except Exception as exc:
        logger.warning(f"Failed to fetch video title: {exc}")
    return None


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
            .select("id, summary_markdown, video_url")
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


def save_summary_to_supabase(
    update: Update,
    video_id: str,
    video_url: str,
    summary: str,
    chunk_count: int,
    transcript_source: str,
    ai_title: str | None = None,
) -> str | None:
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
        if ai_title:
            summary_payload["ai_title"] = ai_title

        try:
            summary_resp = SUPABASE.table("summaries").insert(summary_payload).execute()
        except Exception as exc:
            if "ai_title" in summary_payload:
                logger.warning(f"Supabase save with ai_title failed, retry without ai_title: {exc}")
                summary_payload.pop("ai_title", None)
                summary_resp = SUPABASE.table("summaries").insert(summary_payload).execute()
            else:
                raise

        if summary_resp.data and len(summary_resp.data) > 0:
            return summary_resp.data[0].get("id")
    except Exception as exc:
        logger.warning(f"Supabase save failed: {exc}")
    return None


def save_feedback_to_supabase(update: Update, summary_id: str, rating: int):
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
        rating = max(1, min(5, int(rating)))
        payload = {
            "summary_id": summary_id,
            "user_id": user_id,
            "rating": rating,
        }
        try:
            SUPABASE.table("summary_feedback").upsert(payload, on_conflict="summary_id,user_id").execute()
        except Exception as exc:
            logger.warning(f"Supabase rating save failed, fallback to liked: {exc}")
            fallback_payload = {
                "summary_id": summary_id,
                "user_id": user_id,
                "liked": rating >= 4,
            }
            SUPABASE.table("summary_feedback").upsert(fallback_payload, on_conflict="summary_id,user_id").execute()
    except Exception as exc:
        logger.warning(f"Supabase feedback save failed: {exc}")


def generate_ai_title(transcript: str, summary_markdown: str, fallback_title: str | None = None) -> str:
    fallback = (fallback_title or "Конспект видео").strip()

    if not CLIENT:
        return fallback

    try:
        transcript_short = transcript[:4000]
        summary_short = summary_markdown[:2500]
        response = CLIENT.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "Сгенерируй короткий информативный заголовок конспекта на русском. Только одна строка, без кавычек.",
                },
                {
                    "role": "user",
                    "content": (
                        f"Оригинальный заголовок: {fallback}\n\n"
                        f"Транскрипт (фрагмент):\n{transcript_short}\n\n"
                        f"Конспект:\n{summary_short}\n\n"
                        "Требования: 4-9 слов, конкретно и по сути."
                    ),
                },
            ],
            temperature=0.2,
            max_tokens=40,
        )
        title = (response.choices[0].message.content or "").strip().strip('"')
        if title:
            return title[:120]
    except Exception as exc:
        logger.warning(f"AI title generation failed: {exc}")

    return fallback


def get_transcript_from_youtube_transcript_api(video_id: str) -> str | None:
    global _YTA_BACKOFF_UNTIL

    now = time.time()
    if now < _YTA_BACKOFF_UNTIL:
        logger.warning("youtube-transcript-api is in cooldown after 429; skipping request")
        return None

    preferred_langs = ["ru", "uk", "en", "en-US", "en-GB"]

    def _join_items(items) -> str:
        text = " ".join(
            (item.get("text", "") if isinstance(item, dict) else getattr(item, "text", ""))
            for item in items
        ).strip()
        return re.sub(r"\s+", " ", text).strip()

    try:
        if hasattr(YouTubeTranscriptApi, "get_transcript"):
            transcript_items = YouTubeTranscriptApi.get_transcript(video_id, languages=preferred_langs)
        else:
            transcript = YouTubeTranscriptApi().fetch(video_id, languages=preferred_langs)
            transcript_items = list(transcript)

        if not transcript_items:
            raise RuntimeError("Empty transcript items on fast path")

        text = _join_items(transcript_items)
        if text:
            return text
    except Exception as exc:
        msg = str(exc)
        if "Too Many Requests" in msg or "429" in msg:
            _YTA_BACKOFF_UNTIL = time.time() + 180
            logger.warning("youtube-transcript-api rate limited (429). Cooldown for 180s.")
        else:
            logger.warning(f"youtube-transcript-api fast path failed: {exc}")

    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

        try:
            direct = transcript_list.find_transcript(preferred_langs)
            text = _join_items(direct.fetch())
            if text:
                return text
        except Exception:
            pass

        try:
            generated = transcript_list.find_generated_transcript(preferred_langs)
            text = _join_items(generated.fetch())
            if text:
                return text
        except Exception:
            pass

        for tr in transcript_list:
            try:
                text = _join_items(tr.fetch())
                if text:
                    return text
            except Exception:
                pass

            if getattr(tr, "is_translatable", False):
                for target_lang in ("ru", "en"):
                    try:
                        translated = tr.translate(target_lang)
                        text = _join_items(translated.fetch())
                        if text:
                            return text
                    except Exception:
                        continue

    except Exception as exc:
        msg = str(exc)
        if "Too Many Requests" in msg or "429" in msg:
            _YTA_BACKOFF_UNTIL = time.time() + 180
            logger.warning("youtube-transcript-api fallback rate limited (429). Cooldown for 180s.")
        else:
            logger.warning(f"youtube-transcript-api fallback failed: {exc}")

    return None


def get_transcript_from_supadata(video_id: str) -> str | None:
    global _SUPADATA_BACKOFF_UNTIL

    now = time.time()
    if now < _SUPADATA_BACKOFF_UNTIL:
        logger.warning("SUPADATA is in cooldown after rate-limit; skipping request")
        return None

    if not SUPADATA_API_KEY:
        logger.warning("SUPADATA_API_KEY is not set")
        return None

    try:
        url = "https://api.supadata.ai/v1/youtube/transcript"
        headers = {"x-api-key": SUPADATA_API_KEY}
        params = {"videoId": video_id, "text": True}
        response = requests.get(url, headers=headers, params=params, timeout=30)
        if response.status_code == 429:
            _SUPADATA_BACKOFF_UNTIL = time.time() + 120
            logger.warning("SUPADATA rate limited (429). Cooldown for 120s.")
            return None
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


def analyze_chunk(chunk_text: str, chunk_index: int, total_chunks: int, video_title: str | None = None) -> str:
    system_prompt = (
        "Ты эксперт в извлечении принципиальных идей. "
        "Выдай только то, без чего человек не поймёт суть. "
        "Не добавляй фактов, которых нет в тексте."
    )
    title_block = f"Заголовок видео: {video_title}\n\n" if video_title else ""
    user_prompt = f"""
{title_block}Чанк {chunk_index}/{total_chunks}.

Верни результат в формате:
1) Главная мысль (1-2 предложения)
2) Ключевые идеи (3-6 пунктов)
3) Важные оговорки/ограничения (если есть)

Сосредоточься на том, что важно именно в контексте этого видео.
Учитывай заголовок видео как рамку контекста.
Если в тексте упоминаются конкретные персоны, фиксируй их в ключевых идеях.

ИГНОРИРУЙ:
- Вступления и самопрезентации автора
- Призывы подписаться, лайкнуть, включить уведомления
- Спонсорские вставки и рекламу
- Повторения одной мысли разными словами

Пиши своими словами, не копируй фразы автора дословно.
Включай цифры, названия инструментов, конкретные примеры, если это важно.
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


def synthesize_analyses(analyses: list[str], video_title: str | None = None) -> str:
    joined = "\n\n---\n\n".join(analyses)
    evolution_guidelines = get_quality_guidelines_text()
    evolution_block = (
        f"\n\nДополнительные эволюционные правила качества (сформированы на основе прошлых оценок):\n{evolution_guidelines}\n"
        if evolution_guidelines
        else ""
    )
    system_prompt = (
        "Ты синтезируешь несколько частичных аналитик в единый конспект. "
        "Сосредоточься на самых важных вещах из видео, избегай оценок формата 'стоит ли смотреть'. "
        "Сначала определи тему видео (например: театр, бизнес, наука, спорт, кино, образование и т.д.) "
        "и подстрой лексику/тон итогового конспекта под эту тему, сохраняя фактическую точность. "
        "Выделяй практические выводы, ключевые мысли и важные детали, которые стоит запомнить."
    )
    title_block = f"Заголовок видео: {video_title}\n\n" if video_title else ""
    user_prompt = f"""
{title_block}Собери итоговый конспект строго в формате:

Правила:
- Учитывай заголовок видео в формулировках конспекта.
- Если в материале есть упоминания персон, укажи их по делу в релевантных пунктах.
- Пиши своими словами, не копируй фразы автора дословно.
- Включай цифры, названия инструментов, конкретные примеры, если необходимо.
- Используй максимально точные названия брендов/сервисов/терминов из материала. Пример: если речь о Wordstat, пиши именно `wordstat.yandex.ru`, а не общее "подбор слов".
- Без вводных фраз: не начинай с "Конечно!", "Вот конспект:", "В этом видео..."
- Сразу выдавай результат по структуре.
- Не дублируй смысл между разделами: `🎯 Краткое резюме` = компактный обзор; `🧭 Вывод` = новый итог/применимость/ограничения, без перефраза резюме.

ИГНОРИРУЙ:
- Вступления и самопрезентации автора
- Призывы подписаться, лайкнуть, включить уведомления
- Спонсорские вставки и рекламу
- Повторения одной мысли разными словами

🎯 **Краткое резюме** (ровно 3 предложения)

⚡**Ключевые моменты** (1-4 пункта, каждый пункт должен начинаться с символа ◾; пункты должны быть уникальными и не повторять друг друга)

🧭 **Вывод** (1-2 предложения: итог и зачем это важно в контексте темы; не повторяй формулировки из резюме)

Материалы для синтеза:
{joined}
{evolution_block}
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


def summarize_with_multi_agent_pipeline(text: str, video_title: str | None = None) -> tuple[str, int]:
    chunks = chunk_transcript(text, target_tokens=2500, max_tokens=3000, overlap_tokens=200)
    if not chunks:
        raise ValueError("Не удалось разбить транскрипт на чанки")

    analyses: list[str] = []
    total = len(chunks)
    for idx, chunk in enumerate(chunks, start=1):
        analyses.append(analyze_chunk(chunk, idx, total, video_title=video_title))

    return synthesize_analyses(analyses, video_title=video_title), total
