"""Parse .eml files from data/source/mail_eml into JSONL (one JSON object per line).

Incremental: re-runs skip messages already present in the output file.
Email parsing and deduplication reuse helpers from mbox_parser.
"""

from __future__ import annotations

import email
import json
import os
from email.utils import parsedate_to_datetime
from glob import glob

try:
    from src.parser.mbox_parser import (
        extract_attachments,
        get_body,
        get_conversation_id,
        load_blocklist,
        make_message_id,
    )
except ModuleNotFoundError:
    from mbox_parser import (  # type: ignore[no-redef]
        extract_attachments,
        get_body,
        get_conversation_id,
        load_blocklist,
        make_message_id,
    )

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
OUTPUT_DIR = os.path.join(DATA_DIR, "1_parser")
ATTACHMENTS_DIR = os.path.join(OUTPUT_DIR, "attachments")
SOURCE_DIR = os.path.join(DATA_DIR, "source", "mail_eml")
JSONL_PATH = os.path.join(OUTPUT_DIR, "messages.jsonl")


def _collect_eml_files() -> list[str]:
    """Return sorted list of .eml file paths in SOURCE_DIR (recursive)."""
    pattern = os.path.join(SOURCE_DIR, "**", "*.eml")
    return sorted(glob(pattern, recursive=True))


def _load_seen_ids() -> set[str]:
    """Read existing message IDs from the JSONL file for deduplication."""
    seen: set[str] = set()
    if os.path.exists(JSONL_PATH):
        with open(JSONL_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        seen.add(json.loads(line)["id"])
                    except (json.JSONDecodeError, KeyError):
                        pass
    return seen


def _process_single_eml(eml_path: str, out, seen_ids: set[str], blocked_ids: set[str]) -> int:
    """Process one .eml file. Returns 1 if written, 0 if skipped."""
    with open(eml_path, "rb") as f:
        msg = email.message_from_bytes(f.read())

    message_id = make_message_id(msg)

    if message_id in blocked_ids:
        print(f"  {message_id} (blocklisted – skipped)")
        return 0

    if message_id in seen_ids:
        print(f"  {message_id} (duplicate – skipped)")
        return 0
    seen_ids.add(message_id)

    try:
        start_date = parsedate_to_datetime(msg["date"])
        timestamp = start_date.strftime("%Y-%m-%d %H:%M")
    except Exception:
        timestamp = ""

    message_json = {
        "id": message_id,
        "channel": "Mail",
        "timestamp": timestamp,
        "sender": msg["from"],
        "receiver": msg["to"],
        "cc": msg["cc"],
        "bcc": msg["bcc"],
        "subject": msg["subject"],
        "text": get_body(msg),
        "conversation_id": get_conversation_id(msg),
        "chat_name": None,
        "attachments": extract_attachments(msg, message_id),
    }

    out.write(json.dumps(message_json, ensure_ascii=False) + "\n")
    print(f"  {message_id}")
    return 1


def main():
    os.makedirs(ATTACHMENTS_DIR, exist_ok=True)

    eml_files = _collect_eml_files()
    if not eml_files:
        print(f"No .eml files found in {SOURCE_DIR}")
        return

    print(f"Found {len(eml_files)} .eml file(s) in {SOURCE_DIR}")

    seen_ids = _load_seen_ids()
    blocked_ids = load_blocklist()
    if blocked_ids:
        print(f"Blocklist active: {len(blocked_ids)} message ID(s) will be skipped.")
    total_written = 0

    with open(JSONL_PATH, "a", encoding="utf-8") as out:
        for file_idx, eml_path in enumerate(eml_files, 1):
            eml_name = os.path.basename(eml_path)
            print(f"\n=== [{file_idx}/{len(eml_files)}] Processing {eml_name} ===")
            total_written += _process_single_eml(eml_path, out, seen_ids, blocked_ids)

    print(f"\nDone – appended {total_written} email(s) to {JSONL_PATH}")


if __name__ == "__main__":
    main()
