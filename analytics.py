import logging
from datetime import datetime, timezone

from telegram import Update

from config import SUPABASE

logger = logging.getLogger(__name__)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def track_user_activity(update: Update):
    if not SUPABASE:
        return
    user = update.effective_user
    if not user or user.is_bot:
        return

    try:
        now = _iso_now()
        resp = (
            SUPABASE.table("analytics_users")
            .select("telegram_user_id, message_count, commands_count, summaries_count, first_seen_at")
            .eq("telegram_user_id", user.id)
            .limit(1)
            .execute()
        )
        existing = resp.data[0] if resp.data else None

        if existing:
            payload = {
                "telegram_user_id": user.id,
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "language_code": user.language_code,
                "is_bot": bool(user.is_bot),
                "last_seen_at": now,
                "message_count": int(existing.get("message_count") or 0) + 1,
                "commands_count": int(existing.get("commands_count") or 0),
                "summaries_count": int(existing.get("summaries_count") or 0),
            }
            SUPABASE.table("analytics_users").upsert(payload, on_conflict="telegram_user_id").execute()
        else:
            payload = {
                "telegram_user_id": user.id,
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "language_code": user.language_code,
                "is_bot": bool(user.is_bot),
                "first_seen_at": now,
                "last_seen_at": now,
                "message_count": 1,
                "commands_count": 0,
                "summaries_count": 0,
            }
            SUPABASE.table("analytics_users").insert(payload).execute()
    except Exception as exc:
        logger.warning(f"Analytics user activity failed: {exc}")


def increment_user_counter(update: Update, field: str):
    if not SUPABASE:
        return
    if field not in {"commands_count", "summaries_count"}:
        return
    user = update.effective_user
    if not user or user.is_bot:
        return

    try:
        resp = (
            SUPABASE.table("analytics_users")
            .select("telegram_user_id, commands_count, summaries_count")
            .eq("telegram_user_id", user.id)
            .limit(1)
            .execute()
        )
        if not resp.data:
            track_user_activity(update)
            resp = (
                SUPABASE.table("analytics_users")
                .select("telegram_user_id, commands_count, summaries_count")
                .eq("telegram_user_id", user.id)
                .limit(1)
                .execute()
            )
            if not resp.data:
                return
        row = resp.data[0]
        payload = {
            "telegram_user_id": user.id,
            field: int(row.get(field) or 0) + 1,
            "last_seen_at": _iso_now(),
        }
        SUPABASE.table("analytics_users").upsert(payload, on_conflict="telegram_user_id").execute()
    except Exception as exc:
        logger.warning(f"Analytics increment failed: {exc}")


def track_event(update: Update, event_name: str, meta: dict | None = None):
    if not SUPABASE:
        return
    user = update.effective_user
    if not user or user.is_bot:
        return

    try:
        payload = {
            "telegram_user_id": user.id,
            "event_name": event_name,
            "event_at": _iso_now(),
            "meta": meta or {},
        }
        SUPABASE.table("analytics_events").insert(payload).execute()
    except Exception as exc:
        logger.warning(f"Analytics event failed ({event_name}): {exc}")
