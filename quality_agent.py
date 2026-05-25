import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import (
    CLIENT,
    OPENAI_MODEL,
    QUALITY_EVOLUTION_ENABLED,
    QUALITY_HISTORY_LIMIT,
    QUALITY_MIN_OVERALL_SCORE,
    QUALITY_STATE_PATH,
)

logger = logging.getLogger(__name__)


def _extract_section(summary: str, title: str) -> str:
    pattern = rf"{re.escape(title)}.*?(?=\n\n[🎯📌🧭]|\Z)"
    match = re.search(pattern, summary, flags=re.S)
    return (match.group(0).strip() if match else "").strip()


def _extract_bullet_lines(summary: str) -> list[str]:
    lines = [ln.strip() for ln in summary.splitlines()]
    return [ln for ln in lines if ln.startswith("◾")]


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _default_state() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": None,
        "total_evaluations": 0,
        "running": {
            "coverage": 0.0,
            "faithfulness": 0.0,
            "structure": 0.0,
            "overall": 0.0,
        },
        "style_guidelines": [
            "Сохраняй структуру: 🎯 Краткое резюме, 📌 Важные вещи, 🧭 Вывод.",
            "В блоке 📌 Важные вещи используй только маркеры ◾.",
            "Не добавляй факты, которых нет в транскрипте.",
        ],
        "history": [],
    }


def _load_state() -> dict[str, Any]:
    p = Path(QUALITY_STATE_PATH)
    if not p.exists():
        return _default_state()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"Failed to read quality state, reset: {exc}")
        return _default_state()


def _save_state(state: dict[str, Any]):
    p = Path(QUALITY_STATE_PATH)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _evaluate_with_llm(transcript: str, summary: str) -> dict[str, Any]:
    if not CLIENT:
        return {
            "coverage": 0.0,
            "faithfulness": 0.0,
            "structure": 0.0,
            "overall": 0.0,
            "missing_key_points": [],
            "hallucinations": [],
            "improvement_suggestions": ["OpenAI client is not configured."],
        }

    transcript_short = transcript[:16000]
    summary_short = summary[:7000]

    system_prompt = (
        "Ты агент-оценщик качества конспектов. "
        "Оцени только по фактам из транскрипта, будь строгим и кратким. "
        "Верни ТОЛЬКО JSON без markdown."
    )
    user_prompt = f"""
Оцени конспект по шкале 0..5:
- coverage: насколько покрыты ключевые мысли
- faithfulness: насколько нет искажений/галлюцинаций
- structure: соблюдение формата и читаемости
- overall: общий итог

Также верни:
- missing_key_points: массив до 5 упущенных ключевых мыслей
- hallucinations: массив подозрительных/неподтверждённых утверждений
- improvement_suggestions: массив до 5 улучшений

Формат ответа строго JSON:
{{
  "coverage": number,
  "faithfulness": number,
  "structure": number,
  "overall": number,
  "missing_key_points": [string],
  "hallucinations": [string],
  "improvement_suggestions": [string]
}}

Транскрипт:
{transcript_short}

Конспект:
{summary_short}
"""

    response = CLIENT.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
        max_tokens=700,
    )

    raw = (response.choices[0].message.content or "").strip()
    try:
        data = json.loads(raw)
    except Exception:
        cleaned = raw.strip("` \n")
        cleaned = cleaned.replace("json\n", "", 1) if cleaned.lower().startswith("json\n") else cleaned
        data = json.loads(cleaned)

    return {
        "coverage": max(0.0, min(5.0, _safe_float(data.get("coverage")))),
        "faithfulness": max(0.0, min(5.0, _safe_float(data.get("faithfulness")))),
        "structure": max(0.0, min(5.0, _safe_float(data.get("structure")))),
        "overall": max(0.0, min(5.0, _safe_float(data.get("overall")))),
        "missing_key_points": list(data.get("missing_key_points") or [])[:5],
        "hallucinations": list(data.get("hallucinations") or [])[:5],
        "improvement_suggestions": list(data.get("improvement_suggestions") or [])[:5],
    }


def _evaluate_structure_heuristics(summary: str) -> dict[str, Any]:
    has_brief = "🎯 **Краткое резюме**" in summary
    has_important = "📌 **Важные вещи**" in summary
    has_conclusion = "🧭 **Вывод**" in summary
    bullets = _extract_bullet_lines(summary)
    bad_dash_bullets = [ln for ln in summary.splitlines() if ln.strip().startswith("-")]

    score = 5.0
    if not (has_brief and has_important and has_conclusion):
        score -= 2.0
    if len(bullets) < 5:
        score -= 1.0
    if bad_dash_bullets:
        score -= 1.0

    return {
        "has_brief": has_brief,
        "has_important": has_important,
        "has_conclusion": has_conclusion,
        "bullet_count": len(bullets),
        "dash_bullet_count": len(bad_dash_bullets),
        "structure_heuristic_score": max(0.0, min(5.0, score)),
    }


def _evolve_guidelines(state: dict[str, Any], evaluation: dict[str, Any], heuristics: dict[str, Any]):
    suggestions = evaluation.get("improvement_suggestions") or []
    guidelines: list[str] = list(state.get("style_guidelines") or [])

    for s in suggestions:
        s_norm = str(s).strip()
        if not s_norm:
            continue
        if s_norm not in guidelines:
            guidelines.append(s_norm)

    if heuristics.get("dash_bullet_count", 0) > 0 and "В блоке 📌 Важные вещи используй только маркеры ◾." not in guidelines:
        guidelines.append("В блоке 📌 Важные вещи используй только маркеры ◾.")

    state["style_guidelines"] = guidelines[-20:]


def evaluate_and_evolve(video_id: str, transcript: str, summary: str) -> dict[str, Any]:
    if not QUALITY_EVOLUTION_ENABLED:
        return {"enabled": False}

    state = _load_state()
    llm_eval = _evaluate_with_llm(transcript, summary)
    heuristics = _evaluate_structure_heuristics(summary)

    # Merge structure score conservatively
    merged_structure = (llm_eval["structure"] * 0.7) + (heuristics["structure_heuristic_score"] * 0.3)
    llm_eval["structure"] = round(merged_structure, 2)
    llm_eval["overall"] = round((llm_eval["coverage"] + llm_eval["faithfulness"] + llm_eval["structure"]) / 3, 2)

    _evolve_guidelines(state, llm_eval, heuristics)

    item = {
        "at": datetime.now(timezone.utc).isoformat(),
        "video_id": video_id,
        "scores": {
            "coverage": llm_eval["coverage"],
            "faithfulness": llm_eval["faithfulness"],
            "structure": llm_eval["structure"],
            "overall": llm_eval["overall"],
        },
        "missing_key_points": llm_eval["missing_key_points"],
        "hallucinations": llm_eval["hallucinations"],
        "improvement_suggestions": llm_eval["improvement_suggestions"],
        "heuristics": heuristics,
        "pass_threshold": llm_eval["overall"] >= QUALITY_MIN_OVERALL_SCORE,
    }

    history = list(state.get("history") or [])
    history.append(item)
    if len(history) > QUALITY_HISTORY_LIMIT:
        history = history[-QUALITY_HISTORY_LIMIT:]
    state["history"] = history

    total = len(history)
    state["total_evaluations"] = total

    for metric in ("coverage", "faithfulness", "structure", "overall"):
        avg = sum(h["scores"][metric] for h in history) / total
        state["running"][metric] = round(avg, 3)

    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)

    return {
        "enabled": True,
        "item": item,
        "running": state["running"],
        "style_guidelines": state["style_guidelines"],
    }


def get_quality_guidelines_text() -> str:
    if not QUALITY_EVOLUTION_ENABLED:
        return ""
    state = _load_state()
    guidelines = state.get("style_guidelines") or []
    if not guidelines:
        return ""
    return "\n".join(f"- {g}" for g in guidelines)
