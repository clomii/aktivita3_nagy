from __future__ import annotations

import hashlib
import html
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path


@dataclass(frozen=True)
class OfficialSourceSpec:
    doc_id: str
    title: str
    url: str
    tags: tuple[str, ...]


OFFICIAL_SOURCES: tuple[OfficialSourceSpec, ...] = (
    OfficialSourceSpec(
        "official_ai_act_full",
        "Full official EU AI Act snapshot - Regulation (EU) 2024/1689",
        "https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng",
        ("official", "ai-act", "eur-lex", "full-text"),
    ),
    OfficialSourceSpec(
        "official_gdpr_full",
        "Full official GDPR snapshot - Regulation (EU) 2016/679",
        "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32016R0679",
        ("official", "gdpr", "eur-lex", "full-text"),
    ),
    OfficialSourceSpec(
        "official_ec_data_protection_rules",
        "European Commission data protection rules for business and organisations",
        "https://commission.europa.eu/law/law-topic/data-protection/eu-data-protection-rules_en",
        ("official", "gdpr", "commission", "data-protection"),
    ),
    OfficialSourceSpec(
        "official_ec_gdpr_principles",
        "European Commission GDPR principles",
        "https://commission.europa.eu/law/law-topic/data-protection/rules-business-and-organisations/principles-gdpr_en",
        ("official", "gdpr", "commission", "principles"),
    ),
    OfficialSourceSpec(
        "official_edpb_consent_guidelines",
        "EDPB Guidelines 05/2020 on consent under Regulation 2016/679",
        "https://www.edpb.europa.eu/our-work-tools/our-documents/guidelines/guidelines-052020-consent-under-regulation-2016679_en",
        ("official", "gdpr", "edpb", "consent"),
    ),
    OfficialSourceSpec(
        "official_edpb_wp29_endorsed_guidelines",
        "EDPB endorsed WP29 guidelines",
        "https://www.edpb.europa.eu/our-work-tools/general-guidance/endorsed-wp29-guidelines_en",
        ("official", "gdpr", "edpb", "guidelines"),
    ),
    OfficialSourceSpec(
        "official_gpai_code_practice",
        "European Commission GPAI Code of Practice",
        "https://digital-strategy.ec.europa.eu/en/policies/contents-code-gpai",
        ("official", "ai-act", "gpai", "commission"),
    ),
)

ALLOWED_HOSTS = {
    "eur-lex.europa.eu",
    "commission.europa.eu",
    "www.edpb.europa.eu",
    "digital-strategy.ec.europa.eu",
}


class _TextExtractor(HTMLParser):
    block_tags = {
        "article",
        "aside",
        "br",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "main",
        "p",
        "section",
        "td",
        "th",
        "tr",
    }
    ignored_tags = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self.ignored_tags:
            self.ignored_depth += 1
        if self.ignored_depth == 0 and tag in self.block_tags:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.ignored_tags and self.ignored_depth:
            self.ignored_depth -= 1
        if self.ignored_depth == 0 and tag in self.block_tags:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.ignored_depth == 0:
            self.parts.append(data)

    def text(self) -> str:
        return _clean_text(" ".join(self.parts))


def download_official_sources(data_dir: Path, timeout: int = 45) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    official_dir = data_dir / "official"
    raw_dir = official_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    sources: list[dict[str, object]] = []
    metadata: list[dict[str, object]] = []
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for spec in OFFICIAL_SOURCES:
        parsed = urllib.parse.urlparse(spec.url)
        if parsed.hostname not in ALLOWED_HOSTS:
            raise ValueError(f"Host is not allowlisted for official source: {spec.url}")
        payload, content_type = _fetch(spec.url, timeout=timeout)
        extracted = _payload_to_text(payload, content_type, spec.url)
        if len(extracted.split()) < 80:
            raise RuntimeError(f"Downloaded source is unexpectedly short: {spec.doc_id}")

        path = raw_dir / f"{spec.doc_id}.md"
        digest = hashlib.sha256(payload).hexdigest()
        body = (
            f"# {spec.title}\n\n"
            f"Source URL: {spec.url}\n\n"
            f"Fetched at UTC: {fetched_at}\n\n"
            f"SHA256: {digest}\n\n"
            "Official snapshot text follows.\n\n"
            f"{extracted}\n"
        )
        path.write_text(body, encoding="utf-8")

        sources.append(
            {
                "doc_id": spec.doc_id,
                "title": spec.title,
                "fmt": "md",
                "source_url": spec.url,
                "path": path.as_posix(),
                "access": "public",
                "tags": list(spec.tags),
                "is_distractor": False,
            }
        )
        metadata.append(
            {
                **asdict(spec),
                "fetched_at_utc": fetched_at,
                "content_type": content_type,
                "bytes": len(payload),
                "sha256": digest,
                "local_path": path.as_posix(),
                "word_count": len(extracted.split()),
            }
        )

    (official_dir / "sources.json").write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")
    (official_dir / "fetch_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return sources, metadata


def _fetch(url: str, timeout: int) -> tuple[bytes, str]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5",
            "User-Agent": "tuke-nlp-activity3-official-source-fetcher/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(), response.headers.get("Content-Type", "")
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        raise RuntimeError(f"Could not fetch official source {url}: {exc}") from exc


def _payload_to_text(payload: bytes, content_type: str, url: str) -> str:
    charset = _charset_from_content_type(content_type) or "utf-8"
    raw = payload.decode(charset, errors="replace")
    if "<html" in raw[:1000].lower() or "text/html" in content_type.lower() or url.endswith("_en"):
        extractor = _TextExtractor()
        extractor.feed(raw)
        return extractor.text()
    return _clean_text(html.unescape(raw))


def _charset_from_content_type(content_type: str) -> str | None:
    match = re.search(r"charset=([^;\s]+)", content_type, flags=re.I)
    return match.group(1).strip("\"'") if match else None


def _clean_text(text: str) -> str:
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = []
    previous = ""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            if previous:
                lines.append("")
            previous = ""
            continue
        if _is_navigation_noise(line):
            continue
        if line != previous:
            lines.append(line)
        previous = line
    return "\n".join(lines).strip()


def _is_navigation_noise(line: str) -> bool:
    lowered = line.lower()
    noisy = {
        "skip to main content",
        "search",
        "menu",
        "close",
        "cookies",
        "language",
        "login",
        "share this page",
        "print this page",
    }
    return lowered in noisy or (len(line) <= 2 and not line.isalnum())
