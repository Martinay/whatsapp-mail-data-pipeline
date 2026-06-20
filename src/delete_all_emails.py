"""Delete all email messages from the pipeline.

Queries the SQLite database for all messages with channel='Mail',
then calls src/delete_message.py with the message IDs batched in groups of 100.
"""

import os
import sqlite3
import subprocess
import sys

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DB_PATH = os.path.join(DATA_DIR, "pipeline.db")
SCRIPT_DIR = os.path.dirname(__file__)
DELETE_SCRIPT = os.path.join(SCRIPT_DIR, "delete_message.py")


def get_email_message_ids() -> list[str]:
    """Get all message IDs where channel is 'Mail'."""
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.execute("SELECT id FROM messages WHERE channel = 'Mail'")
        return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()


def batch_delete(message_ids: list[str], batch_size: int = 100):
    """Call delete_message.py with message IDs in batches."""
    total = len(message_ids)
    num_batches = (total + batch_size - 1) // batch_size

    for i in range(0, total, batch_size):
        batch = message_ids[i : i + batch_size]
        batch_num = (i // batch_size) + 1
        print(f"\n{'='*60}")
        print(f"Batch {batch_num}/{num_batches} ({len(batch)} messages)")
        print(f"{'='*60}")

        result = subprocess.run(
            [sys.executable, DELETE_SCRIPT] + batch,
            capture_output=False,
        )

        if result.returncode != 0:
            print(f"ERROR: Batch {batch_num} failed with return code {result.returncode}")
            sys.exit(1)


if __name__ == "__main__":
    message_ids = get_email_message_ids()

    if not message_ids:
        print("No email messages found in the database.")
        sys.exit(0)

    print(f"Found {len(message_ids)} email messages to delete.")
    print(f"Will process in {(len(message_ids) + 99) // 100} batch(es) of up to 100.")

    response = input("\nProceed with deletion? (yes/no): ").strip().lower()
    if response != "yes":
        print("Aborted.")
        sys.exit(0)

    batch_delete(message_ids)
    print(f"\nDone. Deleted {len(message_ids)} email messages.")
