"""Describe video attachments: extract keyframes → Mistral vision summary + audio transcription.

Supports batch processing with configurable concurrency (BATCH_SIZE env var,
default 3).  Already-described records are skipped on re-run so the script
is safe to resume after interruption.
"""

from __future__ import annotations

import base64
import copy
import json
import mimetypes
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI, OpenAIError

load_dotenv()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
INPUT_JSONL = os.path.join(DATA_DIR, "2_enrich", "a_audio.jsonl")
ATTACHMENTS_DIR = os.path.join(DATA_DIR, "1_parser", "attachments")
OUTPUT_JSONL = os.path.join(DATA_DIR, "2_enrich", "a2_videos.jsonl")
TMP_DIR = os.path.join(DATA_DIR, "2_enrich", "video_tmp")

SUPPORTED_VIDEO = {".mp4", ".webm", ".mov", ".avi", ".mkv", ".3gp"}

KEYFRAME_INTERVAL_SEC = 5
MAX_KEYFRAMES = 6
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "5"))


def _init_vision_client() -> OpenAI:
    api_key = os.getenv("MISTRAL_API_KEY_VISION")
    base_url = os.getenv("MISTRAL_BASE_URL")
    if not api_key:
        raise ValueError("MISTRAL_API_KEY_VISION not set")
    if not base_url:
        raise ValueError("MISTRAL_BASE_URL not set")
    return OpenAI(api_key=api_key, base_url=base_url)


def _init_transcribe_client() -> OpenAI:
    api_key = os.getenv("MISTRAL_API_KEY_TRANSCRIBE")
    base_url = os.getenv("MISTRAL_BASE_URL")
    if not api_key:
        raise ValueError("MISTRAL_API_KEY_TRANSCRIBE not set")
    if not base_url:
        raise ValueError("MISTRAL_BASE_URL not set")
    return OpenAI(api_key=api_key, base_url=base_url)


def get_video_duration(video_path: Path) -> float:
    """Get video duration in seconds via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        return 0.0


def extract_keyframes(video_path: Path, output_dir: Path) -> list[Path]:
    """Extract keyframes from video at regular intervals."""
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(output_dir / "frame_%03d.jpg")

    duration = get_video_duration(video_path)
    if duration <= 0:
        return []

    # Calculate interval to get at most MAX_KEYFRAMES frames
    interval = max(1.0, duration / MAX_KEYFRAMES)

    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vf", f"fps=1/{interval:.1f}",
        "-frames:v", str(MAX_KEYFRAMES),
        "-q:v", "2",
        pattern,
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        print(f"  Warning: Keyframe extraction failed: {e}")
        return []

    frames = sorted(output_dir.glob("frame_*.jpg"))
    return frames


def extract_audio_track(video_path: Path, output_dir: Path) -> Path | None:
    """Extract audio track from video as mp3."""
    audio_path = output_dir / f"{video_path.stem}_audio.mp3"
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-acodec", "libmp3lame", "-q:a", "4",
        str(audio_path),
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if audio_path.exists() and audio_path.stat().st_size > 0:
            return audio_path
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    return None


def describe_keyframes(client: OpenAI, frames: list[Path]) -> str:
    """Send keyframes to Mistral vision model for a visual summary."""
    content: list[dict] = [
        {
            "type": "text",
            "text": (
                "Diese Bilder sind Einzelbilder (Keyframes) aus einem Video. "
                "Beschreibe in maximal 4 Sätzen auf Deutsch, was in dem Video zu sehen ist. "
                "Wenn Text sichtbar ist, transkribiere ihn."
            ),
        }
    ]
    for frame in frames:
        with open(frame, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        mime = mimetypes.guess_type(str(frame))[0] or "image/jpeg"
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        })

    result = client.chat.completions.create(
        model="mistral-medium-latest",
        messages=[
            {"role": "system", "content": "Du bist ein hilfreicher Assistent, der Videoinhalte analysiert."},
            {"role": "user", "content": content},
        ],
        timeout=60,
        max_tokens=5000,
    )

    if not result.choices or result.choices[0].message.content is None:
        return ""
    return result.choices[0].message.content


def transcribe_audio(client: OpenAI, audio_path: Path) -> str:
    """Transcribe audio via Mistral voxtral-mini."""
    with audio_path.open("rb") as f:
        try:
            result = client.audio.transcriptions.create(
                model="voxtral-mini-latest",
                file=f,
                response_format="json",
                prompt="Die folgende Audio Datei ist der Ton eines Videos. Der Kontext ist Yachtmanagement.",
            )
        except OpenAIError as e:
            print(f"  Warning: Audio transcription failed: {e}")
            return ""
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


def _process_record(
    vision_client: OpenAI,
    transcribe_client: OpenAI,
    data: dict,
) -> dict:
    """Process all video attachments of one JSONL record."""
    for attach in data.get("attachments", []):
        att_path = Path(ATTACHMENTS_DIR) / attach.get("path", "")
        ext = att_path.suffix.lower()
        if ext not in SUPPORTED_VIDEO:
            continue
        if not att_path.exists():
            continue

        print(f"  Processing video: {att_path.name}")
        msg_tmp = Path(TMP_DIR) / data["id"]
        msg_tmp.mkdir(parents=True, exist_ok=True)

        # 1. Extract keyframes and describe visually
        frames = extract_keyframes(att_path, msg_tmp)
        if frames:
            video_summary = describe_keyframes(vision_client, frames)
            attach["video_summary"] = video_summary
            print(f"    Visual summary: {video_summary[:80]}{'...' if len(video_summary) > 80 else ''}")

        # 2. Extract audio and transcribe
        audio_file = extract_audio_track(att_path, msg_tmp)
        if audio_file:
            transcript = transcribe_audio(transcribe_client, audio_file)
            if transcript:
                attach["audio_transcription"] = transcript
                print(f"    Audio transcript: {transcript[:80]}{'...' if len(transcript) > 80 else ''}")

    return data


# ── main ──────────────────────────────────────────────────────────────

def main():
    os.makedirs(os.path.dirname(OUTPUT_JSONL), exist_ok=True)
    os.makedirs(TMP_DIR, exist_ok=True)

    vision_client = _init_vision_client()
    transcribe_client = _init_transcribe_client()

    with open(INPUT_JSONL, "r", encoding="utf-8") as inp:
        lines = inp.readlines()
    total = len(lines)

    done_ids = _load_done_ids(OUTPUT_JSONL)
    if done_ids:
        print(f"Resuming – {len(done_ids)} records already processed, skipping them.")

    # Open in append mode so we can resume
    try:
        with open(OUTPUT_JSONL, "a", encoding="utf-8") as out:
            pending: list[dict] = []
            pending_indices: list[int] = []

            for idx, line in enumerate(lines, 1):
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    print(f"  Warning: skipping malformed line {idx}")
                    continue

                if data["id"] in done_ids:
                    print(f"[{idx}/{total}] {data['id']}  (skip – already done)")
                    continue

                pending.append(data)
                pending_indices.append(idx)

                if len(pending) >= BATCH_SIZE or idx == total:
                    # Process the batch of records concurrently
                    with ThreadPoolExecutor(max_workers=min(BATCH_SIZE, len(pending))) as pool:
                        futures = {
                            pool.submit(
                                _process_record, vision_client, transcribe_client, copy.deepcopy(rec),
                            ): (rec, pidx)
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

    finally:
        shutil.rmtree(TMP_DIR, ignore_errors=True)
    print(f"\n✅ Done – wrote {OUTPUT_JSONL}")


if __name__ == "__main__":
    main()
