from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class Citation:
    doc_id: str
    chunk_id: str
    quote: str
    source_url: str = ""

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.doc_id:
            errors.append("citation.doc_id is empty")
        if not self.chunk_id:
            errors.append("citation.chunk_id is empty")
        if not self.quote or len(self.quote.strip()) < 8:
            errors.append("citation.quote is too short")
        return errors


@dataclass
class AnswerSchema:
    answer: str
    citations: list[Citation] = field(default_factory=list)
    confidence: float = 0.0
    abstained: bool = False
    reasoning_trace: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["confidence"] = round(float(self.confidence), 4)
        return data

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not isinstance(self.answer, str) or not self.answer.strip():
            errors.append("answer must be a non-empty string")
        if not 0.0 <= float(self.confidence) <= 1.0:
            errors.append("confidence must be between 0 and 1")
        if not isinstance(self.abstained, bool):
            errors.append("abstained must be a boolean")
        tool_only = any("calculator" in step or "eurlex_fetch" in step or "run_python" in step for step in self.reasoning_trace)
        if not self.abstained and not self.citations and not tool_only:
            errors.append("non-abstained answer must include citations")
        for index, citation in enumerate(self.citations):
            for error in citation.validate():
                errors.append(f"citations[{index}].{error}")
        return errors


@dataclass
class PlanStep:
    id: str
    objective: str
    tool: str | None = None
    query: str | None = None


@dataclass
class TypedPlan:
    question: str
    steps: list[PlanStep]
    tool_budget: int = 6
    risk_level: Literal["low", "medium", "high"] = "medium"

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.steps:
            errors.append("plan must contain at least one step")
        if self.tool_budget < 1 or self.tool_budget > 12:
            errors.append("tool_budget must be in range 1..12")
        seen: set[str] = set()
        for step in self.steps:
            if step.id in seen:
                errors.append(f"duplicate step id {step.id}")
            seen.add(step.id)
        return errors


@dataclass
class ToolCallTrace:
    tool: str
    arguments: dict[str, Any]
    ok: bool
    started_at: str
    ended_at: str
    latency_s: float
    result_preview: str


@dataclass
class JudgeScore:
    faithfulness: float
    relevance: float
    citation_accuracy: float
    tool_correctness: float
    abstain_accuracy: float
    overall: float
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunTrace:
    run_id: str
    variant: str
    question_id: str
    question: str
    model: str
    judge_model: str
    started_at: str = field(default_factory=_now_iso)
    ended_at: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: list[ToolCallTrace] = field(default_factory=list)
    state_events: list[dict[str, Any]] = field(default_factory=list)
    retrieved_chunk_ids: list[str] = field(default_factory=list)
    answer: AnswerSchema | None = None
    judge_score: JudgeScore | None = None
    errors: list[str] = field(default_factory=list)

    def finish(self) -> None:
        self.ended_at = _now_iso()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.answer is not None:
            data["answer"] = self.answer.to_dict()
        if self.judge_score is not None:
            data["judge_score"] = self.judge_score.to_dict()
        return data

    def add_state(self, state: str, message: str, **payload: Any) -> None:
        self.state_events.append({"at": _now_iso(), "state": state, "message": message, **payload})


def count_tokens(text: str) -> int:
    return len([part for part in text.replace("\n", " ").split(" ") if part.strip()])
