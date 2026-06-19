"""Parse standalone documents into JSONL (one JSON object per document).

Scans data/source/documents/ for supported file types (PDF, DOCX, Numbers,
etc.), extracts text content, and produces JSONL records with channel="Document".

For PDFs, page images are extracted via PyMuPDF and saved as separate
attachments so that the enrich pipeline's describe_pictures.py can generate
image summaries (important for image-heavy PDFs with little extractable text).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

# Add project root so we can import from sibling packages
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.enrich.extract_documents import ALL_SUPPORTED, extract_text_content

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
OUTPUT_DIR = os.path.join(DATA_DIR, "1_parser")
ATTACHMENTS_DIR = os.path.join(OUTPUT_DIR, "attachments")
SOURCE_DIR = os.path.join(DATA_DIR, "source", "documents")
JSONL_PATH = os.path.join(OUTPUT_DIR, "messages.jsonl")

# Threshold: if a PDF page yields fewer than this many characters of text,
# we consider it image-heavy and extract the page as an image.
IMAGE_HEAVY_CHARS_PER_PAGE = 50

# Image extensions to skip (they aren't "documents")
SKIP_EXTENSIONS = {".ds_store"}


def make_message_id(filepath: str) -> str:
    """Deterministic ID from the source file path."""
    return hashlib.sha256(filepath.encode()).hexdigest()[:16]


def _collect_document_files() -> list[Path]:
    """Return sorted list of supported document files in SOURCE_DIR."""
    if not os.path.isdir(SOURCE_DIR):
        return []

    files = []
    for name in sorted(os.listdir(SOURCE_DIR)):
        path = Path(SOURCE_DIR) / name
        if not path.is_file():
            continue
        if name.startswith("."):
            continue
        ext = path.suffix.lower()
        if ext in SKIP_EXTENSIONS:
            continue
        if ext in ALL_SUPPORTED:
            files.append(path)
        else:
            print(f"  Skipping unsupported file: {name}")
    return files


def _extract_pdf_page_images(
    pdf_path: Path, message_id: str, start_attachment_id: int
) -> list[dict]:
    """Extract page images from a PDF and save to attachments dir.

    Only extracts pages that are image-heavy (little extractable text).
    Returns a list of attachment dicts for the extracted images.
    """
    import pymupdf

    attachments = []
    attachment_id = start_attachment_id

    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception as e:
        print(f"  Warning: Could not open PDF for image extraction: {e}")
        return attachments

    for page_num in range(len(doc)):
        page = doc[page_num]
        page_text = page.get_text().strip()

        # Only extract image for pages with little text
        if len(page_text) > IMAGE_HEAVY_CHARS_PER_PAGE:
            continue

        try:
            # Render page to a PNG image at 150 DPI (good balance of quality/size)
            pix = page.get_pixmap(dpi=150)
            img_filename = f"{message_id}_{attachment_id}.png"
            img_path = Path(ATTACHMENTS_DIR) / img_filename
            pix.save(str(img_path))

            attachments.append({
                "id": attachment_id,
                "path": img_filename,
                "original_filename": f"{pdf_path.stem}_page_{page_num + 1}.png",
            })
            attachment_id += 1
        except Exception as e:
            print(f"  Warning: Failed to extract page {page_num + 1} image: {e}")

    doc.close()

    if attachments:
        print(f"  Extracted {len(attachments)} page image(s) from {pdf_path.name}")

    return attachments


def _get_file_timestamp(path: Path) -> str:
    """Get file modification time as 'YYYY-MM-DD HH:MM' string."""
    try:
        mtime = path.stat().st_mtime
        dt = _dt.datetime.fromtimestamp(mtime)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def main():
    os.makedirs(ATTACHMENTS_DIR, exist_ok=True)

    doc_files = _collect_document_files()
    if not doc_files:
        print(f"No document files found in {SOURCE_DIR}")
        return

    print(f"Found {len(doc_files)} document(s) in {SOURCE_DIR}")

    # Seed seen_ids from any messages already in the file so re-runs don't duplicate.
    seen_ids: set[str] = set()
    if os.path.exists(JSONL_PATH):
        with open(JSONL_PATH, encoding="utf-8") as existing:
            for line in existing:
                line = line.strip()
                if line:
                    try:
                        seen_ids.add(json.loads(line)["id"])
                    except (json.JSONDecodeError, KeyError):
                        pass

    written = 0

    # Append to JSONL (mbox_parser / whatsapp_parser may have already written)
    with open(JSONL_PATH, "a", encoding="utf-8") as out:
        for file_idx, doc_path in enumerate(doc_files, 1):
            message_id = make_message_id(str(doc_path))

            if message_id in seen_ids:
                print(f"  [{file_idx}/{len(doc_files)}] {doc_path.name} (duplicate – skipped)")
                continue
            seen_ids.add(message_id)

            # Extract text content
            text = extract_text_content(doc_path) or ""
            if not text:
                print(f"  [{file_idx}/{len(doc_files)}] {doc_path.name} (no text extracted)")

            # Copy source file to attachments
            ext = doc_path.suffix.lower()
            source_attachment_name = f"{message_id}_0{ext}"
            dest_path = Path(ATTACHMENTS_DIR) / source_attachment_name
            try:
                shutil.copy2(doc_path, dest_path)
            except Exception as e:
                print(f"  Warning: Could not copy {doc_path.name}: {e}")
                continue

            attachments = [
                {
                    "id": 0,
                    "path": source_attachment_name,
                    "original_filename": doc_path.name,
                }
            ]

            # For PDFs: extract page images from image-heavy pages
            if ext == ".pdf":
                page_images = _extract_pdf_page_images(doc_path, message_id, start_attachment_id=1)
                attachments.extend(page_images)

            # Build the message record
            message_json = {
                "id": message_id,
                "channel": "Document",
                "timestamp": _get_file_timestamp(doc_path),
                "sender": None,
                "receiver": None,
                "cc": None,
                "bcc": None,
                "subject": doc_path.stem,
                "text": text,
                "conversation_id": None,
                "chat_name": None,
                "attachments": attachments,
            }

            out.write(json.dumps(message_json, ensure_ascii=False) + "\n")
            att_count = len(attachments)
            print(
                f"  [{file_idx}/{len(doc_files)}] {message_id}  "
                f"{doc_path.name}  ({len(text)} chars, {att_count} attachment(s))"
            )
            written += 1

    print(f"\nDone – appended {written} document(s) to {JSONL_PATH}")


if __name__ == "__main__":
    main()
