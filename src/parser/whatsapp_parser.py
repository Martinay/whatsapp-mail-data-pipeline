"""Parse WhatsApp ZIP backup into JSONL (one JSON object per line)."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import uuid
import zipfile
from pathlib import Path


DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
OUTPUT_DIR = os.path.join(DATA_DIR, "1_parser")
ATTACHMENTS_DIR = os.path.join(OUTPUT_DIR, "attachments")
SOURCE_DIR = os.path.join(DATA_DIR, "source", "whatsapp")
EXTRACTION_BASE = os.path.join(OUTPUT_DIR, "whatsapp_extracted")
JSONL_PATH = os.path.join(OUTPUT_DIR, "messages.jsonl")

BRACKET_LINE_RE = re.compile(r"^\u200e?\[([^\]]+)\]\s([^:]+?):\s?(.*)$")
ATTACHMENT_RE = re.compile(r"<Anhang:\s([^>]+)>", re.IGNORECASE)
CALL_KEYWORDS = ("voice call", "video call", "sprachanruf", "videoanruf", "anruf")
TIMESTAMP_FMT = "%d.%m.%y, %H:%M:%S"

# Prefix used by WhatsApp export filenames
_CHAT_PREFIX = "WhatsApp Chat - "


def _is_blank(text: str) -> bool:
    return not text.replace("\u200e", "").strip()


def _chat_name_from_filename(zip_name: str) -> str:
    """Derive a chat name from the ZIP filename.

    'WhatsApp Chat - Nils Heyde.zip' → 'Nils Heyde'
    """
    stem = os.path.splitext(zip_name)[0]
    if stem.startswith(_CHAT_PREFIX):
        return stem[len(_CHAT_PREFIX):].strip()
    return stem.strip()


def parse_chat(text: str) -> list[dict]:
    msgs: list[dict] = []
    current: dict | None = None

    for raw in text.splitlines():
        m = BRACKET_LINE_RE.match(raw)
        if m:
            if current is not None and not _is_blank(current["content"]):
                msgs.append(current)
            ts_raw, sender, content = m.groups()
            try:
                ts = _dt.datetime.strptime(ts_raw.strip(), TIMESTAMP_FMT)
            except ValueError:
                raise ValueError(f"Invalid timestamp: {ts_raw}")
            current = {
                "timestamp": ts,
                "sender": sender.strip(),
                "content": content.replace("\u200e", "").strip(),
            }
        else:
            if current is not None:
                current["content"] += "\n" + raw.replace("\u200e", "")

    if current is not None and not _is_blank(current["content"]):
        msgs.append(current)
    return msgs


def extract_chat_name(messages: list[dict]) -> str:
    """Derive chat name from participants.

    For a 1:1 chat this is the other person's name/number.
    For a group chat this is all unique sender names joined.
    """
    senders = list(dict.fromkeys(msg["sender"] for msg in messages))
    return " & ".join(senders)


def make_message_id(timestamp: _dt.datetime, sender: str, text: str, chat_name: str = "") -> str:
    """Deterministic ID from chat + timestamp + sender + text prefix."""
    raw = f"{chat_name}|{timestamp.isoformat()}|{sender}|{text[:100]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def process_attachments(
    text: str, message_id: str, extraction_dir: Path
) -> tuple[str, list[dict]]:
    """Extract attachment references from text. Returns cleaned text and attachment list."""
    matches = list(ATTACHMENT_RE.finditer(text))
    if not matches:
        return text, []

    attachments = []
    attachment_count = 0
    for m in matches:
        text = text.replace(m.group(0), "")
        filename = m.group(1).strip()
        src = extraction_dir / filename
        if not src.exists():
            continue
        ext = src.suffix.lower()
        tgt_name = f"{message_id}_{attachment_count}{ext}"
        dest = Path(ATTACHMENTS_DIR) / tgt_name
        shutil.copy2(src, dest)
        attachments.append(
            {"id": attachment_count, "path": tgt_name, "original_filename": filename}
        )
        attachment_count += 1

    return text.strip(), attachments


def _collect_zip_files() -> list[str]:
    """Return sorted list of .zip file paths in SOURCE_DIR."""
    files = []
    for name in sorted(os.listdir(SOURCE_DIR)):
        path = os.path.join(SOURCE_DIR, name)
        if os.path.isfile(path) and name.lower().endswith(".zip"):
            files.append(path)
    return files


def _process_single_zip(zip_path: str, out, seen_ids: set[str]) -> int:
    """Process one WhatsApp ZIP backup. Returns number of messages written."""
    zip_name = os.path.basename(zip_path)
    chat_name = _chat_name_from_filename(zip_name)

    # Extract into a per-zip subdirectory to avoid file collisions
    extraction_dir = Path(EXTRACTION_BASE) / Path(zip_name).stem
    os.makedirs(extraction_dir, exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extraction_dir)

    # Find _chat.txt
    chat_file = extraction_dir / "_chat.txt"
    if not chat_file.exists():
        print(f"  ⚠ _chat.txt not found in {zip_name}, skipping")
        return 0

    raw_messages = parse_chat(
        chat_file.read_text(encoding="utf-8", errors="ignore")
    )
    if not raw_messages:
        print(f"  ⚠ No messages parsed from {zip_name}, skipping")
        return 0

    print(f"  Parsed {len(raw_messages)} messages  |  Chat: {chat_name}")

    written = 0
    for idx, msg in enumerate(raw_messages, 1):
        message_id = make_message_id(
            msg["timestamp"], msg["sender"], msg["content"], chat_name
        )

        if message_id in seen_ids:
            print(f"    [{idx}/{len(raw_messages)}] {message_id} (duplicate – skipped)")
            continue
        seen_ids.add(message_id)

        clean_text, attachments = process_attachments(
            msg["content"], message_id, extraction_dir
        )

        message_json = {
            "id": message_id,
            "channel": "Whatsapp",
            "timestamp": msg["timestamp"].strftime("%Y-%m-%d %H:%M"),
            "sender": msg["sender"],
            "receiver": chat_name,
            "cc": None,
            "bcc": None,
            "subject": None,
            "text": clean_text,
            "conversation_id": chat_name,
            "chat_name": chat_name,
            "attachments": attachments,
        }

        out.write(json.dumps(message_json, ensure_ascii=False) + "\n")
        print(f"    [{idx}/{len(raw_messages)}] {message_id}")
        written += 1

    return written


def main():
    os.makedirs(ATTACHMENTS_DIR, exist_ok=True)

    zip_files = _collect_zip_files()
    if not zip_files:
        print(f"No WhatsApp ZIP files found in {SOURCE_DIR}")
        return

    print(f"Found {len(zip_files)} WhatsApp ZIP file(s) in {SOURCE_DIR}")

    total_messages = 0
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
    # Append to JSONL (mbox_parser may have already written to the same file)
    with open(JSONL_PATH, "a", encoding="utf-8") as out:
        for file_idx, zip_path in enumerate(zip_files, 1):
            zip_name = os.path.basename(zip_path)
            print(f"\n=== [{file_idx}/{len(zip_files)}] Processing {zip_name} ===")
            total_messages += _process_single_zip(zip_path, out, seen_ids)

    print(f"\nDone – appended {total_messages} WhatsApp messages to {JSONL_PATH}")


if __name__ == "__main__":
    main()
