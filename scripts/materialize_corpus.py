from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag_agent.documents import build_chunks, materialize_corpus, save_chunks


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raw_dir = Path("data/raw")
    eval_path = Path("data/eval/questions.json")
    processed_dir = Path("data/processed")
    sources = materialize_corpus(raw_dir, eval_path)
    chunks = build_chunks(raw_dir, chunk_tokens=450, overlap=80, include_secret=True)
    save_chunks(chunks, processed_dir)
    print(f"Materialized {len(sources)} source documents and {len(chunks)} chunks.")
    print(f"Questions: {eval_path}")


if __name__ == "__main__":
    main()
