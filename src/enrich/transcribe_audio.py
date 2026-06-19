"""Transcribe audio attachments (.opus, .mp3, .m4a, etc.) via Mistral voxtral-mini.

Supports batch processing with configurable concurrency (BATCH_SIZE env var,
default 5).  Already-transcribed records are skipped on re-run so the script
is safe to resume after interruption.
"""

import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import imageio_ffmpeg
from dotenv import load_dotenv
from openai import OpenAI, OpenAIError

load_dotenv()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
INPUT_JSONL = os.path.join(DATA_DIR, "2_enrich", "0_unzipped.jsonl")
INPUT_ATTACHMENTS = os.path.join(DATA_DIR, "1_parser", "attachments")
OUTPUT_JSONL = os.path.join(DATA_DIR, "2_enrich", "a_audio.jsonl")
TMP_DIR = os.path.join(DATA_DIR, "2_enrich", "transcribe_tmp")

SUPPORTED = {".opus", ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm"}
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "5"))


def _init_client() -> OpenAI:
    api_key = os.getenv("MISTRAL_API_KEY_TRANSCRIBE")
    base_url = os.getenv("MISTRAL_BASE_URL")
    if not api_key:
        raise ValueError("MISTRAL_API_KEY_TRANSCRIBE not set")
    if not base_url:
        raise ValueError("MISTRAL_BASE_URL not set")
    return OpenAI(api_key=api_key, base_url=base_url)


def convert_to_mp3(src: Path, dst: Path) -> None:
    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ffmpeg_path, "-y", "-i", str(src), "-acodec", "libmp3lame", str(dst)]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffmpeg failed: {e.stderr.decode(errors='ignore')}")


def transcribe(client: OpenAI, audio_path: Path) -> str:
    with audio_path.open("rb") as f:
        try:
            result = client.audio.transcriptions.create(
                model="voxtral-mini-latest",
                file=f,
                response_format="json",
                prompt="Die folgende Audio Datei enthält eine Sprachnachricht aus Whatsapp. Der Kontext ist Fehler auf einem Boot.",
            )
        except OpenAIError as e:
            raise RuntimeError(f"Transcription failed: {e}")
    return result.text if hasattr(result, "text") else str(result)


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


def _prepare_audio(att_path: Path) -> Path:
    """Convert .opus → .mp3 if necessary and return the path to feed the API."""
    if att_path.suffix.lower() == ".opus":
        mp3_path = Path(TMP_DIR) / (att_path.stem + ".mp3")
        if not mp3_path.exists():
            print(f"  Converting {att_path.name} → MP3")
            convert_to_mp3(att_path, mp3_path)
        return mp3_path
    return att_path


def _transcribe_attachment(client: OpenAI, attach: dict) -> tuple[dict, str | None]:
    """Transcribe a single attachment dict; returns (attach, transcript | None)."""
    att_path = Path(INPUT_ATTACHMENTS) / attach.get("path", "")
    ext = att_path.suffix.lower()
    if ext not in SUPPORTED:
        return attach, None

    audio_path = _prepare_audio(att_path)
    transcript = transcribe(client, audio_path)
    return attach, transcript


def _process_record(
    client: OpenAI,
    data: dict,
) -> dict:
    """Process all audio attachments of one JSONL record in parallel."""
    attachments = data.get("attachments", [])
    audio_attachments = [
        a for a in attachments
        if Path(INPUT_ATTACHMENTS).joinpath(a.get("path", "")).suffix.lower() in SUPPORTED
    ]

    if not audio_attachments:
        return data

    # Transcribe audio attachments concurrently within the record
    with ThreadPoolExecutor(max_workers=min(BATCH_SIZE, len(audio_attachments))) as pool:
        futures = {
            pool.submit(_transcribe_attachment, client, a): a
            for a in audio_attachments
        }
        for future in as_completed(futures):
            attach, transcript = future.result()
            if transcript is not None:
                attach["audio_transcription"] = transcript

    return data


# ── main ──────────────────────────────────────────────────────────────

def main():
    os.makedirs(os.path.dirname(OUTPUT_JSONL), exist_ok=True)
    os.makedirs(TMP_DIR, exist_ok=True)
    client = _init_client()

    with open(INPUT_JSONL, "r", encoding="utf-8") as inp:
        lines = inp.readlines()
    total = len(lines)

    done_ids = _load_done_ids(OUTPUT_JSONL)
    if done_ids:
        print(f"Resuming – {len(done_ids)} records already processed, skipping them.")

    # Open in append mode so we can resume
    with open(OUTPUT_JSONL, "a", encoding="utf-8") as out:
        # Process records in batches
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
                            processed = rec  # write original without transcription
                        out.write(json.dumps(processed, ensure_ascii=False) + "\n")
                        print(f"[{pidx}/{total}] {processed['id']}")

                out.flush()
                pending.clear()
                pending_indices.clear()

    shutil.rmtree(TMP_DIR, ignore_errors=True)
    print(f"\n✅ Done – wrote {OUTPUT_JSONL}")


if __name__ == "__main__":
    main()
