from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rag_agent.evaluator import run_level2_eval


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Run level-2 EU AI Act/GDPR agent benchmark.")
    parser.add_argument("--config", default="configs/level2.yaml", help="Path to JSON-compatible YAML config.")
    parser.add_argument("--quick", action="store_true", help="Run a small smoke-test subset.")
    parser.add_argument("--llm-judge", action="store_true", help="Use the configured judge model for S2_full_agent scoring.")
    args = parser.parse_args()

    summaries = run_level2_eval(Path(args.config), quick=args.quick, llm_judge=args.llm_judge)
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
