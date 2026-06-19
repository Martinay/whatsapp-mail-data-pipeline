"""Enrich attachments with extension, mimetype, size_bytes. Add word_count to messages."""

import json
import mimetypes
import os
from pathlib import Path

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
INPUT_JSONL = os.path.join(DATA_DIR, "2_enrich", "c_documents.jsonl")
ATTACHMENTS_DIR = os.path.join(DATA_DIR, "1_parser", "attachments")
OUTPUT_JSONL = os.path.join(DATA_DIR, "2_enrich", "d_metadata.jsonl")

mimetypes.init()


def main():
    os.makedirs(os.path.dirname(OUTPUT_JSONL), exist_ok=True)

    with open(INPUT_JSONL, "r", encoding="utf-8") as inp, \
         open(OUTPUT_JSONL, "w", encoding="utf-8") as out:

        lines = inp.readlines()
        total = len(lines)

        for idx, line in enumerate(lines, 1):
            data = json.loads(line)

            # Word count for message text
            text = data.get("text", "") or ""
            data["word_count"] = len(text.split()) if text.strip() else 0

            # Attachment metadata
            total_size = len(text.encode("utf-8"))
            for attach in data.get("attachments", []):
                ref = attach.get("original_filename", attach.get("path", ""))
                ext = Path(ref).suffix.lower()
                mime, _ = mimetypes.guess_type(ref, strict=False)

                att_path = Path(ATTACHMENTS_DIR) / attach.get("path", "")
                size = att_path.stat().st_size if att_path.exists() else 0

                attach["extension"] = ext
                attach["mimetype"] = mime or "application/octet-stream"
                attach["size_bytes"] = size
                total_size += size

            data["size_bytes"] = total_size

            out.write(json.dumps(data, ensure_ascii=False) + "\n")
            print(f"[{idx}/{total}] {data['id']}")


if __name__ == "__main__":
    main()
