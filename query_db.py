import sqlite3
import os
from openai import OpenAI
from dotenv import load_dotenv

# ------------------- SETUP -------------------
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_APIKEY"))

DB_FILE = "ott_subscriptions.db"

# ------------------- SCHEMA CONTEXT FOR LLM -------------------

DB_SCHEMA = """
You have access to a SQLite database with OTT subscription transactions.

TABLES:

1. services
   - id       INTEGER  (primary key)
   - name     TEXT     (e.g. 'Netflix', 'Spotify', 'Amazon Prime', 'Disney+ Hotstar')

2. transactions
   - id          INTEGER  (primary key)
   - service_id  INTEGER  (foreign key → services.id)
   - amount      INTEGER  (in Indian Rupees ₹)
   - date        TEXT     (ISO format: YYYY-MM-DD)
   - frequency   TEXT     ('monthly' or 'yearly')
   - created_at  TEXT     (when the record was inserted)

To get the service name, JOIN transactions with services on service_id = services.id.
"""

# ------------------- LLM: NL → SQL -------------------

def generate_sql(user_query):
    prompt = f"""You are an expert SQL assistant.

{DB_SCHEMA}

Convert the user's question into a single valid SQLite SELECT query.

Rules:
- Return ONLY the raw SQL query, no explanation, no markdown, no backticks.
- Always join with services table when service name is needed.
- Use strftime for any date/month/year grouping.
- For "latest" or "recent", use ORDER BY date DESC LIMIT 1.
- Never use DROP, DELETE, INSERT, UPDATE or any write operations.

User question: {user_query}

SQL:"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=300
    )
    return response.choices[0].message.content.strip()


# ------------------- LLM: ROWS → SENTENCE -------------------

def generate_answer(user_query, sql, rows, columns):
    if not rows:
        data_str = "No results found."
    else:
        header = " | ".join(columns)
        row_lines = "\n".join(" | ".join(str(v) for v in row) for row in rows)
        data_str = f"{header}\n{row_lines}"

    prompt = f"""The user asked: "{user_query}"

The following SQL was run:
{sql}

Results:
{data_str}

Write a clear, natural language answer to the user's question based on these results.
Be concise. Use ₹ for amounts. Mention specific numbers from the results.
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=200
    )
    return response.choices[0].message.content.strip()


# ------------------- QUERY RUNNER -------------------

def run_query(sql):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        cur = conn.execute(sql)
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description] if cur.description else []
        return rows, columns, None
    except sqlite3.Error as e:
        return [], [], str(e)
    finally:
        conn.close()


# ------------------- MAIN LOOP -------------------

def main():
    print("=" * 55)
    print("  OTT Subscription Query Assistant")
    print("  Ask anything about your subscriptions.")
    print("  Type 'exit' to quit.")
    print("=" * 55)

    while True:
        print()
        user_query = input("You: ").strip()

        if not user_query:
            continue
        if user_query.lower() in ("exit", "quit", "q"):
            print("Bye!")
            break

        # Step 1: Generate SQL
        print("\n⚙  Generating SQL...")
        sql = generate_sql(user_query)
        print(f"   → {sql}")

        # Step 2: Run SQL
        rows, columns, error = run_query(sql)

        if error:
            print(f"\n✗ SQL Error: {error}")
            print("  Try rephrasing your question.")
            continue

        # Step 3: Print raw rows
        if rows:
            print(f"\n📊 Raw Results ({len(rows)} row{'s' if len(rows) != 1 else ''}):")
            header = " | ".join(columns)
            print("   " + header)
            print("   " + "-" * len(header))
            for row in rows:
                print("   " + " | ".join(str(v) for v in row))
        else:
            print("\n📊 No results found.")

        # Step 4: Natural language answer
        print("\n💬 Answer:")
        answer = generate_answer(user_query, sql, rows, columns)
        print(f"   {answer}")


if __name__ == "__main__":
    main()