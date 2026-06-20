import argparse
import os
import sqlite3
import chromadb
import json
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DB_PATH = os.path.join(DATA_DIR, "pipeline.db")
CHROMA_DIR = os.path.join(DATA_DIR, "chromadb")

def delete_from_jsonl(file_path: str, message_ids_set: set):
    if not os.path.exists(file_path):
        return
    
    deleted_count = 0
    temp_path = file_path + ".tmp"
    try:
        with open(file_path, 'r', encoding='utf-8') as f_in, open(temp_path, 'w', encoding='utf-8') as f_out:
            for line in f_in:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    if data.get("id") in message_ids_set:
                        deleted_count += 1
                        continue
                except json.JSONDecodeError:
                    pass
                f_out.write(line)
        
        if deleted_count > 0:
            os.replace(temp_path, file_path)
            print(f"Deleted {deleted_count} record(s) from {os.path.basename(file_path)}.")
        else:
            os.remove(temp_path)
    except Exception as e:
        print(f"Error processing {file_path}: {e}")
        if os.path.exists(temp_path):
            os.remove(temp_path)

def delete_messages(message_ids: list):
    message_ids_set = set(message_ids)
    
    # 1. Delete from SQLite
    print(f"Deleting {len(message_ids)} messages from SQLite...")
    conn = sqlite3.connect(DB_PATH)
    try:
        for msg_id in message_ids:
            # Get rowid for FTS deletion
            cursor = conn.execute("SELECT rowid, text, subject, sender, receiver FROM messages WHERE id = ?", (msg_id,))
            row = cursor.fetchone()
            
            if row:
                rowid, text, subject, sender, receiver = row
                # Delete physical attachments files
                cursor = conn.execute("SELECT path FROM attachments WHERE message_id = ?", (msg_id,))
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
                conn.execute("DELETE FROM attachments WHERE message_id = ?", (msg_id,))
                conn.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
                
                # Delete from FTS5 index (External Content Table requires special delete syntax)
                conn.execute(
                    "INSERT INTO messages_fts(messages_fts, rowid, text, subject, sender, receiver) VALUES('delete', ?, ?, ?, ?, ?)",
                    (rowid, text or '', subject or '', sender or '', receiver or '')
                )
        conn.commit()
        print("Successfully deleted from SQLite (messages, attachments, FTS).")
    except Exception as e:
        print(f"SQLite error: {e}")
        conn.rollback()
    finally:
        conn.close()

    # 2. Delete from ChromaDB
    print(f"Deleting {len(message_ids)} messages from ChromaDB...")
    try:
        chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
        collection = chroma_client.get_collection("messages")
        
        all_ids_to_delete = []
        for msg_id in message_ids:
            # Find chunks belonging to this message_id via metadata
            results = collection.get(where={"message_id": msg_id})
            ids_to_delete = results.get("ids", [])
            all_ids_to_delete.extend(ids_to_delete)
            
        if all_ids_to_delete:
            collection.delete(ids=all_ids_to_delete)
            print(f"Successfully deleted {len(all_ids_to_delete)} chunks from ChromaDB.")
        else:
            print("No chunks found in ChromaDB for these message IDs.")
            
    except Exception as e:
        print(f"ChromaDB error: {e}")

    # 3. Delete from intermediate JSONL files
    print(f"\nDeleting {len(message_ids)} messages from intermediate JSONL files...")
    jsonl_files = [
        os.path.join(DATA_DIR, "1_parser", "messages.jsonl")
    ]
    
    enrich_dir = os.path.join(DATA_DIR, "2_enrich")
    if os.path.exists(enrich_dir):
        for f in os.listdir(enrich_dir):
            if f.endswith(".jsonl"):
                jsonl_files.append(os.path.join(enrich_dir, f))
                
    for f_path in jsonl_files:
        delete_from_jsonl(f_path, message_ids_set)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Delete messages from SQLite, ChromaDB, and JSONL by ID.")
    parser.add_argument("message_ids", type=str, nargs='+', help="The ID(s) of the message(s) to delete (up to 100).")
    args = parser.parse_args()
    
    message_ids = args.message_ids
    if len(message_ids) > 100:
        print("Warning: More than 100 message IDs provided. Proceeding with the first 100.")
        message_ids = message_ids[:100]
        
    delete_messages(message_ids)
