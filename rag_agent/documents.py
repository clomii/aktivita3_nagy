from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from textwrap import wrap
from typing import Iterable

from .seed_data import DOCUMENTS, QUESTIONS, SeedDocument


@dataclass(frozen=True)
class SourceDocument:
    doc_id: str
    title: str
    fmt: str
    source_url: str
    path: str
    access: str
    tags: list[str]
    is_distractor: bool = False


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    title: str
    text: str
    source_url: str
    access: str
    tags: list[str]
    is_distractor: bool = False
    token_start: int = 0
    token_end: int = 0

    def preview(self, n: int = 180) -> str:
        compact = " ".join(self.text.split())
        return compact[:n] + ("..." if len(compact) > n else "")


def _clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _simple_pdf_bytes(title: str, body: str) -> bytes:
    """Create a tiny valid text PDF without external dependencies."""
    lines = [title, ""] + wrap(_clean_text(body), width=92)
    text_ops = ["BT", "/F1 10 Tf", "50 790 Td", "14 TL"]
    for line in lines[:54]:
        text_ops.append(f"({_escape_pdf_text(line)}) Tj")
        text_ops.append("T*")
    text_ops.append("ET")
    stream = "\n".join(text_ops).encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{i} 0 obj\n".encode("ascii"))
        out.extend(obj)
        out.extend(b"\nendobj\n")
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(out)


def materialize_corpus(raw_dir: Path, eval_path: Path) -> list[SourceDocument]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    eval_path.parent.mkdir(parents=True, exist_ok=True)
    sources: list[SourceDocument] = []

    for document in DOCUMENTS:
        suffix = {"html": ".html", "md": ".md", "pdf": ".pdf"}[document.fmt]
        path = raw_dir / f"{document.doc_id}{suffix}"
        if document.fmt == "html":
            body = html.escape(_clean_text(document.content)).replace("\n", "<br>\n")
            path.write_text(
                f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(document.title)}</title></head>"
                f"<body><article><h1>{html.escape(document.title)}</h1><p>{body}</p></article></body></html>\n",
                encoding="utf-8",
            )
        elif document.fmt == "md":
            path.write_text(f"# {document.title}\n\n{_clean_text(document.content)}\n", encoding="utf-8")
        else:
            path.write_bytes(_simple_pdf_bytes(document.title, document.content))

        sources.append(
            SourceDocument(
                doc_id=document.doc_id,
                title=document.title,
                fmt=document.fmt,
                source_url=document.source_url,
                path=str(path.as_posix()),
                access=document.access,
                tags=list(document.tags),
                is_distractor=document.is_distractor,
            )
        )

    sources.extend(load_official_sources(raw_dir.parent))

    manifest = raw_dir.parent / "sources.json"
    manifest.write_text(
        json.dumps([asdict(item) for item in sources], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    eval_path.write_text(json.dumps(QUESTIONS, ensure_ascii=False, indent=2), encoding="utf-8")
    return sources


def load_official_sources(data_dir: Path) -> list[SourceDocument]:
    manifest_path = data_dir / "official" / "sources.json"
    if not manifest_path.exists():
        return []
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    sources: list[SourceDocument] = []
    for item in data:
        path = Path(item["path"])
        if not path.exists():
            continue
        sources.append(
            SourceDocument(
                doc_id=item["doc_id"],
                title=item["title"],
                fmt=item["fmt"],
                source_url=item["source_url"],
                path=item["path"],
                access=item["access"],
                tags=list(item["tags"]),
                is_distractor=bool(item.get("is_distractor", False)),
            )
        )
    return sources


def load_sources(raw_dir: Path) -> list[SourceDocument]:
    manifest_path = raw_dir.parent / "sources.json"
    if not manifest_path.exists():
        materialize_corpus(raw_dir, Path("data/eval/questions.json"))
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    return [SourceDocument(**item) for item in data]


def read_document(source: SourceDocument) -> str:
    path = Path(source.path)
    if source.fmt == "html":
        raw = path.read_text(encoding="utf-8")
        raw = re.sub(r"<(script|style).*?</\1>", " ", raw, flags=re.I | re.S)
        raw = re.sub(r"<[^>]+>", " ", raw)
        return _clean_text(html.unescape(raw))
    if source.fmt == "md":
        return _clean_text(path.read_text(encoding="utf-8"))
    return _extract_pdf_text(path)


def _extract_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        if text.strip():
            return _clean_text(text)
    except Exception:
        pass

    raw = path.read_bytes().decode("latin-1", errors="ignore")
    matches = re.findall(r"(?<!\\)\((.*?)(?<!\\)\)\s*Tj", raw, flags=re.S)
    text = "\n".join(match.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\") for match in matches)
    return _clean_text(text)


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-ZÀ-ž0-9_./:-]+", text.lower())


def chunk_document(source: SourceDocument, chunk_tokens: int, overlap: int) -> list[Chunk]:
    text = read_document(source)
    if "official" in source.tags:
        semantic_chunks = _chunk_semantic_sections(source, text, chunk_tokens=chunk_tokens, overlap=overlap)
        if semantic_chunks:
            return semantic_chunks
    tokens = text.split()
    if not tokens:
        return []
    chunks: list[Chunk] = []
    step = max(1, chunk_tokens - overlap)
    start = 0
    index = 0
    while start < len(tokens):
        end = min(len(tokens), start + chunk_tokens)
        chunk_text = " ".join(tokens[start:end])
        chunks.append(
            Chunk(
                chunk_id=f"{source.doc_id}::c{index:03d}",
                doc_id=source.doc_id,
                title=source.title,
                text=chunk_text,
                source_url=source.source_url,
                access=source.access,
                tags=source.tags,
                is_distractor=source.is_distractor,
                token_start=start,
                token_end=end,
            )
        )
        if end == len(tokens):
            break
        start += step
        index += 1
    return chunks


def _chunk_semantic_sections(source: SourceDocument, text: str, chunk_tokens: int, overlap: int) -> list[Chunk]:
    sections = _split_semantic_sections(text)
    if not sections:
        return []
    chunks: list[Chunk] = []
    index = 0
    global_start = 0
    step = max(1, chunk_tokens - overlap)
    for heading, section_text in sections:
        tokens = section_text.split()
        if not tokens:
            continue
        start = 0
        while start < len(tokens):
            end = min(len(tokens), start + chunk_tokens)
            chunk_text = " ".join(tokens[start:end])
            if heading and heading not in chunk_text[:160]:
                chunk_text = f"{heading}. {chunk_text}"
            chunks.append(
                Chunk(
                    chunk_id=f"{source.doc_id}::c{index:03d}",
                    doc_id=source.doc_id,
                    title=source.title,
                    text=chunk_text,
                    source_url=source.source_url,
                    access=source.access,
                    tags=source.tags,
                    is_distractor=source.is_distractor,
                    token_start=global_start + start,
                    token_end=global_start + end,
                )
            )
            index += 1
            if end == len(tokens):
                break
            start += step
        global_start += len(tokens)
    return chunks


def _split_semantic_sections(text: str) -> list[tuple[str, str]]:
    heading_re = re.compile(
        r"^(Article\s+\d+[a-zA-Z]?|CHAPTER\s+[IVXLC]+|SECTION\s+\d+|ANNEX\s+[IVXLC\d]+|"
        r"Recital\s+\d+|[0-9]+\.\s+[A-Z][A-Za-z ,;:/()-]{6,120})\b",
        flags=re.I,
    )
    sections: list[tuple[str, list[str]]] = []
    current_heading = "Official source overview"
    current_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if current_lines:
                current_lines.append("")
            continue
        if heading_re.match(stripped) and len(" ".join(current_lines).split()) >= 80:
            sections.append((current_heading, current_lines))
            current_heading = stripped[:180]
            current_lines = [stripped]
        else:
            if heading_re.match(stripped) and len(" ".join(current_lines).split()) < 80:
                current_heading = stripped[:180]
            current_lines.append(stripped)
    if current_lines:
        sections.append((current_heading, current_lines))

    prepared = [(heading, _clean_text("\n".join(lines))) for heading, lines in sections]
    prepared = [(heading, body) for heading, body in prepared if len(body.split()) >= 40]
    if len(prepared) <= 1 and len(text.split()) > 0:
        return [("Official source full text", text)]
    return prepared


def build_chunks(
    raw_dir: Path,
    chunk_tokens: int = 450,
    overlap: int = 80,
    include_secret: bool = True,
) -> list[Chunk]:
    sources = load_sources(raw_dir)
    chunks: list[Chunk] = []
    for source in sources:
        if source.access == "secret" and not include_secret:
            continue
        chunks.extend(chunk_document(source, chunk_tokens=chunk_tokens, overlap=overlap))
    return chunks


def save_chunks(chunks: Iterable[Chunk], processed_dir: Path) -> Path:
    processed_dir.mkdir(parents=True, exist_ok=True)
    path = processed_dir / "chunks.json"
    path.write_text(
        json.dumps([asdict(chunk) for chunk in chunks], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_questions(eval_path: Path) -> list[dict[str, object]]:
    if not eval_path.exists():
        materialize_corpus(Path("data/raw"), eval_path)
    return json.loads(eval_path.read_text(encoding="utf-8"))
