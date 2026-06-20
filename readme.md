# Data Processing Pipeline

Pipeline that converts emails (mbox) and WhatsApp chat backups into a structured **SQLite + ChromaDB** store, queryable via an **LLM agent** with function-calling.

## Architecture

```
mbox + WhatsApp ZIP → Parser (JSONL) → Enrichment → SQLite (+ FTS5) + ChromaDB → Agent
```

**4 Stages:**

| Stage | Scripts | Function |
|-------|---------|----------|
| `src/parser/` | `mbox_parser.py`, `eml_parser.py`, `whatsapp_parser.py`, `document_parser.py` | Raw data → JSONL (`messages.jsonl`) + attachments |
| `src/enrich/` | `extract_zips.py`, `transcribe_audio.py`, `describe_videos.py`, `describe_pictures.py`, `extract_documents.py`, `enrich_metadata.py` | ZIP→Attachments, Audio→Text, Video→Summary+Transcript, Images→Summary, Documents→Text (PDF/Excel/Word/PPTX/HTML/TXT/DOC/ICS/VCF), Metadata |
| `src/load/` | `sqlite_loader.py`, `chromadb_loader.py` | JSONL → SQLite (structure + FTS5) + ChromaDB (semantics) |
| `src/agent/` | `agent.py`, `tools.py`, `prompts.py` | Interactive LLM agent with SQL, semantic search, fulltext search, and Excel export |

**Models:**
- Mistral voxtral-mini-latest (audio transcription)
- Mistral mistral-medium-latest (image/video analysis + agent)
- Mistral mistral-embed (embeddings)

## Setup

1. **Install Dependencies**
   ```bash
   uv sync
   ```

2. **Environment Variables**
   ```bash
   cp .env.example .env
   ```
   Fill in `MISTRAL_API_KEY_TRANSCRIBE`, `MISTRAL_API_KEY_VISION`, `MISTRAL_BASE_URL`.

3. **Place Source Data**
   - `data/source/mail/` — mbox files
   - `data/source/whatsapp/` — WhatsApp chat export ZIPs
   - `data/source/documents/` — standalone documents (PDF, DOCX, Numbers, etc.)

4. **System Requirements**
   - `ffmpeg` must be on PATH (for audio/video processing)

## Run Pipeline

```bash
# 1. Parse (outputs data/1_parser/messages.jsonl)
uv run python src/parser/mbox_parser.py
uv run python src/parser/eml_parser.py
uv run python src/parser/whatsapp_parser.py
uv run python src/parser/document_parser.py

# 2. Enrich (JSONL pipeline: 0_unzipped → a_audio → a2_videos → b_pictures → c_documents → d_metadata)
uv run python src/enrich/extract_zips.py
uv run python src/enrich/transcribe_audio.py
uv run python src/enrich/describe_videos.py
uv run python src/enrich/describe_pictures.py
uv run python src/enrich/extract_documents.py
uv run python src/enrich/enrich_metadata.py

# 3. Load into databases
uv run python src/load/sqlite_loader.py
uv run python src/load/chromadb_loader.py

# 4. Start agent
uv run python src/agent/agent.py
```

## Agent Capabilities

- **SQL queries**: Count messages, filter by date/sender/channel, aggregate statistics
- **Semantic search**: Find messages by topic/content similarity
- **Fulltext search**: Find messages by exact keywords, names, phrases (FTS5)
- **Excel export**: Generate downloadable `.xlsx` files for court-ready lists

## Data Schema

Each message is a flat row. WhatsApp conversations are grouped via `chat_name` / `conversation_id`.

**messages**: `id, channel, timestamp, sender, receiver, cc, bcc, subject, text, word_count, size_bytes, conversation_id, chat_name`

**attachments**: `id, message_id, path, extension, mimetype, original_filename, size_bytes, audio_transcription, image_summary, video_summary, extracted_text`

## Data Flow

```
Parser Stage:    mbox/ZIP → data/1_parser/messages.jsonl + attachments/
Enrich Stage:    messages.jsonl → 0_unzipped.jsonl → a_audio.jsonl → a2_videos.jsonl → b_pictures.jsonl → c_documents.jsonl → d_metadata.jsonl
Load Stage:      d_metadata.jsonl → pipeline.db (SQLite + FTS5) + chromadb/
```
