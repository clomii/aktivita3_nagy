from __future__ import annotations

import argparse
import json
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .agents import AgentSettings, MultiAgentAssistant
from .documents import materialize_corpus
from .evaluator import load_config
from .retrieval import HybridRetriever


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "configs" / "level2.yaml"
DEFAULT_PAGE = ROOT / "report" / "live_demo.html"
MAX_BODY_BYTES = 12_000


class DemoRuntime:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self._assistant: MultiAgentAssistant | None = None

    def assistant(self) -> MultiAgentAssistant:
        if self._assistant is not None:
            return self._assistant

        config = load_config(self.config_path)
        raw_dir = ROOT / str(config["paths"]["raw_dir"])
        eval_set = ROOT / str(config["paths"]["eval_set"])
        materialize_corpus(raw_dir, eval_set)

        retrieval_cfg = config["retrieval"]
        retriever = HybridRetriever.from_paths(
            raw_dir,
            chunk_tokens=int(retrieval_cfg["chunk_tokens"]),
            overlap=int(retrieval_cfg["chunk_overlap"]),
            include_secret=True,
            rrf_k=int(retrieval_cfg["rrf_k"]),
            candidate_k=int(retrieval_cfg["candidate_k"]),
            final_k=int(retrieval_cfg["final_k"]),
            embedding_model=str(retrieval_cfg.get("embedding_model", "nomic-embed-text")),
            dense_backend=str(retrieval_cfg.get("dense_backend", "ollama")),
            reranker_mode=str(retrieval_cfg.get("reranker_mode", "llm")),
            reranker_model=str(retrieval_cfg.get("reranker_model", "qwen2.5:3b")),
        )
        self._assistant = MultiAgentAssistant(
            retriever,
            model=config["models"]["agent_models"][0],
            judge_model=config["models"]["judge_model"],
            settings=AgentSettings(
                max_tool_calls=int(config["agent"]["max_tool_calls"]),
                max_replans=int(config["agent"]["max_replans"]),
                abstain_score_threshold=float(retrieval_cfg["abstain_score_threshold"]),
                provider=config["models"].get("provider", "auto"),
            ),
        )
        return self._assistant

    def ask(self, question: str, label: str = "web") -> dict[str, Any]:
        answer, trace = self.assistant().answer(question, question_id=label)
        return {
            "answer": answer.to_dict(),
            "trace": {
                "run_id": trace.run_id,
                "variant": trace.variant,
                "question_id": trace.question_id,
                "state_events": trace.state_events,
                "retrieved_chunk_ids": trace.retrieved_chunk_ids,
                "tool_calls": [call.__dict__ for call in trace.tool_calls],
                "input_tokens": trace.input_tokens,
                "output_tokens": trace.output_tokens,
                "errors": trace.errors,
                "started_at": trace.started_at,
                "ended_at": trace.ended_at,
            },
        }


def make_handler(runtime: DemoRuntime, page_path: Path) -> type[BaseHTTPRequestHandler]:
    class LiveDemoHandler(BaseHTTPRequestHandler):
        server_version = "RAGLiveDemo/1.0"

        def do_GET(self) -> None:
            if self.path in {"/", "/demo", "/live-demo"}:
                self._send_file(page_path, "text/html; charset=utf-8")
                return
            if self.path == "/api/health":
                self._send_json({"ok": True, "service": "live-demo"})
                return
            self._send_json({"error": "not_found"}, status=404)

        def do_POST(self) -> None:
            if self.path != "/api/ask":
                self._send_json({"error": "not_found"}, status=404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_BODY_BYTES:
                    self._send_json({"error": "invalid_body_size"}, status=400)
                    return
                raw_body = self.rfile.read(length)
                try:
                    body_text = raw_body.decode("utf-8")
                except UnicodeDecodeError:
                    body_text = raw_body.decode("cp1250")
                payload = json.loads(body_text)
                question = str(payload.get("question", "")).strip()
                label = str(payload.get("label", "web")).strip()[:40] or "web"
                if not question:
                    self._send_json({"error": "question_required"}, status=400)
                    return
                if len(question) > 2000:
                    self._send_json({"error": "question_too_long"}, status=400)
                    return
                result = runtime.ask(question, label=label)
                self._send_json(result)
            except Exception as exc:  # pragma: no cover - surfaced to browser during demo
                self._send_json(
                    {
                        "error": "server_error",
                        "message": str(exc),
                        "traceback": traceback.format_exc(limit=4),
                    },
                    status=500,
                )

        def log_message(self, format: str, *args: Any) -> None:
            sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

        def _send_file(self, path: Path, content_type: str) -> None:
            if not path.exists():
                self._send_json({"error": f"missing file: {path}"}, status=500)
                return
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

    return LiveDemoHandler


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Run the EU AI Act/GDPR live demo web page.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--page", type=Path, default=DEFAULT_PAGE)
    args = parser.parse_args()

    runtime = DemoRuntime(args.config)
    handler = make_handler(runtime, args.page)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Live demo is running at http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
