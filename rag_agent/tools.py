from __future__ import annotations

import ast
import contextlib
import io
import json
import math
import operator
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

from .retrieval import HybridRetriever
from .schemas import ToolCallTrace


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class ToolError(RuntimeError):
    pass


class Toolset:
    def __init__(self, retriever: HybridRetriever) -> None:
        self.retriever = retriever

    def call(self, name: str, **kwargs: Any) -> tuple[Any, ToolCallTrace]:
        started_at = _iso()
        started = time.perf_counter()
        ok = True
        try:
            if name == "search_kb":
                result = self.search_kb(**kwargs)
            elif name == "calculator":
                result = calculator(**kwargs)
            elif name == "run_python":
                result = run_python(**kwargs)
            elif name == "eurlex_fetch":
                result = eurlex_fetch(**kwargs)
            else:
                raise ToolError(f"Unknown tool: {name}")
        except Exception as exc:
            ok = False
            result = {"error": str(exc)}
        ended_at = _iso()
        preview = json.dumps(result, ensure_ascii=False, default=str)
        trace = ToolCallTrace(
            tool=name,
            arguments=kwargs,
            ok=ok,
            started_at=started_at,
            ended_at=ended_at,
            latency_s=round(time.perf_counter() - started, 6),
            result_preview=preview[:700],
        )
        return result, trace

    def search_kb(self, query: str, top_k: int = 6, include_secret: bool = False) -> list[dict[str, object]]:
        results = self.retriever.search(query, top_k=top_k, include_secret=include_secret)
        return [result.to_dict() for result in results]


_ALLOWED_BINOPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARY: dict[type[ast.unaryop], Callable[[Any], Any]] = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def calculator(expr: str) -> dict[str, object]:
    tree = ast.parse(expr, mode="eval")

    def eval_node(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return eval_node(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
            return _ALLOWED_BINOPS[type(node.op)](eval_node(node.left), eval_node(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
            return _ALLOWED_UNARY[type(node.op)](eval_node(node.operand))
        raise ToolError("calculator supports only numeric expressions")

    value = eval_node(tree)
    return {"expr": expr, "result": value}


def run_python(code: str) -> dict[str, object]:
    parsed = ast.parse(code, mode="exec")
    blocked = (ast.Import, ast.ImportFrom, ast.With, ast.AsyncWith, ast.Lambda, ast.ClassDef, ast.FunctionDef)
    for node in ast.walk(parsed):
        if isinstance(node, blocked):
            raise ToolError("run_python sandbox blocks imports, with-blocks and definitions")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ToolError("dunder access is blocked")
    safe_globals = {
        "__builtins__": {
            "abs": abs,
            "min": min,
            "max": max,
            "sum": sum,
            "len": len,
            "range": range,
            "round": round,
            "print": print,
        },
        "math": math,
    }
    safe_locals: dict[str, object] = {}
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        result: object | None = None
        if parsed.body and isinstance(parsed.body[-1], ast.Expr):
            setup = ast.Module(body=parsed.body[:-1], type_ignores=[])
            ast.fix_missing_locations(setup)
            exec(compile(setup, "<sandbox>", "exec"), safe_globals, safe_locals)
            expr = ast.Expression(parsed.body[-1].value)
            ast.fix_missing_locations(expr)
            result = eval(compile(expr, "<sandbox>", "eval"), safe_globals, safe_locals)
        else:
            exec(compile(parsed, "<sandbox>", "exec"), safe_globals, safe_locals)
    output: dict[str, object] = {"stdout": buffer.getvalue().strip(), "ok": True}
    if result is not None:
        output["result"] = result
    return output


def eurlex_fetch(celex_id: str) -> dict[str, object]:
    celex_id = celex_id.strip().upper()
    cache = {
        "32024R1689": {
            "title": "Regulation (EU) 2024/1689 - Artificial Intelligence Act",
            "url": "https://eur-lex.europa.eu/eli/reg/2024/1689/",
            "summary": "AI Act with risk-based rules for prohibited practices, high-risk AI, transparency and GPAI.",
        },
        "32016R0679": {
            "title": "Regulation (EU) 2016/679 - General Data Protection Regulation",
            "url": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32016R0679",
            "summary": "GDPR rules for processing personal data, controller duties and data subject rights.",
        },
    }
    if celex_id not in cache:
        raise ToolError(f"CELEX {celex_id} is not in the allowlisted demo cache")

    url = cache[celex_id]["url"]
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname not in {"eur-lex.europa.eu"}:
        raise ToolError("external URL is not allowlisted")

    result = dict(cache[celex_id])
    if os.environ.get("LIVE_EURLEX") != "1":
        result["http_status"] = None
        result["live_fetch"] = False
        result["note"] = "Set LIVE_EURLEX=1 to perform a live EUR-Lex check; returned reproducible local CELEX cache."
        return result

    try:
        request = urllib.request.Request(url, headers={"User-Agent": "nlp-activity3-compliance-agent/1.0"})
        with urllib.request.urlopen(request, timeout=4) as response:
            result["http_status"] = response.status
            result["live_fetch"] = True
    except (urllib.error.URLError, TimeoutError, OSError):
        result["http_status"] = None
        result["live_fetch"] = False
        result["note"] = "Network unavailable; returned reproducible local CELEX cache."
    return result
