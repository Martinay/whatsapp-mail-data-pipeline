"""Load enriched JSONL into ChromaDB with Mistral embeddings."""

import json
import os

import chromadb
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
INPUT_JSONL = os.path.join(DATA_DIR, "2_enrich", "d_metadata.jsonl")
CHROMA_DIR = os.path.join(DATA_DIR, "chromadb")
COLLECTION_NAME = "messages"

MAX_BATCH_ITEMS = 20       # Mistral embed API batch limit
MAX_BATCH_CHARS = 40000    # Safe threshold (~10k tokens) to prevent total token limit errors
CHUNK_SIZE = 1500          # Max characters per chunk
CHUNK_OVERLAP = 200        # Overlap to preserve context between chunks


def _init_embed_client() -> OpenAI:
    api_key = os.getenv("MISTRAL_API_KEY_TRANSCRIBE")  # reuse key
    base_url = os.getenv("MISTRAL_BASE_URL")
    if not api_key or not base_url:
        raise ValueError("MISTRAL_API_KEY_TRANSCRIBE and MISTRAL_BASE_URL must be set")
    return OpenAI(api_key=api_key, base_url=base_url)


def build_document(data: dict) -> str:
    """Concatenate all text content for embedding."""
    parts = []

    if data.get("text"):
        parts.append(data["text"])

    for attach in data.get("attachments", []):
        if attach.get("audio_transcription"):
            parts.append(attach["audio_transcription"])
        if attach.get("image_summary"):
            parts.append(attach["image_summary"])
        if attach.get("video_summary"):
            parts.append(attach["video_summary"])
        if attach.get("extracted_text"):
            parts.append(attach["extracted_text"])

    return "\n".join(parts).strip()


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split text into overlapping chunks, respecting paragraphs and words."""
    chunks = []
    i = 0
    while i < len(text):
        chunk = text[i : i + chunk_size]

        # If we're not at the end, try to snap to a logical break
        if i + chunk_size < len(text):
            last_newline = chunk.rfind('\n')
            last_space = chunk.rfind(' ')

            if last_newline > chunk_size // 2:
                chunk = chunk[:last_newline]
                step = last_newline
            elif last_space > chunk_size // 2:
                chunk = chunk[:last_space]
                step = last_space
            else:
                step = chunk_size
        else:
            step = chunk_size

        cleaned_chunk = chunk.strip()
        if cleaned_chunk:
            chunks.append(cleaned_chunk)

        # Always advance by at least 1 to prevent infinite loops
        advance = max(1, step - overlap)
        i += advance

    return chunks


def embed_batch(client: OpenAI, texts: list[str]) -> list[list[float]]:
    """Get embeddings via Mistral API."""
    response = client.embeddings.create(model="mistral-embed", input=texts)
    return [item.embedding for item in response.data]


def main():
    embed_client = _init_embed_client()
    chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)

    # Get or create collection – preserves existing data across re-runs
    collection = chroma_client.get_or_create_collection(
        name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )

    with open(INPUT_JSONL, "r", encoding="utf-8") as f:
        lines = f.readlines()

    total_lines = len(lines)

    # Pre-load existing IDs from ChromaDB to skip already-embedded chunks
    existing_count = collection.count()
    seen_ids: set[str] = set()
    if existing_count > 0:
        print(f"Loading {existing_count} existing IDs from ChromaDB...")
        # ChromaDB returns up to 'limit' results; page through all of them
        offset = 0
        page_size = 5000
        while True:
            result = collection.get(limit=page_size, offset=offset, include=[])
            ids = result["ids"]
            if not ids:
                break
            seen_ids.update(ids)
            offset += len(ids)
            if len(ids) < page_size:
                break
        print(f"Skipping {len(seen_ids)} already-embedded chunk IDs.")

    # Collect batches
    batch_ids = []
    batch_docs = []
    batch_metas = []
    current_batch_chars = 0

    for idx, line in enumerate(lines, 1):
        data = json.loads(line)
        doc = build_document(data)
        
        if not doc:
            continue

        # Split the document into manageable chunks
        chunks = chunk_text(doc, CHUNK_SIZE, CHUNK_OVERLAP)
        total_chunks = len(chunks)

        for chunk_idx, chunk in enumerate(chunks):
            # Create a unique ID for each chunk
            chunk_id = f"{data['id']}_chunk_{chunk_idx}"

            # Skip duplicate IDs (e.g. duplicate records in the input JSONL)
            if chunk_id in seen_ids:
                continue
            seen_ids.add(chunk_id)
            
            # Neue und erweiterte Metadaten (None wird in "" umgewandelt wegen ChromaDB-Einschränkungen)
            meta = {
                "message_id": data.get("id", ""),
                "channel": data.get("channel") or "",
                "timestamp": data.get("timestamp") or "",
                "sender": data.get("sender") or "",
                "receiver": data.get("receiver") or "",
                "cc": data.get("cc") or "",
                "bcc": data.get("bcc") or "",
                "subject": data.get("subject") or "",
                "conversation_id": data.get("conversation_id") or "",
                "chat_name": data.get("chat_name") or "",
                "chunk_index": chunk_idx,
                "total_chunks": total_chunks,
                "is_chunked": total_chunks > 1
            }

            batch_ids.append(chunk_id)
            batch_docs.append(chunk)
            batch_metas.append(meta)
            current_batch_chars += len(chunk)

            # Check if we hit either the item limit OR the character/token limit
            if len(batch_ids) >= MAX_BATCH_ITEMS or current_batch_chars >= MAX_BATCH_CHARS:
                embeddings = embed_batch(embed_client, batch_docs)
                collection.upsert(
                    ids=batch_ids,
                    embeddings=embeddings,
                    documents=batch_docs,
                    metadatas=batch_metas,
                )
                print(f"[Line {idx}/{total_lines}] Embedded batch of {len(batch_ids)} chunks.")
                
                # Reset batch state
                batch_ids, batch_docs, batch_metas = [], [], []
                current_batch_chars = 0

    # Flush remaining chunks
    if batch_ids:
        embeddings = embed_batch(embed_client, batch_docs)
        collection.upsert(
            ids=batch_ids,
            embeddings=embeddings,
            documents=batch_docs,
            metadatas=batch_metas,
        )
        print(f"Embedded final batch of {len(batch_ids)} chunks.")

    print(f"Done. ChromaDB: {CHROMA_DIR} ({collection.count()} chunks)")


if __name__ == "__main__":
    main()