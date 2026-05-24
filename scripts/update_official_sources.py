from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag_agent.documents import build_chunks, materialize_corpus, save_chunks
from rag_agent.official_sources import download_official_sources


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Download official EU AI Act/GDPR source snapshots.")
    parser.add_argument("--data-dir", default="data", help="Project data directory.")
    parser.add_argument("--timeout", type=int, default=45, help="HTTP timeout per source in seconds.")
    parser.add_argument("--skip-index", action="store_true", help="Only download official snapshots; do not rebuild chunks.")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    sources, metadata = download_official_sources(data_dir, timeout=args.timeout)
    print(f"Downloaded {len(sources)} official source snapshots.")
    for item in metadata:
        print(f"- {item['doc_id']}: {item['word_count']} words, sha256={str(item['sha256'])[:12]}...")

    if not args.skip_index:
        raw_dir = data_dir / "raw"
        eval_path = data_dir / "eval" / "questions.json"
        processed_dir = data_dir / "processed"
        combined_sources = materialize_corpus(raw_dir, eval_path)
        chunks = build_chunks(raw_dir, chunk_tokens=450, overlap=80, include_secret=True)
        save_chunks(chunks, processed_dir)
        official_chunks = [chunk for chunk in chunks if "official" in chunk.tags]
        print(f"Combined manifest: {len(combined_sources)} documents.")
        print(f"Rebuilt index: {len(chunks)} chunks ({len(official_chunks)} official chunks).")


if __name__ == "__main__":
    main()
