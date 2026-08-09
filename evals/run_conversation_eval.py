import argparse
import json

from evals.conversation_evaluator import evaluate_conversations


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate chat requirement collection")
    parser.add_argument(
        "--live",
        action="store_true",
        help="call the configured DeepSeek model instead of simulated extraction",
    )
    args = parser.parse_args()
    result = evaluate_conversations(live=args.live)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
