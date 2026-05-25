import json
import logging
from pathlib import Path

from quality_agent import evaluate_and_evolve
from services import get_transcript, summarize_with_multi_agent_pipeline

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def load_dataset(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    items = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        items.append(json.loads(line))
    return items


def run(dataset_path: str = "eval_dataset.jsonl"):
    dataset = load_dataset(dataset_path)
    if not dataset:
        logger.info("Dataset is empty. Add rows to eval_dataset.jsonl")
        return

    results = []

    for idx, row in enumerate(dataset, start=1):
        video_id = row.get("video_id")
        if not video_id:
            continue

        logger.info(f"[{idx}/{len(dataset)}] video_id={video_id}")
        transcript, _ = get_transcript(video_id)
        if not transcript:
            logger.warning(f"Skip {video_id}: transcript unavailable")
            continue

        summary, _ = summarize_with_multi_agent_pipeline(transcript)
        quality = evaluate_and_evolve(video_id=video_id, transcript=transcript, summary=summary)
        item = quality.get("item") or {}
        scores = item.get("scores") or {}

        results.append(
            {
                "video_id": video_id,
                "overall": scores.get("overall"),
                "coverage": scores.get("coverage"),
                "faithfulness": scores.get("faithfulness"),
                "structure": scores.get("structure"),
                "pass_threshold": item.get("pass_threshold"),
            }
        )

    out = Path("eval_results.json")
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    if results:
        avg = {
            "overall": round(sum((r.get("overall") or 0) for r in results) / len(results), 3),
            "coverage": round(sum((r.get("coverage") or 0) for r in results) / len(results), 3),
            "faithfulness": round(sum((r.get("faithfulness") or 0) for r in results) / len(results), 3),
            "structure": round(sum((r.get("structure") or 0) for r in results) / len(results), 3),
        }
    else:
        avg = {"overall": 0, "coverage": 0, "faithfulness": 0, "structure": 0}

    report = [
        "# Eval report",
        "",
        f"Total evaluated: {len(results)}",
        f"Avg overall: {avg['overall']}",
        f"Avg coverage: {avg['coverage']}",
        f"Avg faithfulness: {avg['faithfulness']}",
        f"Avg structure: {avg['structure']}",
        "",
        "See details in eval_results.json and quality_state.json",
    ]
    Path("eval_report.md").write_text("\n".join(report), encoding="utf-8")

    logger.info("Benchmark completed. Files: eval_results.json, eval_report.md")


if __name__ == "__main__":
    run()
