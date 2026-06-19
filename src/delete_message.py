import argparse
import os
import sqlite3
import chromadb
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DB_PATH = os.path.join(DATA_DIR, "pipeline.db")
CHROMA_DIR = os.path.join(DATA_DIR, "chromadb")

def delete_message(message_id: str):
    # 1. Delete from SQLite
    print(f"Deleting message '{message_id}' from SQLite...")
    conn = sqlite3.connect(DB_PATH)
    try:
        # Get rowid for FTS deletion
        cursor = conn.execute("SELECT rowid, text, subject, sender, receiver FROM messages WHERE id = ?", (message_id,))
        row = cursor.fetchone()
        
        if row:
            rowid, text, subject, sender, receiver = row
            # Delete physical attachments files
            cursor = conn.execute("SELECT path FROM attachments WHERE message_id = ?", (message_id,))
            attachment_paths = [r[0] for r in cursor.fetchall() if r[0]]
            
            for att_path in attachment_paths:
                full_path = os.path.join(DATA_DIR, "1_parser", "attachments", att_path)
                if os.path.exists(full_path):
                    try:
                        os.remove(full_path)
                        print(f"Deleted physical attachment file: {att_path}")
                    except Exception as e:
                        print(f"Failed to delete attachment {att_path}: {e}")

            # Delete from messages and attachments
            conn.execute("DELETE FROM attachments WHERE message_id = ?", (message_id,))
            conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
            
            # Delete from FTS5 index (External Content Table requires special delete syntax)
            conn.execute(
                "INSERT INTO messages_fts(messages_fts, rowid, text, subject, sender, receiver) VALUES('delete', ?, ?, ?, ?, ?)",
                (rowid, text, subject, sender, receiver)
            )
            print("Successfully deleted from SQLite (messages, attachments, FTS).")
        else:
            print("Message ID not found in SQLite.")
            
        conn.commit()
    except Exception as e:
        print(f"SQLite error: {e}")
        conn.rollback()
    finally:
        conn.close()

    # 2. Delete from ChromaDB
    print(f"Deleting message '{message_id}' from ChromaDB...")
    try:
        chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
        collection = chroma_client.get_collection("messages")
        
        # Find chunks belonging to this message_id via metadata
        results = collection.get(where={"message_id": message_id})
        ids_to_delete = results.get("ids", [])
        
        if ids_to_delete:
            collection.delete(ids=ids_to_delete)
            print(f"Successfully deleted {len(ids_to_delete)} chunks from ChromaDB.")
        else:
            print("No chunks found in ChromaDB for this message ID.")
            
    except Exception as e:
        print(f"ChromaDB error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Delete a message from SQLite and ChromaDB by ID.")
    parser.add_argument("message_id", type=str, help="The ID of the message to delete.")
    args = parser.parse_args()
    
    delete_message(args.message_id)
