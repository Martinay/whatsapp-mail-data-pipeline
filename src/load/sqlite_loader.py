"""Load enriched JSONL into SQLite database with FTS5 full-text index."""

import json
import os
import sqlite3

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
INPUT_JSONL = os.path.join(DATA_DIR, "2_enrich", "d_metadata.jsonl")
DB_PATH = os.path.join(DATA_DIR, "pipeline.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    timestamp DATETIME NOT NULL,
    sender TEXT,
    receiver TEXT,
    cc TEXT,
    bcc TEXT,
    subject TEXT,
    text TEXT,
    word_count INTEGER,
    size_bytes INTEGER,
    conversation_id TEXT,
    chat_name TEXT
);

CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER,
    message_id TEXT REFERENCES messages(id),
    path TEXT,
    extension TEXT,
    mimetype TEXT,
    original_filename TEXT,
    size_bytes INTEGER,
    audio_transcription TEXT,
    image_summary TEXT,
    video_summary TEXT,
    extracted_text TEXT,
    PRIMARY KEY (id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_msg_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_msg_sender ON messages(sender);
CREATE INDEX IF NOT EXISTS idx_msg_channel ON messages(channel);
CREATE INDEX IF NOT EXISTS idx_msg_receiver ON messages(receiver);
CREATE INDEX IF NOT EXISTS idx_msg_conversation ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_msg_chat_name ON messages(chat_name);

-- FTS5 full-text search index
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    text, subject, sender, receiver,
    content='messages', content_rowid='rowid'
);
"""

FTS_POPULATE = """
INSERT INTO messages_fts(messages_fts) VALUES('delete-all');
INSERT INTO messages_fts(rowid, text, subject, sender, receiver)
    SELECT rowid, COALESCE(text, ''), COALESCE(subject, ''),
           COALESCE(sender, ''), COALESCE(receiver, '')
    FROM messages;
"""


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)

    with open(INPUT_JSONL, "r", encoding="utf-8") as f:
        lines = f.readlines()

    total = len(lines)

    for idx, line in enumerate(lines, 1):
        data = json.loads(line)

        conn.execute(
            """INSERT OR REPLACE INTO messages
               (id, channel, timestamp, sender, receiver, cc, bcc,
                subject, text, word_count, size_bytes, conversation_id, chat_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["id"],
                data["channel"],
                data["timestamp"],
                data.get("sender"),
                data.get("receiver"),
                data.get("cc"),
                data.get("bcc"),
                data.get("subject"),
                data.get("text"),
                data.get("word_count"),
                data.get("size_bytes"),
                data.get("conversation_id"),
                data.get("chat_name"),
            ),
        )

        for attach in data.get("attachments", []):
            conn.execute(
                """INSERT OR REPLACE INTO attachments
                   (id, message_id, path, extension, mimetype, original_filename,
                    size_bytes, audio_transcription, image_summary, video_summary, extracted_text)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    attach["id"],
                    data["id"],
                    attach.get("path"),
                    attach.get("extension"),
                    attach.get("mimetype"),
                    attach.get("original_filename"),
                    attach.get("size_bytes"),
                    attach.get("audio_transcription"),
                    attach.get("image_summary"),
                    attach.get("video_summary"),
                    attach.get("extracted_text"),
                ),
            )

        if idx % 100 == 0 or idx == total:
            conn.commit()
            print(f"[{idx}/{total}]")

    conn.commit()

    # Populate FTS5 index
    print("Building FTS5 index...")
    conn.executescript(FTS_POPULATE)
    conn.commit()

    conn.close()
    print(f"Done. Database: {DB_PATH}")


if __name__ == "__main__":
    main()
