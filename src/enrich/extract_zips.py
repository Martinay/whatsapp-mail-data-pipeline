"""Extract zip archives: unzip contents and add each file as a new attachment."""

import json
import os
import zipfile
from pathlib import Path

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
INPUT_JSONL = os.path.join(DATA_DIR, "1_parser", "messages.jsonl")
ATTACHMENTS_DIR = os.path.join(DATA_DIR, "1_parser", "attachments")
OUTPUT_JSONL = os.path.join(DATA_DIR, "2_enrich", "0_unzipped.jsonl")


def extract_zip(zip_path: Path, message_id: str, start_idx: int) -> list[dict]:
    """Extract files from a zip archive and return new attachment dicts."""
    new_attachments: list[dict] = []

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.infolist():
                # Skip directories and macOS resource forks
                if member.is_dir() or member.filename.startswith("__MACOSX"):
                    continue

                # Build a safe output filename
                original_name = Path(member.filename).name
                if not original_name:
                    continue

                ext = Path(original_name).suffix or ".bin"
                idx = start_idx + len(new_attachments)
                att_filename = f"{message_id}_{idx}{ext}"
                out_path = Path(ATTACHMENTS_DIR) / att_filename

                # Extract the single member
                data = zf.read(member.filename)
                out_path.write_bytes(data)

                new_attachments.append({
                    "id": idx,
                    "path": att_filename,
                    "original_filename": original_name,
                })
                print(f"    Extracted: {original_name} → {att_filename}")
    except (zipfile.BadZipFile, Exception) as e:
        print(f"  Warning: Failed to extract {zip_path.name}: {e}")

    return new_attachments


def main():
    os.makedirs(os.path.dirname(OUTPUT_JSONL), exist_ok=True)

    with open(INPUT_JSONL, "r", encoding="utf-8") as inp, \
         open(OUTPUT_JSONL, "w", encoding="utf-8") as out:

        lines = inp.readlines()
        total = len(lines)

        for idx, line in enumerate(lines, 1):
            data = json.loads(line)

            attachments = data.get("attachments", [])
            new_attachments: list[dict] = []
            # Track the next available attachment index
            max_id = max((a.get("id", 0) for a in attachments), default=-1) + 1

            for attach in attachments:
                att_path = Path(ATTACHMENTS_DIR) / attach.get("path", "")
                if att_path.suffix.lower() == ".zip" and att_path.exists():
                    print(f"  Unzipping {att_path.name}")
                    extracted = extract_zip(att_path, data["id"], max_id)
                    new_attachments.extend(extracted)
                    max_id += len(extracted)
                    # Drop the .zip entry itself — its contents replace it
                else:
                    new_attachments.append(attach)

            data["attachments"] = new_attachments
            out.write(json.dumps(data, ensure_ascii=False) + "\n")
            print(f"[{idx}/{total}] {data['id']}")


if __name__ == "__main__":
    main()
