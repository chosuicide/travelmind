import json
from pathlib import Path
from typing import Callable

from app.conversations.extractor import extract_message
from app.conversations.normalizer import normalize_patch
from app.conversations.policy import next_question
from app.conversations.schemas import ExtractedMessage
from app.conversations.state import merge_draft


CASES_PATH = Path(__file__).resolve().parent / "conversation_cases.json"


def load_conversation_cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]


# === 对话评测器：同一批多轮用例既能跑模拟回归，也能跑真实 DeepSeek ===
# 流程：消息 → 提取操作 → 归一化/合并 → 草稿与下一问断言 → 汇总
def evaluate_conversations(
    *,
    live: bool = False,
    extractor: Callable[[dict, str], ExtractedMessage] = extract_message,
) -> dict:
    results = []
    for case in load_conversation_cases():
        draft = dict(case.get("initial_draft") or {})
        turn_results = []
        for turn in case["turns"]:
            try:
                extraction = (
                    extractor(draft, turn["message"])
                    if live
                    else ExtractedMessage.model_validate(
                        turn["simulated_extraction"]
                    )
                )
            except Exception as exc:
                turn_results.append(
                    {
                        "message": turn["message"],
                        "intent": None,
                        "draft": draft,
                        "question": next_question(draft),
                        "passed": False,
                        "failures": [
                            f"extractor failed with {type(exc).__name__}"
                        ],
                    }
                )
                break
            if extraction.intent == "update_draft":
                patch, clears = normalize_patch(
                    draft,
                    extraction.patch,
                    extraction.clear_fields,
                )
                draft = merge_draft(
                    draft,
                    patch,
                    clear_fields=clears,
                    add_interests=extraction.add_interests,
                    remove_interests=extraction.remove_interests,
                ).model_dump(mode="json", exclude_none=True)

            failures = []
            expected_intent = turn.get("expected_intent", "update_draft")
            if extraction.intent != expected_intent:
                failures.append(
                    f"intent expected {expected_intent}, got {extraction.intent}"
                )
            for field, expected in turn.get("expected_draft", {}).items():
                if draft.get(field) != expected:
                    failures.append(
                        f"{field} expected {expected!r}, got {draft.get(field)!r}"
                    )
            for field in turn.get("expected_absent_fields", []):
                if field in draft:
                    failures.append(f"{field} should be absent")
            question = next_question(draft)
            expected_text = turn.get("expected_question_contains")
            if expected_text and expected_text not in question:
                failures.append(
                    f"question should contain {expected_text!r}, got {question!r}"
                )
            turn_results.append(
                {
                    "message": turn["message"],
                    "intent": extraction.intent,
                    "draft": draft,
                    "question": question,
                    "passed": not failures,
                    "failures": failures,
                }
            )
        results.append(
            {
                "case_id": case["id"],
                "passed": all(turn["passed"] for turn in turn_results),
                "turns": turn_results,
            }
        )
    return {
        "live": live,
        "passed": all(case["passed"] for case in results),
        "total_cases": len(results),
        "passed_cases": sum(case["passed"] for case in results),
        "cases": results,
    }
