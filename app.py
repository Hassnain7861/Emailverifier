"""
Bulk Email Verifier - Streamlit app.
Local only, no login, no database. Upload CSV or paste emails, verify, export valid.
"""
import queue
import threading

import pandas as pd
import streamlit as st

from verifier import verify_batch_to_queue, _DONE

st.set_page_config(page_title="Bulk Email Verifier", layout="wide")
st.title("Bulk Email Verifier")
st.caption("Upload a CSV or paste emails. Duplicates are removed. Verification runs locally (syntax → MX → SMTP).")

MAX_WORKERS = 5
DELAY_MIN, DELAY_MAX = 1.0, 2.0


def parse_emails_from_text(text: str) -> list[str]:
    """Extract email-like strings from pasted text (one per line or comma-separated)."""
    if not (text or "").strip():
        return []
    emails = []
    for line in text.replace(",", "\n").splitlines():
        for part in line.split():
            part = part.strip()
            if "@" in part and "." in part:
                emails.append(part.strip())
    return emails


def parse_emails_from_csv(uploaded_file) -> list[str]:
    """Try to find an email column in the CSV."""
    if uploaded_file is None:
        return []
    try:
        df = pd.read_csv(uploaded_file)
    except Exception:
        return []
    if df.empty:
        return []
    # Prefer column named email, Email, E-mail, etc.
    for col in df.columns:
        c = str(col).lower().strip()
        if c in ("email", "e-mail", "email address", "emails"):
            return [x for x in df[col].astype(str).str.strip().dropna().tolist() if "@" in x]
    # Otherwise use first column
    return [x for x in df.iloc[:, 0].astype(str).str.strip().dropna().tolist() if "@" in x]


# Input section
input_method = st.radio("Input method", ["Paste emails", "Upload CSV"], horizontal=True)
emails_raw: list[str] = []

if input_method == "Paste emails":
    paste = st.text_area("Paste emails (one per line or comma-separated)", height=120)
    emails_raw = parse_emails_from_text(paste or "")
else:
    csv_file = st.file_uploader("Upload CSV", type=["csv"])
    emails_raw = parse_emails_from_csv(csv_file)

# Normalize and dedupe
emails_raw = [e.strip().lower() for e in emails_raw if (e or "").strip()]
emails_unique = list(dict.fromkeys(emails_raw))

if not emails_unique:
    st.info("Enter or upload some emails to start.")
    st.stop()

st.success(f"**{len(emails_unique)}** unique emails (from {len(emails_raw)} after removing duplicates).")

if "verification_results" not in st.session_state:
    st.session_state.verification_results = None

if st.button("Start verification", type="primary"):
    result_queue = queue.Queue()
    results = []

    def run_batch():
        verify_batch_to_queue(
            emails_unique,
            result_queue,
            max_workers=MAX_WORKERS,
            delay_min=DELAY_MIN,
            delay_max=DELAY_MAX,
        )

    thread = threading.Thread(target=run_batch)
    thread.start()

    progress_bar = st.progress(0.0, text="Verifying…")
    results_placeholder = st.empty()
    total = len(emails_unique)

    while True:
        try:
            item = result_queue.get(timeout=0.5)
        except queue.Empty:
            if not thread.is_alive():
                break
            n = len(results)
            progress_bar.progress(min(1.0, n / total), text=f"Verifying… {n}/{total}")
            if results:
                df_so_far = pd.DataFrame(results, columns=["Email", "Status", "Reason"])
                results_placeholder.dataframe(df_so_far, use_container_width=True)
            continue
        if item is _DONE:
            break
        email, status, reason = item
        results.append((email, status, reason))
        n = len(results)
        progress_bar.progress(min(1.0, n / total), text=f"Verifying… {n}/{total}")
        df_so_far = pd.DataFrame(results, columns=["Email", "Status", "Reason"])
        results_placeholder.dataframe(df_so_far, use_container_width=True)

    thread.join()
    progress_bar.progress(1.0, text="Done.")
    st.session_state.verification_results = results

if st.session_state.verification_results is not None:
    df_results = pd.DataFrame(
        st.session_state.verification_results,
        columns=["Email", "Status", "Reason"],
    )
    st.dataframe(df_results, use_container_width=True)

    valid_only = df_results[df_results["Status"] == "Valid"]["Email"]
    if not valid_only.empty:
        csv_bytes = valid_only.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download valid emails (CSV)",
            data=csv_bytes,
            file_name="valid_emails.csv",
            mime="text/csv",
        )
    else:
        st.info("No valid emails to download.")

if st.session_state.verification_results is None:
    st.info("Click **Start verification** to run checks (syntax → MX → SMTP). Max 5 threads, 1–2 s delay between SMTP checks).")
