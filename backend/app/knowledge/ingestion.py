"""Document loading, chunking, and masked ingestion into ChromaDB.

CSV rows become one document each (masking sensitive columns); text/pdf/md
docs are chunked. Masking happens BEFORE embedding — raw PII never reaches
the vector store, so retrieval cannot leak it either.
"""
from __future__ import annotations

import csv
import logging
import re
import uuid
from pathlib import Path

from . import config as kcfg

logger = logging.getLogger(__name__)


def mask_account_number(acc: str | None) -> str | None:
    if not acc:
        return acc
    return f"XXXXX{str(acc)[-4:]}"


def mask_utr(utr: str | None) -> str | None:
    if not utr:
        return utr
    s = str(utr)
    if len(s) <= 6:
        return s[:2] + "***"
    return s[:4] + "***" + s[-2:]


def _mask_record(record: dict) -> dict:
    out = dict(record)
    if "account_number" in out:
        out["account_number"] = mask_account_number(out.get("account_number"))
    if "utr_number" in out:
        out["utr_number"] = mask_utr(out.get("utr_number"))
    # metadata dicts carry CSV columns too — mask nested sensitive keys
    if isinstance(out.get("metadata"), dict):
        meta = dict(out["metadata"])
        if "account_number" in meta:
            meta["account_number"] = mask_account_number(meta.get("account_number"))
        if "utr_number" in meta:
            meta["utr_number"] = mask_utr(meta.get("utr_number"))
        out["metadata"] = meta
    # Content strings can embed sensitive values as text ("key: value" lines
    # from CSV rows) — mask those too, by key name.
    if "content" in out and isinstance(out["content"], str):
        text = out["content"]
        text = re.sub(
            r"(account_number\s*:\s*)(\S+)",
            lambda m: m.group(1) + (mask_account_number(m.group(2)) or m.group(2)),
            text,
        )
        text = re.sub(
            r"(utr_number\s*:\s*)(\S+)",
            lambda m: m.group(1) + (mask_utr(m.group(2)) or m.group(2)),
            text,
        )
        out["content"] = text
    return out


def load_csv_rows(file_path: Path) -> list[dict]:
    """Each CSV row → {content, metadata}, sensitive columns masked."""
    rows: list[dict] = []
    with open(file_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for i, row in enumerate(reader):
            clean = _mask_record({k: v for k, v in row.items() if v is not None})
            content = "\n".join(f"{k}: {v}" for k, v in clean.items())
            metadata = {
                "source": file_path.name,
                "kind": "csv_row",
                "row": i,
                **{k: str(v) for k, v in clean.items()},
            }
            rows.append({"content": content, "metadata": metadata})
    return rows


def load_text_file(file_path: Path) -> list[dict]:
    """txt/md/pdf → whole document, metadata stamped. PDF via pypdf if present."""
    suffix = file_path.suffix.lower()
    text = ""
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("pypdf is required for PDF ingestion") from exc
        text = "\n\n".join(
            page.extract_text() or "" for page in PdfReader(str(file_path)).pages
        )
    else:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return []
    rel = str(file_path.name)
    return [{
        "content": text,
        "metadata": {"source": rel, "kind": "document"},
    }]


def chunk_text(text: str) -> list[str]:
    """Simple paragraph-anchored chunker (no langchain dependency)."""
    size, overlap = kcfg.CHUNK_SIZE, kcfg.CHUNK_OVERLAP
    if len(text) <= size:
        return [text] if text.strip() else []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        # prefer breaking on a paragraph/line boundary inside the window
        if end < len(text):
            cut = max(
                text.rfind("\n\n", start, end),
                text.rfind("\n", start, end),
            )
            if cut > start + size // 2:
                end = cut
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def load_documents(data_dir: Path | None = None) -> list[dict]:
    """Load every supported file under data_dir into ingest-ready records."""
    data_dir = data_dir or (Path(kcfg.KNOWLEDGE_DATA_DIR) if kcfg.KNOWLEDGE_DATA_DIR else None)
    if data_dir is None or not Path(data_dir).exists():
        return []
    records: list[dict] = []
    for path in sorted(Path(data_dir).rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        try:
            if suffix == ".csv":
                records.extend(load_csv_rows(path))
            elif suffix in (".txt", ".md", ".pdf"):
                records.extend(load_text_file(path))
            else:
                logger.debug("skipping unsupported file %s", path.name)
        except Exception as exc:
            logger.error("failed to load %s: %s", path.name, exc)
    return records


def ingest_records(records: list[dict]) -> int:
    """Chunk + embed + store. Returns number of chunks added.

    This is the knowledge-layer's mask boundary: every record passes through
    _mask_record here regardless of its source, so sensitive values can never
    reach the vector store even via the raw-text API path."""
    from .store import get_store

    texts: list[str] = []
    metadatas: list[dict] = []
    for rec in records:
        masked = _mask_record(rec)
        content, metadata = masked["content"], masked["metadata"]
        if metadata.get("kind") == "csv_row":
            # one document per row — no chunking, keeps records retrievable
            texts.append(content)
            metadatas.append(metadata)
        else:
            for piece in chunk_text(content):
                texts.append(piece)
                metadatas.append(metadata)
    if not texts:
        return 0
    ids = [str(uuid.uuid4()) for _ in texts]
    return get_store().add(texts, metadatas, ids)


def ingest_directory(data_dir: Path | None = None) -> int:
    return ingest_records(load_documents(data_dir))


def ingest_texts(texts: list[str], metadatas: list[dict]) -> int:
    """Ingest raw strings (API path) — chunked, metadata stamped."""
    records = [
        {"content": t, "metadata": {**m, "kind": m.get("kind", "document")}}
        for t, m in zip(texts, metadatas)
    ]
    return ingest_records(records)
