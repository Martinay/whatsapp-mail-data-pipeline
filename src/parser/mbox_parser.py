"""Parse mbox file into JSONL (one JSON object per line)."""

import hashlib
import json
import mailbox
import mimetypes
import os
import re
from email.header import decode_header
from email.utils import parsedate_to_datetime


BLOCKLIST_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "source", "blocklist.json"
)


def load_blocklist() -> set[str]:
    """Load blocked message IDs from data/source/blocklist.json.

    Entries can be plain strings or objects with an ``id`` key
    (and an optional ``description``).  Returns an empty set if
    the file does not exist.
    """
    if not os.path.exists(BLOCKLIST_PATH):
        return set()
    with open(BLOCKLIST_PATH, encoding="utf-8") as f:
        data = json.load(f)
    ids: set[str] = set()
    for entry in data.get("blocked_message_ids", []):
        if isinstance(entry, dict):
            ids.add(entry["id"])
        else:
            ids.add(entry)
    return ids


DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
OUTPUT_DIR = os.path.join(DATA_DIR, "1_parser")
ATTACHMENTS_DIR = os.path.join(OUTPUT_DIR, "attachments")
SOURCE_DIR = os.path.join(DATA_DIR, "source", "mail")
JSONL_PATH = os.path.join(OUTPUT_DIR, "messages.jsonl")

# File extensions to skip when scanning the source directory
SKIP_EXTENSIONS = {".numbers", ".xlsx", ".csv", ".ds_store"}


def make_message_id(msg) -> str:
    """Deterministic ID from Message-ID header, falling back to sender+date+subject."""
    raw = msg["Message-ID"]
    if not raw:
        raw = f"{msg['from'] or ''}|{msg['date'] or ''}|{msg['subject'] or ''}"
    return hashlib.sha256(raw.encode(errors="ignore")).hexdigest()[:16]


def get_body(msg) -> str:
    if msg.is_multipart():
        parts = []
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload is not None:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        parts.append(payload.decode(charset, errors="replace"))
                    except LookupError:
                        parts.append(payload.decode("utf-8", errors="ignore"))
        return "\n".join(parts)
    else:
        payload = msg.get_payload(decode=True)
        if payload is not None:
            charset = msg.get_content_charset() or "utf-8"
            try:
                return payload.decode(charset, errors="replace")
            except LookupError:
                return payload.decode("utf-8", errors="ignore")
        return ""
    return ""


def sanitize_filename(raw_filename: str, content_type: str | None = None) -> str:
    """Decode RFC 2047 encoded filenames and clean up MIME artefacts."""
    if not raw_filename:
        return raw_filename

    # Attempt full RFC 2047 decoding of the filename
    parts = decode_header(raw_filename)
    decoded_parts: list[str] = []
    for data, charset in parts:
        if isinstance(data, bytes):
            decoded_parts.append(data.decode(charset or "utf-8", errors="ignore"))
        else:
            decoded_parts.append(data)
    filename = "".join(decoded_parts)

    # Strip any remaining MIME encoding fragments (e.g. =?UTF-8?Q?...?=)
    filename = re.sub(r"=\?[^?]*\?[BbQq]\?", "", filename)
    filename = filename.replace("?=", "")

    # Validate that the extension looks reasonable (only ASCII letters and digits)
    _, ext = os.path.splitext(filename)
    known_extensions = {
        ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
        ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp",
        ".mp4", ".mov", ".avi", ".mp3", ".wav", ".opus", ".ogg",
        ".htm", ".html", ".txt", ".csv", ".ics", ".zip", ".bin",
    }
    if ext.lower() not in known_extensions and content_type:
        # Extension looks wrong – guess from Content-Type
        guessed = mimetypes.guess_extension(content_type)
        if guessed:
            base = os.path.splitext(filename)[0]
            filename = base + guessed

    return filename


def extract_attachments(msg, message_id: str) -> list[dict]:
    attachments = []
    if not msg.is_multipart():
        return attachments

    attachment_count = 0
    for part in msg.walk():
        content_disposition = part.get("Content-Disposition", "")
        if "attachment" not in content_disposition:
            continue

        raw_filename = part.get_filename()
        content_type = part.get_content_type()
        filename = sanitize_filename(raw_filename, content_type)

        extension = ""
        if filename:
            _, extension = os.path.splitext(filename)
            extension = "".join(c for c in extension if c.isalnum())
            if extension:
                extension = "." + extension

        attachment_path = f"{message_id}_{attachment_count}{extension or '.bin'}"
        full_path = os.path.join(ATTACHMENTS_DIR, attachment_path)

        # Decode payload
        payload = None
        try:
            content_encoding = part.get("Content-Transfer-Encoding", "")
            if content_encoding.lower() == "base64":
                import base64

                raw = part.get_payload()
                if isinstance(raw, str):
                    payload = base64.b64decode("".join(raw.split()))
                else:
                    payload = raw
            else:
                payload = part.get_payload(decode=True)
        except Exception:
            try:
                import base64

                raw = part.get_payload()
                if isinstance(raw, str):
                    payload = base64.b64decode("".join(raw.split()))
                else:
                    payload = raw
            except Exception:
                continue

        if not payload:
            continue

        try:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "wb") as f:
                f.write(payload)
        except Exception:
            continue

        attachment_count += 1
        attachments.append(
            {
                "id": attachment_count - 1,
                "path": attachment_path,
                "original_filename": filename,
            }
        )

    return attachments


def get_conversation_id(msg) -> str | None:
    """Extract conversation thread ID from In-Reply-To or References headers."""
    in_reply_to = msg.get("In-Reply-To", "")
    if in_reply_to:
        return in_reply_to.strip().strip("<>")
    references = msg.get("References", "")
    if references:
        # First reference is the thread root
        first_ref = references.strip().split()[0]
        return first_ref.strip("<>")
    return None


def _collect_mbox_files() -> list[str]:
    """Return sorted list of mbox file paths in SOURCE_DIR, skipping non-mbox files."""
    files = []
    for name in sorted(os.listdir(SOURCE_DIR)):
        path = os.path.join(SOURCE_DIR, name)
        if not os.path.isfile(path):
            continue
        _, ext = os.path.splitext(name)
        if ext.lower() in SKIP_EXTENSIONS or name.startswith("."):
            continue
        files.append(path)
    return files


def main():
    os.makedirs(ATTACHMENTS_DIR, exist_ok=True)

    mbox_files = _collect_mbox_files()
    if not mbox_files:
        print(f"No mbox files found in {SOURCE_DIR}")
        return

    print(f"Found {len(mbox_files)} mbox file(s) in {SOURCE_DIR}")

    blocked_ids = load_blocklist()
    if blocked_ids:
        print(f"Blocklist active: {len(blocked_ids)} message ID(s) will be skipped.")

    seen_ids: set[str] = set()
    skipped = 0
    blocked = 0

    with open(JSONL_PATH, "w", encoding="utf-8") as out:
        for file_idx, mbox_path in enumerate(mbox_files, 1):
            mbox_name = os.path.basename(mbox_path)
            print(f"\n=== [{file_idx}/{len(mbox_files)}] Processing {mbox_name} ===")
            mbox = mailbox.mbox(mbox_path)
            total = len(mbox)

            for idx, msg in enumerate(mbox, 1):
                message_id = make_message_id(msg)

                if message_id in blocked_ids:
                    blocked += 1
                    print(f"  [{idx}/{total}] {message_id} (blocklisted – skipped)")
                    continue

                if message_id in seen_ids:
                    skipped += 1
                    print(f"  [{idx}/{total}] {message_id} (duplicate – skipped)")
                    continue
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
                print(f"  [{idx}/{total}] {message_id}")

    print(f"\nDone – wrote all messages to {JSONL_PATH} (skipped {skipped} duplicates, {blocked} blocklisted)")


if __name__ == "__main__":
    main()
