"""Describe image attachments via Mistral mistral-medium (vision + OCR).

Supports batch processing with configurable concurrency (BATCH_SIZE env var,
default 5).  Already-described records are skipped on re-run so the script
is safe to resume after interruption.
"""

import base64
import json
import mimetypes
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
INPUT_JSONL = os.path.join(DATA_DIR, "2_enrich", "a2_videos.jsonl")
ATTACHMENTS_DIR = os.path.join(DATA_DIR, "1_parser", "attachments")
OUTPUT_JSONL = os.path.join(DATA_DIR, "2_enrich", "b_pictures.jsonl")

SUPPORTED = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".tiff", ".ico"}
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "8"))

PROMPT = (
    "Was ist auf diesem Bild zu sehen? Benutze maximal 4 Sätze für die Zusammenfassung in Deutsch. "
    "Falls Text zu sehen ist, transkribiere diesen. Wenn der Text in einer anderen Sprache als Deutsch ist, "
    'übersetze ihn zusätzlich in das Deutsche. Das Ausgabe Format ist Json: '
    '{"summary": "<Zusammenfassung>", "transcription": "<Transkription>", '
    '"language": "<Sprache der Transkription>", "translation": "<Transkription Translated>"} '
    'Wenn es keinen Text auf dem Bild gibt, lasse die Felder "transcription", "language" und "translation" leer.'
)


def _init_client() -> OpenAI:
    api_key = os.getenv("MISTRAL_API_KEY_VISION")
    base_url = os.getenv("MISTRAL_BASE_URL")
    if not api_key:
        raise ValueError("MISTRAL_API_KEY_VISION not set")
    if not base_url:
        raise ValueError("MISTRAL_BASE_URL not set")
    return OpenAI(api_key=api_key, base_url=base_url)


def summarize(client: OpenAI, image_path: Path) -> str:
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")

    mime = mimetypes.guess_type(str(image_path))[0] or "application/octet-stream"

    result = client.chat.completions.create(
        model="mistral-medium-latest",
        messages=[
            {"role": "system", "content": "Du bist ein Hilfsbereiter Assistent, welcher Bilder analysiert."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            },
        ],
        timeout=30,
        max_tokens=5000,
    )

    if not result.choices or result.choices[0].message.content is None:
        raise RuntimeError(f"No response for {image_path}")
    return result.choices[0].message.content


# ── helpers for batch processing ──────────────────────────────────────

def _load_done_ids(path: str) -> set[str]:
    """Return the set of record IDs already written to the output file."""
    done: set[str] = set()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(json.loads(line)["id"])
                except (json.JSONDecodeError, KeyError):
                    continue
    return done


def _summarize_attachment(client: OpenAI, attach: dict) -> tuple[dict, str | None]:
    """Summarize a single image attachment; returns (attach, summary | None)."""
    att_path = Path(ATTACHMENTS_DIR) / attach.get("path", "")
    if att_path.suffix.lower() not in SUPPORTED:
        return attach, None
    if not att_path.exists():
        return attach, None
    summary = summarize(client, att_path)
    return attach, summary


def _process_record(client: OpenAI, data: dict) -> dict:
    """Process all image attachments of one JSONL record in parallel."""
    attachments = data.get("attachments", [])
    image_attachments = [
        a for a in attachments
        if Path(ATTACHMENTS_DIR).joinpath(a.get("path", "")).suffix.lower() in SUPPORTED
        and Path(ATTACHMENTS_DIR).joinpath(a.get("path", "")).exists()
    ]

    if not image_attachments:
        return data

    # Summarize image attachments concurrently within the record
    with ThreadPoolExecutor(max_workers=min(BATCH_SIZE, len(image_attachments))) as pool:
        futures = {
            pool.submit(_summarize_attachment, client, a): a
            for a in image_attachments
        }
        for future in as_completed(futures):
            attach, summary = future.result()
            if summary is not None:
                attach["image_summary"] = summary
                print(f"  Summarized {attach.get('path', '?')}")

    return data


# ── main ──────────────────────────────────────────────────────────────

def main():
    os.makedirs(os.path.dirname(OUTPUT_JSONL), exist_ok=True)
    client = _init_client()

    with open(INPUT_JSONL, "r", encoding="utf-8") as inp:
        lines = inp.readlines()
    total = len(lines)

    done_ids = _load_done_ids(OUTPUT_JSONL)
    if done_ids:
        print(f"Resuming – {len(done_ids)} records already processed, skipping them.")

    # Open in append mode so we can resume
    with open(OUTPUT_JSONL, "a", encoding="utf-8") as out:
        pending: list[dict] = []
        pending_indices: list[int] = []

        for idx, line in enumerate(lines, 1):
            data = json.loads(line)

            if data["id"] in done_ids:
                print(f"[{idx}/{total}] {data['id']}  (skip – already done)")
                continue

            pending.append(data)
            pending_indices.append(idx)

            if len(pending) >= BATCH_SIZE or idx == total:
                # Process the batch of records concurrently
                with ThreadPoolExecutor(max_workers=min(BATCH_SIZE, len(pending))) as pool:
                    futures = {
                        pool.submit(_process_record, client, rec): (rec, pidx)
                        for rec, pidx in zip(pending, pending_indices)
                    }
                    for future in as_completed(futures):
                        rec, pidx = futures[future]
                        try:
                            processed = future.result()
                        except Exception as exc:
                            print(f"  ⚠ Record {rec['id']} failed: {exc}")
                            processed = rec  # write original without enrichment
                        out.write(json.dumps(processed, ensure_ascii=False) + "\n")
                        print(f"[{pidx}/{total}] {processed['id']}")

                out.flush()
                pending.clear()
                pending_indices.clear()

    print(f"\n✅ Done – wrote {OUTPUT_JSONL}")


if __name__ == "__main__":
    main()
