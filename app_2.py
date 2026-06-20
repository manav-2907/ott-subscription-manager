import streamlit as st
import os
import json
import sqlite3
from datetime import datetime, timedelta
import base64
from bs4 import BeautifulSoup
import re
from collections import defaultdict

# Import functions from existing files
from gmail_auth import get_gmail_service, is_authenticated, logout

# Import from load_to_sqlite.py
from load_to_sqlite import (
    create_schema,
    get_or_create_service,
    load_transactions
)

# Import from query_db.py (only if OpenAI key exists)
try:
    from query_db import generate_sql, run_query, generate_answer
    HAS_OPENAI = True
except:
    HAS_OPENAI = False

# ------------------- IMPROVED EXTRACTION FUNCTIONS -------------------
MIN_AMOUNT = 50  # Filter out transactions below ₹50

def extract_amount(text):
    """Extract amount from text"""
    if not text:
        return None
    match = re.search(r'(?:₹|INR|Rs\.?)\s?(\d+(?:,\d+)?(?:\.\d+)?)', text, re.IGNORECASE)
    if match:
        return float(match.group(1).replace(',', ''))
    return None

def detect_service(subject, body):
    """Detect OTT service name"""
    text = (subject + " " + body).lower()
    
    if "netflix" in text:
        return "Netflix"
    if "spotify" in text:
        return "Spotify"
    if "amazon prime" in text or "prime" in text:
        return "Amazon Prime"
    if "hotstar" in text or "disney" in text:
        return "Disney+ Hotstar"
    # Bank alerts (e.g. ICICI standing instruction) say "...towards youtube"
    # or render the merchant as "youtubepremium", never "youtube premium".
    if "youtube" in text or "youtubepremium" in text:
        return "YouTube Premium"

    return None

def parse_date(date_str):
    """Parse date to YYYY-MM-DD format"""
    if not date_str:
        return None
    try:
        date_clean = re.sub(r'\([A-Z]{3}\)', '', date_str).strip()
        
        formats = [
            "%a, %d %b %Y %H:%M:%S %z",
            "%d %b %Y %H:%M:%S %z",
            "%a, %d %b %Y %H:%M:%S",
            "%d %b %Y %H:%M:%S",
            "%Y-%m-%d",
            "%d-%m-%Y",
            "%Y-%m-%dT%H:%M:%S",
            "%d/%m/%Y"
        ]
        
        for fmt in formats:
            try:
                dt = datetime.strptime(date_clean, fmt)
                return dt.strftime("%Y-%m-%d")
            except:
                continue
        
        return None
    except:
        return None

def calculate_frequency(service_transactions, service_name):
    """
    Calculate billing frequency from transaction dates and amounts.
    Improved logic to detect yearly subscriptions better.
    """
    if len(service_transactions) < 2:
        if service_transactions:
            amount = service_transactions[0]["amount_charged"]
            # Amazon Prime yearly is ₹399 (or ₹1499 for old plans)
            # Monthly would be ₹179 or ₹299
            if service_name == "Amazon Prime" and amount >= 350:
                return "yearly"
        return "monthly"
    
    dates = []
    for txn in service_transactions:
        try:
            dt = datetime.strptime(txn["transaction_date"], "%Y-%m-%d")
            dates.append(dt)
        except:
            continue
    
    if len(dates) < 2:
        if service_transactions:
            amount = service_transactions[0]["amount_charged"]
            if service_name == "Amazon Prime" and amount >= 350:
                return "yearly"
        return "monthly"
    
    dates.sort()
    gaps = [(dates[i] - dates[i-1]).days for i in range(1, len(dates))]
    avg_gap = sum(gaps) / len(gaps)
    sorted_gaps = sorted(gaps)
    median_gap = sorted_gaps[len(sorted_gaps) // 2]
    
    if service_name == "Amazon Prime":
        amounts = [txn["amount_charged"] for txn in service_transactions]
        avg_amount = sum(amounts) / len(amounts)
        
        # Amazon Prime yearly: ₹399 or ₹1499
        # Amazon Prime monthly: ₹179 or ₹299
        # Threshold: ₹350
        if avg_amount >= 350:
            return "yearly"
        elif avg_amount < 350 and median_gap < 100:
            return "monthly"
    
    if avg_gap >= 300 or median_gap >= 300:
        return "yearly"
    else:
        return "monthly"

# Import from load_to_sqlite.py
from load_to_sqlite import (
    create_schema,
    get_or_create_service,
    load_transactions
)

# Import from query_db.py (only if OpenAI key exists)
try:
    from query_db import generate_sql, run_query, generate_answer
except:
    pass

# ------------------- FUNCTIONS FROM test_3.py -------------------
# Copied here to avoid module-level service initialization

def extract_body(payload):
    parts = payload.get('parts')
    if parts:
        for part in parts:
            mime_type = part.get('mimeType')
            data = part.get('body', {}).get('data')
            if not data:
                continue
            text = base64.urlsafe_b64decode(data).decode('utf-8')
            if mime_type == 'text/plain':
                return text
            if mime_type == 'text/html':
                return text
    body = payload.get('body', {}).get('data')
    if body:
        return base64.urlsafe_b64decode(body).decode('utf-8')
    return ""

def clean_html(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    return soup.get_text()

def is_ott_transaction_email(body):
    ott_keywords = [
        "netflix", "nflx", "amazon prime", "prime video",
        "hotstar", "disney+", "disney plus", "sonyliv",
        "zee5", "jiocinema", "youtube premium", "youtube",
        "spotify premium", "spotify", "apple tv+",
        "hbo max", "paramount+"
    ]
    transaction_keywords = [
        "transaction", "payment", "charged", "billed",
        "invoice", "receipt", "paid", "subscription","processed"
    ]
    body_lower = body.lower()
    has_ott = any(re.search(r'\b' + re.escape(k) + r'\b', body_lower) for k in ott_keywords)
    has_transaction = any(re.search(r'\b' + re.escape(k) + r'\b', body_lower) for k in transaction_keywords)
    return has_ott and has_transaction

def is_actual_transaction(subject, body):
    text = (subject + " " + body).lower()

    # --- HARD EXCLUDE (check body carefully, not just subject) ---
    exclude_keywords = [
        "upcoming payment", "payment due",
        "declined", "failed", "revise", "retry",
        "last chance", "recommendation", "curated",
        "tailored", "referral",
        "time is running out", "action needed",
        "account on hold", "2 days left",
        "membership has been cancelled",
        "payment issue", "update payment",
        "payment was unsuccessful", "your picks"
    ]
    if any(kw in text for kw in exclude_keywords):
        return False

    # --- STRONG CONFIRMATION (must match one of these) ---
    confirmation_keywords = [
        r"transaction success",
        r"transaction alert",
        r"payment successful",
        r"successfully processed",
        r"has been used for a transaction",
        r"debited",
        r"auto renew.*set up successfully",
        r"moto/si on card",
        r"standing instruction.*success",
        r"welcome back to netflix",
        r"membership.*renewed",
        r"your.*prime.*membership.*set up",
    ]
    if any(re.search(kw, text) for kw in confirmation_keywords):
        return True

    return False

def fetch_all_messages(service, query):
    """Handles pagination to get ALL matching emails"""
    all_messages = []
    page_token = None

    while True:
        if page_token:
            results = service.users().messages().list(
                userId='me',
                q=query,
                maxResults=500,
                pageToken=page_token  
            ).execute()
        else:
            results = service.users().messages().list(
                userId='me',
                q=query,
                maxResults=500
            ).execute()

        messages = results.get('messages', [])
        all_messages.extend(messages)

        page_token = results.get('nextPageToken')
        if not page_token:
            break

    return all_messages

# Constants
DB_FILE = "ott_subscriptions.db"
OTT_TRANSACTIONS_FILE = "ott_transactions.json"
OTT_RESULTS_FILE = "ott_results.json"

# ------------------- PAGE CONFIG & STYLING -------------------
st.set_page_config(
    page_title="OTT Subscription Manager",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
        /* Tighten top padding */
        .block-container { padding-top: 2.5rem; padding-bottom: 3rem; }

        /* Gradient hero header */
        .hero {
            background: linear-gradient(135deg, #6d28d9 0%, #db2777 100%);
            padding: 1.4rem 1.8rem;
            border-radius: 16px;
            color: white;
            margin-bottom: 1.6rem;
            box-shadow: 0 8px 24px rgba(109,40,217,0.25);
        }
        .hero h1 { color: white; font-size: 1.9rem; margin: 0; font-weight: 700; }
        .hero p  { color: rgba(255,255,255,0.85); margin: 0.3rem 0 0 0; font-size: 0.95rem; }

        /* Metric cards */
        div[data-testid="stMetric"] {
            background: var(--secondary-background-color);
            border: 1px solid rgba(128,128,128,0.18);
            border-radius: 14px;
            padding: 1rem 1.2rem;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        }
        div[data-testid="stMetricValue"] { font-size: 1.7rem; font-weight: 700; }

        /* Tab bar */
        button[data-baseweb="tab"] { font-size: 1rem; font-weight: 600; }
        div[data-testid="stTabs"] button[aria-selected="true"] { color: #db2777; }

        /* Buttons */
        .stButton button {
            border-radius: 10px;
            font-weight: 600;
            transition: transform 0.05s ease;
        }
        .stButton button:hover { transform: translateY(-1px); }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="hero">
        <h1>🎬 OTT Subscription Manager</h1>
        <p>Track, analyze and query your streaming subscriptions — straight from your inbox.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# ------------------- SIDEBAR -------------------
with st.sidebar:
    st.markdown("### 🔐 Account")

    if is_authenticated():
        st.success("Connected to Gmail")
        if st.button("Log out", use_container_width=True):
            logout()
            st.rerun()
    else:
        st.warning("Not connected")
        if st.button("🔗 Connect Gmail", use_container_width=True, type="primary"):
            try:
                get_gmail_service()
                st.success("Authentication successful!")
                st.rerun()
            except Exception as e:
                st.error(f"Authentication failed: {e}")

    st.divider()

    # Pipeline status indicators
    st.markdown("### 📦 Pipeline Status")
    steps = [
        ("Emails fetched", OTT_TRANSACTIONS_FILE),
        ("Data extracted", OTT_RESULTS_FILE),
        ("Loaded to DB", DB_FILE),
    ]
    for label, path in steps:
        icon = "✅" if os.path.exists(path) else "⬜"
        st.markdown(f"{icon} {label}")

    st.divider()
    st.caption("OpenAI query: " + ("enabled ✅" if HAS_OPENAI else "disabled ⬜"))

# Main tabs
tab0, tab1, tab2, tab3, tab4 = st.tabs(
    ["📊 Dashboard", "📥 Fetch Emails", "🔍 Extract Data", "🗄️ Load to DB", "💬 Query DB"]
)

# Tab 0: Dashboard
with tab0:
    st.header("Subscription Dashboard")

    if not os.path.exists(DB_FILE):
        st.warning(f"{DB_FILE} not found. Run the pipeline first (Fetch → Extract → Load).")
    else:
        import pandas as pd

        conn = sqlite3.connect(DB_FILE)
        df = pd.read_sql_query(
            """
            SELECT s.name AS service, t.amount, t.date, t.frequency
            FROM transactions t
            JOIN services s ON s.id = t.service_id
            ORDER BY t.date DESC
            """,
            conn,
        )
        conn.close()

        if df.empty:
            st.info("No transactions in the database yet.")
        else:
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"])
            df["month"] = df["date"].dt.strftime("%Y-%m")

            # --- Summary metrics ---
            total_spend = df["amount"].sum()
            num_services = df["service"].nunique()
            num_txns = len(df)
            months_span = max(df["month"].nunique(), 1)
            avg_per_month = total_spend / months_span

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total spent", f"₹{total_spend:,.0f}")
            c2.metric("Services", num_services)
            c3.metric("Transactions", num_txns)
            c4.metric("Avg / month", f"₹{avg_per_month:,.0f}")

            # --- Spend per service ---
            st.subheader("Spend by Service")
            by_service = (
                df.groupby("service")["amount"].sum().sort_values(ascending=False)
            )
            st.bar_chart(by_service)

            # --- Monthly trend ---
            st.subheader("Monthly Spending Trend")
            by_month = df.groupby("month")["amount"].sum().sort_index()
            st.line_chart(by_month)

            # --- Active subscriptions + next renewal estimate ---
            st.subheader("Subscriptions & Estimated Next Renewal")
            latest = df.sort_values("date").groupby("service").tail(1).copy()
            renewal_rows = []
            for _, row in latest.iterrows():
                freq = (row["frequency"] or "monthly").lower()
                period_days = 365 if freq == "yearly" else 30
                next_renewal = row["date"] + timedelta(days=period_days)
                renewal_rows.append({
                    "Service": row["service"],
                    "Last charged": row["date"].strftime("%Y-%m-%d"),
                    "Amount": f"₹{row['amount']:,.0f}",
                    "Frequency": freq,
                    "Est. next renewal": next_renewal.strftime("%Y-%m-%d"),
                })
            renewal_df = pd.DataFrame(renewal_rows).sort_values("Est. next renewal")
            st.dataframe(renewal_df, use_container_width=True, hide_index=True)

            # Estimated annualized cost
            annual = 0.0
            for _, row in latest.iterrows():
                freq = (row["frequency"] or "monthly").lower()
                annual += row["amount"] * (1 if freq == "yearly" else 12)
            st.info(f"Estimated annual cost at current plans: ₹{annual:,.0f}")

# Tab 1: Fetch Emails from Gmail
with tab1:
    st.header("Fetch OTT Transaction Emails")
    
    if not is_authenticated():
        st.warning("Please authenticate first using the sidebar")
    else:
        days = st.number_input("Fetch emails from last N days:", min_value=1, max_value=730, value=366)
        
        if st.button("Fetch Emails"):
            with st.spinner("Fetching emails..."):
                try:
                    service = get_gmail_service()
                    cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y/%m/%d')
                    
                    query = f"""after:{cutoff_date} 
                        (netflix OR prime OR hotstar OR disney OR sonyliv OR zee5 OR jiocinema OR youtube OR spotify) 
                        (transaction OR payment OR charged OR billed OR invoice OR receipt OR subscription)"""
                    
                    st.info(f"Searching emails after {cutoff_date}...")
                    messages = fetch_all_messages(service, query)
                    st.info(f"Found {len(messages)} potential OTT transaction emails")
                    
                    ott_emails = []
                    progress_bar = st.progress(0)
                    
                    for idx, msg in enumerate(messages):
                        msg_id = msg['id']
                        message = service.users().messages().get(userId='me', id=msg_id).execute()
                        payload = message['payload']
                        headers = payload.get("headers", [])
                        
                        subject = ""
                        date = ""
                        for header in headers:
                            if header['name'] == 'Subject':
                                subject = header['value']
                            if header['name'] == 'Date':
                                date = header['value']
                        
                        body = extract_body(payload)
                        if "<html" in body:
                            body = clean_html(body)
                        
                        if is_ott_transaction_email(body):
                            if is_actual_transaction(subject, body):
                                ott_emails.append({"subject": subject, "date": date, "body": body})
                        
                        progress_bar.progress((idx + 1) / len(messages))
                    
                    # Remove duplicates
                    seen = set()
                    unique_emails = []
                    for email in ott_emails:
                        key = (email['subject'], email['date'])
                        if key not in seen:
                            seen.add(key)
                            unique_emails.append(email)
                    
                    # Save to file
                    with open(OTT_TRANSACTIONS_FILE, 'w', encoding='utf-8') as f:
                        json.dump(unique_emails, f, indent=2, ensure_ascii=False)
                    
                    st.success(f"Saved {len(unique_emails)} unique emails to {OTT_TRANSACTIONS_FILE}")
                    st.info(f"Removed {len(ott_emails) - len(unique_emails)} duplicates")
                    
                except Exception as e:
                    st.error(f"Error: {e}")

# Tab 2: Extract Information
with tab2:
    st.header("Extract Transaction Information")
    
    if not os.path.exists(OTT_TRANSACTIONS_FILE):
        st.warning(f"{OTT_TRANSACTIONS_FILE} not found. Please fetch emails first.")
    else:
        # Show info about current data
        try:
            with open(OTT_TRANSACTIONS_FILE, "r", encoding="utf-8") as f:
                current_emails = json.load(f)
            
            if current_emails:
                # Get date range
                dates = []
                for email in current_emails:
                    date_str = email.get("date", "")
                    parsed = parse_date(date_str)
                    if parsed:
                        dates.append(parsed)
                
                if dates:
                    dates.sort()
                    oldest = dates[0]
                    newest = dates[-1]
                    st.info(f"📧 Current data: {len(current_emails)} emails from {oldest} to {newest}")
        except:
            pass
        
        if st.button("Extract Information"):
            with st.spinner("Extracting information..."):
                try:
                    # Load emails
                    with open(OTT_TRANSACTIONS_FILE, "r", encoding="utf-8") as f:
                        emails = json.load(f)
                    
                    st.info(f"Processing {len(emails)} emails...")
                    
                    # Process each email
                    all_transactions = []
                    skipped_low_amount = 0
                    
                    for email in emails:
                        subject = email.get("subject", "")
                        body = email.get("body", "")
                        date = email.get("date", "")
                        
                        service = detect_service(subject, body)
                        amount = extract_amount(subject) or extract_amount(body)
                        parsed_date = parse_date(date)
                        
                        # Filter out low amounts (test transactions, failed charges)
                        if amount and amount < MIN_AMOUNT:
                            skipped_low_amount += 1
                            continue
                        
                        if service and amount and parsed_date:
                            all_transactions.append({
                                "service_name": service,
                                "amount_charged": amount,
                                "transaction_date": parsed_date
                            })
                    
                    # Group by service to calculate frequency
                    service_groups = defaultdict(list)
                    for txn in all_transactions:
                        service_groups[txn["service_name"]].append(txn)
                    
                    # Add frequency with improved detection
                    for service, transactions in service_groups.items():
                        frequency = calculate_frequency(transactions, service)
                        for txn in transactions:
                            if txn["service_name"] == service:
                                txn["billing_frequency"] = frequency
                    
                    # Sort by date
                    all_transactions.sort(key=lambda x: x["transaction_date"], reverse=True)
                    
                    # Transform to final format
                    final_output = []
                    for txn in all_transactions:
                        final_output.append({
                            "service": txn["service_name"],
                            "amount": txn["amount_charged"],
                            "date": txn["transaction_date"],
                            "frequency": txn["billing_frequency"]
                        })
                    
                    # Save
                    with open(OTT_RESULTS_FILE, "w", encoding="utf-8") as f:
                        json.dump(final_output, f, indent=2, ensure_ascii=False)
                    
                    st.success(f"Saved {len(final_output)} transactions to {OTT_RESULTS_FILE}")
                    if skipped_low_amount > 0:
                        st.info(f"Filtered out {skipped_low_amount} transactions below ₹{MIN_AMOUNT} (likely test/failed charges)")
                    
                    # Display summary
                    st.subheader("Extracted Transactions")
                    st.dataframe(final_output)
                    
                except Exception as e:
                    st.error(f"Error: {e}")

# Tab 3: Load to Database
with tab3:
    st.header("Load to SQLite Database")
    
    if not os.path.exists(OTT_RESULTS_FILE):
        st.warning(f"{OTT_RESULTS_FILE} not found. Please extract information first.")
    else:
        if st.button("Load to Database"):
            with st.spinner("Loading to database..."):
                try:
                    # Load transactions
                    with open(OTT_RESULTS_FILE, "r", encoding="utf-8") as f:
                        transactions = json.load(f)
                    
                    # Connect to DB
                    conn = sqlite3.connect(DB_FILE)
                    conn.execute("PRAGMA foreign_keys = ON")
                    
                    # Create schema
                    create_schema(conn)
                    
                    # Load transactions
                    inserted, skipped = load_transactions(conn, transactions)
                    
                    st.success(f"Inserted: {inserted} | Skipped (duplicates): {skipped}")
                    
                    # Display summary
                    st.subheader("Services")
                    services = conn.execute("SELECT id, name FROM services ORDER BY name").fetchall()
                    st.table(services)
                    
                    st.subheader("Recent Transactions")
                    rows = conn.execute("""
                        SELECT s.name, t.amount, t.date, t.frequency
                        FROM transactions t
                        JOIN services s ON s.id = t.service_id
                        ORDER BY t.date DESC
                        LIMIT 20
                    """).fetchall()
                    st.table(rows)
                    
                    total = conn.execute("SELECT SUM(amount) FROM transactions").fetchone()[0]
                    st.info(f"Total across all transactions: ₹{total}")
                    
                    conn.close()
                    
                except Exception as e:
                    st.error(f"Error: {e}")

# Tab 4: Query Database
with tab4:
    st.header("Query Database with Natural Language")
    
    if not os.path.exists(DB_FILE):
        st.warning(f"{DB_FILE} not found. Please load data to database first.")
    elif not HAS_OPENAI:
        st.warning("OpenAI integration not available. Please install openai and python-dotenv packages and set OPENAI_APIKEY in .env file")
    else:
        # Check if OpenAI API key is set
        from dotenv import load_dotenv
        load_dotenv()
        
        if not os.getenv("OPENAI_APIKEY"):
            st.warning("OPENAI_APIKEY not found in environment. Please set it in .env file")
        else:
            user_query = st.text_input("Ask a question about your subscriptions:")
            
            if st.button("Query") and user_query:
                with st.spinner("Processing query..."):
                    try:
                        # Generate SQL
                        st.subheader("Generated SQL")
                        sql = generate_sql(user_query)
                        st.code(sql, language="sql")
                        
                        # Run query
                        rows, columns, error = run_query(sql)
                        
                        if error:
                            st.error(f"SQL Error: {error}")
                        else:
                            # Display results
                            st.subheader("Results")
                            if rows:
                                import pandas as pd
                                df = pd.DataFrame(rows, columns=columns)
                                st.dataframe(df)
                            else:
                                st.info("No results found")
                            
                            # Generate natural language answer
                            st.subheader("Answer")
                            answer = generate_answer(user_query, sql, rows, columns)
                            st.write(answer)
                    
                    except Exception as e:
                        st.error(f"Error: {e}")
            
            # Quick queries
            st.subheader("Quick Queries")
            col1, col2 = st.columns(2)
            
            with col1:
                if st.button("Total spent"):
                    st.session_state.quick_query = "What is my total spending?"
            
            with col2:
                if st.button("Monthly breakdown"):
                    st.session_state.quick_query = "Show me spending by month"
            
            if hasattr(st.session_state, 'quick_query'):
                st.info(f"Query: {st.session_state.quick_query}")