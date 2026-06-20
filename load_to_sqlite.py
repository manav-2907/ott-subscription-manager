import sys
import json
import sqlite3
from datetime import datetime

# Windows consoles default to cp1252, which can't encode ₹ — force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

INPUT_FILE = "ott_results.json"
DB_FILE = "ott_subscriptions.db"


def create_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS services (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            name    TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            service_id  INTEGER NOT NULL REFERENCES services(id),
            amount      INTEGER NOT NULL,
            date        TEXT NOT NULL,        -- ISO format: YYYY-MM-DD
            frequency   TEXT                  -- 'monthly' | 'yearly' | NULL
                        CHECK(frequency IN ('monthly', 'yearly', NULL)),
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(service_id, amount, date)  -- prevent duplicate inserts
        );
    """)
    conn.commit()


def get_or_create_service(conn, name):
    row = conn.execute("SELECT id FROM services WHERE name = ?", (name,)).fetchone()
    if row:
        return row[0]
    cur = conn.execute("INSERT INTO services (name) VALUES (?)", (name,))
    conn.commit()
    return cur.lastrowid


def load_transactions(conn, transactions):
    inserted = 0
    skipped = 0

    for txn in transactions:
        service_id = get_or_create_service(conn, txn["service"])
        try:
            conn.execute(
                """
                INSERT INTO transactions (service_id, amount, date, frequency)
                VALUES (?, ?, ?, ?)
                """,
                (service_id, txn["amount"], txn["date"], txn.get("frequency"))
            )
            inserted += 1
        except sqlite3.IntegrityError:
            # Duplicate — already exists
            skipped += 1

    conn.commit()
    return inserted, skipped


def print_summary(conn):
    print("\n" + "=" * 50)
    print("SERVICES")
    print("=" * 50)
    for row in conn.execute("SELECT id, name FROM services ORDER BY name"):
        print(f"  [{row[0]}] {row[1]}")

    print("\n" + "=" * 50)
    print("TRANSACTIONS")
    print("=" * 50)
    rows = conn.execute("""
        SELECT s.name, t.amount, t.date, t.frequency
        FROM transactions t
        JOIN services s ON s.id = t.service_id
        ORDER BY t.date DESC
    """).fetchall()

    for r in rows:
        print(f"  {r[2]}  {r[0]:<20} ₹{r[1]:<6} {r[3] or 'unknown'}")

    total = conn.execute("SELECT SUM(amount) FROM transactions").fetchone()[0]
    print(f"\n  Total across all transactions: ₹{total}")


def main():
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        transactions = json.load(f)

    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")

    create_schema(conn)
    inserted, skipped = load_transactions(conn, transactions)

    print(f"✓ Inserted: {inserted}  |  ⟳ Skipped (duplicates): {skipped}")
    print_summary(conn)

    conn.close()
    print(f"\nDatabase saved to: {DB_FILE}")


if __name__ == "__main__":
    main()
