"""Phase 1: ingest the client's SOP documents into the Pinecone knowledge base.

Pipeline
  1. Discover policy files in data/policy (.md .txt .pdf .csv .xlsx).
  2. Parse each with a format-aware chunking strategy:
       .md   -> split by headings (#, ##, ###), then into ~600-char chunks; heading path kept as context
       .txt  -> ~600-char chunks with 60-char overlap
       .pdf  -> page by page, then chunked; page number kept
       .csv/.xlsx -> one chunk per row, rendered as "column: value | column: value"
  3. Incremental sync with a SHA-256 fingerprint per file:
       unchanged -> skipped, changed -> old chunks replaced, deleted -> chunks removed from the index.
  4. Upsert in batches. Pinecone embeds the text server-side with llama-text-embed-v2.

Usage:  python scripts/ingest_sop_pinecone.py [--force]
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.sop_store import NAMESPACE, TEXT_FIELD, get_index, index_exists  # noqa: E402

POLICY_DIR = config.DATA_DIR / "policy"
CACHE_FILE = config.DATA_DIR / "cache" / "sop_hash_cache.json"
SUPPORTED = {".md", ".txt", ".pdf", ".csv", ".xlsx"}
BATCH_SIZE = 90  # Pinecone accepts up to 96 records per upsert_records call

splitter = RecursiveCharacterTextSplitter(chunk_size=600, chunk_overlap=60)
md_splitter = MarkdownHeaderTextSplitter(
    headers_to_split_on=[("#", "h1"), ("##", "h2"), ("###", "h3")], strip_headers=True
)


# ---------------------------------------------------------------- parsing
def parse_markdown(path: Path) -> list[tuple[str, str]]:
    chunks = []
    for section in md_splitter.split_text(path.read_text(encoding="utf-8")):
        heading = " > ".join(section.metadata[h] for h in ("h1", "h2", "h3") if h in section.metadata)
        for piece in splitter.split_text(section.page_content):
            chunks.append((heading, piece))
    return chunks


def parse_text(path: Path) -> list[tuple[str, str]]:
    return [("", piece) for piece in splitter.split_text(path.read_text(encoding="utf-8"))]


def parse_pdf(path: Path) -> list[tuple[str, str]]:
    chunks = []
    for page_no, page in enumerate(PdfReader(path).pages, start=1):
        text = (page.extract_text() or "").strip()
        chunks += [(f"Page {page_no}", piece) for piece in splitter.split_text(text)] if text else []
    return chunks


def parse_table(path: Path) -> list[tuple[str, str]]:
    df = pd.read_csv(path) if path.suffix == ".csv" else pd.read_excel(path)
    rows = []
    for row_no, row in enumerate(df.to_dict(orient="records"), start=1):
        rows.append((f"Row {row_no}", " | ".join(f"{col}: {val}" for col, val in row.items() if pd.notna(val))))
    return rows


PARSERS = {".md": parse_markdown, ".txt": parse_text, ".pdf": parse_pdf, ".csv": parse_table, ".xlsx": parse_table}


def build_records(path: Path) -> list[dict]:
    records = []
    for i, (section, text) in enumerate(PARSERS[path.suffix.lower()](path)):
        text = text.strip()
        if not text:
            continue
        records.append(
            {
                "_id": f"{path.name}::{i:04d}",
                # the heading path is embedded together with the body so each chunk keeps its context
                TEXT_FIELD: f"{section}\n{text}" if section else text,
                "source_file": path.name,
                "section": section,
                "file_format": path.suffix.lower().lstrip("."),
                "chunk_index": i,
                "doc_type": "compliance_sop",
            }
        )
    return records


# ---------------------------------------------------------------- index sync
def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def delete_file_chunks(index, file_name: str) -> int:
    ids = [getattr(item, "id", item) for page in index.list(prefix=f"{file_name}::", namespace=NAMESPACE) for item in page]
    for start in range(0, len(ids), 1000):
        index.delete(ids=ids[start:start + 1000], namespace=NAMESPACE)
    return len(ids)


def load_cache(fresh_index: bool, force: bool) -> dict:
    if fresh_index or force or not CACHE_FILE.exists():
        return {}
    return json.loads(CACHE_FILE.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync SOP documents into Pinecone.")
    parser.add_argument("--force", action="store_true", help="re-ingest every file even if unchanged")
    args = parser.parse_args()

    fresh_index = not index_exists()
    index = get_index(create_if_missing=True)
    cache = load_cache(fresh_index, args.force)

    files = {p.name: p for p in sorted(POLICY_DIR.iterdir()) if p.suffix.lower() in SUPPORTED}
    print(f"Found {len(files)} policy file(s) in {POLICY_DIR.relative_to(config.PROJECT_ROOT)}")

    for gone in sorted(set(cache) - set(files)):
        print(f"  - {gone}: deleted from disk, removed {delete_file_chunks(index, gone)} chunk(s)")
        cache.pop(gone)

    for name, path in files.items():
        digest = fingerprint(path)
        if cache.get(name) == digest:
            print(f"  = {name}: unchanged, skipped")
            continue
        removed = delete_file_chunks(index, name)
        records = build_records(path)
        for start in range(0, len(records), BATCH_SIZE):
            index.upsert_records(namespace=NAMESPACE, records=records[start:start + BATCH_SIZE])
        cache[name] = digest
        print(f"  + {name}: {len(records)} chunk(s) upserted" + (f" (replaced {removed})" if removed else ""))

    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    print("SOP knowledge base is in sync.")


if __name__ == "__main__":
    main()
